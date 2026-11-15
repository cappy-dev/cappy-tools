import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("certcheck.py")
SPEC = importlib.util.spec_from_file_location("certcheck", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load certcheck module")
certcheck = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(certcheck)


class ParseHostPortTests(unittest.TestCase):
    def test_hostname_with_port(self):
        self.assertEqual(certcheck.parse_host_port("example.com:8443", 443), ("example.com", 8443))

    def test_hostname_uses_default_port(self):
        self.assertEqual(certcheck.parse_host_port("example.com", 443), ("example.com", 443))

    def test_ipv6_literal_uses_default_port(self):
        self.assertEqual(certcheck.parse_host_port("2001:db8::1", 443), ("2001:db8::1", 443))

    def test_bracketed_ipv6_literal_with_port(self):
        self.assertEqual(certcheck.parse_host_port("[2001:db8::1]:8443", 443), ("2001:db8::1", 8443))

    def test_unclosed_ipv6_bracket_is_rejected(self):
        with self.assertRaises(ValueError):
            certcheck.parse_host_port("[2001:db8::1", 443)


if __name__ == "__main__":
    unittest.main()
