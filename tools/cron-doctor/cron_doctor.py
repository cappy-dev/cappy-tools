#!/usr/bin/env python3
"""cron_doctor.py - Audit crontab files for common problems.

A zero-dependency diagnostic tool that checks crontab entries for common
mistakes: bad schedule fields, commands that reference missing executables,
overlapping schedules, unreachable time ranges, silently disabled jobs,
missing PATH/MAILTO context, and more.

Usage:
    python3 cron_doctor.py [--file FILE] [--system] [--json] [--quiet]
    python3 cron_doctor.py -f mycron.txt
    python3 cron_doctor.py --system
    crontab -l | python3 cron_doctor.py

Exit codes:
    0  No issues found (or only warnings with --quiet)
    1  One or more entries have problems
    2  Could not read input

No external packages required. Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

__version__ = "1.0.0"

# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class Finding:
    """A single issue found in a crontab entry."""

    severity: str  # "error", "warning", "info"
    message: str


@dataclass
class CronEntry:
    """A parsed crontab line with its schedule, command, and findings."""

    line_number: int
    raw: str
    environment: bool = False
    minute: str = ""
    hour: str = ""
    dom: str = ""
    month: str = ""
    dow: str = ""
    command: str = ""
    source_file: str = ""
    findings: List[Finding] = field(default_factory=list)

    @property
    def is_disabled(self) -> bool:
        """Return True if the command starts with a comment-like marker."""
        stripped = self.command.lstrip()
        return stripped.startswith(("#", "REM", "DISABLED"))

    @property
    def is_valid_format(self) -> bool:
        return bool(self.minute and self.command)


# --------------------------------------------------------------------------- #
# Cron field validators
# --------------------------------------------------------------------------- #

FIELD_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 7),  # 0 and 7 both mean Sunday
}

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

# Match a single cron field token (number, range, step, list, name, wildcard)
TOKEN_RE = re.compile(
    r"^(?:\*|\d+(?:-\d+)?(?:/\d+)?|\*/\d+|[a-z]{3}(?:-[a-z]{3})?(?:/\d+)?)$",
    re.IGNORECASE,
)


def validate_field(value: str, field_name: str) -> List[str]:
    """Return a list of error messages for a cron schedule field."""
    errors: List[str] = []
    if not value:
        errors.append(f"{field_name} is empty")
        return errors

    low, high = FIELD_RANGES[field_name]
    name_map = MONTH_NAMES if field_name == "month" else (
        DOW_NAMES if field_name == "dow" else {}
    )

    for part in value.split(","):
        part = part.strip().lower()
        if part == "*":
            continue

        # Substitute names before numeric validation.
        for name, num in name_map.items():
            part = part.replace(name, str(num))

        if not TOKEN_RE.match(part):
            errors.append(f"{field_name}: invalid token '{part}'")
            continue

        # Steps: a/b
        if "/" in part:
            base, step_str = part.split("/", 1)
            if not step_str.isdigit() or int(step_str) == 0:
                errors.append(f"{field_name}: step value '{step_str}' is invalid")
            part = base

        if part == "*":
            continue

        # Range: a-b
        if "-" in part:
            left, right = part.split("-", 1)
            if not (left.isdigit() and right.isdigit()):
                errors.append(f"{field_name}: range '{part}' is not numeric")
                continue
            lo, hi = int(left), int(right)
            if lo > hi:
                errors.append(f"{field_name}: range {lo}-{hi} has start after end")
            if lo < low or hi > high:
                if not (field_name == "dow" and hi == 7 and lo == 0):
                    errors.append(
                        f"{field_name}: range {lo}-{hi} out of bounds ({low}-{high})"
                    )
            continue

        # Single number
        if not part.isdigit():
            errors.append(f"{field_name}: invalid token '{part}'")
            continue

        num = int(part)
        # dow 7 means Sunday, which is valid.
        effective_high = 7 if (field_name == "dow" and num == 7) else high
        if num < low or num > effective_high:
            errors.append(
                f"{field_name}: value {num} out of bounds ({low}-{effective_high})"
            )

    return errors


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

ENV_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)
SPECIAL_HEADERS = {
    "@yearly", "@annually", "@monthly", "@weekly",
    "@daily", "@midnight", "@hourly", "@reboot",
}


def parse_crontab(text: str, has_user_field: bool = False) -> List[CronEntry]:
    """Parse raw crontab text into CronEntry objects.

    Args:
        text: Raw crontab text.
        has_user_field: True if entries include a username field between the
            schedule and the command (system crontab format used in
            /etc/crontab and /etc/cron.d/*. False for per-user crontabs
            produced by `crontab -e`.
    """
    entries: List[CronEntry] = []
    needed_fields = 7 if has_user_field else 6

    for idx, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()

        # Skip blank lines and comments.
        if not stripped or stripped.startswith("#"):
            continue

        # Environment variable assignment.
        env_match = ENV_RE.match(stripped)
        if env_match and not _looks_like_schedule(stripped):
            entries.append(CronEntry(
                line_number=idx, raw=raw_line, environment=True,
                command=stripped,
            ))
            continue

        # Special @ headers.
        if stripped.startswith("@"):
            parts = stripped.split(None, 2 if has_user_field else 1)
            header = parts[0].lower()
            command = parts[-1] if len(parts) > 1 else ""
            entry = CronEntry(
                line_number=idx, raw=raw_line,
                minute=header, hour=header, dom=header,
                month=header, dow=header, command=command,
            )
            if header not in SPECIAL_HEADERS:
                entry.findings.append(Finding(
                    "error", f"Unknown special header '@{header[1:]}'"
                ))
            entries.append(entry)
            continue

        # Standard format (5 schedule fields [+ user field] + command).
        fields = stripped.split(None, needed_fields - 1)
        if len(fields) < needed_fields:
            entries.append(CronEntry(
                line_number=idx, raw=raw_line,
                command=stripped,
            ))
            entries[-1].findings.append(Finding(
                "error",
                f"Line has fewer than {needed_fields} fields; expected "
                f"{'5 schedule + user + command' if has_user_field else '5 schedule fields + command'}",
            ))
            continue

        if has_user_field:
            minute, hour, dom, month, dow, _user, command = (
                fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[6]
            )
        else:
            minute, hour, dom, month, dow, command = (
                fields[0], fields[1], fields[2], fields[3], fields[4], fields[5]
            )

        entries.append(CronEntry(
            line_number=idx, raw=raw_line,
            minute=minute, hour=hour,
            dom=dom, month=month,
            dow=dow, command=command,
        ))

    return entries


def _looks_like_schedule(line: str) -> bool:
    """Heuristic: if the first token contains cron schedule chars, not env."""
    first = line.split(None, 1)[0]
    return bool(re.search(r"[*]/,\-|^\d+$", first))


# --------------------------------------------------------------------------- #
# Heuristic checks (command presence, overlap, etc.)
# --------------------------------------------------------------------------- #


def which(executable: str) -> Optional[str]:
    """Return the full path of an executable, or None if not found."""
    return shutil.which(executable)


def extract_base_command(command: str) -> str:
    """Extract the base executable name from a shell command string."""
    cmd = command.strip()
    # Strip leading env assignments like FOO=bar BAR=baz actualcmd
    while True:
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\S+\s+", cmd)
        if not m:
            break
        cmd = cmd[m.end():]

    # Strip a leading "exec "
    if cmd.startswith("exec "):
        cmd = cmd[5:]

    # If it starts with / or ./ it is an absolute/relative path.
    if cmd.startswith(("/", "./", "../")):
        token = cmd.split()[0]
        return token

    # Otherwise take the first token as the executable name.
    token = cmd.split()[0] if cmd.split() else ""
    # Handle common shell wrappers.
    if token in ("bash", "sh", "python", "python3", "perl", "ruby"):
        rest = cmd.split(None, 1)
        if len(rest) > 1:
            nxt = rest[1].split()[0]
            if nxt and not nxt.startswith("-"):
                return nxt
    return token


def check_command_exists(entry: CronEntry) -> None:
    """Warn if the base executable in the command is not on PATH."""
    if entry.is_disabled or not entry.command:
        return

    base = extract_base_command(entry.command)
    if not base:
        return

    # If the command is an absolute/relative path, check directly.
    if base.startswith(("/", "./", "../")):
        path = Path(base)
        if not path.exists():
            entry.findings.append(Finding("warning", f"Script not found: {base}"))
        elif not path.is_file():
            entry.findings.append(Finding("warning", f"Path is not a file: {base}"))
        return

    # Common builtins and shell constructs that are always available.
    if base in ("true", "false", ":", "echo", "test", "[", "sleep",
                "run-parts", "logger"):
        return

    if which(base) is None:
        entry.findings.append(Finding(
            "warning",
            f"Executable '{base}' not found on PATH (cron uses a minimal PATH)",
        ))


def check_cron_specifics(entries: Sequence[CronEntry]) -> None:
    """Run cross-entry and entry-specific best-practice checks."""

    has_path = any(
        e.environment and e.command.upper().startswith("PATH=")
        for e in entries
    )
    has_mailto = any(
        e.environment and e.command.upper().startswith("MAILTO=")
        for e in entries
    )

    for entry in entries:
        if entry.environment or not entry.command:
            continue

        # %-sign gotcha: unescaped % in crontab means newline.
        if "%" in entry.command and "\\%" not in entry.command:
            # Count unescaped percent signs.
            unescaped = re.sub(r"\\%", "", entry.command).count("%")
            if unescaped:
                entry.findings.append(Finding(
                    "info",
                    f"Command contains {unescaped} unescaped '%' sign(s); "
                    "cron treats unescaped % as a newline",
                ))

        # Sunday ambiguity warning for dow.
        if entry.dow and "7" in entry.dow.split(","):
            entry.findings.append(Finding(
                "info",
                "'7' in dow means Sunday (same as 0); valid but uncommon",
            ))

        # Detect weekend-only vs weekday-only patterns.
        if entry.dow in ("0", "7", "0,7", "7,0", "sun"):
            entry.findings.append(Finding(
                "info", "Job runs only on Sunday",
            ))

        if entry.dow == "6":
            entry.findings.append(Finding(
                "info", "Job runs only on Saturday",
            ))

        # Check for overly long-running implied patterns.
        if entry.minute == "*" and entry.hour != "*" and entry.hour != "":
            hour_val = entry.hour
            if hour_val.isdigit():
                entry.findings.append(Finding(
                    "info",
                    f"Job runs every minute during hour {hour_val} (60 times)",
                ))

    # Global warnings appended to first executable entry.
    first_cmd = next((e for e in entries if not e.environment), None)
    if first_cmd is not None:
        if not has_path:
            first_cmd.findings.append(Finding(
                "info",
                "No PATH defined; cron uses a minimal default PATH. "
                "Consider setting PATH= at the top of your crontab",
            ))
        if not has_mailto:
            first_cmd.findings.append(Finding(
                "info",
                "No MAILTO defined; cron output will be emailed to the "
                "crontab owner if mail is configured",
            ))


def detect_overlaps(entries: Sequence[CronEntry]) -> None:
    """Flag entries that run at the exact same schedule (potential overlaps)."""
    schedule_map: dict[str, List[CronEntry]] = {}

    for entry in entries:
        if entry.environment or not entry.command or entry.is_disabled:
            continue
        if not entry.is_valid_format:
            continue
        key = (entry.minute, entry.hour, entry.dom, entry.month, entry.dow)
        schedule_map.setdefault(
            f"{key}", []
        ).append(entry)

    for _key, group in schedule_map.items():
        if len(group) > 1:
            for entry in group:
                entry.findings.append(Finding(
                    "info",
                    f"Shares identical schedule with {len(group) - 1} other "
                    "job(s); may cause resource contention",
                ))


def check_disabled(entry: CronEntry) -> None:
    """Flag entries that look intentionally disabled."""
    if entry.is_disabled:
        entry.findings.append(Finding(
            "warning",
            "Command appears disabled (commented or marked DISABLED/REM)",
        ))


def check_empty_command(entry: CronEntry) -> None:
    if not entry.environment and not entry.command.strip():
        entry.findings.append(Finding("error", "Empty command after schedule"))


# --------------------------------------------------------------------------- #
# Full audit
# --------------------------------------------------------------------------- #


def audit_entries(entries: Sequence[CronEntry]) -> None:
    """Run all checks against parsed entries, mutating findings in place."""
    for entry in entries:
        if entry.environment:
            continue

        if entry.minute and entry.minute.startswith("@"):
            continue  # Special header already validated during parse.

        # Validate schedule fields (only for standard 5-field entries).
        if entry.is_valid_format:
            for fname in ("minute", "hour", "dom", "month", "dow"):
                errors = validate_field(getattr(entry, fname), fname)
                for err in errors:
                    entry.findings.append(Finding("error", err))

        check_disabled(entry)
        check_empty_command(entry)
        check_command_exists(entry)

    check_cron_specifics(entries)
    detect_overlaps(entries)


# --------------------------------------------------------------------------- #
# Input sources
# --------------------------------------------------------------------------- #


def read_crontab(args: argparse.Namespace) -> Optional[str]:
    """Return crontab text from the chosen source, or None on failure."""
    if args.file:
        try:
            return Path(args.file).read_text()
        except OSError as exc:
            print(f"Error reading file '{args.file}': {exc}", file=sys.stderr)
            return None

    if args.system:
        # Read /etc/crontab and all files in /etc/cron.d/*. These use the
        # system crontab format which includes a username field. The
        # /etc/cron.{daily,hourly,weekly,monthly} directories contain plain
        # shell scripts (not crontab syntax) so they are deliberately excluded.
        # The system entry separator used during parsing + display.
        system_files: List[tuple[str, str]] = []

        main_file = Path("/etc/crontab")
        if main_file.exists():
            content = main_file.read_text()
            system_files.append((str(main_file), content))

        cron_d = Path("/etc/cron.d")
        if cron_d.is_dir():
            for f in sorted(cron_d.iterdir()):
                if f.name.startswith(".") or not f.is_file():
                    continue
                try:
                    system_files.append((str(f), f.read_text()))
                except OSError:
                    pass

        if not system_files:
            print("No system crontab files found.", file=sys.stderr)
            return None

        # Use unique markers so the parser can identify each file boundary
        # and tag entries. We join with comment lines that include a marker.
        marked_chunks = []
        for fpath, content in system_files:
            marked_chunks.append(
                f"# >>> FILE: {fpath} (user-field format)\n{content}\n"
            )
        return "\n".join(marked_chunks)

    stdin_text = getattr(args, "crontab_input", None)
    if stdin_text:
        return stdin_text

    # Default: try to read the user's crontab via `crontab -l`.
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        print("The 'crontab' command was not found. Use --file or --system.",
              file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("Timed out reading crontab.", file=sys.stderr)
        return None
    if result.returncode != 0:
        msg = result.stderr.strip() or "no crontab for current user"
        print(f"Could not read crontab: {msg}", file=sys.stderr)
        return None
    return result.stdout


# --------------------------------------------------------------------------- #
# Output formatting
# --------------------------------------------------------------------------- #


SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
SEVERITY_SYMBOL = {"error": "X", "warning": "!", "info": "i"}


def format_findings(entries: Sequence[CronEntry], use_color: bool) -> List[str]:
    """Return a list of human-readable output lines."""
    lines: List[str] = []
    red = "\033[91m" if use_color else ""
    yellow = "\033[93m" if use_color else ""
    cyan = "\033[96m" if use_color else ""
    dim = "\033[2m" if use_color else ""
    reset = "\033[0m" if use_color else ""

    total_errors = 0
    total_warnings = 0
    total_info = 0
    checked = 0

    for entry in entries:
        if entry.environment:
            continue
        checked += 1

        # Sort findings by severity.
        findings = sorted(
            entry.findings,
            key=lambda f: SEVERITY_ORDER.get(f.severity, 3),
        )
        errors = sum(1 for f in findings if f.severity == "error")
        warnings = sum(1 for f in findings if f.severity == "warning")
        infos = sum(1 for f in findings if f.severity == "info")
        total_errors += errors
        total_warnings += warnings
        total_info += infos

        status = "OK"
        status_color = cyan
        if errors:
            status = "ERRORS"
            status_color = red
        elif warnings:
            status = "WARN"
            status_color = yellow
        elif infos:
            status = "info"
            status_color = dim

        preview = entry.command[:60]
        if len(entry.command) > 60:
            preview += "..."

        lines.append(
            f"{status_color}[{status}]{reset} line {entry.line_number}: {preview}"
        )
        for f in findings:
            sym = SEVERITY_SYMBOL[f.severity]
            color = red if f.severity == "error" else (
                yellow if f.severity == "warning" else dim
            )
            lines.append(f"  {color}({sym}){reset} {f.message}")

    lines.append("")
    lines.append(
        f"Checked {checked} entries: "
        f"{total_errors} error(s), {total_warnings} warning(s), "
        f"{total_info} info"
    )

    return lines


def format_json(entries: Sequence[CronEntry]) -> str:
    """Return a JSON representation of the audit results."""
    result = []
    for entry in entries:
        if entry.environment:
            continue
        result.append({
            "line": entry.line_number,
            "command": entry.command,
            "schedule": {
                "minute": entry.minute,
                "hour": entry.hour,
                "dom": entry.dom,
                "month": entry.month,
                "dow": entry.dow,
            },
            "findings": [
                {"severity": f.severity, "message": f.message}
                for f in entry.findings
            ],
        })
    return json.dumps({"entries": result}, indent=2)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


EPILOG = """\
Examples:
  crontab -l | python3 cron_doctor.py          # Check current user crontab
  echo "0 2 * * * /bin/backup" | python3 cron_doctor.py
  python3 cron_doctor.py -f mycrontab.txt       # Check a file
  python3 cron_doctor.py --system               # Check /etc/cron.d and /etc/crontab
  python3 cron_doctor.py --json                 # Machine-readable output

Stdin is read automatically when the input is piped and no --file or --system
option is given.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cron_doctor",
        description="Audit crontab files for common problems and mistakes.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("-f", "--file", help="Path to a crontab file to check")
    src.add_argument("--system", action="store_true",
                     help="Check system cron files (/etc/crontab and /etc/cron.d/*)")

    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--quiet", action="store_true",
                        help="Only show errors and warnings, not info")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable color output")
    parser.add_argument("--version", action="version",
                        version=f"cron_doctor {__version__}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # If stdin is piped and no explicit source, read from stdin.
    if not args.file and not args.system and not sys.stdin.isatty():
        args.crontab_input = sys.stdin.read()

    text = read_crontab(args)
    if text is None:
        return 2

    entries = parse_crontab(text, has_user_field=args.system)
    audit_entries(entries)

    if args.quiet:
        # Drop info-level findings so only errors and warnings are reported.
        for e in entries:
            if e.findings:
                e.findings = [f for f in e.findings
                              if f.severity in ("error", "warning")]

    has_errors = any(
        any(f.severity == "error" for f in e.findings)
        for e in entries if e.findings
    )

    if args.json:
        json_entries = entries
        if args.quiet:
            json_entries = [e for e in entries if e.findings]
        print(format_json(json_entries))
    else:
        use_color = (not args.no_color
                     and sys.stdout.isatty()
                     and os.environ.get("NO_COLOR") is None)
        if args.quiet:
            to_show = [e for e in entries if e.findings]
        else:
            to_show = entries
        output = format_findings(to_show, use_color)
        print("\n".join(output))

    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
