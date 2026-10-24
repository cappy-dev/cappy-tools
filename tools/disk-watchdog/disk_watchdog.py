#!/usr/bin/env python3
"""
disk_watchdog.py - A tiny disk health and usage watchdog for self-hosted boxes.

Checks every mounted filesystem against a usage threshold and optionally reads
SMART attributes for the underlying block devices. When something looks wrong it
emits an alert through one or more configured channels (log file, webhook, email
via sendmail, or a command). Designed to run from cron or systemd timers.

Zero external dependencies. Just the Python standard library.

License: AGPL-3.0
"""

import argparse
import json
import os
import re
import shutil
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib import request as urlreq
from urllib.error import URLError

__version__ = "1.0.0"

DEFAULT_CONFIG = {
    "usage_warn": 85,
    "usage_crit": 95,
    "smart_check": True,
    "smart_fail_values": {
        "Reallocated_Sector_Ct": 0,
        "Current_Pending_Sector": 0,
        "Offline_Uncorrectable": 0,
    },
    "ignore_fs_types": [
        "tmpfs", "devtmpfs", "squashfs", "overlay", "fuse.snapraid",
        "proc", "sysfs", "cgroup", "cgroup2", "bpf", "pstore",
        "debugfs", "tracefs", "configfs", "securityfs", "fusectl",
        "binfmt_misc", "fuse", "fuse.gvfsd-fuse", "mqueue", "hugetlbfs",
        "ramfs", "rpc_pipefs", "devpts", "efivarfs", "autofs",
    ],
    "ignore_mount_prefixes": ["/var/lib/docker/overlay2"],
    "alerts": {
        "log": {"enabled": True, "path": "/var/log/disk-watchdog.log"},
        "webhook": {"enabled": False, "url": "", "method": "POST"},
        "email": {"enabled": False, "to": "", "smtp_host": "localhost", "smtp_port": 25},
        "command": {"enabled": False, "exec": ""},
    },
    "quiet_when_ok": True,
}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path):
    """Load JSON config, layered on top of the built-in defaults."""
    cfg = _deep_copy(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path, "r") as fh:
            user = json.load(fh)
        _deep_merge(cfg, user)
    return cfg


def _deep_copy(obj):
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return list(obj)
    return obj


def _deep_merge(base, overlay):
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


# ---------------------------------------------------------------------------
# Disk usage checks
# ---------------------------------------------------------------------------

def get_mounts(ignore_types, ignore_prefixes):
    """Return a list of dicts describing mounted filesystems worth checking."""
    mounts = []
    if not os.path.exists("/proc/mounts"):
        return mounts
    seen = set()
    with open("/proc/mounts", "r") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 4:
                continue
            device, mount, fstype = parts[0], parts[1], parts[2]
            if fstype in ignore_types:
                continue
            if any(mount.startswith(p) for p in ignore_prefixes):
                continue
            if not os.path.isdir(mount):
                continue
            key = (device, mount)
            if key in seen:
                continue
            seen.add(key)
            mounts.append({
                "device": device,
                "mount": mount,
                "fstype": fstype,
            })
    return mounts


def check_usage(mount, warn_pct, crit_pct):
    """Return a result dict for the given mount point."""
    try:
        st = os.statvfs(mount)
    except OSError as exc:
        return {
            "mount": mount,
            "ok": False,
            "level": "error",
            "message": "statvfs failed for {}: {}".format(mount, exc),
            "percent": None,
            "used_gb": None,
            "total_gb": None,
        }

    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free
    if total <= 0:
        return {
            "mount": mount,
            "ok": True,
            "level": "info",
            "message": "non-sized filesystem (likely virtual)",
            "percent": 0,
            "used_gb": 0.0,
            "total_gb": 0.0,
        }

    pct = round((used / total) * 100, 1)
    used_gb = used / (1024 ** 3)
    total_gb = total / (1024 ** 3)

    if pct >= crit_pct:
        level = "critical"
        ok = False
        msg = "disk usage {pct}% exceeds critical threshold {crit}%".format(
            pct=pct, crit=crit_pct)
    elif pct >= warn_pct:
        level = "warning"
        ok = False
        msg = "disk usage {pct}% exceeds warning threshold {warn}%".format(
            pct=pct, warn=warn_pct)
    else:
        level = "ok"
        ok = True
        msg = "disk usage {pct}% is within limits".format(pct=pct)

    return {
        "mount": mount,
        "ok": ok,
        "level": level,
        "message": msg,
        "percent": pct,
        "used_gb": round(used_gb, 2),
        "total_gb": round(total_gb, 2),
    }


