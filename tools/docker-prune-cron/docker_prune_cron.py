#!/usr/bin/env python3
"""
docker-prune-cron
================
Safely prune Docker images, containers, volumes, networks, and build cache
without nuking running services. Designed to run from cron.

Features
--------
- Dry-run mode (default): reports what WOULD be removed, deletes nothing.
- Per-layer age filters: keep images newer than N hours, prune older ones.
- Protects running containers and volumes in use.
- HTML report written next to the log for quick browser checks.
- Cron-friendly exit codes:
    0  success (or dry run with no warnings)
    1  pruning attempted but some layer failed
    2  Docker daemon unreachable or CLI missing
    3  bad arguments
- Zero dependencies beyond Python >=3.8 and the docker CLI.

Usage
-----
  python3 docker_prune_cron.py                  # dry run, report only
  python3 docker_prune_cron.py --apply           # actually prune
  python3 docker_prune_cron.py --apply --force   # skip the safety pause
  python3 docker_prune_cron.py --hours 168       # keep images < 7 days old
  python3 docker_prune_cron.py --report /var/www/docker-prune.html
  python3 docker_prune_cron.py --quiet           # only log on warnings

Cron example (prune every Sunday at 3am, keep recent images):
  0 3 * * 0  /usr/bin/python3 /usr/local/bin/docker_prune_cron.py --apply --force --hours 168 --quiet >> /var/log/docker-prune.log 2>&1
"""

from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import List, Tuple

__version__ = "1.0.0"

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_NO_DOCKER = 2
EXIT_BAD_ARGS = 3

DEFAULT_KEPT_HOURS = 72
DEFAULT_REPORT_PATH = "docker-prune-report.html"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str, quiet: bool = False) -> None:
    """Print a timestamped log line unless quiet mode is on."""
    if quiet:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


