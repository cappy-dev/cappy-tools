import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "tools"
    / "backup-verify"
    / "backup_verify.py"
)
SPEC = importlib.util.spec_from_file_location("backup_verify", MODULE_PATH)
backup_verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_verify)


class LoadChecksumsTests(unittest.TestCase):
    def test_reads_csv(self):
        checksum = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            checksum_file = Path(directory) / "checksums.csv"
            checksum_file.write_text(
                f"hash,filename\n{checksum},backup.tar.gz\n"
            )

            self.assertEqual(
                backup_verify.load_checksums(checksum_file),
                {"backup.tar.gz": checksum},
            )

    def test_reads_shasum_format(self):
        checksum = "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            checksum_file = Path(directory) / "checksums.sha256"
            checksum_file.write_text(f"{checksum}  backup.tar.gz\n")

            self.assertEqual(
                backup_verify.load_checksums(checksum_file),
                {"backup.tar.gz": checksum},
            )


if __name__ == "__main__":
    unittest.main()
