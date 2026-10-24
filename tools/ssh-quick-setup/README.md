# ssh-quick-setup

A single shell script to generate SSH keys, deploy them to remote hosts, and harden the local SSH daemon. Zero dependencies beyond bash and coreutils.

## What it does

- **keygen**: Generates a strong Ed25519 key pair with 100 key derivation rounds
- **deploy**: Copies your public key to a remote host via SSH pipe
- **harden**: Locks down sshd_config (disables password auth, restricts root login, sets modern algorithms)
- **status**: Shows a quick summary of SSH configuration, keys, and backups
- **rollback**: Restores sshd_config from the most recent backup
- **dry-run**: Preview every change before committing to it
- **custom port**: Optionally relocate the SSH listening port during hardening

No pip packages, no Node, no extra dependencies. Just Bash.

## Supported distros

Tested on Debian, Ubuntu, Fedora, RHEL/Rocky/Alma, and Arch. Any Linux system with OpenSSH and systemd works.

## Quick start

Clone and make it executable:

```bash
git clone https://github.com/cappy-dev/ssh-quick-setup.git
cd ssh-quick-setup
chmod +x ssh-quick-setup.sh
```

### Step 1: Generate a key

```bash
./ssh-quick-setup.sh keygen --comment "homelab"
```

This creates `~/.ssh/id_ed25519` (private) and `~/.ssh/id_ed25519.pub` (public). Ed25519 keys are shorter, faster, and at least as secure as RSA 3072.

### Step 2: Deploy to a host

```bash
./ssh-quick-setup.sh deploy 192.168.1.50 --user admin
```

This copies your public key to `admin@192.168.1.50` so you can log in without a password.

### Step 3: Harden the local SSH daemon

```bash
sudo ./ssh-quick-setup.sh harden
```

This makes the following changes to `/etc/ssh/sshd_config`:

- `PasswordAuthentication no` (key-based auth required)
- `PermitRootLogin prohibit-password` (root can use keys, not passwords)
- `PubkeyAuthentication yes`
- `ChallengeResponseAuthentication no`
- `X11Forwarding no`
- Modern `KexAlgorithms` (Curve25519 and SHA-512 groups)
- Modern `Ciphers` (ChaCha20-Poly1305 and AES-GCM)
- Modern `MACs` (HMAC-SHA-2 and UMAC in ETM mode)

### Step 4: Verify

```bash
./ssh-quick-setup.sh status
```

Prints the effective values of every security-relevant directive, lists your SSH keys, and counts backups.

## Commands

```bash
./ssh-quick-setup.sh keygen [options]
./ssh-quick-setup.sh deploy HOST [options]
./ssh-quick-setup.sh harden [options]
./ssh-quick-setup.sh status
./ssh-quick-setup.sh rollback [options]
./ssh-quick-setup.sh help
```

## Options

- `--key FILE`: Use a specific private key file (default `~/.ssh/id_ed25519`)
- `--port PORT`: Bind SSH to a non-standard port during hardening
- `--comment TEXT`: Comment string for the generated key
- `--user USER`: Remote username for deploy (default: current user)
- `--dry-run`: Print every action without executing anything
- `--yes, -y`: Skip all confirmation prompts
- `--help, -h`: Show help text

## Examples

### Generate a key with a custom comment

```bash
./ssh-quick-setup.sh keygen --comment "me@laptop"
```

### Deploy a specific key

```bash
./ssh-quick-setup.sh deploy web.example.com --user deploy --key ~/.ssh/id_deploy
```

### Harden with a non-standard port

```bash
sudo ./ssh-quick-setup.sh harden --port 2222
```

Update your firewall after changing the port:

```bash
sudo ufw allow 2222/tcp
sudo firewall-cmd --add-port=2222/tcp --permanent && sudo firewall-cmd --reload
```

### Preview changes without applying them

```bash
sudo ./ssh-quick-setup.sh harden --dry-run
```

Every change prints as a `[DRY]` line and nothing is modified.

### Roll back a bad change

```bash
sudo ./ssh-quick-setup.sh rollback
```

Restores the most recent backup from `/etc/ssh/backups/` and reloads sshd.

### Skip prompts for automation

```bash
./ssh-quick-setup.sh deploy 10.0.0.5 --user deploy --yes
```

## Safety features

- **Automatic backup**: Before hardening, the script copies `sshd_config` to `/etc/ssh/backups/sshd_config.<timestamp>.bak`
- **Syntax validation**: After editing, the script runs `sshd -t` to check config syntax. If it fails, the original config is restored immediately
- **Your session stays open**: The script reloads the service instead of restarting, so your active SSH session is never dropped
- **Dry-run everywhere**: Every command respects `--dry-run`, so you can inspect the full plan beforehand

## Security notes

After hardening:

- Password authentication is disabled. You must use SSH keys.
- Root login requires keys. Password login is blocked.
- Only modern key exchange and cipher algorithms are allowed.
- X11 forwarding is disabled by default.
- Keep your current session open when testing changes.
- If you lose access, run `rollback` to restore the previous config.

## License

MIT