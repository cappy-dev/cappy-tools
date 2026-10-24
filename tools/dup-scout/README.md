# dup-scout

Finds duplicate files by comparing their sizes and SHA-256 hashes.

## Features
- Zero dependencies. Runs on any system with Python 3.8 or newer.
- Three-phase pipeline (size, partial hash, full hash) for speed.
- Output as plain text, JSON, or CSV.
- Optional `--delete` mode with `--dry-run` preview and `--keep` to
  choose which copy survives (first or last, sorted alphabetically).
- `--min-size` filter to skip tiny files (e.g. `1M` ignores anything
  under one mebibyte).
- `--exclude` to skip directory names like `.git`, `node_modules`, or
  `.venv` without descending into them.
- Cron-friendly exit codes.

## Install

No build step. Just grab the script and run it:

```
curl -O https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/dup-scout/dup_scout.py
chmod +x dup_scout.py
```

Or clone the repo:

```
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/dup-scout
python3 dup_scout.py --help
```

## Usage
```
python3 dup_scout.py PATH [PATH...] [options]
```

Scan a single directory and print a human-readable report:

```
python3 dup_scout.py /mnt/media
```

Scan multiple roots at once:

```
python3 dup_scout.py /mnt/media /home/me/Downloads /backup/photos
```

Only look at files of at least 1 MiB and skip version control folders:

```
python3 dup_scout.py /home/me --min-size 1M --exclude '.git,node_modules,.venv'
```

Write a JSON report to a file:

```
python3 dup_scout.py /mnt/media --format json --output report.json
```

Export CSV for spreadsheets:

```
python3 dup_scout.py /mnt/media --format csv --output dupes.csv
```

Preview what would be removed, keeping the first (alphabetically
smallest) copy of each duplicate group:

```
python3 dup_scout.py /mnt/media --delete --dry-run --keep first
```

Actually delete duplicates, this time keeping the last copy:

```
python3 dup_scout.py /mnt/media --delete --keep last
```

Suppress progress messages for clean cron logs:

```
python3 dup_scout.py /mnt/media --quiet --format json --output /var/log/dupes.json
```

## Exit codes

- `0` no duplicate groups were found
- `1` at least one duplicate group was found
- `2` usage error or scan failure

These codes make it easy to wire `dup_scout` into a cron job or health
check: a non-zero exit from anything other than "duplicates found"
means something is actually wrong.

## Output formats

### text (default)

```
Duplicate report: 2 group(s)
Potential space savings (1 file per group): 97.7k

Group 1: 97.7k each, 2 copies, digest a852686cc256...
  /mnt/media/big1.bin
  /mnt/media/big2.bin

Group 2: 22B each, 3 copies, digest 4e2a8727b4a2...
  /mnt/media/a.txt
  /mnt/media/b.txt
  /mnt/media/sub/c.txt
```

Groups are sorted largest first, paths sorted alphabetically within each
group.

### json

```json
{
  "groups": [
    {
      "digest": "a852686cc2562f59aa29f23415a1e3f73aacc8ef3095ba8ff71688e2d31fba28",
      "size": 100000,
      "paths": [
        "/mnt/media/big1.bin",
        "/mnt/media/big2.bin"
      ]
    }
  ],
  "total_groups": 1,
  "potential_savings_bytes": 100000,
  "potential_savings_human": "97.7k"
}
```

### csv

Columns are `group,digest,size_bytes,path`, one row per duplicate file.

## How it stays fast

On a million-file tree, the vast majority of files have a unique size and
are eliminated in phase 1 with a single `stat()` call and no disk reads.
Of the remainder, most size collisions differ in the first 64 KiB, so
phase 2 spends a tiny fraction of the I/O that a naive full-hash would
need. Phase 3 only runs on files that still look identical after the
first two passes, which is typically a very small set.

## Safety

`--delete` without `--dry-run` is real and irreversible (the script
calls `os.remove`). The tool prints a note to stderr reminding you to
preview with `--dry-run` first when you skip the preview. It always
keeps one copy per group, chosen by `--keep` (default `first`).

The script never follows symlinks unless you pass `--follow-links`, and
it never deletes files it has not hashed with the full SHA-256.

## Limitations
- Only filesystems that expose stable device and inode numbers get the
  hardlink dedup optimization. On filesystems that lack it (some network
  mounts), the inode check is skipped automatically.
- Generates the full file list in memory before printing results. For
  extremely large trees (tens of millions of candidates) you may want
  to scan per-subdirectory instead.

## License

AGPL-3.0. See the [root license](../../LICENSE).
