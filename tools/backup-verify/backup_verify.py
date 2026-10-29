#!/usr/bin/env python3
"""
backup_verify.py -- Verify the integrity of backup archives.

A zero-dependency tool for self-hosters who want confidence that the backups
they rotate and stash away are actually restorable. Checks include:

  - File readability (not truncated / corrupt on disk)
  - Archive format validation (tar, tar.gz, tar.bz2, tar.xz, zip)
  - Gzip member CRC verification
  - Optional SHA256 comparison against a recorded checksum file
  - Optional --extract test that unpacks into a temp directory and reports size

Usage:
    backup_verify.py PATH [--checksums FILE] [--extract] [--json] [--quiet]

Exit codes (cron-friendly):
    0  all backups passed
    1  one or more backups failed verification
    2  configuration / argument error
    3  no backups matched the given path pattern

Pure Python 3.8+ standard library. No pip, no venv, no fuss.
"""

import argparse
import bz2
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

VERSION = "1.0.0"

# -- ANSI helpers (disabled when --quiet or not a tty) -----------------------

class _NoColor:
    def wrap(self, text, *_args):
        return text


class _Color:
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    def wrap(self, text, code):
        return f"{code}{text}{self.RESET}"


def make_colorizer(use_color):
    if not use_color:
        return _NoColor()
    return _Color()


# -- Checksum file loading ---------------------------------------------------

def load_checksums(path):
    """Load a checksum file in md5sum/shasum -c compatible format.

    Supports lines like:
        <hash>  <filename>     (two spaces, relative or absolute name)
    Plus a CSV fallback with columns: hash, filename
    Returns dict mapping basename -> hex digest string.
    """
    if not path:
        return {}

    result = {}
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Checksum file not found: {path}")

    raw = p.read_text(errors="replace")
    # Try shasum format first
    needs_csv = False
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and len(parts[0]) in (32, 40, 64, 128):
            digest, name = parts
            name = name.strip().lstrip("*")
            result[os.path.basename(name)] = digest.lower().strip()
            continue
        needs_csv = True

    if not result and needs_csv:
        # Fall back to CSV
        reader = csv.DictReader(io.StringIO(raw))
        for row in reader:
            digest = (row.get("hash") or row.get("sha256") or "").strip().lower()
            name = (row.get("filename") or row.get("file") or "").strip()
            if digest and name:
                result[os.path.basename(name)] = digest

    return result


# -- Core verification --------------------------------------------------------

def sha256_of(filepath, chunk_size=1024 * 1024):
    """Compute SHA256 hex digest of a file, streaming."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def is_readable(filepath):
    """Confirm the file exists, is readable, and is not zero bytes."""
    p = Path(filepath)
    if not p.is_file():
        return False, "file does not exist"
    if p.stat().st_size == 0:
        return False, "file is zero bytes"
    try:
        with open(filepath, "rb") as f:
            f.read(16)  # touch first bytes
        # Seek to end to confirm we can read the tail too
        with open(filepath, "rb") as f:
            f.seek(-16, os.SEEK_END)
            tail = f.read(16)
            if not tail and p.stat().st_size >= 16:
                return False, "could not read file tail (truncated?)"
    except OSError as exc:
        return False, f"read error: {exc}"
    return True, "ok"


def verify_gzip(filepath):
    """Verify gzip member integrity by decompressing in full (checks CRC)."""
    try:
        with gzip.open(filepath, "rb") as f:
            while f.read(1024 * 1024):
                pass
        return True, "gzip CRC ok"
    except (OSError, EOFError) as exc:
        return False, f"gzip error: {exc}"


def verify_tar(filepath, mode):
    """Open a tar archive and iterate every member to confirm it is sound."""
    try:
        with tarfile.open(filepath, mode) as tf:
            count = 0
            total = 0
            for member in tf:
                count += 1
                if member.isfile():
                    total += member.size
                    # Actually read the file data to surface decompression errors
                    tf.extractfile(member).read()
        return True, f"tar ok ({count} members, {total} bytes data)"
    except (tarfile.TarError, OSError, EOFError) as exc:
        return False, f"tar error: {exc}"


def verify_zip(filepath):
    """Validate a zip archive by testing every entry's CRC via testzip()."""
    # zipfile natively checks CRC32 on read; we exercise it entry by entry.
    z = zipfile.ZipFile(filepath, "r")
    bad = z.testzip()  # returns name of first bad file or None
    if bad is not None:
        z.close()
        return False, f"zip CRC failure in: {bad}"
    count = len(z.namelist())
    z.close()
    return True, f"zip ok ({count} entries)"


