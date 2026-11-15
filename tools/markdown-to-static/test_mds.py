import tempfile
import unittest
from pathlib import Path

from mds import build_site


class BuildSitePathTests(unittest.TestCase):
    def test_rejects_output_equal_to_content_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content = Path(temp_dir) / "content"
            content.mkdir()
            source = content / "page.md"
            source.write_text("# Keep me", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must not contain or be inside"):
                build_site(content, content)

            self.assertTrue(source.exists())

    def test_rejects_output_inside_content_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content = Path(temp_dir) / "content"
            content.mkdir()
            source = content / "page.md"
            source.write_text("# Keep me", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must not contain or be inside"):
                build_site(content, content / "output")

            self.assertTrue(source.exists())

    def test_rejects_content_inside_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            content = output / "content"
            content.mkdir(parents=True)
            source = content / "page.md"
            source.write_text("# Keep me", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must not contain or be inside"):
                build_site(content, output)

            self.assertTrue(source.exists())

    def test_builds_when_directories_are_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content = root / "content"
            output = root / "output"
            content.mkdir()
            (content / "page.md").write_text("# Hello", encoding="utf-8")

            build_site(content, output)

            self.assertIn("<h1>Hello</h1>", (output / "page.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
