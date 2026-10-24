# homelab-ports

Scans selected TCP ports and labels common self-hosted services. Scan only systems you own or have permission to test.

## Features
- Zero third-party dependencies. Runs on any machine with Python 3.9+.
- Sensible curated default port list for common self-hosted services.
- Accepts a CIDR range, a single IP, or a hostname.
- CSV output for piping into reports or spreadsheets.
- Optional `--top` preset for a fast scan of the most common ports.
- Configurable concurrency and per-probe timeout.
- Clean ANSI colored output (and a `--no-color` fallback).

## Installation
No install step is required. Just download the script and run it.

```sh
curl -O https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/homelab-ports/homelab_ports.py
chmod +x homelab_ports.py
```

Optionally place it somewhere on your `PATH`:

```sh
sudo install homelab_ports.py /usr/local/bin/homelab-ports
homelab-ports --help
```

## Requirements
- Python 3.9 or newer.
- No pip packages needed. Uses only the standard library.

## Usage
```sh
# Scan your whole home subnet (192.168.1.0/24) with the default port list.
python3 homelab_ports.py 192.168.1.0/24

# Scan a single host with a tighter per-probe timeout.
python3 homelab_ports.py 10.0.0.5 --timeout 1

# Scan a hostname. Forward DNS resolution happens automatically.
python3 homelab_ports.py nas.lan

# Use the built-in top preset (~25 most common ports) for a faster pass.
python3 homelab_ports.py 192.168.1.0/24 --top

# Provide your own port list, including ranges.
python3 homelab_ports.py 192.168.1.0/24 --ports 80,443,8080,8123-8125

# Emit CSV for reports or spreadsheets.
python3 homelab_ports.py 192.168.1.0/24 --csv > scan.csv

# Show a single-host full table, including closed ports.
python3 homelab_ports.py 10.0.0.5 --all

# Quiet summary mode, great for cron or a monitoring hook.
python3 homelab_ports.py 192.168.1.0/24 --summary-only
```

## Example output

```text
========================================================================
 homelab-ports scan 
========================================================================
Target(s) : 192.168.1.50, 192.168.1.51
Ports      : 55
Timeout    : 0.80s
Workers    : 96
========================================================================
192.168.1.50:22    OPEN  (SSH)
192.168.1.50:80    OPEN  (HTTP)
192.168.1.50:443   OPEN  (HTTPS)
192.168.1.50:8080  OPEN  (HTTP alt (Traefik / web apps))
192.168.1.51:8123  OPEN  (Home Assistant)
========================================================================
Done. 5 open port(s) across 2 host(s).
```

With `--no-color` the ANSI escapes are dropped, handy for piped logs.

## Tips

- Pair with your existing status page: run on a schedule and diff the open-port
  set against a known-good baseline. New open ports become an alert.
- Broaden a scan cautiously. Scanning a /16 is slow even at 96 workers because
  of host discovery overhead. Prefer the exact subnet that runs your services.
- A returned `closed` port only means no TCP listener answered. A firewall that
  drops (rather than rejects) packets will look identical to closed. Use a tool
  like `tcpdump` alongside if you need packet-level certainty.
- UDP services (DNS, syslog, QUIC) will not show up here. This tool only does
  TCP connect probes, which is intentional to keep it simple and dependency free.

## Exit codes

- `0` if the scan completed, regardless of how many ports were open.
- A nonzero code (with a message) if the target could not be resolved or parsed.

## License

AGPL-3.0. See the [root license](../../LICENSE).
