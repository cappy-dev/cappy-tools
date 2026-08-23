#!/usr/bin/env python3
"""mem-sentinel: Zero-dependency RAM and swap usage monitor for Linux.

Reads /proc/meminfo and warns when memory or swap usage crosses configurable
thresholds. Designed for cron: prints nothing on success, writes a report
when a threshold is breached, and exits non-zero so your cron wrapper can
route it to an alert channel.

No external packages. Python 3.8+. Standard library only.

Examples
--------
  # one_shot check, exit 0 if everything is under 90%
  python3 mem_sentinel.py

  # warn at 80% RAM, 70% swap, emit an HTML report
  python3 mem_sentinel.py --mem-warn 80 --swap-warn 70 --html /var/www/mem.html

  # repeat-friendly: print a single JSON line for every run, good or bad
  python3 mem_sentinel.py --json --always

Exit codes
----------
  0  all good (or always-on mode printed a healthy line)
  1  a warning threshold was breached
  2  could not read memory info (unsupported platform, bad path, etc.)
  3  bad command line arguments
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import socket
import sys
from email.message import EmailMessage
from html import escape as _esc
from typing import Dict, List, Optional, Tuple

__version__ = "1.0.0"

PROC_MEMINFO = "/proc/meminfo"
PROC_LOADAVG = "/proc/loadavg"


def _read_meminfo(path: str = PROC_MEMINFO) -> Dict[str, int]:
    """Parse /proc/meminfo into a dict of field -> bytes.

    /proc/meminfo lines look like ``MemTotal:       16384000 kB``.
    We convert every value to bytes so downstream math is consistent.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise RuntimeError(f"could not read {path}: {exc}") from exc

    fields: Dict[str, int] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        value = value.strip()
        # Strip the trailing unit (always kB in /proc/meminfo on Linux).
        parts = value.split()
        if not parts:
            continue
        try:
            num = int(parts[0])
        except ValueError:
            continue
        if len(parts) > 1 and parts[1].lower() in ("kb", "kib"):
            num *= 1024
        fields[name] = num
    if "MemTotal" not in fields:
        raise RuntimeError(f"{path} did not contain a MemTotal line; is this Linux?")
    return fields