# ---------------------------------------------------------------------------
# SMART checks
# ---------------------------------------------------------------------------

def get_block_devices():
    """Return a list of block device nodes that could have SMART data."""
    devices = []
    by_path = "/dev/disk/by-id"
    if os.path.isdir(by_path):
        for name in sorted(os.listdir(by_path)):
            full = os.path.join(by_path, name)
            # Skip partitions (names containing a dash followed by a digit)
            if re.search(r"-\d+$", name):
                continue
            if name.startswith(("nvme-", "ata-", "scsi-", "usb-")):
                realpath = os.path.realpath(full)
                if realpath and realpath not in devices:
                    devices.append(realpath)
    if not devices:
        # Fall back to scanning /dev for obvious Whole disks
        for name in sorted(os.listdir("/dev")):
            if re.match(r"^(sd[a-z]+|nvme\d+n\d+)$", name):
                devices.append(os.path.join("/dev", name))
    return devices


def read_smart(device, fail_values):
    """Run smartctl -A and parse attributes we care about. Returns result dict."""
    if not shutil.which("smartctl"):
        return {
            "device": device,
            "ok": True,
            "level": "info",
            "message": "smartctl not installed; SMART check skipped",
            "attributes": {},
        }

    try:
        proc = subprocess.run(
            ["smartctl", "-A", "-j", device],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            universal_newlines=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {
            "device": device,
            "ok": True,
            "level": "info",
            "message": "smartctl invocation failed: {}".format(exc),
            "attributes": {},
        }

    attributes = {}
    failed = []

    # The -j flag returns JSON on recent smartctl versions; parse both forms.
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None

    if data and isinstance(data, dict):
        ata = data.get("ata_smart_attributes", {})
        for entry in ata.get("table", []):
            name = entry.get("name", "")
            raw_val = entry.get("raw", {})
            if isinstance(raw_val, dict):
                value = raw_val.get("value", 0)
            else:
                value = raw_val
            if name in fail_values:
                attributes[name] = value
                threshold = fail_values[name]
                if value > threshold:
                    failed.append({"name": name, "value": value, "threshold": threshold})
    else:
        # Fallback: parse the classic text output
        for line in proc.stdout.splitlines():
            cols = line.split()
            if len(cols) >= 10 and cols[1] in fail_values:
                name = cols[1]
                raw_val = _parse_int_safe(cols[-1])
                attributes[name] = raw_val
                threshold = fail_values[name]
                if raw_val > threshold:
                    failed.append({"name": name, "value": raw_val, "threshold": threshold})

    if failed:
        detail = ", ".join(
            "{n}={v} (threshold {t})".format(n=f["name"], v=f["value"], t=f["threshold"])
            for f in failed
        )
        return {
            "device": device,
            "ok": False,
            "level": "critical",
            "message": "SMART attribute failure: {}".format(detail),
            "attributes": attributes,
            "failed": failed,
        }

    return {
        "device": device,
        "ok": True,
        "level": "ok",
        "message": "SMART attributes within thresholds",
        "attributes": attributes,
    }


def _parse_int_safe(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def emit_alerts(config, results):
    """Send alert messages for each failed check through configured channels."""
    failed = [r for r in results if not r["ok"]]
    if not failed:
        return 0

    alerts = config.get("alerts", {})
    count = 0

    if alerts.get("log", {}).get("enabled"):
        _alert_log(alerts["log"]["path"], failed)
        count += 1

    if alerts.get("webhook", {}).get("enabled"):
        _alert_webhook(alerts["webhook"], failed)
        count += 1

    if alerts.get("email", {}).get("enabled"):
        _alert_email(alerts["email"], failed)
        count += 1

    if alerts.get("command", {}).get("enabled"):
        _alert_command(alerts["command"], failed)
        count += 1

    return count


def _format_message(failed):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["[disk-watchdog {}] alerts:".format(stamp)]
    for item in failed:
        location = item.get("mount") or item.get("device") or "unknown"
        lines.append("  - {} [{}]: {}".format(location, item["level"], item["message"]))
    return "\n".join(lines)


def _alert_log(path, failed):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    msg = _format_message(failed)
    try:
        with open(path, "a") as fh:
            fh.write(msg + "\n")
    except OSError as exc:
        print("warn: could not write log {}: {}".format(path, exc), file=sys.stderr)


def _alert_webhook(cfg, failed):
    payload = json.dumps({"source": "disk-watchdog", "alerts": failed}).encode("utf-8")
    url = cfg.get("url", "")
    if not url:
        return
    method = cfg.get("method", "POST").upper()
    req = urlreq.Request(url, data=payload if method != "GET" else None, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urlreq.urlopen(req, timeout=10) as resp:
            if resp.status >= 400:
                print("warn: webhook returned {} {}".format(resp.status, url),
                      file=sys.stderr)
    except URLError as exc:
        print("warn: webhook failed: {}".format(exc), file=sys.stderr)


def _alert_email(cfg, failed):
    to = cfg.get("to", "")
    if not to:
        return
    msg = EmailMessage()
    msg["Subject"] = "[disk-watchdog] alert on {}".format(
        datetime.now().strftime("%Y-%m-%d %H:%M"))
    msg["From"] = "disk-watchdog@localhost"
    msg["To"] = to
    msg.set_content(_format_message(failed))
    host = cfg.get("smtp_host", "localhost")
    port = cfg.get("smtp_port", 25)
    try:
        with smtplib.SMTP(host, port, timeout=15) as srv:
            srv.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        print("warn: email send failed: {}".format(exc), file=sys.stderr)


def _alert_command(cfg, failed):
    exe = cfg.get("exec", "")
    if not exe:
        return
    try:
        subprocess.run(
            exe, shell=True, input=_format_message(failed),
            timeout=30, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            universal_newlines=True,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print("warn: alert command failed: {}".format(exc), file=sys.stderr)


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(config, smart_only=False, usage_only=False, devices=None):
    results = []
    cfg_warn = config.get("usage_warn", 85)
    cfg_crit = config.get("usage_crit", 95)

    if not smart_only:
        mounts = get_mounts(
            config.get("ignore_fs_types", []),
            config.get("ignore_mount_prefixes", []),
        )
        for m in mounts:
            results.append(check_usage(m["mount"], cfg_warn, cfg_crit))

    if not usage_only and config.get("smart_check", True):
        fail_vals = config.get("smart_fail_values", {})
        devs = devices or get_block_devices()
        for dev in devs:
            results.append(read_smart(dev, fail_vals))

    return results


def render(results, quiet_when_ok, fmt):
    all_ok = all(r["ok"] for r in results)
    if quiet_when_ok and all_ok:
        return

    if fmt == "json":
        print(json.dumps(results, indent=2))
        return

    # human readable
    for r in results:
        loc = r.get("mount") or r.get("device") or "unknown"
        level = r["level"].upper()
        line = "{level:8} {loc:30} {msg}".format(level=level, loc=loc, msg=r["message"])
        if r.get("percent") is not None:
            line += "  ({}% / {} of {} GB)".format(
                r["percent"], r.get("used_gb", 0), r.get("total_gb", 0))
        print(line)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="disk_watchdog",
        description="Check disk usage and SMART health, alert on problems.",
    )
    parser.add_argument("-c", "--config", default="/etc/disk-watchdog.json",
                        help="Path to JSON config file (default: %(default)s)")
    parser.add_argument("--smart-only", action="store_true",
                        help="Only run SMART checks, skip usage")
    parser.add_argument("--usage-only", action="store_true",
                        help="Only run usage checks, skip SMART")
    parser.add_argument("--device", action="append", default=None,
                        help="Block device to SMART check (repeatable)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--verbose", action="store_true",
                        help="Print all checks even when everything is OK")
    parser.add_argument("--print-default-config", action="store_true",
                        help="Print the built-in default config and exit")
    parser.add_argument("--version", action="version",
                        version="disk-watchdog {}".format(__version__))
    args = parser.parse_args(argv)

    if args.print_default_config:
        print(json.dumps(DEFAULT_CONFIG, indent=2))
        return 0

    config = load_config(args.config)
    results = run(
        config,
        smart_only=args.smart_only,
        usage_only=args.usage_only,
        devices=args.device,
    )

    render(results, quiet_when_ok=not args.verbose,
           fmt="json" if args.json else "text")

    emit_alerts(config, results)

    if any(not r["ok"] for r in results):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
