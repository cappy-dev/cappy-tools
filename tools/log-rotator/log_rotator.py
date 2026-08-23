#!/usr/bin/env python3
"""
log_rotator.py - Zero-dependency log rotation for self-hosted services.

Rotates, compresses, and prunes plain-text log files. Designed to run from
cron or a systemd timer. No external libraries, just the Python standard
library (Python >= 3.8).

Rotation strategy per log file:
  1. If the file exceeds the max size threshold, rename it to <name>.1 and
     optionally gzip it. A fresh empty file is created in its place so the
     writing process keeps its file handle.
  2. Existing numbered rotations are shifted: <name>.1 -> <name>.2, etc.
  3. Rotations older than --keep-days or beyond --keep-count are deleted.

Exit codes:
  0  everything rotated cleanly, or nothing to do
  1  one or more files could not be processed (warnings)
  2  configuration / usage error
  3  unexpected crash (a bug, please report it)
"""

import argparse
import gzip
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone

__version__ = "1.0.0"

# Exit codes for cron friendliness.
EXIT_OK = 0
EXIT_WARN = 1
EXIT_USAGE = 2
EXIT_CRASH = 3


def parse_size(text):
    """Parse a human size string like '10M', '500k', '2G' into bytes."""
    if text is None:
        return None
    text = text.strip().strip(",").lower()
    if not text:
        raise ValueError("empty size")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]?i?b?|b)?", text)
    if not match:
        raise ValueError("could not parse size: %r" % text)
    number = float(match.group(1))
    unit = (match.group(2) or "b").rstrip("b").rstrip("i")
    multipliers = {
        "": 1,
        "k": 1024,
        "m": 1024 ** 2,
        "g": 1024 ** 3,
        "t": 1024 ** 4,
    }
    if unit not in multipliers:
        raise ValueError("unknown size unit: %r" % unit)
    return int(number * multipliers[unit])


