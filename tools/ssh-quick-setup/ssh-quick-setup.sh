#!/usr/bin/env bash
# ssh-quick-setup.sh
#
# Harden and configure SSH on Linux. Zero dependencies beyond bash and coreutils.
# Commands: keygen, deploy, harden, status, rollback
#

set -euo pipefail

VERSION="1.0.0"
SSHD_CONFIG="/etc/ssh/sshd_config"
SSHD_BACKUP_DIR="/etc/ssh/backups"
DEFAULT_KEY="$HOME/.ssh/id_ed25519"
SSH_DIR="$HOME/.ssh"

DRY_RUN=false
ASSUME_YES=false
KEY_FILE=""
SSH_PORT=""
KEY_COMMENT=""
REMOTE_USER="$(whoami)"

# Colors for TTY
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BOLD='' NC=''
fi

log()  { echo -e "${GREEN}[INFO]${NC} $*" >&2; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }
dry()  { echo -e "${YELLOW}[DRY]${NC} $*" >&2; }

confirm() {
    if [ "$ASSUME_YES" = "true" ]; then return 0; fi
    read -rp "$(echo -e "${BOLD}${1}${NC} [y/N] ")" answer
    case "$answer" in y|Y|yes|YES) return 0 ;; esac
    return 1
}

cmd_help() {
    cat <<'HELP'
ssh-quick-setup - Harden and configure SSH with one script

Commands:
    keygen              Generate an Ed25519 key pair
    deploy HOST         Copy public key to remote host
    harden              Harden sshd_config
    status              Show SSH configuration summary
    rollback            Restore sshd_config from backup
    help                Show this help

Options:
    --key FILE          Private key file (default: ~/.ssh/id_ed25519)
    --port PORT         SSH port to listen on (with harden)
    --comment TEXT      Comment for generated key (with keygen)
    --user USER         Remote username (default: current user)
    --dry-run           Preview changes without executing
    --yes, -y           Skip confirmation prompts
    --help, -h          Show help
HELP
}

cmd_keygen() {
    local key="${KEY_FILE:-$DEFAULT_KEY}"
    if [ -f "$key" ]; then
        warn "Key already exists at $key"
        if ! confirm "Overwrite existing key?"; then
            log "Keeping existing key."
            return 0
        fi
        rm -f "$key" "$key.pub"
    fi
    mkdir -p "$SSH_DIR"
    chmod 700 "$SSH_DIR"
    local comment="${KEY_COMMENT:-$(whoami)@$(hostname)}"
    if [ "$DRY_RUN" = "true" ]; then
        dry "ssh-keygen -t ed25519 -a 100 -f $key -C '$comment' -N ''"
    else
        ssh-keygen -t ed25519 -a 100 -f "$key" -C "$comment" -N "" -q
        chmod 600 "$key"
        chmod 644 "$key.pub"
        log "Generated Ed25519 key pair:"
        echo "  Private key: $key"
        echo "  Public key:  $key.pub"
        cat "$key.pub"
    fi
}

cmd_deploy() {
    local host="${1:-}"
    shift || true
    if [ -z "$host" ]; then
        err "Usage: ssh-quick-setup.sh deploy HOST [--user USER] [--key FILE]"
        exit 1
    fi
    local key="${KEY_FILE:-$DEFAULT_KEY}"
    local pubkey="${key}.pub"
    if [ ! -f "$pubkey" ]; then
        err "Public key not found: $pubkey"
        err "Generate one first: ssh-quick-setup.sh keygen"
        exit 1
    fi
    log "Deploying public key to ${REMOTE_USER}@${host} ..."
    if [ "$DRY_RUN" = "true" ]; then
        dry "ssh ${REMOTE_USER}@${host} 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys' < $pubkey"
    else
        cat "$pubkey" | ssh "${REMOTE_USER}@${host}" \
            "umask 077; mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
        log "Public key deployed. Test with: ssh -i $key ${REMOTE_USER}@${host}"
    fi
}

set_directive() {
    # Replace or append a sshd_config directive using awk
    local name="$1"
    local value="$2"
    local config="$3"
    if awk "NR==FNR && /^[$(printf '\t') ]*${name}[[:space:]]/ { found=1; print \"${name} ${value}\"; next } { print }" "$config" > "$config.tmp" && [ "$found" = "1" ]; then
        mv "$config.tmp" "$config"
    elif awk "NR==FNR && /^[$(printf '\t') ]*${name}[[:space:]]/ { found=1; print \"${name} ${value}\"; next } { print }" "$config" > "$config.tmp"; then
        mv "$config.tmp" "$config"
    else
        printf '%s\n' "${name} ${value}" >> "$config.tmp"
        mv "$config.tmp" "$config"
    fi
}

