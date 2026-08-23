# restart-hawk

A tiny, zero-dependency tool that watches long-running processes and tells you when they unexpectedly restart or silently disappear.

Restart Hawk is built for cron. You point it at a list of process names (or a config file), and on every run it records the current PID of each process. If the PID changes, it announces a restart. If the process is gone, it announces a disappearance. Exit codes are machine-friendly so you can wire it into monitoring or shell scripts.

Pure Python 3.8+ standard library. No pip. No venv. No virtualenv. No Docker. Just one file.

## Why

Long-running services usually start back up on their own after a crash. From the outside, everything looks fine, but the process restarted in the middle of the night and you never knew. Restart Hawk closes that gap. Run it from cron every few minutes and you get a permanent, logged record of every restart and silent exit.

## Features

- Watches any process by its short command name (what you see in `/proc/<pid>/comm`).
- Detects PID changes (restarts) and missing processes (silent exits).
- Logs every restart and disappearance to a timestamped events log.
- Cron-friendly exit codes: 0 healthy, 1 warning (restart), 2 critical (vanished or missing).
- Optional alert command, run with the event message appended, for wiring into email, webhooks, or chat.
- Text and self-contained HTML status reports.
- INI config file for keeping many watches in one place.
- Zero dependencies. Runs anywhere Python 3.8 or newer is installed.

## Installation

Copy the script onto your machine and make it executable.

```bash
curl -fsSL -o restart_hawk.py https://raw.githubusercontent.com/cappy-dev/restart-hawk/main/restart_hawk.py
chmod +x restart_hawk.py
./restart_hawk.py --version
```

Or clone it:

```bash
git clone https://github.com/cappy-dev/restart-hawk.git
cd restart-hawk
./restart_hawk.py --version
```

No install step. No package manager. The entire tool is one Python file.

## Usage

All commands support `--state-dir` to override where state is stored (the default is `~/.restart-hawk`).

### Watch processes from the command line

```bash
./restart_hawk.py watch nginx postgres redis
```

Each name is matched against the short command name of every running process. Pass `name=pattern` to give a watch a friendly label that differs from the process name:

```bash
./restart_hawk.py watch web=nginx db=postgres
```

### Watch processes from a config file

Create `hawks.ini`:

```ini
[hawk]
state_dir = ~/.restart-hawk
alert_command = mail -s "Restart Hawk Alert" ops@example.com

[watches]
web = nginx
db = postgres
cache = redis
queue = celery
```

Then run:

```bash
./restart_hawk.py watch --config hawks.ini
```

### Print the last known state

```bash
./restart_hawk.py status
```

### Emit a status report

Plain text (the default):

```bash
./restart_hawk.py report
```

A self-contained HTML page you can serve or email:

```bash
./restart_hawk.py report html > /var/www/html/restart-hawk.html
```

### Clear all stored state

```bash
./restart_hawk.py clear
```

## Exit codes

Restart Hawk is designed for cron and shell scripting. The exit code is the worst case across all watched processes.

| Code | Meaning                                                    |
|------|------------------------------------------------------------|
| 0    | Healthy. Every watched process is alive and PID is stable.|
| 1    | Warning. At least one process restarted since last run.    |
| 2    | Critical. At least one process vanished or was never found.|

A cron entry could look like this:

```cron
*/5 * * * * /home/ops/restart_hawk.py watch --config /home/ops/hawks.ini || echo "hawk problem" | mail -s "Restart Hawk" ops@example.com
```

## Examples

First run records the current PID:

```text
$ ./restart_hawk.py watch nginx postgres
OK nginx (nginx): first sighting, PID 845
OK postgres (postgres): first sighting, PID 912
```

Second run finds the same PIDs:

```text
$ ./restart_hawk.py watch nginx postgres
OK nginx (nginx): running PID 845
OK postgres (postgres): running PID 912
```

After postgres is restarted:

```text
$ ./restart_hawk.py watch nginx postgres
OK nginx (nginx): running PID 845
WARNING postgres (postgres): restarted, now PID 1043 (was 912)
```

After nginx is stopped:

```text
$ ./restart_hawk.py watch nginx postgres
CRITICAL nginx (nginx): process vanished (was PID 845)
OK postgres (postgres): running PID 1043
```

The status report:

```text
$ ./restart_hawk.py report
Name                 Pattern              Status   PIDs
----------------------------------------------------------------------
nginx                nginx                DOWN     -
postgres             postgres             UP       1043
```

## State and logs

By default, Restart Hawk stores its state in `~/.restart-hawk/`:

- `state.json` holds the last known PID and timestamp for each watch.
- `events.log` is a timestamped append-only record of every restart and disappearance.

Point `--state-dir` or the `state_dir` config key somewhere else if you do not want state under your home directory.

## Alerting

Set the `alert_command` option in the `[hawk]` section of the config file. The command is run through `bash -c` with the event message appended as a single quoted argument. Some examples:

```ini
alert_command = mail -s "Restart Hawk Alert" you@example.com
alert_command = curl -s -X POST -d @- https://hooks.example.com/hawk
alert_command = /usr/local/bin/hawk-webhook.sh
```

Alert commands run only for WARNING and CRITICAL events, not routine OK messages. Failures inside the alert command are swallowed so they never break a watch.

## How process matching works

Restart Hawk reads `/proc/<pid>/comm`, which the Linux kernel truncates to 15 characters. Match patterns are compared case-insensitively against this short name. If a pattern is longer than 15 characters, it is truncated for the comparison, because the kernel truncates it too.

If `/proc` is not readable, Restart Hawk falls back to calling `pgrep -x`. A pattern that does not match a real process name will simply report CRITICAL, which lets you catch configuration typos and silently killed services.

## Requirements

- Python 3.8 or newer
- A Linux system with a readable `/proc` filesystem, or `pgrep` available as a fallback

## License

MIT. See `LICENSE`.