def _read_loadavg(path: str = PROC_LOADAVG) -> Optional[Tuple[float, float, float]]:
    """Return (1m, 5m, 15m) load averages, or None if unavailable."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            parts = fh.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError):
        return None


def _pct(used: int, total: int) -> float:
    """Safe percentage: 0.0 when total is zero instead of a divide error."""
    if total <= 0:
        return 0.0
    return (used / total) * 100.0


def collect(path: str = PROC_MEMINFO) -> Dict[str, object]:
    """Collect a single memory snapshot.

    Returns a dict with byte counts, percentages, and a few derived metrics
    (used, available, cached). ``buffers`` and ``cached`` are separate from
    ``available`` because some operators want to see them broken out.
    """
    info = _read_meminfo(path)

    mem_total = info.get("MemTotal", 0)
    mem_free = info.get("MemFree", 0)
    mem_available = info.get("MemAvailable", mem_free)
    buffers = info.get("Buffers", 0)
    cached = info.get("Cached", 0)
    # Slab is part of cached-ish kernel memory on most kernels.
    slab = info.get("Slab", 0)
    sreclaimable = info.get("SReclaimable", 0)

    # used = total - available is the modern Linux recommendation.
    # Fallback (older kernels without MemAvailable): total - free - buffers - cached.
    if "MemAvailable" in info:
        mem_used = max(mem_total - mem_available, 0)
    else:
        mem_used = max(mem_total - mem_free - buffers - cached, 0)

    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)

    snapshot: Dict[str, object] = {
        "host": socket.gethostname(),
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "mem": {
            "total": mem_total,
            "used": mem_used,
            "available": mem_available,
            "free": mem_free,
            "buffers": buffers,
            "cached": cached,
            "slab": slab,
            "sreclaimable": sreclaimable,
            "used_pct": round(_pct(mem_used, mem_total), 2),
            "available_pct": round(_pct(mem_available, mem_total), 2),
        },
        "swap": {
            "total": swap_total,
            "used": swap_used,
            "free": swap_free,
            "used_pct": round(_pct(swap_used, swap_total), 2),
        },
        "loadavg": _read_loadavg(),
    }
    return snapshot


def evaluate(snap: Dict[str, object], mem_warn: float, mem_crit: float,
             swap_warn: float, swap_crit: float) -> Dict[str, object]:
    """Decide which (if any) thresholds were breached.

    The worst state wins: if memory is critical and swap is merely warning,
    the overall state is ``critical``. ``swap`` is skipped entirely when no
    swap is configured (SwapTotal == 0).
    """
    mem_pct = float(snap["mem"]["used_pct"])  # type: ignore[index]
    swap_pct = float(snap["swap"]["used_pct"])  # type: ignore[index]
    swap_total = int(snap["swap"]["total"])  # type: ignore[index]

    states = []  # track each metric's individual state
    states.append(_classify("mem", mem_pct, mem_warn, mem_crit))

    if swap_total > 0:
        states.append(_classify("swap", swap_pct, swap_warn, swap_crit))
    else:
        states.append({"metric": "swap", "state": "skipped", "pct": 0.0,
                       "threshold": None, "message": "no swap configured"})

    # worst wins: critical > warning > ok > skipped
    order = {"critical": 3, "warning": 2, "ok": 1, "skipped": 0}
    worst = max(states, key=lambda s: order.get(s["state"], 0))

    return {
        "state": worst["state"],
        "states": states,
        "exit_code": 1 if worst["state"] in ("warning", "critical") else 0,
    }


def _classify(metric: str, pct: float, warn: float, crit: float) -> Dict[str, object]:
    if pct >= crit:
        return {"metric": metric, "state": "critical", "pct": pct,
                "threshold": crit,
                "message": f"{metric} usage {pct:.1f}% >= critical {crit:.1f}%"}
    if pct >= warn:
        return {"metric": metric, "state": "warning", "pct": pct,
                "threshold": warn,
                "message": f"{metric} usage {pct:.1f}% >= warning {warn:.1f}%"}
    return {"metric": metric, "state": "ok", "pct": pct, "threshold": None,
            "message": f"{metric} usage {pct:.1f}% under warning {warn:.1f}%"}


def _human(n: int) -> str:
    """Format bytes as a short human-readable string (KiB/MiB/GiB/TiB)."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(n) < 1024 or unit == "PiB":
            if unit == "B":
                return f"{n} {unit}"
            return f"{n:.1f} {unit}"
        n = n / 1024  # type: ignore[assignment]
    return f"{n} {unit}"


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def format_text(snap: Dict[str, object], verdict: Dict[str, object]) -> str:
    """Plain-text report. One block per metric, then a summary line."""
    lines: List[str] = []
    lines.append(f"mem-sentinel {__version__} on {snap['host']} "
                 f"at {snap['timestamp_utc']}")
    lines.append("")
    m = snap["mem"]  # type: ignore[index]
    lines.append("Memory")
    lines.append(f"  total      {_human(int(m['total']))}")  # type: ignore[index]
    lines.append(f"  used       {_human(int(m['used']))}   {m['used_pct']}%")  # type: ignore[index]
    lines.append(f"  available  {_human(int(m['available']))}   {m['available_pct']}%")  # type: ignore[index]
    lines.append(f"  buffers    {_human(int(m['buffers']))}")  # type: ignore[index]
    lines.append(f"  cached     {_human(int(m['cached']))}")  # type: ignore[index]
    lines.append("")
    s = snap["swap"]  # type: ignore[index]
    if int(s["total"]) > 0:  # type: ignore[index]
        lines.append("Swap")
        lines.append(f"  total  {_human(int(s['total']))}")  # type: ignore[index]
        lines.append(f"  used   {_human(int(s['used']))}   {s['used_pct']}%")  # type: ignore[index]
    else:
        lines.append("Swap: none configured")
    lines.append("")
    if snap["loadavg"] is not None:
        la = snap["loadavg"]  # type: ignore[index]
        lines.append(f"Load average: {la[0]:.2f} {la[1]:.2f} {la[2]:.2f}")  # type: ignore[index]
        lines.append("")
    for st in verdict["states"]:  # type: ignore[index]
        tag = st["state"].upper()  # type: ignore[index]
        lines.append(f"[{tag:8}] {st['metric']:5} {st['message']}")  # type: ignore[index]
    lines.append("")
    lines.append(f"Overall: {verdict['state'].upper()}")  # type: ignore[index]
    return "\n".join(lines)


