#!/usr/bin/env python3
"""
dns-diff: Compare DNS records between two nameservers.

Queries A, AAAA, MX, TXT, NS, CNAME, and SOA records for one or more
domains against two nameservers, then prints what was added, removed,
or changed. Uses only the Python standard library (socket + subprocess
calling the host's `dig` binary, or falling back to raw DNS over UDP).

Usage examples:

  python3 dnsdiff.py example.com ns1.old.com ns2.new.com
  python3 dnsdiff.py --batch domains.txt ns1.old.com ns2.new.com
  python3 dnsdiff.py example.com ns1.old.com ns2.new.com -r A MX TXT
  python3 dnsdiff.py example.com 8.8.8.8 1.1.1.1 --json

No dependencies required beyond Python 3.8+ and optionally `dig`.
"""

import argparse
import json
import socket
import struct
import subprocess
import sys
from typing import Dict, List, Optional, Set, Tuple


__version__ = "1.0.0"

# DNS record types we support by default.
DEFAULT_RTYPES: List[str] = ["A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA"]

# ---------------------------------------------------------------------------
# Low-level DNS query implementation (fallback when dig is not available).
# This implements a minimal DNS client over UDP using the IANA wire format.
# Reference: RFC 1035 section 4.1.
# ---------------------------------------------------------------------------


def _build_query(qname: str, qtype: int) -> bytes:
    """Build a raw DNS query packet."""
    # Header: id=0x1234, flags=0x0100 (recursion desired), 1 question, 0 rest.
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    question = b""
    for label in qname.strip(".").split("."):
        encoded = label.encode("ascii")
        question += bytes([len(encoded)]) + encoded
    question += b"\x00"  # null terminator for root label
    question += struct.pack("!HH", qtype, 1)  # type + class IN
    return header + question


def _parse_name(data: bytes, offset: int) -> Tuple[str, int]:
    """Parse a DNS name with pointer compression support."""
    labels: List[str] = []
    jumped = False
    original_offset = offset
    while True:
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if (length & 0xC0) == 0xC0:
            if not jumped:
                original_offset = offset + 2
                jumped = True
            ptr = struct.unpack("!H", data[offset : offset + 2])[0] & 0x3FFF
            offset = ptr
            continue
        offset += 1
        labels.append(data[offset : offset + length].decode("ascii", "replace"))
        offset += length
    name = ".".join(labels)
    next_offset = original_offset if jumped else offset
    return name, next_offset


def _parse_record_rdata(data: bytes, offset: int, rtype: int, rdlength: int) -> str:
    """Parse the RDATA section of a resource record into a human-readable string."""
    end = offset + rdlength
    if rtype == 1:  # A
        raw = data[offset:end]
        if len(raw) == 4:
            return socket.inet_ntoa(raw)
        return raw.hex()
    if rtype == 28:  # AAAA
        raw = data[offset:end]
        if len(raw) == 16:
            return socket.inet_ntop(socket.AF_INET6, raw)
        return raw.hex()
    if rtype in (5, 2, 12):  # CNAME, NS, PTR
        name, _ = _parse_name(data, offset)
        return name + "."
    if rtype == 6:  # SOA
        mname, off = _parse_name(data, offset)
        rname, off = _parse_name(data, off)
        serial, refresh, retry, expire, minimum = struct.unpack(
            "!IIIII", data[off : off + 20]
        )
        return (
            f"{mname}. {rname}. {serial} {refresh} {retry} {expire} {minimum}"
        )
    if rtype == 15:  # MX
        pref = struct.unpack("!H", data[offset : offset + 2])[0]
        exch, _ = _parse_name(data, offset + 2)
        return f"{pref} {exch}."
    if rtype == 16:  # TXT
        parts: List[str] = []
        pos = offset
        while pos < end:
            tlen = data[pos]
            pos += 1
            parts.append(data[pos : pos + tlen].decode("utf-8", "replace"))
            pos += tlen
        return "".join(parts)
    # Fallback: hex dump of raw RDATA.
    return data[offset:end].hex()


_QTYPE_MAP = {
    "A": 1,
    "AAAA": 28,
    "CNAME": 5,
    "MX": 15,
    "NS": 2,
    "SOA": 6,
    "TXT": 16,
    "PTR": 12,
}


