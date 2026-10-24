# cron-doctor

Checks crontab files for syntax errors, missing commands, overlapping schedules, and disabled jobs.

## Features
- Validates all five schedule fields (minute, hour, day-of-month, month, day-of-week)
- Checks for out-of-bounds values, reversed ranges, and invalid names
- Detects commands that reference executables not on PATH
- Flags disabled or commented-out jobs
- Warns about unescaped percent signs (cron treats unescaped % as a newline)
- Detects schedule overlaps that may cause resource contention
- Supports standard 5-field user crontabs and 6-field system crontabs
- Supports special headers (@daily, @weekly, @reboot, etc.)
- JSON output for scripting and CI integration
- Zero dependencies beyond the Python standard library
- Python 3.8+

## Installation
No install needed. Just download the single file and run it:

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/cron-doctor/cron_doctor.py
chmod +x cron_doctor.py
```

Or clone the repo:

```bash
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/cron-doctor
```

## Quick start
Check your current user crontab:

```bash
crontab -l | python3 cron_doctor.py
```

Check a specific crontab file:

```bash
python3 cron_doctor.py --file mycron.txt
```

Check system cron files (/etc/crontab and everything in /etc/cron.d/):

```bash
python3 cron_doctor.py --system
```

Pipe any crontab text directly:

```bash
echo "0 2 * * * /bin/backup" | python3 cron_doctor.py
```

## Usage
```
usage: cron_doctor [-h] [-f FILE | --system] [--json] [--quiet] [--no-color] [--version]

Audit crontab files for common problems and mistakes.

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  Path to a crontab file to check
  --system              Check system cron files (/etc/crontab and /etc/cron.d/*)
  --json                Output results as JSON
  --quiet               Only show errors and warnings, not info
  --no-color            Disable color output
  --version             show program's version number and exit

Stdin is read automatically when the input is piped and no --file or --system
option is given.
```

## Examples
### Find a typo in a schedule field

```bash
$ echo "70 2 * * * /bin/backup" | python3 cron_doctor.py --no-color
[ERRORS] line 1: /bin/backup
  (X) minute: value 70 out of bounds (0-59)

Checked 1 entry: 1 error(s), 0 warning(s), 0 info
```

### Detect an unescaped percent sign

Cron interprets unescaped % characters as newlines. The command after the
first % becomes stdin. This is a very common source of silently broken jobs:

```bash
$ echo "0 1 * * * date +%Y-%m-%d > /tmp/today.txt" | python3 cron_doctor.py --no-color
[info] line 1: date +%Y-%m-%d > /tmp/today.txt
  (i) Command contains 3 unescaped '%' sign(s); cron treats unescaped % as a newline
```

Fix it by escaping each percent sign:

```bash
0 1 * * * date +\%Y-\%m-\%d > /tmp/today.txt
```

### JSON output for CI or scripting

```bash
$ echo "0 2 * * * /bin/backup" | python3 cron_doctor.py --json
{
  "entries": [
    {
      "line": 1,
      "command": "/bin/backup",
      "schedule": {
        "minute": "0",
        "hour": "2",
        "dom": "*",
        "month": "*",
        "dow": "*"
      },
      "findings": []
    }
  ]
}
```

### Quiet mode (only errors and warnings)

```bash
$ python3 cron_doctor.py --file mycron.txt --quiet
```

### Check system crontabs

```bash
$ sudo python3 cron_doctor.py --system
[OK] line 2: test -e /run/systemd/system || SERVICE_MODE=1 /usr/lib/...
[OK] line 3: test -e /run/systemd/system || SERVICE_MODE=1 /sbin/e2scrub_...

Checked 2 entries: 0 error(s), 0 warning(s), 0 info
```

## What cron_doctor checks

**Schedule field validation:**
- Values out of bounds (minute 70, hour 25, month 13, etc.)
- Ranges where start is after end (e.g. 10-5 in a non-wrapping field)
- Invalid step values (step of 0)
- Unrecognized month or day-of-week names
- Malformed tokens

**Command checks:**
- Executable not found on PATH (cron runs with a minimal PATH, often /usr/bin:/bin)
- Absolute or relative script paths that do not exist
- Empty commands

**Best-practice warnings:**
- Lines with fewer fields than expected
- Commented-out or intentionally disabled jobs
- Unescaped percent signs in commands
- Multiple jobs sharing an identical schedule (potential resource contention)
- No PATH variable defined in the crontab
- No MAILTO variable defined in the crontab
- Sunday specified as 7 instead of 0 (valid but uncommon)

**Unsupported (by design):**
- Vixie cron extensions beyond standard syntax
- Quartz-style cron expressions (seconds field, L, W, # modifiers)

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | No issues found (or only info with --quiet) |
| 1 | One or more entries have errors or warnings |
| 2 | Could not read input |

## Integration ideas

### Pre-commit hook

Save this as .git/hooks/check-crontab and make it executable:

```bash
#!/bin/sh
python3 /path/to/cron_doctor.py --file crontab.txt --quiet --no-color
```

### CI pipeline

```yaml
- name: Lint crontab
  run: python3 cron_doctor.py --file deploy/crontab.txt --json
```

A non-zero exit code from errors will fail the pipeline step.

## Limitations
cron_doctor performs static analysis. It does not execute commands, check
whether a job would actually fire at a given time, or validate shell syntax
inside the command string. It checks whether the base executable is on PATH
using Python's shutil.which, which reflects your current environment, not the
environment cron will use at runtime.

## License

AGPL-3.0. See the [root license](../../LICENSE).
