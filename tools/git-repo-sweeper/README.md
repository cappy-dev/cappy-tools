# git-repo-sweeper

Walk a directory tree, find every nested git repository, and tell you which ones need attention. Uncommitted changes, unpushed commits, branches that have fallen behind, and stale branches are all reported at a glance. Zero dependencies beyond Python 3.8 and the git CLI.

Perfect for homelab users, developers with dozens of cloned repos, and anyone who keeps local mirrors of upstream projects. Run it from cron and let the exit code tell you whether anything drifted while you were away.

## What it does

- Recursively scans a directory for git repositories (stops at the first repo it finds in a subtree)
- Reports repositories with uncommitted changes (modified, staged, or untracked files, with counts)
- Flags the current branch as ahead or behind its upstream, so you never forget a push or a pull
- Lists branches whose last commit is older than `--stale-days` days
- Notes branches that have a remote configured but no upstream tracking branch
- Prints a readable text report by default, or JSON for piping, or a self contained HTML status page
- Returns cron friendly exit codes so a scheduler can decide when to page a human

## Requirements

- Python 3.8 or newer
- The `git` command line tool on your PATH

No pip packages. Everything runs on the standard library.

## Installation

```bash
git clone https://github.com/cappy-dev/git-repo-sweeper.git
cd git-repo-sweeper
chmod +x git_repo_sweeper.py
```

There is nothing to install globally. Just run the script in place, or copy it wherever you keep your tools.

## Quick start

Scan the current directory and print a text report.

```bash
./git_repo_sweeper.py .
```

Sample output:

```
git-repo-sweeper: scanned 3 repo(s)
  1 need attention, 2 clean

Repos that need attention:

  /home/you/projects/my-app
    branch : main
    uncommitted: 2 modified, 0 staged, 5 untracked
    ahead/behind upstream: 3/0
    stale branches (30 days): old-feature, experiment-2024
```

Scan a specific tree and write an HTML status page alongside it.

```bash
./git_repo_sweeper.py ~/projects --html ~/projects/status.html
```

Scan quietly from cron and only alert when something needs attention.

```bash
0 6 * * * /home/you/tools/git_repo_sweeper.py /home/you/projects --quiet
```

Because the exit code is nonzero only when a repo needs attention, your scheduler can treat any nonzero exit as a reason to notify you.

## Output formats

### Text (default)

Human readable, scrape friendly, sent to stdout. Use `--quiet` to drop the progress line on stderr.

### JSON

Pipe machine readable output into other tools.

```bash
./git_repo_sweeper.py ~/projects --json | jq '.repos[] | select(.needs_attention)'
```

### HTML

A self contained, styled status page with a summary card row and a per repo table that color codes clean and attention rows. Drop the file on any static host.

```bash
./git_repo_sweeper.py ~/projects --html /var/www/repos-status.html
```

## Options

```
usage: git_repo_sweeper.py [-h] [path] [--max-depth N] [--stale-days N]
                           [--html FILE] [--json] [--quiet]

positional arguments:
  path                Directory to scan for nested git repositories
                      (default: current directory)

options:
  --max-depth N       Maximum directory depth to descend. 0 means unlimited
  --stale-days N      A branch is stale if its last commit is older than this
                      many days (default: 30, 0 disables)
  --html FILE         Write an HTML report to FILE in addition to text output
  --json              Emit JSON to stdout instead of the plain text report
  --quiet             Suppress the per-line scanning progress output
  -h, --help          Show this message and exit
```

## Exit codes

Designed for cron and other schedulers.

- `0` Every scanned repository is clean, nothing to report
- `1` Usage error (bad arguments or missing path)
- `2` One or more repositories need attention
- `3` Could not scan (path does not exist, or git is not installed)

## How it detects things

The tool shells out to git using only safe, read only plumbing commands.

- `git status --porcelain=v1` for the working tree and index state
- `git rev-list --left-right --count @{u}...BRANCH` for ahead/behind counts
- `git for-each-ref` with `committerdate:unix` to compute branch age
- `git remote` to decide whether a missing upstream is actually a problem

It never runs any commit, push, pull, checkout, or fetch. Sweeping is strictly read only and safe to run at any time.

## Use cases

- Morning review: run it over your projects folder and fix the flagged repos before coffee
- CI gate: check that a monorepo's working copies have no drift before a release
- Homelab hygiene: point it at `/opt` or `/srv` where service repos live and watch for forgotten work
- Onboarding: hand a new teammate the script so they can see the state of the shared repos tree in one command

## License

MIT. See the [LICENSE](LICENSE) file for details.
