#!/usr/bin/env python3
"""
update-herald: Check for available package updates and security patches.
Zero dependencies, just the Python standard library.

Supports: apt (Debian/Ubuntu), dnf (Fedora/RHEL), pacman (Arch)
Optional notifications: webhooks (generic JSON POST) and local files.

Usage:
    python3 update_herald.py                # print report to stdout
    python3 update_herald.py --json         # machine-readable JSON output
    python3 update_herald.py --webhook URL   # POST JSON to a webhook
    python3 update_herald.py --notify-cmd 'notify-send "%s' # run a command
    python3 update_herald.py --file /tmp/updates.txt         # write to a file
    python3 update_herald.py --security-only # only report security updates
    python3 update_herald.py --quiet         # suppress stdout output

Exit codes:
    0 - success, updates may or may not be available
    1 - no supported package manager detected
    2 - error running package manager commands
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Update:
    """Represents a single available package update."""
    name: str
    current: str
    available: str
    is_security: bool = False
    source: str = ""


@dataclass
class Report:
    """Aggregated update report."""
    manager: str
    updates: List[Update] = field(default_factory=list)
    total_count: int = 0
    security_count: int = 0
    raw_output: str = ""

    def to_dict(self):
        return {
            "manager": self.manager,
            "total_updates": self.total_count,
            "security_updates": self.security_count,
            "updates": [
                {
                    "name": u.name,
                    "current": u.current,
                    "available": u.available,
                    "is_security": u.is_security,
                }
                for u in self.updates
            ],
        }


# ----------
# Detection
# ----------

def detect_package_manager():
    """Return the first available package manager name or None."""
    for mgr in ("apt", "dnf", "pacman"):
        if shutil.which(mgr):
            return mgr
    return None


# ----------
# Parsers
# ----------

def parse_apt(output):
    """Parse apt list --upgradable output into Update objects."""
    updates = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("Listing"):
            continue
        # Format: <name>/<arch or suite> <version> <suite details>
        # e.g. curl/stable-security 8.5.0-2ubuntu10 [upgradable from: 8.4.0-2]
        # The first token is always "name/..." so split on the first slash.
        parts = line.split()
        if len(parts) < 2:
            continue
        name_arch = parts[0]
        name = name_arch.split("/")[0] if "/" in name_arch else name_arch
        available = parts[1].rstrip(",")

        current = ""
        match = re.search(r"upgradable from:\s*(\S+)]?", line)
        if match:
            current = match.group(1)

        # apt doesn't tag security in this output, so we leave False
        updates.append(Update(
            name=name,
            current=current.strip(",[]"),
            available=available.strip(",[]"),
            is_security=False,
            source="apt",
        ))
    return updates


def parse_dnf(output, security_names=None):
    """Parse dnf check-update output into Update objects."""
    security_names = security_names or []
    updates = []
    in_packages = False
    for line in output.strip().splitlines():
        line = line.rstrip()
        if not line:
            in_packages = True
            continue
        if line.startswith("Last metadata") or "metadata expiration" in line:
            continue
        if line.startswith("Obsoleting") or line.startswith("Security:"):
            continue
        if not in_packages:
            continue
        # Format: <name>.<arch>          <version>         <repo>
        parts = line.split()
        if len(parts) < 2:
            continue
        name_arch = parts[0]
        name = name_arch.rsplit(".", 1)[0] if "." in name_arch else name_arch
        available = parts[1]
        updates.append(Update(
            name=name,
            current="",  # dnf doesn't show current in check-update
            available=available,
            is_security=name in security_names,
            source="dnf",
        ))
    return updates


def parse_pacman(output):
    """Parse pacman -Qu output into Update objects."""
    updates = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: <name> <current> -> <available>
        # e.g. firefox 123.0-1 -> 124.0-1
        match = re.match(r"^(\S+)\s+(\S+)\s*->\s*(\S+)", line)
        if match:
            name, current, available = match.group(1), match.group(2), match.group(3)
            updates.append(Update(
                name=name,
                current=current,
                available=available,
                is_security=False,  # pacman doesn't mark security in -Qu alone
                source="pacman",
            ))
        else:
            # Fallback: just the package name
            name = line.split()[0]
            updates.append(Update(
                name=name,
                current="",
                available="",
                is_security=False,
                source="pacman",
            ))
    return updates


# ----------
# Checkers
# ----------

def run_command(cmd_list, timeout=120):
    """Run a command and return (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return "", f"Command not found: {' '.join(cmd_list)}", 127
    except subprocess.TimeoutExpired:
        return "", f"Command timed out: {' '.join(cmd_list)}", 124


def check_apt():
    """Check for updates using apt."""
    stdout, stderr, rc = run_command(["apt", "list", "--upgradable"])
    # Refresh the package lists first
    if rc == 0:
        updates = parse_apt(stdout)
    else:
        # Try refreshing package lists
        _, stderr2, _ = run_command(["sudo", "-n", "apt", "update"])
        stdout2, _, _ = run_command(["apt", "list", "--upgradable"])
        updates = parse_apt(stdout2)

    # Check for security updates specifically
    sec_stdout, _, _ = run_command(["apt", "list", "--upgradable", "--component=security"])
    sec_names = {u.name for u in parse_apt(sec_stdout)}

    for u in updates:
        if u.name in sec_names:
            u.is_security = True

    return Report(
        manager="apt",
        updates=updates,
        total_count=len(updates),
        security_count=sum(1 for u in updates if u.is_security),
    )


def check_dnf():
    """Check for updates using dnf."""
    stdout, stderr, rc = run_command(["dnf", "check-update"])
    # dnf returns 100 when updates are available, 0 when none
    if rc not in (0, 100):
        # Try with sudo for non-interactive refresh
        _, _, _ = run_command(["sudo", "-n", "dnf", "makecache"])
        stdout, _, _ = run_command(["dnf", "check-update"])

    updates = parse_dnf(stdout)

    # Get security updates
    sec_stdout, _, sec_rc = run_command(
        ["dnf", "--security", "--assumeno", "check-update"]
    )
    sec_names = {u.name for u in parse_dnf(sec_stdout)} if sec_rc in (0, 100) else set()

    for u in updates:
        if u.name in sec_names:
            u.is_security = True

    return Report(
        manager="dnf",
        updates=updates,
        total_count=len(updates),
        security_count=sum(1 for u in updates if u.is_security),
    )


def check_pacman():
    """Check for updates using pacman."""
    stdout, stderr, rc = run_command(["pacman", "-Qu"])
    # pacman -Qu returns 1 when no updates, 0 when there are updates
    updates = parse_pacman(stdout)

    return Report(
        manager="pacman",
        updates=updates,
        total_count=len(updates),
        security_count=0,  # pacman doesn't distinguish security in standard -Qu
    )


CHECKERS = {
    "apt": check_apt,
    "dnf": check_dnf,
    "pacman": check_pacman,
}


# ----------
# Notifications
# ----------

def notify_webhook(url, report_dict):
    """POST the report as JSON to a webhook URL."""
    import urllib.request
    import urllib.error

    payload = json.dumps(report_dict).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.URLError as e:
        print(f"Webhook error: {e}", file=sys.stderr)
        return None


def notify_file(path, text):
    """Write the report text to a file."""
    try:
        with open(path, "w") as f:
            f.write(text + "\n")
        return True
    except OSError as e:
        print(f"File write error: {e}", file=sys.stderr)
        return False


def notify_command(cmd_template, text):
    """Run a command, substituting %s with the report text."""
    # Escape for shell safety
    safe_text = text.replace("'", "'\\''")
    cmd = cmd_template % safe_text if "%s" in cmd_template else cmd_template
    _, stderr, rc = run_command(["bash", "-c", cmd])
    if rc != 0:
        print(f"Notify command failed (rc={rc}): {stderr}", file=sys.stderr)
    return rc == 0


# ----------
# Formatting
# ----------

def format_text_report(report):
    """Return a human-readable plain text summary."""
    lines = []
    lines.append(f"=== Update Herald Report ===")
    lines.append(f"Package Manager: {report.manager}")
    lines.append(f"Total Updates:   {report.total_count}")
    lines.append(f"Security Updates: {report.security_count}")
    lines.append("")

    if not report.updates:
        lines.append("Everything is current. What a lucky break!")
        return "\n".join(lines)

    lines.append("Available Updates:")
    lines.append("-" * 60)
    for u in sorted(report.updates, key=lambda x: (not x.is_security, x.name)):
        tag = "[SECURITY] " if u.is_security else ""
        if u.current:
            lines.append(f"  {tag}{u.name} ({u.current} -> {u.available})")
        else:
            lines.append(f"  {tag}{u.name} -> {u.available}")

    lines.append("")
    lines.append(f"Summary: {report.total_count} updates available, "
                 f"{report.security_count} security.")
    return "\n".join(lines)


# ----------
# Main
# ----------

def main():
    parser = argparse.ArgumentParser(
        description="Check for available package updates and security patches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  update-herald                    Print update report to stdout
  update-herald --json             Output as JSON
  update-herald --webhook https://hooks.example.com/api
  update-herald --notify-cmd 'notify-send "Updates available: %s"'
  update-herald --file /var/tmp/update-report.txt
  update-herald --security-only    Only show security updates
  update-herald --quiet            No stdout, useful for cron
""",
    )
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON instead of text")
    parser.add_argument("--webhook", metavar="URL",
                        help="POST JSON report to a webhook URL")
    parser.add_argument("--notify-cmd", metavar="CMD",
                        help="Run a command with report text substituted as %%s")
    parser.add_argument("--file", metavar="PATH",
                        help="Write the report text to a file")
    parser.add_argument("--security-only", action="store_true",
                        help="Only include security updates in the report")
    parser.add_argument("--quiet", action="store_true",
                        help="Do not print report to stdout")
    args = parser.parse_args()

    # Detect package manager
    mgr = detect_package_manager()
    if not mgr:
        print("No supported package manager detected.", file=sys.stderr)
        print("Supported: apt (Debian/Ubuntu), dnf (Fedora/RHEL), pacman (Arch)",
              file=sys.stderr)
        return 1

    # Gather updates
    checker = CHECKERS[mgr]
    try:
        report = checker()
    except Exception as e:
        print(f"Error checking updates: {e}", file=sys.stderr)
        return 2

    # Filter to security only if requested
    if args.security_only:
        report.updates = [u for u in report.updates if u.is_security]
        report.total_count = len(report.updates)
        report.security_count = report.total_count

    # Build output formats
    report_dict = report.to_dict()
    text_report = format_text_report(report)

    # Print to stdout unless quiet
    if not args.quiet:
        if args.json:
            print(json.dumps(report_dict, indent=2))
        else:
            print(text_report)

    # File notification
    if args.file:
        notify_file(args.file, text_report)

    # Command notification
    if args.notify_cmd:
        notify_command(args.notify_cmd, text_report)

    # Webhook notification
    if args.webhook:
        notify_webhook(args.webhook, report_dict)

    return 0


if __name__ == "__main__":
    sys.exit(main())
