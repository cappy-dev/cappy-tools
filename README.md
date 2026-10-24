# Cappy tools

Standalone tools for Linux administration, backups, networking, and AI APIs. Each directory contains its own usage guide. Requirements vary by tool.

## Use a tool

```sh
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/ssh-key-audit
```

Follow the README in the selected directory. Read the options before running commands that delete files, change system configuration, or install packages.

## Tools

- [`backup-verify`](tools/backup-verify). Checks backup archives for unreadable data, invalid structure, and checksum mismatches. Supports tar and zip archives, with an optional extraction check.
- [`cert-clock`](tools/cert-clock). Checks TLS certificate expiration dates from remote hosts or local PEM files. Uses Python standard-library modules and returns exit codes for monitoring.
- [`cron-doctor`](tools/cron-doctor). Checks crontab files for syntax errors, missing commands, overlapping schedules, and disabled jobs.
- [`cron-pulse`](tools/cron-pulse). Records job heartbeats and reports scheduled jobs that missed their expected window.
- [`cron-validator`](tools/cron-validator). Validates five-field cron expressions, explains their fields, and previews upcoming run times.
- [`cron-watchdog`](tools/cron-watchdog). Records successful cron runs and reports jobs that missed their expected window.
- [`cross-shell-prompt`](tools/cross-shell-prompt). A Bash and Zsh prompt showing the previous exit status, current Git branch, and working directory.
- [`disk-sentinel`](tools/disk-sentinel). Checks disk space, SMART health, drive temperatures, and selected SMART attributes on Linux.
- [`disk-watchdog`](tools/disk-watchdog). Monitors filesystem usage and SMART health, with configurable log, webhook, email, and command alerts.
- [`disk-xray`](tools/disk-xray). Reports directory sizes and large files in a terminal tree or an HTML report. Uses the Python standard library.
- [`dns-diff`](tools/dns-diff). Compares DNS records between two nameservers for provider migrations and configuration checks.
- [`dns-torch`](tools/dns-torch). Checks DNS records, TLS certificates, email configuration, blocklists, and HTTP headers. Requires Python and dig.
- [`dns-update-host`](tools/dns-update-host). Updates a DNS A or AAAA record when the host's public IP address changes.
- [`docker-cleaner`](tools/docker-cleaner). Reports unused Docker objects and removes them only when explicitly requested. Dry-run is the default.
- [`docker-health-watch`](tools/docker-health-watch). Monitors Docker container health through the local Docker socket and sends JSON alerts when health changes.
- [`docker-prune-cron`](tools/docker-prune-cron). Runs scheduled Docker cleanup commands, writes an HTML report, and returns a monitoring exit code.
- [`docker-volume-backup`](tools/docker-volume-backup). Archives Docker named volumes with checksums and optional Docker Compose coordination.
- [`dup-scout`](tools/dup-scout). Finds duplicate files by comparing their sizes and SHA-256 hashes.
- [`eden-launcher`](tools/eden-launcher). Downloads, caches, and launches the latest Eden nightly Linux amd64 PGO AppImage from its official release feed.
- [`git-backup-lite`](tools/git-backup-lite). Backs up local Git repositories with git bundle and copies bare repositories into timestamped backup runs.
- [`git-repo-sweeper`](tools/git-repo-sweeper). Reports uncommitted changes, unpushed commits, and stale branches across local Git repositories.
- [`git-snapshot`](tools/git-snapshot). Creates timestamped tar archives of Git repositories, including the branch and commit in the filename.
- [`homelab-backup-rotator`](tools/homelab-backup-rotator). Prunes backup files by glob pattern and retention count. Run with --dry-run before allowing deletion.
- [`homelab-ports`](tools/homelab-ports). Scans selected TCP ports and labels common self-hosted services. Scan only systems you own or have permission to test.
- [`homelab-status`](tools/homelab-status). Checks HTTP endpoints and TCP ports, then writes a self-contained HTML status page.
- [`jmail-client`](tools/jmail-client). A Python client for the public Jmail Data API. See the API documentation for current access requirements and limits.
- [`jmail-python`](tools/jmail-python). A Python client for the public Jmail Data API. See the API documentation for current access requirements and limits.
- [`llama-cpp-benchmark`](tools/llama-cpp-benchmark). Runs llama-cli against GGUF models and compares its reported timing measurements. Requires Python and a working llama.cpp build.
- [`llm-bench-lite`](tools/llm-bench-lite). Measures time to first token, tokens per second, and request latency for OpenAI-compatible APIs.
- [`log-rotator`](tools/log-rotator). Rotates, compresses, and prunes local log files by size, count, and age.
- [`log-shark`](tools/log-shark). Summarizes syslog, journalctl JSON, Nginx, and Apache logs, including errors, client addresses, and HTTP status codes.
- [`markdown-to-static`](tools/markdown-to-static). Converts Markdown files to HTML using a template and writes them to a build directory.
- [`mem-sentinel`](tools/mem-sentinel). Reads Linux memory and swap usage from /proc/meminfo and reports configurable threshold breaches.
- [`net-pulse`](tools/net-pulse). Checks connection latency, DNS resolution, HTTP reachability, and download throughput, with an optional HTML report.
- [`nim-api-watchdog`](tools/nim-api-watchdog). Checks NVIDIA NIM availability and reports recovery so interrupted Hermes Agent tasks can resume.
- [`nvidia-nim-tools`](tools/nvidia-nim-tools). Lists NVIDIA NIM models, sends completion requests, and generates embeddings from the command line. Access depends on your API key and provider limits.
- [`port-warden`](tools/port-warden). Audits listening TCP and UDP ports on Linux and flags services that may be unsafe to expose.
- [`restart-hawk`](tools/restart-hawk). Tracks process IDs across runs and reports processes that restarted or disappeared.
- [`simple-cert-check`](tools/simple-cert-check). Checks TLS certificate expiration dates and reports certificates nearing expiry.
- [`ssh-key-audit`](tools/ssh-key-audit). Audits authorized keys, SSH client configuration, and SSH daemon settings for common security problems.
- [`ssh-quick-setup`](tools/ssh-quick-setup). Generates SSH keys, deploys public keys to remote hosts, and applies SSH daemon hardening settings. Review the settings before applying them.
- [`ssl-cert-checker`](tools/ssl-cert-checker). Checks TLS certificate expiration dates for one or more hostnames and returns warning exit codes.
- [`systemd-timer-monitor`](tools/systemd-timer-monitor). Audits systemd services and timers and writes an HTML report of failures and scheduling problems.
- [`tailscale-acl-audit`](tools/tailscale-acl-audit). Checks Tailscale ACL files for common policy mistakes without sending the policy over the network.
- [`tailscale-quick-setup`](tools/tailscale-quick-setup). Installs and configures Tailscale using the official package repositories. Requires curl and sudo.
- [`ufw-bulwark`](tools/ufw-bulwark). Audits UFW rules and defaults for potentially unsafe exposure of network services.
- [`update-herald`](tools/update-herald). Reports available Linux package updates and security patches, with configurable notifications.

## Unimplemented entries

- [`csv-pretty`](tools/csv-pretty). Its former repository was empty. No working formatter is included.

## Maintenance

Run `python3 scripts/check_tools.py` to check documentation links, license placement, and Python and shell syntax. Tool-specific tests and usage examples stay in their directories.

The migrated repositories retain their original commit ancestry. `migrations.json` records the source commits.

## License

AGPL-3.0. See [LICENSE](LICENSE) for the complete text. Existing package metadata that permits later AGPL versions still applies. Earlier releases keep their original license terms.
