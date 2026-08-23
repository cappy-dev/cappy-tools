#!/usr/bin/env python3
"""cert-clock - Zero-dependency X.509/TLS certificate expiry monitor.

Checks how long until TLS certificates expire, either by connecting to a
remote host or by reading a local PEM file.  Designed to run from cron and
to produce a non-zero exit code when a certificate is close to expiring so
you can wire it into monitoring or alert systems.

Pure standard library.  No third-party packages required.

Examples
--------
  # Check a remote HTTPS endpoint
  python3 cert_clock.py --host example.com --warn 14

  # Check multiple remote hosts
  python3 cert_clock.py --host example.com --host host:8443 --warn 14

  # Check a local PEM file (leaf only, default)
  python3 cert_clock.py --file /etc/letsencrypt/live/site/fullchain.pem --warn 14

  # Combine remote and file checks, exit non-zero if anything is critical
  python3 cert_clock.py --file certs/server.pem --host api.site.com:443 \
                       --warn 14 --critical 3

Exit codes
----------
  0  All certificates have more time left than --critical days.
  1  At least one certificate is within --critical days of expiry (or expired).
  2  Usage error or an unrecoverable runtime error occurred.
"""

from __future__ import annotations

import argparse
import os
import socket
import ssl
import sys
import tempfile
from datetime import datetime, timezone

__version__ = "0.1.0"

DEFAULT_PORT = 443
DEFAULT_WARN_DAYS = 14
DEFAULT_CRIT_DAYS = 3
DEFAULT_TIMEOUT = 10

PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
PEM_END = "-----END CERTIFICATE-----"

# ssl._ssl._test_decode_cert returns notAfter in this format:
#   "Oct 12 18:08:35 2026 GMT"
_NETAFTER_FMT = "%b %d %H:%M:%S %Y %Z"


class CertResult:
    """One outcome row for a single check target."""

    def __init__(
        self,
        target: str,
        ok: bool,
        not_after: datetime | None,
        days_left: float | None,
        message: str,
    ):
        self.target = target
        self.ok = ok
        self.not_after = not_after
        self.days_left = days_left
        self.message = message

    def status_for(self, warn: int, crit: int) -> str:
        """Short status word for tabular output, using the given thresholds.

        - EXPIRED  : days_left is negative.
        - CRITICAL : 0 <= days_left <= crit.
        - WARN     : crit < days_left <= warn.
        - OK       : days_left > warn.
        - ERROR    : the check itself failed.
        - UNKNOWN  : days_left could not be computed.
        """
        if not self.ok:
            return "ERROR"
        if self.days_left is None:
            return "UNKNOWN"
        if self.days_left < 0:
            return "EXPIRED"
        if self.days_left <= crit:
            return "CRITICAL"
        if self.days_left <= warn:
            return "WARN"
        return "OK"


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def _decode_der_with_ssl(der: bytes) -> dict:
    """Decode a DER cert via the standard library's test_decode_cert helper.

    ssl._ssl._test_decode_cert reads a PEM file and returns a dict with keys
    like 'notAfter', 'subject', 'issuer', and 'version'.  We convert the DER
    bytes to PEM on the fly and feed a temporary file to it.  This route uses
    the same OpenSSL code path that Python's ssl module relies on, so it is
    far more robust than a hand-rolled ASN.1 walker.
    """
    pem = ssl.DER_cert_to_PEM_cert(der)
    fd, path = tempfile.mkstemp(prefix="cert_clock_", suffix=".pem")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(pem)
        return ssl._ssl._test_decode_cert(path)  # type: ignore[attr-defined]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _parse_net_after(value: str) -> datetime:
    """Parse ssl's "Oct 12 18:08:35 2026 GMT" notAfter string to UTC datetime."""
    if not value:
        raise ValueError("empty notAfter string")
    # strptime with %Z handles GMT.  The result is treated as UTC.
    dt = datetime.strptime(value, _NETAFTER_FMT).replace(tzinfo=timezone.utc)
    return dt


