# dns-diff

Compare DNS records between two nameservers. Spot what changed when you migrate
DNS providers, test new records before cutting over, or audit your nameservers
for drift.

Zero dependencies. Pure Python 3.8+. Optionally uses `dig` if installed, and
falls back to a built-in DNS-over-UDP client otherwise.

## What it does

Give it a domain and two nameservers. It queries A, AAAA, MX, TXT, NS, CNAME,
and SOA records from each server, then prints records that were added, removed,
or unchanged when comparing left versus right.

## Install

No install needed. Download the script and run it with Python 3.8+.

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/dns-diff/main/dnsdiff.py
chmod +x dnsdiff.py
./dnsdiff.py --help
```

Or clone the repo:

```bash
git clone https://github.com/cappy-dev/dns-diff.git
cd dns-diff
python3 dnsdiff.py --help
```

## Usage

```
python3 dnsdiff.py SERVER_A SERVER_B [options]
```

### Required positional arguments

- `SERVER_A`: the first (left) nameserver, e.g. `ns1.example.com` or `8.8.8.8`
- `SERVER_B`: the second (right) nameserver, e.g. `ns2.example.com` or `1.1.1.1`

### Options

- `-d, --domain DOMAIN`: check a single domain
- `--batch FILE`: a text file with one domain per line (lines starting with `#` are ignored)
- `-r, --records TYPES`: space-separated list of record types (default: `A AAAA MX TXT NS CNAME SOA`)
- `-t, --timeout SECONDS`: per-query timeout (default: `5.0`)
- `-o, --output FORMAT`: `text`, `json`, or `markdown` (default: `text`)
- `--no-dig`: skip dig and always use the built-in raw DNS client
- `--exit-code`: exit with code `1` when differences are found (handy for CI)
- `--version`: print the version number
- `-h, --help`: show help

### Supported record types

`A`, `AAAA`, `CNAME`, `MX`, `NS`, `SOA`, `TXT`, `PTR`

## Examples

Compare a single domain between two nameservers:

```bash
python3 dnsdiff.py ns1.example.com ns2.example.com -d mydomain.com
```

Compare between Google DNS and Cloudflare DNS:

```bash
python3 dnsdiff.py 8.8.8.8 1.1.1.1 -d example.com
```

Check only A and MX records:

```bash
python3 dnsdiff.py ns1.example.com ns2.example.com -d example.com -r A MX
```

Batch check a list of domains from a file:

```bash
python3 dnsdiff.py ns1.example.com ns2.example.com --batch domains.txt
```

Export results as JSON (good for piping into another tool):

```bash
python3 dnsdiff.py 8.8.8.8 1.1.1.1 -d example.com -o json
```

Generate a Markdown report for a wiki or README:

```bash
python3 dnsdiff.py ns1.example.com ns2.example.com --batch domains.txt -o markdown > dns-report.md
```

Use in CI to fail the build when DNS records differ between servers:

```bash
python3 dnsdiff.py ns1.example.com ns2.example.com --batch domains.txt --exit-code
echo "Exit code: $?"
```

Use the built-in client without dig:

```bash
python3 dnsdiff.py 8.8.8.8 1.1.1.1 -d example.com --no-dig
```

## Batch file format

One domain per line. Blank lines and lines starting with `#` are skipped.

```
# production domains
app.example.com
staging.example.com
api.example.com
# blog.example.com is paused, skip it
```

## How the fallback client works

When `dig` is not found on the system, dns-diff sends raw DNS query packets
over UDP on port 53 using the built-in `socket` module and parses the wire
format described in RFC 1035. This means the tool works on minimal containers
and slim boot images with no extra packages. Support for DNSSEC, TCP fallback,
and EDNS is intentionally left out to keep the script small.

## Output format notes

- **text**: colored terminal output (colors auto-disable when piped). Shows
  `-` for removed, `+` for added, `=` for matching records.
- **json**: a JSON array with one object per domain, including `left`,
  `right`, `added`, `removed`, `common`, and `record_types` keys.
- **markdown**: a compact list summary suitable for wiki pages or READMEs.
- When `--exit-code` is set, the script returns exit code `1` if any domain
  had differences. This is useful for CI pipelines and monitoring checks.

## License

MIT
