#!/usr/bin/env python3
"""net-pulse: Zero-dependency internet connectivity monitor for homelabs.

Measures latency, DNS resolution, and download speed against configurable
targets. Designed to run from cron and exit non-zero when connectivity is
degraded, so schedulers and monitoring stacks can pick it up easily.

Requires only Python >= 3.8 and the standard library.

Exit codes:
    0  all targets healthy
    1  at least one target unhealthy (latency over threshold, DNS failed,
       or speed under threshold)
    2  usage error / bad config

Usage examples:
    python3 net_pulse.py
    python3 net_pulse.py --config /etc/net-pulse.toml
    python3 net_pulse.py --json
    python3 net_pulse.py --report /var/www/html/net-pulse.html
    python3 net_pulse.py --latency-warn 200 --speed-warn 5.0
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.request import urlopen, Request

__version__ = "1.0.0"

DEFAULT_TARGETS = [
    {"host": "1.1.1.1", "label": "Cloudflare DNS", "type": "ping"},
    {"host": "8.8.8.8", "label": "Google DNS", "type": "ping"},
    {"url": "https://www.google.com", "label": "Google homepage",
     "type": "http"},
    {"url": "https://www.cloudflare.com", "label": "Cloudflare",
     "type": "http"},
    {"host": "github.com", "label": "GitHub DNS",
     "type": "dns", "resolve": "github.com"},
    {"url": "https://speed.cloudflare.com/__down?bytes=1000000",
     "label": "Cloudflare 1MB download", "type": "speed"},
]

# Thresholds that mean "unhealthy".
DEFAULT_LATENCY_WARN_MS = 250.0
DEFAULT_SPEED_WARN_MBPS = 1.0
DEFAULT_DNS_TIMEOUT_SEC = 3.0
DEFAULT_HTTP_TIMEOUT_SEC = 8.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Sample:
    label: str
    kind: str
    ok: bool
    detail: str = ""
    latency_ms: Optional[float] = None
    speed_mbps: Optional[float] = None
    resolved: Optional[str] = None


@dataclass
class Result:
    generated_at: str
    samples: List[Sample] = field(default_factory=list)
    healthy: bool = True
    summary: str = ""

    def add(self, s: Sample) -> None:
        self.samples.append(s)
        if not s.ok:
            self.healthy = False

    def finalize(self) -> None:
        ok = sum(1 for s in self.samples if s.ok)
        total = len(self.samples)
        bad = [s for s in self.samples if not s.ok]
        if not bad:
            latencies = [s.latency_ms for s in self.samples
                         if s.latency_ms is not None]
            avg = statistics.mean(latencies) if latencies else 0.0
            self.summary = (f"All {total} targets healthy "
                            f"(avg latency {avg:.0f} ms).")
        else:
            names = ", ".join(s.label for s in bad)
            self.summary = (f"{len(bad)}/{total} targets unhealthy: {names}")


# --------------------------------------------------------------------------- #
# Config parsing (tiny TOML subset, no external dep).
# --------------------------------------------------------------------------- #

def _read_config(path: str) -> Tuple[dict, List[dict]]:
    """Read a minimal TOML file: [[targets]] tables plus key/value settings.

    We only support the subset this tool needs so we stay dependency-free.
    Lines like:
        latency_warn_ms = 250
        speed_warn_mbps = 1.0
        [[targets]]
        url = "https://example.com"
        label = "Example"
        type = "http"
    """
    settings: dict = {}
    targets: List[dict] = []
    current: Optional[dict] = None
    in_targets_table = False

    if not os.path.exists(path):
        return settings, targets

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"net-pulse: cannot read config {path}: {exc}")

    for lineno, line in enumerate(raw.splitlines(), 1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        low = stripped.lower()
        if low.startswith("[[targets]]"):
            current = {}
            targets.append(current)
            in_targets_table = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            # Unknown table header; ignore its keys to stay forgiving.
            in_targets_table = False
            current = None
            continue
        if "=" not in stripped:
            raise SystemExit(
                f"net-pulse: {path}:{lineno}: not a key=value line")
        key, _, val = stripped.partition("=")
        key = key.strip()
        val = val.strip()
        parsed = _parse_toml_value(val, path, lineno)
        if in_targets_table and current is not None:
            current[key] = parsed
        else:
            settings[key] = parsed

    return settings, targets


def _parse_toml_value(val: str, path: str, lineno: int):
    if val.startswith('"""') and val.endswith('"""') and len(val) >= 6:
        return val[3:-3]
    if (val.startswith('"') and val.endswith('"')) or \
       (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        # Bare string: tolerate it but warn-style fallback to the raw text.
        return val


# --------------------------------------------------------------------------- #
# Probes.
# --------------------------------------------------------------------------- #

def _tcp_latency(host: str, port: int, timeout: float
                 ) -> Tuple[bool, Optional[float], str]:
    """Measure round-trip time of a TCP three-way handshake (no ICMP needed).

    This is a clean fallback when the OS ping command is unavailable and a
    decent approximation of "is this host reachable right now" for the kinds
    of public anycast services a homelab watches (1.1.1.1:53, 8.8.8.8:53).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    start = time.monotonic()
    try:
        sock.connect((host, port))
    except socket.timeout:
        return False, None, f"TCP connect timed out after {timeout}s"
    except OSError as exc:
        return False, None, f"TCP connect failed: {exc}"[:200]
    finally:
        sock.close()
    elapsed_ms = (time.monotonic() - start) * 1000.0
    return True, elapsed_ms, ""


