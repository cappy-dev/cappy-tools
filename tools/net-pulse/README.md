# net-pulse

Zero-dependency internet connectivity monitor for homelabs and self-hosters.

net-pulse probes latency, DNS resolution, HTTP reachability, and download
throughput against configurable targets, then exits with a clean status code
so cron and monitoring stacks can pick it up. It writes an attractive HTML
status page on demand and works in minimal containers without iputils-ping
installed (it falls back to a TCP handshake latency probe automatically).

## Features

- Pure Python standard library, no pip installs required
- Four probe types: `ping`, `dns`, `http`, `speed`
- ICMP ping with automatic TCP fallback when `ping` is missing
- Configurable warning thresholds for latency and throughput
- HTML status page with a clean dark theme
- JSON or plain text output
- Cron-friendly exit codes (0 healthy, 1 unhealthy, 2 config error)
- Optional TOML config file for custom target lists

## Installation

net-pulse is a single file. Drop it somewhere on your PATH or run it in place.

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/net-pulse/main/net_pulse.py
chmod +x net_pulse.py
```

Requires Python 3.8 or newer and the OS ping command for ICMP probes (optional;
the tool falls back to TCP when ping is unavailable).

## Usage

Run the default probes against Cloudflare, Google, and GitHub:

```bash
python3 net_pulse.py
```

Write an HTML status page and keep stdout quiet for cron:

```bash
python3 net_pulse.py --quiet --report /var/www/html/net-pulse.html
```

Get JSON output for piping into another tool:

```bash
python3 net_pulse.py --json
```

Tighten the warning thresholds so sluggish links show up:

```bash
python3 net_pulse.py --latency-warn 150 --speed-warn 10
```

### Command line options

- `--config PATH` - load targets and settings from a TOML config file
- `--json` - print JSON instead of plain text
- `--report PATH` - also write an HTML status page to this path
- `--latency-warn MS` - ping latency warning threshold in ms (default 250)
- `--speed-warn MBPS` - download throughput warning threshold in Mbps (default 1)
- `--quiet` - suppress stdout output (still writes --report if set)
- `--version` - print version and exit

### Config file format

Create a `net-pulse.toml` to define your own targets and thresholds. Only a
small TOML subset is supported (key=value lines and `[[targets]]` tables).

```toml
latency_warn_ms = 200
speed_warn_mbps = 5.0
ping_timeout_sec = 1
dns_timeout_sec = 3
http_timeout_sec = 8

[[targets]]
host = "1.1.1.1"
label = "Cloudflare Anycast"
type = "ping"

[[targets]]
host = "github.com"
label = "GitHub DNS"
type = "dns"
resolve = "github.com"

[[targets]]
url = "https://www.example.com"
label = "Example site"
type = "http"

[[targets]]
url = "https://speed.cloudflare.com/__down?bytes=5000000"
label = "5MB Cloudflare throughput"
type = "speed"
```

Then run:

```bash
python3 net_pulse.py --config net-pulse.toml
```

## Probe types

- `ping` - latency to a host via ICMP, with automatic TCP fallback (port 53
  for IP literals, port 80 for hostnames). Reports average and jitter over
  four samples.
- `dns` - resolves a hostname via the system resolver. Reports the first
  returned address.
- `http` - opens a URL with urllib and reports the HTTP status code and
  round trip time.
- `speed` - downloads a URL fully and reports throughput in Mbps. Pair with
  an endpoint like `https://speed.cloudflare.com/__down?bytes=N` that serves
  a known payload.

## Exit codes

- 0 - all targets healthy
- 1 - at least one target unhealthy (probe failed or threshold crossed)
- 2 - usage or config error

## Cron example

Check connectivity every 15 minutes and refresh an HTML page:

```cron
*/15 * * * * /usr/local/bin/net_pulse.py --quiet \
  --report /var/www/html/net-pulse.html \
  --config /etc/net-pulse.toml
```

Because net-pulse exits non-zero when something is unhealthy, you can also
chain alerts:

```bash
python3 net_pulse.py --quiet --config /etc/net-pulse.toml \
  || curl -s -X POST -d "net-pulse unhealthy" https://example.com/webhook
```

## Default targets

With no config file, net-pulse probes six targets:

1. Cloudflare DNS (1.1.1.1) via ping
2. Google DNS (8.8.8.8) via ping
3. Google homepage via http
4. Cloudflare homepage via http
5. GitHub.com via dns
6. Cloudflare 1MB endpoint via speed

## Output example

```
net-pulse run at 2026-07-18T04:04:21+00:00
All 6 targets healthy (avg latency 86 ms).

  [OK  ] Cloudflare DNS (ping) - 12 ms - avg 12 ms, jitter 1 ms (tcp, n=4)
  [OK  ] Google DNS (ping) - 2 ms - avg 2 ms, jitter 1 ms (tcp, n=4)
  [OK  ] Google homepage (http) - 74 ms - HTTP 200 in 74 ms
  [OK  ] Cloudflare (http) - 256 ms - HTTP 200 in 256 ms
  [OK  ] GitHub DNS (dns) - resolved to 140.82.114.3
  [OK  ] Cloudflare 1MB download (speed) - 34.14 Mbps - 1.00 MB in 0.23s = 34.14 Mbps

Overall: HEALTHY
```

## License

MIT. See LICENSE.
