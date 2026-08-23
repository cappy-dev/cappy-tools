# docker-volume-backup

A small, dependency-light backup tool for Docker named volumes. It dumps each
volume to a gzip-compressed tar archive using a throwaway `busybox` container,
writes a SHA-256 checksum next to every archive, and optional containers from a
Docker Compose project while the snapshot is taken.

Everything you need is `docker`, `tar`, `gzip` and `sha256sum`, which ship with
every mainstream Linux distribution. No Python, no cron daemon, no extra
back-end.

## Why

Named volumes are the right place to put data a container must own, but they are
invisible to the host filesystem and easy to forget in a backup plan. This
script makes a clean, restorable snapshot of any volume with one command and
keeps the last N copies for you so the disk does not fill up.

## Install

1. Make sure Docker Engine is installed and your user can run `docker`.
2. Clone or copy the single script.

```
git clone https://github.com/cappy-dev/docker-volume-backup.git
cd docker-volume-backup
chmod +x docker-volume-backup.sh
```

There is nothing to build or install. Run it from anywhere.

## Usage

```
./docker-volume-backup.sh [options] <volume> [<volume> ...]
```

Options:

- `-d <dir>`     Output directory (default `./backups`)
- `-k <n>`       Keep last N backups per volume, prune older (default 7, 0 off)
- `-p <prefix>`  File name prefix (default `vol`)
- `-z <level>`   gzip level 1 to 9 (default 6)
- `-g <pgid>`    Docker Compose project id to pause during backup, then resume
- `-v`           Verbose log output to stderr
- `-n`           Dry run, show what would happen
- `-h`           Show the built-in help

## Examples

Back up one volume to the default directory:

```
./docker-volume-backup.sh pg_data
```

Back up three volumes into a shared archive dir and keep 14 copies each:

```
./docker-volume-backup.sh -d /mnt/backups -k 14 pg_data app_uploads redis_data
```

Back up all volumes whose name starts with `app_`:

```
./docker-volume-backup.sh $(docker volume ls -q --filter name=app_)
```

Pause a Compose stack while its volumes are snapshotted by passing the project
id (the value of the `com.docker.compose.project` label, which is usually the
directory name the compose file lives in):

```
./docker-volume-backup.sh -g myapp app_db app_config
```

Dry run to preview what would be written and pruned:

```
./docker-volume-backup.sh -n -v pg_data
```

## Restore

To restore a backup into a volume (empty or not):

```
docker run --rm \
  -v pg_data:/target \
  -v /mnt/backups/vol-pg_data-20260101-120000.tar.gz:/backup.tar.gz:ro \
  busybox:stable sh -c 'cd /target && tar xzf /backup.tar.gz'
```

The checksum file lets you confirm the archive is intact first:

```
cd /mnt/backups
sha256sum -c vol-pg_data-20260101-120000.tar.gz.sha256
```

## Automate with cron

A daily backup at 2:30 keeping 30 days, with a run log:

```
30 2 * * * /home/me/bin/docker-volume-backup.sh -d /mnt/backups -k 30 pg_data app_uploads >> /var/log/dvb.log 2>&1
```

## How it works

For each volume the script runs a throwaway `busybox:stable` container that
mounts the volume read-only at `/source` and a temp file for output at
`/out.tar.gz`. It runs `tar cNf /out.tar.gz -C /source .` inside that container
so the archive is written atomically. The temp file is moved into the output
directory only after tar succeeds, so a failed or interrupted run never leaves a
half-written archive behind. A SHA-256 file is written next to every archive so
you can verify integrity later.

When you pass `-g <project>` the script pauses every container that carries the
`com.docker.compose.project=<project>` label, takes the snapshot, then resumes
those same containers. This gives you a consistent point-in-time picture of data
that a running service is actively writing to.

The retention pass keeps the N newest archives per volume (based on file mtime)
and deletes both the archive and its `.sha256` sidecar for anything older.

## Limitations

- Backs up named volumes only, not bind mounts or anonymous volumes.
- An entire volume is snapshotted each run. There is no incremental mode.
- The busybox image is pulled on first use; the host must reach a registry.
- Volume contents are tarred as root, so file ownership inside the archive
  reflects what the container sees. Restore as root to preserve owners.

## License

MIT, see [LICENSE](LICENSE).