cmd_harden() {
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
        warn "Need root. Re-running with sudo."
        exec sudo bash "$0" "$@"
    fi
    if [ "$(id -u)" -ne 0 ]; then
        err "This command requires root. Use sudo."
        exit 1
    fi
    if [ ! -f "$SSHD_CONFIG" ]; then
        err "sshd_config not found at $SSHD_CONFIG"
        err "Is openssh-server installed?"
        exit 1
    fi
    log "Changes to be made to $SSHD_CONFIG:"
    log "  PasswordAuthentication no"
    log "  PermitRootLogin prohibit-password"
    log "  PubkeyAuthentication yes"
    log "  ChallengeResponseAuthentication no"
    log "  X11Forwarding no"
    log "  Modern KexAlgorithms, Ciphers, MACs"
    if [ -n "$SSH_PORT" ]; then
        log "  Port $SSH_PORT"
    fi
    if ! confirm "Proceed with hardening?"; then
        log "Aborted."
        exit 0
    fi
    mkdir -p "$SSHD_BACKUP_DIR"
    local stamp
    stamp="$(date +%s)"
    cp "$SSHD_CONFIG" "$SSHD_BACKUP_DIR/sshd_config.${stamp}.bak"
    log "Backup: $SSHD_BACKUP_DIR/sshd_config.${stamp}.bak"
    # Build the hardened config
    local tmpconfig
    tmpconfig="$(mktemp)"
    cp "$SSHD_CONFIG" "$tmpconfig"
    # Remove old values and add new ones
    for line in "PasswordAuthentication no" "PermitRootLogin prohibit-password" \
                "PubkeyAuthentication yes" "ChallengeResponseAuthentication no" "X11Forwarding no"; do
        local k="${line%% *}"
        # Remove existing lines (commented or not)
        grep -vE "^[[:space:]]*#?[[:space:]]*${k}[[:space:]]" "$tmpconfig" > "${tmpconfig}.clean" || true
        mv "${tmpconfig}.clean" "$tmpconfig" 2>/dev/null || cp "$tmpconfig" "${tmpconfig}.clean"
        echo "$line" >> "$tmpconfig"
    done
    # Modern algorithms (append them)
    cat >> "$tmpconfig" << 'ALGS'
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,umac-128-etm@openssh.com
ALGS
    if [ -n "$SSH_PORT" ]; then
        # Remove any existing Port line and add new one
        grep -vE "^[[:space:]]*#?[[:space:]]*Port[[:space:]]" "$tmpconfig" > "${tmpconfig}.clean" || true
        mv "${tmpconfig}.clean" "$tmpconfig" 2>/dev/null || true
        echo "Port $SSH_PORT" >> "$tmpconfig"
    fi
    # Validate
    if sshd -t -f "$tmpconfig" 2>/dev/null; then
        mv "$tmpconfig" "$SSHD_CONFIG"
        chmod 644 "$SSHD_CONFIG"
        log "Config validated and installed."
        if systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null; then
            log "sshd reloaded."
        else
            warn "Could not reload sshd. Manual reload needed."
        fi
        warn "Keep your SSH session open to test access!"
    else
        err "Config validation failed. Restored backup."
        cp "$SSHD_BACKUP_DIR/sshd_config.${stamp}.bak" "$SSHD_CONFIG"
        rm -f "$tmpconfig"
    fi
}

cmd_status() {
    echo -e "${BOLD}SSH Configuration Summary${NC}"
    echo "  Config: $SSHD_CONFIG"
    [ -f "$SSHD_CONFIG" ] || echo "  (not found)"
    echo ""
    [ -f "$SSHD_CONFIG" ] || exit 1
    for name in Port PermitRootLogin PasswordAuthentication PubkeyAuthentication UsePAM X11Forwarding; do
        local val
        val="$(grep -iE "^[[:space:]]*${name}[[:space:]]+" "$SSHD_CONFIG" 2>/dev/null | head -1 | awk '{print $2}')"
        val="${val:-default}"
        printf "  %-30s %s\n" "$name:" "$val"
    done
    echo ""
    echo -e "${BOLD}SSH keys in ~/.ssh:${NC}"
    local count
    count="$(find "$SSH_DIR" -name '*.pub' 2>/dev/null | wc -l)"
    echo "  $count key(s) found"
    echo ""
    echo -e "${BOLD}Backups:${NC}"
    [ -d "$SSHD_BACKUP_DIR" ] && echo "  $(ls -1 "$SSHD_BACKUP_DIR" 2>/dev/null | wc -l) backup(s)" || echo "  (none)"
}

cmd_rollback() {
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
        exec sudo bash "$0" "$@"
    fi
    [ ! -d "$SSHD_BACKUP_DIR" ] && err "No backups found" && exit 1
    local latest
    latest="$(find "$SSHD_BACKUP_DIR" -name 'sshd_config.*.bak' -type f | sort | tail -1)"
    [ -z "$latest" ] && err "No backups found" && exit 1
    log "Restoring $(basename "$latest")"
    if ! confirm "Restore this backup and reload sshd?"; then
        log "Aborted."
        exit 0
    fi
    cp "$latest" "$SSHD_CONFIG"
    chmod 644 "$SSHD_CONFIG"
    systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || warn "Manual reload needed"
    log "Rollback complete."
}

# Parse options
COMMAND="help"
while [ $# -gt 0 ]; do
    case "$1" in
        --key) KEY_FILE="$2"; shift 2 ;;
        --port) SSH_PORT="$2"; shift 2 ;;
        --comment) KEY_COMMENT="$2"; shift 2 ;;
        --user) REMOTE_USER="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --yes|-y) ASSUME_YES=true; shift ;;
        --help|-h) COMMAND="help"; shift; break ;;
        *) COMMAND="$1"; shift; break ;;
    esac
done

# Run command
case "$COMMAND" in
    keygen) cmd_keygen ;;
    deploy) shift || true; cmd_deploy "${@:---}" ;;
    harden) cmd_harden ;;
    status) cmd_status ;;
    rollback) cmd_rollback ;;
    help|--help|-h|"") cmd_help ;;
    *) err "Unknown command: $COMMAND"; cmd_help; exit 1 ;;
esac