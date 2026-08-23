# systemd-timer-monitor

Audit systemd services and timers, report failures and mis-scheduled timers as a clean HTML status page. Zero dependencies, standard library only.

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Dependencies](https://img.shields.io/badge/dependencies-zero-success)

`systemd_audit.py` inspects the local systemd journal and produces a single self-contained HTML file you can serve from any static web server or open in a browser. It also exports machine-readable JSON for monitoring pipelines and alerting.

## What it checks

- **Failed services.** Lists every currently failed service unit with its active/sub state and description.
- **Stuck timers.** Lists all timer units, when each will next run, and when each last fired. Timers that are marked active but have never triggered get an `overdue` flag.
- **Overall health.** A summary card at the top shows failed unit count, active timer count, and total timers, with a healthy/issues badge.

## Why

A surprising number of homelab outages come from a silently failed service or a timer that stopped firing weeks ago. This tool gives you a one-page glance at the health of your systemd-managed services so you spot problems before they cascade.

## Requirements

- A Linux machine running systemd (Ubuntu, Debian, Fedora, Arch, and most modern distros ship it).
- Python 3.8 or newer.
- No third-party packages. Pure standard library.

You need permission to run `systemctl` (works as a regular user for most read operations; failed-unit counts are most reliable when run as root).

## Installation

Clone the repository:

```bash
git clone https://github.com/cappy-dev/systemd-timer-monitor.git
cd systemd-timer-monitor
chmod +x systemd_audit.py
```

Or just copy the single script somewhere on your path:

```bash
sudo cp systemd_audit.py /usr/local/bin/systemd-audit
sudo chmod +x /usr/local/bin/systemd-audit
```

## Usage

Run a one-off audit and write an HTML report:

```bash
./systemd_audit.py -o /var/www/html/systemd-report.html
```

Also export JSON for a monitoring pipeline:

```bash
./systemd_audit.py -o report.html --json report.json
```

Skip parts of the audit if you only want one piece:

```bash
./systemd_audit.py --no-timer -o failed-services.html
./systemd_audit.py --no-service -o timers.html
```

Run quietly from a cron job and let the exit code drive alerting (non-zero exit when issues are found):

```bash
./systemd_audit.py --quiet -o /var/www/html/systemd-report.html || notify-send "systemd audit found issues"
```

### Cron example

Run the audit every 15 minutes and serve the result:

```cron
*/15 * * * * /usr/local/bin/systemd-audit --quiet -o /var/www/html/systemd-report.html --json /var/www/html/systemd-report.json
```

Point Caddy, nginx, or Apache at that directory and you have a live status page for your homelab services.

## Command line options

```
-o, --output FILE      Write HTML report to FILE (default: systemd-report.html)
--json FILE            Also write machine readable JSON to FILE
--no-service           Skip failed-service audit
--no-timer             Skip timer audit
--no-health            Skip health summary
--timeout SEC          systemctl command timeout in seconds (default: 15)
--quiet                Print nothing to stdout
-v, --version          Show version and exit
-h, --help             Show help and exit
```

### Exit codes

```
0  Report generated, no issues found
1  Report generated but one or more failed units were detected, or a write error occurred
2  systemctl not found or unreadable
```

The non-zero exit on failure makes the script easy to wire into monitoring: if the audit returns non-zero, your cron wrapper can page you or trigger an alert.

## HTML report sections

The generated HTML is a single dark-themed page with three sections:

1. **Summary cards.** Failed unit count (red if non-zero), active timers, total timers, and an overall healthy or issues badge.
2. **Failed services table.** Every failed `.service` unit, its active/sub state, and the service description from systemd.
3. **Timers table.** Every timer unit, its active/sub state, the next fire time, the last fire time, and an `overdue` badge when a timer is active but has never triggered.

The report is fully self-contained (inline CSS, no external assets) so it works offline and behind firewalls.

## JSON output

When you pass `--json`, the script writes a JSON document alongside the HTML:

```json
{
  "generated_at": "2026-07-12 04:03:06",
  "hostname": "lab-1",
  "version": "1.0.0",
  "failed_services": [],
  "timers": [
    {
      "unit": "fstrim.timer",
      "active": "active",
      "sub": "waiting",
      "description": "Discard unused filesystem blocks once a week",
      "next_elapse": "Mon 2026-07-13 00:31:03 UTC",
      "last_trigger": "Mon 2026-07-06 00:40:30 UTC",
      "overdue": false
    }
  ],
  "health": {
    "failed_units": 0,
    "active_timers": 8,
    "total_timers": 8,
    "healthy": true
  }
}
```

This is convenient for feeding into Prometheus exporters, Grafana dashboards, or a simple `jq` check in a shell script.

## Example workflow

Here is a full setup that runs the audit every 10 minutes and alerts you if anything fails:

```bash
# 1. Install the script
sudo cp systemd_audit.py /usr/local/bin/systemd-audit
sudo chmod +x /usr/local/bin/systemd-audit

# 2. Create a state directory for reports
sudo mkdir -p /var/www/systemd-reports
sudo chown $USER /var/www/systemd-reports

# 3. Add to your crontab
crontab -e
```

Add this line (adjust the notification command to your tool of choice):

```cron
*/10 * * * * /usr/local/bin/systemd-audit --quiet --no-legend \
  -o /var/www/systemd-reports/index.html \
  --json /var/www/systemd-reports/status.json \
  || /usr/local/bin/notify-failure "systemd audit found issues on $(hostname)"
```

Serve the directory statically and bookmark it. You now have a live view of every systemd service and timer on the box.

## Privacy

The audit runs entirely on the local machine. No data leaves the host. The HTML and JSON files contain unit names, descriptions, and timestamps only, never logs or environment variables. If you choose to expose the report over HTTP, put it behind authentication or restrict it to your LAN.

## Contributing

Contributions are welcome. Please keep the script dependency-free and make sure `python3 systemd_audit.py -o /tmp/r.html --json /tmp/r.json` still runs cleanly before opening a pull request.

## License

MIT. See [LICENSE](LICENSE).
