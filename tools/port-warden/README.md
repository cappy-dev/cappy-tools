# port-warden

A zero-dependency Python tool that audits listening TCP and UDP ports on a
Linux host. It maps every open port to the owning process and flags services
that are risky to expose -- databases, Telnet, VNC, SNMP, Redis without auth,
and the like -- so you can catch an accidental exposure before someone else
does.

Designed for cron. Exits non-zero when warnings are found, which makes it
trivial to wire into a monitoring pipeline or a shell script that pages you
at 3 AM.

## Features

- Scans both TCP and UDP listeners across IPv4 and IPv6
- Maps each port to the owning process and PID
- Flags a curated set of risky services (Telnet, plain FTP, exposed databases,
  unauthenticated Redis, VNC, X11, SNMP, Memcached, and more)
- Flags ports bound to `0.0.0.0` / `::` so you can spot things that should
  really be on `127.0.0.1`
- Two output modes: a readable text table and machine-parseable JSON
- `--quiet` mode prints nothing unless there is a warning, perfect for cron
- No third-party packages. Uses only the Python 3 standard library plus
  `ss` from `iproute2` (with a `/proc/net` fallback when `ss` is missing)
- Runs entirely locally. No network calls ever leave the machine

## Requirements

- Linux with `/proc` and `/sys` available
- Python 3.7 or newer
- `ss` from the `iproute2` package (recommended; falls back to `/proc/net`
  parsing if `ss` is absent)
- `ip` from `iproute2` (only needed for the `--interface` filter)

## Installation

No build step. Drop the script anywhere on your `PATH` and make it
executable:

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/port-warden/main/port_warden.py
chmod +x port_warden.py
sudo mv port_warden.py /usr/local/bin/port-warden
```

That is the entire install. You also have the option to clone it:

```bash
git clone https://github.com/cappy-dev/port-warden.git
cd port-warden
python3 port_warden.py
```

## Usage

Run a basic scan:

```bash
python3 port_warden.py
```

Sample output:

```
port-warden: 5 listening socket(s) found
PROTO   BIND ADDR            PORT    PROCESS            WARNINGS
----------------------------------------------------------------------
tcp     0.0.0.0              22      sshd
tcp     0.0.0.0              80      nginx
tcp     0.0.0.0              6379    redis-server       high: Redis (often unauthenticated by default)
tcp     127.0.0.1            5432    postgres
udp     0.0.0.0              68      dhclient           low: Listening on all interfaces; restrict to localhost if not intended for external access
----------------------------------------------------------------------
Summary: 5 socket(s), 2 with warnings.
```

Get machine-readable JSON for logging or alerting:

```bash
python3 port_warden.py --json
```

Quiet mode prints nothing when everything looks fine, then non-zero exit codes
tell your cron wrapper what happened:

```bash
python3 port_warden.py --quiet
echo $?
```

Allow specific ports silently so a known-expected service does not raise a
flag:

```bash
python3 port_warden.py --allow 80,443,6379
```

Restrict the scan to a single interface:

```bash
python3 port_warden.py --interface eth0
```

Show the version:

```bash
python3 port_warden.py --version
```

## Exit Codes

The exit code makes `port-warden` friendly to cron, systemd timers, and shell
scripts that want to branch on the result:

- `0` -- no warnings found
- `1` -- at least one port has a low or medium severity warning
- `2` -- at least one port has a high severity warning (risky service exposed)

## Cron Example

Put the script somewhere stable and add a line to your crontab to check every
15 minutes, emailing only when a new warning appears:

```bash
*/15 * * * * /usr/local/bin/port-warden --quiet --json >> /var/log/port-warden.log 2>&1
```

Or wrap it so cron sends mail only on a non-zero exit, which keeps your inbox
quiet when the host is healthy:

```bash
*/15 * * * * /usr/local/bin/port-warden --quiet || (echo "port-warden flagged issues on $(hostname)"; /usr/local/bin/port-warden)
```

## Customization

The risk rules live near the top of `port_warden.py` in two dictionaries:

- `RISKY_PORT_MAP` -- ports that should not be publicly exposed, keyed by
  port number with a human-readable description
- `RISKY_PROCESS_PATTERNS` -- regex patterns matched against process names,
  with a short note on why each is flagged

Edit those dictionaries to match your own environment and policy. No config
file is needed; the rules are intentionally readable and small.

## License

MIT License. See the `LICENSE` file for the full text.
