#!/bin/sh
# dns-update-host: update a DNS A record when your public IP changes.
#
# Designed for self-hosters on a dynamic public IP address. Reads your
# current public IP, compares it to the last value we wrote to the state
# file, and updates the DNS record only when it actually changed. This
# keeps you safe from API rate limits and dead simple to run from cron.
#
# Works with Cloudflare today. The DNS provider is pluggable: see the
# "provider" hooks near the bottom of the script if you want to add more.
#
# Zero runtime dependencies beyond /bin/sh, curl, and standard POSIX
# utilities (sed, grep, printf, cat). Tested on dash, bash, busybox ash.

# ---- defaults (override via environment or config file) -------------------
STATE_FILE="${STATE_FILE:-/var/lib/dns-update-host/state}"
CONFIG_FILE="${CONFIG_FILE:-/etc/dns-update-host.conf}"

# Provider: cloudflare
CF_ZONE_ID="${CF_ZONE_ID:-}"
CF_API_TOKEN="${CF_API_TOKEN:-}"
CF_RECORD_NAME="${CF_RECORD_NAME:-}"

# How to fetch the public IP. Override IP_FETCH_URL to use a different
# echo service. The response must be a bare IPv4 address.
IP_FETCH_URL="${IP_FETCH_URL:-https://api.ipify.org}"

# Set ENABLE_IPV6=1 to also keep an AAAA record in sync using the host's
# discovered IPv6 address (see fetch_public_ipv6 below).
ENABLE_IPV6="${ENABLE_IPV6:-0}"

# ---- initialization -------------------------------------------------------

# Load optional config file. This is where you would put the secrets and
# the record name for a system-wide install. Sourced, not parsed.
if [ -n "$CONFIG_FILE" ] && [ -f "$CONFIG_FILE" ]; then
    # shellcheck disable=SC1090
    . "$CONFIG_FILE"
fi

# Make the state directory writable regardless of who created it.
STATE_DIR=$(dirname "$STATE_FILE")
mkdir -p "$STATE_DIR" 2>/dev/null

# ---- helpers --------------------------------------------------------------

