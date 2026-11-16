"""CLI tests — mocked network, temp SCOUT_HOME. No Qt."""

import io
import json
import os
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scout import apis, cli, core


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SCOUT_HOME"] = self.tmp.name
        core._RING.clear()

    def tearDown(self):
        os.environ.pop("SCOUT_HOME", None)

    def run_cli(self, argv):
        """Run the CLI, capturing stdout. Returns (exit code, text)."""
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()


class AddRemoveTests(CliTestCase):
    def test_add_then_list(self):
        code, text = self.run_cli(
            ["add", "https://example.com/a", "--title", "Alpha"])
        self.assertEqual(code, 0)
        self.assertIn("Added Alpha", text)
        code, text = self.run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertIn("Alpha", text)
        self.assertIn("Found", text)  # the workflow state shows

    def test_add_without_scheme_gets_https(self):
        self.run_cli(["add", "example.com/a"])
        self.assertEqual(core.load_list()[0]["url"],
                         "https://example.com/a")

    def test_add_twice_is_one_entry(self):
        self.run_cli(["add", "https://example.com/a"])
        code, text = self.run_cli(["add", "https://example.com/a/"])
        self.assertEqual(code, 0)
        self.assertIn("already in My List", text)
        self.assertEqual(len(core.load_list()), 1)

    def test_remove(self):
        self.run_cli(["add", "https://example.com/a"])
        code, text = self.run_cli(["remove", "https://example.com/a/"])
        self.assertEqual(code, 0)
        self.assertIn("Removed", text)
        self.assertEqual(core.load_list(), [])

    def test_remove_missing_says_so(self):
        code, text = self.run_cli(["remove", "https://example.com/nope"])
        self.assertEqual(code, 0)
        self.assertIn("not in My List", text)

    def test_list_empty(self):
        code, text = self.run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertIn("empty", text)


class CheckTests(CliTestCase):
    def test_check_marks_statuses(self):
        self.run_cli(["add", "https://example.com/a"])
        self.run_cli(["add", "https://example.com/b"])
        with unittest.mock.patch.object(
                apis, "check_link", side_effect=["ok", "dead"]):
            code, text = self.run_cli(["check"])
        self.assertEqual(code, 0)
        self.assertIn("1 working, 1 dead", text)
        statuses = [item["status"] for item in core.load_list()]
        self.assertEqual(statuses, ["ok", "dead"])

    def test_check_empty_list(self):
        code, text = self.run_cli(["check"])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to check", text)


