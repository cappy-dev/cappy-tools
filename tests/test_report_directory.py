import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "ssh-key-audit" / "ssh-key-audit.py"
SPEC = importlib.util.spec_from_file_location("ssh_key_audit", SCRIPT)
ssh_key_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ssh_key_audit)


class ReportDirectoryTests(unittest.TestCase):
    def run_main_with_reports(self, report_dir):
        json_report = report_dir / "ssh-audit-report.json"
        html_report = report_dir / "ssh-audit-report.html"
        with (
            patch.object(ssh_key_audit, "REPORT_PATH", str(json_report)),
            patch.object(ssh_key_audit, "HTML_REPORT_PATH", str(html_report)),
            patch.object(ssh_key_audit, "check_authorized_keys", return_value=([], [], [], "OK")),
            patch.object(ssh_key_audit, "check_ssh_config", return_value=([], [], "OK")),
            patch.object(ssh_key_audit, "check_sshd_config", return_value=([], [], "OK")),
            patch.object(ssh_key_audit, "find_duplicates", return_value=[]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exit_code = ssh_key_audit.main()
        return exit_code, json_report, html_report

    def test_fresh_directory_is_private_with_permissive_umask(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "fresh-home" / ".ssh"
            previous_umask = os.umask(0o022)
            try:
                exit_code, json_report, html_report = self.run_main_with_reports(report_dir)
            finally:
                os.umask(previous_umask)

            self.assertEqual(exit_code, 0)
            self.assertEqual(report_dir.stat().st_mode & 0o777, 0o700)
            self.assertTrue(json_report.is_file())
            self.assertEqual(json.loads(json_report.read_text())["duplicates"], [])
            self.assertTrue(html_report.is_file())
            self.assertIn("SSH Key Audit Report", html_report.read_text())

    def test_existing_directory_permissions_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "existing-home" / ".ssh"
            report_dir.mkdir(parents=True)
            report_dir.chmod(0o750)

            exit_code, json_report, html_report = self.run_main_with_reports(report_dir)

            self.assertEqual(exit_code, 0)
            self.assertEqual(report_dir.stat().st_mode & 0o777, 0o750)
            self.assertTrue(json_report.is_file())
            self.assertTrue(html_report.is_file())


if __name__ == "__main__":
    unittest.main()
