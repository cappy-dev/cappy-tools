#!/usr/bin/env python3
"""
git-repo-sweeper

A zero-dependency Python tool that walks a directory tree, finds local git
repositories, and reports the ones that need attention:

  - Repositories with uncommitted changes (modified, staged, or untracked)
  - Repositories whose current branch has commits not pushed to an upstream
  - Repositories that are ahead AND behind their upstream (need a pull)
  - Repositories that have no upstream tracking branch yet
  - Repositories that have branches that have not been touched in N days

Output options:
  - Plain text (the default, cron friendly)
  - HTML report file (--html out.html)
  - JSON (--json) for piping into other tools

Exit codes (cron friendly):
  0  No issues found in any scanned repo
  1  Usage error (bad arguments or missing paths)
  2  One or more repositories need attention
  3  Could not scan (path does not exist, git not installed)

This tool uses only the Python 3.8+ standard library and the git CLI. No pip
packages required.

Author: cappy-dev
License: AGPL-3.0
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def run_git(repo: Path, args: List[str]) -> Optional[str]:
    """Run a git command inside repo. Return stdout on success, None on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo)] + args,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def git_available() -> bool:
    try:
        proc = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def find_repos(root: Path) -> List[Path]:
    """Walk root looking for directories that contain a .git entry.

    When a git repo is found, that directory is yielded and its children are
    skipped so we do not descend into nested working copies or submodules.
    """
    repos: List[Path] = []
    # os.walk lets us prune by mutating dirs in place.
    for dirpath, dirnames, _filenames in os.walk(root):
        # Skip the inside of .git directories.
        if ".git" in dirnames:
            repos.append(Path(dirpath))
            # Do not descend further into this repo.
            dirnames[:] = []
        else:
            # Never descend into .git internals but keep scanning siblings.
            dirnames[:] = [d for d in dirnames if d != ".git"]
    return repos


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class RepoReport:
    path: Path
    branch: str = "(unknown)"
    has_uncommitted: bool = False
    untracked_count: int = 0
    modified_count: int = 0
    staged_count: int = 0
    ahead: int = 0
    behind: int = 0
    stale_branches: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return bool(
            self.has_uncommitted
            or self.ahead
            or self.behind
            or self.stale_branches
            or self.errors
        )

    @property
    def relative_path(self) -> str:
        try:
            return str(self.path.resolve())
        except OSError:
            return str(self.path)

    def to_dict(self) -> dict:
        return {
            "path": self.relative_path,
            "branch": self.branch,
            "has_uncommitted_changes": self.has_uncommitted,
            "untracked_count": self.untracked_count,
            "modified_count": self.modified_count,
            "staged_count": self.staged_count,
            "ahead": self.ahead,
            "behind": self.behind,
            "stale_branches": self.stale_branches,
            "errors": self.errors,
            "needs_attention": self.needs_attention,
        }


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #

def current_branch(repo: Path) -> str:
    out = run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    if out:
        return out.strip()
    # Detached HEAD.
    out = run_git(repo, ["rev-parse", "--short", "HEAD"])
    if out:
        return "(detached at " + out.strip() + ")"
    return "(unknown)"


def has_any_remote(repo: Path) -> bool:
    """True if the repo has at least one configured remote."""
    out = run_git(repo, ["remote"])
    if not out:
        return False
    return bool(out.strip())


def porcelain_status(repo: Path) -> str:
    return run_git(repo, ["status", "--porcelain=v1", "--untracked-files=normal"]) or ""


def upstream_refs(repo: Path, branch: str) -> tuple[int, int, bool]:
    """Return (ahead, behind, has_upstream) for branch.

    ahead/behind are 0 when there is no upstream or git cannot compute them.
    """
    if branch.startswith("("):
        return (0, 0, False)
    out = run_git(
        repo,
        ["rev-list", "--left-right", "--count", "@{u}..." + branch],
    )
    if not out:
        return (0, 0, False)
    parts = out.strip().split()
    if len(parts) != 2:
        return (0, 0, False)
    try:
        behind = int(parts[0])  # commits unique to upstream
        ahead = int(parts[1])   # commits unique to local branch
    except ValueError:
        return (0, 0, False)
    return (ahead, behind, True)


def stale_branches(repo: Path, days: int) -> List[str]:
    """Return branches whose last commit is older than days days."""
    if days <= 0:
        return []
    out = run_git(
        repo,
        ["for-each-ref", "--format=%(refname:short) %(committerdate:unix)", "refs/heads"],
    )
    if not out:
        return []
    cutoff = datetime.datetime.now(tz=datetime.timezone.utc).timestamp() - (days * 86400)
    stale: List[str] = []
    for line in out.strip().splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        name, ts_str = parts
        try:
            ts = float(ts_str)
        except ValueError:
            continue
        if ts < cutoff:
            stale.append(name)
    return stale