def _ping_once(host: str, timeout_sec: float
               ) -> Tuple[bool, Optional[float], str, str]:
    """One latency probe via the OS ping command.

    Falls back to a TCP handshake probe (port 53 for IP literals, port 80
    for hostnames) when the ping binary is missing, so the tool still works
    in minimal containers without iputils-ping installed.

    Returns (ok, ms, detail, method).
    """
    cmd = ["ping", "-c", "1", "-W", str(int(timeout_sec)), host]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout_sec + 2.0)
    except FileNotFoundError:
        # Fallback: TCP handshake latency.
        port = 53 if _looks_like_ip(host) else 80
        ok, ms, detail = _tcp_latency(host, port, timeout_sec)
        return ok, ms, detail, "tcp"
    except subprocess.TimeoutExpired:
        return False, None, f"ping timed out after {timeout_sec}s", "icmp"

    out = (proc.stdout.decode("utf-8", "replace")
           if proc.stdout else "")
    if proc.returncode != 0:
        detail = (proc.stderr.decode("utf-8", "replace").strip()
                  or "ping failed")
        return False, None, detail[:200], "icmp"

    # Typical Linux output: "time=12.3 ms"
    token = "time="
    idx = out.rfind(token)
    if idx < 0:
        return True, 0.0, "", "icmp"
    rest = out[idx + len(token):]
    digits = ""
    for ch in rest:
        if ch.isdigit() or ch == ".":
            digits += ch
        else:
            break
    try:
        return True, float(digits), "", "icmp"
    except ValueError:
        return True, 0.0, "", "icmp"


def _looks_like_ip(host: str) -> bool:
    parts = host.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() for p in parts)


def probe_ping(target: dict, timeout: float) -> Sample:
    host = str(target.get("host", ""))
    label = target.get("label", host)
    samples_ms: List[float] = []
    last_detail = ""
    methods: set = set()
    rounds = 4
    for _ in range(rounds):
        ok, ms, detail, method = _ping_once(host, timeout)
        methods.add(method)
        if ok and ms is not None:
            samples_ms.append(ms)
        elif detail:
            last_detail = detail
        time.sleep(0.05)
    if not samples_ms:
        return Sample(label=label, kind="ping", ok=False,
                      detail=last_detail or "no replies received")
    avg = statistics.mean(samples_ms)
    jitter = (statistics.pstdev(samples_ms) if len(samples_ms) > 1 else 0.0)
    method_tag = "+".join(sorted(methods))
    sample = Sample(label=label, kind="ping", ok=True,
                    latency_ms=avg,
                    detail=f"avg {avg:.0f} ms, jitter {jitter:.0f} ms "
                           f"({method_tag}, n={len(samples_ms)})")
    return sample


