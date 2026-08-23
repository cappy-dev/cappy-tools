#!/usr/bin/env python3
"""
disk-xray: Zero-dependency disk usage analyzer for Linux.

Scans directories and shows the biggest space hogs in a clean tree view.
Pure Python standard library. No pip install, no virtualenv, no fuss.

Usage:
    python3 disk_xray.py [options] [path ...]

If no path is given, the current directory is used.

A homelab helper from cappy-dev.
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

__version__ = "1.0.0"

# Exit codes (cron-friendly)
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_ACCESS = 2
EXIT_INTERRUPTED = 130


class DirNode:
    """A directory in the usage tree."""

    __slots__ = ("path", "size", "children", "file_count", "dir_count", "errors")

    def __init__(self, path):
        self.path = path
        self.size = 0
        self.children = {}
        self.file_count = 0
        self.dir_count = 0
        self.errors = 0

    def add_child(self, name, node):
        self.children[name] = node


def human_size(num_bytes):
    """Format a byte count as a human-readable string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    units = ["KB", "MB", "GB", "TB", "PB"]
    size = float(num_bytes)
    for unit in units:
        size /= 1024.0
        if size < 1024:
            # One decimal place, but drop the .0 for clean integers-ish values
            if size >= 100:
                return f"{int(round(size))} {unit}"
            return f"{size:.1f} {unit}"
    return f"{size:.1f} EB"


def scan_path(root, follow_symlinks=False, count_hardlinks=False,
              ignore=None, show_progress=False, out=sys.stderr):
    """
    Walk a directory tree and build a DirNode structure with sizes.

    Sizes are real file sizes (du-style: symbolic links count as their
    target only if follow_symlinks is True; otherwise they contribute 0).
    Hard-linked files are counted once per inode when count_hardlinks is
    False (the default, matching `du` behavior). When True, every link
    counts.
    """
    root = os.path.abspath(root)
    root_node = DirNode(root)
    seen_inodes = set()
    ignore_set = set(ignore) if ignore else set()
    scanned = 0

    def is_ignored(name, full):
        if name in ignore_set:
            return True
        for prefix in ignore_set:
            if prefix.endswith("/") and full.startswith(prefix):
                return True
        return False

    # Use an explicit stack to avoid recursion limits on deep trees.
    # Each stack entry: (dir_node, dir_path)
    stack = [(root_node, root)]

    while stack:
        try:
            node, cur_path = stack.pop()
        except KeyboardInterrupt:
            raise

        try:
            entries = sorted(os.scandir(cur_path), key=lambda e: e.name)
        except PermissionError:
            node.errors += 1
            continue
        except OSError as exc:
            node.errors += 1
            print(f"warn: cannot read {cur_path}: {exc}", file=out)
            continue

        for entry in entries:
            full = entry.path
            name = entry.name
            if is_ignored(name, full):
                continue

            scanned += 1
            if show_progress and scanned % 5000 == 0:
                print(f"\r  scanned {scanned:,} entries...", end="", file=out)

            try:
                if entry.is_symlink():
                    if follow_symlinks:
                        try:
                            st = entry.stat(follow_symlinks=True)
                        except OSError:
                            node.errors += 1
                            continue
                    else:
                        # Symlinks themselves are tiny; skip for size.
                        node.file_count += 1
                        continue
                else:
                    st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                node.errors += 1
                continue

            if entry.is_dir(follow_symlinks=False):
                child = DirNode(full)
                node.add_child(name, child)
                node.dir_count += 1
                stack.append((child, full))
            else:
                # File (or symlink-to-file when following).
                size = st.st_size
                if not count_hardlinks and st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen_inodes:
                        node.file_count += 1
                        continue
                    seen_inodes.add(key)
                node.size += size
                node.file_count += 1

    # Progress newline
    if show_progress and scanned >= 5000:
        print("", file=out)

    # Roll up sizes from children.
    _rollup(root_node)
    return root_node


def _rollup(node):
    """Compute directory sizes by summing children (post-order)."""
    total = node.size
    files = node.file_count
    dirs = node.dir_count
    for child in node.children.values():
        _rollup(child)
        total += child.size
        files += child.file_count
        dirs += child.dir_count
    node.size = total
    node.file_count = files
    node.dir_count = dirs


def top_dirs(node, n):
    """Return the top N immediate children of a node by size."""
    kids = list(node.children.values())
    kids.sort(key=lambda c: c.size, reverse=True)
    return kids[:n]