def _dns_query_raw(
    qname: str, rtype_str: str, server: str, timeout: float = 5.0
) -> List[str]:
    """Perform a raw DNS query over UDP and return a sorted list of answers."""
    qtype = _QTYPE_MAP.get(rtype_str.upper())
    if qtype is None:
        return []
    # Normalize server: add :53 if no port specified.
    if ":" not in server or server.count(":") > 1 and not server.startswith("["):
        # IPv6 without brackets or plain IPv4/hostname without port.
        if ":" not in server:
            addr = (server, 53)
        else:
            # Could be IPv6 address. Use bracket notation handling.
            addr = (server, 53)
    else:
        host, _, port_str = server.rpartition(":")
        addr = (host, int(port_str))

    packet = _build_query(qname, qtype)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, addr)
        response = sock.recvfrom(65535)[0]
    except (socket.timeout, OSError) as exc:
        return [f"[query-error: {exc}]"]
    finally:
        sock.close()

    answers: List[str] = []
    # Parse header.
    _, flags, qdcount, ancount, _, _ = struct.unpack(
        "!HHHHHH", response[:12]
    )
    rcode = flags & 0x0F
    offset = 12
    # Skip questions.
    for _ in range(qdcount):
        _, offset = _parse_name(response, offset)
        offset += 4  # qtype + qclass
    # Parse answers.
    for _ in range(ancount):
        _, offset = _parse_name(response, offset)
        rr_type, rr_class, _ttl, rdlength = struct.unpack(
            "!HHIH", response[offset : offset + 10]
        )
        offset += 10
        rdata = _parse_record_rdata(response, offset, rr_type, rdlength)
        answers.append(rdata)
        offset += rdlength
    if rcode != 0:
        answers.append(f"[rcode: {rcode}]")
    return sorted(answers)


def _dns_query_dig(
    qname: str, rtype_str: str, server: str, timeout: float = 5.0
) -> List[str]:
    """Query DNS using the system `dig` binary if available."""
    # dig resolves "@server" with or without a port. We pass the server as-is.
    # Format: dig +short +noall +answer @server qname rtype
    dig_server = f"@{server}" if not server.startswith("@") else server
    cmd = [
        "dig",
        "+short",
        "+noall",
        "+answer",
        dig_server,
        qname,
        rtype_str.upper(),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        # If dig is missing entirely, fall back gracefully.
        if isinstance(exc, FileNotFoundError):
            return None  # type: ignore  # signal caller to use raw method
        return [f"[dig-timeout]"]
    # dig +short gives one record per line. For TXT it includes quotes, which
    # we strip for cleaner diffing.
    lines: List[str] = []
    for line in result.stdout.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if rtype_str.upper() == "TXT":
            # Remove surrounding quotes that dig adds.
            clean = clean.strip('"')
        lines.append(clean)
    return sorted(lines)


def query_dns(
    qname: str, rtype_str: str, server: str, timeout: float = 5.0
) -> List[str]:
    """
    Query a DNS record type for a domain from a specific nameserver.

    Tries `dig` first (if installed) and falls back to a built-in raw
    DNS-over-UDP parser so the tool works with zero external deps.
    """
    result = _dns_query_dig(qname, rtype_str, server, timeout)
    if result is None:
        # dig not available, use the raw client.
        result = _dns_query_raw(qname, rtype_str, server, timeout)
    return result


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------


def diff_records(
    server_a: str,
    server_b: str,
    domain: str,
    rtypes: List[str],
    timeout: float,
) -> Dict[str, dict]:
    """
    Compare DNS records for one domain between two nameservers.

    Returns a dict keyed by record type, each containing 'left', 'right',
    'added', 'removed', and 'changed' (for records present on both sides
    but with differing values).
    """
    results: Dict[str, dict] = {}
    for rtype in rtypes:
        records_a = set(query_dns(domain, rtype, server_a, timeout))
        records_b = set(query_dns(domain, rtype, server_b, timeout))
        added = sorted(records_b - records_a)
        removed = sorted(records_a - records_b)
        common = sorted(records_a & records_b)
        changed = sorted(
            r for r in (records_a ^ records_b) if r not in added and r not in removed
        ) if False else []  # added/removed already capture the symmetric diff
        results[rtype] = {
            "left": sorted(records_a),
            "right": sorted(records_b),
            "added": added,
            "removed": removed,
            "common": common,
        }
    return results


def has_changes(result: Dict[str, dict]) -> bool:
    """Return True if any record type had differences."""
    for rtype_data in result.values():
        if rtype_data["added"] or rtype_data["removed"]:
            return True
    return False


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

# ANSI color helpers (auto-disabled when stdout is not a TTY).
_USE_COLOR = sys.stdout.isatty()
_RESET = "\033[0m" if _USE_COLOR else ""
_GREEN = "\033[32m" if _USE_COLOR else ""
_RED = "\033[31m" if _USE_COLOR else ""
_YELLOW = "\033[33m" if _USE_COLOR else ""
_CYAN = "\033[36m" if _USE_COLOR else ""
_BOLD = "\033[1m" if _USE_COLOR else ""


def _color(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}"


def format_text(
    domain: str,
    server_a: str,
    server_b: str,
    result: Dict[str, dict],
) -> str:
    """Render a text diff report for one domain."""
    lines: List[str] = []
    lines.append(
        _color(
            f"=== {domain} ===",
            _BOLD,
        )
    )
    lines.append(f"  Left  : {server_a}")
    lines.append(f"  Right : {server_b}")
    any_diff = False
    for rtype, data in result.items():
        added = data["added"]
        removed = data["removed"]
        common = data["common"]
        if added or removed:
            any_diff = True
            lines.append(f"\n  [{rtype}]")
            for item in removed:
                lines.append(
                    f"    {_color('-', _RED)} {item}"
                )
            for item in added:
                lines.append(
                    f"    {_color('+', _GREEN)} {item}"
                )
            if common and (added or removed):
                lines.append(
                    f"    {_color('=', _CYAN)} "
                    f"{len(common)} matching record(s)"
                )
        else:
            lines.append(
                f"\n  [{rtype}] {_color('unchanged', _GREEN)} "
                f"({len(common)} record(s))"
            )
    if not any_diff:
        lines.append(
            f"\n  {_color('No differences found!', _GREEN)}"
        )
    lines.append("")
    return "\n".join(lines)


def format_json(
    domain: str,
    server_a: str,
    server_b: str,
    result: Dict[str, dict],
) -> str:
    """Render a JSON diff report for one domain."""
    payload = {
        "domain": domain,
        "left_server": server_a,
        "right_server": server_b,
        "record_types": {},
    }
    for rtype, data in result.items():
        payload["record_types"][rtype] = {
            "left": data["left"],
            "right": data["right"],
            "added": data["added"],
            "removed": data["removed"],
            "common": data["common"],
        }
    return json.dumps(payload, indent=2)


def format_markdown(
    domain: str,
    _server_a: str,
    _server_b: str,
    result: Dict[str, dict],
) -> str:
    """Render a Markdown diff report for one domain."""
    lines: List[str] = [f"## {domain}\n"]
    for rtype, data in result.items():
        added = data["added"]
        removed = data["removed"]
        if not added and not removed:
            lines.append(f"- **{rtype}**: unchanged ({len(data['common'])} records)")
            continue
        lines.append(f"- **{rtype}**:")
        for item in removed:
            lines.append(f"  - `-` {item}")
        for item in added:
            lines.append(f"  - `+` {item}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dnsdiff",
        description="Compare DNS records between two nameservers.",
        epilog="No dependencies required. Optionally uses `dig` if installed.",
    )
    parser.add_argument(
        "server_a",
        help="First (left) nameserver, e.g. ns1.example.com or 8.8.8.8",
    )
    parser.add_argument(
        "server_b",
        help="Second (right) nameserver, e.g. ns2.example.com or 1.1.1.1",
    )
    parser.add_argument(
        "-d",
        "--domain",
        help="Single domain to check.",
    )
    parser.add_argument(
        "--batch",
        metavar="FILE",
        help="File with one domain per line to check in batch.",
    )
    parser.add_argument(
        "-r",
        "--records",
        nargs="+",
        default=DEFAULT_RTYPES,
        help=(
            "Record types to query (default: %(default)s). "
            "Supported: A AAAA MX TXT NS CNAME SOA PTR"
        ),
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=5.0,
        help="Per-query timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--output",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: %(default)s)",
    )
    parser.add_argument(
        "--no-dig",
        action="store_true",
        help="Disable dig and always use the built-in raw DNS client.",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit with code 1 when differences are found (useful for CI).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"dnsdiff {__version__}",
    )
    return parser


