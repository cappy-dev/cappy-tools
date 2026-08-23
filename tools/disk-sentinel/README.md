# disk-sentinel

A tiny zero-dependency Python script that monitors disk health on Linux. It checks disk space usage, S.M.A.R.T. self-assessment results, disk temperature, and known-bad S.M.A.R.T. attributes, then prints a clean report and exits with a machine-friendly status code.

Perfect for cron, monitoring hooks, or a quick sanity check from the terminal.

## What it does

- Lists every real block device on the system (skips loop, ram, md, dm, and virtual mappings)
- Reads disk model, serial, size, and rotational flag from `/sys/block` (no root needed)
- Reads mounted filesystem usage with `df`
- Runs `smartctl` to collect S.M.A.R.T. attributes, overall health, and temperature
- Flags critical S.M.A.R.T. attributes (reallocated sectors, pending sectors, UDMA CRC errors, and more)
- Prints a color text report or machine-readable JSON
- Exits 0 on OK, 1 on warning, 2 on critical, 3 on error, so you can wire it into cron or CI

No pip packages required. Uses only the Python standard library.

## Requirements

- Python 3.7 or newer
- `smartmontools` (provides `smartctl`) for S.M.A.R.T. data. The script still runs without it, but skips S.M.A.R.T. checks and warns you.
- Reading S.M.A.R.T. data usually needs root, or membership in the `disk` group. Disk usage and `/sys` info work as a normal user.

Install smartmontools on Debian or Ubuntu:

```bash
sudo apt install smartmontools
```

On Fedora or RHEL:

```bash
sudo dnf install smartmontools
```

On Arch:

```bash
sudo pacman -S smartmontools
```

On Alpine:

```bash
sudo apk add smartmontools
```

## Installation

```bash
git clone https://github.com/cappy-dev/disk-sentinel.git
cd disk-sentinel
chmod +x disk_sentinel.py
```

No build step. No Python virtual environment needed. The script is a single file.

## Usage

Check every disk on the system, with text output and colors:

```bash
sudo ./disk_sentinel.py
```

Example output:

```
[ OK ] sda  Samsung SSD 850 EVO / SSD / 250.1 GiB
         usage:  42.3% (98.0 GiB used of 232.0 GiB) mount: /
         temp:   33 C
         SMART:  PASS

[CRIT] sdb  WDC WD20EARS / HDD / 1.8 TiB
         usage:  91.7% (1.7 GiB used of 1.8 GiB) mount: /mnt/backup
         temp:   61 C
         SMART:  PASS
         flags:  disk usage 91.7% >= 90.0%, temperature 61 C >= 60 C,
                 Current_Pending_Sector (id 197) non-zero: 12
```

JSON output, great for piping into `jq` or a monitoring agent:

```bash
sudo ./disk_sentinel.py --json | jq '.[] | select(.status=="crit")'
```

Check a single device, ignoring colors (handy for logs):

```bash
sudo ./disk_sentinel.py --device sda --no-color
```

Tighten the thresholds. Warn at 70 percent, critical at 90 percent, warn temperature at 45 C:

```bash
sudo ./disk_sentinel.py --usage-warn 70 --usage-crit 90 --temp-warn 45
```

Quiet mode for cron. It prints nothing and only the exit code matters:

```bash
sudo ./disk_sentinel.py --quiet
echo "exit status: $?"
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0    | All disks OK |
| 1    | At least one disk in warning state |
| 2    | At least one disk in critical state |
| 3    | Tool error (no devices found, subcommand failed) |

## Cron example

Run a check every morning at 6 AM and email yourself only on a problem:

```bash
# crontab -e
0 6 * * * /path/to/disk-sentinel/disk_sentinel.py --quiet || /path/to/disk-sentinel/disk_sentinel.py --no-color | mail -s "disk-sentinel alert on $(hostname)" you@example.com
```

## What S.M.A.R.T. attributes are flagged

The script watches the following attribute IDs and warns when their raw value is non-zero. Some of these are bumped to critical when they signal data loss risk.

- 5    Reallocated Sector Count
- 187  Reported Uncorrectable Errors
- 188  Command Timeout
- 197  Current Pending Sector  (critical when non-zero)
- 198  Offline Uncorrectable   (critical when non-zero)
- 199  UDMA CRC Error Count
- 231  SSD Life Left (vendor specific, inverted: low is bad)
- 232  Available Reserved Space (Intel SSDs, inverted)
- 233  Media Wearout Indicator (Intel SSDs, inverted)

Reallocated sectors (ID 5) are counted as critical when non-zero. Pending sectors and offline uncorrectable (197, 198) likewise escalate to critical because they predict imminent failure.

## Options

```
--device DEVICE        Inspect only this device (sda, nvme0n1, ...). Repeatable.
--usage-warn PCT       Warn at this usage percent (default 85)
--usage-crit PCT       Critical at this usage percent (default 95)
--temp-warn C          Warn at this temperature in Celsius (default 55)
--temp-crit C          Critical at this temperature in Celsius (default 65)
--json                 Emit JSON instead of text
--no-color             Disable ANSI color
--quiet                Print nothing, exit code only
```

## License

MIT. See `LICENSE`.
