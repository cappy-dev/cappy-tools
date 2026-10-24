SSH Key Security Auditor
========================

Description
-----------
A zero-dependency Python tool that audits SSH configurations for security vulnerabilities. 
Checks authorized_keys files, SSH client config, and SSH daemon config for common security issues.

Features
--------
- Zero dependencies (uses only Python standard library)
- Checks for weak key types (ssh-dss, ssh-rsa1, etc)
- Detects duplicate keys in authorized_keys
- Identifies weak ciphers and MACs in SSH config
- Checks for dangerous SSH daemon settings
- Generates JSON and HTML reports
- Cron-friendly exit codes

Installation
------------
1. Clone the repository:
   git clone https://github.com/cappy-dev/ssh-key-audit.git
   cd ssh-key-audit

2. Make the script executable:
   chmod +x ssh-key-audit.py

3. Run the audit:
   python3 ssh-key-audit.py

Usage
-----
Run the script with no arguments to audit your SSH configuration:

   python3 ssh-key-audit.py

The script will:
- Check ~/.ssh/authorized_keys for weak keys and duplicates
- Check ~/.ssh/config for weak algorithms
- Check /etc/ssh/sshd_config for insecure settings (requires sudo)
- Generate JSON and HTML reports in ~/.ssh/

Examples
--------
Basic audit:
   $ python3 ssh-key-audit.py
   ============================================================
   SSH Key Security Auditor v1.0.0
   ============================================================
   
   [*] Checking authorized_keys...
   [-] Issues: 2
         authorized_keys has group/other permissions: 0o644
         Line 3: Weak key type 'ssh-dss'
   [+] Status: OK
   
   [*] Checking SSH client config...
   [+] Status: OK
   
   [*] Checking SSH daemon config...
   [-] Issues: 2
         PasswordAuthentication is enabled
         PermitRootLogin is enabled
   
   [*] Checking for duplicate authorized_keys files...
   [+] No duplicates found
   
   ============================================================
   SUMMARY
   ============================================================
   Critical issues: 4
   Warnings: 0
   
   [-] CRITICAL issues found. Review and fix immediately!

Cron usage
----------
Add to crontab to run weekly:
   0 2 * * 0 /path/to/ssh-key-audit.py >> /var/log/ssh-audit.log 2>&1

Exit codes
----------
0 - No issues found
1 - Warnings found
2 - Critical issues found
3 - Error during check

Security notes
--------------
- Run as your regular user to check user-specific configs
- Use sudo to check system-wide sshd_config
- Review HTML report for detailed findings
- Never run as root for routine checks unless necessary

License
-------
MIT License - see LICENSE file for details