def probe_dns(target: dict, timeout: float) -> Sample:
    host = str(target.get("resolve", target.get("host", "")))
    label = target.get("label", host)
    try:
        socket.setdefaulttimeout(timeout)
        addr_info = socket.getaddrinfo(host, None)
        socket.setdefaulttimeout(None)
    except socket.gaierror as exc:
        return Sample(label=label, kind="dns", ok=False,
                      detail=f"resolution failed: {exc}")
    except socket.timeout:
        return Sample(label=label, kind="dns", ok=False,
                      detail=f"timed out after {timeout}s")
    addrs = {info[4][0] for info in addr_info}
    if not addrs:
        return Sample(label=label, kind="dns", ok=False,
                      detail="no addresses returned")
    picked = sorted(addrs)[0]
    return Sample(label=label, kind="dns", ok=True,
                  latency_ms=None,
                  resolved=picked,
                  detail=f"resolved to {picked}")


def probe_http(target: dict, timeout: float) -> Sample:
    url = str(target.get("url", ""))
    label = target.get("label", url)
    req = Request(url, headers={"User-Agent": f"net-pulse/{__version__}"})
    start = time.monotonic()
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", resp.getcode())
            # Drain a tiny bit so the connection is actually exercised.
            resp.read(2048)
    except Exception as exc:
        return Sample(label=label, kind="http", ok=False,
                      detail=f"{type(exc).__name__}: {exc}"[:200])
    elapsed_ms = (time.monotonic() - start) * 1000.0
    return Sample(label=label, kind="http", ok=True,
                  latency_ms=elapsed_ms,
                  detail=f"HTTP {status} in {elapsed_ms:.0f} ms")


def probe_speed(target: dict, timeout: float) -> Sample:
    url = str(target.get("url", ""))
    label = target.get("label", url)
    req = Request(url, headers={"User-Agent": f"net-pulse/{__version__}"})
    start = time.monotonic()
    downloaded = 0
    try:
        with urlopen(req, timeout=timeout) as resp:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                downloaded += len(chunk)
                if time.monotonic() - start > timeout:
                    break
    except Exception as exc:
        return Sample(label=label, kind="speed", ok=False,
                      detail=f"{type(exc).__name__}: {exc}"[:200])
    elapsed = time.monotonic() - start
    if elapsed <= 0 or downloaded <= 0:
        return Sample(label=label, kind="speed", ok=False,
                      detail="no bytes downloaded")
    mbps = (downloaded * 8) / 1_000_000.0 / elapsed
    return Sample(label=label, kind="speed", ok=True,
                  speed_mbps=mbps,
                  detail=f"{downloaded/1_000_000.0:.2f} MB in "
                         f"{elapsed:.2f}s = {mbps:.2f} Mbps")


PROBES = {
    "ping": probe_ping,
    "dns": probe_dns,
    "http": probe_http,
    "speed": probe_speed,
}


# --------------------------------------------------------------------------- #
# Health evaluation against thresholds.
# --------------------------------------------------------------------------- #

def _evaluate(sample: Sample, latency_warn: float,
              speed_warn: float) -> Optional[str]:
    """Return a reason string if the sample is unhealthy, else None."""
    if not sample.ok:
        return "probe failed"
    if sample.kind == "ping" and sample.latency_ms is not None:
        if sample.latency_ms > latency_warn:
            return f"latency {sample.latency_ms:.0f} ms over warn {latency_warn:.0f}"
    if sample.kind == "http" and sample.latency_ms is not None:
        if sample.latency_ms > latency_warn * 2:
            return f"http latency {sample.latency_ms:.0f} ms"
    if sample.kind == "speed" and sample.speed_mbps is not None:
        if sample.speed_mbps < speed_warn:
            return f"throughput {sample.speed_mbps:.2f} Mbps under warn {speed_warn:.2f}"
    return None


