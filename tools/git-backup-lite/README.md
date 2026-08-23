# git-backup-lite

Zero-dependency local backup for git repositories. One Python script, no extra
packages, just the standard library.

Point it at a folder full of git repos and it bundles each one into a timestamped
backup run. Non-bare repos are captured with `git bundle` (one file per repo that
preserves every ref and object). Bare repos are copied directly. A JSON report is
written beside the backups so you can see what happened in each run.

Great for homelab cron jobs, offline snapshots, and anyone who wants a simple
"copy my repos somewhere safe" script without wiring up a full backup suite.

## Features

- No third-party packages. Python 3.8 or newer plus the `git` binary.
- Works on bare and non-bare repos.
- `git bundle` output keeps the full history, branches, and tags.
- Optional `--mirror` mode also copies the working tree.
- Retention with `--keep N` prunes old run folders.
- `--dry-run` shows the plan without writing anything.
- JSON run report for scripting and alerting.
- Exclude subdirectories from the scan.

## Installation

Drop the script anywhere and make it executable:

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/git-backup-lite/main/git_backup.py
chmod +x git_backup.py
```

Or clone the repo:

```bash
git clone https://github.com/cappy-dev/git-backup-lite.git
cd git-backup-lite
```

That is it. No pip install, no virtualenv, no build step.

## Usage

```bash
python3 git_backup.py <source-dir> <backup-dir> [options]
```

Back up everything under `~/projects` into `/mnt/backup`:

```bash
python3 git_backup.py ~/projects /mnt/backup
```

Keep only the seven most recent runs (handy for a nightly cron job):

```bash
python3 git_backup.py ~/projects /mnt/backup --keep 7
```

Also copy working trees (bigger and slower, but preserves uncommitted files):

```bash
python3 git_backup.py ~/projects /mnt/backup --mirror
```

Skip some directories during the scan:

```bash
python3 git_backup.py ~/projects /mnt/backup --exclude node_modules --exclude .cache
```

See what would happen without writing anything:

```bash
python3 git_backup.py ~/projects /mnt/backup --dry-run
```

## Output layout

Each run creates a directory named `run-YYYYMMDD-HHMMSS` inside your backup dir:

```
/backup
  run-20260729-013000
    repo-one
      repo-one.bundle
    server-config
      server-config.bundle
      worktree/        # only with --mirror
    report.json
```

`report.json` lists every repo found, whether it succeeded, and the byte size of
the backup. It is stable JSON, so you can diff runs or feed it into monitoring.

## Example cron entry

Run a nightly backup at 1:30 AM, keep two weeks of runs, only log failures:

```bash
30 1 * * * /home/me/git-backup-lite/git_backup.py /home/me/projects /mnt/backup --keep 14 --quiet
```

## Restoring from a bundle

A bundle is a full git backup. To restore a repo from one:

```bash
git clone repo-one.bundle repo-one-restored
cd repo-one-restored
git fetch --all
```

For a bare repo backup, copy the folder back and run `git fsck` to confirm:

```bash
cp -r run-20260729-013000/standalone.git /srv/git/standalone.git
git --git-dir /srv/git/standalone.git fsck --full
```

## Exit codes

- `0` every repo backed up, or no repos found.
- `1` one or more repos failed to back up.
- `2` usage or setup error (bad paths, missing destination).

## Requirements

- Python 3.8 or newer.
- The `git` command available on your `PATH`.

Tested on Linux. Should also work on macOS and WSL since it uses only the
standard library plus the git CLI.

## License

MIT. See `LICENSE`.
