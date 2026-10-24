# docker-prune-cron

Runs scheduled Docker cleanup commands, writes an HTML report, and returns a monitoring exit code.

## Requirements
- Python 3.8 or newer (standard library only).
- The `docker` CLI installed and on your `PATH`.
- A reachable Docker daemon. The script checks this before doing anything.

No pip installs, no virtualenv, no containers required.

## Installation
Drop the single script somewhere on your `PATH` and make it executable:

```bash
sudo curl -fsSL -o /usr/local/bin/docker-prune-cron \
  https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/docker-prune-cron/docker_prune_cron.py
sudo chmod +x /usr/local/bin/docker-prune-cron
docker-prune-cron --version
```

Or clone it if you prefer:

```bash
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/docker-prune-cron
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

AGPL-3.0. See the [root license](../../LICENSE).
