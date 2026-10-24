# simple-cert-check

Checks TLS certificate expiration dates and reports certificates nearing expiry.

## Features
- Checks one or more HTTPS endpoints in a single invocation
- Reports days remaining until expiry, the not-after date, and the issuer
- Color-coded output for OK / warning / expired / error
- JSON output for scripting and monitoring integration
- Configurable warning window (default: 14 days)
- Sensible exit codes so you can wire it into cron, systemd timers, or CI
- Works against expired, self-signed, and untrusted certificates
  (verification is intentionally disabled so you can still see what is
  wrong with a broken cert)

## Installation
No installation needed. Just grab the script and make it executable:

```sh
curl -O https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/simple-cert-check/certcheck.py
chmod +x certcheck.py
```

Or clone the repo:

```sh
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/simple-cert-check
chmod +x certcheck.py
```

That is it. There is no setup.py, no requirements.txt, nothing to build.

## Usage
```sh
./certcheck.py example.com
./certcheck.py example.com google.com github.com
./certcheck.py --port 8443 my-server.local
./certcheck.py --warn 30 example.com
./certcheck.py --json example.com
./certcheck.py --quiet example.com
```

### Options

- `--port N` - default port to use when a host does not include one (default 443)
- `--warn DAYS` - warn when a certificate expires within this many days (default 14)
- `--timeout SECONDS` - connection timeout per host (default 10)
- `--json` - print results as JSON instead of human-readable lines
- `--quiet` - only print warnings and errors, suppress healthy results

You can also pass a host with an explicit port using the `host:port` form:

```sh
./certcheck.py my-router.local:443 my-mail.local:993
```

### Example output

```sh
$ ./certcheck.py --warn 60 example.com google.com
[WARN]    example.com:443                expires in  47.7 days (Aug 29 21:41:26 2026 GMT) issuer: SSL Corporation, Cloudflare TLS Issuing ECC CA 3
[OK]      google.com:443                 expires in  63.2 days (Sep 14 08:35:29 2026 GMT) issuer: Google Trust Services, WE2
```

With `--json`:

```json
[
  {
    "host": "example.com",
    "port": 443,
    "status": "ok",
    "days_remaining": 47.7,
    "not_after": "Aug 29 21:41:26 2026 GMT",
    "issuer": "SSL Corporation, Cloudflare TLS Issuing ECC CA 3",
    "subject": "example.com",
    "error": null
  }
]
```

## Exit codes

Wire this script into cron or a monitoring system and use the exit code
to decide whether to alert.

| Code | Meaning                                                       |
|------|---------------------------------------------------------------|
| 0    | every certificate has more than the warning window remaining  |
| 1    | one or more certificates expire within the warning window    |
| 2    | one or more certificates are already expired                  |
| 3    | one or more connections failed (could not fetch a certificate) |

When several problems occur at once, the highest applicable code wins.

## How it works

The script opens a TLS connection to each host using Python's built-in
`ssl` module. Verification is intentionally turned off so that expired,
self-signed, or otherwise untrusted certificates can still be inspected
(which is usually exactly what you want when debugging a broken cert).

Because Python only populates the human-readable certificate dictionary
when verification is enabled, the script fetches the raw DER bytes of
the certificate, converts them to PEM in memory, and decodes them with
the standard library's internal certificate decoder. The result is the
same structured dict that `getpeercert()` would return on a trusted
connection, parsed entirely with the standard library.

No external process, no `openssl` CLI, no third-party packages.

## Cron example

Run a nightly check and alert only when something needs attention:

```sh
0 6 * * * /path/to/certcheck.py --quiet --warn 14 my-site.example my-other-site.example
```

Because `--quiet` suppresses healthy output and the exit code is non-zero
on warnings or errors, you can wire the result straight into a mailer or
a notifier.

## License

AGPL-3.0. See the [root license](../../LICENSE).
