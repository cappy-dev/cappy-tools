# log-rotator

Rotates, compresses, and prunes local log files by size, count, and age.

## Features
- Rotate when a log exceeds a size threshold (100k, 5M, 1G, ...)
- Keep at most N rotated copies per log file
- Delete rotated copies older than N days
- Optionally gzip rotated files (preserves the `.gz` suffix through shifts)
- Dry-run mode to preview actions without touching files
- Prune-only mode to clean up without rotating
- Timestamped report file for audit trails
- Cron-friendly exit codes: 0 ok, 1 warning, 2 usage error, 3 crash
- Pure Python standard library (Python >= 3.8)

## Quick start

```bash
# Copy the script somewhere on your PATH.
chmod +x log_rotator.py
sudo cp log_rotator.py /usr/local/bin/log-rotator

# Rotate nginx logs over 50M, keep 7 copies, gzip them.
log-rotator /var/log/nginx/access.log /var/log/nginx/error.log \
  --max-size 50M --keep-count 7 --compress
```

## Installation
No build step. No pip install. Just two files in this repo:

- `log_rotator.py` the entire tool

Copy `log_rotator.py` wherever you like. Make it executable:

```bash
chmod +x log_rotator.py
```

Run it directly:

```bash
./log_rotator.py --help
```

Or put it on your PATH:

```bash
sudo cp log_rotator.py /usr/local/bin/log-rotator
log-rotator --version
```

Requires Python 3.8 or newer. Nothing else.

## Usage
```
usage: log_rotator [-h] [-s MAX_SIZE] [-n KEEP_COUNT] [-d KEEP_DAYS]
                   [-z] [--no-compress] [--always] [--prune-only] [-v]
                   [--report REPORT] [--dry-run] [--version]
                   files [files ...]

Rotate, compress, and prune plain-text log files. Zero dependencies.

positional arguments:
  files                 Log files to rotate (supports ~ and $VAR expansion).

options:
  -s, --max-size SIZE   Rotate when a log exceeds this size. Default: 10M.
  -n, --keep-count N    Keep at most N rotated copies per file.
  -d, --keep-days N     Delete rotated copies older than N days.
  -z, --compress        Gzip-compress rotated files (sets .gz suffix).
      --no-compress     Disable compression (the default).
      --always          Always rotate regardless of file size.
      --prune-only      Do not rotate. Only delete rotations that exceed limits.
  -v, --verbose         Print details. Repeat (-vv) for more.
      --report PATH     Append a timestamped text report to this file.
      --dry-run         Show what would happen without modifying anything.
  -h, --help            Show this help message and exit.
      --version         Print the version and exit.
```

Size values accept `k`, `m`, `g`, `t` suffixes (binary: 1k = 1024 bytes).

## Examples
Rotate a single log over 10M (the default), keep 5 copies:

```bash
log-rotator /var/log/myapp.log --keep-count 5
```

Rotate multiple logs, compress, keep 14 copies, delete anything older than 30 days:

```bash
log-rotator /var/log/nginx/access.log /var/log/nginx/error.log \
  --max-size 100M --keep-count 14 --keep-days 30 --compress
```

Force a rotation every run (useful in a nightly cron):

```bash
log-rotator /var/log/myapp.log --always --keep-count 7 --compress
```

Prune only, do not rotate. Delete copies older than 90 days:

```bash
log-rotator /var/log/myapp.log --prune-only --keep-days 90
```

Preview what would happen without changing anything:

```bash
log-rotator /var/log/myapp.log --max-size 50M --dry-run
```

Append a timestamped report to a file for later inspection:

```bash
log-rotator /var/log/myapp.log --max-size 50M --compress \
  --report /var/log/rotation-report.txt -v
```

## How rotation works

For each log file:

1. If the file size is under the threshold, nothing happens (unless `--always`).
2. Every existing rotation is shifted up by one number.
   `app.log.1` becomes `app.log.2`, and so on. The `.gz` suffix stays put.
3. Rotations that exceed `--keep-count` or `--keep-days` are deleted.
4. The current log is renamed to `app.log.1`.
5. If `--compress` is set, `app.log.1` is gzipped into `app.log.1.gz`.
6. A fresh empty `app.log` is created so the writing process keeps its file handle.

This is not a log daemon. It does not signal or reload any service. It only
manages the files on disk. Most long-running services reopen their log file on
the next write because the file handle points at the inode, not the name. If
your service caches the file descriptor and will not pick up the new file, send
it a reload signal in the same cron entry:

```bash
0 3 * * *  /usr/local/bin/log-rotator /var/log/myapp.log --keep-count 7 -z && kill -HUP $(pidof myapp)
```

## Cron integration

Add a line to `/etc/crontab` or a user crontab. Example: rotate nightly at 3 AM.

```
0 3 * * *  /usr/local/bin/log-rotator /var/log/myapp.log --max-size 50M \
           --keep-count 14 --keep-days 30 --compress \
           --report /var/log/rotation-report.txt
```

Or as a systemd timer:

```ini
# /etc/systemd/system/log-rotator.service
[Unit]
Description=Rotate logs for myapp

[Service]
Type=oneshot
ExecStart=/usr/local/bin/log-rotator /var/log/myapp.log \
  --max-size 50M --keep-count 14 --keep-days 30 --compress

# /etc/systemd/system/log-rotator.timer
[Unit]
Description=Rotate logs for myapp nightly

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable with:

```bash
sudo systemctl enable --now log-rotator.timer
```

## Exit codes

The tool uses stable exit codes so cron or monitoring can detect problems.

- 0: Everything rotated cleanly, or nothing to do.
- 1: One or more files could not be processed.
- 2: Configuration or usage error.
- 3: Unexpected crash. Please report it as a bug.

## Permissions

The script runs with the permissions of the user that launches it. To rotate
logs owned by another user (such as system logs under `/var/log`), run it as
root or with `sudo`. The script never changes ownership of the files it creates.
The fresh empty log file is created with the default umask of the caller, so if
your service requires a specific owner or mode, set it after rotation:

```bash
log-rotator /var/log/myapp.log --keep-count 7 -z
chown myapp:myapp /var/log/myapp.log
```

## Limitations
- Only plain-text files on a local filesystem. No rotation for stdout or pipes.
- Compressed files are not re-compressed if they already end in `.gz`.
- No built-in signal handling for log daemons. Reload your service yourself.
- No timestamp in the rotation suffix. Rotations are numbered, not dated.

## License

AGPL-3.0. See the [root license](../../LICENSE).
