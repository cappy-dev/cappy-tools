#!/usr/bin/env python3
"""
dup_scout.py - Find duplicate files by content hash.

Zero dependencies. Pure Python 3.8+. Uses SHA-256 to detect identical
content, with a size pre-filter so only files sharing a size with
another file get hashed. Designed for homelab media libraries,
backup trees, and cluttered downloads folders.

Usage:
    python3 dup_scout.py /path/to/scan
    python3 dup_scout.py /path1 /path2 --min-size 1M
    python3 dup_scout.py /path --output report.json --format json
    python3 dup_scout.py /path --delete --dry-run
    python3 dup_scout.py /path --exclude '.git,node_modules'

Exit codes (cron-friendly):
    0  Found no duplicates
    1  Found at least one duplicate group
    2  Usage error or scan failure
"""

import argparse
import csv
import hashlib
import json
import os
import stat as statmod
import sys
from collections import defaultdict


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def parse_size(text):
    """Parse a human size string like 1M, 500k, 2G into bytes (int)."""
    if text is None:
        return 0
    text = str(text).strip()
    if not text:
        return 0
    units = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    suffix = text[-1].lower()
    if suffix in units:
        number = float(text[:-1])
        if number < 0:
            raise ValueError("size cannot be negative")
        return int(number * units[suffix])
    return int(float(text))


def human_size(num_bytes):
    """Format a byte count as a short human-readable string."""
    number = float(num_bytes)
    for unit in ("B", "k", "M", "G", "T", "P"):
        if abs(number) < 1024.0:
            if unit == "B":
                return f"{int(number)}{unit}"
            return f"{number:.1f}{unit}"
        number /= 1024.0
    return f"{number:.1f}E"


# --------------------------------------------------------------------------- #
# Walking / sizing
# --------------------------------------------------------------------------- #

def should_skip(name, exclude_dirs, exclude_globs):
    """Return True if a directory entry should be skipped."""
    if name in exclude_dirs:
        return True
    base = name.lower()
    for pattern in exclude_globs:
        if pattern in base:
            return True
    return False


def walk_sizes(roots, exclude_dirs, follow_links):
    """Walk roots and yield (path, size) for every regular file.

    Files that cannot be stat'd or read are silently skipped.
    """
    seen_inodes = set()
    for root in roots:
        if not os.path.exists(root):
            print(f"warning: {root} does not exist, skipping", file=sys.stderr)
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_links):
            # prune excluded dirs in-place
            kept = []
            for d in dirnames:
                if should_skip(d, exclude_dirs, ()):
                    continue
                kept.append(d)
            dirnames[:] = kept
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                try:
                    st = os.stat(full, follow_symlinks=follow_links)
                except OSError:
                    continue
                # only regular files
                if not statmod.S_ISREG(st.st_mode):
                    continue
                # dedupe hardlinks and same file via inode
                key = (st.st_dev, st.st_ino)
                if st.st_ino and key in seen_inodes:
                    continue
                if st.st_ino:
                    seen_inodes.add(key)
                yield full, st.st_size


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #

def hash_file(path, size):
    """Return SHA-256 hex digest of a file, or None on error."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)  # 1 MiB
                if not chunk:
                    break
                h.update(chunk)
    except OSError as exc:
        print(f"warning: could not read {path}: {exc}", file=sys.stderr)
        return None
    return h.hexdigest()


def partial_hash(path, size, limit):
    """Hash only the first `limit` bytes (or whole file if smaller).

    Returns (digest, is_partial) or (None, False) on error.
    """
    if size <= limit:
        return hash_file(path, size), False
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            h.update(fh.read(limit))
    except OSError as exc:
        print(f"warning: could not read {path}: {exc}", file=sys.stderr)
        return None, False
    return h.hexdigest(), True


# --------------------------------------------------------------------------- #
# Core scan
# --------------------------------------------------------------------------- #

def find_duplicates(roots, min_size, exclude_dirs, follow_links, quiet):
    """Scan roots and return a list of duplicate groups.

    Each group is a dict: {"digest": ..., "size": ..., "paths": [...]}.
    Only groups with 2+ paths are returned.
    """
    # Phase 1: collect sizes
    if not quiet:
        print("Phase 1: indexing files...")
    size_map = defaultdict(list)
    total_files = 0
    for path, size in walk_sizes(roots, exclude_dirs, follow_links):
        if size < min_size:
            continue
        size_map[size].append(path)
        total_files += 1

    # keep only sizes with more than one file (possible duplicates)
    candidates = {s: paths for s, paths in size_map.items() if len(paths) > 1}

    if not quiet:
        print(f"  {total_files} files scanned, "
              f"{sum(len(p) for p in candidates.values())} share a size, "
              f"{len(candidates)} candidate sizes.")

    if not candidates:
        return []

    # Phase 2: partial hash for quick elimination
    if not quiet:
        print("Phase 2: quick partial hash of candidates...")
    partial_limit = 1 << 16  # 64 KiB
    partial_groups = defaultdict(lambda: defaultdict(list))
    for size, paths in candidates.items():
        for p in paths:
            digest, is_partial = partial_hash(p, size, partial_limit)
            if digest is None:
                continue
            # prefix with 'p' for partial, 'f' for full-size files under limit
            tag = "p" if is_partial else "f"
            partial_groups[size][tag + digest].append(p)

    # keep buckets with 2+ files
    still_candidates = defaultdict(list)
    for size, buckets in partial_groups.items():
        for digest, paths in buckets.items():
            if len(paths) > 1:
                still_candidates[size].append(paths)

    flat = []
    for size, bucket_list in still_candidates.items():
        for paths in bucket_list:
            flat.extend((size, p) for p in paths)

    if not quiet:
        print(f"  {len(flat)} files remain after partial hash.")

    if not flat:
        return []

    # Phase 3: full hash the survivors
    if not quiet:
        print("Phase 3: full SHA-256 of survivors...")
    full_groups = defaultdict(list)
    for size, path in flat:
        digest = hash_file(path, size)
        if digest is None:
            continue
        full_groups[(size, digest)].append(path)

    groups = []
    for (size, digest), paths in full_groups.items():
        if len(paths) < 2:
            continue
        groups.append({
            "digest": digest,
            "size": size,
            "paths": sorted(paths),
        })

    groups.sort(key=lambda g: g["size"], reverse=True)
    return groups


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def render_text(groups, total_wasted):
    lines = []
    lines.append(f"Duplicate report: {len(groups)} group(s)")
    lines.append(f"Potential space savings (1 file per group): {human_size(total_wasted)}")
    lines.append("")
    for i, group in enumerate(groups, 1):
        lines.append(f"Group {i}: {human_size(group['size'])} each, "
                     f"{len(group['paths'])} copies, digest {group['digest'][:12]}...")
        for p in group["paths"]:
            lines.append(f"  {p}")
        lines.append("")
    return "\n".join(lines)


def render_json(groups, total_wasted):
    payload = {
        "groups": groups,
        "total_groups": len(groups),
        "potential_savings_bytes": total_wasted,
        "potential_savings_human": human_size(total_wasted),
    }
    return json.dumps(payload, indent=2)


def render_csv(groups):
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["group", "digest", "size_bytes", "path"])
    for i, group in enumerate(groups, 1):
        for p in group["paths"]:
            writer.writerow([i, group["digest"], group["size"], p])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Delete helper
# --------------------------------------------------------------------------- #

def apply_deletion(groups, dry_run, keep_first, quiet):
    """Delete duplicates, keeping one file per group.

    If keep_first is True, the first (sorted) path survives and the rest
    are deleted. If dry_run is True, only print what would happen.
    Returns count of files deleted (or that would be).
    """
    deleted = 0
    for group in groups:
        paths = group["paths"]
        survivor = paths[0] if keep_first else paths[-1]
        victims = [p for p in paths if p != survivor]
        for v in victims:
            if dry_run:
                print(f"[dry-run] would delete: {v}")
            else:
                try:
                    os.remove(v)
                    if not quiet:
                        print(f"deleted: {v}")
                except OSError as exc:
                    print(f"error deleting {v}: {exc}", file=sys.stderr)
                    continue
            deleted += 1
    return deleted


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(
        prog="dup_scout",
        description="Find duplicate files by content hash. Zero dependencies.",
    )
    parser.add_argument("paths", nargs="+", metavar="PATH",
                        help="one or more directories/files to scan")
    parser.add_argument("--min-size", default="0",
                        help="ignore files smaller than this (e.g. 1M, 500k, 2G)")
    parser.add_argument("--exclude", default="",
                        help="comma-separated dir names to skip "
                             "(e.g. .git,node_modules,.venv)")
    parser.add_argument("--follow-links", action="store_true",
                        help="follow symbolic links when walking")
    parser.add_argument("--format", choices=["text", "json", "csv"],
                        default="text", help="output format (default: text)")
    parser.add_argument("--output", metavar="FILE",
                        help="write output to a file instead of stdout")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress progress messages on stderr")
    parser.add_argument("--delete", action="store_true",
                        help="delete duplicate copies (use with --dry-run first)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be deleted without deleting")
    parser.add_argument("--keep", choices=["first", "last"], default="first",
                        help="which copy to keep when --delete is used "
                             "(default: first, the lexically smallest path)")
    parser.add_argument("--version", action="version",
                        version="dup-scout 1.0.0")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        min_size = parse_size(args.min_size)
    except ValueError as exc:
        print(f"error: bad --min-size value: {exc}", file=sys.stderr)
        return 2

    if args.exclude:
        exclude_dirs = set(d.strip() for d in args.exclude.split(",") if d.strip())
    else:
        exclude_dirs = set()

    if args.delete and args.dry_run is False and not sys.stdin.closed:
        # nudge the user toward safety if they forgot --dry-run
        print("note: running deletion without --dry-run. "
              "Re-run with --dry-run first to preview.", file=sys.stderr)

    roots = args.paths

    try:
        groups = find_duplicates(
            roots, min_size, exclude_dirs,
            follow_links=args.follow_links, quiet=args.quiet,
        )
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        print(f"fatal: {exc}", file=sys.stderr)
        return 2

    # compute wasted space: for each group, (copies - 1) * size
    total_wasted = sum((len(g["paths"]) - 1) * g["size"] for g in groups)

    # render chosen format
    if args.format == "json":
        out = render_json(groups, total_wasted)
    elif args.format == "csv":
        out = render_csv(groups)
    else:
        out = render_text(groups, total_wasted)

    if args.output:
        try:
            with open(args.output, "w") as fh:
                fh.write(out)
                if not out.endswith("\n"):
                    fh.write("\n")
        except OSError as exc:
            print(f"error writing output: {exc}", file=sys.stderr)
            return 2
        if not args.quiet:
            print(f"wrote report to {args.output}", file=sys.stderr)
    else:
        print(out)

    # optional deletion
    if args.delete:
        deleted = apply_deletion(
            groups, dry_run=args.dry_run,
            keep_first=(args.keep == "first"),
            quiet=args.quiet,
        )
        if not args.quiet:
            label = "would be deleted" if args.dry_run else "deleted"
            print(f"{deleted} duplicate file(s) {label}.", file=sys.stderr)

    # cron-friendly exit: 1 if any duplicates found, else 0
    return 1 if groups else 0


if __name__ == "__main__":
    sys.exit(main())