# --------------------------------------------------------------------------- #
# Output renderers.
# --------------------------------------------------------------------------- #

_STATUS_ICON = {True: "OK", False: "FAIL"}


def render_text(result: Result) -> str:
    lines = [f"net-pulse run at {result.generated_at}",
             result.summary, ""]
    for s in result.samples:
        status = _STATUS_ICON[s.ok]
        line = f"  [{status:4}] {s.label} ({s.kind})"
        if s.latency_ms is not None:
            line += f" - {s.latency_ms:.0f} ms"
        if s.speed_mbps is not None:
            line += f" - {s.speed_mbps:.2f} Mbps"
        if s.detail:
            line += f" - {s.detail}"
        lines.append(line)
    lines.append("")
    lines.append("Overall: " + ("HEALTHY" if result.healthy else "UNHEALTHY"))
    return "\n".join(lines)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>net-pulse status</title>
<style>
  :root {{
    --bg: #0f172a;
    --card: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --ok: #22c55e;
    --fail: #ef4444;
    --warn: #f59e0b;
    --border: #334155;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2rem;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                 Roboto, Helvetica, Arial, sans-serif;
  }}
  h1 {{ margin: 0 0 0.25rem; font-size: 1.6rem; }}
  .meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1.5rem; }}
  .pill {{
    display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
    font-size: 0.75rem; font-weight: 700; letter-spacing: 0.04em;
  }}
  .pill.ok {{ background: var(--ok); color: #052e16; }}
  .pill.fail {{ background: var(--fail); color: #2a0606; }}
  .grid {{
    display: grid; gap: 1rem;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 0.75rem; padding: 1rem 1.1rem;
  }}
  .card h2 {{ margin: 0 0 0.5rem; font-size: 1rem; }}
  .row {{
    display: flex; justify-content: space-between;
    color: var(--muted); font-size: 0.85rem; margin: 0.15rem 0;
  }}
  .row strong {{ color: var(--text); }}
  .detail {{ color: var(--muted); font-size: 0.8rem; margin-top: 0.4rem; }}
  footer {{ margin-top: 2rem; color: var(--muted); font-size: 0.8rem; }}
  a {{ color: var(--text); }}
</style>
</head>
<body>
  <h1>net-pulse <span class="pill {pill_class}">{pill_text}</span></h1>
  <p class="meta">
    Generated {generated_at} - {summary}
  </p>
  <div class="grid">
{cards}
  </div>
  <footer>
    net-pulse v{version} - zero-dependency connectivity monitor
  </footer>
</body>
</html>
"""


def render_html(result: Result) -> str:
    pill_class = "ok" if result.healthy else "fail"
    pill_text = "HEALTHY" if result.healthy else "UNHEALTHY"
    cards = []
    for s in result.samples:
        status_badge = "ok" if s.ok else "fail"
        status_text = "OK" if s.ok else "FAIL"
        rows = []
        if s.latency_ms is not None:
            rows.append(
                f'<div class="row"><span>Latency</span>'
                f'<strong>{s.latency_ms:.0f} ms</strong></div>')
        if s.speed_mbps is not None:
            rows.append(
                f'<div class="row"><span>Throughput</span>'
                f'<strong>{s.speed_mbps:.2f} Mbps</strong></div>')
        if s.resolved:
            rows.append(
                f'<div class="row"><span>Resolved</span>'
                f'<strong>{escape(s.resolved)}</strong></div>')
        rows.append(
            f'<div class="row"><span>Type</span><strong>{escape(s.kind)}'
            f'</strong></div>')
        detail_html = (f'<div class="detail">{escape(s.detail)}</div>'
                       if s.detail else "")
        cards.append(
            f'    <div class="card">\n'
            f'      <h2>{escape(s.label)} '
            f'<span class="pill {status_badge}">{status_text}</span></h2>\n'
            f'      {chr(10).join(rows)}\n'
            f'      {detail_html}\n'
            f'    </div>')
    return _HTML_TEMPLATE.format(
        pill_class=pill_class,
        pill_text=pill_text,
        generated_at=escape(result.generated_at),
        summary=escape(result.summary),
        cards="\n".join(cards),
        version=__version__,
    )


def render_json(result: Result) -> str:
    payload = {
        "generated_at": result.generated_at,
        "healthy": result.healthy,
        "summary": result.summary,
        "samples": [asdict(s) for s in result.samples],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #

def run(args: argparse.Namespace) -> Result:
    settings, cfg_targets = _read_config(args.config) if args.config else ({}, [])

    targets: List[dict]
    if cfg_targets:
        targets = cfg_targets
    elif args.config:
        # Explicit config file with no targets: warn but still probe.
        targets = []
    else:
        targets = list(DEFAULT_TARGETS)

    latency_warn = float(settings.get("latency_warn_ms", args.latency_warn))
    speed_warn = float(settings.get("speed_warn_mbps", args.speed_warn))
    dns_timeout = float(settings.get("dns_timeout_sec", DEFAULT_DNS_TIMEOUT_SEC))
    http_timeout = float(settings.get("http_timeout_sec", DEFAULT_HTTP_TIMEOUT_SEC))
    ping_timeout = float(settings.get("ping_timeout_sec", 1.0))

    result = Result(generated_at=_now_iso())

    for t in targets:
        kind = str(t.get("type", "")).strip().lower()
        probe = PROBES.get(kind)
        if probe is None:
            result.add(Sample(label=str(t.get("label", "?")),
                              kind=kind or "unknown", ok=False,
                              detail=f"unknown probe type {kind!r}"))
            continue
        timeout = {
            "ping": ping_timeout,
            "dns": dns_timeout,
            "http": http_timeout,
            "speed": http_timeout * 4,
        }.get(kind, http_timeout)
        sample = probe(t, timeout)
        reason = _evaluate(sample, latency_warn, speed_warn)
        if reason is not None:
            sample.ok = False
            sample.detail = (sample.detail + " | " if sample.detail else "") + reason
        result.add(sample)

    result.finalize()
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="net-pulse",
        description=("Zero-dependency internet connectivity monitor. "
                     "Probes latency, DNS, HTTP, and download throughput, "
                     "exits non-zero when anything is unhealthy."),
    )
    p.add_argument("--config", metavar="PATH",
                   help="path to a net-pulse.toml config file")
    p.add_argument("--json", action="store_true",
                   help="print results as JSON instead of plain text")
    p.add_argument("--report", metavar="PATH",
                   help="also write an HTML status page to this path")
    p.add_argument("--latency-warn", type=float,
                   default=DEFAULT_LATENCY_WARN_MS,
                   help=f"ping latency warning threshold in ms "
                        f"(default {DEFAULT_LATENCY_WARN_MS})")
    p.add_argument("--speed-warn", type=float,
                   default=DEFAULT_SPEED_WARN_MBPS,
                   help=f"download throughput warning threshold in Mbps "
                        f"(default {DEFAULT_SPEED_WARN_MBPS})")
    p.add_argument("--quiet", action="store_true",
                   help="suppress stdout output (still writes --report if set)")
    p.add_argument("--version", action="version",
                   version=f"net-pulse {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = run(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            return 2
        raise

    rendered = render_json(result) if args.json else render_text(result)
    if not args.quiet:
        print(rendered)

    if args.report:
        try:
            Path(args.report).write_text(render_html(result),
                                         encoding="utf-8")
            if not args.quiet:
                print(f"\nHTML report written to {args.report}")
        except OSError as exc:
            print(f"warning: could not write report {args.report}: {exc}",
                  file=sys.stderr)

    return 0 if result.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
