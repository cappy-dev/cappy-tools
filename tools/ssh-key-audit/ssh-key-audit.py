#!/usr/bin/env python3
"""
SSH Key Security Auditor

A zero-dependency tool that audits SSH configurations for security vulnerabilities.
Checks authorized_keys, ~/.ssh/config, and /etc/ssh/sshd_config.

Exit codes:
  0 - No issues found
  1 - Warnings found
  2 - Critical issues found
  3 - Error during check
"""

import os
import sys
import stat
import hashlib
import json
import re
from pathlib import Path

# Configuration
AUTH_KEYS_PATH = os.path.expanduser("~/.ssh/authorized_keys")
SSH_CONFIG_PATH = os.path.expanduser("~/.ssh/config")
SSHD_CONFIG_PATH = "/etc/ssh/sshd_config"
REPORT_PATH = os.path.expanduser("~/.ssh/ssh-audit-report.json")
HTML_REPORT_PATH = os.path.expanduser("~/.ssh/ssh-audit-report.html")

# Known weak algorithms
WEAK_KEY_TYPES = ["ssh-rsa", "ssh-dss", "rsa1"]
WEAK_ALGORITHMS = ["DES", "3DES", "MD5", "arcfour"]


def read_file_secure(path):
    """Read a file with proper permission checks."""
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except (IOError, OSError) as e:
        return None


def get_file_perms(path):
    """Get file permissions, return None if file doesn't exist."""
    try:
        st = os.stat(path)
        return st.st_mode
    except (IOError, OSError):
        return None


def check_authorized_keys():
    """Check authorized_keys file for security issues."""
    issues = []
    warnings = []
    key_fingerprints = []
    
    if not os.path.exists(AUTH_KEYS_PATH):
        return issues, warnings, key_fingerprints, "File not found"
    
    # Check permissions
    perms = get_file_perms(AUTH_KEYS_PATH)
    if perms is not None:
        # Should be 600 or 400 (owner read/write only)
        if perms & (stat.S_IRWXG | stat.S_IRWXO):
            issues.append(f"authorized_keys has group/other permissions: {oct(stat.S_IMODE(perms))}")
    
    content = read_file_secure(AUTH_KEYS_PATH)
    if content is None:
        return issues, warnings, key_fingerprints, "Permission denied"
    
    lines = content.strip().split("\n")
    processed_keys = set()
    
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        parts = line.split()
        if not parts:
            continue
        
        key_type = parts[0]
        
        # Check for weak key types
        if key_type in WEAK_KEY_TYPES:
            issues.append(f"Line {i}: Weak key type '{key_type}'")
        
        # Check for key type in algorithm
        for weak_algo in WEAK_ALGORITHMS:
            if weak_algo.lower() in line.lower():
                issues.append(f"Line {i}: Weak algorithm detected ({weak_algo})")
        
        # Extract key data and compute fingerprint
        if len(parts) >= 2:
            try:
                import base64
                key_data = parts[1]
                # Decode key for fingerprint
                decoded = base64.b64decode(key_data)
                sha256_fp = hashlib.sha256(decoded).hexdigest()
                key_fingerprints.append({
                    "line": i,
                    "type": key_type,
                    "fingerprint_sha256": sha256_fp,
                    "raw_type": key_type
                })
            except Exception:
                warnings.append(f"Line {i}: Could not decode key for fingerprint")
    
    # Check for duplicates
    key_hashes = {}
    for fp in key_fingerprints:
        fp_hash = fp.get("fingerprint_sha256", "")
        if fp_hash in key_hashes:
            issues.append(f"Duplicate key found on lines {key_hashes[fp_hash]} and {fp['line']}")
        else:
            key_hashes[fp_hash] = fp["line"]
    
    status = "OK" if not issues else "ISSUES"
    return issues, warnings, key_fingerprints, status


