# dns-update-host

Updates a DNS A or AAAA record when the host's public IP address changes.

## Installation
### Option 1: system install (recommended for a server)

```sh
sudo install -m755 dns-update-host.sh /usr/local/sbin/dns-update-host
sudo install -d -m755 /var/lib/dns-update-host
sudo install -m600 /dev/null /etc/dns-update-host.conf
sudo chmod 600 /etc/dns-update-host.conf
sudo "$EDITOR" /etc/dns-update-host.conf
```

Put this in `/etc/dns-update-host.conf` (note the edit above makes it `0600` so only root can read the token):

```sh
CF_ZONE_ID=your_zone_id_here
CF_API_TOKEN=your_api_token_here
CF_RECORD_NAME=home.example.com
# Optional: also keep an AAAA record in sync
# ENABLE_IPV6=1
```

Then add a cron entry that runs it every five minutes:

```sh
sudo crontab -e
```

```cron
*/5 * * * * /usr/local/sbin/dns-update-host >> /var/log/dns-update-host.log 2>&1
```

### Option 2: single-file, no install

```sh
chmod +x dns-update-host.sh
CF_ZONE_ID=... CF_API_TOKEN=... CF_RECORD_NAME=home.example.com \
  STATE_FILE=$HOME/.dns-update-host.state \
  ./dns-update-host.sh
```

## Usage
Run a one-off check that only prints your current public IP and never talks to the API:

```sh
dns-update-host --check
```

Run the updater (creates the record if missing, updates it if changed, stays quiet if unchanged):

```sh
dns-update-host
```

Show help:

```sh
dns-update-host --help
```

Typical log output:

```text
[2026-08-02T04:05:00Z] Public IPv4 is 203.0.113.42
[2026-08-02T04:05:00Z] Updating A home.example.com: 203.0.113.17 -> 203.0.113.42
[2026-08-02T04:05:01Z] Updated A record for home.example.com
```

When nothing changed you just see the first line plus an "unchanged" notice, which keeps logs readable and makes it obvious in review when an actual update happened.

## Configuration reference

Read from `${CONFIG_FILE}` (`/etc/dns-update-host.conf` by default) when it exists, otherwise from the environment. Command-line flags never carry secrets.

| Variable | Meaning | Default |
| --- | --- | --- |
| `CF_ZONE_ID` | Cloudflare zone id for the domain. Required. | (none) |
| `CF_API_TOKEN` | Cloudflare API token with DNS edit permission scoped to the zone. Required. | (none) |
| `CF_RECORD_NAME` | The fully qualified record name to update, e.g. `home.example.com`. Required. | (none) |
| `STATE_FILE` | Where the last-known IP is stored. | `/var/lib/dns-update-host/state` |
| `IP_FETCH_URL` | URL that echoes back the public IPv4 as a bare IP. | `https://api.ipify.org` |
| `IPV6_FETCH_URL` | URL that echoes back the public IPv6. If unset, the script uses `ip route` to discover the host's global IPv6 address. | (unset) |
| `ENABLE_IPV6` | Set to `1` to also keep an `AAAA` record in sync. | `0` |
| `CONFIG_FILE` | Path to the config file. | `/etc/dns-update-host.conf` |

The state file is a plain `KEY=VALUE` format rewritten atomically so an interrupted run never corrupts it. Deleting it forces the next run to re-sync even if the IP is unchanged, which is handy after you recreate the zone.

## Creating the Cloudflare token

1. Open the Cloudflare dashboard, pick your domain, and on the Overview page copy the **Zone ID** from the right hand sidebar. That goes into `CF_ZONE_ID`.
2. Go to My Profile, API Tokens, Create Token.
3. Use the **Edit zone DNS** template or a custom token with these settings:
   - Permissions: `Zone` > `DNS` > `Edit`.
   - Zone Resources: `Include` > `Specific zone` > your domain.
   - (Optionally) limit by client IP.
4. Copy the token into `CF_API_TOKEN`. It is shown only once.

## Adding a new provider

The script calls exactly three provider functions: `cloudflare_get_record_id`, `cloudflare_create_record`, and `cloudflare_update_record`. To support another DNS backend, write the three equivalents for that backend and wire them into `sync_record`. The provider API surface is intentionally tiny: given a record type, name, and content, "find it, create it if missing, or update it in place."

## Security notes

- The config file holds an API token that can rewrite DNS for your zone, so keep it `0600` and owned by the user that runs the script. Never commit it to git.
- `dns-update-host` logs IP values but never the token.
- `State` only contains the last IP stored on disk, no secrets.
- Use a token scoped to a single zone with DNS edit permission. Avoid account-wide tokens.

## Testing without touching DNS

`--check` reaches out to `IP_FETCH_URL` to confirm network egress and prints the discovered IPv4 (and IPv6 if enabled), then exits. It never reads the config secrets that grant DNS access, so you can hand it to a support person for debugging without leaking the token.

## License

AGPL-3.0. See the [root license](../../LICENSE).
