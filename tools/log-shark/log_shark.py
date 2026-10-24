#!/usr/bin/env python3
"""
log_shark.py - Zero-dependency log analyzer for common log formats.

Parses syslog, journalctl JSON, nginx access logs, and Apache access logs,
then surfaces useful summaries: top client IPs, HTTP status code breakdowns,
error spikes, and the busiest endpoints. Designed for cron and quick SSH checks.

Author: cappy-dev
License: AGPL-3.0
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Log line patterns
# ---------------------------------------------------------------------------

# nginx combined log format example:
# 192.168.1.5 - - [10/Jul/2026:13:05:42 +0000] "GET /index.html HTTP/1.1" 200 612 "https://ref.example.com" "Mozilla/5.0"
NGINX_RE = re.compile(
    r'(?P<ip>\d+\.\d+\.\d+\.\d+)'
    r'\s+\S+\s+\S+\s+'
    r'\[(?P<ts>[^\]]+)\]'
    r'\s+"(?P<method>\w+)\s+(?P<path>\S+)\s+(?P<proto>[^"]+)"'
    r'\s+(?P<status>\d{3})'
    r'\s+(?P<size>\S+)'
    r'(?:\s+"(?P<ref>[^"]*)"\s+"(?P<ua>[^"]*)")?'
)

# Apache common log format example:
# 192.168.1.5 - - [10/Jul/2026:13:05:42 +0000] "GET /index.html HTTP/1.1" 200 612
APACHE_RE = re.compile(
    r'(?P<ip>\d+\.\d+\.\d+\.\d+)'
    r'\s+\S+\s+\S+\s+'
    r'\[(?P<ts>[^\]]+)\]'
    r'\s+"(?P<method>\w+)\s+(?P<path>\S+)\s+(?P<proto>[^"]*)"'
    r'\s+(?P<status>\d{3})'
    r'\s+(?P<size>\S+)'
)

# syslog example:
# Jul 10 13:05:42 myhost sshd[1234]: Accepted publickey for root from 10.0.0.5
SYSLOG_RE = re.compile(
    r'(?P<ts>\w{3}\s+\d+\s+\d+:\d+:\d+)'
    r'\s+(?P<host>\S+)'
    r'\s+(?P<proc>[^:\[]+)(?:\[(?P<pid>\d+)\])?:'
    r'\s+(?P<msg>.*)'
)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class LogEntry:
    """A single normalized log entry."""
    __slots__ = ("ip", "status", "method", "path", "timestamp", "message", "proc", "raw")

    def __init__(self, raw=""):
        self.ip = None
        self.status = None
        self.method = None
        self.path = None
        self.timestamp = None
        self.message = None
        self.proc = None
        self.raw = raw


def _parse_journalctl_json(line, entry):
    """Parse a single journalctl --output=json line."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return False
    entry.message = obj.get("MESSAGE", "")
    entry.proc = obj.get("SYSLOG_IDENTIFIER") or obj.get("_COMM") or ""
    ts_us = obj.get("__REALTIME_TIMESTAMP")
    if ts_us:
        try:
            entry.timestamp = datetime.fromtimestamp(int(ts_us) / 1_000_000)
        except (ValueError, OSError):
            entry.timestamp = None
    # Try to extract an IP from the message body.
    m = re.search(r'(\d+\.\d+\.\d+\.\d+)', entry.message or "")
    if m:
        entry.ip = m.group(1)
    # Try to understand common error keywords.
    lowered = (entry.message or "").lower()
    if any(kw in lowered for kw in ("error", "fail", "panic", "fatal")):
        entry.status = "ERR"
    return True


