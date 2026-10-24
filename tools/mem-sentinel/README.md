# mem-sentinel

Reads Linux memory and swap usage from /proc/meminfo and reports configurable threshold breaches.

## What it does

- Reads `/proc/meminfo` and computes used/available/free/buffers/cached for RAM
- Reads swap total, used, and free
- Classifies each metric as OK, WARNING, CRITICAL, or SKIPPED (when there is no swap)
- Exits 0 when everything is fine, 1 when a warning or critical threshold is breached
- Optional outputs: plain text (default), single-line JSON, or a dark-mode HTML status page
- Optional alerts: POST JSON to a webhook URL, or send an email via SMTP, but only when something is wrong
- Designed for cron: silent on success, noisy on failure, clean exit codes

## Requirements
- Linux (reads `/proc/meminfo` and `/proc/loadavg`)
- Python 3.8 or newer
- That is it. No third-party packages.

## Install

Drop the file somewhere on your PATH and make it executable:

```bash
chmod +x mem_sentinel.py
sudo cp mem_sentinel.py /usr/local/bin/mem-sentinel
```

Or just run it in place:

```bash
python3 mem_sentinel.py
```

No build step. No package manager. No compiled extensions.

## Usage
### One-shot check

```bash
python3 mem_sentinel.py
```

Prints nothing and exits 0 when memory and swap are under the default thresholds (80% RAM warn, 92% RAM crit, 70% swap warn, 90% swap crit). Prints a text report and exits 1 when any threshold is breached.

### Custom thresholds

```bash
python3 mem_sentinel.py --mem-warn 75 --mem-crit 90 --swap-warn 60 --swap-crit 85
```

### JSON output for log shippers

```bash
python3 mem_sentinel.py --json --always
```

With `--always`, a healthy run still prints one JSON line. Great for feeding into `jq`, `jq` pipelines, or a log shipper that expects a status line on every cron tick:

```bash
python3 mem_sentinel.py --json --always | jq .
```

### HTML status page

```bash
python3 mem_sentinel.py --html /var/www/mem.html --always
```

Writes a standalone dark-mode HTML file you can serve with nginx, Caddy, or just open in a browser. Use `--always` so the page exists even on healthy runs. Without `--always`, the page is only (re)written when a threshold is breached.

### Webhook alert

```bash
python3 mem_sentinel.py --webhook https://hooks.example.com/alerts
```

When a threshold is breached, mem-sentinel POSTs the JSON payload to the URL. The request uses the standard library `urllib`, so no `requests` dependency. The request is a best-effort fire-and-forget: a failed webhook never masks the exit code.

### Email alert

```bash
python3 mem_sentinel.py \
  --email-to ops@example.com \
  --email-from sentinel@example.com \
  --smtp-host mail.example.com \
  --smtp-port 587
```

Sends a plain-text alert email when a threshold is breached. Uses `smtplib` from the standard library.

## Cron examples

### Every 5 minutes, write an HTML page

```cron
*/5 * * * * /usr/local/bin/mem-sentinel --html /var/www/mem.html --always
```

The `--always` flag means the page is rewritten on every run, healthy or not, so the served page never goes stale. On a breach, the exit code is 1, so a wrapper like [cron-pulse](https://github.com/cappy-dev/cappy-tools/tree/main/tools/cron-pulse) can route it.

### Every 10 minutes, alert on a breach

```cron
*/10 * * * * /usr/local/bin/mem-sentinel --webhook https://hooks.example.com/alerts
```

Stays completely silent and exits 0 on healthy runs. Posts a JSON payload only when memory or swap crosses a threshold.

### Every minute, ship a JSON status line to a log

```cron
* * * * * /usr/local/bin/mem-sentinel --json --always >> /var/log/mem-sentinel.jsonl
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | all good (or `--always` printed a healthy line) |
| 1 | a warning or critical threshold was breached |
| 2 | could not read memory info (unsupported platform or bad path) |
| 3 | bad command line arguments |

## How it computes RAM usage

mem-sentinel follows the modern Linux convention:

```
used = MemTotal - MemAvailable
```

`MemAvailable` is the kernel's best estimate of how much memory is usable without swapping, accounting for reclaimable cache and buffers. It is more honest than `MemFree`. On older kernels without `MemAvailable`, the script falls back to:

```
used = MemTotal - MemFree - Buffers - Cached
```

Swap is straightforward: `SwapTotal - SwapFree`.

## Flags reference

```
--mem-warn PCT          warn when RAM usage >= PCT        (default 80)
--mem-crit PCT          critical when RAM usage >= PCT   (default 92)
--swap-warn PCT         warn when swap usage >= PCT      (default 70)
--swap-crit PCT         critical when swap usage >= PCT  (default 90)
--meminfo PATH          path to a meminfo file           (default /proc/meminfo)
--json                  emit a single JSON line
--html PATH             write an HTML status page to PATH
--always                print output even when OK
--webhook URL           POST JSON to URL on WARNING or CRITICAL
--webhook-timeout SEC   webhook request timeout         (default 10)
--email-to ADDR         alert email recipient
--email-from ADDR       alert email sender
--smtp-host HOST        SMTP host                        (default localhost)
--smtp-port PORT        SMTP port                        (default 25)
--quiet                 suppress stdout on a breach
--version               print version and exit
```

`--json` and `--html` are mutually exclusive. Everything else composes freely.

## Add to PATH

If you want `mem-sentinel` (no `.py`) on your PATH:

```bash
chmod +x mem_sentinel.py
sudo cp mem_sentinel.py /usr/local/bin/mem-sentinel
```

The shebang (`#!/usr/bin/env python3`) means you can run it directly.

## What a breach looks like

Default text output when RAM hits a warning threshold:

```
mem-sentinel 1.0.0 on towerbox at 2026-08-07T04:15:00+00:00

Memory
  total      31.4 GiB
  used       26.1 GiB   83.2
  available  5.3 GiB   16.8
  buffers    256.0 KiB
  cached     4.1 GiB

Swap
  total      8.0 GiB
  used       1.2 GiB   15.0

[OK       ] mem   mem usage 83.2% >= warning 80.0%
[OK       ] swap  swap usage 15.0% under warning 70.0%

Overall: WARNING
```

JSON output is a single line, ideal for piping into `jq`:

```json
{"tool":"mem-sentinel","version":"1.0.0","host":"towerbox","timestamp_utc":"2026-08-07T04:15:00+00:00","mem_used_pct":83.2,"mem_available_pct":16.8,"swap_used_pct":15.0,"state":"warning","states":[{"metric":"mem","state":"warning","pct":83.2,"threshold":80.0,"message":"mem usage 83.2% >= warning 80.0%"},{"metric":"swap","state":"ok","pct":15.0,"threshold":null,"message":"swap usage 15.0% under warning 70.0%"}]}
```

## License

AGPL-3.0. See the [root license](../../LICENSE).
