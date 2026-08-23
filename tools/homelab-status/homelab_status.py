#!/usr/bin/env python3
"""homelab-status: Check your self-hosted services and generate an HTML status page."""

import json
import socket
import sys
import time
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from urllib.parse import urlparse

CONFIG_PATH = Path(__file__).parent / "services.json"
OUTPUT_PATH = Path(__file__).parent / "status.html"
TIMEOUT = 10  # seconds per check


def load_config(path: Path) -> dict:
    if not path.exists():
        print(f"Config not found: {path}")
        print("Create a services.json like this:")
        print(json.dumps({
            "title": "My Homelab",
            "services": [
                {"name": "Web", "url": "https://example.com", "expect": 200},
                {"name": "DNS", "host": "1.1.1.1", "port": 53, "type": "tcp"},
                {"name": "API", "url": "http://localhost:8080/health", "expect": 200}
            ]
        }, indent=2))
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def check_http(url: str, expect: int = 200) -> dict:
    """Check an HTTP/HTTPS endpoint and return a result dict."""
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    path = parsed.path or "/"
    is_https = parsed.scheme == "https"
    if not port:
        port = 443 if is_https else 80

    start = time.monotonic()
    try:
        conn_class = HTTPSConnection if is_https else HTTPConnection
        conn = conn_class(host, port, timeout=TIMEOUT)
        method = "HEAD" if is_https else "GET"
        conn.request(method, path)
        response = conn.getresponse()
        # Read the body so the connection can be reused or closed cleanly
        response.read()
        elapsed = time.monotonic() - start
        status_code = response.status
        conn.close()
        ok = status_code == expect
        return {
            "up": ok,
            "status_code": status_code,
            "latency_ms": round(elapsed * 1000),
            "detail": f"HTTP {status_code}" + ("" if ok else f" (expected {expect})"),
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "up": False,
            "status_code": None,
            "latency_ms": round(elapsed * 1000),
            "detail": str(e),
        }


def check_tcp(host: str, port: int) -> dict:
    """Check a raw TCP port and return a result dict."""
    start = time.monotonic()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT)
        sock.connect((host, port))
        sock.close()
        elapsed = time.monotonic() - start
        return {
            "up": True,
            "status_code": None,
            "latency_ms": round(elapsed * 1000),
            "detail": f"TCP port {port} open",
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "up": False,
            "status_code": None,
            "latency_ms": round(elapsed * 1000),
            "detail": str(e),
        }


def check_service(service: dict) -> dict:
    """Route to the correct checker based on service type."""
    if "url" in service:
        expect = service.get("expect", 200)
        return check_http(service["url"], expect)
    elif "host" in service and "port" in service:
        return check_tcp(service["host"], service["port"])
    else:
        return {"up": False, "status_code": None, "latency_ms": 0, "detail": "No url or host/port defined"}


def generate_html(title: str, results: list, timestamp: str) -> str:
    """Generate a self-contained HTML status page."""
    total = len(results)
    up_count = sum(1 for r in results if r["up"])
    overall = "all systems operational" if up_count == total else f"{total - up_count} service(s) down"

    rows = ""
    for r in results:
        icon = "\u2705" if r["up"] else "\u274c"
        bg = "#2d4a2d" if r["up"] else "#4a2d2d"
        latency = f'{r["latency_ms"]}ms' if r["latency_ms"] is not None else "N/A"
        code = f' (HTTP {r["status_code"]})' if r["status_code"] else ""
        rows += f"""      <tr style="background:{bg}">
        <td>{icon} {r["name"]}</td>
        <td>{r["detail"]}{code}</td>
        <td>{latency}</td>
      </tr>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - Status</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #1a1a2e; color: #eee; margin: 2rem auto; max-width: 720px; }}
  h1 {{ color: #e0e0e0; border-bottom: 2px solid #333; padding-bottom: .5rem; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: .6rem .8rem; }}
  th {{ background: #16213e; color: #aaa; font-size: .85rem; text-transform: uppercase; }}
  .meta {{ color: #777; font-size: .85rem; margin-top: 1.5rem; }}
  .badge {{ display: inline-block; padding: .25rem .6rem; border-radius: 4px;
            font-weight: 600; font-size: .85rem; margin-bottom: 1rem; }}
  .ok   {{ background: #1b4332; color: #b7e4c7; }}
  .warn {{ background: #6b2020; color: #f5b7b7; }}
</style>
</head>
<body>
  <h1>{title}</h1>
  <span class="badge {"ok" if up_count == total else "warn"}">{overall}</span>
  <table>
    <thead><tr><th>Service</th><th>Detail</th><th>Latency</th></tr></thead>
    <tbody>
{rows}    </tbody>
  </table>
  <p class="meta">Last checked: {timestamp}</p>
</body>
</html>
"""


def main():
    config = load_config(CONFIG_PATH)
    title = config.get("title", "Homelab Status")
    services = config.get("services", [])

    if not services:
        print("No services defined in config. Add some and try again.")
        sys.exit(1)

    print(f"Checking {len(services)} service(s) for '{title}'...")
    results = []
    for svc in services:
        name = svc.get("name", "unknown")
        print(f"  - {name}...", end=" ", flush=True)
        result = check_service(svc)
        result["name"] = name
        tag = "UP" if result["up"] else "DOWN"
        print(f"{tag} ({result['latency_ms']}ms) {result['detail']}")
        results.append(result)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    html = generate_html(title, results, now)

    OUTPUT_PATH.write_text(html)
    print(f"\nStatus page written to {OUTPUT_PATH}")
    up = sum(1 for r in results if r["up"])
    print(f"Summary: {up}/{len(results)} services UP")


if __name__ == "__main__":
    main()
