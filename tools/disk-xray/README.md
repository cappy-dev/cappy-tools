# disk-xray

Reports directory sizes and large files in a terminal tree or an HTML report. Uses the Python standard library.

## Features
- Fast tree view of directories ranked by size, with proportional bars
- Shows the largest individual files in the scanned tree
- Optional HTML report with sortable-style tables and progress bars
- Ignore directories by name or path prefix
- Size threshold filter: hide entries smaller than a given amount
- Follow-symlinks and hard-link counting options
- Human-readable sizes (KB, MB, GB, TB)
- Cron-friendly exit codes (0 OK, 2 access errors, 130 interrupted)
- Single file. No dependencies beyond Python 3.8 and the standard library.

## Quick start

```sh
python3 disk_xray.py /usr/lib
```

Output:

```
Root:    /usr/lib
Total:   1.8 GB
Files:   22,468
Dirs:    3,598

lib                              1.8 GB [####################]
+-- x86_64-linux-gnu                 726 MB [########............]
|   +-- perl                            24.4 MB [#...................]
|   +-- dri                             14.8 MB [....................]
|   --- gconv                            7.8 MB [....................]
+-- jvm                              329 MB [####................]
|   --- java-25-openjdk-amd64            329 MB [####################]
+-- modules                          298 MB [###.................]
|   +-- 6.17.0-1018-oracle               147 MB [##########..........]
|   --- 6.17.0-1019-oracle               147 MB [##########..........]
--- snapd                            104 MB [#...................]

Top 10 largest individual files:
      138 MB  /usr/lib/jvm/java-25-openjdk-amd64/lib/modules
      137 MB  /usr/lib/x86_64-linux-gnu/libLLVM.so.20.1
```

## Installation
No installation needed. Just download the single script and run it.

```sh
curl -LO https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/disk-xray/disk_xray.py
chmod +x disk_xray.py
./disk_xray.py --help
```

Or clone the repo:

```sh
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/disk-xray
python3 disk_xray.py .
```

## Usage
```
usage: disk-xray [-h] [-n TOP] [-d DEPTH] [-f BIG_FILES] [-L]
                 [--count-hardlinks] [-i IGNORE] [--threshold THRESHOLD]
                 [--html FILE] [--progress] [-q] [paths ...]

Zero-dependency disk usage analyzer. Find the biggest space hogs in a
directory tree.

positional arguments:
  paths                 one or more directories to scan (default: .)

options:
  -h, --help            show this help message and exit
  -n TOP, --top TOP     show top N entries per level (default: 10)
  -d DEPTH, --depth DEPTH
                        max tree depth (default: 3)
  -f BIG_FILES, --big-files BIG_FILES
                        also show the N largest individual files (default: 10,
                        use 0 to disable)
  -L, --follow-symlinks follow symbolic links (default: off)
  --count-hardlinks     count every hard link (default: count each shared
                        inode once, like du)
  -i IGNORE, --ignore IGNORE
                        ignore directory name or path prefix (repeatable)
  --threshold THRESHOLD
                        hide entries smaller than this (e.g. 100M, 1G)
  --html FILE           also write an HTML report to FILE
  --progress            print scan progress to stderr
  -q, --quiet           suppress the summary header
  --version             show program's version number and exit
```

## Examples
Scan the home directory, show top 15 directories, drill 4 levels deep:

```sh
python3 disk_xray.py ~ -n 15 -d 4
```

Scan a server, ignore cache and temp dirs, skip the big-files list:

```sh
python3 disk_xray.py /var --ignore .cache --ignore .config --ignore tmp -f 0
```

Only show entries larger than 100 MB:

```sh
python3 disk_xray.py / --threshold 100M
```

Generate an HTML report for a weekly disk audit (great for cron):

```sh
python3 disk_xray.py /var --html /var/www/disk-report.html -q
```

Scan multiple paths in one command:

```sh
python3 disk_xray.py /var/log /tmp /home
```

Follow symlinks into mounted volumes:

```sh
python3 disk_xray.py /mnt/tank --follow-symlinks -d 5
```

## Use case: weekly cron job

Drop this into a crontab to email yourself a disk report every Sunday:

```sh
# Every Sunday at 6 AM: email disk usage report
0 6 * * 0 /path/to/disk_xray.py /var /home --html /tmp/disk-report.html -q -f 20 \
    && mail -s "Weekly disk usage report" you@example.com < /tmp/disk-report.html
```

Or just save the text output:

```sh
0 6 * * 0 /path/to/disk_xray.py / --threshold 1G -f 20 > /tmp/disk-weekly.txt
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Scan completed with no access errors |
| 1 | Bad usage (invalid arguments) |
| 2 | Scan completed but some directories were unreadable (permission denied) |
| 130 | Interrupted by Ctrl-C |

## How sizes are computed

- Directory sizes are the sum of all files beneath them (recursively).
- Symlinks contribute zero by default (they are not the data). Use
  `--follow-symlinks` if you want the target's size counted.
- Hard-linked files are counted once per inode by default (same as `du`).
  Use `--count-hardlinks` to count every link reference.
- Files that cannot be stat-ed (permission denied, broken links) are skipped
  and increment the error counter.

## Why no dependencies?

Because you should be able to run a disk analyzer on a minimal server, a
rescue shell, or a fresh Pi without installing anything. Just Python 3.8 and
the standard library. Nothing else.

## License

AGPL-3.0. See the [root license](../../LICENSE).