def human_size(num_bytes):
    """Format a byte count in a human-friendly way."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0 or unit == "TB":
            if unit == "B":
                return "%d %s" % (num_bytes, unit)
            return "%.1f %s" % (num_bytes, unit)
        num_bytes /= 1024.0
    return "%.1f TB" % num_bytes


def rotate_suffixes(base_path):
    """Return existing rotation files for a base, like app.log.1, app.log.2.gz.

    Returns a list of (suffix_number, is_gzipped, full_path) sorted high to low
    so the caller can shift them safely.
    """
    directory = os.path.dirname(base_path) or "."
    base_name = os.path.basename(base_path)
    pattern = re.compile(r"^" + re.escape(base_name) + r"\.(\d+)(\.gz)?$")
    results = []
    try:
        for entry in os.listdir(directory):
            m = pattern.match(entry)
            if m:
                number = int(m.group(1))
                gzipped = bool(m.group(2))
                results.append((number, gzipped, os.path.join(directory, entry)))
    except FileNotFoundError:
        return []
    results.sort(key=lambda item: item[0], reverse=True)
    return results


def rotate_one(log_path, max_size, keep_count, keep_days, compress, verbose):
    """Rotate a single log file. Returns a string describing what happened."""
    messages = []
    try:
        current_size = os.path.getsize(log_path)
    except FileNotFoundError:
        return None  # Not present, nothing to do.
    except OSError as exc:
        return "error: cannot stat %s: %s" % (log_path, exc)

    if max_size is not None and current_size < max_size:
        return None  # Under threshold, leave it alone.

    # Shift every existing rotation up by one number.
    numbered = rotate_suffixes(log_path)
    for number, gzipped, path in numbered:
        new_number = number + 1
        new_path = "%s.%d%s" % (log_path, new_number, ".gz" if gzipped else "")
        if keep_count is not None and new_number > keep_count:
            # This rotation would exceed the count limit: delete instead.
            try:
                os.remove(path)
                messages.append("pruned (count): %s" % path)
            except OSError as exc:
                messages.append("warn: could not prune %s: %s" % (path, exc))
            continue
        if keep_days is not None:
            try:
                mtime = os.path.getmtime(path)
                age_days = (time.time() - mtime) / 86400.0
                if age_days > keep_days and (keep_count is None or new_number > keep_count):
                    os.remove(path)
                    messages.append("pruned (age %.1fd): %s" % (age_days, path))
                    continue
            except OSError as exc:
                messages.append("warn: could not stat %s for age: %s" % (path, exc))
        try:
            os.rename(path, new_path)
            if verbose:
                messages.append("shifted: %s -> %s" % (path, new_path))
        except OSError as exc:
            messages.append("warn: could not shift %s -> %s: %s" % (path, new_path, exc))

    # Move the current log to .1, then handle compression.
    rotated_path = log_path + ".1"
    try:
        os.rename(log_path, rotated_path)
        messages.append("rotated: %s (%s)" % (log_path, human_size(current_size)))
    except OSError as exc:
        messages.append("error: could not rotate %s: %s" % (log_path, exc))
        return "; ".join(messages)

    if compress:
        gz_path = rotated_path + ".gz"
        try:
            with open(rotated_path, "rb") as src, gzip.open(gz_path, "wb") as dst:
                shutil.copyfileobj(src, dst, length=64 * 1024)
            os.remove(rotated_path)
            if verbose:
                messages.append("compressed: %s -> %s" % (rotated_path, gz_path))
        except OSError as exc:
            messages.append("warn: compression failed for %s: %s" % (rotated_path, exc))

    # Recreate a fresh empty file so the writing service keeps its handle.
    try:
        with open(log_path, "wb") as handle:
            handle.write(b"")
    except OSError as exc:
        messages.append("warn: could not recreate %s: %s" % (log_path, exc))

    return "; ".join(messages)


def prune_old(log_path, keep_days, keep_count, verbose):
    """Delete existing rotations that exceed age or count limits without rotating."""
    messages = []
    numbered = rotate_suffixes(log_path)
    if not numbered:
        return messages
    if keep_count is not None:
        for index, (number, gzipped, path) in enumerate(numbered):
            if number > keep_count:
                try:
                    os.remove(path)
                    messages.append("pruned (count): %s" % path)
                except OSError as exc:
                    messages.append("warn: could not prune %s: %s" % (path, exc))
    if keep_days is not None:
        for number, gzipped, path in numbered:
            try:
                mtime = os.path.getmtime(path)
                age_days = (time.time() - mtime) / 86400.0
                if age_days > keep_days:
                    os.remove(path)
                    messages.append("pruned (age %.1fd): %s" % (age_days, path))
            except OSError as exc:
                messages.append("warn: could not stat/prune %s: %s" % (path, exc))
    return messages


def expand_targets(raw_targets):
    """Expand any glob-like targets. For now just resolve env vars and return as-is."""
    targets = []
    for raw in raw_targets:
        expanded = os.path.expanduser(os.path.expandvars(raw))
        targets.append(expanded)
    return targets


def write_report(report_path, lines):
    """Write a timestamped text report file if requested."""
    if not report_path:
        return
    try:
        with open(report_path, "a", encoding="utf-8") as handle:
            handle.write("# log_rotator report %s\n" % datetime.now(timezone.utc).isoformat())
            for line in lines:
                handle.write(line + "\n")
            handle.write("\n")
    except OSError as exc:
        sys.stderr.write("warn: could not write report to %s: %s\n" % (report_path, exc))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="log_rotator",
        description="Zero-dependency log rotation, compression, and pruning for cron.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="Log files to rotate (supports ~ and $VAR expansion, one or more).",
    )
    parser.add_argument(
        "-s",
        "--max-size",
        default="10M",
        help="Rotate when a log exceeds this size. Examples: 100k, 5M, 1G. Default: 10M.",
    )
    parser.add_argument(
        "-n",
        "--keep-count",
        type=int,
        default=None,
        help="Keep at most N rotated copies per log file. Default: keep all.",
    )
    parser.add_argument(
        "-d",
        "--keep-days",
        type=int,
        default=None,
        help="Delete rotated copies older than this many days. Default: keep all.",
    )
    parser.add_argument(
        "-z",
        "--compress",
        action="store_true",
        dest="compress",
        default=None,
        help="Gzip-compress rotated files (sets .gz suffix). Off by default.",
    )
    parser.add_argument(
        "--no-compress",
        action="store_false",
        dest="compress",
        help="Disable compression (the default).",
    )
    parser.add_argument(
        "--always",
        action="store_true",
        help="Always rotate regardless of file size (forcing a turn every run).",
    )
    parser.add_argument(
        "--prune-only",
        action="store_true",
        help="Do not rotate. Only delete rotations that exceed age/count limits.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Print details. Repeat (-vv) for more.",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Append a timestamped text report of all actions to this file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without modifying or deleting anything.",
    )
    parser.add_argument("--version", action="version", version="log_rotator %s" % __version__)

    args = parser.parse_args(argv)

    try:
        max_size = None if args.always else parse_size(args.max_size)
    except ValueError as exc:
        parser.error("--max-size: %s" % exc)
    if max_size is not None and max_size < 0:
        parser.error("--max-size must be positive (got %d bytes)." % max_size)
    if args.keep_count is not None and args.keep_count < 0:
        parser.error("--keep-count must be zero or positive.")
    if args.keep_days is not None and args.keep_days < 0:
        parser.error("--keep-days must be zero or positive.")

    targets = expand_targets(args.files)
    report_lines = []
    had_warning = False
    had_rotation = False

    for target in targets:
        # Note: --dry-run adjusts behavior inline by emitting messages instead
        # of taking action. We keep it simple rather than layering a mock FS.
        if args.dry_run:
            try:
                size = os.path.getsize(target)
            except OSError as exc:
                sys.stderr.write("dry-run %s: %s\n" % (target, exc))
                had_warning = True
                continue
            will_rotate = args.always or (max_size is None) or (size >= max_size)
            action = "rotate+compress" if args.compress else "rotate"
            if args.prune_only:
                action = "prune"
            print("DRY-RUN %s [%s] would %s (size %s)" % (
                target, action, "act" if will_rotate else "skip", human_size(size),
            ))
            continue

        if args.prune_only:
            notes = prune_old(
                target, args.keep_days, args.keep_count, args.verbose,
            )
            for note in notes:
                had_warning = had_warning or note.startswith("warn") or note.startswith("error")
                print(note)
                report_lines.append(note)
            continue

        result = rotate_one(
            target,
            max_size,
            args.keep_count,
            args.keep_days,
            args.compress,
            args.verbose > 0,
        )
        if result:
            print("%s:\n\t%s" % (target, result))
            report_lines.append("%s: %s" % (target, result))
            had_rotation = True
            if result.startswith("error"):
                had_warning = True
        elif args.verbose:
            print("%s: under threshold, skipped" % target)

    if args.report:
        write_report(args.report, report_lines)

    if had_warning:
        return EXIT_WARN
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        sys.exit(EXIT_WARN)
    except SystemExit:
        raise
    except Exception as exc:  # Surface a clean crash message for the user.
        sys.stderr.write("fatal: %s\n" % exc)
        sys.exit(EXIT_CRASH)