log() {
    printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

die() {
    log "ERROR: $*" >&2
    exit 1
}

# Write a value to a key in the state file. The state file is a simple
# KEY=VALUE format. We rewrite it atomically to avoid corruption when
# the process is interrupted mid-write.
state_set() {
    key=$1
    val=$2
    tmp="${STATE_FILE}.tmp"
    if [ -f "$STATE_FILE" ]; then
        grep -v "^${key}=" "$STATE_FILE" > "$tmp" 2>/dev/null || true
    else
        : > "$tmp"
    fi
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
    mv "$tmp" "$STATE_FILE"
}

state_get() {
    key=$1
    [ -f "$STATE_FILE" ] || return 0
    grep "^${key}=" "$STATE_FILE" 2>/dev/null | head -n1 | cut -d= -f2-
}

# Fetch the current public IPv4 address. Returns empty string on failure.
fetch_public_ipv4() {
    curl -fsS --max-time 15 "$IP_FETCH_URL" 2>/dev/null
}

# Fetch the current public IPv6 address. By default this asks the host
# what address it would use to reach a public IPv6 host, then trims the
# interface suffix. Override IPV6_FETCH_URL to use an echo service instead.
fetch_public_ipv6() {
    if [ -n "$IPV6_FETCH_URL" ]; then
        curl -fsS --max-time 15 "$IPV6_FETCH_URL" 2>/dev/null
        return
    fi
    addr=$(ip -6 -o route get 2001:4860:4860::8888 2>/dev/null \
           | sed -n 's/.*src \([0-9a-f:]*\).*/\1/p' | head -n1)
    # Strip any zone/interface suffix if present.
    echo "$addr" | cut -d% -f1
}

# Validate that a string looks like an IPv4 dotted quad.
is_ipv4() {
    echo "$1" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' || return 1
    for octet in $(echo "$1" | tr . ' '); do
        [ "$octet" -le 255 ] 2>/dev/null || return 1
    done
}

# Validate that a string looks like an IPv6 address (very loose check).
is_ipv6() {
    echo "$1" | grep -Eq '^[0-9a-fA-F:]+$' && [ "${1#*:*}" != "$1" ]
}

# ---- providers ------------------------------------------------------------

# Cloudflare: look up the record id for the given name and type.
# Echoes the record id, or empty string if not found.
cloudflare_get_record_id() {
    type=$1
    name=$2
    curl -fsS --max-time 30 \
        -H "Authorization: Bearer ${CF_API_TOKEN}" \
        -H "Content-Type: application/json" \
        "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?type=${type}&name=${name}" \
        2>/dev/null \
        | sed -n 's/.*"id":"\([0-9a-f]*\)".*/\1/p' | head -n1
}

# Cloudflare: create a new DNS record. $1=type $2=name $3=content
cloudflare_create_record() {
    type=$1
    name=$2
    content=$3
    curl -fsS --max-time 30 \
        -H "Authorization: Bearer ${CF_API_TOKEN}" \
        -H "Content-Type: application/json" \
        -X POST \
        "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records" \
        --data "{\"type\":\"${type}\",\"name\":\"${name}\",\"content\":\"${content}\",\"ttl\":300,\"proxied\":false}" \
        2>/dev/null
}

# Cloudflare: update an existing record in place. $1=record_id $2=type $3=name $4=content
cloudflare_update_record() {
    rid=$1
    type=$2
    name=$3
    content=$4
    curl -fsS --max-time 30 \
        -H "Authorization: Bearer ${CF_API_TOKEN}" \
        -H "Content-Type: application/json" \
        -X PUT \
        "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${rid}" \
        --data "{\"type\":\"${type}\",\"name\":\"${name}\",\"content\":\"${content}\",\"ttl\":300,\"proxied\":false}" \
        2>/dev/null
}

# Update a single record to match the desired content. Creates the record
# if it does not exist yet, otherwise updates it.
sync_record() {
    type=$1
    name=$2
    content=$3
    state_key="last_${type}_${name}"
    last_content=$(state_get "$state_key")

    if [ "$last_content" = "$content" ]; then
        log "${type} ${name} unchanged at ${content}"
        return 0
    fi

    log "Updating ${type} ${name}: ${last_content:-<none>} -> ${content}"
    rid=$(cloudflare_get_record_id "$type" "$name")
    if [ -z "$rid" ]; then
        out=$(cloudflare_create_record "$type" "$name" "$content")
        if echo "$out" | grep -q '"success":true'; then
            log "Created new ${type} record for ${name}"
            state_set "$state_key" "$content"
        else
            log "FAILED to create ${type} record for ${name}: ${out}"
            return 1
        fi
    else
        out=$(cloudflare_update_record "$rid" "$type" "$name" "$content")
        if echo "$out" | grep -q '"success":true'; then
            log "Updated ${type} record for ${name}"
            state_set "$state_key" "$content"
        else
            log "FAILED to update ${type} record for ${name}: ${out}"
            return 1
        fi
    fi
}

# ---- main -----------------------------------------------------------------

run() {
    # Required Cloudflare config.
    [ -n "$CF_ZONE_ID" ]     || die "CF_ZONE_ID is required (set it in ${CONFIG_FILE} or the environment)"
    [ -n "$CF_API_TOKEN" ]   || die "CF_API_TOKEN is required"
    [ -n "$CF_RECORD_NAME" ] || die "CF_RECORD_NAME is required (e.g. home.example.com)"

    ipv4=$(fetch_public_ipv4)
    if [ -z "$ipv4" ]; then
        die "could not fetch public IPv4 from ${IP_FETCH_URL}"
    fi
    is_ipv4 "$ipv4" || die "fetched IPv4 '${ipv4}' does not look like an address"
    log "Public IPv4 is ${ipv4}"

    sync_record A "$CF_RECORD_NAME" "$ipv4" || exit 1

    if [ "$ENABLE_IPV6" = "1" ]; then
        ipv6=$(fetch_public_ipv6)
        if [ -n "$ipv6" ] && is_ipv6 "$ipv6"; then
            log "Public IPv6 is ${ipv6}"
            sync_record AAAA "$CF_RECORD_NAME" "$ipv6" || true
        else
            log "IPv6 enabled but no global address found; skipping AAAA"
        fi
    fi
}

# Allow --check to do a dry run that only prints the current IP without
# touching the API. Useful for first-time setup and debugging.
case "${1:-}" in
    --check)
        ipv4=$(fetch_public_ipv4)
        echo "Public IPv4: ${ipv4:-(unreachable)}"
        if [ "$ENABLE_IPV6" = "1" ]; then
            ipv6=$(fetch_public_ipv6)
            echo "Public IPv6: ${ipv6:-(none)}"
        fi
        ;;
    --help|-h)
        cat <<EOF
dns-update-host: keep a DNS A (and AAAA) record synced to your public IP.

Usage: dns-update-host.sh [--check] [--help]

Configuration is read from ${CONFIG_FILE} if it exists, otherwise from the
environment. Required variables:

  CF_ZONE_ID        Cloudflare zone id for the domain.
  CF_API_TOKEN      Cloudflare API token with DNS edit permission on the zone.
  CF_RECORD_NAME    Fully qualified record name, e.g. home.example.com.

Optional variables:

  STATE_FILE        Path to the state file (default ${STATE_FILE}).
  IP_FETCH_URL      URL that echoes back the public IPv4 (default ${IP_FETCH_URL}).
  IPV6_FETCH_URL    URL that echoes back the public IPv6 (default: ip route).
  ENABLE_IPV6       Set to 1 to also sync an AAAA record (default 0).
EOF
        ;;
    "")
        run
        ;;
    *)
        die "unknown argument: ${1}. Use --help for usage."
        ;;
esac
