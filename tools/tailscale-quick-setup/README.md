# tailscale-quick-setup

A single shell script to install and configure Tailscale on common Linux distros. Zero dependencies beyond curl and sudo.

## What it does

- Detects your Linux distribution automatically
- Adds the official Tailscale package repository
- Installs Tailscale from the official repo
- Enables and starts the tailscaled service
- Authenticates with an auth key or interactive browser login
- Optionally uninstalls Tailscale cleanly

## Supported distros

- Ubuntu (20.04, 22.04, 24.04+)
- Debian (11, 12+)
- Linux Mint
- Pop!_OS
- Fedora (38, 39, 40+)
- RHEL / CentOS / Rocky / Alma Linux
- Arch Linux / Manjaro / EndeavourOS

## Quick start

One-liner to install and log in interactively:

```bash
curl -sSL https://raw.githubusercontent.com/cappy-dev/tailscale-quick-setup/main/tailscale-quick-setup.sh | bash
```

Or clone and run:

```bash
git clone https://github.com/cappy-dev/tailscale-quick-setup.git
cd tailscale-quick-setup
bash tailscale-quick-setup.sh
```

## Usage

```bash
bash tailscale-quick-setup.sh [OPTIONS]
```

Options:

- `--auth-key=KEY` Authenticate non-interactively with a Tailscale auth key
- `--yes, -y` Skip all confirmation prompts
- `--uninstall` Remove Tailscale and clean up repo files
- `--help, -h` Show help text

## Examples

Install and log in via browser:

```bash
bash tailscale-quick-setup.sh
```

Install with an auth key (great for headless servers):

```bash
bash tailscale-quick-setup.sh --auth-key=tskey-auth-xxxxx
```

Install without any prompts:

```bash
bash tailscale-quick-setup.sh --auth-key=tskey-auth-xxxxx --yes
```

Uninstall Tailscale:

```bash
bash tailscale-quick-setup.sh --uninstall
```

## After installation

Once Tailscale is running, try these commands:

```bash
tailscale status        # check connection status
tailscale ip           # show your Tailscale IP addresses
tailscale up --ssh     # enable SSH access over Tailscale
tailscale down         # disconnect without uninstalling
```

## How it works

1. Reads `/etc/os-release` to identify your distro
2. Adds the correct Tailscale GPG key and apt/yum/pacman repo
3. Installs the `tailscale` package from the official source
4. Runs `systemctl enable --now tailscaled`
5. Calls `tailscale up` with your auth key or opens a browser login

## Requirements

- A supported Linux distribution
- `sudo` privileges
- `curl` (installed automatically on Debian/Ubuntu if missing)

## License

MIT
