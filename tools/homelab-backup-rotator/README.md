# homelab-backup-rotator

Prunes backup files by glob pattern and retention count. Run with --dry-run before allowing deletion.

## Usage

Requires Python 3.10 or newer. Run from this tool's directory:

```sh
python3 backup_rotator.py /path/to/backups --policy '*.tar.gz=7' --dry-run
```

The command keeps the seven newest matching files. Repeat `--policy` for separate groups. Remove `--dry-run` only after checking which files it would delete. This tool prunes existing backups; it does not create them.

## License

AGPL-3.0. See the [root license](../../LICENSE).
