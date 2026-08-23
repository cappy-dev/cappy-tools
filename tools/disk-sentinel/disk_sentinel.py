#!/usr/bin/env python3
"""
disk-sentinel: Zero-dependency disk health monitor for Linux.

Checks disk space usage and S.M.A.R.T. attributes on local block devices.
Prints a clean report to the terminal and can exit non-zero when a disk
crosses a warning threshold, making it useful for cron or CI.

Requires:
  - Python 3.7+
  - smartmontools (smartctl) installed and runnable by the current user
    (usually means running as root, or adding the user to the disk group)

No third-party Python packages are needed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_USAGE_WARN_PCT = 85
DEFAULT_USAGE_CRIT_PCT = 95
DEFAULT_TEMP_WARN_C = 55
DEFAULT_TEMP_CRIT_C = 65

# A handful of S.M.A.R.T. IDs that are worth flagging bright and early.
# Numbers are normalized so the same list works for ATA and SCSI-ish reports.
SMART_RED_FLAG_IDS = {
    5: "Reallocated_Sector_Ct",      # Reallocated sectors
    187: "Reported_Uncorrect",        # Uncorrectable errors
    188: "Command_Timeout",           # Command timeouts
    197: "Current_Pending_Sector",    # Sectors waiting to be remapped
    198: "Offline_Uncorrectable",     # Offline uncorrectable sectors
    199: "UDMA_CRC_Error_Count",      # Cable / UDMA errors
    231: "SSD_Life_Left",             # SSD life left (some vendors)
    232: "Available_Reserved_Space",  # SSD reserved space (Intel)
    233: "Media_Wearout_Indicator",   # SSD wear (Intel)
}

EXIT_OK = 0
EXIT_WARN = 1
EXIT_CRIT = 2
EXIT_ERR = 3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SmartAttr:
    """One S.M.A.R.T. attribute row from smartctl -A."""

    aid: int
    name: str
    raw_value: int


@dataclass
class DiskReport:
    """Everything we collect about a single block device."""

    device: str
    model: Optional[str] = None
    serial: Optional[str] = None
    size_bytes: Optional[int] = None
    is_rotational: Optional[bool] = None
    is_ssd: Optional[bool] = None

    # Filesystem / capacity (from df). None if the device has no mounted fs.
    usage_pct: Optional[float] = None
    fs_total: Optional[int] = None
    fs_used: Optional[int] = None
    fs_free: Optional[int] = None
    fs_mount: Optional[str] = None

    # S.M.A.R.T. summary
    smart_available: Optional[bool] = None
    smart_passed: Optional[bool] = None
    smart_enabled: Optional[bool] = None
    smart_temp_c: Optional[int] = None
    smart_attrs: List[SmartAttr] = field(default_factory=list)
    smart_error: Optional[str] = None

    # Our verdict for this disk.
    status: str = "unknown"  # ok, warn, crit, unknown
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def run(cmd: List[str], timeout: int = 15) -> "subprocess.CompletedProcess[str]":
    """Run a command and capture output as text. Raises nothing."""
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def list_block_devices() -> List[str]:
    """Return a sorted list of whole-disk block devices (sda, nvme0n1, etc.).

    We look at /sys/block and keep everything except loop/ram/zram/md/dm devices
    so we only inspect real disks, not virtual mappings.
    """
    skip_prefixes = ("loop", "ram", "zram", "md", "dm", "sr", "fd")
    devices: List[str] = []
    try:
        entries = sorted(os.listdir("/sys/block"))
    except FileNotFoundError:
        return devices
    for entry in entries:
        if entry.startswith(skip_prefixes):
            continue
        devices.append(entry)
    return devices


def read_sys(device: str, *path: str) -> str:
    """Read one file under /sys/block/<device>/ and return its stripped content."""
    full = os.path.join("/sys/block", device, *path)
    try:
        with open(full, "r") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def model_of(device: str) -> Optional[str]:
    """Best-effort model string from /sys."""
    for cand in ("device/model", "device/vendor", "device/wwid"):
        val = read_sys(device, cand)
        if val:
            return val
    return None


def serial_of(device: str) -> Optional[str]:
    for cand in ("device/serial", "device/wwid"):
        val = read_sys(device, cand)
        if val:
            return val
    return None


def size_of(device: str) -> Optional[int]:
    val = read_sys(device, "size")
    if val.isdigit():
        return int(val) * 512  # /sys/block/<dev>/size is in 512-byte sectors
    return None


def is_rotational(device: str) -> Optional[bool]:
    val = read_sys(device, "queue/rotational")
    if val == "1":
        return True
    if val == "0":
        return False
    return None


# ---------------------------------------------------------------------------
# Filesystem usage (df)
# ---------------------------------------------------------------------------


def parse_df(device_path: str) -> Optional[tuple]:
    """Return (total, used, free, pct, mount) for the first df line matching device.

    We match on the trailing device name so /dev/sda1 matches sda1.
    """
    result = run(["df", "--block-size=1", "--output=target,size,used,avail,pcent,source"])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 6:
            continue
        source = parts[5]
        # Compare the basename, /dev/sda1 -> sda1, and also handle nvme names.
        if os.path.basename(source) == os.path.basename(device_path):
            try:
                total = int(parts[1])
                used = int(parts[2])
                free = int(parts[3])
                pct = float(parts[4].rstrip("%"))
            except ValueError:
                return None
            return (total, used, free, pct, parts[0])
    return None


def fill_usage(report: DiskReport) -> None:
    """Populate filesystem usage fields for partitions of this device."""
    dev_path = "/dev/" + report.device
    # Find partitions under /sys/block/<dev> that look like sda1, nvme0n1p1, etc.
    parts: List[str] = []
    try:
        for entry in os.listdir(os.path.join("/sys/block", report.device)):
            if entry.startswith(report.device):
                parts.append(entry)
    except OSError:
        parts = []
    parts.sort()

    # Report the partition that has the highest usage. That way one disk with a
    # near-full root partition still flags even if another partition is empty.
    best = None
    for part in parts:
        info = parse_df("/dev/" + part)
        if info is None:
            continue
        if best is None or info[3] > best[3]:
            best = info
            best_part = part
    if best is not None:
        report.fs_total, report.fs_used, report.fs_free, report.usage_pct, report.fs_mount = best


# ---------------------------------------------------------------------------
# S.M.A.R.T. parsing (smartctl)
# ---------------------------------------------------------------------------


def smartctl_path() -> Optional[str]:
    return shutil.which("smartctl")


def parse_smart_attrs(text: str) -> List[SmartAttr]:
    """Pull attribute rows out of `smartctl -A` output (ATA style)."""
    attrs: List[SmartAttr] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        # The table header starts with "ID# ATTRIBUTE_NAME".
        if "ID#" in line and "ATTRIBUTE_NAME" in line:
            in_table = True
            continue
        if not in_table:
            continue
        # Blank line or a Python style key:val footer ends the table.
        if not stripped:
            break
        parts = stripped.split()
        if not parts[0].isdigit():
            break
        try:
            aid = int(parts[0])
        except ValueError:
            break
        # Raw value is normally the last column. Some firmware prints it as
        # a single integer, others as "0 0 0" triplets. We only grab the last
        # integer-ish token, which gives the simple rightmost value.
        raw_token = parts[-1]
        try:
            raw = int(raw_token)
        except ValueError:
            # Handle "0 0 0" style by taking the last integer-ish token.
            raw = 0
            for token in reversed(parts):
                try:
                    raw = int(token)
                    break
                except ValueError:
                    continue
        # Name is parts[1]; strip trailing numbers off vendor suffixes lazily.
        name = parts[1] if len(parts) > 1 else f"Attr_{aid}"
        attrs.append(SmartAttr(aid=aid, name=name, raw_value=raw))
    return attrs


def parse_smart_temp(text: str) -> Optional[int]:
    """Find a temperature reading in smartctl output (celsius)."""
    for line in text.splitlines():
        low = line.lower()
        if "temperature" in low and "celsius" in low and ":" in low:
            # Lines look like: "194 Temperature_Celsius 0x0023 100 100 ... 35"
            # or NVMe: "Temperature:                        35 Celsius"
            parts = low.split(":")
            if len(parts) >= 2:
                tail = parts[-1].strip()
                # Grab the first integer we can in the tail.
                for token in tail.split():
                    try:
                        return int(token)
                    except ValueError:
                        continue
    # Fallback: look for "N Celsius" anywhere.
    import re
    match = re.search(r"(\d+)\s*celsius", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def fill_smart(report: DiskReport) -> None:
    """Run smartctl against the device and fill in S.M.A.R.T. fields."""
    sctl = smartctl_path()
    if sctl is None:
        report.smart_error = "smartctl not found"
        return
    # -i = info, -A = attributes, -H = health, -n standby = do not spin up disks
    cmd = [sctl, "-i", "-A", "-H", "/dev/" + report.device]
    res = run(cmd, timeout=20)
    text = res.stdout + res.stderr

    # Health self-assessment test result.
    report.smart_available = bool(res.returncode != 127)  # 127 means not found
    # smartctl return bits encode status. Bit 3 (value 8) = FAILED.
    # Bit 0 (1) = SMART status failing now/then.
    # We treat the overall health line as the source of truth.
    report.smart_passed = None
    for line in text.splitlines():
        low = line.lower()
        if "smart overall-health" in low or "self-assessment test result" in low:
            if "passed" in low:
                report.smart_passed = True
            elif "failed" in low:
                report.smart_passed = False
            break
    # NVMe style.
    if report.smart_passed is None:
        for line in text.splitlines():
            low = line.lower()
            if "smart/health information" in low and "ok" in low:
                report.smart_passed = True
                break
            if "critical warning" in low:
                report.smart_passed = "0x00" in low
                # only count the value line, not headers
                break

    if "SMART support is: Available" in text or "SMART/Health Information" in text:
        report.smart_available = True
    if "SMART support is: Enabled" in text:
        report.smart_enabled = True
    elif "SMART support is: Disabled" in text:
        report.smart_enabled = False

    report.smart_temp_c = parse_smart_temp(text)
    report.smart_attrs = parse_smart_attrs(text)

    if res.returncode == 127:
        report.smart_error = "smartctl not found"
    elif res.returncode != 0 and "FAILED" in text.upper():
        report.smart_error = None  # captured in smart_passed


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def evaluate(report: DiskReport, args: argparse.Namespace) -> None:
    """Set report.status and report.reasons based on thresholds."""
    worst = "ok"
    reasons: List[str] = []

    def bump(level: str, reason: str) -> None:
        nonlocal worst
        reasons.append(reason)
        if level == "crit" and worst != "crit":
            worst = "crit"
        elif level == "warn" and worst == "ok":
            worst = "warn"

    # Usage percent.
    if report.usage_pct is not None:
        if report.usage_pct >= args.usage_crit:
            bump("crit", f"disk usage {report.usage_pct:.1f}% >= {args.usage_crit}%")
        elif report.usage_pct >= args.usage_warn:
            bump("warn", f"disk usage {report.usage_pct:.1f}% >= {args.usage_warn}%")

    # Temperature.
    if report.smart_temp_c is not None:
        if report.smart_temp_c >= args.temp_crit:
            bump("crit", f"temperature {report.smart_temp_c} C >= {args.temp_crit} C")
        elif report.smart_temp_c >= args.temp_warn:
            bump("warn", f"temperature {report.smart_temp_c} C >= {args.temp_warn} C")

    # S.M.A.R.T. overall.
    if report.smart_passed is False:
        bump("crit", "S.M.A.R.T. self-assessment FAILED")
    elif report.smart_passed is None and report.smart_available:
        bump("warn", "S.M.A.R.T. health status could not be read")

    # Red-flag attributes. Any non-zero raw value here is worth a warning.
    for attr in report.smart_attrs:
        if attr.aid in SMART_RED_FLAG_IDS:
            # Attribute 199 (UDMA CRC) at small values is often just a flaky
            # cable. We warn at 0+ but we want plain counts.
            name = SMART_RED_FLAG_IDS.get(attr.aid, attr.name)
            # Life-left / reserved space attributes are inverted: low is bad.
            inverted = attr.aid in (231, 232, 233)
            if inverted and attr.raw_value < 10 and attr.raw_value != 0:
                bump("warn", f"{name} (id {attr.aid}) low: {attr.raw_value}")
            elif not inverted and attr.raw_value > 0 and attr.aid in (197, 198, 5, 187, 188):
                bump("crit", f"{name} (id {attr.aid}) non-zero: {attr.raw_value}")
            elif not inverted and attr.raw_value > 0:
                bump("warn", f"{name} (id {attr.aid}) non-zero: {attr.raw_value}")

    report.status = worst
    report.reasons = reasons


# ---------------------------------------------------------------------------
# Reporting / output
# ---------------------------------------------------------------------------


def human_size(num: int) -> str:
    """Turn a byte count into a friendly string."""
    if num is None:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}"
        num = int(num / 1024)
    return f"{num} EiB"


STATUS_SYMBOL = {
    "ok": "[ OK ]",
    "warn": "[WARN]",
    "crit": "[CRIT]",
    "unknown": "[????]",
}

STATUS_COLOR = {
    "ok": "\033[32m",      # green
    "warn": "\033[33m",    # yellow
    "crit": "\033[31m",    # red
    "unknown": "\033[37m", # white/grey
}
RESET_COLOR = "\033[0m"


def colorize(text: str, status: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{STATUS_COLOR.get(status, '')}{text}{RESET_COLOR}"


def print_text_report(reports: List[DiskReport], use_color: bool) -> None:
    if not reports:
        print("No block devices found.")
        return
    for r in reports:
        sym = STATUS_SYMBOL.get(r.status, "[????]")
        head = f"{sym} {r.device}"
        extra_bits: List[str] = []
        if r.model:
            extra_bits.append(r.model)
        kind = "SSD" if r.is_ssd else ("HDD" if r.is_ssd is False else "?")
        extra_bits.append(kind)
        if r.size_bytes is not None:
            extra_bits.append(human_size(r.size_bytes))
        print(colorize(head, r.status, use_color) + "  " + " / ".join(extra_bits))

        if r.usage_pct is not None:
            used = r.fs_used if r.fs_used is not None else 0
            total = r.fs_total if r.fs_total is not None else 0
            print(
                f"         usage: {r.usage_pct:5.1f}% "
                f"({human_size(used)} used of {human_size(total)}) "
                f"mount: {r.fs_mount}"
            )
        else:
            print("         usage: no mounted filesystem")

        if r.smart_temp_c is not None:
            print(f"         temp:   {r.smart_temp_c} C")
        if r.smart_passed is not None:
            print(f"         SMART:  {'PASS' if r.smart_passed else 'FAIL'}")
        if r.smart_error:
            print(f"         note:   {r.smart_error}")
        if r.reasons:
            print(f"         flags:  {', '.join(r.reasons)}")
        print()


def print_json_report(reports: List[DiskReport]) -> None:
    payload = []
    for r in reports:
        payload.append(
            {
                "device": r.device,
                "model": r.model,
                "size_bytes": r.size_bytes,
                "is_ssd": r.is_ssd,
                "usage": {
                    "percent": r.usage_pct,
                    "total": r.fs_total,
                    "used": r.fs_used,
                    "free": r.fs_free,
                    "mount": r.fs_mount,
                },
                "smart": {
                    "available": r.smart_available,
                    "enabled": r.smart_enabled,
                    "passed": r.smart_passed,
                    "temperature_c": r.smart_temp_c,
                    "error": r.smart_error,
                    "attrs": [a.__dict__ for a in r.smart_attrs],
                },
                "status": r.status,
                "reasons": r.reasons,
            }
        )
    print(json.dumps(payload, indent=2))


def worst_exit_code(reports: List[DiskReport]) -> int:
    if not reports:
        return EXIT_ERR
    worst = "ok"
    for r in reports:
        if r.status == "crit":
            return EXIT_CRIT
        if r.status == "warn" and worst != "crit":
            worst = "warn"
        if r.status == "unknown" and worst == "ok":
            worst = "warn"
    return {"ok": EXIT_OK, "warn": EXIT_WARN, "crit": EXIT_CRIT}.get(worst, EXIT_OK)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_reports(devices: List[str]) -> List[DiskReport]:
    reports: List[DiskReport] = []
    for dev in devices:
        r = DiskReport(device=dev)
        r.model = model_of(dev)
        r.serial = serial_of(dev)
        r.size_bytes = size_of(dev)
        r.is_rotational = is_rotational(dev)
        r.is_ssd = False if (r.is_rotational is True) else (
            True if (r.is_rotational is False) else None
        )
        if r.is_ssd is None and dev.startswith("nvme"):
            r.is_ssd = True
        fill_usage(r)
        fill_smart(r)
        reports.append(r)
    return reports


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="disk-sentinel",
        description="Zero-dependency disk health monitor for Linux.",
        epilog=(
            "Requires the smartmontools package (smartctl) for S.M.A.R.T. data."
        ),
    )
    p.add_argument(
        "--device",
        action="append",
        default=None,
        help="Device to inspect (e.g. sda, nvme0n1). Can repeat. Defaults to all disks.",
    )
    p.add_argument(
        "--usage-warn",
        type=float,
        default=DEFAULT_USAGE_WARN_PCT,
        help="Warn when disk usage reaches this percent (default: %(default)s)",
    )
    p.add_argument(
        "--usage-crit",
        type=float,
        default=DEFAULT_USAGE_CRIT_PCT,
        help="Critical when disk usage reaches this percent (default: %(default)s)",
    )
    p.add_argument(
        "--temp-warn",
        type=int,
        default=DEFAULT_TEMP_WARN_C,
        help="Warn when disk temperature reaches this C (default: %(default)s)",
    )
    p.add_argument(
        "--temp-crit",
        type=int,
        default=DEFAULT_TEMP_CRIT_C,
        help="Critical when disk temperature reaches this C (default: %(default)s)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of the text report.",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color in text output.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the report. Only the exit code matters.",
    )
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    devices = args.device if args.device else list_block_devices()
    if not devices:
        if not args.quiet:
            print("No block devices found.")
        return EXIT_ERR

    reports = build_reports(devices)
    for r in reports:
        evaluate(r, args)

    if not args.quiet:
        if args.json:
            print_json_report(reports)
        else:
            use_color = (not args.no_color) and sys.stdout.isatty()
            print_text_report(reports, use_color)

    return worst_exit_code(reports)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except subprocess.TimeoutExpired as exc:
        print(f"timeout running command: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERR)
    except KeyboardInterrupt:
        sys.exit(EXIT_ERR)