def not_after_from_der(der: bytes) -> datetime:
    """Return the notAfter datetime for a single DER certificate."""
    info = _decode_der_with_ssl(der)
    raw = info.get("notAfter")
    if not raw:
        raise RuntimeError("decoded certificate has no notAfter field")
    return _parse_net_after(raw)


def load_remote_not_after(
    host: str, port: int, timeout: int, insecure: bool
) -> datetime:
    """Connect to host:port and pull the leaf cert notAfter timestamp."""
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            der = ssock.getpeercert(binary_form=True)
    if not der:
        raise RuntimeError("no certificate returned by peer")
    return not_after_from_der(der)


def _parse_pem_chain(pem_text: str) -> list[datetime]:
    """Split PEM blocks out of text and decode each one with ssl."""
    blocks: list[str] = []
    started = False
    buf: list[str] = []
    for line in pem_text.splitlines():
        stripped = line.strip()
        if stripped == PEM_BEGIN:
            started = True
            buf = [stripped]
        elif stripped == PEM_END and started:
            buf.append(stripped)
            blocks.append("\n".join(buf))
            buf = []
            started = False
        elif started:
            buf.append(stripped)
    results: list[datetime] = []
    for block in blocks:
        der = ssl.PEM_cert_to_DER_cert(block)
        if not der:
            continue
        try:
            results.append(not_after_from_der(der))
        except Exception:
            continue
    if not results:
        raise ValueError("no PEM CERTIFICATE blocks found in input")
    return results


