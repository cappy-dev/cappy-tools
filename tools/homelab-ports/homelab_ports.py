#!/usr/bin/env python3
"""homelab-ports: scan the local network for ports common to self-hosted services.

A tiny, dependency-free CLI that walks a CIDR range (or a single host) and
checks the ports most often used by self-hosted homelab services. It reports
which ports are open and labels them with the service that typically owns them,
so you can spot misconfigured or unexpectedly exposed services at a glance.

Usage:
    python3 homelab_ports.py 192.168.1.0/24
    python3 homelab_ports.py 192.168.1.0/24 --ports 80,443,8080
    python3 homelab_ports.py 10.0.0.5 --timeout 1 --top 25
    python3 homelab_ports.py scanme.example.org

NOTE: only scan networks you own or have explicit permission to scan.
This tool is intended for homelab self-audits, not offensive scanning.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterable


# A curated map of ports commonly run by self-hosted homelab services.
# Keys are port numbers, values are short human-friendly labels.
DEFAULT_PORTS: dict[int, str] = {
    22: "SSH",
    53: "DNS (Pi-hole / AdGuard)",
    80: "HTTP",
    443: "HTTPS",
    1883: "MQTT",
    3000: "Grafana / Uptime Kuma / web apps",
    3306: "MySQL / MariaDB",
    4040: "Cockpit",
    4443: "Cockpit (TLS)",
    5432: "PostgreSQL",
    5000: "Flask / Harbor / web apps",
    6379: "Redis",
    7474: "Neo4j (HTTP)",
    7687: "Neo4j (Bolt)",
    7878: "Radarr",
    7999: "BTCPay Server",
    8000: "Common dev server (Trango / FastAPI)",
    8008: "Synapse (Matrix HTTP)",
    8080: "HTTP alt (Traefik / web apps)",
    8081: "Tautulli / blocky",
    8086: "InfluxDB",
    8123: "Home Assistant",
    8443: "HTTPS alt",
    8888: "HTTP alt / Jupyter",
    9000: "Portainer / SonarQube",
    9001: "Portainer (agent)",
    9090: "Prometheus / Cockpit",
    9091: "Prometheus (pushgateway)",
    9092: "Alertmanager",
    9100: "Node Exporter",
    9117: "Jackett",
    9417: "Tr accident / custom",
    9443: "Portainer (HTTPS)",
    9696: "Prowlarr",
    11334: "AdGuard Home",
    11443: "AdGuard Home (TLS)",
    27017: "MongoDB",
    # Web UIs for popular self-hosted apps
    5050: "CouchPotato / Overseerr",
    7878: "Radarr",
    8983: "Sonarr",
    8989: "Sonarr (alt) / tvheadend",
    8959: "NZBGet",
    6767: "Bazarr",
    9981: "TVHeadend",
    # Sync / storage
    137: "NetBIOS name",
    138: "NetBIOS datagram",
    139: "SMB / NetBIOS session",
    445: "SMB",
    5357: "WS-Discovery",
    # Remote / admin
    3389: "RDP",
    5900: "VNC",
    6443: "Kubernetes API",
    10250: "kubelet",
    5555: "ADB / Android debug",
}

# A smaller "top" preset for quick scans.
TOP_PORTS: dict[int, str] = {
    22: DEFAULT_PORTS[22],
    80: DEFAULT_PORTS[80],
    443: DEFAULT_PORTS[443],
    3000: DEFAULT_PORTS[3000],
    3306: DEFAULT_PORTS[3306],
    5432: DEFAULT_PORTS[5432],
    6379: DEFAULT_PORTS[6379],
    8000: DEFAULT_PORTS[8000],
    8080: DEFAULT_PORTS[8080],
    8123: DEFAULT_PORTS[8123],
    8443: DEFAULT_PORTS[8443],
    9000: DEFAULT_PORTS[9000],
    9090: DEFAULT_PORTS[9090],
    9100: DEFAULT_PORTS[9100],
    445: DEFAULT_PORTS[445],
    3389: DEFAULT_PORTS[3389],
    5900: DEFAULT_PORTS[5900],
    6443: DEFAULT_PORTS[6443],
    27017: DEFAULT_PORTS[27017],
    1883: DEFAULT_PORTS[1883],
    53: DEFAULT_PORTS[53],
    8081: DEFAULT_PORTS[8081],
    8086: DEFAULT_PORTS[8086],
    9443: DEFAULT_PORTS[9443],
    5050: DEFAULT_PORTS[5050],
}

DEFAULT_PORT_LIST = sorted(DEFAULT_PORTS.keys())
DEFAULT_TIMEOUT = 0.8
DEFAULT_WORKERS = 96


@dataclass
class ProbeResult:
    """Outcome of a single host+port probe."""

    host: str
    port: int
    open: bool
    label: str

    def cooldown_label(self) -> str:
        return self.label or str(self.port)


def parse_ports(spec: str) -> list[int]:
    """Parse a comma-separated list of ports (with ranges)."""
    ports: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            ports.extend(range(int(lo), int(hi) + 1))
        else:
            ports.append(int(chunk))
    return sorted(set(ports))


def resolve_targets(target: str) -> list[str]:
    """Resolve a CIDR range or hostname into a list of host strings.

    A bare IP or a hostname resolves to a single host. A CIDR (e.g.
    192.168.1.0/24) expands to every usable host address in the range.
    Network and broadcast addresses are skipped.
    """
    # Try CIDR first.
    if "/" in target:
        try:
            network = ipaddress.ip_network(target, strict=False)
            hosts = list(network.hosts())
            if not hosts:
                # /32 or /31 edge cases.
                hosts = [network.network_address]
            return [str(h) for h in hosts]
        except ValueError:
            pass

    # Try a bare IP.
    try:
        ipaddress.ip_address(target)
        return [target]
    except ValueError:
        pass

    # Otherwise resolve the hostname (forward DNS).
    try:
        resolved = socket.gethostbyname_ex(target)
        return list(dict.fromkeys(resolved[2]))
    except socket.gaierror as exc:
        raise SystemExit(f"could not resolve host '{target}': {exc}") from exc


def probe(host: str, port: int, label: str, timeout: float) -> ProbeResult:
    """TCP connect-probe a single host:port. Returns a ProbeResult."""
    deadline = timeout
    if isinstance(host, str):
        try:
            ipaddress.ip_address(host)
            addr = (host, port)
        except ValueError:
            addr = (host, port)
    else:
        addr = (str(host), port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(deadline)
    try:
        sock.connect(addr)
        return ProbeResult(host=addr[0], port=port, open=True, label=label)
    except (socket.timeout, OSError, ConnectionError):
        return ProbeResult(host=addr[0], port=port, open=False, label=label)
    finally:
        sock.close()


def scan(
    targets: Iterable[str],
    ports: list[int],
    label_map: dict[int, str],
    timeout: float,
    workers: int,
    on_hit=None,
) -> list[ProbeResult]:
    """Probe every (host, port) pair. Returns only the open results, sorted."""
    # Snapshot to a list so we can count.
    targets = list(targets)
    jobs = [(h, p) for h in targets for p in ports]
    hits: list[ProbeResult] = []
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(probe, h, p, label_map.get(p, str(p)), timeout): (h, p)
            for h, p in jobs
        }
        for fut in as_completed(futures):
            result = fut.result()
            if result.open:
                with lock:
                    hits.append(result)
                    if on_hit is not None:
                        on_hit(result)
            # Re-raise is avoided; probe never throws.

    hits.sort(key=lambda r: (socket.inet_aton(r.host) if _is_ip(r.host) else r.host, r.port))
    return hits


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def banner(text: str, char: str = "=") -> str:
    line = char * len(text)
    return f"{line}\n{text}\n{line}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="homelab-ports",
        description=(
            "scan the local network for ports common to self-hosted services. "
            "Only scan networks you own or have permission to scan."
        ),
    )
    parser.add_argument(
        "target",
        help="CIDR range (192.168.1.0/24), single IP (10.0.0.5), or hostname.",
    )
    parser.add_argument(
        "--ports",
        default=None,
        help=(
            "comma-separated custom ports (ranges ok, e.g. '80,443,8080-8082'). "
            "Default: curated list of common self-hosted service ports."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        nargs="?",
        const=25,
        default=None,
        help=(
            "scan only the {N} most common self-hosted ports. "
            "Without a value, uses the built-in top preset (~25 ports)."
        ),
    )
    port_group = parser.add_mutually_exclusive_group()
    port_group.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                            help="per-probe connect timeout in seconds (default %(default)s).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="max concurrent probe threads (default %(default)s).")
    parser.add_argument("--open-only", action="store_true",
                        help="print only open ports (default behaviour).")
    parser.add_argument("--all", action="store_true",
                        help="also print closed ports (noisy, only for one host).")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the intro banner and per-line scanning output.")
    parser.add_argument("--no-color", action="store_true",
                        help="disable ANSI color output.")

    conversion_group = parser.add_mutually_exclusive_group()
    conversion_group.add_argument("--csv", action="store_true",
                                  help="output results as CSV (host,port,service) to stdout.")
    conversion_group.add_argument("--summary-only", action="store_true",
                                  help="print only the final summary line.")

    args = parser.parse_args(argv)

    # Build the label map and port list.
    if args.ports:
        ports = parse_ports(args.ports)
        label_map = {p: DEFAULT_PORTS.get(p, str(p)) for p in ports}
    elif args.top is not None:
        label_map = dict(TOP_PORTS)
        ports = sorted(label_map.keys())
        # If a number was passed, trim to that many.
        if isinstance(args.top, int) and args.top > 0:
            ports = ports[: args.top]
    else:
        label_map = dict(DEFAULT_PORTS)
        ports = DEFAULT_PORT_LIST

    targets = resolve_targets(args.target)

    if not args.quiet and not args.csv and not args.summary_only:
        print(banner(f" homelab-ports scan "))
        print(f"Target(s) : {', '.join(targets) if len(targets) <= 6 else f'{len(targets)} hosts'}")
        print(f"Ports     : {len(ports)} ")
        print(f"Timeout   : {args.timeout:.2f}s")
        print(f"Workers   : {args.workers}")
        if len(targets) * len(ports) > 10000:
            print("Heads up: that's a lot of probes. Consider --top or a narrower CIDR.")
        print("=" * 72)

    found: list[ProbeResult] = []

    def on_hit(result: ProbeResult) -> None:
        if args.quiet or args.csv:
            return
        if args.open_only or not args.all:
            _print_result(result, args.no_color, open_only=True)

    # When --all is set on a single host we also want to show closed ports.
    scanned_total = len(targets) * len(ports)
    if not args.quiet and not args.csv and not args.summary_only and not args.all:
        pass  # progress dots handled inline below if verbose

    # The actual scan.
    hits = scan(targets, ports, label_map, args.timeout, args.workers, on_hit)
    found = hits

    if args.csv:
        for r in found:
            print(f"{r.host},{r.port},{r.label or ''}")
        return 0

    if args.all and len(targets) == 1:
        # Re-draw the full table for one host when the user asked for closed too.
        if not args.summary_only and not args.quiet:
            single_target = targets[0]
            for p in ports:
                r = ProbeResult(host=single_target, port=p,
                                open=(any(h.port == p and h.host == single_target for h in found)),
                                label=label_map.get(p, str(p)))
                _print_result(r, args.no_color, open_only=False)
        # Single-host --all table already printed.

    if args.summary_only:
        print(f"{len(found)} open ports on {len(targets)} host(s), scanned {scanned_total} probes.")
        return 0

    if not args.quiet and not args.csv and not args.summary_only:
        print("=" * 72)
        print(f"Done. {len(found)} open port(s) across {len(targets)} host(s).")
        if not found:
            print("Nothing open on any of the scanned service ports. Either your services")
            print("are down, bound to localhost only, or filtered by a firewall.")
        return 0


def _print_result(result: ProbeResult, no_color: bool, open_only: bool) -> str:
    """Print a single probe result line.

    open_only: if True, only open-True rows are printed. If False, all rows print.
    """
    if open_only and not result.open:
        return ""
    state = "OPEN" if result.open else "closed"
    if no_color:
        color = ""
        reset = ""
    else:
        color = "\033[32m" if result.open else "\033[31m"
        reset = "\033[0m"
    label = f"  ({result.label})" if result.label else ""
    print(f"{color}{result.host}:{result.port}  {state}{reset}{label}")
    return ""


if __name__ == "__main__":
    sys.exit(main())