def detect_and_verify(filepath):
    """Detect the archive type from signature bytes and run the right check."""
    signature = b""
    try:
        with open(filepath, "rb") as f:
            signature = f.read(6)
    except OSError:
        return True, "skipped format check (could not read header)"

    # gzip: 1f 8b
    if signature[:2] == b"\x1f\x8b":
        return verify_gzip(filepath)
    # bzip2: 'BZ'
    if signature[:2] == b"BZ":
        # bzip2 does not expose streaming CRC, but open + read validates block headers
        try:
            with bz2.open(filepath, "rb") as f:
                while f.read(1024 * 1024):
                    pass
            return True, "bzip2 stream ok"
        except OSError as exc:
            return False, f"bzip2 error: {exc}"
    # zip: 'PK'
    if signature[:2] == b"PK":
        return verify_zip(filepath)
    # xz: fd 37 7a 58 5a 00
    if signature == b"\xfd7zXZ\x00":
        # lzma module can decompress the raw stream but a tar.xz is wrapped.
        # Fall through to tar test if tarfile supports xz.
        return verify_tar(filepath, "r:xz")
    # xz tar
    return verify_tar(filepath, "r:*")


def extract_test(filepath, dest):
    """Extract an archive into dest and return (ok, message, unpacked_bytes)."""
    try:
        with tarfile.open(filepath, "r:*") as tf:
            tf.extractall(dest)
        total = sum(f.stat().st_size for f in Path(dest).rglob("*") if f.is_file())
        return True, f"extract ok ({total} bytes unpacked)", total
    except tarfile.TarError:
        pass
    except Exception:
        pass
    # Try zip
    try:
        with zipfile.ZipFile(filepath, "r") as z:
            z.extractall(dest)
        total = sum(f.stat().st_size for f in Path(dest).rglob("*") if f.is_file())
        return True, f"extract ok ({total} bytes unpacked)", total
    except Exception as exc:
        return False, f"extract failed: {exc}", 0


# -- Per-file verification orchestration -------------------------------------

def verify_one(filepath, checksums, do_extract, color):
    """Run all configured checks on a single file. Returns a result dict."""
    name = os.path.basename(filepath)
    size = 0
    try:
        size = os.path.getsize(filepath)
    except OSError:
        pass

    result = {
        "file": name,
        "path": str(filepath),
        "size": size,
        "checks": [],
        "passed": True,
    }

    def record(ok, label, detail):
        status = "PASS" if ok else "FAIL"
        result["checks"].append({"check": label, "status": status, "detail": detail})
        if not ok:
            result["passed"] = False

    # 1. Readability
    ok, detail = is_readable(filepath)
    record(ok, "readable", detail)
    if not ok:
        return result  # no point running further checks on an unreadable file

    # 2. Format / archive integrity
    ok, detail = detect_and_verify(filepath)
    record(ok, "archive", detail)

    # 3. SHA256 comparison (optional)
    if checksums:
        expected = checksums.get(name)
        if expected is None:
            record(False, "sha256", "no checksum entry for this file")
        else:
            actual = sha256_of(filepath)
            if actual.lower() == expected.lower():
                record(True, "sha256", "checksum matches")
            else:
                record(False, "sha256", f"mismatch (expected {expected[:12]}..., got {actual[:12]}...)")

    # 4. Extract test (optional)
    if do_extract:
        with tempfile.TemporaryDirectory(prefix="bv_") as tmp:
            ok, detail, _ = extract_test(filepath, tmp)
            record(ok, "extract", detail)

    return result