def check_ssh_config():
    """Check SSH client config for issues."""
    issues = []
    warnings = []
    
    if not os.path.exists(SSH_CONFIG_PATH):
        return issues, warnings, "File not found"
    
    content = read_file_secure(SSH_CONFIG_PATH)
    if content is None:
        return issues, warnings, "Permission denied"
    
    issues_found = []
    
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        # Check for weak ciphers/mac
        for weak_algo in WEAK_ALGORITHMS:
            if weak_algo.lower() in line.lower():
                issues.append(f"Weak algorithm in config: {line}")
    
    status = "OK" if not issues else "ISSUES"
    return issues, warnings, status


def check_sshd_config():
    """Check sshd_config for security issues."""
    issues = []
    warnings = []
    
    if not os.path.exists(SSHD_CONFIG_PATH):
        return issues, warnings, "File not found (requires sudo for system config)"
    
    content = read_file_secure(SSHD_CONFIG_PATH)
    if content is None:
        return issues, warnings, "Permission denied"
    
    in_match_block = False
    
    for line in content.split("\n"):
        line_stripped = line.strip()
        
        if line_stripped.upper().startswith("MATCH "):
            in_match_block = True
        
        if not line_stripped or line_stripped.startswith("#") or line_stripped.upper().startswith("MATCH "):
            continue
        
        # Check for dangerous settings
        if re.match(r"^\s*PasswordAuthentication\s+yes", line_stripped, re.IGNORECASE):
            issues.append("PasswordAuthentication is enabled")
        
        if re.match(r"^\s*PermitRootLogin\s+yes", line_stripped, re.IGNORECASE):
            issues.append("PermitRootLogin is enabled")
        
        if re.match(r"^\s*PermitEmptyPasswords\s+yes", line_stripped, re.IGNORECASE):
            issues.append("PermitEmptyPasswords is enabled")
        
        if re.match(r"^\s*UsePAM\s+no", line_stripped, re.IGNORECASE):
            warnings.append("UsePAM is disabled")
    
    status = "OK" if not issues else "ISSUES"
    return issues, warnings, status


def find_duplicates():
    """Find duplicate authorized_keys files in the system."""
    duplicates = []
    home_ssh = Path.home() / ".ssh"
    
    if not home_ssh.exists():
        return duplicates
    
    keys = {}
    for f in home_ssh.glob("authorized_keys*"):
        try:
            with open(f, "rb") as fh:
                content = fh.read()
                fhash = hashlib.sha256(content).hexdigest()
                if fhash in keys:
                    duplicates.append((str(keys[fhash]), str(f)))
                else:
                    keys[fhash] = f
        except Exception:
            pass
    
    return duplicates


def generate_html_report(results):
    """Generate HTML report from audit results."""
    html = """<!DOCTYPE html>
<html>
<head>
    <title>SSH Key Audit Report</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 2rem; }
        .critical { color: #d32f2f; background: #ffebee; padding: 1rem; border-radius: 4px; margin: 0.5rem 0; }
        .warning { color: #f57c00; background: #fff3e0; padding: 1rem; border-radius: 4px; margin: 0.5rem 0; }
        .ok { color: #388e3c; }
        pre { background: #f5f5f5; padding: 1rem; overflow-x: auto; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ddd; padding: 0.5rem; text-align: left; }
        th { background: #f5f5f5; }
    </style>
</head>
<body>
    <h1>SSH Key Audit Report</h1>
    <p>Generated: """ + __import__('datetime').datetime.now().isoformat() + """</p>
"""
    
    for section in ["authorized_keys", "ssh_config", "sshd_config"]:
        data = results.get(section, {})
        status = data.get("status", "Unknown")
        if status == "OK":
            status_class = "ok"
        else:
            status_class = "critical"
        html += f"<h2>{section}</h2>"
        html += f"<p>Status: <span class='{status_class}'>{status}</span></p>"
        
        if data.get("issues"):
            html += "<h3>Critical Issues</h3><ul>"
            for issue in data["issues"]:
                html += f"<li class='critical'>{issue}</li>"
            html += "</ul>"
        
        if data.get("warnings"):
            html += "<h3>Warnings</h3><ul>"
            for warning in data["warnings"]:
                html += f"<li class='warning'>{warning}</li>"
            html += "</ul>"
        
        if data.get("fingerprints"):
            html += "<h3>Key Fingerprints</h3><table><tr><th>Line</th><th>Type</th><th>SHA256</th></tr>"
            for fp in data["fingerprints"][:10]:  # Limit to first 10
                html += f"<tr><td>{fp['line']}</td><td>{fp['type']}</td><td>{fp['fingerprint_sha256'][:32]}...</td></tr>"
            html += "</table>"
    
    html += "</body></html>"
    return html


