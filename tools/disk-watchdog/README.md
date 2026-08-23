# disk-watchdog

A tiny Python watchdog that monitors disk usage and SMART health on a Linux box. When a filesystem gets too full or a drive reports failing SMART attributes, it fires alerts through log files, webhooks, email, or a custom command. Designed to run from cron or systemd timers with zero external dependencies.

## What it does

- Reports disk usage for every mounted real filesystem against warning and critical thresholds
- Parses SMART attributes from `smartctl -A` and flags deteriorating drives (reallocated sectors, pending sectors, offline uncorrectable)
- Skips virtual and pseudo filesystems (/proc, /sys, tmpfs, and friends) automatically
- Alerts through four pluggable channels: log file, JSON webhook, email via SMTP, or a shell command
- Stays quiet when everything is fine, perfect for cron jobs that only produce output when something is wrong
- One script, no pip installs, just the Python standard library

## Requirements

- Python 3.7 or newer
- `smartctl` from the `smartmontools` package is optional but recommended for SMART checks. On Debian/Ubuntu: `apt install smartmontools`. On Fedora: `dnf install smartmontools`.
- Root or sudo is helpful for reading SMART data and writing to `/var/log/`

No pip packages needed. The script uses only standard library modules.

## Installation

```bash
git clone https://github.com/cappy-dev/disk-watchdog.git
cd disk-watchdog
chmod +x disk_watchdog.py
```

Optional: symlink it somewhere on your PATH:

```bash
sudo ln -s $(pwd)/disk_watchdog.py /usr/local/bin/disk-watchdog
```

Install `smartmontools` if you want SMART checks (highly recommended):

```bash
# Debian / Ubuntu
sudo apt install smartmontools

# Fedora / RHEL
sudo dnf install smartmontools

# Arch
sudo pacman -S smartmontools
```

## Quick start

Run a one-off check with the built-in defaults (usage warn at 85%, critical at 95%):

```bash
python3 disk_watchdog.py --verbose
```

Without `--verbose` it prints nothing when everything is OK, which is exactly what you want from a cron job.

Generate a starter config you can edit:

```bash
python3 disk_watchdog.py --print-default-config > /etc/disk-watchdog.json
sudo nano /etc/disk-watchdog.json
```

Or copy and edit the bundled example:

```bash
sudo cp config.example.json /etc/disk-watchdog.json
sudo nano /etc/disk-watchdog.json
```

## Usage

```bash
python3 disk_watchdog.py [OPTIONS]
```

Options:

- `-c, --config FILE`   Path to a JSON config file (default: /etc/disk-watchdog.json)
- `--smart-only`        Only run SMART checks, skip usage checks
- `--usage-only`        Only run usage checks, skip SMART
- `--device PATH`       Block device to SMART check. Repeat to check multiple devices
- `--json`              Print results as JSON instead of human readable text
- `--verbose`          Print all checks even when everything is OK
- `--print-default-config`  Dump the built-in default config as JSON and exit
- `--version`           Show version and exit

### Examples

Run everything with defaults:

```bash
python3 disk_watchdog.py
```

Usage check only, as JSON:

```bash
python3 disk_watchdog.py --usage-only --json
```

SMART check only on specific devices:

```bash
python3 disk_watchdog.py --smart-only --device /dev/sda --device /dev/nvme0n1
```

Verbose output showing every filesystem:

```bash
python3 disk_watchdog.py --verbose
```

Generate a config and tweak thresholds:

```bash
python3 disk_watchdog.py --print-default-config > /etc/disk-watchdog.json
```

## Configuration

The config is a single JSON file. Every field has a default, so you only need to set the values you want to change.

The bundled `--print-default-config` dumps the full schema. Key fields:

