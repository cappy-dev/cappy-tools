# dns-torch

Light up DNS problems with a single command.

A zero-dependency Python CLI that runs DNS, SSL, email, blacklist, and HTTP
security header checks on any domain and prints a color-coded report to your
terminal. Uses only the Python standard library plus `dig` (bundled with most
Linux and macOS systems).

## What it checks

- **DNS resolution** - A, AAAA, MX, NS, TXT records
- **DNSSEC** - signed or unsigned zone
- **System resolver** - systemd-resolved or /etc/resolv.conf
- **TCP connectivity** - ports 80 and 443 with latency
- **SSL/TLS certificate** - validity, issuer, expiry, SANs
- **HTTP security headers** - HSTS, CSP, X-Frame-Options, and more
- **Mail exchange** - MX records sorted by priority
- **SPF / DKIM / DMARC** - email authentication records
- **DNSBL blacklist** - checks domain IPs against Spamhaus, Spamcop, SORBS

## Requirements

- Python 3.9 or newer
- `dig` (from the `dnsutils` or `bind9-utils` package on most distros)
- Works on Linux and macOS

## Installation

Clone the repo and make the script executable:

```bash
git clone https://github.com/cappy-dev/dns-torch.git
cd dns-torch
chmod +x dns_torch.py
```

Or run it directly without cloning:

```bash
python3 <(curl -sL https://raw.githubusercontent.com/cappy-dev/dns-torch/main/dns_torch.py) example.com
```

No pip packages needed. Zero dependencies beyond the standard library and dig.

## Usage

Basic scan of a domain:

```bash
./dns_torch.py example.com
```

Skip email checks (MX, SPF, DKIM, DMARC):

```bash
./dns_torch.py --no-email example.com
```

Output everything as JSON:

```bash
./dns_torch.py --json example.com
```

Turn off colors:

```bash
./dns_torch.py --no-color example.com
```

Use a specific DKIM selector:

```bash
./dns_torch.py --dkim-selector google example.com
```

Skip individual checks:

```bash
./dns_torch.py --no-ssl --no-blacklist --no-http-headers example.com
```

## Example output

```
dns-torch  |  example.com
  2026-06-29 00:12:00 UTC

============================================================
  DNS RESOLUTION
============================================================
  OK   A     1 record(s)
        93.184.216.34
  OK   AAAA  1 record(s)
        2606:2800:220:1:248:1893:25c8:1946
  OK   MX    1 record(s)
        0 mail.example.com
  OK   NS    2 record(s)
        a.iana-servers.net
        b.iana-servers.net
  OK   TXT   1 record(s)
        v=spf1 -all

============================================================
  SSL/TLS CERTIFICATE
============================================================
  OK   Certificate valid
       CN:    www.example.com
       Issuer: DigiCert Inc
       Expires: Nov  8 00:00:00 2027 (522 days left)

============================================================
  SUMMARY
============================================================
  All checks passed - no issues detected
```

## Options

```
positional:
  domain                 Domain name to diagnose

flags:
  --dkim-selector SEL    DKIM selector to check (default: default)
  --no-email             Skip email checks (MX, SPF, DKIM, DMARC)
  --no-ssl               Skip SSL/TLS certificate check
  --no-dnssec            Skip DNSSEC check
  --no-blacklist         Skip DNSBL blacklist check
  --no-connectivity      Skip TCP connectivity check
  --no-http-headers      Skip HTTP security headers check
  --no-resolver          Skip resolver identification
  --json                 Output results as JSON
  --no-color             Disable colored output
```

## Why no pip packages?

dns-torch is built for those moments when you SSH into a server and need DNS
answers fast. The only external tool it calls is `dig`, which is already
present on nearly every Linux server. Everything else comes from the Python
standard library, so there is nothing to install and nothing to break.

## License

MIT
