#!/usr/bin/env python3
"""
cron-pulse: Zero-dependency cron job heartbeat monitor.

cron-pulse watches the pulse of your scheduled tasks. Each job checks in with a
simple "I ran" heartbeat to a local file (typically via a one-line wrapper at
the start or end of the crontab entry). cron-pulse then verifies every job that
was expected to fire actually fired within its grace window. If a job goes
silent, cron-pulse exits non-zero so a wrapper cron entry, systemd timer, or
external scheduler can raise the alarm (email, webhook, page, whatever).

Two modes:

  record   Called from inside a crontab entry to register a heartbeat.
           Example crontab line:
             0 3 * * *  /opt/scripts/backup.sh && cron-pulse record backup

  check    Reads every heartbeat, compares each job's last-seen time against
           its expected interval plus grace window, and reports which jobs are
           alive, late, or missing. Exits 0 when everything is green, 2 when
           any job is late, and 3 when any job has never checked in.

State lives in a single JSON file that you point cron-pulse at (default
~/.cron-pulse/state.json). Nothing is sent anywhere unless you wire it up.

Requires Python >=3.8 and the standard library only.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__version__ = "1.0.0"

# Exit codes are intentionally distinct so wrappers and schedulers can branch.
EXIT_OK = 0
EXIT_LATE = 2
EXIT_MISSING = 3
EXIT_HARD_ERROR = 4


def _now() -> float:
    """Return current UTC time as a POSIX timestamp. Wrapped for tests."""
    return time.time()


def _default_state_path() -> Path:
    home = os.environ.get("HOME", str(Path.home()))
    return Path(home) / ".cron-pulse" / "state.json"


def _utc_iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _human_age(seconds: float) -> str:
    """Turn a number of seconds into '2h 14m' style text."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


@dataclasses.dataclass
class JobState:
    name: str
    last_seen: float
    interval: int          # expected seconds between heartbeats
    grace: int             # extra seconds tolerated before "late"
    first_seen: float
    note: str = ""

    @classmethod
    def from_dict(cls, name: str, raw: dict) -> "JobState":
        return cls(
            name=name,
            last_seen=float(raw.get("last_seen", 0.0)),
            interval=int(raw.get("interval", 0)),
            grace=int(raw.get("grace", 0)),
            first_seen=float(raw.get("first_seen", raw.get("last_seen", 0.0))),
            note=str(raw.get("note", "")),
        )

    def to_dict(self) -> dict:
        return {
            "last_seen": self.last_seen,
            "interval": self.interval,
            "grace": self.grace,
            "first_seen": self.first_seen,
            "note": self.note,
        }


def load_state(path: Path) -> Dict[str, JobState]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        # Corrupt state should not take down the whole monitor.
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, JobState] = {}
    for name, entry in raw.items():
        if isinstance(entry, dict):
            out[name] = JobState.from_dict(name, entry)
    return out


