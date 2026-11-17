import tempfile
import unittest
from pathlib import Path

from check_tools import markdown_anchors, missing_markdown_anchor


class MarkdownAnchorTests(unittest.TestCase):
    def test_heading_slugs_duplicate_headings_and_explicit_ids(self):
        text = """# API guide
## API guide
## Run `tool` now
<a id=\"legacy-name\"></a>
"""
        self.assertEqual(
            markdown_anchors(text),
            {"api-guide", "api-guide-1", "run-tool-now", "legacy-name"},
        )

    def test_checks_same_file_and_relative_markdown_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "index.md"
            guide = root / "guide.md"
            source.write_text("# Home\n")
            guide.write_text("# Setup steps\n")

            self.assertFalse(missing_markdown_anchor(source, "#home"))
            self.assertFalse(missing_markdown_anchor(source, "guide.md#setup-steps"))
            self.assertTrue(missing_markdown_anchor(source, "guide.md#missing"))
            self.assertFalse(missing_markdown_anchor(source, "https://example.com/#missing"))


if __name__ == "__main__":
    unittest.main()
