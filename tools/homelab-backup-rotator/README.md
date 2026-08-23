# homelab-backup-rotator

A tiny, dependency-free backup rotation helper for self-hosted services.

It keeps the N most recent backup files for each "group" you define and deletes
the rest. Pair it with your own dump or tar command and drop it into cron. No
pip installs, no root, no offsite uploads, no secrets. Just simple local
cleanup so your homelab SSD does not fill up on Tuesday morning.

## What it does

- Scans a directory for files matching one or more glob patterns.
- Sorts each matching group by modification time (newest first).
- Keeps the configured number of newest files per group.
- Deletes the rest (with an optional dry-run mode).
- Prints a clear report that is easy to read in cron mail or logs.

## What it does not do

- It does not create backups. Write your own `pg_dump` or `tar` step first.
- It does not encrypt or upload offsite. Keep it simple.
- It does not require root or any third party Python package.

## Requirements

- Python 3.9 or newer (only the standard library is used).
- Read and write access to your backup directory.

## Installation

Clone the repo and make the script executable, or just run it with
`python3`:

```bash
git clone https://github.com/cappy-dev/homelab-backup-rotator.git
cd homelab-backup-rotator
chmod +x backup_rotator.py
```

No virtual environment is needed. There are zero dependencies.

## Usage

```bash
python3 backup_rotator.py <directory> -p '<pattern>=<keep>' [-n] [-v]
```

- `directory`: folder that holds your backup files.
- `-p / --policy`: a retention policy, repeatable. Format is `pattern=keep`.
- `-n / --dry-run`: show what would be deleted, but delete nothing.
- `-v / --verbose`: print one line per file action.
- `--version`: print the version and exit.

A policy is a glob pattern plus the number of newest files to keep. For
example `nextcloud-*.tar.gz=7` keeps the 7 newest files whose name matches
`nextcloud-*.tar.gz` and deletes the rest.

## Examples

Dry run first to preview what would happen:

```bash
python3 backup_rotator.py /srv/backups \
  -p 'nextcloud-*.tar.gz=7' \
  -p 'pihole-*.tar.gz=4' \
  -n
```

Run it for real (no `-n`):

```bash
python3 backup_rotator.py /srv/backups \
  -p 'nextcloud-*.tar.gz=7' \
  -p 'pihole-*.tar.gz=4' \
  -v
```

Pair it with a real backup step in a shell script:

```bash
#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=/srv/backups
mkdir -p "$BACKUP_DIR"

# 1. Make the backup.
STAMP=$(date +%Y%m%d)
tar czf "$BACKUP_DIR/nextcloud-$STAMP.tar.gz" -C /var/www nextcloud

# 2. Rotate old backups, keep the 7 newest.
python3 /opt/homelab-backup-rotator/backup_rotator.py \
  "$BACKUP_DIR" \
  -p 'nextcloud-*.tar.gz=7'
```

Wire that script into cron nightly:

```cron
0 2 * * * /usr/local/bin/nextcloud-backup.sh
```

A typical report looks like:

```
  homelab-backup-rotator [LIVE] - 1.0.0
  ----------------------------------------------------
  directory : /srv/backups
  scanned   : 14 file(s)
  kept      : 11 file(s)
  deleted   : 3 file(s)
  freed     : 612.4 MB
  finished  : 2026-08-01 02:00:01 UTC
  removed:
    - nextcloud-20260720.tar.gz
    - nextcloud-20260721.tar.gz
    - pihole-20260715.tar.gz
```

## Common patterns

Keep a week of database dumps:

```bash
python3 backup_rotator.py /srv/backups -p 'db-*.sql.gz=7'
```

Keep a month of configs and a week of media:

```bash
python3 backup_rotator.py /srv/backups \
  -p 'configs-*.tar.gz=30' \
  -p 'media-*.tar.gz=7'
```

Delete everything older than the newest 1 copy (aggressive):

```bash
python3 backup_rotator.py /srv/backups -p 'daily-*.tar.gz=1'
```

## Exit codes

- `0`: rotation completed successfully.
- `2`: bad input (missing directory, malformed policy, directory not found).

## Testing it safely

Always run with `-n` the first time against a real directory. The dry run shows
exactly which files would be deleted and how much space would be freed, without
touching anything. Once the output looks right, drop `-n` and let cron handle
the rest.

## License

MIT License. See `LICENSE` for details.