def _parse_access(line, pattern, entry):
    """Parse nginx or Apache access log line."""
    m = pattern.match(line)
    if not m:
        return False
    entry.ip = m.group("ip")
    entry.status = m.group("status")
    entry.method = m.group("method")
    entry.path = m.group("path")
    try:
        entry.timestamp = datetime.strptime(m.group("ts"), "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        entry.timestamp = None
    return True


def _parse_syslog(line, entry):
    """Parse traditional syslog lines."""
    m = SYSLOG_RE.match(line)
    if not m:
        return False
    entry.timestamp = _guess_syslog_ts(m.group("ts"))
    entry.proc = m.group("proc").strip()
    entry.message = m.group("msg")
    ip_m = re.search(r'(\d+\.\d+\.\d+\.\d+)', entry.message or "")
    if ip_m:
        entry.ip = ip_m.group(1)
    lowered = (entry.message or "").lower()
    if any(kw in lowered for kw in ("error", "fail", "panic", "fatal", "denied")):
        entry.status = "ERR"
    return True


def _guess_syslog_ts(text):
    """Convert a syslog timestamp (no year) to a datetime in the current year."""
    try:
        return datetime.strptime(f"{datetime.now().year} {text}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None


def detect_and_parse(line):
    """Return a LogEntry or None if the line matches no known format."""
    entry = LogEntry(raw=line.rstrip("\n"))

    stripped = line.strip()
    if not stripped:
        return None

    # JSON first (journalctl --output=json).
    if stripped.startswith("{") and _parse_journalctl_json(stripped, entry):
        return entry

    # nginx combined format (has referrer + user agent).
    if _parse_access(line, NGINX_RE, entry):
        return entry

    # Apache common format.
    if _parse_access(line, APACHE_RE, entry):
        return entry

    # Traditional syslog.
    if _parse_syslog(line, entry):
        return entry

    # Not recognized at all: keep a bare entry so we can count unparsed lines.
    entry.message = stripped
    entry.status = "UNPARSED"
    return entry


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(entries, window_minutes=5, top_n=10):
    """Compute summary statistics from a list of LogEntry objects."""
    total = len(entries)
    parsed = [e for e in entries if e.status != "UNPARSED"]
    unparsed = total - len(parsed)

    status_counts = Counter(e.status for e in parsed if e.status)
    ip_counts = Counter(e.ip for e in parsed if e.ip)
    path_counts = Counter(e.path for e in parsed if e.path)
    method_counts = Counter(e.method for e in parsed if e.method)
    proc_counts = Counter(e.proc for e in parsed if e.proc)

    error_entries = [
        e for e in parsed
        if e.status and (e.status == "ERR" or (e.status.isdigit() and int(e.status) >= 400))
    ]
    error_ip_counts = Counter(e.ip for e in error_entries if e.ip)

    # Error spike detection: count errors per time window.
    spikes = []
    if error_entries and window_minutes > 0:
        buckets = defaultdict(list)
        for e in error_entries:
            if e.timestamp:
                ts = e.timestamp
                # Normalize to tz-naive so access-log (offset-aware) and
                # syslog (offset-naive) timestamps are comparable.
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                bucket = ts.replace(
                    minute=(ts.minute // window_minutes) * window_minutes,
                    second=0,
                    microsecond=0,
                )
                buckets[bucket].append(e)
        for bucket, items in sorted(buckets.items()):
            if len(items) >= 3:
                spikes.append((bucket, len(items)))

    return {
        "total": total,
        "parsed": len(parsed),
        "unparsed": unparsed,
        "status_counts": status_counts,
        "ip_counts": ip_counts,
        "path_counts": path_counts,
        "method_counts": method_counts,
        "proc_counts": proc_counts,
        "error_count": len(error_entries),
        "error_ip_counts": error_ip_counts,
        "spikes": spikes,
        "top_n": top_n,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _bar(count, max_count, width=30):
    filled = int((count / max_count) * width) if max_count else 0
    return "#" * filled + "-" * (width - filled)


def _print_counter(title, counter, top_n, show_bar=True):
    print(f"\n  {title}")
    print(f"  {'-' * len(title)}")
    if not counter:
        print("  (none)")
        return
    max_val = max(counter.values())
    for key, count in counter.most_common(top_n):
        bar = _bar(count, max_val) if show_bar else ""
        display = key if key else "(blank)"
        print(f"  {display:<45} {count:>6}  {bar}")
    if sum(counter.values()) > sum(c for _, c in counter.most_common(top_n)):
        remaining = sum(counter.values()) - sum(c for _, c in counter.most_common(top_n))
        print(f"  {'...+others':<45} {remaining:>6}")


def render_report(stats):
    """Print a human-readable analysis report."""
    total = stats["total"]
    parsed = stats["parsed"]
    unparsed = stats["unparsed"]

    if total == 0:
        print("log-shark: no log lines found.")
        return

    pct = (parsed / total * 100) if total else 0
    print("+==========================================================+")
    print("|                    log-shark report                      |")
    print("+==========================================================+")
    print(f"\n  Total lines:  {total}")
    print(f"  Parsed:       {parsed} ({pct:.0f}%)")
    print(f"  Unparsed:     {unparsed}")
    print(f"  Errors/4xx+:  {stats['error_count']}")

    if stats["status_counts"]:
        _print_counter("HTTP status / error breakdown", stats["status_counts"], stats["top_n"])

    if stats["ip_counts"]:
        _print_counter("Top client IPs (all traffic)", stats["ip_counts"], stats["top_n"])

    if stats["error_ip_counts"]:
        _print_counter("Top IPs producing errors", stats["error_ip_counts"], stats["top_n"])

    if stats["path_counts"]:
        _print_counter("Busiest endpoints", stats["path_counts"], stats["top_n"])

    if stats["method_counts"]:
        _print_counter("HTTP methods", stats["method_counts"], stats["top_n"])

    if stats["proc_counts"]:
        _print_counter("Syslog processes", stats["proc_counts"], stats["top_n"])

    if stats["spikes"]:
        print("\n  Error spikes (>= 3 per window)")
        print("  " + "-" * 30)
        for bucket, count in stats["spikes"]:
            print(f"  {bucket.strftime('%Y-%m-%d %H:%M')}  {count:>6} errors")
    else:
        print("\n  Error spikes: none detected")

    print("\n+==========================================================+")


def render_json(stats):
    """Dump the analysis as JSON for scripting integration."""
    def ser(counter):
        return dict(counter.most_common(stats["top_n"]))

    payload = {
        "total": stats["total"],
        "parsed": stats["parsed"],
        "unparsed": stats["unparsed"],
        "error_count": stats["error_count"],
        "status_codes": ser(stats["status_counts"]),
        "top_ips": ser(stats["ip_counts"]),
        "top_error_ips": ser(stats["error_ip_counts"]),
        "top_paths": ser(stats["path_counts"]),
        "methods": ser(stats["method_counts"]),
        "processes": ser(stats["proc_counts"]),
        "spikes": [
            {"time": bucket.isoformat(), "count": count}
            for bucket, count in stats["spikes"]
        ],
    }
    print(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Input gathering
# ---------------------------------------------------------------------------

def gather_lines(args):
    """Yield log lines from files or stdin."""
    if not args.files or args.files == ["-"]:
        for line in sys.stdin:
            yield line
        return

    for path in args.files:
        if path == "-":
            for line in sys.stdin:
                yield line
            continue
        if os.path.isdir(path):
            # Recursively read all files in a directory.
            for root, _dirs, files in sorted(os.walk(path)):
                for fname in sorted(files):
                    full = os.path.join(root, fname)
                    try:
                        with open(full, "r", errors="replace") as fh:
                            for line in fh:
                                yield line
                    except OSError:
                        continue
        else:
            try:
                with open(path, "r", errors="replace") as fh:
                    for line in fh:
                        yield line
            except OSError as exc:
                print(f"log-shark: cannot read {path}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="log-shark",
        description="Zero-dependency log analyzer. Parses syslog, journalctl JSON, "
                    "nginx, and Apache access logs and prints a quick summary.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        default=None,
        help="Log file(s) or a directory to scan. Reads stdin if omitted.",
    )
    parser.add_argument(
        "-n", "--top",
        type=int,
        default=10,
        help="Number of top entries to show per category (default: 10).",
    )
    parser.add_argument(
        "-w", "--window",
        type=int,
        default=5,
        help="Window size in minutes for error spike detection (default: 5).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of the text report.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print nothing unless errors or spikes are found. "
             "Exit non-zero if errors are detected (cron-friendly).",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    entries = []
    for line in gather_lines(args):
        entry = detect_and_parse(line)
        if entry is not None:
            entries.append(entry)

    stats = analyze(entries, window_minutes=args.window, top_n=args.top)

    if args.quiet:
        if stats["error_count"] == 0 and not stats["spikes"]:
            return 0
        if not args.json:
            render_report(stats)
        else:
            render_json(stats)
        return 1

    if args.json:
        render_json(stats)
    else:
        render_report(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
