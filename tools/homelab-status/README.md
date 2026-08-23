# homelab-status

A tiny Python script that checks your self-hosted services are up and generates a clean HTML status page. No dependencies beyond the Python standard library.

## What it does

- Pings HTTP/HTTPS endpoints and checks the response status code
- Tests raw TCP ports (DNS, databases, game servers, etc.)
- Outputs a self-contained HTML file you can host anywhere
- Zero external dependencies, just Python 3.7+

## Installation

```bash
git clone https://github.com/cappy-dev/homelab-status.git
cd homelab-status
```

No pip install needed. The script uses only standard library modules.

## Usage

1. Copy the example config and edit it for your services:

```bash
cp services.json my-services.json
```

2. Edit `my-services.json` to list your homelab services (see Configuration below).

3. Run the check:

```bash
python3 homelab_status.py
```

This reads `services.json` from the same directory by default and writes `status.html` next to the script.

4. Open `status.html` in a browser to see your status dashboard.

### Custom config path

```bash
python3 homelab_status.py --config /path/to/my-services.json
```

Wait, that is not implemented yet. For now, just put `services.json` in the same folder as the script, or symlink it.

## Configuration

Create a `services.json` file in the same directory as the script:

```json
{
  "title": "My Homelab",
  "services": [
    {"name": "Nextcloud", "url": "https://cloud.example.com", "expect": 200},
    {"name": "Pi-hole DNS", "host": "192.168.1.10", "port": 53, "type": "tcp"},
    {"name": "Gitea", "url": "https://git.example.com", "expect": 200},
    {"name": "Minecraft", "host": "192.168.1.20", "port": 25565, "type": "tcp"},
    {"name": "Uptime Kuma", "url": "http://192.168.1.5:3001", "expect": 200}
  ]
}
```

### Service fields

- **name** (required): Display name for the service
- **url** (for HTTP checks): Full URL to check. The script makes a HEAD (HTTPS) or GET (HTTP) request and compares the status code against `expect`
- **expect** (optional, default 200): Expected HTTP status code
- **host** + **port** (for TCP checks): Hostname or IP and port number. The script tries to open a TCP connection and reports whether the port is open
- **type**: Set to `"tcp"` for TCP checks. Not required for HTTP checks

## Automation

Run it on a schedule with cron:

```bash
# Every 5 minutes
*/5 * * * * cd /path/to/homelab-status && python3 homelab_status.py
```

Then serve the `status.html` file with any static file server (Caddy, Nginx, Python `http.server`, etc.):

```bash
# Quick and dirty
python3 -m http.server 8080 --directory /path/to/homelab-status
```

## HTML output

The generated page is a single self-contained HTML file with inline CSS. It works offline and requires no JavaScript. Dark theme by default. Shows each service with an up/down icon, status detail, and latency in milliseconds.

## Why another status page?

Most self-hosted status tools are full web apps with databases and containers. Sometimes you just want a script that runs on cron and writes an HTML file. That is all this is.

## License

MIT
