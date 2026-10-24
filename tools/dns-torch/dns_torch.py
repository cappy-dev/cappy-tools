#!/usr/bin/env python3
"""dns-torch: Light up DNS problems with a single command.

A zero-dependency DNS troubleshooting toolkit that runs common
diagnostic checks and prints a clear, color-coded report.

Requires: Python 3.9+ (no pip packages needed)
"""

import argparse
import json
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COLORS = {
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "cyan": "\033[96m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

NO_COLOR = not sys.stdout.isatty()


def c(name: str, text: str) -> str:
    """Wrap *text* in ANSI color *name*."""
    if NO_COLOR:
        return text
    return f"{COLORS.get(name, '')}{text}{COLORS['reset']}"


def section(title: str) -> None:
    width = 60
    print()
    print(c("bold", "=" * width))
    print(c("bold", f"  {title}"))
    print(c("bold", "=" * width))


def status_icon(ok: bool) -> str:
    return c("green", "OK") if ok else c("red", "FAIL")


def run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run a command and return (returncode, combined_output)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:
        return -1, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "timed out"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_dns_resolve(domain: str) -> dict:
    """Basic DNS resolution via socket."""
    section("DNS RESOLUTION")
    result = {"check": "dns_resolve", "domain": domain, "records": {}}

    for rtype in ("A", "AAAA", "MX", "NS", "TXT"):
        rtype_name = rtype
        args = ["dig", "+short", domain, rtype]
        rc, out = run(args)
        records = [line for line in out.splitlines() if line.strip()] if rc == 0 else []
        ok = rc == 0
        result["records"][rtype_name] = {"ok": ok, "values": records}
        icon = status_icon(ok)
        count = len(records)
        print(f"  {icon}  {rtype_name:4s}  {count} record(s)")
        for val in records[:5]:
            print(f"        {c('cyan', val)}")
        if count > 5:
            print(f"        ... and {count - 5} more")

    # Fallback: try socket.getaddrinfo if dig is missing
    if not result["records"]["A"]["ok"]:
        try:
            ais = socket.getaddrinfo(domain, None, socket.AF_INET)
            ips = list({a[4][0] for a in ais})
            result["records"]["A"] = {"ok": True, "values": ips}
            print(f"  {status_icon(True)}  A    {len(ips)} record(s) (via socket)")
            for ip in ips[:5]:
                print(f"        {c('cyan', ip)}")
        except socket.gaierror:
            result["records"]["A"] = {"ok": False, "values": []}
            print(f"  {status_icon(False)}  A    resolution failed")

    return result


def check_dnssec(domain: str) -> dict:
    """DNSSEC validation status."""
    section("DNSSEC")
    rc, out = run(["dig", "+dnssec", "+short", domain, "DNSKEY"])
    has_keys = bool(out.strip()) if rc == 0 else False
    # Also check DS record at parent
    rc2, out2 = run(["dig", "+short", "DS", domain])
    has_ds = bool(out2.strip()) if rc2 == 0 else False

    signed = has_keys or has_ds
    result = {"check": "dnssec", "signed": signed, "dnskey": has_keys, "ds": has_ds}

    icon = status_icon(True) if signed else c("yellow", "NONE")
    status = "signed" if signed else "not signed"
    print(f"  {icon}  DNSSEC: {status}")
    if has_keys:
        print(f"       DNSKEY records found")
    if has_ds:
        print(f"       DS records found at parent")

    return result


def check_resolver_info() -> dict:
    """Identify the system DNS resolver."""
    section("RESOLVER")
    result = {"check": "resolver"}

    # Try resolvectl if available (systemd-resolved)
    rc, out = run(["resolvectl", "status"], timeout=5)
    if rc == 0 and out:
        lines = out.splitlines()[:8]
        print(c("cyan", "  systemd-resolved detected"))
        for line in lines:
            print(f"  {line}")
        result["type"] = "systemd-resolved"
        return result

    # Fallback: read /etc/resolv.conf
    try:
        with open("/etc/resolv.conf") as f:
            content = f.read().strip()
        nameservers = [
            l.split("#")[0].split(";", 1)[0].strip().split()[1]
            for l in content.splitlines()
            if l.strip().startswith("nameserver")
        ]
        print(c("cyan", "  /etc/resolv.conf"))
        for ns in nameservers:
            print(f"    nameserver {ns}")
        result["type"] = "resolv.conf"
        result["nameservers"] = nameservers
    except Exception as exc:
        print(f"  {status_icon(False)}  Could not read resolver info: {exc}")
        result["type"] = "unknown"

    return result


def check_mx(domain: str) -> dict:
    """Mail exchange check with priority ordering."""
    section("MAIL EXCHANGE (MX)")
    rc, out = run(["dig", "+short", domain, "MX"])
    result = {"check": "mx", "records": []}

    if rc != 0 or not out.strip():
        print(f"  {c('yellow', 'NONE')}  No MX records found")
        return result

    records = []
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            priority = parts[0]
            host = parts[1].rstrip(".")
            records.append({"priority": int(priority), "host": host})

    records.sort(key=lambda r: r["priority"])
    result["records"] = records

    for rec in records:
        print(f"  {status_icon(True)}  {rec['priority']:3d}  {c('cyan', rec['host'])}")

    return result


def check_dkim(domain: str, selector: str) -> dict:
    """DKIM record check."""
    section("DKIM")
    dkim_domain = f"{selector}._domainkey.{domain}"
    rc, out = run(["dig", "+short", dkim_domain, "TXT"])
    found = bool(out.strip()) if rc == 0 else False
    result = {"check": "dkim", "selector": selector, "found": found}

    icon = status_icon(True) if found else c("yellow", "MISSING")
    print(f"  {icon}  Selector '{selector}'")
    if found:
        for line in out.splitlines()[:3]:
            print(f"       {c('cyan', line)}")
    else:
        print(f"       No DKIM TXT record at {dkim_domain}")
        print(f"       Tip: try a different selector with --dkim-selector")

    return result


def check_spf(domain: str) -> dict:
    """SPF record check."""
    section("SPF")
    rc, out = run(["dig", "+short", domain, "TXT"])
    result = {"check": "spf", "found": False, "record": ""}

    if rc == 0 and out.strip():
        for line in out.splitlines():
            if "v=spf1" in line:
                result["found"] = True
                result["record"] = line.strip().strip('"')
                break

    icon = status_icon(True) if result["found"] else c("yellow", "MISSING")
    print(f"  {icon}  SPF record")
    if result["found"]:
        print(f"       {c('cyan', result['record'])}")
    else:
        print(f"       No v=spf1 TXT record found")

    return result


def check_dmarc(domain: str) -> dict:
    """DMARC record check."""
    section("DMARC")
    dmarc_domain = f"_dmarc.{domain}"
    rc, out = run(["dig", "+short", dmarc_domain, "TXT"])
    found = False
    record = ""
    if rc == 0 and out.strip():
        for line in out.splitlines():
            if "v=DMARC1" in line:
                found = True
                record = line.strip().strip('"')
                break

    result = {"check": "dmarc", "found": found, "record": record}
    icon = status_icon(True) if found else c("yellow", "MISSING")
    print(f"  {icon}  DMARC record at _dmarc.{domain}")
    if found:
        print(f"       {c('cyan', record)}")
    else:
        print(f"       No DMARC TXT record found")

    return result


def check_blacklist(domain: str) -> dict:
    """Basic blacklist check via DNSBL lookups for the domain's IPs."""
    section("BLACKLIST")
    # Get the A records first
    rc, out = run(["dig", "+short", domain, "A"])
    ips = [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []
    if not ips:
        # Fallback via socket
        try:
            ais = socket.getaddrinfo(domain, None, socket.AF_INET)
            ips = list({a[4][0] for a in ais})
        except socket.gaierror:
            ips = []

    dnsbl_servers = [
        "zen.spamhaus.org",
        "bl.spamcop.net",
        "dnsbl.sorbs.net",
    ]

    result = {"check": "blacklist", "ips": ips, "listed": []}
    total_checks = 0
    listed_count = 0

    for ip in ips:
        parts = ip.split(".")
        reversed_ip = ".".join(reversed(parts))
        for bl in dnsbl_servers:
            query = f"{reversed_ip}.{bl}"
            rc2, out2 = run(["dig", "+short", query, "A"])
            total_checks += 1
            is_listed = rc2 == 0 and bool(out2.strip())
            if is_listed:
                listed_count += 1
                result["listed"].append({"ip": ip, "blacklist": bl, "response": out2.strip()})
                print(f"  {c('red', 'LISTED')}  {ip} on {bl} ({out2.strip()})")
            else:
                pass  # clean, skip verbose output

    if listed_count == 0:
        print(f"  {status_icon(True)}  {len(ips)} IP(s) clean across {total_checks} checks")
    else:
        print(f"  {c('red', f'{listed_count} listing(s) found')}")

    return result


def check_ssl(domain: str, port: int = 443) -> dict:
    """SSL/TLS certificate check."""
    section("SSL/TLS CERTIFICATE")
    result = {"check": "ssl", "valid": False, "issuer": "", "expires": ""}

    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()

        result["valid"] = True
        result["issuer"] = dict(x[0] for x in cert.get("issuer", ())) if cert else ""
        expire_date = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        result["expires"] = cert["notAfter"]

        days_left = (expire_date - datetime.now(expire_date.tzinfo)).days
        icon = status_icon(True) if days_left > 30 else c("yellow", "WARN") if days_left > 7 else c("red", "CRIT")

        subject = dict(x[0] for x in cert.get("subject", ())) if cert else {}
        cn = subject.get("commonName", domain)
        org = result["issuer"].get("organizationName", "Unknown")

        print(f"  {icon}  Certificate valid")
        print(f"       CN:    {c('cyan', cn)}")
        print(f"       Issuer: {org}")
        print(f"       Expires: {cert['notAfter']} ({days_left} days left)")

        # Check SANs
        sans =cert.get("subjectAltName", [])
        if sans:
            san_list = [v for t, v in sans]
            print(f"       SANs:   {', '.join(san_list[:5])}")
            if len(san_list) > 5:
                print(f"               ... and {len(san_list) - 5} more")

    except ssl.SSLCertVerificationError as e:
        result["valid"] = False
        print(f"  {status_icon(False)}  Certificate invalid")
        print(f"       {c('red', str(e))}")
    except Exception as e:
        result["valid"] = False
        print(f"  {status_icon(False)}  Could not check certificate")
        print(f"       {c('red', str(e))}")

    return result


def check_connectivity(domain: str, port: int = 443, timeout: int = 5) -> dict:
    """TCP connectivity check."""
    section("TCP CONNECTIVITY")
    result = {"check": "connectivity", "ports": {}}

    for p in [80, 443]:
        try:
            start = time.monotonic()
            with socket.create_connection((domain, p), timeout=timeout):
                elapsed = time.monotonic() - start
                ok = True
        except Exception:
            elapsed = 0
            ok = False

        result["ports"][p] = {"ok": ok, "latency_ms": round(elapsed * 1000, 1)}
        icon = status_icon(ok)
        latency = f"{elapsed * 1000:.0f}ms" if ok else "N/A"
        service = "HTTP" if p == 80 else "HTTPS"
        print(f"  {icon}  {domain}:{p} ({service})  {latency}")

    return result


def check_http_headers(domain: str) -> dict:
    """Fetch and display security-relevant HTTP headers."""
    section("HTTP SECURITY HEADERS")
    result = {"check": "http_headers", "headers": {}}

    try:
        import http.client
        conn = http.client.HTTPSConnection(domain, timeout=10)
        conn.request("HEAD", "/", headers={"Host": domain, "User-Agent": "dns-torch/1.0"})
        resp = conn.getresponse()
        headers = dict(resp.getheaders())
        conn.close()
    except Exception:
        try:
            import http.client
            conn = http.client.HTTPConnection(domain, timeout=10)
            conn.request("HEAD", "/", headers={"Host": domain, "User-Agent": "dns-torch/1.0"})
            resp = conn.getresponse()
            headers = dict(resp.getheaders())
            conn.close()
        except Exception as e:
            print(f"  {status_icon(False)}  Could not fetch headers: {e}")
            return result

    important = [
        ("strict-transport-security", "HSTS"),
        ("content-security-policy", "CSP"),
        ("x-content-type-options", "X-Content-Type-Options"),
        ("x-frame-options", "X-Frame-Options"),
        ("x-xss-protection", "X-XSS-Protection"),
        ("referrer-policy", "Referrer-Policy"),
        ("permissions-policy", "Permissions-Policy"),
    ]

    found_count = 0
    for header, label in important:
        value = headers.get(header) or headers.get(header.lower())
        present = value is not None
        if present:
            found_count += 1
        result["headers"][header] = present
        icon = status_icon(True) if present else c("yellow", "MISSING")
        display_val = value[:60] if present else ""
        line = f"  {icon}  {label}"
        if display_val:
            line += f"  {c('cyan', display_val)}"
        print(line)

    print(f"\n  {found_count}/{len(important)} security headers present")
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def diagnose(domain: str, args: argparse.Namespace) -> list[dict]:
    """Run all checks and return the results list."""
    all_results: list[dict] = []

    print(c("bold", f"\ndns-torch  |  {domain}"))
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    if not args.no_resolver:
        all_results.append(check_resolver_info())

    all_results.append(check_dns_resolve(domain))

    if not args.no_connectivity:
        all_results.append(check_connectivity(domain))

    if not args.no_ssl:
        all_results.append(check_ssl(domain))

    if not args.no_http_headers:
        all_results.append(check_http_headers(domain))

    if not args.no_dnssec:
        all_results.append(check_dnssec(domain))

    if not args.no_email:
        all_results.append(check_mx(domain))
        all_results.append(check_spf(domain))
        all_results.append(check_dkim(domain, args.dkim_selector))
        all_results.append(check_dmarc(domain))

    if not args.no_blacklist:
        all_results.append(check_blacklist(domain))

    # Summary
    section("SUMMARY")
    total = len(all_results)
    passes = sum(1 for r in all_results if r.get("ok", True) or r.get("found", True) or r.get("valid", True) or r.get("signed", False) or r.get("records"))
    # Count issues
    issues = 0
    for r in all_results:
        if r.get("check") == "dns_resolve":
            for rtype, info in r.get("records", {}).items():
                if not info.get("ok"):
                    issues += 1
        if r.get("check") == "ssl" and not r.get("valid"):
            issues += 1
        if r.get("check") == "connectivity":
            for port, info in r.get("ports", {}).items():
                if not info.get("ok"):
                    issues += 1
        if r.get("check") == "blacklist" and r.get("listed"):
            issues += len(r["listed"])

    if issues == 0:
        print(f"  {c('green', 'All checks passed')} - no issues detected")
    else:
        print(f"  {c('red', f'{issues} issue(s) detected')} - review results above")

    print()
    return all_results


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dns-torch",
        description="Light up DNS problems with a single command.",
    )
    parser.add_argument("domain", help="Domain name to diagnose")
    parser.add_argument("--dkim-selector", default="default",
                        help="DKIM selector to check (default: default)")
    parser.add_argument("--no-email", action="store_true",
                        help="Skip email checks (MX, SPF, DKIM, DMARC)")
    parser.add_argument("--no-ssl", action="store_true",
                        help="Skip SSL/TLS certificate check")
    parser.add_argument("--no-dnssec", action="store_true",
                        help="Skip DNSSEC check")
    parser.add_argument("--no-blacklist", action="store_true",
                        help="Skip DNSBL blacklist check")
    parser.add_argument("--no-connectivity", action="store_true",
                        help="Skip TCP connectivity check")
    parser.add_argument("--no-http-headers", action="store_true",
                        help="Skip HTTP security headers check")
    parser.add_argument("--no-resolver", action="store_true",
                        help="Skip resolver identification")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colored output")
    args = parser.parse_args()

    # Strip any scheme or path from domain
    domain = args.domain.strip()
    if "://" in domain:
        domain = urlparse(domain).netloc or domain
    domain = domain.split("/")[0].split(":")[0]

    global NO_COLOR
    if args.no_color or not sys.stdout.isatty():
        NO_COLOR = True

    results = diagnose(domain, args)

    if args.json:
        # Remove non-serializable items
        print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
