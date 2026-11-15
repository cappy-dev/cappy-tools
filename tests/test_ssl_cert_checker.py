import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "tools/ssl-cert-checker/ssl_cert_checker.py"
SPEC = importlib.util.spec_from_file_location("ssl_cert_checker", SCRIPT)
ssl_cert_checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ssl_cert_checker
SPEC.loader.exec_module(ssl_cert_checker)


class CertificateExpiryMessageTests(unittest.TestCase):
    def test_expired_certificate_reports_elapsed_full_days(self):
        now = datetime(2026, 1, 2, 0, 0, 1, tzinfo=timezone.utc)
        expired_at = now - timedelta(seconds=1)
        cert = {
            "notBefore": "Jan  1 00:00:00 2020 GMT",
            "notAfter": expired_at.strftime("%b %d %H:%M:%S %Y GMT"),
        }
        base = ssl_cert_checker.CertResult(
            "example.com", 443, "error", "", "", "", "", -9999, ""
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with patch.object(ssl_cert_checker, "datetime", FixedDateTime):
            result = ssl_cert_checker._result_from_cert(base, cert)

        self.assertEqual(result.status, "expired")
        self.assertEqual(result.days_remaining, -1)
        self.assertEqual(result.message, "Certificate expired 0 day(s) ago.")


if __name__ == "__main__":
    unittest.main()
