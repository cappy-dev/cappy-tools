#!/usr/bin/env python3
"""
simple-cert-check - TLS certificate expiration checker

Checks the expiration date of TLS/SSL certificates on one or more
HTTPS endpoints and warns you before they expire. Zero dependencies:
uses only the Python standard library.

Usage:
    ./certcheck.py example.com
    ./certcheck.py example.com google.com github.com
    ./certcheck.py --port 8443 example.com
    ./certcheck.py --warn 14 example.com
    ./certcheck.py --json example.com
    ./certcheck.py --quiet example.com

Exit codes:
    0 - all certificates are healthy (more than warning days remaining)
    1 - one or more certificates expire within the warning window
    2 - one or more certificates are already expired
    3 - one or more connections failed (could not retrieve a certificate)
"""

import argparse
import base64
import json
import os
import socket
import ssl
import sys
import tempfile
from datetime import datetime, timezone


def fetch_cert_dict(hostname, port, timeout):
    """Connect to hostname:port and return a parsed certificate dict.

    We deliberately disable certificate verification so that we can still
    inspect expired, self-signed, or otherwise untrusted certificates
    (which is exactly what a homelab operator often cares about). The
    binary DER form is fetched first because Python's getpeercert() only
    fills the dict when verification is enabled. We then convert the DER
    bytes to a temporary PEM file and decode it with the standard
    library's internal certificate decoder, producing the same dict that
    getpeercert() would return on a trusted connection.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((hostname, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
            der_bytes = ssock.getpeercert(binary_form=True)

    if not der_bytes:
        return {}

    decoder = getattr(ssl._ssl, "_test_decode_cert", None)
    if decoder is None:
        return {}

    pem = (
        b"-----BEGIN CERTIFICATE-----\n"
        + base64.encodebytes(der_bytes)
        + b"-----END CERTIFICATE-----\n"
    )
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        os.write(fd, pem)
        os.close(fd)
        return decoder(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def parse_cert_info(cert):
    """Extract notAfter, issuer, and subject from a certificate dict."""
    not_after = cert.get("notAfter", "")
    if not not_after:
        raise ValueError("certificate did not contain an notAfter field")
    issuer_parts = []
    for rdn in cert.get("issuer", ()):
        for key, value in rdn:
            if key in ("commonName", "organizationName"):
                issuer_parts.append(value)
    subject_parts = []
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                subject_parts.append(value)
    return {
        "not_after": not_after,
        "issuer": ", ".join(issuer_parts) or "unknown",
        "subject": ", ".join(subject_parts) or "unknown",
    }


def parse_not_after(not_after_str):
    """Parse the date string produced by the ssl certificate decoder.

    Python formats certificate dates like 'Aug 29 21:41:26 2026 GMT'.
    """
    formats = [
        "%b %d %H:%M:%S %Y GMT",
        "%b %d %H:%M:%S %Y %Z",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(not_after_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError("unrecognized date format: %r" % not_after_str)


def check_host(hostname, port, timeout, warn_days, now):
    """Check one host and return a result dict."""
    result = {
        "host": hostname,
        "port": port,
        "status": "ok",
        "days_remaining": None,
        "not_after": None,
        "issuer": None,
        "subject": None,
        "error": None,
    }
    try:
        cert = fetch_cert_dict(hostname, port, timeout)
    except socket.gaierror:
        result["status"] = "error"
        result["error"] = "DNS lookup failed for %s" % hostname
        return result
    except socket.timeout:
        result["status"] = "error"
        result["error"] = "connection timed out to %s:%d" % (hostname, port)
        return result
    except (ConnectionRefusedError, ConnectionError, OSError) as exc:
        result["status"] = "error"
        result["error"] = "connection failed to %s:%d: %s" % (hostname, port, exc)
        return result
    except ssl.SSLError as exc:
        result["status"] = "error"
        result["error"] = "TLS error on %s:%d: %s" % (hostname, port, exc)
        return result

    try:
        info = parse_cert_info(cert)
    except ValueError as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    result["not_after"] = info["not_after"]
    result["issuer"] = info["issuer"]
    result["subject"] = info["subject"]

    try:
        expiry = parse_not_after(info["not_after"])
    except ValueError as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    days_remaining = (expiry - now).total_seconds() / 86400.0
    result["days_remaining"] = round(days_remaining, 1)
    if days_remaining < 0:
        result["status"] = "expired"
    elif days_remaining <= warn_days:
        result["status"] = "warning"
    return result


def colorize(text, code):
    """Wrap text in ANSI color codes if stdout is a tty."""
    if sys.stdout.isatty():
        return "\033[%sm%s\033[0m" % (code, text)
    return text


def format_result(result):
    """Format one result for human-readable output."""
    host = result["host"]
    port = result["port"]
    label = "%s:%d" % (host, port)
    if result["status"] == "error":
        return colorize("[ERROR]   %-30s %s" % (label, result["error"]), "31")
    days = result["days_remaining"]
    when = result["not_after"]
    issuer = result["issuer"]
    if result["status"] == "expired":
        line = "[EXPIRED] %-30s expired %6.1f days ago (%s) issuer: %s" % (
            label, abs(days), when, issuer)
        return colorize(line, "31")
    if result["status"] == "warning":
        line = "[WARN]    %-30s expires in %6.1f days (%s) issuer: %s" % (
            label, days, when, issuer)
        return colorize(line, "33")
    line = "[OK]      %-30s expires in %6.1f days (%s) issuer: %s" % (
        label, days, when, issuer)
    return colorize(line, "32")


def main():
    parser = argparse.ArgumentParser(
        prog="certcheck.py",
        description="Check TLS certificate expiration dates.",
    )
    parser.add_argument(
        "hosts",
        nargs="+",
        help="Hostnames to check, e.g. example.com or example.com:8443",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=443,
        help="default port if not given in the host string (default: 443)",
    )
    parser.add_argument(
        "--warn",
        type=int,
        default=14,
        help="warn this many days before expiry (default: 14)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="connection timeout in seconds (default: 10)",
    )
    parser.add_argument("--json", action="store_true", help="output results as JSON")
    parser.add_argument("--quiet", action="store_true", help="only show warnings and errors")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)

    targets = []
    for entry in args.hosts:
        if ":" in entry and entry.rsplit(":", 1)[1].isdigit():
            host, port_str = entry.rsplit(":", 1)
            targets.append((host.strip(), int(port_str)))
        else:
            targets.append((entry.strip(), args.port))

    results = []
    for host, port in targets:
        results.append(check_host(host, port, args.timeout, args.warn, now))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            if args.quiet and r["status"] == "ok":
                continue
            print(format_result(r))

    exit_code = 0
    for r in results:
        if r["status"] == "expired":
            exit_code = max(exit_code, 2)
        elif r["status"] == "warning":
            exit_code = max(exit_code, 1)
        elif r["status"] == "error":
            exit_code = max(exit_code, 3)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