def warn(msg: str) -> None:
    """Warnings are always shown, even in quiet mode."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] WARNING: {msg}", file=sys.stderr, flush=True)


def find_docker() -> str | None:
    """Locate the docker CLI on PATH."""
    return shutil.which("docker")


def docker_ok(docker_bin: str) -> bool:
    """Return True if the Docker daemon responds."""
    try:
        proc = subprocess.run(
            [docker_bin, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def docker_version(docker_bin: str) -> str:
    """Return the Docker server version string, or 'unknown'."""
    try:
        proc = subprocess.run(
            [docker_bin, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def run_prune(
    docker_bin: str,
    layer: str,
    apply: bool,
    extra_args: List[str] | None = None,
) -> Tuple[bool, str]:
    """
    Run `docker <layer> prune ...`. Returns (success, captured_output).
    In dry-run mode the command always succeeds and the output is the plan.
    """
    cmd: List[str] = [docker_bin, layer, "prune", "--force"]
    if extra_args:
        cmd.extend(extra_args)
    if not apply:
        cmd.append("--dry-run")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        out = proc.stdout.strip() or proc.stderr.strip()
        if not apply:
            return True, out
        return proc.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, f"prune {layer} timed out after 300s"
    except (FileNotFoundError, OSError) as exc:
        return False, f"prune {layer} failed to execute: {exc}"


def image_count(docker_bin: str) -> int:
    """Count locally cached images."""
    try:
        proc = subprocess.run(
            [docker_bin, "images", "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return 0
        return len([ln for ln in proc.stdout.splitlines() if ln.strip()])
    except Exception:
        return 0


def disk_usage(docker_bin: str) -> str:
    """Return a human readable Docker disk usage summary line."""
    try:
        proc = subprocess.run(
            [docker_bin, "system", "df", "--format",
             "Images: {{.Images}} ({{.ImagesSize}}), "
             "Containers: {{.Containers}}, "
             "Local Volumes: {{.LocalVolumes}} ({{.LocalVolumesSize}}), "
             "Build Cache: {{.BuildCache}} ({{.BuildCacheSize}})"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "(unavailable)"
    except Exception:
        return "(unavailable)"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(
    path: str,
    started_at: str,
    finished_at: str,
    docker_ver: str,
    disk_before: str,
    disk_after: str,
    results: List[Tuple[str, bool, str]],
    apply: bool,
) -> None:
    """Write a self contained HTML status report."""
    rows: List[str] = []
    all_ok = True
    for layer, ok, out in results:
        status = "OK" if ok else "FAIL"
        klass = "ok" if ok else "fail"
        if not ok:
            all_ok = False
        rows.append(
            "<tr>"
            f'<td class="layer">{html.escape(layer)}</td>'
            f'<td class="{klass}">{status}</td>'
            f'<td><pre>{html.escape(out)[:4000]}</pre></td>'
            "</tr>"
        )
    banner = "APPLIED" if apply else "DRY RUN"
    overall = "all good" if all_ok else "some layers failed"
    title = f"docker-prune-cron report ({banner})"

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{
    --bg: #0f1115; --fg: #e6e6e6; --muted: #8a93a3;
    --card: #161a22; --ok: #3fb950; --fail: #f85149; --accent: #58a6ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2rem;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    background: var(--bg); color: var(--fg);
  }}
  h1 {{ font-size: 1.4rem; }}
  h1 .tag {{
    font-size: 0.8rem; padding: 0.15rem 0.5rem; border-radius: 6px;
    background: var(--accent); color: #0b0d12; margin-left: 0.6rem;
    vertical-align: middle;
  }}
  .meta {{ color: var(--muted); margin: 0.5rem 0 1.5rem; font-size: 0.85rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.7rem; border-bottom: 1px solid #232834; }}
  th {{ color: var(--muted); font-weight: 600; font-size: 0.8rem; }}
  .layer {{ color: var(--accent); }}
  .ok {{ color: var(--ok); font-weight: 700; }}
  .fail {{ color: var(--fail); font-weight: 700; }}
  pre {{ margin: 0; white-space: pre-wrap; word-break: break-word; max-height: 240px; overflow: auto; font-size: 0.75rem; }}
  .summary {{ margin: 1.5rem 0; padding: 1rem; background: var(--card); border-radius: 8px; font-size: 0.85rem; }}
  .summary strong {{ color: var(--fg); }}
</style>
</head>
<body>
  <h1>docker-prune-cron <span class="tag">{banner}</span></h1>
  <div class="meta">
    docker server: <strong>{html.escape(docker_ver)}</strong> &middot;
    started {html.escape(started_at)} &middot;
    finished {html.escape(finished_at)} &middot;
    overall: <strong>{overall}</strong>
  </div>
  <div class="summary">
    <strong>Before:</strong> {html.escape(disk_before)}<br>
    <strong>After:</strong> {html.escape(disk_after)}
  </div>
  <table>
    <thead><tr><th>Layer</th><th>Status</th><th>Output</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc)
    except OSError as exc:
        warn(f"could not write report to {path}: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="docker-prune-cron",
        description="Safely prune Docker layers with cron-friendly exit codes.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="actually delete (default is dry run, report only)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="skip the interactive safety pause before applying",
    )
    p.add_argument(
        "--hours",
        type=float,
        default=DEFAULT_KEPT_HOURS,
        help=(
            f"keep images/containers newer than this many hours "
            f"(default: {DEFAULT_KEPT_HOURS})"
        ),
    )
    p.add_argument(
        "--no-volumes",
        action="store_true",
        help="skip pruning volumes (safest mode for production)",
    )
    p.add_argument(
        "--no-build-cache",
        action="store_true",
        help="skip pruning build cache",
    )
    p.add_argument(
        "--report",
        default=DEFAULT_REPORT_PATH,
        help=f"path for HTML report (default: {DEFAULT_REPORT_PATH})",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="suppress normal log lines; warnings still print",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"docker-prune-cron {__version__}",
    )
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_BAD_ARGS

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    docker_bin = find_docker()
    if not docker_bin:
        warn("docker CLI not found on PATH")
        return EXIT_NO_DOCKER

    if not docker_ok(docker_bin):
        warn("Docker daemon is not reachable. Is dockerd running?")
        return EXIT_NO_DOCKER

    docker_ver = docker_version(docker_bin)
    log(f"docker server version: {docker_ver}", args.quiet)
    log(f"mode: {'APPLY' if args.apply else 'DRY RUN'}", args.quiet)
    log(f"keeping images newer than {args.hours} hours", args.quiet)

    if args.apply and not args.force:
        log("Applying in 10s. Press Ctrl+C to cancel.", args.quiet)
        log("Use --force to skip this pause in cron.", args.quiet)
        try:
            time.sleep(10)
        except KeyboardInterrupt:
            warn("cancelled by user before pruning")
            return EXIT_OK

    disk_before = disk_usage(docker_bin)
    log(f"disk before: {disk_before}", args.quiet)

    # Build the per-layer plan.
    filter_arg = [f"filter=until={args.hours}h"] if args.hours and args.hours > 0 else []
    tasks: List[Tuple[str, bool, str, List[str] | None]] = [
        ("container", False, "", filter_arg),
        ("image", False, "", filter_arg),
        ("network", False, "", None),
    ]
    if not args.no_volumes:
        tasks.append(("volume", False, "", None))
    if not args.no_build_cache:
        tasks.append(("builder", True, "", None))  # builder uses a different subcommand

    results: List[Tuple[str, bool, str]] = []
    for layer, is_builder, _, extra in tasks:
        log(f"pruning {layer} ...", args.quiet)
        if is_builder:
            # `docker builder prune` instead of `docker <layer> prune`
            cmd = [docker_bin, "builder", "prune", "--force"]
            if extra:
                cmd.append(f"--{extra[0]}")
            if not args.apply:
                cmd.append("--dry-run")
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600, check=False
                )
                out = proc.stdout.strip() or proc.stderr.strip()
                ok = proc.returncode == 0 if args.apply else True
            except (subprocess.TimeoutExpired, OSError) as exc:
                ok, out = False, str(exc)
        else:
            ok, out = run_prune(docker_bin, layer, args.apply, extra)
        results.append((layer, ok, out))
        if ok:
            log(f"  {layer}: ok", args.quiet)
        else:
            warn(f"{layer} prune failed: {out[:200]}")

    disk_after = disk_usage(docker_bin)
    log(f"disk after: {disk_after}", args.quiet)

    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_report(
        args.report,
        started_at,
        finished_at,
        docker_ver,
        disk_before,
        disk_after,
        results,
        args.apply,
    )
    log(f"report written to {args.report}", args.quiet)

    images_now = image_count(docker_bin)
    log(f"images remaining: {images_now}", args.quiet)

    if any(not ok for _, ok, _ in results):
        warn("one or more prune layers failed")
        return EXIT_PARTIAL

    log("all prune layers completed cleanly", args.quiet)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
