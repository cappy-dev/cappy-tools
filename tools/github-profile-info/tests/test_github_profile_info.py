from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "github-profile-info"
LOADER = SourceFileLoader("github_profile_info", str(SCRIPT))
SPEC = spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
profile_info = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(profile_info)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class GitHubResponseTests(unittest.TestCase):
    def test_api_get_sends_headers_and_decodes_json(self):
        response = Response(json.dumps({"login": "octocat"}).encode())
        with patch.object(profile_info.urllib.request, "urlopen", return_value=response) as urlopen:
            result = profile_info.api_get("https://api.github.com/users/octocat")
        self.assertEqual(result, {"login": "octocat"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "github-profile-info/1.0")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github.v3+json")

    def test_fetch_repos_follows_full_pages(self):
        page_one = [{"id": n} for n in range(100)]
        responses = [Response(json.dumps(payload).encode()) for payload in (page_one, [{"id": 100}], [])]
        with patch.object(profile_info.urllib.request, "urlopen", side_effect=responses) as urlopen:
            repos = profile_info.fetch_repos("octocat")
        self.assertEqual(len(repos), 101)
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("page=2", urlopen.call_args.args[0].full_url)

    def test_fetch_pinned_extracts_and_deduplicates_profile_links(self):
        html = b'''<div class="js-pinned-items-reorder-list"><a href="/octocat/hello-world" class="Link">Repo</a><a href="/octocat/hello-world" class="Link">Repo</a></div></div></div>'''
        with patch.object(profile_info.urllib.request, "urlopen", return_value=Response(html)):
            self.assertEqual(profile_info.fetch_pinned("octocat"), ["octocat/hello-world"])

    def test_cli_writes_requested_markdown_without_network(self):
        user = {
            "login": "octocat", "name": "The Octocat", "type": "User",
            "public_repos": 1, "public_gists": 2, "followers": 3,
            "following": 4, "html_url": "https://github.com/octocat",
        }
        repos = [{"name": "hello-world", "stargazers_count": 9, "forks_count": 2,
                  "language": "Python", "html_url": "https://github.com/octocat/hello-world"}]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "summary.md"
            stderr = io.StringIO()
            with patch.object(sys, "argv", ["github-profile-info", "@octocat", "--repos", "--no-pinned", "--output", str(output)]), \
                 patch.object(profile_info, "fetch_user", return_value=user) as fetch_user, \
                 patch.object(profile_info, "fetch_repos", return_value=repos) as fetch_repos, \
                 patch.object(profile_info, "fetch_pinned", side_effect=AssertionError("unexpected pinned request")), \
                 redirect_stderr(stderr):
                profile_info.main()
            content = output.read_text()
        fetch_user.assert_called_once_with("octocat")
        fetch_repos.assert_called_once_with("octocat")
        self.assertIn("# The Octocat (octocat)", content)
        self.assertIn("## Top Repos (by stars)", content)
        self.assertIn("- Stars: 9", content)
        self.assertIn(f"Written to {output}", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