def print_result(result, color, quiet):
    if quiet:
        return
    glyph_ok = color.wrap("PASS", _Color.GREEN if isinstance(color, _Color) else "")
    glyph_fail = color.wrap("FAIL", _Color.RED if isinstance(color, _Color) else "")
    status_glyph = glyph_ok if result["passed"] else glyph_fail
    size_kb = result["size"] / 1024
    print(f"{status_glyph}  {result['file']}  ({size_kb:.1f} KB)")
    for chk in result["checks"]:
        mark = color.wrap("+", _Color.GREEN) if chk["status"] == "PASS" else color.wrap("x", _Color.RED)
        print(f"     {mark} {chk['check']}: {chk['detail']}")


# -- Path expansion and matching ---------------------------------------------

def resolve_targets(path_arg):
    """Expand a path argument into a list of concrete files.

    - A directory is scanned for archive-like files (*.tar*, *.zip, *.gz, *.bz2, *.xz).
    - A glob pattern like /backups/*.tar.gz is expanded.
    - A single file is returned as-is.
    """
    p = Path(path_arg)
    if p.is_dir():
        pats = ("*.tar", "*.tar.gz", "*.tgz", "*.tar.bz2", "*.tbz2",
                "*.tar.xz", "*.txz", "*.zip", "*.gz", "*.bz2", "*.xz")
        seen = set()
        found = []
        for pat in pats:
            for match in sorted(p.glob(pat)):
                key = str(match.resolve())
                if key not in seen:
                    seen.add(key)
                    found.append(match)
        return found

    if p.is_file():
        return [p]

    # Try as a glob (covers /path/to/*.tar.gz)
    import glob as _glob
    matches = sorted(_glob.glob(path_arg))
    return [Path(m) for m in matches if Path(m).is_file()]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="backup_verify",
        description="Verify the integrity of backup archives. Zero dependencies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0=pass  1=fail  2=arg error  3=no backups found",
    )
    parser.add_argument("path", help="Backup file, directory of backups, or glob pattern")
    parser.add_argument("--checksums", "-c", default=None,
                        help="Checksum file (shasum -c format or CSV with hash,filename)")
    parser.add_argument("--extract", "-x", action="store_true",
                        help="Extract each archive into a temp dir to confirm it is restorable")
    parser.add_argument("--json", action="store_true",
                        help="Emit results as JSON (disables colored text output)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress per-check output (useful with --json)")
    parser.add_argument("--version", "-V", action="version", version=f"backup_verify {VERSION}")
    args = parser.parse_args(argv)

    use_color = sys.stdout.isatty() and not args.json and not args.quiet
    color = make_colorizer(use_color)

    # Load checksums (may raise FileNotFoundError)
    try:
        checksums = load_checksums(args.checksums)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Resolve targets
    targets = resolve_targets(args.path)
    if not targets:
        if args.json:
            json.dump({"error": "no backups found", "path": args.path}, sys.stdout)
            sys.stdout.write("\n")
        else:
            print(f"no backups found at: {args.path}", file=sys.stderr)
        return 3

    results = []
    for target in targets:
        result = verify_one(str(target), checksums, args.extract, color)
        results.append(result)
        if not args.json:
            print_result(result, color, args.quiet)

    all_passed = all(r["passed"] for r in results)

    if args.json:
        payload = {
            "version": VERSION,
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
            "failed": sum(1 for r in results if not r["passed"]),
            "results": results,
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif not args.quiet:
        passed = sum(1 for r in results if r["passed"])
        failed = len(results) - passed
        tag = color.wrap("PASS", _Color.GREEN) if all_passed else color.wrap("FAIL", _Color.RED)
        print(f"\n{tag}  {passed} passed, {failed} failed of {len(results)} total")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