def find_big_files(node, n):
    """Find the N largest individual files in the whole tree."""
    big = []

    def walk(n):
        # Files in this node (we need to re-scan to get per-file sizes, since
        # DirNode only stores the sum). For efficiency we rely on the size sum
        # already being correct; to find big files we do a separate pass.
        try:
            for entry in os.scandir(n.path):
                if entry.name in (".", ".."):
                    continue
                if entry.is_symlink():
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                big.append((st.st_size, entry.path))
        except (PermissionError, OSError):
            pass
        for child in n.children.values():
            walk(child)

    walk(node)
    big.sort(key=lambda x: x[0], reverse=True)
    return big[:n]


def render_tree(node, top, indent="", max_depth=3, depth=0,
                out=sys.stdout, threshold=None):
    """Render a tree of the biggest directories."""
    if depth > max_depth:
        return
    children = top_dirs(node, top)
    if not children:
        return
    for i, child in enumerate(children):
        if threshold is not None and child.size < threshold:
            break
        is_last = (i == len(children) - 1) or (
            threshold is not None and
            (i + 1 < len(children) and children[i + 1].size < threshold)
        )
        branch = "+" if is_last else "|"
        marker = "-" if is_last else "+"
        bar = _bar(child.size, node.size)
        label = os.path.basename(child.path) or child.path
        print(f"{indent}{marker}-- {label:<28} {human_size(child.size):>10} {bar}", file=out)
        if is_last:
            new_indent = indent + "    "
        else:
            new_indent = indent + "|   "
        render_tree(child, top, new_indent, max_depth, depth + 1, out, threshold)


