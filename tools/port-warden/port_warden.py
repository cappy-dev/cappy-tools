#!/usr/bin/env python3
"""
port-warden: A zero-dependency Linux listening-port scanner and auditor.

Scans listening TCP and UDP ports on the local machine, maps each port to the
owning process, and flags potentially unsafe or unexpected services based on a
small built-in rule set. Designed for cron: exits non-zero when risky ports are
found, so you can wire it into monitoring pipelines.

USAGE
    port_warden.py                 scan all interfaces, text output
    port_warden.py --json          machine-readable JSON output
    port_warden.py --allow 80,443  treat listed ports as always-safe
    port_warden.py --interface eth0  restrict scan to one interface
    port_warden.py --quiet         only print warnings, nothing else

REQUIREMENTS
    - Linux with /proc and /sys
    - `ss` from iproute2 (used for enumeration; falls back to /proc/net if absent)
    - Python 3.7+ standard library only

No third-party packages. No network calls. Everything stays local.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from collections import OrderedDict

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# Built-in risk rules. Ports and service names that trigger a warning.
# ---------------------------------------------------------------------------

# Ports that should generally NOT be exposed to the public internet.
# These get flagged with severity "high" when listening on 0.0.0.0 or ::
RISKY_PORT_MAP = {
    23:    "Telnet (unencrypted remote shell)",
    21:    "FTP control (cleartext credentials)",
    69:    "TFTP (no authentication)",
    161:   "SNMP (often misconfigured with default community strings)",
    389:   "LDAP (unencrypted)",
    636:   "LDAP over TLS (verify certificate chain)",
    1433:  "Microsoft SQL Server (database exposed)",
    1521:  "Oracle DB (database exposed)",
    3306:  "MySQL (database exposed)",
    5432:  "PostgreSQL (database exposed)",
    27017: "MongoDB (database exposed)",
    6379:  "Redis (often unauthenticated by default)",
    9200:  "Elasticsearch (often unauthenticated by default)",
    11211: "Memcached (amplification attack surface)",
    5900:  "VNC (unencrypted remote desktop)",
    5901:  "VNC (unencrypted remote desktop)",
    6000:  "X11 (unencrypted remote display)",
}

# Process name patterns that are flagged regardless of port.
RISKY_PROCESS_PATTERNS = [
    (re.compile(r"^telnet", re.I),       "Telnet daemon is insecure"),
    (re.compile(r"^vsftpd", re.I),       "FTP daemon; prefer SFTP/SCP"),
    (re.compile(r"^tftpd", re.I),        "TFTP daemon lacks authentication"),
    (re.compile(r"^(x11vnc|tigervnc|tightvnc)", re.I), "VNC is unencrypted"),
    (re.compile(r"^snmpd", re.I),        "SNMP can leak system data"),
]

# Ports commonly expected on a web/db host; used for the "summary" line.
COMMON_SAFE_PORTS = {22, 80, 443}


def run(cmd):
    """Run a command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10
        )
        return result.stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


# ---------------------------------------------------------------------------
# Port enumeration via `ss`
# ---------------------------------------------------------------------------

def parse_ss_output():
    """Parse `ss -tulnp` output into a list of listening port dicts."""
    raw = run(["ss", "-tulnpH"])
    if not raw.strip():
        return []

    ports = []
    # ss -H disables header line. Columns are space-separated but the
    # process info column may contain spaces, so we parse by fixed prefixes.
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].lower()       # tcp, tcp6, udp, udp6
        state = parts[1]
        local = parts[4]               # addr:port

        # Only listening TCP and all UDP are interesting.
        if proto.startswith("tcp") and state != "LISTEN":
            continue

        bind_addr, port = _split_addr(local, proto)
        if port is None:
            continue

        # Process info is the last column, looks like users:(("name",pid=1234,fd=5))
        proc_info = " ".join(parts[6:]) if len(parts) > 6 else ""
        proc_name, proc_pid = _parse_process(proc_info)

        ports.append({
            "proto": proto,
            "bind_addr": bind_addr,
            "port": port,
            "process": proc_name,
            "pid": proc_pid,
        })
    return ports


def _strip_iface_suffix(addr):
    """Remove an `%interface` qualifier that ss appends to scoped addresses."""
    # ss prints scoped addresses like 127.0.0.53%lo or fe80::1%eth0.
    if addr and "%" in addr:
        return addr.split("%", 1)[0]
    return addr


