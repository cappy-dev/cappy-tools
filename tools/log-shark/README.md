# log-shark

Zero-dependency log analyzer for common log formats. Parses syslog, journalctl JSON, nginx access logs, and Apache access logs, then surfaces a quick summary: top client IPs, HTTP status code breakdowns, error spikes, busiest endpoints, and more.

Designed for quick SSH checks and cron jobs on homelab servers. No pip install, no virtualenv, no Docker. Just Python 3.8+ standard library.

## Features

- Parses four common log formats automatically (auto-detection per line)
- **nginx combined** access logs (with referrer and user agent)
- **Apache common** access logs
- **syslog** traditional format (`Jul 10 13:05:42 host proc[pid]: message`)
- **journalctl JSON** output (`journalctl --output=json | python3 log_shark.py`)
- Top client IPs, busiest endpoints, HTTP status code breakdown
- Error spike detection (clusters of 4xx/5xx or error keywords per time window)
- ASCII bar charts (no gnuplot or matplotlib needed)
- JSON output mode for piping into other tools
- `--quiet` mode for cron: prints nothing unless errors or spikes are found, exits non-zero on errors
- 100% standard library, zero external dependencies

## Installation

No install needed. Just download the script and make it executable:

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/log-shark/main/log_shark.py
chmod +x log_shark.py
```

Or clone it:

```bash
git clone https://github.com/cappy-dev/log-shark.git
cd log-shark
```

## Usage

Analyze a single log file:

```bash
python3 log_shark.py /var/log/nginx/access.log
```

Pipe journalctl output directly:

```bash
journalctl --output=json -u sshd | python3 log_shark.py
```

Analyze multiple files at once:

```bash
python3 log_shark.py /var/log/nginx/access.log /var/log/apache2/access.log
```

Scan an entire directory recursively:

```bash
python3 log_shark.py /var/log/nginx/
```

Read from stdin (pipe any command output):

```bash
cat /var/log/syslog | python3 log_shark.py
```

## Options

```
positional arguments:
  files            Log file(s) or a directory to scan. Reads stdin if omitted.

optional arguments:
  -n, --top N      Number of top entries to show per category (default: 10)
  -w, --window N   Window size in minutes for error spike detection (default: 5)
  --json           Output JSON instead of the text report
  --quiet          Print nothing unless errors or spikes are found
                   Exit non-zero if errors are detected (cron-friendly)
```

## Output Example

```
+==========================================================+
|                    log-shark report                      |
+==========================================================+

  Total lines:  5230
  Parsed:       5230 (100%)
  Unparsed:     0
  Errors/4xx+:  187

  HTTP status / error breakdown
  -----------------------------
  200                                              4512     ##############################
  404                                               134     -
  500                                                38     -
  301                                                28     -
  403                                                17     -
  ...+others                                         10

  Top client IPs (all traffic)
  ------------------------------
  192.168.1.5                                       1850     ##############################
  10.0.0.42                                          902     ###############
  10.0.0.8                                           430     #######
  ...+others                                        2048

  Error spikes (>= 3 per window)
  ------------------------------
  2026-07-10 13:05     8 errors
  2026-07-10 14:20     5 errors
```

## Cron Integration

Use `--quiet` to stay silent when everything looks fine and get alerted only when errors or spikes are found. The exit code is non-zero when errors are detected, making it easy to wire into monitoring:

```bash
# crontab example: run every 10 minutes
*/10 * * * * /home/user/log-shark/log_shark.py --quiet /var/log/nginx/access.log && echo "OK" || curl -d "alert=nginx-errors" https://hooks.example.com/n8n
```

Another pattern: capture the JSON output for a dashboard or webhook:

```bash
python3 log_shark.py --json /var/log/nginx/ > /var/www/status/log-stats.json
```

## JSON Output

Use `--json` to get machine-readable output suitable for piping into `jq`, a webhook, or another script:

```bash
python3 log_shark.py --json /var/log/nginx/access.log | jq '.top_ips'
```

Sample JSON payload:

```json
{
  "total": 5230,
  "parsed": 5230,
  "unparsed": 0,
  "error_count": 187,
  "status_codes": { "200": 4512, "404": 134, "500": 38 },
  "top_ips": { "192.168.1.5": 1850, "10.0.0.42": 902 },
  "top_paths": { "/index.html": 1200, "/api/data": 800 },
  "methods": { "GET": 4000, "POST": 1000 },
  "spikes": [
    { "time": "2026-07-10T13:05:00", "count": 8 }
  ]
}
```

## Supported Log Formats

| Format | Example Line |
|--------|-------------|
| nginx combined | `192.168.1.5 - - [10/Jul/2026:13:05:42 +0000] "GET / HTTP/1.1" 200 612 "https://ref" "Mozilla/5.0"` |
| Apache common | `192.168.1.5 - - [10/Jul/2026:13:05:42 +0000] "GET / HTTP/1.1" 200 612` |
| syslog | `Jul 10 13:05:42 myhost sshd[1234]: Accepted publickey for root from 10.0.0.5` |
| journalctl JSON | `{"MESSAGE":"...", "__REALTIME_TIMESTAMP":"...", "SYSLOG_IDENTIFIER":"sshd"}` |

Lines that do not match any known format are counted as unparsed but still appear in the summary total.

## Requirements

- Python 3.8 or newer
- No external packages required

## License

MIT