def _bar(size, total, width=20):
    """Draw a simple ASCII bar proportional to size/total."""
    if total <= 0:
        return "[" + " " * width + "]"
    ratio = min(size / total, 1.0)
    filled = int(round(ratio * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def render_summary(node, out=sys.stdout):
    """Print a one-line summary for the scanned root."""
    print(f"\nRoot:    {node.path}", file=out)
    print(f"Total:   {human_size(node.size)}", file=out)
    print(f"Files:   {node.file_count:,}", file=out)
    print(f"Dirs:    {node.dir_count:,}", file=out)
    if node.errors:
        print(f"Errors:  {node.errors} (permission denied or unreadable)", file=out)


def render_big_files(files, out=sys.stdout, n=10):
    """Print the list of largest individual files."""
    if not files:
        return
    print(f"\nTop {len(files)} largest individual files:", file=out)
    for size, path in files:
        print(f"  {human_size(size):>10}  {path}", file=out)


def render_html(root_node, big_files, out_path):
    """Write an HTML report to out_path."""
    rows = []
    for child in top_dirs(root_node, 50):
        pct = (child.size / root_node.size * 100) if root_node.size else 0
        label = os.path.basename(child.path) or child.path
        rows.append(
            f"      <tr><td>{_esc(label)}</td>"
            f"<td class='num'>{human_size(child.size)}</td>"
            f"<td>{pct:.1f}%</td>"
            f"<td><div class='bar' style='width:{pct:.1f}%'></div></td></tr>"
        )
    file_rows = []
    for size, path in big_files:
        file_rows.append(
            f"      <tr><td class='num'>{human_size(size)}</td>"
            f"<td>{_esc(path)}</td></tr>"
        )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>disk-xray report for {_esc(root_node.path)}</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; margin: 2rem; color: #222; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 0.5rem; }}
  th, td {{ text-align: left; padding: 0.3rem 0.6rem; border-bottom: 1px solid #eee; }}
  th {{ background: #f6f6f6; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .bar {{ height: 1rem; background: #4a90d9; border-radius: 3px; min-width: 2px; }}
  .summary {{ background: #f9f9f9; padding: 1rem; border-radius: 6px; }}
  .summary span {{ display: inline-block; margin-right: 2rem; }}
  footer {{ margin-top: 2rem; color: #888; font-size: 0.8rem; }}
</style>
</head>
<body>
<h1>disk-xray report</h1>
<div class="summary">
  <span><b>Path:</b> {_esc(root_node.path)}</span>
  <span><b>Total:</b> {human_size(root_node.size)}</span>
  <span><b>Files:</b> {root_node.file_count:,}</span>
  <span><b>Dirs:</b> {root_node.dir_count:,}</span>
</div>
<h2>Top directories by size</h2>
<table>
  <thead><tr><th>Directory</th><th class='num'>Size</th><th>% of total</th><th></th></tr></thead>
  <tbody>
{chr(10).join(rows)}
  </tbody>
</table>
<h2>Top individual files</h2>
<table>
  <thead><tr><th class='num'>Size</th><th>Path</th></tr></thead>
  <tbody>
{chr(10).join(file_rows)}
  </tbody>
</table>
<footer>Generated by disk-xray v{__version__} on {now}</footer>
</body>
</html>
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def _esc(text):
    """Minimal HTML escaping using the standard library."""
    import html as _html
    return _html.escape(text, quote=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="disk-xray",
        description="Zero-dependency disk usage analyzer. Find the biggest"
                    " space hogs in a directory tree.",
        epilog="Part of the cappy-dev homelab toolkit.",
    )
    parser.add_argument("paths", nargs="*", default=["."],
                        help="one or more directories to scan (default: .)")
    parser.add_argument("-n", "--top", type=int, default=10,
                        help="show top N entries per level (default: 10)")
    parser.add_argument("-d", "--depth", type=int, default=3,
                        help="max tree depth (default: 3)")
    parser.add_argument("-f", "--big-files", type=int, default=10,
                        help="also show the N largest individual files "
                             "(default: 10, use 0 to disable)")
    parser.add_argument("-L", "--follow-symlinks", action="store_true",
                        help="follow symbolic links (default: off)")
    parser.add_argument("--count-hardlinks", action="store_true",
                        help="count every hard link (default: count each "
                             "shared inode once, like du)")
    parser.add_argument("-i", "--ignore", action="append", default=[],
                        help="ignore directory name or path prefix "
                             "(repeatable)")
    parser.add_argument("--threshold", default=None,
                        help="hide entries smaller than this (e.g. 100M, 1G)")
    parser.add_argument("--html", metavar="FILE",
                        help="also write an HTML report to FILE")
    parser.add_argument("--progress", action="store_true",
                        help="print scan progress to stderr")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="suppress the summary header")
    parser.add_argument("--version", action="version",
                        version=f"disk-xray {__version__}")
    args = parser.parse_args(argv)

    threshold_bytes = _parse_size(args.threshold) if args.threshold else None

    saw_error = False
    for path in args.paths:
        if not os.path.isdir(path):
            print(f"error: {path} is not a directory", file=sys.stderr)
            saw_error = True
            continue

        if not args.quiet and len(args.paths) > 1:
            print(f"=== {path} ===", file=sys.stdout)

        try:
            node = scan_path(
                path,
                follow_symlinks=args.follow_symlinks,
                count_hardlinks=args.count_hardlinks,
                ignore=args.ignore,
                show_progress=args.progress,
            )
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return EXIT_INTERRUPTED

        if not args.quiet:
            render_summary(node)

        if node.size > 0:
            print("", file=sys.stdout)
            root_label = os.path.basename(node.path) or node.path
            bar = _bar(node.size, node.size)
            print(f"{root_label:<28} {human_size(node.size):>10} {bar}", file=sys.stdout)
            render_tree(node, args.top, indent="", max_depth=args.depth,
                        threshold=threshold_bytes)

        if args.big_files > 0:
            big = find_big_files(node, args.big_files)
            render_big_files(big)

        if args.html:
            big = find_big_files(node, 20) if node.size > 0 else []
            render_html(node, big, args.html)
            if not args.quiet:
                print(f"\nHTML report written to {args.html}", file=sys.stdout)

        if node.errors:
            saw_error = True

    return EXIT_NO_ACCESS if saw_error and not args.paths else EXIT_OK


def _parse_size(text):
    """Parse a human size string like '100M' or '1.5G' into bytes."""
    text = text.strip().upper()
    if not text:
        return None
    units = {"B": 1, "K": 1024, "KB": 1024, "M": 1024**2,
             "MB": 1024**2, "G": 1024**3, "GB": 1024**3,
             "T": 1024**4, "TB": 1024**4}
    for suffix in ("KB", "MB", "GB", "TB", "B", "K", "M", "G", "T"):
        if text.endswith(suffix):
            num = text[:-len(suffix)].strip()
            try:
                return int(float(num) * units[suffix])
            except ValueError:
                break
    # No suffix: treat as raw bytes.
    try:
        return int(float(text))
    except ValueError:
        print(f"warning: could not parse size '{text}', ignoring threshold",
              file=sys.stderr)
        return None


if __name__ == "__main__":
    sys.exit(main())