def inspect_repo(repo: Path, stale_days: int) -> RepoReport:
    report = RepoReport(path=repo)
    report.branch = current_branch(repo)
    if report.branch == "(unknown)":
        report.errors.append("Could not determine current branch")
        return report

    status = porcelain_status(repo)
    if status is not None:
        untracked = modified = staged = 0
        for line in status.splitlines():
            if not line:
                continue
            index = line[0]
            worktree = line[1]
            if index == "?":
                untracked += 1
            else:
                if index != " " and index != "?":
                    staged += 1
                if worktree != " ":
                    modified += 1
        report.untracked_count = untracked
        report.modified_count = modified
        report.staged_count = staged
        report.has_uncommitted = bool(untracked or modified or staged)

    ahead, behind, has_upstream = upstream_refs(repo, report.branch)
    report.ahead = ahead
    report.behind = behind
    if not has_upstream and has_any_remote(repo):
        # A remote is configured but the current branch is not tracking an
        # upstream, so local commits cannot be pushed without specifying one.
        report.errors.append("Branch has remote but no upstream tracking branch")

    report.stale_branches = stale_branches(repo, stale_days)
    return report


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def render_text(reports: List[RepoReport]) -> str:
    lines: List[str] = []
    flag = reports[0].needs_attention if reports else False
    summary = []
    total = len(reports)
    attention = sum(1 for r in reports if r.needs_attention)
    clean = total - attention
    summary.append("git-repo-sweeper: scanned {} repo(s)".format(total))
    summary.append("  {} need attention, {} clean".format(attention, clean))
    lines.append("\n".join(summary))

    if not reports:
        return "\n".join(lines) + "\n"

    flagged = [r for r in reports if r.needs_attention]
    flagged.sort(key=lambda r: r.relative_path)

    if not flagged:
        lines.append("\nNothing to do. Every scanned repo is clean.")
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("Repos that need attention:")
    for r in flagged:
        lines.append("")
        lines.append("  " + r.relative_path)
        lines.append("    branch : " + r.branch)
        if r.has_uncommitted:
            lines.append(
                "    uncommitted: {} modified, {} staged, {} untracked".format(
                    r.modified_count, r.staged_count, r.untracked_count
                )
            )
        if r.ahead or r.behind:
            lines.append("    ahead/behind upstream: {}/{}".format(r.ahead, r.behind))
        if r.stale_branches:
            lines.append("    stale branches ({}) days): {}".format(
                getattr(render_text, "_stale_days", 30),
                ", ".join(r.stale_branches),
            ))
        for err in r.errors:
            lines.append("    note: " + err)
    return "\n".join(lines) + "\n"


