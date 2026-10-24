#!/usr/bin/env bash
#
# tailscale-quick-setup - Install and configure Tailscale on common Linux distros
#
# Usage: curl -sSL https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/tailscale-quick-setup/tailscale-quick-setup.sh | bash
#   Or:  bash tailscale-quick-setup.sh [--auth-key=tskey-xxx] [--yes] [--uninstall]
#
set -euo pipefail

VERSION="1.0.0"
SCRIPT_NAME="tailscale-quick-setup"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Defaults
AUTH_KEY=""
AUTO_YES=false
UNINSTALL=false
TAILSCALE_ARGS=""

# Parse args
for arg in "$@"; do
    case "$arg" in
        --auth-key=*)  AUTH_KEY="${arg#*=}" ;;
        --yes|-y)      AUTO_YES=true ;;
        --uninstall)   UNINSTALL=true ;;
        --help|-h)
            echo "$SCRIPT_NAME v$VERSION"
            echo ""
            echo "Install and configure Tailscale on common Linux distros."
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --auth-key=KEY   Tailscale auth key (tskey-...)"
            echo "  --yes, -y       Skip confirmation prompts"
            echo "  --uninstall     Remove Tailscale completely"
            echo "  --help, -h      Show this help"
            echo ""
            echo "If no auth key is provided, the script will install Tailscale"
            echo "and print a login URL for interactive authentication."
            exit 0
            ;;
        *) warn "Unknown option: $arg" ;;
    esac
done

# Detect distro
DISTRO_ID="unknown"
DISTRO_VERSION="unknown"
DISTRO_NAME="Unknown"

detect_distro() {
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        DISTRO_ID="${ID:-unknown}"
        DISTRO_VERSION="${VERSION_ID:-unknown}"
        DISTRO_NAME="${NAME:-Unknown}"
    elif [ -f /etc/redhat-release ]; then
        DISTRO_ID="rhel"
        DISTRO_VERSION="unknown"
        DISTRO_NAME="RHEL-compatible"
    else
        DISTRO_ID="unknown"
        DISTRO_VERSION="unknown"
        DISTRO_NAME="Unknown"
    fi
}

confirm() {
    if [ "$AUTO_YES" = true ]; then return 0; fi
    echo -e "${YELLOW}$1 [y/N]${NC}"
    read -r reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

# Uninstall
do_uninstall() {
    info "Removing Tailscale..."
    case "$DISTRO_ID" in
        ubuntu|debian|linuxmint|pop)
            sudo systemctl stop tailscaled 2>/dev/null || true
            sudo apt-get remove -y tailscale tailscale-archive-keyring 2>/dev/null || true
            sudo rm -f /etc/apt/sources.list.d/tailscale.list
            sudo rm -f /usr/share/keyrings/tailscale-archive-keyring.gpg
            ;;
        fedora)
            sudo systemctl stop tailscaled 2>/dev/null || true
            sudo dnf remove -y tailscale 2>/dev/null || true
            sudo rm -f /etc/yum.repos.d/tailscale.repo
            ;;
        rhel|centos|rocky|alma)
            sudo systemctl stop tailscaled 2>/dev/null || true
            sudo yum remove -y tailscale 2>/dev/null || true
            sudo rm -f /etc/yum.repos.d/tailscale.repo
            ;;
        arch|manjaro|endeavouros)
            sudo systemctl stop tailscaled 2>/dev/null || true
            sudo pacman -Rns --noconfirm tailscale 2>/dev/null || true
            ;;
        *) err "Unsupported distro: $DISTRO_ID"; exit 1 ;;
    esac
    ok "Tailscale removed."
    exit 0
}

# Distros: Ubuntu / Debian / Mint / Pop!_OS
install_ubuntu_debian() {
    info "Adding Tailscale GPG key and repo for $DISTRO_NAME..."
    sudo apt-get update -y
    sudo apt-get install -y curl gnupg
    sudo mkdir -p /usr/share/keyrings
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
        | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list \
        | sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null
    sudo apt-get update -y
    sudo apt-get install -y tailscale
}

