#!/usr/bin/env python3
"""
homelab-backup-rotator
======================
A tiny, dependency-free backup rotation helper for self-hosted services.

Rotate and prune local backup directories using simple retention policies:
keep the N most recent backups of each "group", delete the rest.

A group is a directory whose name matches a prefix. For example, given a
backup folder containing:

    nextcloud-db-20260801.tar.gz
    nextcloud-db-20260802.tar.gz
    nextcloud-db-20260803.tar.gz
    ...
    pihole-20260801.tar.gz
    pihole-20260802.tar.gz

you can ask the rotator to keep the 7 most recent "nextcloud-db-*" files and
the 4 most recent "pihole-*" files, deleting everything older.

What it does:
  - Scans a directory for files matching one or more glob patterns.
  - Sorts each group by modification time (newest first).
  - Keeps the configured number of newest entries per group.
  - Deletes the rest (with an optional dry-run mode).
  - Prints a clear report so you can wire it into cron.

What it does NOT do:
  - Create backups. Pair it with your own dump/tar command.
  - Encrypt or upload offsite. Keep it simple.
  - Require root or any pip package.
"""

from __future__ import annotations

import argparse
import fnmatch
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

LOG = logging.getLogger("backup_rotator")

VERSION = "1.0.0"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RetentionPolicy:
    """Keep the `keep` newest files matching `pattern`.

    Frozen so instances are hashable and usable as dict keys during grouping.
    """

    pattern: str
    keep: int


@dataclass
class RotationResult:
    """Summary of a single rotation run."""

    scanned: int = 0
    kept: int = 0
    deleted: int = 0
    freed_bytes: int = 0
    deleted_paths: List[str] = field(default_factory=list)
    kept_paths: List[str] = field(default_factory=list)

    def merge(self, other: "RotationResult") -> None:
        self.scanned += other.scanned
        self.kept += other.kept
        self.deleted += other.deleted
        self.freed_bytes += other.freed_bytes
        self.deleted_paths.extend(other.deleted_paths)
        self.kept_paths.extend(other.kept_paths)


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def human_size(num_bytes: int) -> str:
    """Turn a byte count into a short, human friendly string."""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} EB"


def parse_policies(raw: List[str]) -> List[RetentionPolicy]:
    """Parse policy strings of the form 'pattern=keep' into objects.

    Examples:
        'nextcloud-*.tar.gz=7'
        'pihole-*=4'
    """
    policies: List[RetentionPolicy] = []
    for item in raw:
        if "=" not in item:
            raise ValueError(
                f"Bad policy '{item}'. Expected 'pattern=keep', "
                f"for example 'nextcloud-*.tar.gz=7'."
            )
        pattern, keep_str = item.rsplit("=", 1)
        pattern = pattern.strip()
        try:
            keep = int(keep_str.strip())
        except ValueError as exc:
            raise ValueError(
                f"Bad keep value '{keep_str}' in '{item}'. Must be an integer."
            ) from exc
        if keep < 0:
            raise ValueError(f"Keep value must be >= 0, got {keep} in '{item}'.")
        if not pattern:
            raise ValueError(f"Empty pattern in policy '{item}'.")
        policies.append(RetentionPolicy(pattern=pattern, keep=keep))
    return policies


def group_by_pattern(
    directory: Path, policies: List[RetentionPolicy]
) -> Dict[RetentionPolicy, List[Path]]:
    """Return every file in `directory` matching each policy's pattern."""
    grouped: Dict[RetentionPolicy, List[Path]] = {p: [] for p in policies}
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Backup directory '{directory}' does not exist."
        ) from exc

    for entry in entries:
        if not entry.is_file():
            continue
        name = entry.name
        for policy in policies:
            # fnmatch is case-insensitive on some platforms; be explicit.
            if fnmatch.fnmatchcase(name, policy.pattern):
                grouped[policy].append(entry)
                break  # a file belongs to the first matching policy
    return grouped


def sort_newest_first(files: List[Path]) -> List[Path]:
    """Sort by modification time, newest first. Ties broken by name."""
    return sorted(files, key=lambda p: (-p.stat().st_mtime, p.name))


def rotate_group(
    policy: RetentionPolicy,
    files: List[Path],
    dry_run: bool,
) -> RotationResult:
    """Apply one retention policy to one group of files."""
    result = RotationResult()
    ordered = sort_newest_first(files)
    result.scanned = len(ordered)

    keep_list = ordered[: policy.keep]
    delete_list = ordered[policy.keep :]

    result.kept = len(keep_list)
    result.kept_paths = [p.name for p in keep_list]

    for path in delete_list:
        size = path.stat().st_size
        result.deleted += 1
        result.freed_bytes += size
        result.deleted_paths.append(path.name)
        if dry_run:
            LOG.info("[dry-run] would delete %s (%s)", path.name, human_size(size))
        else:
            try:
                path.unlink()
                LOG.info("deleted %s (%s)", path.name, human_size(size))
            except OSError as exc:
                LOG.error("failed to delete %s: %s", path, exc)
                # roll back the accounting for this failed delete
                result.deleted -= 1
                result.freed_bytes -= size
                result.deleted_paths.pop()
    return result


def run_rotation(
    directory: Path,
    policies: List[RetentionPolicy],
    dry_run: bool,
) -> RotationResult:
    """Run the full rotation across all policies."""
    grouped = group_by_pattern(directory, policies)
    total = RotationResult()
    for policy, files in grouped.items():
        LOG.info(
            "--- policy '%s' keep=%d -> %d file(s) found",
            policy.pattern,
            policy.keep,
            len(files),
        )
        partial = rotate_group(policy, files, dry_run)
        total.merge(partial)
    return total


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_report(directory: Path, result: RotationResult, dry_run: bool) -> None:
    """Print a human friendly summary of the rotation run."""
    mode = "DRY RUN" if dry_run else "LIVE"
    print()
    print(f"  homelab-backup-rotator [{mode}] - {VERSION}")
    print("  " + "-" * 52)
    print(f"  directory : {directory}")
    print(f"  scanned   : {result.scanned} file(s)")
    print(f"  kept      : {result.kept} file(s)")
    print(f"  deleted   : {result.deleted} file(s)")
    print(f"  freed     : {human_size(result.freed_bytes)}")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"  finished  : {ts}")
    if result.deleted_paths:
        print("  removed:")
        for name in result.deleted_paths:
            print(f"    - {name}")
    print()


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backup_rotator",
        description=(
            "Rotate and prune backup files using retention policies. "
            "Policies have the form 'pattern=keep', for example "
            "'nextcloud-*.tar.gz=7'."
        ),
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing the backup files to rotate.",
    )
    parser.add_argument(
        "-p",
        "--policy",
        action="append",
        dest="policies",
        required=True,
        metavar="PATTERN=KEEP",
        help=(
            "Retention policy, repeatable. Example: --policy "
            "'nextcloud-*.tar.gz=7'"
        ),
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting anything.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print one line per file action.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    directory: Path = args.directory
    if not directory.is_dir():
        print(f"error: '{directory}' is not a directory.", file=sys.stderr)
        return 2

    try:
        policies = parse_policies(args.policies)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        result = run_rotation(directory, policies, args.dry_run)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print_report(directory, result, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
