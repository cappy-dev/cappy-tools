# update-herald

Reports available Linux package updates and security patches, with configurable notifications.

## Features
- Supports apt (Debian/Ubuntu), dnf (Fedora/RHEL), and pacman (Arch)
- Detects security updates and flags them separately
- Multiple output formats: plain text or JSON
- Flexible notifications:
  - POST JSON to any webhook (Slack, Discord, generic HTTP, etc.)
  - Write the report to a file
  - Run a shell command with the report text (notify-send, mail, etc.)
- Small and focused: one script, zero external packages
- Friendly exit codes for scripting and cron use

## Installation
Clone the repository and copy the script somewhere on your PATH:

```bash
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/update-herald
chmod +x update_herald.py
sudo cp update_herald.py /usr/local/bin/update-herald
```

Or just run it directly from the repo:

```bash
python3 update_herald.py
```

No virtual environment needed. update-herald uses only the Python standard library.

### Requirements
- Python 3.8 or newer
- A supported package manager: apt, dnf, or pacman
- Root or sudo access may be required to refresh package lists depending on your system configuration

## Usage
Run a basic check and print the report to the terminal:

```bash
python3 update_herald.py
```

Output as JSON for piping into other tools:

```bash
python3 update_herald.py --json
```

Only show security updates (skip everything else):

```bash
python3 update_herald.py --security-only
```

Send the report to a webhook:

```bash
python3 update_herald.py --webhook https://hooks.example.com/incoming
```

Save the report to a file:

```bash
python3 update_herald.py --file /var/tmp/updates.txt
```

Run a notification command (use %s where the report text should go):

```bash
python3 update_herald.py --notify-cmd 'notify-send "System Updates" "%s"'
```

Quiet mode for cron (suppresses stdout entirely):

```bash
python3 update_herald.py --quiet --webhook https://hooks.example.com/incoming
```

## Cron Example

Add this to your crontab to check for updates every morning at 6 AM and post the result to a webhook, silently:

```cron
0 6 * * * /usr/local/bin/update-herald --quiet --webhook https://hooks.example.com/update-check
```

Or write the report to a file that a status page reads:

```cron
0 6 * * * /usr/local/bin/update-herald --quiet --file /var/www/status/updates.txt
```

## Sample Output

Text format:

```
=== Update Herald Report ===
Package Manager: apt
Total Updates:   3
Security Updates: 1

Available Updates:
------------------------------------------------------------
  [SECURITY] openssl (1.1.1f-1ubuntu2 -> 1.1.1f-1ubuntu2.1)
  curl (8.5.0-2ubuntu10 -> 8.5.0-3ubuntu10)
  vim (2:9.0-1ubuntu1 -> 2:9.0-2ubuntu1)

Summary: 3 updates available, 1 security.
```

JSON format:

```json
{
  "manager": "apt",
  "total_updates": 3,
  "security_updates": 1,
  "updates": [
    {
      "name": "curl",
      "current": "8.5.0-2ubuntu10",
      "available": "8.5.0-3ubuntu10",
      "is_security": false
    },
    {
      "name": "openssl",
      "current": "1.1.1f-1ubuntu2",
      "available": "1.1.1f-1ubuntu2.1",
      "is_security": true
    }
  ]
}
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success, check completed (updates may or may not be available) |
| 1 | No supported package manager detected |
| 2 | Error running package manager commands |

## Webhook Payload

The webhook is sent as an HTTP POST with Content-Type application/json. The body matches the JSON output format shown above. This works with any generic webhook receiver, including custom endpoints, Slack incoming webhooks (wrap in a handler), and Discord webhooks.

## Supported Package Managers

| Manager | Distributions | Security Detection |
|---------|---------------|-------------------|
| apt | Debian, Ubuntu, Linux Mint, Pop!_OS | Yes (via `--component=security`) |
| dnf | Fedora, RHEL, CentOS Stream, Rocky, Alma | Yes (via `--security`) |
| pacman | Arch Linux, Manjaro, EndeavourOS | No (pacman does not tag security in standard output) |

## Cache and Permissions

update-herald runs read-only package checks whenever possible. On some systems, refreshing the package index requires sudo. The tool attempts a non-interactive sudo refresh (`sudo -n apt update` or similar) only if the initial read fails. For production use, configure passwordless sudo for the relevant update commands if you want automatic index refresh.

## Contributing

This is a small utility script kept intentionally lightweight. If you want to add support for another package manager (zypper, emerge, or anything else), open a pull request. Keep changes dependency-free and focused on the single script.

## License

AGPL-3.0. See the [root license](../../LICENSE).
