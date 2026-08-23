# cron-watchdog

A zero-dependency Python tool to monitor whether scheduled cron jobs actually ran on time.

## Features

- Zero dependencies - pure Python standard library
- Tracks cron jobs by recording when they successfully run
- Checks if jobs ran within their expected window
- Alerts via command when jobs miss their expected run time
- Simple configuration via CLI flags or config files
- Cron-friendly exit codes (0 for success, 1 for stale jobs)

## Installation

### From source (recommended for development)

```bash
git clone https://github.com/cappy-dev/cron-watchdog.git
cd cron-watchdog
python3 -m pip install --user .
```

### From PyPI (when published)

```bash
python3 -m pip install cron-watchdog
```

## Usage

### Record a job run

Wrap your cron job with `cron_watchdog.py touch`:

```bash
# In your crontab:
*/5 * * * * /path/to/backup.sh && /path/to/cron_watchdog.py touch backup
```

This records that the `backup` job ran successfully at the current time.

### Check for stale jobs

Run the watchdog to check if all jobs ran on time:

```bash
# Check all jobs (default state dir)
/path/to/cron_watchdog.py check

# With custom state directory and alert command
/path/to/cron_watchdog.py check --state-dir /var/lib/my-watchdog --alert-cmd /usr/bin/notify-send "Cron Alert" "{job} missed"
```

Exit codes:
- `0`: All jobs ran on time (success)
- `1`: One or more jobs missed their expected run time
- `2`: Configuration or usage error
- `3`: I/O error (state file unreadable)

### List tracked jobs

```bash
/path/to/cron_watchdog.py list --state-dir /var/lib/my-watchdog
```

Shows each job, its status (OK or STALE), age in seconds, and expected window.

## Configuration

You can configure jobs in several ways:

1. **Command-line flags** (per invocation)
2. **Config file** (`~/.config/cron-watchdog.conf` or `/etc/cron-watchdog.conf`)
3. **Environment variable** `CRON_WATCHDOG_DIR` for state directory

### Config file format

The config file can be INI-style or JSON:

**INI-style example:**
```ini
[jobs "backup"]
expect_within = 3600
alert_cmd = /usr/bin/notify-send "cron-watchdog" "{job} missed"

[jobs "syncthing"]
expect_within = 120
```

**JSON example:**
```json
{
  "jobs": {
    "backup": {
      "expect_within": 3600,
      "alert_cmd": "/usr/bin/notify-send \"cron-watchdog\" \"{job} missed\""
    },
    "syncthing": {
      "expect_within": 120
    }
  }
}
```

## Development

The project is designed to be simple and self-contained. To run tests:

```bash
# Create a temporary state directory
export CRON_WATCHDOG_DIR=$(mktemp -d)
# Initialize state for a test job
python3 cron_watchdog.py touch test_job

# Run checks
python3 cron_watchdog.py check --state-dir "$CRON_WATCHDOG_DIR"
```

## License

MIT License - see the [LICENSE](LICENSE) file.