def load_file_not_after(path: str) -> list[datetime]:
    """Read up to LEAF+chain PEM certs from a file."""
    if not os.path.isfile(path):
        raise FileNotFoundError("no such file: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        data = fh.read()
    return _parse_pem_chain(data)


def days_until(not_after: datetime, now: datetime) -> float:
    """Return float days from now until not_after (may be negative)."""
    return (not_after - now).total_seconds() / 86400.0


def parse_host_endpoint(spec: str, default_port: int) -> tuple[str, int]:
    """Accept 'host', 'host:port', or '[ipv6]:port' forms."""
    if spec.startswith("["):
        end = spec.find("]")
        if end == -1:
            raise ValueError("malformed IPv6 host: %s" % spec)
        host = spec[1:end]
        rest = spec[end + 1 :]
        if rest.startswith(":"):
            return host, int(rest[1:])
        return host, default_port
    if ":" in spec and spec.count(":") == 1:
        host, _, port = spec.partition(":")
        return host, int(port)
    return spec, default_port


def check_host(host: str, port: int, timeout: int, insecure: bool) -> CertResult:
    """Check a remote host and return a CertResult."""
    label = "%s:%d" % (host, port)
    try:
        not_after = load_remote_not_after(host, port, timeout, insecure)
    except socket.gaierror as e:
        return CertResult(label, False, None, None, "DNS error: %s" % e)
    except socket.timeout:
        return CertResult(label, False, None, None, "connection timed out")
    except ssl.SSLError as e:
        return CertResult(label, False, None, None, "TLS error: %s" % e)
    except Exception as e:  # pragma: no cover - defensive
        return CertResult(label, False, None, None, "error: %s" % e)
    dl = days_until(not_after, utc_now())
    return CertResult(label, True, not_after, dl, "expires %s" % _fmt_dt(not_after))


def check_file(path: str, leaf_only: bool) -> CertResult:
    """Check a local PEM (or fullchain) file and return a CertResult."""
    try:
        not_afters = load_file_not_after(path)
    except FileNotFoundError as e:
        return CertResult(path, False, None, None, str(e))
    except ValueError as e:
        return CertResult(path, False, None, None, "parse error: %s" % e)
    except Exception as e:  # pragma: no cover - defensive
        return CertResult(path, False, None, None, "error: %s" % e)
    if leaf_only:
        not_after = not_afters[0]
        dl = days_until(not_after, utc_now())
        return CertResult(path, True, not_after, dl, "leaf expires %s" % _fmt_dt(not_after))
    soonest = min(not_afters)
    dl = days_until(soonest, utc_now())
    return CertResult(
        path,
        True,
        soonest,
        dl,
        "%d cert(s); soonest expires %s" % (len(not_afters), _fmt_dt(soonest)),
    )


def _fmt_dt(dt: datetime) -> str:
    """Render a datetime in ISO 8601 UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(args: argparse.Namespace) -> int:
    """Execute the requested checks and return the process exit code."""
    unified_warn: int | None = (
        args.warn if isinstance(args.warn, int) else DEFAULT_WARN_DAYS
    )
    unified_crit: int | None = (
        args.critical if isinstance(args.critical, int) else DEFAULT_CRIT_DAYS
    )
    results: list[CertResult] = []

    for spec in args.host or []:
        host, port = parse_host_endpoint(spec, DEFAULT_PORT)
        results.append(check_host(host, port, args.timeout, args.insecure))

    for path in args.file or []:
        results.append(check_file(path, args.leaf_only))

    if not results:
        print("error: provide at least one --host or --file target", file=sys.stderr)
        return 2

    width_target = max(len(r.target or "") for r in results)
    width_target = max(width_target, len("TARGET"))

    crit_hit = False
    print("== cert-clock %s ==" % __version__)
    print(
        "%-8s %-*s %10s  %s"
        % ("STATUS", width_target, "TARGET", "DAYS", "MESSAGE")
    )
    for r in results:
        status = r.status_for(unified_warn, unified_crit) if r.ok else "ERROR"
        if status in ("ERROR", "EXPIRED", "CRITICAL"):
            crit_hit = True
        dl_str = "%.1f" % r.days_left if r.days_left is not None else "n/a"
        print(
            "%-8s %-*s %10s  %s"
            % (status, width_target, r.target, dl_str, r.message)
        )

    # Human-readable summary line.
    expired = [
        r for r in results
        if r.ok and r.days_left is not None and r.days_left < 0
    ]
    crit_n = [
        r for r in results
        if r.ok and r.days_left is not None and 0 <= r.days_left <= unified_crit
    ]
    warn_n = [
        r for r in results
        if r.ok and r.days_left is not None
        and unified_crit < r.days_left <= unified_warn
    ]
    healthy = [
        r for r in results
        if r.ok and r.days_left is not None and r.days_left > unified_warn
    ]
    errored = [r for r in results if not r.ok]

    summary_parts: list[str] = []
    if healthy:
        summary_parts.append("%d healthy" % len(healthy))
    if warn_n:
        summary_parts.append("%d warning" % len(warn_n))
    if crit_n:
        summary_parts.append("%d critical" % len(crit_n))
    if expired:
        summary_parts.append("%d expired" % len(expired))
    if errored:
        summary_parts.append("%d errored" % len(errored))
    print("summary: " + ", ".join(summary_parts or ["nothing to report"]))

    return 1 if crit_hit else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cert-clock",
        description="Monitor X.509/TLS certificate expiry. Cron-friendly exit codes.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--host",
        action="append",
        metavar="HOST[:PORT]",
        help="Remote host to check. May be given multiple times.",
    )
    p.add_argument(
        "--file",
        action="append",
        metavar="PATH",
        help="Local PEM or fullchain file to check. May be given multiple times.",
    )
    p.add_argument(
        "--warn",
        type=int,
        default=DEFAULT_WARN_DAYS,
        help="Threshold in days below which a cert is considered warning (default %d)."
        % DEFAULT_WARN_DAYS,
    )
    p.add_argument(
        "--critical",
        type=int,
        default=DEFAULT_CRIT_DAYS,
        help="Threshold in days below which a cert is considered critical (default %d)."
        % DEFAULT_CRIT_DAYS,
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Socket timeout in seconds for remote checks (default %d)."
        % DEFAULT_TIMEOUT,
    )
    p.add_argument(
        "--insecure",
        action="store_true",
        help="Disable certificate verification for remote checks (self-signed certs).",
    )
    p.add_argument(
        "--leaf-only",
        action="store_true",
        default=True,
        help="For --file targets, report only the leaf cert (default).",
    )
    p.add_argument(
        "--chain",
        action="store_false",
        dest="leaf_only",
        help="For --file targets, report the soonest-expiring cert in the chain.",
    )
    p.add_argument(
        "--version",
        action="version",
        version="cert-clock %s" % __version__,
    )
    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
