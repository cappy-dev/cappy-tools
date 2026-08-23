# docker-prune-cron

A tiny, zero-dependency Python script that prunes Docker layers safely and
on a schedule. It runs the right `docker ... prune` commands for you, prints
cron-friendly log lines, writes an HTML report, and exits with a status code
that tells you whether everything worked.

Made for homelab operators who let Docker eat disk space for weeks and then
wonder where 40 GB went.

## Why

`docker system prune -a --volumes` is a sledgehammer. It can remove images you
wanted to keep, nuke named volumes with real data, and gives you no record of
what happened. `docker-prune-cron` is calmer:

- **Dry run by default.** Nothing is deleted unless you pass `--apply`.
- **Age filters.** Keep images and stopped containers newer than N hours. Only
  older junk gets reclaimed.
- **Volumes are opt-in to delete.** Use `--no-volumes` to never touch them.
- **HTML report.** Drop the generated file somewhere web-served and glance at
  it from your phone.
- **Cron exit codes.** `0` clean, `1` partial failure, `2` Docker unreachable,
  `3` bad args. Wire this straight into your alerting.

## Requirements

- Python 3.8 or newer (standard library only).
- The `docker` CLI installed and on your `PATH`.
- A reachable Docker daemon. The script checks this before doing anything.

No pip installs, no virtualenv, no containers required.

## Installation

Drop the single script somewhere on your `PATH` and make it executable:

```bash
sudo curl -fsSL -o /usr/local/bin/docker-prune-cron \
  https://raw.githubusercontent.com/cappy-dev/docker-prune-cron/main/docker_prune_cron.py
sudo chmod +x /usr/local/bin/docker-prune-cron
docker-prune-cron --version
```

Or clone it if you prefer:

```bash
git clone https://github.com/cappy-dev/docker-prune-cron.git
cd docker-prune-cron
python3 docker_prune_cron.py --help
```

## Usage

See what WOULD be pruned, delete nothing:

```bash
python3 docker_prune_cron.py
```

Actually prune, keeping images and containers newer than 168 hours (7 days):

```bash
python3 docker_prune_cron.py --apply --hours 168 --force
```

Prune everything except volumes (safest useful mode):

```bash
python3 docker_prune_cron.py --apply --no-volumes --force
```

Write the HTML report to a folder served by a web server:

```bash
python3 docker_prune_cron.py --apply \
  --report /var/www/html/docker-prune.html \
  --hours 168 --force
```

Be quiet unless something breaks (good for cron):

```bash
python3 docker_prune_cron.py --apply --hours 168 --force --quiet
```

## Flags

```
--apply          actually delete (default is dry run)
--force          skip the 10 second safety pause before applying
--hours N        keep images/containers newer than N hours (default 72)
--no-volumes     skip pruning volumes entirely
--no-build-cache skip pruning the build cache
--report PATH    where to write the HTML report (default docker-prune-report.html)
--quiet          suppress normal log lines; warnings still print
--version        print version and exit
```

## Cron example

Prune every Sunday at 03:00, keep recent images, log to a file:

```cron
0 3 * * 0  /usr/bin/python3 /usr/local/bin/docker_prune_cron.py \
  --apply --hours 168 --force --quiet \
  --report /var/www/html/docker-prune.html \
  >> /var/log/docker-prune.log 2>&1
```

Pair it with a failure alert (for example on non-zero exit) so you get pinged
the moment the daemon is unreachable or a layer fails.

## Exit codes

- `0` success, or a clean dry run
- `1` pruning ran but at least one layer failed
- `2` Docker CLI missing or daemon unreachable
- `3` bad arguments

## How the pruning works

For each layer the script runs the matching prune command:

| Layer | Command |
| --- | --- |
| containers (stopped) | `docker container prune --force --filter until=Nh` |
| images (dangling + unused older than N) | `docker image prune --force --filter until=Nh` |
| networks (unused) | `docker network prune --force` |
| volumes (unused) | `docker volume prune --force` (unless `--no-volumes`) |
| build cache | `docker builder prune --force --filter until=Nh` (unless `--no-build-cache`) |

In dry-run mode every command gets a `--dry-run` flag appended so Docker tells
you what it plans to reclaim without deleting anything. Running containers and
volumes that are in use are never removed by Docker itself, and the age filter
adds an extra layer of safety.

## Report

The HTML report shows the Docker server version, disk usage before and after,
and the output of each prune layer in a table. Point your browser at the file
or serve the folder to check results from anywhere.

## License

MIT. See `LICENSE`.
