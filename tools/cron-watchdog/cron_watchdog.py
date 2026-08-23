#!/usr/bin/env python3
"""
cron-watchdog: Zero-dependency cron job monitor and alerting tool.

Tracks whether scheduled cron jobs actually ran on time by maintaining a
state file per job. When a job is expected to have run but its state file
is stale or missing, cron-watchdog raises an alert.

Usage:
    # In your crontab, wrap each job you want to monitor:
    #   */5 * * * * /path/to/backup.sh && /path/to/cron_watchdog.py touch backup
    #
    # Then run the watchdog itself periodically to check all tracked jobs:
    #   */5 * * * * /path/to/cron_watchdog.py check

Commands:
    touch <job_name> [--expect-within N]   Record that job_name ran now.
    check [--config CONFIG] [--alert-cmd CMD] [--state-dir DIR]
          Check all tracked jobs. Alerts for stale/missing jobs.
    list [--state-dir DIR]                 List tracked jobs and their status.

State file format (JSON, one file per job, named <job_name>.json):
    {"name": "...", "last_run": 1690000000, "expect_within": 300,
     "last_ok": true}

Exit codes:
    0  check passed, all jobs on time
    1  one or more jobs missed their expected run
    2  configuration or usage error
    3  unable to read/write state files

Configuration file (INI or JSON):
    [jobs "backup"]
    expect_within = 3600
    alert_cmd = /usr/bin/notify-send "cron-watchdog" "{job} missed"

    [jobs "syncthing"]
    expect_within = 120
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_STATE_DIR = os.environ.get("CRON_WATCHDOG_DIR", "/var/lib/cron-watchdog")
DEFAULT_EXPECT_WITHIN = 300  # seconds: a job must touch within this window
DEFAULT_CONFIG_PATHS = [
    "/etc/cron-watchdog.conf",
    os.path.expanduser("~/.config/cron-watchdog.conf"),
    os.path.expanduser("~/.cron-watchdog.conf"),
]

EXIT_OK = 0
EXIT_STALE = 1
EXIT_USAGE = 2
EXIT_IO_ERROR = 3

VERSION = "1.0.0"


def _now() -> int:
    return int(time.time())


def _load_config(path: str | None) -> dict:
    """Load a simple INI-style config. Returns a dict of job configs."""
    cfg: dict[str, dict] = {}
    search_paths: list[str]
    if path:
        search_paths = [path]
    else:
        search_paths = [p for p in DEFAULT_CONFIG_PATHS if os.path.isfile(p)]

    if not search_paths:
        return cfg

    try:
        import configparser

        parser = configparser.ConfigParser()
        parser.read(search_paths)
        for section in parser.sections():
            if section.startswith("jobs "):
                name = section.split(" ", 1)[1]
                cfg[name] = {
                    "expect_within": parser.getint(section, "expect_within", fallback=None),
                    "alert_cmd": parser.get(section, "alert_cmd", fallback=None),
                }
    except Exception:
        # Try JSON config as fallback
        for p in search_paths:
            try:
                with open(p) as f:
                    data = json.load(f)
                if isinstance(data, dict) and "jobs" in data:
                    for name, job_cfg in data["jobs"].items():
                        cfg[name] = {
                            "expect_within": job_cfg.get("expect_within"),
                            "alert_cmd": job_cfg.get("alert_cmd"),
                        }
            except Exception:
                continue
    return cfg


def _state_path(state_dir: str, name: str) -> str:
    # Sanitize the name to avoid path traversal
    safe = name.replace("/", "_").replace("..", "_")
    return os.path.join(state_dir, f"{safe}.json")


def _read_state(state_dir: str, name: str) -> dict | None:
    path = _state_path(state_dir, name)
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        print(f"WARNING: unable to read state for '{name}' at {path}", file=sys.stderr)
        return None


def _write_state(state_dir: str, name: str, data: dict) -> None:
    os.makedirs(state_dir, exist_ok=True)
    path = _state_path(state_dir, name)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception as e:
        print(f"ERROR: unable to write state for '{name}' at {path}: {e}", file=sys.stderr)
        raise


def cmd_touch(args: list[str]) -> int:
    """Record that a job ran successfully right now."""
    if not args:
        print("Usage: cron_watchdog.py touch <job_name> [--expect-within N]", file=sys.stderr)
        return EXIT_USAGE

    name = args[0]
    expect_within = DEFAULT_EXPECT_WITHIN
    state_dir = DEFAULT_STATE_DIR

    i = 1
    while i < len(args):
        if args[i] == "--expect-within" and i + 1 < len(args):
            expect_within = int(args[i + 1])
            i += 2
        elif args[i] == "--state-dir" and i + 1 < len(args):
            state_dir = args[i + 1]
            i += 2
        else:
            i += 1

    now = _now()
    data = {
        "name": name,
        "last_run": now,
        "expect_within": expect_within,
        "last_ok": True,
    }
    try:
        _write_state(state_dir, name, data)
    except Exception:
        return EXIT_IO_ERROR

    print(f"OK: recorded run for '{name}'")
    return EXIT_OK


def cmd_check(args: list[str]) -> int:
    """Check all tracked jobs and alert for stale ones."""
    config_path = None
    alert_cmd_override = None
    state_dir = DEFAULT_STATE_DIR
    quiet = False

    i = 0
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            config_path = args[i + 1]
            i += 2
        elif args[i] == "--state-dir" and i + 1 < len(args):
            state_dir = args[i + 1]
            i += 2
        elif args[i] == "--alert-cmd" and i + 1 < len(args):
            alert_cmd_override = args[i + 1]
            i += 2
        elif args[i] == "--quiet" or args[i] == "-q":
            quiet = True
            i += 1
        else:
            i += 1

    config = _load_config(config_path)

    # Discover tracked jobs: all .json files in state_dir + all config jobs
    tracked_names: set[str] = set()
    try:
        if os.path.isdir(state_dir):
            for fn in os.listdir(state_dir):
                if fn.endswith(".json"):
                    tracked_names.add(fn[:-5])
    except Exception:
        pass
    for name in config:
        tracked_names.add(name)

    if not tracked_names:
        print("No tracked jobs found. Use 'touch <job>' to start tracking, or add jobs to the config.")
        return EXIT_OK

    now = _now()
    stale_jobs: list[tuple[str, int, int]] = []  # (name, age, expect_within)
    alert_failures: list[str] = []

    for name in sorted(tracked_names):
        job_cfg = config.get(name, {})
        expect_within = job_cfg.get("expect_within")

        state = _read_state(state_dir, name)
        if state is None:
            # Job is configured but never ran
            if expect_within is None:
                expect_within = DEFAULT_EXPECT_WITHIN
            stale_jobs.append((name, 0, expect_within))
            if not quiet:
                print(f"MISSING: '{name}' has no recorded run (expected within {expect_within}s)")
            continue

        last_run = state.get("last_run", 0)
        file_expect = state.get("expect_within", DEFAULT_EXPECT_WITHIN)
        # Use the smaller of config expectation and file expectation
        eff_expect = min(file_expect, expect_within or file_expect) if expect_within else file_expect
        if expect_within is None:
            eff_expect = file_expect

        age = now - last_run
        if age > eff_expect:
            stale_jobs.append((name, age, eff_expect))
            if not quiet:
                print(f"STALE: '{name}' last ran {age}s ago (expected within {eff_expect}s)")
        else:
            if not quiet:
                print(f"OK: '{name}' ran {age}s ago (within {eff_expect}s window)")

    # Fire alerts
    exit_code = EXIT_OK
    if stale_jobs:
        exit_code = EXIT_STALE
        for name, age, eff_expect in stale_jobs:
            # Determine alert command: override > config > none
            alert_cmd = alert_cmd_override
            if not alert_cmd:
                alert_cmd = config.get(name, {}).get("alert_cmd")
            if alert_cmd:
                msg = f"cron-watchdog: job '{name}' is stale (last ran {age}s ago, expected within {eff_expect}s)"
                try:
                    # Substitute {job} placeholder
                    final_cmd = alert_cmd.replace("{job}", name)
                    subprocess.run(
                        final_cmd, shell=True, timeout=30,
                        capture_output=True, text=True,
                        env={**os.environ, "CRON_WATCHDOG_MSG": msg},
                    )
                except Exception as e:
                    alert_failures.append(f"{name}: {e}")
                    if not quiet:
                        print(f"WARNING: alert command failed for '{name}': {e}", file=sys.stderr)

    if not quiet and alert_failures:
        print(f"\nSome alert commands failed: {', '.join(alert_failures)}", file=sys.stderr)

    return exit_code


def cmd_list(args: list[str]) -> int:
    """List all tracked jobs and their status."""
    state_dir = DEFAULT_STATE_DIR
    config_path = None

    i = 0
    while i < len(args):
        if args[i] == "--state-dir" and i + 1 < len(args):
            state_dir = args[i + 1]
            i += 2
        elif args[i] == "--config" and i + 1 < len(args):
            config_path = args[i + 1]
            i += 2
        else:
            i += 1

    config = _load_config(config_path)
    tracked_names: set[str] = set()
    try:
        if os.path.isdir(state_dir):
            for fn in os.listdir(state_dir):
                if fn.endswith(".json"):
                    tracked_names.add(fn[:-5])
    except Exception:
        pass
    for name in config:
        tracked_names.add(name)

    if not tracked_names:
        print("No tracked jobs found.")
        return EXIT_OK

    now = _now()
    print(f"{'Job':<24} {'Status':<12} {'Age (s)':>10}  {'Window (s)':>12}")
    print("-" * 62)
    for name in sorted(tracked_names):
        job_cfg = config.get(name, {})
        expect_within = job_cfg.get("expect_within")
        state = _read_state(state_dir, name)
        if state is None:
            eff = expect_within or DEFAULT_EXPECT_WITHIN
            print(f"{name:<24} {'MISSING':<12} {'—':>10}  {eff:>12}")
        else:
            last_run = state.get("last_run", 0)
            file_expect = state.get("expect_within", DEFAULT_EXPECT_WITHIN)
            if expect_within is not None:
                eff = min(file_expect, expect_within)
            else:
                eff = file_expect
            age = now - last_run
            status = "OK" if age <= eff else "STALE"
            print(f"{name:<24} {status:<12} {age:>10}  {eff:>12}")

    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return EXIT_OK

    if argv[0] in ("-v", "--version"):
        print(f"cron-watchdog v{VERSION}")
        return EXIT_OK

    command = argv[0]
    rest = argv[1:]

    if command == "touch":
        return cmd_touch(rest)
    elif command == "check":
        return cmd_check(rest)
    elif command == "list":
        return cmd_list(rest)
    else:
        print(f"Unknown command: {command}\n", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
