#!/usr/bin/env python3
"""
restart_hawk.py - Watch long-running processes for unexpected restarts and silent exits.

Zero dependencies. Pure Python 3.8+ standard library.
Designed for cron: exit codes are machine-friendly.
  0  healthy: every watched process is alive and its PID has not changed.
  1  warning: at least one process restarted (PID changed) since last run.
  2  critical: at least one process has disappeared (no matching PID found).

Usage:
  ./restart_hawk.py watch nginx postgres redis        # watch processes by name
  ./restart_hawk.py --config hawks.ini                 # watch from a config file
  ./restart_hawk.py --status                           # print last known state
  ./restart_hawk.py --report html                      # emit an HTML status page
  ./restart_hawk.py --clear                             # erase all state files

Config file format (INI):
  [hawk]
  state_dir = ~/.restart-hawk          # where PID history lives
  alert_command =                      # optional: shell command run on any event
  [watches]
  nginx = nginx                        # name = match pattern (matches /proc/<pid>/comm)
  db = postgres
  cache = redis
"""

from __future__ import annotations

import argparse
import html
import os
import sys
import time
import json
import shlex
import subprocess
from pathlib import Path

__version__ = "1.0.0"

EXIT_OK = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2


def default_state_dir() -> Path:
    """Return the default state directory under the user's home."""
    home = os.environ.get("HOME", str(Path.home()))
    return Path(home) / ".restart-hawk"


def find_pids(pattern: str) -> list[int]:
    """Return a sorted list of PIDs whose process command name matches pattern.

    Uses /proc on Linux. Falls back to pgrep if /proc is unavailable.
    Matching is case-insensitive and compares against the short command name
    stored in /proc/<pid>/comm. A pattern longer than 15 characters (the
    kernel limit for comm) is truncated for comparison, because the kernel
    truncates it too.
    """
    pattern_lower = pattern.lower()[:15]
    pids: list[int] = []
    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            for entry in proc_root.iterdir():
                if not entry.name.isdigit():
                    continue
                comm_path = entry / "comm"
                try:
                    comm = comm_path.read_text().strip().lower()
                except (OSError, PermissionError):
                    continue
                if comm and comm == pattern_lower:
                    pids.append(int(entry.name))
        except (OSError, PermissionError):
            pass
        if pids:
            return sorted(pids)

    # Fallback to pgrep when /proc is not readable.
    try:
        proc = subprocess.run(
            ["pgrep", "-x", pattern],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return sorted(set(pids))


def load_state(state_dir: Path) -> dict:
    """Load the state file as a dict mapping watch name to last known PID."""
    state_file = state_dir / "state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state_dir: Path, state: dict) -> None:
    """Persist the state dict to the state directory as JSON."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "state.json"
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True))


def append_log(state_dir: Path, line: str) -> None:
    """Append a single timestamped line to the event log."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log_file = state_dir / "events.log"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S %z")
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {line}\n")


