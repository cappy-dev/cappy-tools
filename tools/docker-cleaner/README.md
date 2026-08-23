# docker-cleaner

Reclaim disk space from unused Docker objects with a safe dry-run by default.

`docker-cleaner.sh` prunes stopped containers, dangling images, orphaned networks, and (optionally) unused volumes and build cache. It shows you exactly what would be removed first, then only deletes when you explicitly say so.

Zero dependencies beyond `bash`, `docker`, and standard coreutils. Works on Linux and macOS.

## Why

Docker accumulates stopped containers, dangling images, orphaned networks, and stale build cache over time. The built-in `docker system prune` is powerful but easy to run carelessly. This script:

- Runs a **dry-run by default** so you preview every deletion first.
- Prints a clear **disk usage summary** before and after.
- Separates destructive options (volumes, all images) behind explicit flags.
- Uses friendly, colorized output and prints the exact command for each step.

## Install

Clone or copy the single script. No build step, no runtime dependencies.

```bash
git clone https://github.com/cappy-dev/docker-cleaner.git
cd docker-cleaner
chmod +x docker-cleaner.sh
```

Or download just the script:

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/docker-cleaner/main/docker-cleaner.sh
chmod +x docker-cleaner.sh
```

## Usage

```bash
./docker-cleaner.sh                 # dry-run, preview what would be removed
./docker-cleaner.sh --apply          # delete unused containers, dangling images, networks
./docker-cleaner.sh --apply --all    # also remove unused volumes and unreferenced images
./docker-cleaner.sh --apply --keep-images  # skip images, prune the rest
./docker-cleaner.sh --help
```

## Options

- `--apply`: Actually delete objects. Without this flag nothing is removed.
- `--all`: Aggressive mode. Also prunes unused volumes and images not referenced by a running container. Must be combined with `--apply` to take effect.
- `--keep-images`: Skip image pruning entirely. Still prunes containers, volumes (with `--all`), and networks.
- `--quiet`: Suppress non-error output.
- `--help`: Show help and exit.
- `--version`: Print version.

## Examples

### Preview only, nothing changes

```bash
$ ./docker-cleaner.sh
== docker-cleaner.sh v1.0.0 ==
DRY-RUN MODE (nothing will be deleted). Pass --apply to commit.

Docker disk usage summary
type                 count      size        reclaimable
Images               24         3.2GB       1.8GB (56%)
Containers           7          120MB       60MB (50%)
Local Volumes        5          800MB       0B (0%)
Build Cache          18         1.1GB       1.1GB

[DRY-RUN] prune stopped containers
command: docker container prune --force
...
```

### Apply a safe cleanup (no volumes)

```bash
./docker-cleaner.sh --apply
```

### Aggressive cleanup including unused volumes

`--all` also removes unused volumes, which can delete data in volumes not mounted by a running container. Always do a dry-run first.

```bash
./docker-cleaner.sh          # review what --all would remove
./docker-cleaner.sh --apply --all
```

### Skip image pruning

If you want to keep all images (for example on a slow connection) but still free the rest:

```bash
./docker-cleaner.sh --apply --keep-images
```

## What it prunes

For each pruning step, here is what happens under each flag combination:

- **Stopped containers:** pruned in every mode (`--apply`, `--all`, `--keep-images`).
- **Build cache:** pruned in every mode.
- **Dangling images:** pruned by default and with `--all` (which also drops unreferenced tagged images). Skipped when `--keep-images` is passed.
- **Orphaned networks (not used by any container):** pruned in every mode.
- **Unused volumes:** skipped by default. Pruned only when `--all` is combined with `--apply`. Even with `--keep-images`, passing `--all` still prunes volumes.

## Scheduling

To free space nightly without prompting, with a harmless dry-run left out, you can add to a root crontab. Only do this if you accept the deletions:

```cron
0 3 * * * /path/to/docker-cleaner.sh --apply --quiet
```

For aggressive nightly cleanup (use caution with volumes):

```cron
0 3 * * * /path/to/docker-cleaner.sh --apply --all --quiet
```

## Permissions

The script needs access to the Docker daemon. If you can run `docker ps` you can run this script. On systems that require `sudo`, invoke via `sudo ./docker-cleaner.sh` or ensure your user is in the `docker` group.

## Safety

- Dry-run is the default. Nothing is deleted unless you pass `--apply`.
- Volume pruning is hidden behind `--all` because removing unused volumes can destroy data the daemon does not know about.
- The script never force-pushes, never touches another git repo, and contains no network calls beyond the Docker socket.
- No personal information, API keys, or identifying details are stored.

## License

MIT. See `LICENSE`.