def _split_addr(local, proto):
    """Split an ss local address field into (bind_addr, port)."""
    # IPv6 addresses in ss look like [::]:80 or [fe80::1]:80
    if local.startswith("["):
        end = local.find("]")
        if end == -1:
            return None, None
        bind_addr = _strip_iface_suffix(local[1:end])
        rest = local[end + 1:]
        if rest.startswith(":"):
            try:
                return bind_addr, int(rest[1:])
            except ValueError:
                return None, None
        return bind_addr, None
    # IPv4: 0.0.0.0:80 or 127.0.0.1:80 or *:80
    if ":" in local:
        addr, _, port_str = local.rpartition(":")
        try:
            return _strip_iface_suffix(addr), int(port_str)
        except ValueError:
            return None, None
    return None, None


def _parse_process(info):
    """Extract process name and pid from an ss users:(...) field."""
    if not info:
        return "", ""
    # users:(("nginx",pid=1234,fd=6))
    m = re.search(r'\("([^"]+)",pid=(\d+)', info)
    if m:
        return m.group(1), m.group(2)
    # sometimes: users:((("nginx",pid=1234,fd=6)))
    m = re.search(r'"([^"]+)",pid=(\d+)', info)
    if m:
        return m.group(1), m.group(2)
    return "", ""


# ---------------------------------------------------------------------------
# Fallback: parse /proc/net directly when ss is unavailable
# ---------------------------------------------------------------------------

def parse_proc_net():
    """Fallback parser using /proc/net/tcp, tcp6, udp, udp6."""
    results = []
    files = [("tcp", "tcp"), ("tcp6", "tcp6"), ("udp", "udp"), ("udp6", "udp6")]
    for fname, proto in files:
        path = os.path.join("/proc/net", fname)
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                lines = f.readlines()
        except OSError:
            continue
        if len(lines) < 2:
            continue
        for line in lines[1:]:  # skip header
            fields = line.split()
            if len(fields) < 4:
                continue
            local = fields[1]
            state = fields[3]
            # TCP state 0A = LISTEN; UDP always relevant
            if proto.startswith("tcp") and state != "0A":
                continue
            bind_addr, port = _decode_hex_addr(local, proto)
            if port is None:
                continue
            results.append({
                "proto": proto,
                "bind_addr": bind_addr,
                "port": port,
                "process": "",
                "pid": "",
            })
    return results


def _decode_hex_addr(local, proto):
    """Decode a /proc/net hex address pair like 00000000:0050."""
    if ":" not in local:
        return None, None
    hex_addr, hex_port = local.rsplit(":", 1)
    try:
        port = int(hex_port, 16)
    except ValueError:
        return None, None

    if proto.endswith("6"):
        # 32-char hex = 16-byte IPv6 address, little-endian groups
        addr_bytes = bytes.fromhex(hex_addr)
        # /proc/net stores 16-byte IPv6 as 4 little-endian u32 groups
        groups = []
        for i in range(0, 16, 4):
            g = addr_bytes[i:i + 4][::-1]
            groups.append(g.hex())
        bind_addr = ":".join(groups)
    else:
        # 8-char hex IPv4, little-endian
        try:
            n = int(hex_addr, 16)
        except ValueError:
            return None, port
        bind_addr = socket.inet_ntop(
            socket.AF_INET,
            n.to_bytes(4, byteorder="little"),
        )
    return bind_addr, port


# ---------------------------------------------------------------------------
# Risk assessment
# ---------------------------------------------------------------------------

def assess(entry, allow_ports):
    """Return a list of warning dicts for a single port entry."""
    warnings = []
    port = entry["port"]
    proc = entry["process"]

    if port in allow_ports:
        return []

    if port in RISKY_PORT_MAP:
        warnings.append({
            "severity": "high",
            "code": "risky_port",
            "message": RISKY_PORT_MAP[port],
        })

    for pattern, msg in RISKY_PROCESS_PATTERNS:
        if pattern.search(proc or ""):
            warnings.append({
                "severity": "medium",
                "code": "risky_process",
                "message": msg,
            })
            break

    # Flag anything bound to all interfaces that is not a common safe port.
    is_wildcard = entry["bind_addr"] in ("0.0.0.0", "*", "::", "::0")
    if is_wildcard and port not in COMMON_SAFE_PORTS and port not in allow_ports:
        # Only add this if we have no other warning to avoid pure noise.
        if not warnings:
            warnings.append({
                "severity": "low",
                "code": "exposed_on_wildcard",
                "message": (
                    "Listening on all interfaces; restrict to localhost if "
                    "not intended for external access"
                ),
            })
    return warnings


