import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools/dns-torch/dns_torch.py"
SPEC = importlib.util.spec_from_file_location("dns_torch", SCRIPT)
dns_torch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dns_torch
SPEC.loader.exec_module(dns_torch)

import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

import dns_torch


class JsonOutputTests(unittest.TestCase):
    def test_json_mode_writes_only_serialized_results(self):
        results = [{"check": "dns", "ok": True}]
        output = io.StringIO()

        def diagnose(domain, args):
            print("human-readable report")
            return results

        with patch.object(sys, "argv", ["dns-torch", "example.com", "--json"]):
            with patch.object(dns_torch, "diagnose", side_effect=diagnose):
                with contextlib.redirect_stdout(output):
                    dns_torch.main()

        self.assertEqual(json.loads(output.getvalue()), results)
        self.assertNotIn("human-readable report", output.getvalue())

    def test_normal_mode_keeps_report_output(self):
        output = io.StringIO()

        def diagnose(domain, args):
            print("human-readable report")
            return []

        with patch.object(sys, "argv", ["dns-torch", "example.com"]):
            with patch.object(dns_torch, "diagnose", side_effect=diagnose):
                with contextlib.redirect_stdout(output):
                    dns_torch.main()

        self.assertEqual(output.getvalue(), "human-readable report\n")


if __name__ == "__main__":
    unittest.main()
