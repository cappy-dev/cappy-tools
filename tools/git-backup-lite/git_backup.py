#!/usr/bin/env python3
"""
git-backup-lite: Zero-dependency local backup for bare and working git repos.

Scans a directory tree for git repositories and copies each one into a backup
folder inside a timestamped run directory. Working repos are bundled with
`git bundle` (a single file that preserves all refs and objects). Bare repos
are copied directly. A JSON run report is written next to the backups.

Usage:
  python3 git_backup.py /home/me/projects /mnt/backup
  python3 git_backup.py /home/me/projects /mnt/backup --keep 3
  python3 git_backup.py /home/me/projects /mnt/backup --mirror --quiet

Options:
  --keep N      Keep only the N most recent run directories; delete older ones.
  --mirror      Also copy the working tree of non-bare repos (slower, bigger).
  --quiet       Suppress progress output; exit code only.
  --exclude DIR Skip a subdirectory name during the scan (can repeat).
  --dry-run     Report what would happen without writing anything.

Exit codes:
  0  all repos backed up (or none found)
  1  some repos failed
  2  usage / setup error

No third-party packages required. Python 3.8 or newer.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


GIT_MARKER = "HEAD"
BARE_MARKERS = ("HEAD", "config", "objects")


@dataclass
class RepoResult:
    path: str
    name: str
    bare: bool
    ok: bool
    message: str
    size_bytes: int = 0


@dataclass
class RunReport:
    started: str
    finished: str
    source: str
    dest: str
    found: int = 0
    succeeded: int = 0
    failed: int = 0
    results: List[RepoResult] = field(default_factory=list)

    def add(self, result: RepoResult) -> None:
        self.results.append(result)
        self.found += 1
        if result.ok:
            self.succeeded += 1
        else:
            self.failed += 1


def is_bare_repo(repo_path: Path) -> bool:
    """A bare repo stores its files at the top level; a non-bare repo stores
    them inside a .git directory."""
    head = repo_path / "HEAD"
    config = repo_path / "config"
    return head.is_file() and config.is_file()


def is_git_dir(candidate: Path) -> bool:
    """True if candidate looks like the internals of a git repo (either a
    .git folder or a bare repo root)."""
    return all((candidate / m).exists() for m in BARE_MARKERS)


def find_repos(source: Path, excludes: set) -> List[Path]:
    """Walk source looking for .git directories and bare repos. Returns the
    directory that holds the git internals for each repo."""
    repos: List[Path] = []
    for root, dirs, _ in os.walk(source):
        # Respect excludes to prune the walk early.
        pruned = [d for d in dirs if d in excludes]
        for d in pruned:
            dirs.remove(d)
        if ".git" in dirs:
            repos.append(Path(root) / ".git")
            # Do not descend into the git internals.
            dirs.remove(".git")
            continue
        # Bare repo at top level: stop descending once identified.
        if is_git_dir(Path(root)) and is_bare_repo(Path(root)):
            repos.append(Path(root))
            dirs.clear()
    return repos


def repo_nice_name(git_dir: Path, source: Path) -> str:
    """Derive a human-friendly name for the repo. For a .git folder, use the
    parent directory name. For a bare repo, strip the common .git suffix."""
    if git_dir.name == ".git":
        parent = git_dir.parent
        return parent.name
    name = git_dir.name
    if name.endswith(".git"):
        name = name[:-4]
    return name


def dir_size(start: Path) -> int:
    total = 0
    for root, _, files in os.walk(start):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                continue
    return total


def run_git_bundle(git_dir: Path, dest_file: Path) -> str:
    """Create a git bundle capturing all refs. Returns an error string or ''."""
    cmd = ["git", "--git-dir", str(git_dir), "bundle", "create", str(dest_file), "--all"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "git bundle timed out"
    except FileNotFoundError:
        return "git executable not found"
    if proc.returncode != 0:
        return proc.stderr.strip() or "git bundle failed"
    return ""


def backup_one(git_dir: Path, source: Path, backup_root: Path,
               mirror: bool, dry_run: bool) -> RepoResult:
    name = repo_nice_name(git_dir, source)
    # A non-bare repo has its internals inside a ".git" subdirectory; a bare
    # repo exposes them at the top level. So bare is True when git_dir is NOT a
    # ".git" folder.
    bare = git_dir.name != ".git"
    safe_name = name.replace("/", "_").strip()
    dest_repo_dir = backup_root / safe_name
    message = ""

    if dry_run:
        return RepoResult(
            path=str(git_dir.parent if git_dir.name == ".git" else git_dir),
            name=name, bare=bare, ok=True, message="dry-run skipped",
        )

    dest_repo_dir.mkdir(parents=True, exist_ok=True)

    if bare:
        # Direct copy of the bare repo.
        try:
            if dest_repo_dir.exists():
                shutil.rmtree(dest_repo_dir)
            shutil.copytree(git_dir, dest_repo_dir)
        except OSError as exc:
            return RepoResult(str(git_dir), name, True, False,
                              "copy failed: " + str(exc))
        message = "copied bare repo"
    else:
        bundle_file = dest_repo_dir / (safe_name + ".bundle")
        err = run_git_bundle(git_dir, bundle_file)
        if err:
            return RepoResult(str(git_dir), name, False, False, err)
        message = "bundled"
        if mirror:
            # Also mirror the working tree below the bundle, ignoring .git.
            tree_src = git_dir.parent
            tree_dst = dest_repo_dir / "worktree"
            try:
                shutil.copytree(tree_src, tree_dst,
                                ignore=shutil.ignore_patterns(".git"))
                message += " + worktree"
            except OSError as exc:
                return RepoResult(str(git_dir), name, False, False,
                                  "worktree copy failed: " + str(exc))

    size = dir_size(dest_repo_dir)
    return RepoResult(
        path=str(git_dir.parent if git_dir.name == ".git" else git_dir),
        name=name, bare=bare, ok=True, message=message, size_bytes=size,
    )


def purge_old_runs(dest: Path, keep: int) -> List[str]:
    """Delete run directories older than the N most recent. Returns the list
    of removed directory names."""
    if keep <= 0:
        return []
    runs = sorted(
        [p for p in dest.iterdir() if p.is_dir() and p.name.startswith("run-")],
        key=lambda p: p.name,
        reverse=True,
    )
    removed = []
    for old in runs[keep:]:
        try:
            shutil.rmtree(old)
            removed.append(old.name)
        except OSError:
            continue
    return removed


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="git-backup-lite",
        description="Zero-dependency local backup for git repositories.",
    )
    p.add_argument("source", help="directory to scan for git repos")
    p.add_argument("dest", help="backup destination directory")
    p.add_argument("--keep", type=int, default=0,
                   help="keep only the N most recent run directories")
    p.add_argument("--mirror", action="store_true",
                   help="also copy the working tree of non-bare repos")
    p.add_argument("--quiet", action="store_true",
                   help="suppress progress output")
    p.add_argument("--exclude", action="append", default=[],
                   help="skip a subdirectory name during the scan (repeatable)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would happen, write nothing")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    source = Path(args.source).resolve()
    dest = Path(args.dest).resolve()

    quiet = args.quiet

    def say(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    if not source.is_dir():
        print("error: source directory does not exist: " + str(source),
              file=sys.stderr)
        return 2
    if not dest.exists():
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print("error: cannot create destination: " + str(exc),
                  file=sys.stderr)
            return 2
    if not dest.is_dir():
        print("error: destination is not a directory: " + str(dest),
              file=sys.stderr)
        return 2

    excludes = set(args.exclude)
    say("Scanning " + str(source) + " for git repos...")
    git_dirs = find_repos(source, excludes)
    say("Found " + str(len(git_dirs)) + " repo(s).")

    if not git_dirs:
        say("Nothing to back up. All done!")
        return 0

    started = datetime.datetime.now(datetime.timezone.utc)
    run_label = started.strftime("run-%Y%m%d-%H%M%S")
    if args.dry_run:
        run_dir = dest / (run_label + "-dryrun")
    else:
        run_dir = dest / run_label
    if not args.dry_run:
        run_dir.mkdir(parents=True, exist_ok=False)

    report = RunReport(started=started.isoformat(),
                       finished="",
                       source=str(source),
                       dest=str(run_dir))
    say("Backup run: " + run_dir.name)

    for gd in git_dirs:
        rel = gd.parent.name if gd.name == ".git" else gd.name
        say("  " + rel + " ... ", )
        result = backup_one(gd, source, run_dir,
                            mirror=args.mirror, dry_run=args.dry_run)
        report.add(result)
        status = "ok" if result.ok else "FAIL"
        say("    " + status + ": " + result.message)

    report.finished = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if not args.dry_run:
        report_path = run_dir / "report.json"
        try:
            report_path.write_text(
                json.dumps({
                    "started": report.started,
                    "finished": report.finished,
                    "source": report.source,
                    "dest": report.dest,
                    "found": report.found,
                    "succeeded": report.succeeded,
                    "failed": report.failed,
                    "results": [
                        {
                            "path": r.path,
                            "name": r.name,
                            "bare": r.bare,
                            "ok": r.ok,
                            "message": r.message,
                            "size_bytes": r.size_bytes,
                        } for r in report.results
                    ],
                }, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            say("warn: could not write report.json: " + str(exc))

        if args.keep > 0:
            removed = purge_old_runs(dest, args.keep)
            for name in removed:
                say("Removed old run: " + name)

    say("Backed up " + str(report.succeeded) + "/" + str(report.found)
        + " repo(s).")
    if report.failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