# ---------------------------------------------------------------------------
# Main scan pipeline
# ---------------------------------------------------------------------------

def scan(allow_ports, interface):
    """Run a full scan and return (entries, warnings)."""
    ports = parse_ss_output()
    if not ports:
        ports = parse_proc_net()

    # Deduplicate identical entries (ss and proc can overlap).
    seen = set()
    unique = []
    for p in ports:
        key = (p["proto"], p["bind_addr"], p["port"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    # Optional interface filter.
    if interface:
        iface_addrs = _addresses_for_interface(interface)
        unique = [
            p for p in unique
            if p["bind_addr"] in ("0.0.0.0", "*", "::", "::0") or
               p["bind_addr"] in iface_addrs or
               p["bind_addr"] == interface
        ]

    # Build final entries with warnings.
    entries = []
    for p in unique:
        warnings = assess(p, allow_ports)
        entry = OrderedDict()
        entry["proto"] = p["proto"]
        entry["bind_addr"] = p["bind_addr"]
        entry["port"] = p["port"]
        entry["process"] = p["process"]
        entry["pid"] = p["pid"]
        entry["warnings"] = warnings
        entries.append(entry)

    return entries


def _addresses_for_interface(interface):
    """Return IPv4/IPv6 addresses bound to a named interface via `ip`."""
    addrs = set()
    raw = run(["ip", "-o", "addr", "show", "dev", interface])
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] in ("inet", "inet6"):
            addr = parts[3].split("/")[0]
            addrs.add(addr)
    return addrs


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_text(entries, quiet):
    """Render a human-readable text report."""
    if not entries:
        return "port-warden: no listening ports found."

    lines = []
    lines.append("port-warden: {} listening socket(s) found".format(len(entries)))
    lines.append("PROTO   BIND ADDR            PORT    PROCESS            WARNINGS")
    lines.append("-" * 70)

    any_warning = False
    for e in entries:
        warn_sev = [w["severity"] for w in e["warnings"]]
        warn_text = ""
        if warn_sev:
            worst = "high" if "high" in warn_sev else (
                "medium" if "medium" in warn_sev else "low"
            )
            warn_text = "{}: {}".format(worst, "; ".join(
                w["message"] for w in e["warnings"]
            ))
            any_warning = True

        proc = (e["process"] or "?")[:18]
        line = "{:<7} {:<20} {:<7} {:<18} {}".format(
            e["proto"], e["bind_addr"], e["port"], proc, warn_text
        )
        lines.append(line)

    lines.append("-" * 70)
    lines.append("Summary: {} socket(s), {} with warnings.".format(
        len(entries), sum(1 for e in entries if e["warnings"])
    ))

    if quiet and not any_warning:
        return ""

    return "\n".join(lines).rstrip()


def format_json(entries):
    """Render a JSON report."""
    return json.dumps(
        {
            "version": __version__,
            "host": socket.gethostname(),
            "socket_count": len(entries),
            "warning_count": sum(1 for e in entries if e["warnings"]),
            "entries": list(entries),
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="port-warden",
        description="Audit listening TCP/UDP ports on this Linux host.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output machine-readable JSON instead of a text table.",
    )
    parser.add_argument(
        "--allow", default="",
        help="Comma-separated list of ports to treat as always-safe.",
    )
    parser.add_argument(
        "--interface", default=None,
        help="Only report ports bound to this network interface.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress output when nothing is flagged. Good for cron.",
    )
    parser.add_argument(
        "--version", action="version", version="port-warden {}".format(__version__)
    )
    args = parser.parse_args()

    allow_ports = set()
    if args.allow:
        for tok in args.allow.split(","):
            tok = tok.strip()
            if tok.isdigit():
                allow_ports.add(int(tok))

    entries = scan(allow_ports, args.interface)

    if args.json:
        print(format_json(entries))
    else:
        report = format_text(entries, args.quiet)
        if report:
            print(report)

    warning_count = sum(1 for e in entries if e["warnings"])

    # Exit non-zero if risky ports were found -- ideal for cron wrappers.

    has_high = any(
        w["severity"] == "high"
        for e in entries
        for w in e["warnings"]
    )
    if has_high:
        return 2
    if warning_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(int(main() or 0))