class ImportExportTests(CliTestCase):
    def test_import_markdown(self):
        md = ("# Dataset Scout list\n\n## Reasoning\n\n"
              "- [Alpha](https://example.com/a)\n"
              "Note: good one\n")
        path = Path(self.tmp.name) / "list.md"
        path.write_text(md, encoding="utf-8")
        code, text = self.run_cli(["import", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("Imported 1 links", text)
        items = core.load_list()
        self.assertEqual(items[0]["category"], "reasoning")
        self.assertEqual(items[0]["notes"], "good one")

    def test_import_missing_file_fails_plainly(self):
        code, _text = self.run_cli(
            ["import", str(Path(self.tmp.name) / "nope.md")])
        self.assertEqual(code, 1)

    def test_import_with_bom(self):
        # v0.1.3 T35: a byte-order mark does not break the import.
        md = ("\ufeff# Dataset Scout list\n\n"
              "- [Alpha](https://example.com/a)\n")
        path = Path(self.tmp.name) / "list.md"
        path.write_text(md, encoding="utf-8")
        code, text = self.run_cli(["import", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("Imported 1 links", text)

    def test_export_all_formats(self):
        self.run_cli(["add", "https://example.com/a",
                      "--title", "Alpha"])
        for fmt in ("md", "json", "stella"):
            path = Path(self.tmp.name) / f"out.{fmt}"
            code, text = self.run_cli(["export", fmt, str(path)])
            self.assertEqual(code, 0, text)
            self.assertTrue(path.is_file())
            self.assertIn("Alpha", path.read_text(encoding="utf-8"))
        manifest = json.loads(
            (Path(self.tmp.name) / "out.stella").read_text(
                encoding="utf-8"))
        self.assertEqual(manifest["items"][0]["url"],
                         "https://example.com/a")


class SearchCliTests(CliTestCase):
    FAKE = [{"title": "hf", "url": "https://example.com/hf",
             "category": "all", "source": "Hugging Face",
             "stars": 3, "updated": "2026-11-01",
             "license": "MIT", "description": "a dataset"}]

    def test_search_partial_failure_still_exits_zero(self):
        with unittest.mock.patch.object(
                apis, "search_github",
                side_effect=apis.SearchError("GitHub is down")), \
                unittest.mock.patch.object(
                    apis, "search_huggingface",
                    return_value=self.FAKE):
            code, text = self.run_cli(["search", "math"])
        self.assertEqual(code, 0)
        self.assertIn("hf", text)

    def test_search_both_fail_exits_one(self):
        with unittest.mock.patch.object(
                apis, "search_github",
                side_effect=apis.SearchError("down")), \
                unittest.mock.patch.object(
                    apis, "search_huggingface",
                    side_effect=apis.SearchError("down")), \
                unittest.mock.patch.object(
                    apis, "is_online", return_value=True):
            code, _text = self.run_cli(["search", "math"])
        self.assertEqual(code, 1)

    def test_search_both_fail_offline_says_so(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err), \
                unittest.mock.patch.object(
                    apis, "search_github",
                    side_effect=apis.SearchError("down")), \
                unittest.mock.patch.object(
                    apis, "search_huggingface",
                    side_effect=apis.SearchError("down")), \
                unittest.mock.patch.object(
                    apis, "is_online", return_value=False):
            code, _text = self.run_cli(["search", "math"])
        self.assertEqual(code, 1)
        self.assertIn("offline", err.getvalue().lower())

    def test_search_license_filter(self):
        with unittest.mock.patch.object(
                apis, "search_github", return_value=[]), \
                unittest.mock.patch.object(
                    apis, "search_huggingface",
                    return_value=self.FAKE):
            code, text = self.run_cli(
                ["search", "math", "--license", "Apache-2.0"])
        self.assertEqual(code, 0)
        self.assertIn("No results.", text)

    def test_search_empty_says_how_many_filters_hid(self):
        # The CLI must say how many results the filters hid, like
        # the window does, instead of a bare "No results."
        with unittest.mock.patch.object(
                apis, "search_github", return_value=[]), \
                unittest.mock.patch.object(
                    apis, "search_huggingface",
                    return_value=self.FAKE):
            code, text = self.run_cli(
                ["search", "math", "--license", "Apache-2.0"])
        self.assertEqual(code, 0)
        self.assertIn("No results.", text)
        self.assertIn("(1 hidden by filters)", text)

    def test_search_found_line_names_hidden_count(self):
        stale = {"title": "old", "url": "https://example.com/old",
                 "category": "all", "source": "Hugging Face",
                 "stars": 0, "updated": "2020-01-01",
                 "license": "MIT", "description": ""}
        with unittest.mock.patch.object(
                apis, "search_github", return_value=[]), \
                unittest.mock.patch.object(
                    apis, "search_huggingface",
                    return_value=self.FAKE + [stale]):
            code, text = self.run_cli(["search", "math"])
        self.assertEqual(code, 0)
        self.assertIn("Found 1 results (1 hidden by filters).", text)

    def test_search_results_are_newest_first(self):
        # v0.1.3 T32/T37: the CLI sorts like the GUI, by parsed time.
        # The +12:00 string sorts after the Zulu string, but it is
        # the earlier instant.
        results = [
            {"title": "old", "url": "https://example.com/old",
             "category": "all", "source": "GitHub", "stars": 1,
             "updated": "2026-01-01T00:00:00+12:00",
             "license": "", "description": ""},
            {"title": "new", "url": "https://example.com/new",
             "category": "all", "source": "GitHub", "stars": 1,
             "updated": "2025-12-31T13:00:00Z",
             "license": "", "description": ""},
        ]
        with unittest.mock.patch.object(
                apis, "search_github", return_value=results), \
                unittest.mock.patch.object(
                    apis, "search_huggingface", return_value=[]):
            code, text = self.run_cli(
                ["search", "math", "--include-inactive"])
        self.assertEqual(code, 0)
        self.assertLess(text.index("new"), text.index("old"))

    def test_search_limit_is_clamped_to_100(self):
        # v0.1.3 T43: GitHub takes at most 100 per page.
        gh = unittest.mock.Mock(return_value=[])
        hf = unittest.mock.Mock(return_value=[])
        with unittest.mock.patch.object(apis, "search_github", gh), \
                unittest.mock.patch.object(apis, "search_huggingface", hf):
            code, _text = self.run_cli(["search", "math", "--limit", "500"])
        self.assertEqual(code, 0)
        self.assertEqual(gh.call_args[1]["per_page"], 100)
        self.assertEqual(hf.call_args[1]["limit"], 100)


class UpdateCheckTests(CliTestCase):
    def test_newer_version_reported(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag", return_value="v9.9.9"):
            code, text = self.run_cli(["update-check"])
        self.assertEqual(code, 0)
        self.assertIn("v9.9.9 is out", text)

    def test_current_version_reported(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag",
                return_value=f"v{core.VERSION}"):
            code, text = self.run_cli(["update-check"])
        self.assertEqual(code, 0)
        self.assertIn("latest version", text)

    def test_unreachable_github(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag", return_value=None):
            code, text = self.run_cli(["update-check"])
        self.assertEqual(code, 1)
        self.assertIn("Could not reach GitHub", text)


class SourcesCliTests(CliTestCase):
    def test_sources_add_list_remove(self):
        code, _text = self.run_cli(
            ["sources", "add", "Example", "https://example.com/?q={q}"])
        self.assertEqual(code, 0)
        _code, text = self.run_cli(["sources", "list"])
        self.assertIn("Example", text)
        code, _text = self.run_cli(["sources", "remove", "Example"])
        self.assertEqual(code, 0)
        _code, text = self.run_cli(["sources", "list"])
        self.assertNotIn("Example", text)

    def test_sources_defaults(self):
        self.run_cli(["sources", "add", "Example",
                      "https://example.com/?q={q}"])
        self.run_cli(["sources", "defaults"])
        _code, text = self.run_cli(["sources", "list"])
        self.assertIn("Hugging Face Datasets", text)
        self.assertNotIn("Example", text)


class SelfTestTests(CliTestCase):
    """v0.1.3 T42: --self-test exercises search, sniff, convert."""

    def test_self_test_passes_with_mocked_network(self):
        item = {"title": "a/b", "url": "https://example.com/a/b",
                "source": "Hugging Face"}
        with unittest.mock.patch.object(
                apis, "search_github", return_value=[item]), \
                unittest.mock.patch.object(
                    apis, "search_huggingface", return_value=[]), \
                unittest.mock.patch.object(
                    apis, "sniff_item",
                    return_value={"status": "ready", "detail": "ok"}):
            code, text = self.run_cli(["--self-test"])
        self.assertEqual(code, 0)
        self.assertIn("PASS: Search", text)
        self.assertIn("PASS: Sniff", text)
        self.assertIn("PASS: Convert", text)
        self.assertIn("3 of 3 checks passed", text)

    def test_self_test_fails_when_both_sources_are_down(self):
        with unittest.mock.patch.object(
                apis, "search_github",
                side_effect=apis.SearchError("down")), \
                unittest.mock.patch.object(
                    apis, "search_huggingface",
                    side_effect=apis.SearchError("down")):
            code, text = self.run_cli(["--self-test"])
        self.assertEqual(code, 1)
        self.assertIn("FAIL: Search", text)
        self.assertIn("FAIL: Sniff", text)
        self.assertIn("PASS: Convert", text)

    def test_no_command_prints_help_and_returns_two(self):
        code, text = self.run_cli([])
        self.assertEqual(code, 2)
        self.assertIn("scout-cli", text)


if __name__ == "__main__":
    unittest.main()
