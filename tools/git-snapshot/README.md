# git-snapshot

Creates timestamped tar archives of Git repositories, including the branch and commit in the filename.

## What it does

Creates `.tar.gz` archives of git repos with timestamps and branch/commit info baked into the filename. Perfect for cron-based backups, manual checkpoints before risky rebases, or just keeping history of your work outside of git itself.

## Quick start

```bash
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/git-snapshot
chmod +x git-snapshot.sh

# Snapshot the current repo
./git-snapshot.sh
```

## Usage
```bash
./git-snapshot.sh [OPTIONS] [REPO_PATH]
```

If REPO_PATH is omitted, the current directory is used.

### Options

- `-o, --output DIR` - Output directory for the archive (default: repo parent directory)
- `-g, --include-dotgit` - Include the `.git` directory in the archive
- `-u, --skip-untracked` - Exclude untracked files from the snapshot
- `-n, --no-compress` - Create an uncompressed `.tar` instead of `.tar.gz`
- `-p, --prefix NAME` - Custom prefix for the archive filename
- `--dry-run` - Print what would happen without creating anything
- `-v, --verbose` - Show detailed output
- `-h, --help` - Show help message
- `--version` - Show version number

### Examples
Snapshot the current repo (archive goes to the parent directory):

```bash
./git-snapshot.sh
```

Snapshot a specific repo into `~/backups`:

```bash
./git-snapshot.sh -o ~/backups /path/to/my-repo
```

Snapshot including the `.git` directory (full backup):

```bash
./git-snapshot.sh --include-dotgit
```

Only include tracked files (skip untracked):

```bash
./git-snapshot.sh --skip-untracked
```

Custom prefix for the archive name:

```bash
./git-snapshot.sh --prefix myproject
```

Dry run to see what would be archived:

```bash
./git-snapshot.sh --dry-run
```

Uncompressed tar output:

```bash
./git-snapshot.sh --no-compress
```

## Archive naming

Default format:

```
<repo-name>-<branch>-<short-sha>-<timestamp>.tar.gz
```

Examples:

```
myapp-main-a3f7c2b-20260628-153000.tar.gz
myapp-main-a3f7c2b-dirty-20260628-153000.tar.gz
```

The `-dirty` suffix appears when the working tree has uncommitted changes, so you can tell snapshot apart from a clean checkout.

## Automation with cron

Add a line to your crontab for automatic daily snapshots:

```bash
# Daily at 2 AM
0 2 * * * /path/to/git-snapshot.sh -o /mnt/backups/git /home/user/projects/myapp
```

For multiple repos:

```bash
0 2 * * * for repo in /home/user/projects/*; do /path/to/git-snapshot.sh -o /mnt/backups/git "$repo"; done
```

## How it works

By default, the script uses `git archive` to package tracked files. This means:

- Only files git knows about are included (unless you use `--skip-untracked` to explicitly exclude untracked, or `--include-dotgit` for full backups)
- Untracked files are added automatically unless you pass `--skip-untracked`
- Submodules are included as their current committed state
- The archive uses a prefix directory matching the repo name

When `--include-dotgit` is used, the entire directory (including `.git`) is archived with standard `tar`. This gives you a complete mirror of the repo at that point in time, including all history, reflogs, and stash entries.

## Restoring from a snapshot

Without `.git` (default snapshot):

```bash
mkdir my-repo && tar -xzf my-repo-main-a3f7c2b-20260628-153000.tar.gz -C my-repo
cd my-repo/my-repo
git init
git add .
git commit -m "Restored from snapshot"
```

With `.git` (full backup):

```bash
tar -xzf my-repo-main-a3f7c2b-20260628-153000.tar.gz
cd my-repo
# The .git directory is preserved, so all history is intact
git log   # shows full history
```

## Requirements
- Bash 4.0+
- Git
- tar and gzip (standard on any Linux/macOS)

No Python, no Node.js, no extra packages. Just `git` and `tar`.

## Why not just `git bundle`?

`git bundle` creates a single-file clone of the repo including history, which is great for transferring repos. But `git-snapshot` serves a different purpose:

- `git bundle` requires a separate `git clone` step to inspect the contents
- `git-snapshot` creates a regular `.tar.gz` you can browse immediately
- `git-snapshot` can include untracked files and build artifacts that `git bundle` ignores
- `git-snapshot` includes the full `.git` directory when you want it, preserving reflogs and stash

Use `git bundle` for creating portable repo clones. Use `git-snapshot` for point-in-time backup archives you can open like any other tarball.

## License

AGPL-3.0. See the [root license](../../LICENSE).