def format_json(snap: Dict[str, object], verdict: Dict[str, object]) -> str:
    """Compact single-line JSON, ideal for piping into jq or a log shipper."""
    payload = {
        "tool": "mem-sentinel",
        "version": __version__,
        "host": snap["host"],
        "timestamp_utc": snap["timestamp_utc"],
        "mem_used_pct": snap["mem"]["used_pct"],  # type: ignore[index]
        "mem_available_pct": snap["mem"]["available_pct"],  # type: ignore[index]
        "swap_used_pct": snap["swap"]["used_pct"],  # type: ignore[index]
        "state": verdict["state"],
        "states": verdict["states"],
    }
    return json.dumps(payload, separators=(",", ":"))


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mem-sentinel on {host}</title>
<style>
  :root {{ --bg:#1a1a1a; --fg:#eaeaea; --acc:#6cf; --warn:#f5a623; --crit:#ff5c5c; --ok:#7bd88f; --muted:#888; }}
  body {{ background:var(--bg); color:var(--fg); font-family:system-ui,Segoe UI,Roboto,sans-serif; margin:0; padding:2rem; }}
  h1 {{ font-size:1.4rem; margin:0 0 .25rem; }}
  .sub {{ color:var(--muted); font-size:.9rem; margin-bottom:1.5rem; }}
  .card {{ background:#242424; border:1px solid #333; border-radius:10px; padding:1rem 1.25rem; margin-bottom:1rem; max-width:680px; }}
  .row {{ display:flex; justify-content:space-between; padding:.35rem 0; border-bottom:1px solid #2e2e2e; }}
  .row:last-child {{ border-bottom:none; }}
  .k {{ color:var(--muted); }}
  .v {{ font-variant-numeric:tabular-nums; }}
  .bar {{ height:10px; background:#333; border-radius:6px; overflow:hidden; margin:.4rem 0 .8rem; }}
  .fill {{ height:100%; transition:width .2s; }}
  .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .crit {{ color:var(--crit); }}
  .fill.ok {{ background:var(--ok); }} .fill.warn {{ background:var(--warn); }} .fill.crit {{ background:var(--crit); }}
  .badge {{ display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.8rem; font-weight:600; letter-spacing:.03em; }}
  .badge.ok {{ background:rgba(123,216,143,.15); color:var(--ok); }}
  .badge.warn {{ background:rgba(245,166,35,.15); color:var(--warn); }}
  .badge.crit {{ background:rgba(255,92,92,.15); color:var(--crit); }}
  .badge.skip {{ background:rgba(150,150,150,.15); color:var(--muted); }}
  .legend {{ color:var(--muted); font-size:.8rem; margin-top:.5rem; }}
</style>
</head>
<body>
  <h1>mem-sentinel on {host}</h1>
  <div class="sub">Snapshot generated {ts}</div>

  {cards}

  <div class="legend">Generated by mem-sentinel v{ver}. Zero dependencies, standard library only.</div>
</body>
</html>
"""


def _bar_css_class(state: str) -> str:
    return {"ok": "ok", "warning": "warn", "critical": "crit"}.get(state, "ok")


def format_html(snap: Dict[str, object], verdict: Dict[str, object]) -> str:
    """Standalone dark-mode HTML status page."""
    m = snap["mem"]  # type: ignore[index]
    s = snap["swap"]  # type: ignore[index]
    states = {st["metric"]: st for st in verdict["states"]}  # type: ignore[index]

    def _card(title: str, total: int, used: int, pct: float, state: str,
              extra: List[Tuple[str, str]]) -> str:
        fill = min(max(pct, 0.0), 100.0)
        cls = _bar_css_class(state)
        rows = "".join(
            f'<div class="row"><span class="k">{_esc(k)}</span>'
            f'<span class="v">{_esc(v)}</span></div>'
            for k, v in extra
        )
        return (
            f'<div class="card">'
            f'<div class="row"><span class="k">{_esc(title)} used</span>'
            f'<span class="v">{_esc(_human(used))} / {_esc(_human(total))}'
            f'&nbsp;<span class="{cls}">{pct:.1f}%</span></span></div>'
            f'<div class="bar"><div class="fill {cls}" style="width:{fill:.1f}%"></div></div>'
            f'{rows}'
            f'</div>'
        )

    mem_state = states.get("mem", {}).get("state", "ok")  # type: ignore[union-attr]
    swap_state = states.get("swap", {}).get("state", "skipped")  # type: ignore[union-attr]

    cards = _card("Memory", int(m["total"]), int(m["used"]), float(m["used_pct"]),  # type: ignore[index]
                  mem_state,
                  [("available", _human(int(m["available"]))),  # type: ignore[index]
                   ("free", _human(int(m["free"]))),  # type: ignore[index]
                   ("buffers", _human(int(m["buffers"]))),  # type: ignore[index]
                   ("cached", _human(int(m["cached"])))])  # type: ignore[index]

    if int(s["total"]) > 0:  # type: ignore[index]
        cards += _card("Swap", int(s["total"]), int(s["used"]),  # type: ignore[index]
                       float(s["used_pct"]), swap_state,  # type: ignore[index]
                       [("free", _human(int(s["free"])))])  # type: ignore[index]

    def _badge(state: str) -> str:
        mapping = {"ok": ("ok", "OK"), "warning": ("warn", "WARNING"),
                   "critical": ("crit", "CRITICAL"), "skipped": ("skip", "SKIPPED")}
        css, label = mapping.get(state, ("skip", state.upper()))
        return f'<span class="badge {css}">{label}</span>'

    badges = " ".join(
        f'<div class="row"><span class="k">{_esc(st["metric"])}</span>'  # type: ignore[index]
        f'<span>{_badge(st["state"])}&nbsp;{_esc(str(st["message"]))}</span></div>'  # type: ignore[index]
        for st in verdict["states"]  # type: ignore[index]
    )
    cards += f'<div class="card"><div class="row"><span class="k">Overall</span>' \
             f'<span>{_badge(verdict["state"])}</span></div>{badges}</div>'

    if snap["loadavg"] is not None:
        la = snap["loadavg"]  # type: ignore[index]
        cards += f'<div class="card"><div class="row"><span class="k">' \
                 f'Load average (1/5/15m)</span><span class="v">' \
                 f'{la[0]:.2f} {la[1]:.2f} {la[2]:.2f}</span></div></div>'  # type: ignore[index]

    return _HTML_TEMPLATE.format(
        host=_esc(str(snap["host"])),
        ts=_esc(str(snap["timestamp_utc"])),
        cards=cards,
        ver=__version__,
    )


# ---------------------------------------------------------------------------
# Optional webhook + email alerting (still zero dependency)
# ---------------------------------------------------------------------------


def _post_json(url: str, payload: str, timeout: float) -> Tuple[bool, str]:
    """POST JSON to a webhook URL using only urllib.

    Returns (ok, message). Never raises: callers want a single alert channel,
    and a broken webhook should not mask the actual threshold breach.
    """
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        url, data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(512).decode("utf-8", "replace")
            return resp.status in (200, 201, 202, 204), f"HTTP {resp.status} {body}"
    except urllib.error.URLError as exc:
        return False, f"URLError: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"unexpected: {exc}"


def _send_email(to_addr: str, from_addr: str, smtp_host: str, smtp_port: int,
                snap: Dict[str, object], verdict: Dict[str, object]) -> Tuple[bool, str]:
    """Send a plain alert email using smtplib. Best-effort, never raises."""
    import smtplib
    subj = f"[mem-sentinel] {verdict['state'].upper()} on {snap['host']}"
    body = format_text(snap, verdict)
    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as srv:
            srv.send_message(msg)
        return True, "sent"
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"email failed: {exc}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mem-sentinel",
        description="Zero-dependency RAM and swap usage monitor for Linux. "
                    "Reads /proc/meminfo, warns on configurable thresholds, "
                    "and exits non-zero when a threshold is breached. "
                    "Designed for cron.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--mem-warn", type=float, default=80.0,
                   metavar="PCT", help="warn when RAM usage >= PCT (default 80)")
    p.add_argument("--mem-crit", type=float, default=92.0,
                   metavar="PCT", help="critical when RAM usage >= PCT (default 92)")
    p.add_argument("--swap-warn", type=float, default=70.0,
                   metavar="PCT", help="warn when swap usage >= PCT (default 70)")
    p.add_argument("--swap-crit", type=float, default=90.0,
                   metavar="PCT", help="critical when swap usage >= PCT (default 90)")
    p.add_argument("--meminfo", default=PROC_MEMINFO,
                   metavar="PATH", help="path to a meminfo file (default /proc/meminfo)")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true", help="emit a single JSON line")
    out.add_argument("--html", metavar="PATH", help="write an HTML status page to PATH")
    p.add_argument("--always", action="store_true",
                   help="print output even when everything is OK (for log shippers)")
    p.add_argument("--webhook", metavar="URL",
                   help="POST a JSON payload to this URL when WARNING or CRITICAL")
    p.add_argument("--webhook-timeout", type=float, default=10.0,
                   metavar="SEC", help="webhook request timeout in seconds (default 10)")
    p.add_argument("--email-to", metavar="ADDR", help="alert email recipient")
    p.add_argument("--email-from", metavar="ADDR", help="alert email sender")
    p.add_argument("--smtp-host", default="localhost", help="SMTP host (default localhost)")
    p.add_argument("--smtp-port", type=int, default=25, help="SMTP port (default 25)")
    p.add_argument("--quiet", action="store_true",
                   help="suppress stdout when breaching thresholds (still exits non-zero)")
    p.add_argument("--version", action="version", version=f"mem-sentinel {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Validate thresholds: warn must be strictly less than crit.
    for label, lo, hi in (("mem", args.mem_warn, args.mem_crit),
                          ("swap", args.swap_warn, args.swap_crit)):
        if lo < 0 or hi < 0 or lo > 100 or hi > 100:
            print(f"mem-sentinel: {label} thresholds must be between 0 and 100",
                  file=sys.stderr)
            return 3
        if lo >= hi:
            print(f"mem-sentinel: {label} warn ({lo}) must be below crit ({hi})",
                  file=sys.stderr)
            return 3

    try:
        snap = collect(args.meminfo)
    except RuntimeError as exc:
        print(f"mem-sentinel: {exc}", file=sys.stderr)
        return 2

    verdict = evaluate(snap, args.mem_warn, args.mem_crit,
                       args.swap_warn, args.swap_crit)
    breached = verdict["exit_code"] == 1

    # Output routing. --json and --html can coexist with text-to-stdout.
    if args.json:
        if breached or args.always:
            print(format_json(snap, verdict))
    elif args.html:
        html = format_html(snap, verdict)
        try:
            with open(args.html, "w", encoding="utf-8") as fh:
                fh.write(html)
                fh.write("\n")
            if not args.quiet and breached:
                print(f"mem-sentinel: HTML report written to {args.html} "
                      f"(state: {verdict['state'].upper()})")
        except OSError as exc:
            print(f"mem-sentinel: could not write {args.html}: {exc}",
                  file=sys.stderr)
            return 2
    else:
        if breached or args.always:
            if not args.quiet:
                print(format_text(snap, verdict))

    # Optional alert channels, only when something is wrong.
    if breached:
        if args.webhook:
            ok, msg = _post_json(args.webhook, format_json(snap, verdict),
                                 args.webhook_timeout)
            if not ok and not args.quiet:
                print(f"mem-sentinel: webhook delivery failed: {msg}", file=sys.stderr)
        if args.email_to:
            if not args.email_from:
                print("mem-sentinel: --email-to requires --email-from",
                      file=sys.stderr)
                return 3
            ok, msg = _send_email(args.email_to, args.email_from,
                                  args.smtp_host, args.smtp_port, snap, verdict)
            if not ok and not args.quiet:
                print(f"mem-sentinel: email alert failed: {msg}", file=sys.stderr)

    return verdict["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