def run_alert_command(command: str, message: str) -> None:
    """Run an optional alert command, ignoring failures."""
    if not command:
        return
    quoted_message = shlex.quote(message)
    full_command = f"{command} {quoted_message}"
    try:
        subprocess.run(
            ["bash", "-c", full_command],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def load_config(config_path: str) -> tuple[dict, dict]:
    """Parse an INI config file.

    Returns a tuple of (hawk_options, watches_dict).
    hawk_options may contain state_dir and alert_command.
    watches_dict maps a friendly name to a match pattern.
    """
    import configparser

    parser = configparser.ConfigParser()
    parser.read(config_path)

    hawk_options: dict = {}
    if parser.has_section("hawk"):
        if parser.has_option("hawk", "state_dir"):
            raw = parser.get("hawk", "state_dir")
            hawk_options["state_dir"] = os.path.expanduser(raw)
        if parser.has_option("hawk", "alert_command"):
            hawk_options["alert_command"] = parser.get("hawk", "alert_command")

    watches: dict = {}
    if parser.has_section("watches"):
        for key in parser.options("watches"):
            watches[key] = parser.get("watches", key)
    return hawk_options, watches


def do_watch(args: argparse.Namespace) -> int:
    """Run a single watch pass and return an exit code."""
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    alert_command = ""
    watches: dict[str, str] = {}

    if args.config:
        hawk_options, config_watches = load_config(args.config)
        if "state_dir" in hawk_options and not args.state_dir:
            state_dir = Path(hawk_options["state_dir"])
        alert_command = hawk_options.get("alert_command", "")
        watches.update(config_watches)

    if args.names:
        for name in args.names:
            if "=" in name:
                key, value = name.split("=", 1)
                watches[key] = value
            else:
                watches[name] = name

    if not watches:
        print("restart_hawk: no watches configured. Add process names or a config file.",
              file=sys.stderr)
        return EXIT_CRITICAL

    state = load_state(state_dir)
    worst_exit = EXIT_OK
    events: list[str] = []

    for friendly_name, pattern in sorted(watches.items()):
        pids = find_pids(pattern)
        primary_pid = pids[0] if pids else None
        key = friendly_name
        previous = state.get(key)
        prev_pid = previous.get("pid") if isinstance(previous, dict) else previous

        if primary_pid is None:
            if prev_pid is not None:
                # Process was seen before but is gone now.
                msg = f"CRITICAL {friendly_name} ({pattern}): process vanished (was PID {prev_pid})"
                events.append(msg)
                print(msg)
                worst_exit = max(worst_exit, EXIT_CRITICAL)
            else:
                # Never seen and still missing: treat as critical on first sight.
                msg = f"CRITICAL {friendly_name} ({pattern}): not running (no PID found)"
                events.append(msg)
                print(msg)
                worst_exit = max(worst_exit, EXIT_CRITICAL)
            # Clear saved PID so we do not keep reporting the old one.
            state.pop(key, None)
        else:
            if prev_pid is None:
                # First time we see this process, or it came back after being gone.
                started_flag = previous is None
                if isinstance(previous, dict) and previous.get("pid") is None:
                    started_flag = True
                if started_flag:
                    msg = f"OK {friendly_name} ({pattern}): first sighting, PID {primary_pid}"
                    print(msg)
                else:
                    msg = (f"WARNING {friendly_name} ({pattern}): restarted, "
                           f"now PID {primary_pid} (was {prev_pid})")
                    events.append(msg)
                    print(msg)
                    worst_exit = max(worst_exit, EXIT_WARNING)
            elif prev_pid != primary_pid:
                msg = (f"WARNING {friendly_name} ({pattern}): restarted, "
                       f"now PID {primary_pid} (was {prev_pid})")
                events.append(msg)
                print(msg)
                worst_exit = max(worst_exit, EXIT_WARNING)
            else:
                msg = f"OK {friendly_name} ({pattern}): running PID {primary_pid}"
                print(msg)
            state[key] = {"pid": primary_pid, "pattern": pattern,
                          "timestamp": int(time.time())}

    save_state(state_dir, state)

    for evt in events:
        append_log(state_dir, evt)
        run_alert_command(alert_command, evt)

    return worst_exit


def do_status(args: argparse.Namespace) -> int:
    """Print the last known state in a human readable format."""
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    state = load_state(state_dir)
    if not state:
        print("No state found. Run a watch pass first.")
        return EXIT_OK

    print(f"{'Name':<20} {'Pattern':<20} {'PID':<10} {'Seen'}")
    print("-" * 70)
    for name in sorted(state):
        info = state[name]
        if isinstance(info, dict):
            pid = info.get("pid", "?")
            pattern = info.get("pattern", "?")
            ts = info.get("timestamp", 0)
            seen = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?"
        else:
            pid = info
            pattern = "?"
            seen = "?"
        print(f"{name:<20} {pattern:<20} {str(pid):<10} {seen}")
    return EXIT_OK


def do_report(args: argparse.Namespace) -> int:
    """Emit a status report. Format can be text or html."""
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    fmt = args.report
    state = load_state(state_dir)

    rows: list[tuple[str, str, int]] = []
    for name in sorted(state):
        info = state[name]
        if isinstance(info, dict):
            pid = info.get("pid") or 0
            pattern = info.get("pattern", name)
        else:
            pid = info or 0
            pattern = name
        rows.append((name, pattern, int(pid)))

    results: list[tuple[str, str, str, list[int]]] = []
    for name, pattern, _pid in rows:
        pids = find_pids(pattern)
        results.append((name, pattern, "UP" if pids else "DOWN", pids))

    if fmt == "html":
        return emit_html_report(results)
    return emit_text_report(results)


def emit_text_report(results: list[tuple[str, str, str, list[int]]]) -> int:
    """Print a plain text status report and return an exit code."""
    worst = EXIT_OK
    print(f"{'Name':<20} {'Pattern':<20} {'Status':<8} {'PIDs'}")
    print("-" * 70)
    for name, pattern, status, pids in results:
        pid_str = ", ".join(str(p) for p in pids) if pids else "-"
        print(f"{name:<20} {pattern:<20} {status:<8} {pid_str}")
        if status == "DOWN":
            worst = max(worst, EXIT_CRITICAL)
    return worst


def emit_html_report(results: list[tuple[str, str, str, list[int]]]) -> int:
    """Print a self-contained HTML status page and return an exit code."""
    worst = EXIT_OK
    rows_html = []
    for name, pattern, status, pids in results:
        escaped_name = html.escape(name)
        escaped_pattern = html.escape(pattern)
        color = "#2e7d32" if status == "UP" else "#c62828"
        pid_str = html.escape(", ".join(str(p) for p in pids) if pids else "-")
        rows_html.append(
            "      <tr>"
            f"<td>{escaped_name}</td>"
            f"<td>{escaped_pattern}</td>"
            f"<td style=\"color:{color};font-weight:bold\">{status}</td>"
            f"<td>{pid_str}</td>"
            "</tr>"
        )
        if status == "DOWN":
            worst = max(worst, EXIT_CRITICAL)

    overall = "All clear" if worst == EXIT_OK else "Problems detected"
    overall_color = "#2e7d32" if worst == EXIT_OK else "#c62828"
    generated = time.strftime("%Y-%m-%d %H:%M:%S %z")

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Restart Hawk Status</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
    h1 {{ font-size: 1.5rem; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
    th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #eee; }}
    th {{ background: #f7f7f7; font-size: 0.85rem; text-transform: uppercase;
         letter-spacing: 0.04em; }}
    .overall {{ font-size: 1.1rem; font-weight: bold;
               color: {overall_color}; margin: 0.5rem 0; }}
    .footer {{ margin-top: 1.5rem; font-size: 0.8rem; color: #888; }}
  </style>
</head>
<body>
  <h1>Restart Hawk</h1>
  <p class="overall">{html.escape(overall)}</p>
  <table>
    <thead>
      <tr><th>Name</th><th>Pattern</th><th>Status</th><th>PIDs</th></tr>
    </thead>
    <tbody>
{chr(10).join(rows_html) if rows_html else '      <tr><td colspan="4">No watches configured.</td></tr>'}
    </tbody>
  </table>
  <p class="footer">Generated {html.escape(generated)} by restart_hawk v{__version__}</p>
</body>
</html>
"""
    print(page)
    return worst


def do_clear(args: argparse.Namespace) -> int:
    """Erase the state directory contents."""
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    state_file = state_dir / "state.json"
    log_file = state_dir / "events.log"
    for target in (state_file, log_file):
        if target.exists():
            target.unlink()
    print(f"Cleared state in {state_dir}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Construct the command line parser."""
    parser = argparse.ArgumentParser(
        prog="restart_hawk",
        description="Watch long-running processes for unexpected restarts and silent exits.",
        epilog="Exit codes: 0 healthy, 1 warning (restart), 2 critical (vanished/missing).",
    )
    parser.add_argument("--version", action="version", version=f"restart_hawk {__version__}")
    parser.add_argument("--state-dir", default=None,
                        help="Directory for state files (default: ~/.restart-hawk)")

    sub = parser.add_subparsers(dest="command")

    watch_p = sub.add_parser("watch", help="Run a watch pass")
    watch_p.add_argument("names", nargs="*", help="Process names or name=pattern pairs")
    watch_p.add_argument("--config", default=None,
                        help="INI config file with [ hawk ] and [ watches ] sections")
    watch_p.add_argument("--state-dir", default=None,
                        help="Directory for state files (default: ~/.restart-hawk)")

    status_p = sub.add_parser("status", help="Print last known state")
    status_p.add_argument("--state-dir", default=None,
                         help="Directory for state files (default: ~/.restart-hawk)")

    report_p = sub.add_parser("report", help="Emit a status report")
    report_p.add_argument("report", nargs="?", default="text", choices=["text", "html"],
                         help="Report format: text (default) or html")
    report_p.add_argument("--state-dir", default=None,
                         help="Directory for state files (default: ~/.restart-hawk)")

    clear_p = sub.add_parser("clear", help="Erase all state files")
    clear_p.add_argument("--state-dir", default=None,
                         help="Directory for state files (default: ~/.restart-hawk)")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the command line."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "watch":
        return do_watch(args)
    if args.command == "status":
        return do_status(args)
    if args.command == "report":
        return do_report(args)
    if args.command == "clear":
        return do_clear(args)

    # No subcommand: treat trailing positional names as an implicit watch.
    if isinstance(args, argparse.Namespace) and any(
        key in vars(args) for key in ("names",)
    ):
        return do_watch(args)

    parser.print_help()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