# Distros: Fedora
install_fedora() {
    info "Adding Tailscale repo for Fedora..."
    sudo dnf install -y dnf-plugins-core
    sudo dnf config-manager --add-repo https://pkgs.tailscale.com/stable/fedora/tailscale.repo
    sudo dnf install -y tailscale
}

# Distros: RHEL / CentOS / Rocky / Alma
install_rhel() {
    info "Adding Tailscale repo for $DISTRO_NAME..."
    sudo yum install -y yum-utils
    sudo yum-config-manager --add-repo https://pkgs.tailscale.com/stable/rhel/tailscale.repo
    sudo yum install -y tailscale
}

# Distros: Arch / Manjaro / EndeavourOS
install_arch() {
    info "Installing Tailscale from official repo..."
    sudo pacman -Sy --noconfirm tailscale
}

# Main
main() {
    echo -e "${CYAN}"
    echo "  _____          _  ____  _        _ _        ____  _   _ ____  "
    echo " |_   _|__  __ _| |/ ___|| |      (_) |__    / ___|| | | / ___| "
    echo "   | |/ _ \\/ _\` | | |   | |      | | '_ \\  | |    | | | \\___ \\ "
    echo "   | |  __/ (_| | | |___| |___   | | |_) | | |___ | |_| |___) |"
    echo "   |_|\\___|\\__,_|_|\\____|_____|  |_|_.__/   \\____| \\___/|____/ "
    echo -e "${NC}"
    echo "  Quick Setup v$VERSION"
    echo ""

    detect_distro
    info "Detected: $DISTRO_NAME ($DISTRO_ID $DISTRO_VERSION)"

    if [ "$UNINSTALL" = true ]; then
        confirm "Remove Tailscale from this system?" && do_uninstall
        exit 0
    fi

    # Check if already installed
    if command -v tailscale &>/dev/null; then
        TS_VER=$(tailscale version 2>/dev/null | head -1 || echo "unknown")
        warn "Tailscale is already installed ($TS_VER)."
        confirm "Reinstall / update?" || { info "Skipping install."; exit 0; }
    fi

    # Dispatch to the right installer
    case "$DISTRO_ID" in
        ubuntu|debian|linuxmint|pop) install_ubuntu_debian ;;
        fedora)                       install_fedora ;;
        rhel|centos|rocky|alma)       install_rhel ;;
        arch|manjaro|endeavouros)     install_arch ;;
        *)
            err "Unsupported distro: $DISTRO_ID"
            err "Visit https://tailscale.com/download for manual instructions."
            exit 1
            ;;
    esac

    ok "Tailscale package installed!"

    # Enable and start the daemon
    info "Enabling tailscaled service..."
    sudo systemctl enable --now tailscaled
    ok "tailscaled is running."

    # Authenticate
    if [ -n "$AUTH_KEY" ]; then
        info "Authenticating with provided auth key..."
        sudo tailscale up --authkey="$AUTH_KEY" $TAILSCALE_ARGS
        ok "Authenticated and connected!"
    else
        info "Starting interactive login..."
        sudo tailscale up $TAILSCALE_ARGS 2>&1 | tee /tmp/ts-login-url.txt || true
        echo ""
        info "Open the URL above in your browser to complete login."
        info "If the URL scrolled away, check /tmp/ts-login-url.txt"
    fi

    # Show status
    echo ""
    echo -e "${GREEN}--- Tailscale Status ---${NC}"
    sudo tailscale status 2>/dev/null || true
    echo ""
    ok "Setup complete! Run 'tailscale status' anytime to check connectivity."
    info "Useful commands:"
    echo "  tailscale ip          # show your Tailscale IPs"
    echo "  tailscale status      # show connection status"
    echo "  tailscale up --ssh    # enable SSH over Tailscale"
    echo "  tailscale down        # disconnect without uninstalling"
}

main "$@"
