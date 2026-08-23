# cron-pulse

Zero-dependency cron job heartbeat monitor for Linux. A single Python script with no external packages required.

cron-pulse keeps a pulse on your scheduled tasks. Each job sends a short "I ran" heartbeat from inside its crontab entry. cron-pulse then checks that every job actually fired within its expected window. When a job goes silent, cron-pulse exits non-zero so a wrapper entry, systemd timer, or external scheduler can raise the alarm.

Nothing is sent anywhere on its own. You decide what happens when a job is late or dead.

## Features

- **Zero dependencies.** Runs on Python 3.8 or newer using only the standard library.
- **Two commands.** `record` from inside your crontab, `check` from a wrapper cron line or systemd timer.
- **Grace windows.** Each job declares its expected interval and an extra grace period before it counts as late.
- **Clear exit codes.** `0` all good, `2` one or more jobs are late or dead, `3` a job never checked in, `4` hard error.
- **Single JSON state file.** Easy to back up, easy to inspect, easy to wipe.
- **No network, no daemon.** Pure file based. Nothing to keep running.

## Install

Copy the script onto your machine and make it executable:

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/cron-pulse/main/cron-pulse.py
chmod +x cron-pulse.py
sudo mv cron-pulse.py /usr/local/bin/cron-pulse
```

Or just clone the repo:

```bash
git clone https://github.com/cappy-dev/cron-pulse.git
cd cron-pulse
chmod +x cron-pulse.py
```

No pip install, no virtual environment, no build step.

## Quick start

### 1. Register a heartbeat from each cron job

Add a `cron-pulse record` call at the end of each scheduled task. Give the job a unique name, its expected interval, and an optional grace period.

Edit your crontab:

```bash
crontab -e
```

Then wire up your jobs:

```cron
# Hourly database backup. Record a heartbeat every time it runs.
0 * * * *  /opt/scripts/backup.sh && cron-pulse record backup-db 1h

# Daily log rotation with a 5 minute grace window.
30 3 * * *  /usr/sbin/logrotate /etc/logrotate.conf && cron-pulse record rotate-logs 1d --grace 300

# Monthly cert renewal with a free-form note.
0 0 1 * *  certbot renew --quiet && cron-pulse record renew-cert 30d --note "letsencrypt"
```

The interval accepts `30s`, `5m`, `2h`, `1d`, or a bare number of seconds like `3600`.

### 2. Watch all jobs from one place

Add a single caretaker cron entry that runs `cron-pulse check` and reacts to its exit code:

```cron
# Every 10 minutes, verify every job checked in. Alert on failure.
*/10 * * * *  cron-pulse check || /opt/scripts/alert.sh "cron job missed its pulse"
```

`check` prints nothing when all jobs are healthy and prints a one-line summary when something is wrong. That makes it friendly for wrapping with mail, a webhook, or a paging tool.

## Commands

### `record NAME INTERVAL`

Register a heartbeat for the named job. Called from inside a crontab entry.

Options:

- `--grace SECONDS` extra seconds tolerated before the job is late. Default `120`.
- `--note TEXT` free-form note for your own use.
- `--state PATH` path to the state JSON file. Default `~/.cron-pulse/state.json`.

Examples:

```bash
cron-pulse record backup-db 1h
cron-pulse record weekly-sync 7d --grace 3600 --note "monday sync to NAS"
```

### `check`

Quiet health check. Prints nothing on success, prints a summary on failure. Exits non-zero when any job is late, dead, or never checked in. Perfect for wrapping in a caretaker cron line.

Options:

- `-v`, `--verbose` also print a success line.
- `--state PATH` path to the state file.

### `status`

Print a table of every registered job with its current status, age, interval, and last seen time. Useful for an eyeball glance at the board.

```bash
$ cron-pulse status
JOB          STATUS  AGE     INTERVAL  LAST SEEN
-----------  ------  ------  --------  -----------------------
backup-db    ok      12m     1h 0m     2026-07-15 02:35:51 UTC
rotate-logs  late    1d 6h   1d 0h     2026-07-13 21:30:00 UTC
renew-cert   ok      4d 0h   30d 0h    2026-07-11 00:00:00 UTC

[cron-pulse] WARNING: late/dead jobs: rotate-logs
```

### `list`

List every registered job and its configured interval and grace.

### `purge [NAME]`

Remove a single job by name, or wipe all jobs if no name is given.

```bash
cron-pulse purge backup-db     # remove one job
cron-pulse purge                # clear everything
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All jobs alive. |
| 2 | One or more jobs are late or dead. |
| 3 | One or more jobs have never checked in. |
| 4 | Hard error, such as a bad interval string or a disk error. |

## How "late" and "dead" are decided

Each heartbeat stores the interval you declared plus a grace period. The budget for a job is `interval + grace`.

- **ok**: time since last heartbeat is within the budget.
- **late**: time since last heartbeat is past the budget but under three times the budget.
- **dead**: time since last heartbeat is past three times the budget. The job has been silent long enough that something is very wrong.

A job that has never sent a heartbeat is reported once it appears in state with a zero last-seen time.

## Where state lives

By default, heartbeat data is written to `~/.cron-pulse/state.json`. Override the path with `--state` on any command. The file is human readable JSON and can be safely backed up, copied, or deleted to reset.

## Tips

- **Use absolute paths** in crontab entries. cron runs with a minimal PATH and will not find `cron-pulse` unless it is in `/usr/local/bin` or you give the full path.
- **Record on success.** Put `&&` between your real job and the `cron-pulse record` call so a heartbeat only registers when the job actually completed.
- **One caretaker line.** You only need one cron entry that runs `cron-pulse check`. It watches over all the individual job heartbeats.
- **Pipe to mail.** `cron-pulse check || mail -s "cron alert" admin@example` sends the failure summary to your inbox.
- **Lock with flock.** If your caretaker job might run slowly, wrap it in `flock -n /tmp/cron-pulse.lock` to avoid overlaps.

## License

MIT

## Contributing

Issues and pull requests are welcome at https://github.com/cappy-dev/cron-pulse.