def render_json(reports: List[RepoReport]) -> str:
    payload = {
        "scanned": len(reports),
        "needs_attention": sum(1 for r in reports if r.needs_attention),
        "repos": [r.to_dict() for r in reports],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_html(reports: List[RepoReport], root: Path, generated_at: str) -> str:
    total = len(reports)
    attention = sum(1 for r in reports if r.needs_attention)
    clean = total - attention

    def esc(s: str) -> str:
        return html.escape(s, quote=True)

    rows: List[str] = []
    for r in sorted(reports, key=lambda x: x.relative_path):
        cls = "bad" if r.needs_attention else "good"
        badges = []
        if r.has_uncommitted:
            badges.append("uncommitted")
        if r.ahead:
            badges.append("ahead {}".format(r.ahead))
        if r.behind:
            badges.append("behind {}".format(r.behind))
        if r.stale_branches:
            badges.append("stale: " + ", ".join(r.stale_branches))
        if r.errors:
            badges.append("; ".join(r.errors))
        status = "<span class=\"ok\">clean</span>" if not r.needs_attention else \
            ", ".join(badges) or "needs attention"
        rows.append(
            "      <tr class=\"{cls}\">\n"
            "        <td>{path}</td>\n"
            "        <td>{branch}</td>\n"
            "        <td>{status}</td>\n"
            "      </tr>".format(
                cls=cls,
                path=esc(r.relative_path),
                branch=esc(r.branch),
                status=status,
            )
        )

    overall = "all clean" if attention == 0 else "{} repos need attention".format(attention)
    title = "git-repo-sweeper report"
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "  <title>{title}</title>\n"
        "  <style>\n"
        "    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}\n"
        "    h1 {{ margin-bottom: 0.25rem; }}\n"
        "    .meta {{ color: #555; margin-bottom: 1.5rem; font-size: 0.9rem; }}\n"
        "    .summary {{ display: flex; gap: 1.5rem; margin-bottom: 1.5rem; }}\n"
        "    .card {{ border: 1px solid #ddd; border-radius: 6px; padding: 0.75rem 1.25rem; }}\n"
        "    .card .num {{ font-size: 1.6rem; font-weight: 600; }}\n"
        "    table {{ border-collapse: collapse; width: 100%; }}\n"
        "    th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #eee; vertical-align: top; }}\n"
        "    tr.good td {{ background: #f4faf4; }}\n"
        "    tr.bad td {{ background: #fff5f5; }}\n"
        "    .ok {{ color: #2a7a2a; }}\n"
        "    .bad-text {{ color: #a02020; }}\n"
        "    code {{ background: #f4f4f4; padding: 0.05rem 0.3rem; border-radius: 3px; }}\n"
        "    .overall {{ margin-bottom: 1rem; }}\n"
        "    .overall.bad {{ color: #a02020; }}\n"
        "    .overall.good {{ color: #2a7a2a; }}\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <h1>{title}</h1>\n"
        "  <div class=\"meta\">Scanned <code>{root}</code> at {generated_at}</div>\n"
        "  <div class=\"overall {overall_cls}\"><strong>{overall}</strong></div>\n"
        "  <div class=\"summary\">\n"
        "    <div class=\"card\"><div class=\"num\">{total}</div><div>repos scanned</div></div>\n"
        "    <div class=\"card\"><div class=\"num\">{attention}</div><div>need attention</div></div>\n"
        "    <div class=\"card\"><div class=\"num\">{clean}</div><div>clean</div></div>\n"
        "  </div>\n"
        "  <table>\n"
        "    <thead><tr><th>Path</th><th>Branch</th><th>Status</th></tr></thead>\n"
        "    <tbody>\n"
        "{rows}\n"
        "    </tbody>\n"
        "  </table>\n"
        "</body>\n"
        "</html>\n"
    ).format(
        title=esc(title),
        root=esc(str(root)),
        generated_at=esc(generated_at),
        overall_cls="bad" if attention else "good",
        overall=esc(overall),
        total=total,
        attention=attention,
        clean=clean,
        rows="\n".join(rows) if rows else "      <tr><td colspan=\"3\">No repositories found.</td></tr>",
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="git-repo-sweeper",
        description=(
            "Scan a directory tree for git repos and report the ones that "
            "need attention (uncommitted changes, unpushed commits, stale "
            "branches). Zero dependencies, Python 3.8+."
        ),
    )
    p.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to scan for nested git repositories (default: current directory).",
    )
    p.add_argument(
        "--max-depth",
        type=int,
        default=0,
        help="Maximum directory depth to descend. 0 means unlimited (default: 0).",
    )
    p.add_argument(
        "--stale-days",
        type=int,
        default=30,
        help="A branch is stale if its last commit is older than this many days (default: 30, 0 disables).",
    )
    p.add_argument(
        "--html",
        metavar="FILE",
        help="Write an HTML report to FILE in addition to the text output.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout instead of the plain text report.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-line scanning output."),
    return p


def filter_by_depth(repos: List[Path], root: Path, max_depth: int) -> List[Path]:
    if max_depth <= 0:
        return repos
    root_resolved = root.resolve()
    kept: List[Path] = []
    for repo in repos:
        try:
            rel = repo.resolve().relative_to(root_resolved)
        except ValueError:
            kept.append(repo)
            continue
        depth = len(rel.parts)
        kept.append(repo)
        if depth > max_depth:
            kept.pop()
    return kept


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    root = Path(args.path).expanduser()
    if not root.exists() or not root.is_dir():
        sys.stderr.write("error: {!r} is not a directory\n".format(str(root)))
        return 3
    if not git_available():
        sys.stderr.write("error: git executable not found on PATH\n")
        return 3

    # Stash stale_days for the text renderer so it can show it.
    render_text._stale_days = args.stale_days  # type: ignore[attr-defined]

    if not args.quiet:
        sys.stderr.write("Scanning {} ...\n".format(str(root.resolve())))

    repos = find_repos(root)
    repos = filter_by_depth(repos, root, args.max_depth)
    repos.sort(key=lambda p: str(p.resolve()))

    reports: List[RepoReport] = []
    for repo in repos:
        reports.append(inspect_repo(repo, args.stale_days))

    if args.json:
        print(render_json(reports))
    else:
        sys.stdout.write(render_text(reports))

    if args.html:
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        html_out = render_html(reports, root.resolve(), generated_at)
        try:
            Path(args.html).write_text(html_out, encoding="utf-8")
            sys.stderr.write("HTML report written to {}\n".format(args.html))
        except OSError as exc:
            sys.stderr.write("error: could not write HTML file: {}\n".format(exc))
            return 1

    # Exit code 2 if any repo needs attention.
    attention = sum(1 for r in reports if r.needs_attention)
    return 2 if attention else 0


if __name__ == "__main__":
    sys.exit(main())
