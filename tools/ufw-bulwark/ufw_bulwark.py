#!/usr/bin/env python3
"""
ufw-bulwark: Zero-dependency auditor for UFW (Uncomplicated Firewall) rules.

Reads `ufw status verbose` (or a saved text dump for offline review), inspects
every rule and the global defaults, and flags risky configuration. Produces a
human-readable report plus an optional self-contained HTML page. Designed to run
from cron: exit codes let your scheduler decide whether to alert on warnings or
only on critical findings.

Exit codes:
    0  No findings (firewall looks healthy)
    1  Only warnings (advisory issues)
    2  At least one critical finding open
    3  ufw not installed, not enabled, or could not be read

Requires: Python 3.8+, the `ufw` command present on PATH (unless --input used).
No third-party packages. Pure standard library.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

__version__ = "1.0.0"

# Ports commonly associated with sensitive services. An "allow from anywhere"
# rule against one of these is almost always worth a second look.
SENSITIVE_PORTS = {
    21: "FTP (cleartext credentials)",
    22: "SSH",
    23: "Telnet (cleartext)",
    25: "SMTP (open relay risk)",
    53: "DNS (resolver exposure)",
    135: "RPC / SMB",
    137: "NetBIOS name",
    138: "NetBIOS datagram",
    139: "SMB over NetBIOS",
    445: "SMB",
    1433: "MS SQL Server",
    1521: "Oracle DB",
    2375: "Docker daemon (unencrypted)",
    2376: "Docker daemon (TLS)",
    3306: "MySQL / MariaDB",
    3389: "RDP",
    5432: "PostgreSQL",
    5601: "Kibana",
    5900: "VNC",
    5984: "CouchDB admin",
    6379: "Redis (often no auth)",
    8000: "Common dev server",
    8080: "Common dev/proxy port",
    8443: "Common alt HTTPS",
    9000: "PHP-FPM / Portainer",
    9042: "Cassandra",
    9200: "Elasticsearch (often no auth)",
    9300: "Elasticsearch transport",
    11211: "Memcached",
    27017: "MongoDB",
    27018: "MongoDB",
}

SEVERITY_OK = "ok"
SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_CRITICAL = "critical"

SEVERITY_RANK = {SEVERITY_OK: 0, SEVERITY_INFO: 1, SEVERITY_WARN: 2, SEVERITY_CRITICAL: 3}


@dataclass
class Finding:
    severity: str
    rule: str
    message: str
    hint: str = ""


@dataclass
class AuditResult:
    enabled: Optional[bool]
    ipv6: Optional[bool]
    logging: Optional[str]
    default_incoming: Optional[str]
    default_outgoing: Optional[str]
    default_forward: Optional[str]
    rules: List[Tuple[str, str]] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)

    @property
    def worst_severity(self) -> str:
        if not self.findings:
            return SEVERITY_OK
        return max(self.findings, key=lambda f: SEVERITY_RANK[f.severity]).severity

    @property
    def exit_code(self) -> int:
        if self.enabled is False or self.enabled is None:
            return 3
        if self.default_incoming is None and not self.rules:
            return 3
        worst = self.worst_severity
        if worst == SEVERITY_CRITICAL:
            return 2
        if worst == SEVERITY_WARN:
            return 1
        return 0


def parse_port(spec: str) -> Optional[int]:
    """Extract a single integer port from a token like '22', '22/tcp', '22:25', '8000:8080'."""
    spec = spec.strip()
    if not spec:
        return None
    # strip protocol
    if "/" in spec:
        spec = spec.split("/", 1)[0]
    # a numeric single port
    if spec.isdigit():
        return int(spec)
    # a range "low:high" -> report the low end (whole range is open)
    if ":" in spec:
        first = spec.split(":", 1)[0]
        if first.isdigit():
            return int(first)
    return None


def _split_action_port(token: str) -> Tuple[str, str]:
    """Split '22/tcp ALLOW IN' style trailing action from the port spec."""
    token = token.strip()
    for action in ("ALLOW IN", "ALLOW OUT", "DENY IN", "DENY OUT",
                   "ALLOW FWD", "DENY FWD", "ALLOW", "DENY", "REJECT",
                   "LIMIT IN", "LIMIT OUT"):
        if token.endswith(action):
            return action, token[: -len(action)].strip()
    return "", token


def _run_ufw_status() -> Tuple[Optional[str], Optional[str]]:
    """Invoke `ufw status verbose`. Returns (stdout, stderr_or_error)."""
    if shutil.which("ufw") is None:
        return None, "ufw not found on PATH"
    try:
        proc = subprocess.run(
            ["ufw", "status", "verbose"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return None, "ufw not found on PATH"
    except subprocess.TimeoutExpired:
        return None, "ufw status timed out"
    if proc.returncode != 0 and not proc.stdout:
        return None, (proc.stderr or f"ufw exited {proc.returncode}").strip()
    return proc.stdout, None


def parse_ufw_status(text: str) -> AuditResult:
    """Parse the textual output of `ufw status verbose` into an AuditResult."""
    result = AuditResult(
        enabled=None, ipv6=None, logging=None,
        default_incoming=None, default_outgoing=None, default_forward=None,
    )

    saw_status_line = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        low = line.lower()

        # "Status: active" or "Status: inactive"
        m = re.match(r"^status:\s*(\w+)", low)
        if m:
            saw_status_line = True
            result.enabled = (m.group(1) == "active")
            continue

        if low.startswith("logging:"):
            result.logging = line.split(":", 1)[1].strip()
            continue
        if low.startswith("default:"):
            # "Default: deny (incoming), allow (outgoing), disabled (forwarded)"
            body = line.split(":", 1)[1].strip()
            for piece in body.split(","):
                piece = piece.strip()
                mp = re.match(r"(\w+)\s*\((\w+)\)", piece)
                if not mp:
                    continue
                policy, direction = mp.group(1), mp.group(2).lower()
                if direction == "incoming":
                    result.default_incoming = policy
                elif direction == "outgoing":
                    result.default_outgoing = policy
                elif direction in ("forwarded", "forward"):
                    result.default_forward = policy
            continue
        if low.startswith("ipv6:"):
            result.ipv6 = ("yes" in low or "true" in low or "on" in low)
            continue

        # Rule lines look like one of:
        #   22/tcp                     ALLOW IN    Anywhere
        #   22/tcp (v6)                ALLOW IN    Anywhere (v6)
        #   80/tcp                     ALLOW IN    192.168.1.0/24
        #   22/tcp                     LIMIT IN    Anywhere
        #   22/tcp                     DENY IN     1.2.3.4
        #   Anywhere                   ALLOW IN    1.2.3.4 (the reverse form)
        # Columns are whitespace separated. Some setups print "Anywhere" first.
        tokens = re.split(r"\s{2,}", line)
        tokens = [t.strip() for t in tokens if t.strip()]
        if len(tokens) < 3:
            continue
        if tokens[0] in ("--", "To", "To                         Action From"):
            continue
        if low.startswith("to") and "action" in low and "from" in low:
            # header row
            continue

        # Heuristic: find the action token
        action_idx = None
        action_token = None
        for i, t in enumerate(tokens):
            up = t.upper()
            if up in ("ALLOW IN", "ALLOW OUT", "ALLOW FWD", "ALLOW",
                      "DENY IN", "DENY OUT", "DENY FWD", "DENY",
                      "REJECT IN", "REJECT OUT", "REJECT", "LIMIT IN",
                      "LIMIT OUT"):
                action_idx = i
                action_token = up
                break
        if action_idx is None:
            continue
        port_spec = tokens[0]
        source = " ".join(tokens[action_idx + 1:]).strip()
        result.rules.append((action_token, f"{port_spec} from {source}"))

    if not saw_status_line and result.enabled is None and not result.rules:
        # Could not find any recognizable content
        result.enabled = None
    return result


def audit(result: AuditResult) -> AuditResult:
    findings = result.findings

    if result.enabled is False:
        findings.append(Finding(
            SEVERITY_CRITICAL, "global",
            "UFW is installed but not active (Status: inactive).",
            "Run `sudo ufw enable` to activate the firewall. Existing rules are "
            "ignored while UFW is inactive.",
        ))
        return result

    # Defaults
    if result.default_incoming is None:
        findings.append(Finding(
            SEVERITY_WARN, "default policy",
            "Default incoming policy could not be determined.",
            "Inspect `ufw status verbose`. A missing default usually means a "
            "stale or custom UFW install.",
        ))
    elif result.default_incoming.lower() not in ("deny", "reject"):
        findings.append(Finding(
            SEVERITY_CRITICAL, "default policy",
            f"Default incoming policy is '{result.default_incoming}', so any "
            "unmatched inbound traffic is allowed.",
            "Run `sudo ufw default deny incoming` to restore a safe default.",
        ))

    if result.default_outgoing and result.default_outgoing.lower() == "deny":
        findings.append(Finding(
            SEVERITY_WARN, "default policy",
            "Default outgoing policy is 'deny'. This is secure but will break "
            "updates, DNS, and most services unless explicit allow rules exist.",
            "Confirm you have allow-out rules for everything this host must "
            "reach, or switch back with `sudo ufw default allow outgoing`.",
        ))

    # IPv6
    if result.ipv6 is False:
        findings.append(Finding(
            SEVERITY_WARN, "ipv6",
            "IPv6 support appears disabled in UFW while IPv6 addresses may be "
            "assigned to this host.",
            "Edit /etc/default/ufw and set IPV6=yes, then `sudo ufw disable && "
            "sudo ufw enable`.",
        ))

    # Logging
    if result.logging is None:
        findings.append(Finding(
            SEVERITY_INFO, "logging",
            "UFW logging level is not reported.",
        ))
    else:
        log_low = result.logging.lower()
        if ("off" in log_low) or ("low" not in log_low and "medium" not in log_low
                                   and "high" not in log_low and "medium" not in log_low):
            if "off" in log_low:
                findings.append(Finding(
                    SEVERITY_INFO, "logging",
                    "UFW logging is off. Blocked connection attempts are not "
                    "recorded.",
                    "Run `sudo ufw logging low` to capture dropped packets for "
                    "debugging.",
                ))

    # Rule-level analysis
    seen_keys = set()
    ssh_limit_seen = False
    ssh_allow_anywhere_seen = False

    for action, rule in result.rules:
        port_spec = rule.split(" from ", 1)[0]
        source = rule.split(" from ", 1)[1] if " from " in rule else ""

        action_up = action.upper()
        is_limit = action_up.startswith("LIMIT")
        is_allow = action_up.startswith("ALLOW")
        is_deny = action_up.startswith("DENY") or action_up.startswith("REJECT")
        is_v6 = "(v6)" in port_spec or "(v6)" in source

        key = (port_spec, action, source)
        if key in seen_keys:
            findings.append(Finding(
                SEVERITY_INFO, rule,
                "Duplicate rule appears more than once.",
                "Run `sudo ufw status numbered` and remove the redundant entry.",
            ))
        seen_keys.add(key)

        port = parse_port(port_spec)

        # Sensitive port exposed to the world
        anywhere_source = (source.strip().lower() == "anywhere") or (
            source.strip() == "" )
        if is_allow and anywhere_source and port is not None and port in SENSITIVE_PORTS:
            label = SENSITIVE_PORTS[port]
            findings.append(Finding(
                SEVERITY_CRITICAL, rule,
                f"Port {port} ({label}) is ALLOWED from anywhere.",
                "Restrict this to a trusted source CIDR, e.g. "
                f"`sudo ufw delete allow {port_spec}` then "
                f"`sudo ufw allow from 192.168.1.0/24 to any port {port}`.",
            ))

        # Telnet specifically is almost never acceptable
        if is_allow and anywhere_source and port == 23:
            findings.append(Finding(
                SEVERITY_CRITICAL, rule,
                "Telnet (port 23) is open to the world. Telnet transmits "
                "credentials in cleartext.",
                "Disable telnet entirely and use SSH.",
            ))

        # Track SSH specifics across v4/v6 pairs
        if port == 22 and is_limit:
            if not is_v6:
                ssh_limit_seen = True
        if port == 22 and is_allow and anywhere_source:
            if not is_v6:
                ssh_allow_anywhere_seen = True
            findings.append(Finding(
                SEVERITY_CRITICAL, rule,
                "SSH (22) is ALLOWED from anywhere without rate limiting.",
                "Replace with `sudo ufw delete allow 22` then "
                "`sudo ufw limit 22` to throttle brute-force attempts.",
            ))

        # Broad source CIDR on a deny? unusual but harmless
        # Allow from a public /8 is risky
        if is_allow and not anywhere_source and port is not None and port in SENSITIVE_PORTS:
            cidr_match = re.search(r"(\d+\.\d+\.\d+\.\d+/\d+)", source)
            if cidr_match:
                prefix = int(cidr_match.group(1).split("/")[1])
                if prefix <= 8:
                    findings.append(Finding(
                        SEVERITY_WARN, rule,
                        f"Sensitive port {port} is allowed from a very broad CIDR "
                        f"(/{prefix}).",
                        "Tighten the source range, e.g. a /24 or smaller.",
                    ))

        # A rule that allows everything (port 'Anywhere' / no port)
        if is_allow and port is None and port_spec.lower() in ("anywhere", "any"):
            findings.append(Finding(
                SEVERITY_CRITICAL, rule,
                "RULE allows ALL traffic from the given source with no port "
                "restriction.",
                "Replace with a specific port allow.",
            ))

    # Cross-rule: SSH open to the world and no LIMIT rule at all
    if ssh_allow_anywhere_seen and not ssh_limit_seen:
        # already reported per-rule; nothing extra to add to avoid duplicates
        pass

    return result


def render_text(result: AuditResult) -> str:
    lines: List[str] = []
    lines.append("ufw-bulwark report")
    lines.append("=" * 60)
    lines.append(f"Generated: {datetime.utcnow().isoformat()}Z")
    lines.append("")

    def kv(label, value):
        lines.append(f"  {label:22s} {value}")

    kv("UFW enabled", _fmt_bool(result.enabled))
    kv("IPv6 enabled", _fmt_bool(result.ipv6))
    kv("Logging", result.logging or "(unknown)")
    kv("Default incoming", result.default_incoming or "(unknown)")
    kv("Default outgoing", result.default_outgoing or "(unknown)")
    kv("Default forward", result.default_forward or "(unknown)")
    kv("Rule count", len(result.rules))
    lines.append("")

    if result.enabled is False:
        lines.append("UFW is INACTIVE. No rules are enforced.")
        lines.append("")

    crit = [f for f in result.findings if f.severity == SEVERITY_CRITICAL]
    warn = [f for f in result.findings if f.severity == SEVERITY_WARN]
    info = [f for f in result.findings if f.severity == SEVERITY_INFO]

    lines.append(f"Findings: {len(crit)} critical / {len(warn)} warnings / {len(info)} info")
    lines.append("")

    def block(title, items):
        if not items:
            return
        lines.append(title)
        lines.append("-" * 60)
        for f in items:
            lines.append(f"  [{f.severity.upper()}] {f.rule}")
            lines.append(f"    {f.message}")
            if f.hint:
                lines.append(f"    hint: {f.hint}")
            lines.append("")

    block("CRITICAL", crit)
    block("WARNINGS", warn)
    block("INFO", info)

    if not result.findings:
        lines.append("No findings. Firewall configuration looks healthy.")

    lines.append("")
    lines.append(f"exit code -> {result.exit_code}")
    return "\n".join(lines)


def _fmt_bool(v: Optional[bool]) -> str:
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return "(unknown)"


def render_html(result: AuditResult) -> str:
    crit = [f for f in result.findings if f.severity == SEVERITY_CRITICAL]
    warn = [f for f in result.findings if f.severity == SEVERITY_WARN]
    info = [f for f in result.findings if f.severity == SEVERITY_INFO]

    def esc(s: str) -> str:
        return html.escape(str(s), quote=True)

    def finding_rows(items):
        rows = []
        for f in items:
            sev_class = f.severity
            rows.append(
                f'<tr class="{sev_class}">'
                f'<td><span class="sev {sev_class}">{f.severity.upper()}</span></td>'
                f'<td>{esc(f.rule)}</td>'
                f'<td>{esc(f.message)}'
                + (f'<br><span class="hint">hint: {esc(f.hint)}</span>' if f.hint else "")
                + "</td></tr>"
            )
        return "\n".join(rows)

    summary = (
        f'<div class="summary {result.worst_severity}">'
        f'<span>Worst severity: <b>{result.worst_severity.upper()}</b></span>'
        f'<span>Critical: {len(crit)}</span>'
        f'<span>Warnings: {len(warn)}</span>'
        f'<span>Info: {len(info)}</span>'
        f'<span>Rules: {len(result.rules)}</span>'
        f"</div>"
    )

    status_rows = (
        "<table class='status'>"
        f"<tr><th>UFW enabled</th><td>{esc(_fmt_bool(result.enabled))}</td></tr>"
        f"<tr><th>IPv6 enabled</th><td>{esc(_fmt_bool(result.ipv6))}</td></tr>"
        f"<tr><th>Logging</th><td>{esc(result.logging or '(unknown)')}</td></tr>"
        f"<tr><th>Default incoming</th><td>{esc(result.default_incoming or '(unknown)')}</td></tr>"
        f"<tr><th>Default outgoing</th><td>{esc(result.default_outgoing or '(unknown)')}</td></tr>"
        f"<tr><th>Default forward</th><td>{esc(result.default_forward or '(unknown)')}</td></tr>"
        "</table>"
    )

    findings_table = (
        "<table class='findings'>"
        "<tr><th>Severity</th><th>Rule</th><th>Message</th></tr>"
        + finding_rows(crit + warn + info)
        + "</table>"
    )
    if not result.findings:
        findings_table = "<p class='ok'>No findings. Firewall configuration looks healthy.</p>"

    css = """
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           max-width: 960px; margin: 2em auto; padding: 0 1em; color: #222; }
    h1 { border-bottom: 2px solid #ddd; padding-bottom: .3em; }
    .meta { color: #666; font-size: .9em; }
    .summary { display: flex; gap: 1.5em; flex-wrap: wrap; padding: .8em 1em;
                border-radius: 6px; margin: 1em 0; background: #f4f5f7; }
    .summary.ok { background: #e8f5e9; }
    .summary.warn { background: #fff8e1; }
    .summary.critical { background: #ffebee; }
    .summary b { font-size: 1.1em; }
    .status, .findings { border-collapse: collapse; width: 100%; margin: 1em 0; }
    .status th { text-align: left; width: 200px; background: #f0f0f0; }
    .status td, .status th, .findings td, .findings th {
        border: 1px solid #ddd; padding: .5em .7em; vertical-align: top; }
    .findings th { background: #f0f0f0; text-align: left; }
    tr.critical { background: #ffebee; }
    tr.warn { background: #fff8e1; }
    .sev { padding: .15em .5em; border-radius: 4px; font-weight: 600;
           font-size: .8em; color: #fff; }
    .sev.critical { background: #c62828; }
    .sev.warn { background: #f9a825; }
    .sev.info { background: #5c6bc0; }
    .sev.ok { background: #2e7d32; }
    .hint { color: #666; font-size: .9em; }
    .ok { color: #2e7d32; font-weight: 600; }
    code { background: #f4f5f7; padding: .1em .3em; border-radius: 3px; }
    """

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ufw-bulwark report</title>
<style>{css}</style>
</head>
<body>
<h1>ufw-bulwark report</h1>
<p class="meta">Generated {esc(datetime.utcnow().isoformat() + 'Z')}</p>
{summary}
<h2>Firewall status</h2>
{status_rows}
<h2>Findings</h2>
{findings_table}
<p class="meta">Exit code: {result.exit_code}. Generated by
<a href="https://github.com/cappy-dev/ufw-bulwark">ufw-bulwark</a> v{__version__}.</p>
</body>
</html>
"""


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="ufw-bulwark",
        description="Audit UFW firewall rules and flag risky configuration. "
                    "Zero dependencies, Python 3.8+.",
    )
    p.add_argument("--input", "-i",
                   help="Read ufw status output from a file instead of running "
                        "the ufw command. Useful for offline review or testing.")
    p.add_argument("--html", "-H",
                   help="Also write a self-contained HTML report to this path.")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="Suppress the text report. Still writes HTML if requested.")
    p.add_argument("--no-audit", action="store_true",
                   help="Parse and show status only; do not run the rule audit.")
    p.add_argument("--version", "-V", action="version",
                   version=f"ufw-bulwark {__version__}")
    args = p.parse_args(argv)

    if args.input:
        try:
            with open(args.input, "r", encoding="utf-8") as fh:
                status_text = fh.read()
        except OSError as exc:
            print(f"error: cannot read input file: {exc}", file=sys.stderr)
            return 3
    else:
        status_text, err = _run_ufw_status()
        if status_text is None:
            print(f"error: {err}", file=sys.stderr)
            return 3

    result = parse_ufw_status(status_text)

    if not args.no_audit:
        result = audit(result)

    if not args.quiet:
        print(render_text(result))

    if args.html:
        try:
            with open(args.html, "w", encoding="utf-8") as fh:
                fh.write(render_html(result))
        except OSError as exc:
            print(f"error: cannot write html report: {exc}", file=sys.stderr)
            return 3

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