- `usage_warn` (int): Disk usage percentage that triggers a warning alert. Default 85.
- `usage_crit` (int): Disk usage percentage that triggers a critical alert. Default 95.
- `smart_check` (bool): Whether to run SMART checks. Default true.
- `smart_fail_values` (object): Map of SMART attribute names to their maximum acceptable raw values. Any value above the listed threshold triggers a critical alert. Default watches reallocated sectors, pending sectors, and offline uncorrectable.
- `ignore_fs_types` (array): Filesystem types to skip entirely. Defaults cover tmpfs, proc, sysfs, cgroup, overlay, and other virtual filesystems.
- `ignore_mount_prefixes` (array): Mount points starting with any of these prefixes are skipped. Default ignores Docker overlay mounts.
- `quiet_when_ok` (bool): When true, the script prints nothing if all checks pass. Set to false to always get output. Default true.
- `alerts` (object): Configuration for each alert channel. See below.

### Alert channels

Each channel is independent and enabled or disabled separately.

**log**: Append alerts to a text file.

```json
"alerts": {
  "log": {"enabled": true, "path": "/var/log/disk-watchdog.log"}
}
```

**webhook**: Send a POST (or GET) request with a JSON body containing the alert details. Useful for Slack, Discord, Mattermost, ntfy, or any webhook receiver.

```json
"alerts": {
  "webhook": {"enabled": true, "method": "POST", "url": "https://example.com/hook"}
}
```

**email**: Send an email via an SMTP relay (defaults to localhost:25, so `sendmail` or a local MTA works).

```json
"alerts": {
  "email": {"enabled": true, "to": "admin@example.com", "smtp_host": "localhost", "smtp_port": 25}
}
```

**command**: Run a shell command and pipe the alert text to its stdin. Great for calling your own notification script.

```json
"alerts": {
  "command": {"enabled": true, "exec": "/usr/local/bin/disk-watchdog-hook.sh"}
}
```

Full config example:

```json
{
  "usage_warn": 80,
  "usage_crit": 92,
  "smart_check": true,
  "quiet_when_ok": true,
  "alerts": {
    "log": {"enabled": true, "path": "/var/log/disk-watchdog.log"},
    "webhook": {"enabled": true, "method": "POST", "url": "https://example.com/hook"},
    "email": {"enabled": false, "to": "admin@example.com", "smtp_host": "localhost", "smtp_port": 25},
    "command": {"enabled": false, "exec": "/usr/local/bin/notify.sh"}
  }
}
```

## Automation

### Cron

Run every 10 minutes and let cron mail you any output (works because the script is quiet when all is well):

```cron
*/10 * * * * /usr/local/bin/disk-watchdog
```

### systemd timer

Create two files.

`/etc/systemd/system/disk-watchdog.service`:

```ini
[Unit]
Description=Disk usage and SMART health check

[Service]
Type=oneshot
ExecStart=/usr/local/bin/disk-watchdog
Nice=10
```

`/etc/systemd/system/disk-watchdog.timer`:

```ini
[Unit]
Description=Run disk-watchdog every 10 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
```

Enable and start:

```bash
sudo systemctl enable --now disk-watchdog.timer
```

### Exit codes

The script returns 0 when all checks pass and 2 when any check fails. This makes it easy to chain or test from other scripts:

```bash
disk-watchdog && echo "all good" || echo "check the logs"
```

## SMART attribute defaults

The default config watches three attributes that are common early-warning signs of drive failure:

- `Reallocated_Sector_Ct` - sectors the drive has remapped. Any nonzero value is concerning.
- `Current_Pending_Sector` - sectors awaiting remapping. Often precedes reallocation.
- `Offline_Uncorrectable` - sectors that failed an offline scan.

The threshold for all three defaults to 0, meaning any nonzero raw value triggers a critical alert. Add or change attributes in `smart_fail_values` to match your drive reporting and tolerance.

## Why another disk monitor?

Most disk monitoring tools are either heavyweight web dashboards (Uptime Kuma, Netdata) or tightly coupled to a specific alerting platform. Sometimes you just want a single script you can run from cron that checks your disks and pokes you if something is wrong. That is all this is. No agents, no databases, no containers, no dependencies.

## License

MIT
