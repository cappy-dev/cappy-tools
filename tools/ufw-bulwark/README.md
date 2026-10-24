# ufw-bulwark

Audits UFW rules and defaults for potentially unsafe exposure of network services.

## Requirements
- Python 3.8 or newer
- The `ufw` command on `PATH` (installed by default on Ubuntu and most Debian
  systems). Not needed when you pass `--input` to review a saved dump offline.
- No Python packages beyond the standard library.

## Install

No build step. Just grab the script and make it executable:

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/ufw-bulwark/ufw_bulwark.py
chmod +x ufw_bulwark.py
```

Or clone the repo and link it onto your `PATH`:

```bash
git clone https://github.com/cappy-dev/cappy-tools.git
sudo ln -s "$PWD/ufw-bulwark/ufw_bulwark.py" /usr/local/bin/ufw-bulwark
```

You need permission to run `ufw status verbose`, which on most systems means
running it with `sudo`.

## Usage
Audit the live firewall:

```bash
sudo ufw-bulwark
```

Write an HTML report too:

```bash
sudo ufw-bulwark --html /var/www/ufw-report.html
```

Review a `ufw status verbose` dump captured elsewhere (handy for offline audits
or testing):

```bash
sudo ufw status verbose > host-a.txt
python3 ufw_bulwark.py --input host-a.txt --html host-a.html
```

Parse the status without running the rule audit (status summary only):

```bash
python3 ufw_bulwark.py --input host-a.txt --no-audit
```

Suppress the text report and only produce an HTML file:

```bash
python3 ufw_bulwark.py --input host-a.txt --quiet --html host-a.html
```

Print the version:

```bash
ufw-bulwark --version
```

## Exit codes

Exit codes are designed for cron. Wire them into your scheduler so you get
alerted the way you want.

| Code | Meaning                                                          |
|------|------------------------------------------------------------------|
| 0    | No findings. Firewall configuration looks healthy.               |
| 1    | Only warnings. Advisory issues found, nothing immediately open.  |
| 2    | At least one critical finding. Action recommended.               |
| 3    | UFW not installed, not enabled, or status could not be read.     |

## Running from cron

Drop a file in `/etc/cron.weekly/ufw-bulwark` (Ubuntu style):

```bash
#!/bin/sh
# Weekly UFW firewall audit. Email output goes to the cron recipient.
/usr/local/bin/ufw-bulwark --html /var/www/ufw-report.html
```

Then:

```bash
sudo chmod +x /etc/cron.weekly/ufw-bulwark
```

The script exits non-zero only when a real problem is found, so wrapper scripts
that only alert on failure stay quiet on a clean run.

## What gets flagged

The audit inspects the global defaults and every parsed rule. Findings are
grouped as critical, warning, or info.

**Critical** (something is open that almost certainly should not be):

- Default incoming policy is not `deny` or `reject`
- UFW is installed but inactive (rules are not enforced at all)
- A sensitive port is allowed from `Anywhere` with no source restriction
- Telnet (23) is reachable from anywhere
- SSH is reachable from anywhere without a `LIMIT` rule
- A rule allows all traffic from a source with no port restriction at all

**Warning** (probably fine, but worth a look):

- IPv6 disabled while the host may have IPv6 connectivity
- Sensitive port allowed from a very broad CIDR (prefix `<= 8`)
- Default outgoing policy is `deny`, which usually breaks updates and DNS
- UFW status reported but no default policy line was found

**Info** (housekeeping):

- Duplicate rules
- UFW logging is off, so dropped packets are not recorded
- The logging level could not be determined

Each finding includes a `hint` line with a concrete command to fix or
investigate the issue.

## Sensitive ports

The default list covers services that are commonly run by accident or whose
exposure is almost always a mistake. Current set:

```
21 FTP, 22 SSH, 23 Telnet, 25 SMTP, 53 DNS, 135-139 NetBIOS/SMB, 445 SMB,
1433 MS SQL, 1521 Oracle, 2375/2376 Docker daemon, 3306 MySQL/MariaDB,
3389 RDP, 5432 PostgreSQL, 5601 Kibana, 5900 VNC, 5984 CouchDB,
6379 Redis, 8000/8080/8443 dev servers, 9000 PHP-FPM/Portainer,
9042 Cassandra, 9200/9300 Elasticsearch, 11211 memcached, 27017/27018 MongoDB
```

## License

AGPL-3.0. See the [root license](../../LICENSE).
