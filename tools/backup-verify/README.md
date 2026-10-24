# backup-verify

Checks backup archives for unreadable data, invalid structure, and checksum mismatches. Supports tar and zip archives, with an optional extraction check.

## Features
- Validates gzip CRC, zip CRC32, tar structure, bzip2 stream, and xz headers
- Detects truncated and zero-byte files that other tools silently accept
- Optional SHA256 comparison against a checksum file (shasum -c format or CSV)
- Optional `--extract` test that unpacks into a temp directory to confirm restorability
- Directory scanning automatically picks up common backup file extensions
- Glob patterns work too (`/backups/nightly-*.tar.gz`)
- JSON output mode for wiring into monitoring systems
- Cron-friendly exit codes (see below)
- Pure Python 3.8+ standard library. No pip. No venv. No dependencies.

## Requirements
- Python 3.8 or newer
- Nothing else. Uses only the standard library.

## Installation
Copy the single script wherever you like and make it executable:

```bash
curl -O https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/backup-verify/backup_verify.py
chmod +x backup_verify.py
```

Or just clone the repo:

```bash
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/backup-verify
```

## Usage
### Verify every archive in a directory

```bash
python3 backup_verify.py /backups
```

Output (colorized when run in a terminal):

```
PASS  nightly.tar.gz  (2048.0 KB)
     + readable: ok
     + archive: gzip CRC ok
FAIL  weekly.tar.gz  (50.0 KB)
     + readable: ok
     + archive: gzip error: Compressed file ended before the end-of-stream marker was reached

FAIL  1 passed, 1 failed of 2 total
```

### Verify a single file

```bash
python3 backup_verify.py /backups/nightly.tar.gz
```

### Use a glob pattern

```bash
python3 backup_verify.py "/backups/2026-*.tar.gz"
```

### Compare against a checksum file

Generate checksums alongside your backups:

```bash
cd /backups
sha256sum *.tar.gz > checksums.txt
```

Then verify:

```bash
python3 backup_verify.py /backups --checksums /backups/checksums.txt
```

The checksum file uses the standard shasum format (compatible with `sha256sum -c`).
A CSV file with `hash,filename` columns also works as a fallback.

### Full restorability test

The `--extract` flag unpacks each archive into a temporary directory,
confirms the extraction succeeds, and reports how many bytes came out:

```bash
python3 backup_verify.py /backups --extract
```

### JSON output for monitoring

```bash
python3 backup_verify.py /backups --json
```

```json
{
  "version": "1.0.0",
  "total": 2,
  "passed": 1,
  "failed": 1,
  "results": [ ... ]
}
```

Pipe it to `jq` for quick checks:

```bash
python3 backup_verify.py /backups --json | jq '.failed'
```

## Exit codes

These are designed to work well with cron and monitoring systems:

| Code | Meaning                             |
|------|-------------------------------------|
| 0    | Every backup passed verification    |
| 1    | At least one backup failed          |
| 2    | Argument or configuration error     |
| 3    | No backups matched the given path   |

## Cron example

Run verification one hour after your nightly backup:

```cron
# /etc/cron.d/backup-verify
0 4 * * * root /opt/backup-verify/backup_verify.py /backups --json >> /var/log/backup-verify.log 2>&1
```

A non-zero exit code means something needs your attention. Wire it into your
existing alert system (webhook, email, monitoring agent) based on the exit code.

## Supported formats

| Extension    | Format        | Validation                   |
|--------------|---------------|------------------------------|
| .tar         | uncompressed  | tar member iteration         |
| .tar.gz      | gzip          | gzip CRC check + tar members |
| .tgz         | gzip          | same as above                |
| .tar.bz2     | bzip2         | stream decompression + tar   |
| .tbz2        | bzip2         | same as above                |
| .tar.xz      | xz            | tar member iteration         |
| .txz         | xz            | same as above                |
| .zip         | zip           | CRC32 via testzip()          |
| .gz          | gzip          | gzip CRC check (single file) |
| .bz2         | bzip2         | stream decompression         |
| .xz          | xz            | header check                 |

When pointed at a directory, all of the above extensions are auto-detected.
When pointed at a single file path, the file is checked regardless of extension.

## License

AGPL-3.0. See the [root license](../../LICENSE).
