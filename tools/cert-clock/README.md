# cert-clock

Zero-dependency X.509 / TLS certificate expiry monitor written in pure
Python (standard library only). Check how many days are left on TLS
certificates by connecting to remote hosts or by reading local PEM files.
Designed to run from cron with machine-friendly exit codes so you can wire
it into monitoring, alerting, or deployment pipelines.

No `pip install`. No virtualenv. No third-party packages. Just one file.

## Features

- Check remote hosts over TLS (any port, defaults to 443).
- Check local PEM files, including fullchain bundles with multiple certs.
- Configurable warning and critical thresholds in days.
- `--insecure` flag for self-signed or internal CA certificates.
- IPv6 host syntax supported (`[::1]:8443`).
- Cron-friendly exit codes:
  - `0` every certificate is above the critical threshold.
  - `1` at least one certificate is expired or below the critical threshold.
  - `2` usage error.
- Optional leaf-only or whole-chain reporting for file targets.
- Tabular output with a human-readable summary line.

## Requirements

- Python 3.9 or newer.
- OpenSSL (via the `ssl` standard library module). Shipped with Python.

Nothing else.

## Installation

Copy the script onto your machine:

```sh
curl -fsSL https://raw.githubusercontent.com/cappy-dev/cert-clock/main/cert_clock.py \
  -o /usr/local/bin/cert-clock
chmod +x /usr/local/bin/cert-clock
```

Or clone the repo:

```sh
git clone https://github.com/cappy-dev/cert-clock.git
cd cert-clock
python3 cert_clock.py --help
```

No build step. No install step. Just run it.

## Usage

```
cert-clock [--host HOST[:PORT] ...] [--file PATH ...]
           [--warn DAYS] [--critical DAYS]
           [--timeout SECONDS] [--insecure]
           [--leaf-only | --chain] [--version]
```

Run `python3 cert_clock.py --help` for the full help text.

### Options

- `--host HOST[:PORT]` Remote host to check. Repeat for multiple hosts.
  Defaults to port 443 if no port is given. IPv6 is supported as `[addr]:port`.
- `--file PATH` Local PEM or fullchain file to check. Repeat for multiple files.
- `--warn DAYS` Threshold in days below which a cert is marked WARN (default 14).
- `--critical DAYS` Threshold in days below which a cert is marked CRITICAL
  (default 3).
- `--timeout SECONDS` Socket timeout for remote checks (default 10).
- `--insecure` Disable certificate verification for remote checks. Useful for
  self-signed or internal CA certificates.
- `--leaf-only` For file targets, report only the leaf certificate (default).
- `--chain` For file targets, report the soonest-expiring cert in the chain.
- `--version` Print the version and exit.

### Status labels

- `OK` days left above the warning threshold.
- `WARN` days left above critical but at or below warning.
- `CRITICAL` days left at or below the critical threshold.
- `EXPIRED` notAfter is in the past.
- `ERROR` the check itself failed (DNS, TLS, missing file, parse error).

## Examples

Check a single remote host:

```sh
$ python3 cert_clock.py --host example.com --warn 14 --critical 3
== cert-clock 0.1.0 ==
STATUS   TARGET                DAYS  MESSAGE
OK       example.com:443       77.8  expires 2026-10-27T22:17:21Z
summary: 1 healthy
```

Check multiple hosts:

```sh
$ python3 cert_clock.py --host www.google.com --host github.com:443 \
                         --warn 60 --critical 10
== cert-clock 0.1.0 ==
STATUS   TARGET                     DAYS  MESSAGE
OK       www.google.com:443         62.6  expires 2026-10-12T18:08:35Z
OK       github.com:443             50.8  expires 2026-09-30T23:59:59Z
summary: 2 healthy
```

Check a local PEM file (leaf certificate, default):

```sh
$ python3 cert_clock.py --file /etc/letsencrypt/live/site/fullchain.pem \
                         --warn 14 --critical 3
== cert-clock 0.1.0 ==
STATUS   TARGET                                       DAYS  MESSAGE
WARN     /etc/letsencrypt/live/site/fullchain.pem      9.4  leaf expires 2026-08-20T12:00:00Z
summary: 1 warning
```

Check a fullchain and report the soonest-expiring cert in the bundle:

```sh
$ python3 cert_clock.py --file /etc/letsencrypt/live/site/fullchain.pem \
                         --chain --warn 14 --critical 3
== cert-clock 0.1.0 ==
STATUS   TARGET                                       DAYS  MESSAGE
WARN     /etc/letsencrypt/live/site/fullchain.pem      9.4  3 cert(s); soonest expires 2026-08-20T12:00:00Z
summary: 1 warning
```

Check a self-signed internal service:

```sh
$ python3 cert_clock.py --host internal.local:8443 --insecure --warn 14 --critical 3
== cert-clock 0.1.0 ==
STATUS   TARGET                DAYS  MESSAGE
CRITICAL internal.local:8443   2.0  expires 2026-08-13T04:10:57Z
summary: 1 critical
```

Combine remote and file checks in one run:

```sh
$ python3 cert_clock.py --file /etc/letsencrypt/live/site/fullchain.pem \
                         --host example.com --host github.com \
                         --warn 30 --critical 7
```

## Cron integration

The exit codes make it trivial to wire into cron or a monitoring wrapper.

Run nightly and exit non-zero only when something is critical:

```cron
# /etc/cron.d/cert-clock
0 6 * * * root /usr/local/bin/cert-clock \
  --host example.com \
  --host mail.example.com:993 \
  --file /etc/letsencrypt/live/site/fullchain.pem \
  --warn 14 --critical 3 \
  >> /var/log/cert-clock.log 2>&1
```

Wrap it with a tiny notifier so critical states send an email:

```sh
#!/bin/sh
/usr/local/bin/cert-clock \
  --host example.com \
  --warn 14 --critical 3 \
  || mail -s "[cert-clock] alert" ops@example.com < /dev/null
```

## Exit codes

| Code | Meaning                                                    |
|------|------------------------------------------------------------|
| 0    | Every certificate is above the critical threshold.        |
| 1    | At least one certificate is expired or critical.          |
| 2    | Usage error or an unrecoverable runtime error.             |

## How it works

For remote checks, cert-clock opens a TLS connection to the target and reads
the leaf certificate in DER form via `ssl.SSLSocket.getpeercert`. For file
checks, it splits PEM blocks out of the input and converts each one to DER
with `ssl.PEM_cert_to_DER_cert`. In both cases the DER bytes are decoded
using the standard library's `ssl._ssl._test_decode_cert` helper, which
parses the certificate through the same OpenSSL code path Python already
depends on, returning the `notAfter` timestamp. That timestamp is parsed as
UTC and compared to the current time to compute days remaining.

There is no hand-rolled ASN.1 parser and no third-party dependency. The
certificate parsing leans on `ssl`, which is bundled with CPython.

## License

MIT. See [LICENSE](LICENSE).