def save_state(path: Path, state: Dict[str, JobState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: job.to_dict() for name, job in state.items()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _parse_interval(text: str) -> int:
    """Parse '5m', '2h', '1d', or a bare integer of seconds."""
    text = text.strip().lower()
    if not text:
        raise ValueError("empty interval")
    if text.isdigit():
        return int(text)
    unit, value = text[-1], text[:-1]
    if not value.isdigit():
        raise ValueError(f"bad interval: {text!r}")
    n = int(value)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if unit not in multipliers:
        raise ValueError(f"unknown unit {unit!r} in {text!r}")
    return n * multipliers[unit]


def cmd_record(args: argparse.Namespace) -> int:
    state_path: Path = Path(args.state).expanduser()
    state = load_state(state_path)
    now = _now()
    interval = _parse_interval(args.interval)
    existing = state.get(args.name)
    if existing is None:
        state[args.name] = JobState(
            name=args.name,
            last_seen=now,
            interval=interval,
            grace=int(args.grace),
            first_seen=now,
            note=args.note or "",
        )
    else:
        existing.last_seen = now
        existing.interval = interval
        existing.grace = int(args.grace)
        if args.note:
            existing.note = args.note
        # Keep first_seen from the original registration.
    save_state(state_path, state)
    print(f"[cron-pulse] recorded heartbeat for {args.name!r} at {_utc_iso(now)}")
    return EXIT_OK


def _classify(job: JobState, now: float) -> Tuple[str, float]:
    """Return (status, seconds_since_last_seen).

    Status is one of: never (no heartbeat yet), ok, late, dead.
    """
    if job.last_seen <= 0:
        return "never", 0.0
    age = now - job.last_seen
    budget = job.interval + job.grace
    if age <= budget:
        return "ok", age
    if age <= budget * 3:
        return "late", age
    return "dead", age


def cmd_status(args: argparse.Namespace) -> int:
    state_path: Path = Path(args.state).expanduser()
    state = load_state(state_path)
    if not state:
        print("[cron-pulse] no jobs registered yet.")
        print(f"[cron-pulse] state file would be: {state_path}")
        return EXIT_OK
    now = _now()
    rows: List[Tuple[str, str, str, str, str]] = []
    worst = EXIT_OK
    for name in sorted(state):
        job = state[name]
        status, age = _classify(job, now)
        if status == "never":
            worst = max(worst, EXIT_MISSING)
        elif status in ("late", "dead"):
            worst = max(worst, EXIT_LATE)
        rows.append(
            (
                name,
                status,
                _human_age(age) if status != "never" else "-",
                _human_age(job.interval),
                _utc_iso(job.last_seen) if job.last_seen else "never",
            )
        )
    col_widths = [max(len(r[i]) for r in rows) for i in range(5)]
    headers = ("JOB", "STATUS", "AGE", "INTERVAL", "LAST SEEN")
    col_widths = [max(w, len(h)) for w, h in zip(col_widths, headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in col_widths)))
    for row in rows:
        print(fmt.format(*row))
    print()
    if worst == EXIT_OK:
        print("[cron-pulse] all jobs alive.")
    elif worst == EXIT_LATE:
        late = [r[0] for r in rows if r[1] in ("late", "dead")]
        print(f"[cron-pulse] WARNING: late/dead jobs: {', '.join(late)}")
    elif worst == EXIT_MISSING:
        missing = [r[0] for r in rows if r[1] == "never"]
        print(f"[cron-pulse] WARNING: never checked in: {', '.join(missing)}")
    return worst


def cmd_check(args: argparse.Namespace) -> int:
    """Same as status but quiet by default; print only failures."""
    state_path: Path = Path(args.state).expanduser()
    state = load_state(state_path)
    if not state:
        # Nothing to check is a clean OK so an empty install does not page.
        return EXIT_OK
    now = _now()
    worst = EXIT_OK
    problems: List[str] = []
    for name in sorted(state):
        job = state[name]
        status, age = _classify(job, now)
        if status == "never":
            worst = max(worst, EXIT_MISSING)
            problems.append(f"{name}: never checked in")
        elif status == "late":
            worst = max(worst, EXIT_LATE)
            problems.append(f"{name}: late by {_human_age(age - job.interval - job.grace)}")
        elif status == "dead":
            worst = max(worst, EXIT_LATE)
            problems.append(f"{name}: dead (last seen {_human_age(age)} ago)")
    if problems:
        print("[cron-pulse] " + "; ".join(problems))
    elif args.verbose:
        print(f"[cron-pulse] all {len(state)} jobs alive.")
    return worst


def cmd_purge(args: argparse.Namespace) -> int:
    state_path: Path = Path(args.state).expanduser()
    state = load_state(state_path)
    if args.name:
        if args.name in state:
            del state[args.name]
            save_state(state_path, state)
            print(f"[cron-pulse] removed {args.name!r}")
        else:
            print(f"[cron-pulse] no such job: {args.name!r}")
            return EXIT_HARD_ERROR
    else:
        state.clear()
        save_state(state_path, state)
        print("[cron-pulse] state cleared.")
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    state_path: Path = Path(args.state).expanduser()
    state = load_state(state_path)
    if not state:
        print("(no jobs registered)")
        return EXIT_OK
    for name in sorted(state):
        job = state[name]
        print(f"{name}\tinterval={_human_age(job.interval)}\tgrace={_human_age(job.grace)}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cron-pulse",
        description="Zero-dependency cron job heartbeat monitor.",
    )
    parser.add_argument(
        "--state",
        default=str(_default_state_path()),
        help="path to the state JSON file (default: ~/.cron-pulse/state.json)",
    )
    parser.add_argument("--version", action="version", version=f"cron-pulse {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_record = sub.add_parser("record", help="register a heartbeat for a job")
    p_record.add_argument("name", help="unique job name, e.g. backup-db")
    p_record.add_argument(
        "interval",
        help="expected time between heartbeats, e.g. 5m, 2h, 1d, or 3600 (seconds)",
    )
    p_record.add_argument(
        "--grace",
        default="120",
        help="extra seconds tolerated before a job is late (default: 120)",
    )
    p_record.add_argument("--note", default="", help="free-form note for your own use")
    p_record.set_defaults(func=cmd_record)

    p_status = sub.add_parser("status", help="show every job and its health")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="quiet health check, exits non-zero on failure")
    p_check.add_argument("-v", "--verbose", action="store_true", help="print success line too")
    p_check.set_defaults(func=cmd_check)

    p_purge = sub.add_parser("purge", help="remove one job (name) or all jobs from state")
    p_purge.add_argument("name", nargs="?", default="", help="job to remove; omit to clear all")
    p_purge.set_defaults(func=cmd_purge)

    p_list = sub.add_parser("list", help="list registered jobs")
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return EXIT_HARD_ERROR
    except (ValueError, OSError) as exc:
        print(f"[cron-pulse] error: {exc}", file=sys.stderr)
        return EXIT_HARD_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