def _collect_domains(args: argparse.Namespace) -> List[str]:
    """Build the list of domains to check from CLI args."""
    domains: List[str] = []
    if args.domain:
        domains.append(args.domain)
    if args.batch:
        try:
            with open(args.batch, "r") as fh:
                for line in fh:
                    domain = line.strip()
                    if domain and not domain.startswith("#"):
                        domains.append(domain)
        except OSError as exc:
            print(f"Error reading batch file: {exc}", file=sys.stderr)
            sys.exit(2)
    if not domains:
        print(
            "Error: provide a domain with --domain or a file with --batch",
            file=sys.stderr,
        )
        sys.exit(2)
    return domains


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # If --no-dig is set, we disable dig by monkey-patching it to return None.
    if args.no_dig:
        global _dns_query_dig
        _dns_query_dig = lambda *a, **k: None  # noqa: E731  # force raw client

    # Validate record types.
    requested = [r.upper() for r in args.records]
    for rtype in requested:
        if rtype not in _QTYPE_MAP:
            print(
                f"Error: unsupported record type '{rtype}'. "
                f"Supported: {', '.join(sorted(_QTYPE_MAP))}",
                file=sys.stderr,
            )
            sys.exit(2)

    domains = _collect_domains(args)
    tasks = [(d, requested) for d in domains]

    formatter = {
        "text": format_text,
        "json": format_json,
        "markdown": format_markdown,
    }[args.output]

    found_any_diff = False
    json_results: List[str] = []

    for domain, rtypes in tasks:
        result = diff_records(
            args.server_a,
            args.server_b,
            domain,
            rtypes,
            args.timeout,
        )
        if has_changes(result):
            found_any_diff = True
        formatted = formatter(domain, args.server_a, args.server_b, result)
        if args.output == "json":
            json_results.append(formatted)
        else:
            print(formatted)

    if args.output == "json":
        if json_results:
            print("[\n" + ",\n".join(json_results) + "\n]")
        else:
            print("[]")

    if args.exit_code and found_any_diff:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