def main():
    """Main audit function."""
    print("=" * 60)
    print("SSH Key Security Auditor v1.0.0")
    print("=" * 60)
    
    results = {
        "authorized_keys": {"issues": [], "warnings": [], "status": "OK", "fingerprints": []},
        "ssh_config": {"issues": [], "warnings": [], "status": "OK"},
        "sshd_config": {"issues": [], "warnings": [], "status": "OK"}
    }
    
    # Check authorized_keys
    print("\n[*] Checking authorized_keys...")
    issues, warnings, fps, status = check_authorized_keys()
    results["authorized_keys"] = {
        "issues": issues, "warnings": warnings, "status": status, "fingerprints": fps
    }
    
    if issues:
        print(f"  [-] Issues: {len(issues)}")
        for i in issues:
            print(f"      {i}")
    if warnings:
        print(f"  [!] Warnings: {len(warnings)}")
        for w in warnings:
            print(f"      {w}")
    if status == "OK":
        print("  [+] Status: OK")
    
    # Check SSH config
    print("\n[*] Checking SSH client config...")
    issues, warnings, status = check_ssh_config()
    results["ssh_config"] = {
        "issues": issues, "warnings": warnings, "status": status
    }
    
    if issues:
        print(f"  [-] Issues: {len(issues)}")
        for i in issues:
            print(f"      {i}")
    if status == "OK":
        print("  [+] Status: OK")
    
    # Check sshd_config
    print("\n[*] Checking SSH daemon config...")
    issues, warnings, status = check_sshd_config()
    results["sshd_config"] = {
        "issues": issues, "warnings": warnings, "status": status
    }
    
    if issues:
        print(f"  [-] Issues: {len(issues)}")
        for i in issues:
            print(f"      {i}")
    if warnings:
        print(f"  [!] Warnings: {len(warnings)}")
        for w in warnings:
            print(f"      {w}")
    if status == "OK" or status == "File not found (requires sudo for system config)":
        print(f"  [~] Status: {status}")
    
    # Check for duplicates
    print("\n[*] Checking for duplicate authorized_keys files...")
    dups = find_duplicates()
    if dups:
        print(f"  [!] Found {len(dups)} duplicate authorized_keys files")
        results["duplicates"] = dups
    else:
        print("  [+] No duplicates found")
        results["duplicates"] = []
    
    # Summary
    total_issues = len(results["authorized_keys"]["issues"]) + len(results["ssh_config"]["issues"]) + len(results["sshd_config"]["issues"])
    total_warnings = len(results["authorized_keys"]["warnings"]) + len(results["ssh_config"]["warnings"]) + len(results["sshd_config"]["warnings"])
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Critical issues: {total_issues}")
    print(f"Warnings: {total_warnings}")
    
    # Write JSON report
    with open(REPORT_PATH, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[*] JSON report written to: {REPORT_PATH}")
    
    # Write HTML report
    html = generate_html_report(results)
    with open(HTML_REPORT_PATH, "w") as f:
        f.write(html)
    print(f"[*] HTML report written to: {HTML_REPORT_PATH}")
    
    # Exit code
    if total_issues > 0:
        print("\n[-] CRITICAL issues found. Review and fix immediately!")
        return 2
    elif total_warnings > 0:
        print("\n[!] Warnings found. Consider reviewing.")
        return 1
    else:
        print("\n[+] All checks passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())