"""Regression tests for v0.1.3 core correctness (T1-T16).

P0 data loss + P1 correctness fixes, each class tied to its audit
item. The network is mocked at the apis boundary; Qt tests run
offscreen with a temp SCOUT_HOME. No live network, no real files
outside the temp dirs.
"""
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from scout import apis, cli, core, download  # noqa: E402
from scout.gui import (  # noqa: E402
    LinkCheckWorker, MainWindow, UpdateCheckWorker,
)


class FakeResponse:
    """A canned urllib response (same shape as tests/test_apis.py)."""

    def __init__(self, payload, status=200, headers=None):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._raw = payload
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self._raw[self._pos:]
            self._pos = len(self._raw)
            return chunk
        chunk = self._raw[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, headers=None, body=b""):
    return urllib.error.HTTPError(url, code, "error", headers or {},
                                  io.BytesIO(body))


def _route(mapping):
    """A urlopen stand-in that answers by URL substring."""
    def fake_urlopen(request, timeout=None):
        url = request.full_url
        for needle, response in mapping:
            if needle in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected URL: {url}")
    return fake_urlopen


class TempHomeMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SCOUT_HOME"] = self.tmp.name
        core._RING.clear()

    def tearDown(self):
        os.environ.pop("SCOUT_HOME", None)


class CliTestCase(TempHomeMixin, unittest.TestCase):
    def run_cli(self, argv):
        """Run the CLI, capturing stdout. Returns (exit code, text)."""
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()


_APP = None


def get_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance()
        if _APP is None:
            _APP = QApplication([])
    return _APP


class GuiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        get_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SCOUT_HOME"] = self.tmp.name
        core._RING.clear()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def tearDown(self):
        os.environ.pop("SCOUT_HOME", None)

    def _wait_for_check_button(self, timeout_ms=5000):
        deadline = timeout_ms // 50
        while (not self.window.check_button.isEnabled()
               and deadline > 0):
            QTest.qWait(50)
            deadline -= 1
        self.assertTrue(self.window.check_button.isEnabled())

    def _wait_for_search_button(self, timeout_ms=5000):
        deadline = timeout_ms // 50
        while (not self.window.search_button.isEnabled()
               and deadline > 0):
            QTest.qWait(50)
            deadline -= 1
        self.assertTrue(self.window.search_button.isEnabled())


JSONL_A = ('{"prompt": "a", "completion": "b"}\n'
           '{"prompt": "c", "completion": "d"}\n')
JSONL_B = ('{"prompt": "x", "completion": "y"}\n'
           '{"prompt": "z", "completion": "w"}\n')


def _hf_item(url="https://huggingface.co/datasets/acme/tiny-data"):
    return {"title": "Tiny Data", "source": "Hugging Face", "url": url}


def _gh_item():
    return {"title": "Tiny Repo", "source": "GitHub",
            "url": "https://github.com/acme/tiny-repo"}


def _item(url, title="Item"):
    return core.normalize_item({"title": title, "url": url,
                                "category": "reasoning",
                                "source": "GitHub", "stars": 5,
                                "updated": "", "license": "",
                                "notes": ""})


# ---------------------------------------------------------------- T1, T2, T9, T10 (download)

class OutputNameTests(unittest.TestCase):
    """T1: the source folder stays in the output name."""

    def test_folder_prefix_kept(self):
        self.assertEqual(download.output_name("train/train.jsonl"),
                         "train-train.jsonl")
        self.assertEqual(
            download.output_name("validation/train.jsonl"),
            "validation-train.jsonl")

    def test_bare_filename_unchanged(self):
        self.assertEqual(download.output_name("train.jsonl"),
                         "train.jsonl")

    def test_flatten_collision_is_real(self):
        # "a-b/train.jsonl" and "a/b-train.jsonl" flatten to the
        # same name: the convert loop must number the second one.
        self.assertEqual(download.output_name("a-b/train.jsonl"),
                         download.output_name("a/b-train.jsonl"))

    def test_empty_name_gets_a_fallback(self):
        self.assertEqual(download.output_name(""), "file.jsonl")


class SameBasenameTests(unittest.TestCase):
    """T1: two files sharing a basename must not overwrite each
    other, and the row count counts only rows actually on disk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_hf_download(self):
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            if "validation/train.jsonl" in url:
                Path(dest).write_text(JSONL_B, encoding="utf-8")
            else:
                Path(dest).write_text(JSONL_A, encoding="utf-8")
            return Path(dest).stat().st_size
        return fake_download

    def test_hf_same_basename_in_two_folders(self):
        with mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["train/train.jsonl",
                                        "validation/train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                mock.patch.object(
                    apis, "download_file",
                    side_effect=self._fake_hf_download()):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], 4)
        self.assertEqual(len(report["outputs"]), 2)
        names = sorted(Path(p).name for p in report["outputs"])
        self.assertEqual(names, ["train-train.jsonl",
                                 "validation-train.jsonl"])
        first = [json.loads(line) for line in
                 Path(report["outputs"][0]).read_text(
                     encoding="utf-8").strip().split("\n")]
        second = [json.loads(line) for line in
                  Path(report["outputs"][1]).read_text(
                      encoding="utf-8").strip().split("\n")]
        prompts = {row["prompt"] for row in first + second}
        self.assertEqual(prompts, {"a", "c", "x", "z"})

    def test_github_same_basename_in_two_folders(self):
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            with zipfile.ZipFile(dest, "w") as zf:
                zf.writestr("tiny-repo-main/train/train.jsonl", JSONL_A)
                zf.writestr("tiny-repo-main/validation/train.jsonl",
                            JSONL_B)
            return Path(dest).stat().st_size

        with mock.patch.object(
                apis, "sniff_github",
                return_value={"status": "convert",
                              "files": ["train/train.jsonl"],
                              "total_size": None, "detail": "",
                              "branch": "main"}), \
                mock.patch.object(
                    apis, "content_length", return_value=None), \
                mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(_gh_item(), self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], 4)
        self.assertEqual(len(report["outputs"]), 2)
        names = sorted(Path(p).name for p in report["outputs"])
        self.assertEqual(names, ["tiny-repo-main-train-train.jsonl",
                                 "tiny-repo-main-validation-train.jsonl"])

    def test_flatten_collision_gets_a_number(self):
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            if "a/b-train.jsonl" in url:
                Path(dest).write_text(JSONL_B, encoding="utf-8")
            else:
                Path(dest).write_text(JSONL_A, encoding="utf-8")
            return Path(dest).stat().st_size

        with mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["a-b/train.jsonl",
                                        "a/b-train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], 4)
        names = sorted(Path(p).name for p in report["outputs"])
        self.assertEqual(names, ["a-b-train-2.jsonl", "a-b-train.jsonl"])


class DatasetFolderTests(unittest.TestCase):
    """T2: the output folder is unique per dataset, not per title."""

    def test_same_dataset_same_folder(self):
        item = _hf_item()
        self.assertEqual(download.dataset_folder_name(item),
                         download.dataset_folder_name(dict(item)))

    def test_same_title_different_url_different_folder(self):
        one = _hf_item("https://huggingface.co/datasets/acme/one")
        two = _hf_item("https://huggingface.co/datasets/acme/two")
        self.assertEqual(one["title"], two["title"])
        self.assertNotEqual(download.dataset_folder_name(one),
                            download.dataset_folder_name(two))

    def test_same_title_datasets_do_not_clobber_each_other(self):
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            if "/acme/two/" in url:
                Path(dest).write_text(JSONL_B, encoding="utf-8")
            else:
                Path(dest).write_text(JSONL_A, encoding="utf-8")
            return Path(dest).stat().st_size

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch.object(
                    apis, "sniff_huggingface",
                    return_value={"status": "ready",
                                  "files": ["train.jsonl"],
                                  "total_size": 100, "detail": ""}), \
                    mock.patch.object(
                        apis, "download_file",
                        side_effect=fake_download):
                first = download.download_and_convert(
                    _hf_item(
                        "https://huggingface.co/datasets/acme/one"),
                    out)
                first_path = Path(first["outputs"][0])
                first_text = first_path.read_text(encoding="utf-8")
                second = download.download_and_convert(
                    _hf_item(
                        "https://huggingface.co/datasets/acme/two"),
                    out)
            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertNotEqual(first_path.parent,
                                Path(second["outputs"][0]).parent)
            # The first dataset's output is untouched by the run.
            self.assertTrue(first_path.exists())
            self.assertEqual(first_path.read_text(encoding="utf-8"),
                             first_text)
            self.assertIn('"a"', first_text)
            self.assertIn(
                '"x"',
                Path(second["outputs"][0]).read_text(encoding="utf-8"))


class OneBadFileTests(unittest.TestCase):
    """T9: one bad file must not kill the run or orphan outputs."""

    def test_good_file_converts_bad_csv_reported(self):
        giant_csv = ("prompt,completion\n" + "x" * 200000 + ",done\n")

        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            if str(dest).endswith(".csv"):
                Path(dest).write_text(giant_csv, encoding="utf-8")
            else:
                Path(dest).write_text(JSONL_A, encoding="utf-8")
            return Path(dest).stat().st_size

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch.object(
                    apis, "sniff_huggingface",
                    return_value={"status": "ready",
                                  "files": ["good.jsonl", "bad.csv"],
                                  "total_size": 100, "detail": ""}), \
                    mock.patch.object(
                        apis, "download_file",
                        side_effect=fake_download):
                report = download.download_and_convert(_hf_item(), out)
            self.assertTrue(report["ok"])
            # Only the rows actually on disk are counted.
            self.assertEqual(report["rows"], 2)
            self.assertEqual(len(report["outputs"]), 1)
            self.assertIn("could not convert bad.csv", report["message"])
            target = Path(report["outputs"][0]).parent
            self.assertEqual(
                sorted(p.name for p in target.glob("*.jsonl")),
                ["good.jsonl"])


class DownloadCapTests(unittest.TestCase):
    """T10: the 200 MB data cap and the 500 MB archive cap."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hf_download_gets_the_data_cap(self):
        seen = {}

        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            seen["limit"] = limit
            Path(dest).write_text(JSONL_A, encoding="utf-8")
            return len(JSONL_A)

        with mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            download.download_and_convert(_hf_item(), self.out)
        self.assertEqual(seen["limit"], download.MAX_DATA_FILE_BYTES)

    def test_direct_link_download_gets_the_data_cap(self):
        seen = {}

        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            seen["limit"] = limit
            Path(dest).write_text(JSONL_A, encoding="utf-8")
            return len(JSONL_A)

        item = {"title": "Direct", "source": "Found by hand",
                "url": "https://example.com/data.jsonl"}
        with mock.patch.object(
                apis, "download_file", side_effect=fake_download):
            download.download_and_convert(item, self.out)
        self.assertEqual(seen["limit"], download.MAX_DATA_FILE_BYTES)

    def test_github_archive_download_gets_the_archive_cap(self):
        seen = {}

        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            seen["limit"] = limit
            with zipfile.ZipFile(dest, "w") as zf:
                zf.writestr("tiny-repo-main/train.jsonl", JSONL_A)
            return Path(dest).stat().st_size

        with mock.patch.object(
                apis, "sniff_github",
                return_value={"status": "convert",
                              "files": ["train.jsonl"],
                              "total_size": None, "detail": "",
                              "branch": "main"}), \
                mock.patch.object(
                    apis, "content_length", return_value=None), \
                mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            download.download_and_convert(_gh_item(), self.out)
        self.assertEqual(seen["limit"], download.MAX_ARCHIVE_BYTES)

    def test_over_cap_file_skipped_before_convert(self):
        # The convert loop is the backstop for every branch: a
        # file that arrived over the cap is skipped before it is
        # read into memory. The cap is patched small for the test.
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            Path(dest).write_text(JSONL_A, encoding="utf-8")
            return len(JSONL_A)

        with mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                mock.patch.object(
                    apis, "download_file", side_effect=fake_download), \
                mock.patch.object(download, "MAX_DATA_FILE_BYTES", 10):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertEqual(report["outputs"], [])
        self.assertIn("over the 200 MB file limit", report["message"])

    def test_over_cap_archive_refused_without_downloading(self):
        with mock.patch.object(
                apis, "sniff_github",
                return_value={"status": "convert",
                              "files": ["train.jsonl"],
                              "total_size": None, "detail": "",
                              "branch": "main"}), \
                mock.patch.object(
                    apis, "content_length",
                    return_value=download.MAX_ARCHIVE_BYTES + 1), \
                mock.patch.object(
                    apis, "download_file") as fake_download:
            report = download.download_and_convert(_gh_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertIn("500 MB", report["message"])
        fake_download.assert_not_called()


class DownloadRateLimitTests(unittest.TestCase):
    """T5: a rate limit during the sniff never crashes a download."""

    def test_hf_sniff_rate_limit_is_a_plain_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                    apis, "sniff_huggingface",
                    side_effect=core.RateLimitedError(None)):
                report = download.download_and_convert(_hf_item(),
                                                       Path(tmp))
        self.assertFalse(report["ok"])
        self.assertIn("Could not list the dataset files",
                      report["message"])

    def test_github_sniff_rate_limit_is_a_plain_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                    apis, "sniff_github",
                    side_effect=core.RateLimitedError(None)):
                report = download.download_and_convert(_gh_item(),
                                                       Path(tmp))
        self.assertFalse(report["ok"])
        self.assertIn("Could not list the repo files", report["message"])


# ---------------------------------------------------------------- T4, T12, T13, T7 (core)

class BackupRecoveryTests(TempHomeMixin, unittest.TestCase):
    """T4: a corrupt list file recovers from the .bak backup, the
    backup is never overwritten with the corrupt file, and the
    user is told what was recovered."""

    def _bak_path(self):
        return core.list_path().with_name("mylist.json.bak")

    def _bak_titles(self):
        data = json.loads(self._bak_path().read_text(encoding="utf-8"))
        return [item["title"] for col in data["collections"]
                for item in col["items"]]

    def test_corrupt_list_loads_the_backup(self):
        core.save_list([_item("https://example.com/a", "A")])
        core.save_list([_item("https://example.com/b", "B")])
        core.list_path().write_text("{not json", encoding="utf-8")
        core.list_load_notice()  # drain any earlier notes
        loaded = core.load_list()
        self.assertEqual([item["title"] for item in loaded], ["A"])
        notice = core.list_load_notice()
        self.assertIn("backup", notice)
        self.assertIn("1 link recovered", notice)

    def test_later_save_does_not_overwrite_backup_with_corrupt_file(self):
        core.save_list([_item("https://example.com/a", "A")])
        core.save_list([_item("https://example.com/b", "B")])
        core.list_path().write_text("{not json", encoding="utf-8")
        core.load_list()  # recover from the backup
        core.save_list([_item("https://example.com/c", "C")])
        # The backup still holds A: the corrupt file never replaced it.
        self.assertEqual(self._bak_titles(), ["A"])
        self.assertEqual([item["title"] for item in core.load_list()],
                         ["C"])

    def test_corrupt_list_and_backup_starts_empty_with_notice(self):
        core.list_path().parent.mkdir(parents=True, exist_ok=True)
        core.list_path().write_text("{not json", encoding="utf-8")
        self._bak_path().write_text("{also not json", encoding="utf-8")
        core.list_load_notice()
        self.assertEqual(core.load_list(), [])
        self.assertIn("started with an empty list",
                      core.list_load_notice())

    def test_dropped_rows_are_noticed(self):
        raw = {"items": [
            {"title": "Good", "url": "https://example.com/good"},
            "not a dict at all",
            {"title": "No URL here"},
            {"title": "Also good", "url": "https://example.com/good2"},
        ]}
        core.list_path().parent.mkdir(parents=True, exist_ok=True)
        core.list_path().write_text(json.dumps(raw), encoding="utf-8")
        core.list_load_notice()
        loaded = core.load_list()
        self.assertEqual([item["title"] for item in loaded],
                         ["Good", "Also good"])
        self.assertIn("2 saved rows could not be read and were left out.",
                      core.list_load_notice())

    def test_notice_is_reported_once(self):
        core.list_path().parent.mkdir(parents=True, exist_ok=True)
        core.list_path().write_text("{not json", encoding="utf-8")
        core.list_load_notice()
        core.load_list()
        self.assertIn("damaged", core.list_load_notice())
        self.assertEqual(core.list_load_notice(), "")


class SearchCacheHardeningTests(TempHomeMixin, unittest.TestCase):
    """T12: the search cache is hardened like the list file."""

    def _write_cache(self, entries):
        path = core.home_dir() / "cache.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"entries": entries}),
                        encoding="utf-8")
        return path

    def test_bad_rows_and_dates_are_cleaned_on_load(self):
        now = datetime.now(timezone.utc).isoformat()
        key = core._cache_key("math", "reasoning")
        self._write_cache({key: {
            "query": "math", "category": "reasoning", "saved": now,
            "results": [
                "not a dict",
                {"title": "A", "url": "https://example.com/a",
                 "updated": 12345},
                {"title": "B", "url": "https://example.com/b",
                 "updated": "2026-11-01T00:00:00Z"},
            ]}})
        loaded = core.load_search_cache("math", "reasoning")
        self.assertEqual([row["title"] for row in loaded], ["A", "B"])
        self.assertEqual(loaded[0]["updated"], "")

    def test_eviction_survives_non_dict_entries(self):
        entries = {"junk": "not a dict"}
        for n in range(19):
            entries[core._cache_key(f"q{n}", "all")] = {
                "query": f"q{n}", "category": "all",
                "saved": "2020-01-01T00:00:00+00:00", "results": []}
        path = self._write_cache(entries)
        # One more entry takes it past the cap of 20: eviction must
        # not raise, and the non-dict entry goes first.
        core.save_search_cache("new", "all", [])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), 20)
        self.assertNotIn("junk", data["entries"])
        self.assertIn(core._cache_key("new", "all"), data["entries"])


class ListGenerationTests(TempHomeMixin, unittest.TestCase):
    """T13: a save refuses when the file changed since it was read."""

    def test_generation_empty_without_a_file(self):
        self.assertEqual(core.list_generation(), "")

    def test_generation_changes_after_a_save(self):
        before = core.list_generation()
        core.save_list([_item("https://example.com/a", "A")])
        after = core.list_generation()
        self.assertNotEqual(before, after)
        self.assertEqual(after, core.list_generation())

    def test_matching_generation_saves(self):
        core.save_list([_item("https://example.com/a", "A")])
        gen = core.list_generation()
        core.save_list([_item("https://example.com/b", "B")],
                       expected_generation=gen)
        self.assertEqual(
            [item["title"] for item in core.load_list()], ["B"])

    def test_stale_generation_refuses_and_keeps_the_file(self):
        core.save_list([_item("https://example.com/a", "A")])
        gen = core.list_generation()
        # A CLI save while the GUI was open.
        core.save_list([_item("https://example.com/c", "C")])
        with self.assertRaises(core.ListChangedError):
            core.save_list([_item("https://example.com/b", "B")],
                           expected_generation=gen)
        self.assertEqual(
            [item["title"] for item in core.load_list()], ["C"])

    def test_collections_save_checks_generation_too(self):
        core.save_collections("One", [
            {"name": "One",
             "items": [_item("https://example.com/a", "A")]}])
        gen = core.list_generation()
        core.save_list([_item("https://example.com/c", "C")])
        with self.assertRaises(core.ListChangedError):
            core.save_collections(
                "One",
                [{"name": "One",
                  "items": [_item("https://example.com/b", "B")]}],
                expected_generation=gen)


class MalformedPayloadTests(unittest.TestCase):
    """T7: malformed payloads never raise; bad rows are skipped."""

    def test_github_non_list_items(self):
        self.assertEqual(
            core.parse_github_results({"items": "not a list"}, "all"),
            [])

    def test_github_bad_rows_and_license_shapes(self):
        payload = {"items": [
            "not a dict",
            {"full_name": "a/b", "license": "MIT",
             "topics": "not a list"},
            {"full_name": "c/d", "license": {"spdx_id": "Apache-2.0"}},
            {"full_name": "e/f", "license": 42},
        ]}
        items = core.parse_github_results(payload, "all")
        self.assertEqual([item["title"] for item in items],
                         ["a/b", "c/d", "e/f"])
        self.assertEqual(items[0]["license"], "MIT")
        self.assertEqual(items[0]["topics"], [])
        self.assertEqual(items[1]["license"], "Apache-2.0")
        self.assertEqual(items[2]["license"], "")

    def test_hf_bad_rows_and_tags(self):
        payload = [
            "not a dict",
            {"id": 123, "tags": "not a list"},
            {"id": "x/y", "tags": ["license:mit"]},
        ]
        items = core.parse_hf_results(payload, "all")
        self.assertEqual([item["title"] for item in items],
                         ["123", "x/y"])
        self.assertEqual(
            items[0]["url"], "https://huggingface.co/datasets/123")
        self.assertEqual(items[0]["topics"], [])
        self.assertEqual(items[1]["license"], "MIT")

    def test_parse_iso_date_rejects_non_strings(self):
        self.assertIsNone(core.parse_iso_date(12345))
        self.assertIsNone(core.parse_iso_date(None))
        self.assertIsNone(core.parse_iso_date("not a date"))

    def test_filter_results_skips_non_dict_rows(self):
        rows = ["not a dict",
                {"title": "A", "url": "u", "stars": 5, "updated": ""}]
        kept = core.filter_results(rows, False, None, 0)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["title"], "A")


class RateLimitMessageTests(unittest.TestCase):
    """T6/T14: the limit is per minute, not per hour."""

    def test_message_says_per_minute(self):
        message = str(core.RateLimitedError(None))
        self.assertIn("per minute", message)
        self.assertNotIn("per hour", message)


# ---------------------------------------------------------------- T5, T6, T7, T8, T10, T14, T15 (apis)

class HttpErrorMessageTests(unittest.TestCase):
    """T6: a 403 says what it really is, naming the right service."""

    def test_hf_403_names_hugging_face_only(self):
        err = http_error("https://huggingface.co/api/datasets", 403)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(apis.SearchError) as ctx:
                apis.search_huggingface("math", "training")
        message = str(ctx.exception)
        self.assertIn("Hugging Face", message)
        self.assertNotIn("GitHub", message)
        self.assertNotIn("token", message)

    def test_hf_401_is_not_a_rejected_github_token(self):
        err = http_error("https://huggingface.co/api/datasets", 401)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(apis.SearchError) as ctx:
                apis.search_huggingface("math", "training")
        message = str(ctx.exception)
        self.assertIn("Hugging Face", message)
        self.assertNotIn("GitHub", message)
        self.assertNotIn("token", message)

    def test_github_403_private_repo_is_not_a_rate_limit(self):
        err = http_error(
            "https://api.github.com/x", 403,
            body=b"Resource not accessible by integration")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(apis.SearchError) as ctx:
                apis.search_github("q", "all")
        self.assertIn("private repo", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, core.RateLimitedError)

    def test_github_403_abuse_block_names_the_block(self):
        err = http_error("https://api.github.com/x", 403,
                         body=b"abuse detection")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(apis.SearchError) as ctx:
                apis.search_github("q", "all")
        self.assertIn("briefly blocked", str(ctx.exception))

    def test_github_403_rate_limit_body_is_rate_limited(self):
        err = http_error("https://api.github.com/x", 403,
                         {"X-RateLimit-Reset": "1794629400"},
                         body=b"API rate limit exceeded for user")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(core.RateLimitedError):
                apis.search_github("q", "all")


class TruncatedReplyTests(unittest.TestCase):
    """T7: a truncated or garbled reply is a plain error, not a crash."""

    def test_incomplete_read_is_a_search_error(self):
        with mock.patch(
                "urllib.request.urlopen",
                side_effect=http.client.IncompleteRead(b"part", 100)):
            with self.assertRaises(apis.SearchError) as ctx:
                apis.search_github("q", "all")
        self.assertIn("cut short", str(ctx.exception))

    def test_bad_status_line_is_a_search_error(self):
        with mock.patch(
                "urllib.request.urlopen",
                side_effect=http.client.BadStatusLine("garbage")):
            with self.assertRaises(apis.SearchError) as ctx:
                apis.search_huggingface("math", "training")
        self.assertIn("cut short", str(ctx.exception))

    def test_check_link_truncated_is_unknown(self):
        with mock.patch(
                "urllib.request.urlopen",
                side_effect=http.client.IncompleteRead(b"part", 100)):
            self.assertEqual(apis.check_link("https://example.com/"),
                             "unknown")


class SniffRateLimitTests(unittest.TestCase):
    """T5: a rate limit in a sniff or update check is "unknown",
    never an exception."""

    def test_github_sniff_rate_limit_is_unknown(self):
        err = http_error("https://api.github.com/x", 403,
                         {"X-RateLimit-Reset": "1794629400"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            result = apis.sniff_github("acme/tiny")
        self.assertEqual(result["status"], "unknown")
        self.assertIn("GitHub search limit reached", result["detail"])

    def test_hf_sniff_403_is_unknown(self):
        err = http_error("https://huggingface.co/api/datasets/x", 403)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            result = apis.sniff_huggingface("acme/tiny")
        self.assertEqual(result["status"], "unknown")
        self.assertIn("Hugging Face", result["detail"])

    def test_update_check_rate_limit_is_none(self):
        err = http_error("https://api.github.com/x", 403,
                         {"X-RateLimit-Reset": "1794629400"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertIsNone(apis.latest_release_tag())


class DownloadFileLimitTests(unittest.TestCase):
    """T8/T10: short and over-cap downloads are refused."""

    def test_short_download_refused_and_kept_for_retry(self):
        # T8: fewer bytes than promised is not a download. The
        # .part file is kept so the next run can resume (T27),
        # but the destination never appears.
        body = b"x" * 100
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.jsonl"
            part = dest.with_name(dest.name + ".part")
            with mock.patch(
                    "urllib.request.urlopen",
                    return_value=FakeResponse(
                        body, headers={"Content-Length": "5000"})):
                with self.assertRaises(apis.SearchError) as ctx:
                    apis.download_file("https://example.com/file.jsonl",
                                       dest)
            self.assertIn("part way through", str(ctx.exception))
            self.assertFalse(dest.exists())
            self.assertTrue(part.exists())
            self.assertEqual(part.read_bytes(), body)

    def test_over_limit_download_removes_the_partial(self):
        # T10: past the cap the download stops and the partial
        # goes: resuming a file that is too big is pointless.
        body = b"a" * 5000
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.jsonl"
            part = dest.with_name(dest.name + ".part")
            with mock.patch(
                    "urllib.request.urlopen",
                    return_value=FakeResponse(
                        body, headers={"Content-Length": "5000"})):
                with self.assertRaises(apis.SearchError) as ctx:
                    apis.download_file("https://example.com/file.jsonl",
                                       dest, chunk=1024, limit=100)
            self.assertIn("bigger than 100 bytes", str(ctx.exception))
            self.assertFalse(dest.exists())
            self.assertFalse(part.exists())

    def test_oversized_partial_refused_without_a_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.jsonl"
            part = dest.with_name(dest.name + ".part")
            part.write_bytes(b"a" * 200)
            with mock.patch(
                    "urllib.request.urlopen") as fake_open:
                with self.assertRaises(apis.SearchError) as ctx:
                    apis.download_file("https://example.com/file.jsonl",
                                       dest, limit=100)
            self.assertIn("bigger than 100 bytes", str(ctx.exception))
            fake_open.assert_not_called()
            self.assertFalse(part.exists())


class RateBucketTests(unittest.TestCase):
    """T14: the search quota is tracked apart from the core quota."""

    def setUp(self):
        self._saved = dict(apis.GITHUB_RATE)
        for key in apis.GITHUB_RATE:
            apis.GITHUB_RATE[key] = None

    def tearDown(self):
        apis.GITHUB_RATE.update(self._saved)

    def test_search_reply_updates_only_the_search_bucket(self):
        headers = {"X-RateLimit-Remaining": "7",
                   "X-RateLimit-Reset": "1794629400"}
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse({"items": []},
                                                  headers=headers)):
            apis.search_github("q", "all")
        self.assertEqual(apis.GITHUB_RATE["search_remaining"], 7)
        self.assertIsNotNone(apis.GITHUB_RATE["search_reset"])
        self.assertIsNone(apis.GITHUB_RATE["core_remaining"])
        self.assertIsNone(apis.GITHUB_RATE["core_reset"])
        self.assertEqual(apis.GITHUB_RATE["remaining"], 7)

    def test_core_reply_updates_only_the_core_bucket(self):
        search_headers = {"X-RateLimit-Remaining": "7",
                          "X-RateLimit-Reset": "1794629400"}
        core_headers = {"X-RateLimit-Remaining": "42",
                        "X-RateLimit-Reset": "1794629400"}
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(
                            {"items": []}, headers=search_headers)):
            apis.search_github("q", "all")
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(
                            {"tag_name": "v0.1.2"},
                            headers=core_headers)):
            apis.latest_release_tag()
        # The core reply must not move the search meter.
        self.assertEqual(apis.GITHUB_RATE["search_remaining"], 7)
        self.assertEqual(apis.GITHUB_RATE["core_remaining"], 42)
        self.assertEqual(apis.GITHUB_RATE["remaining"], 42)


class TrueSizeTests(unittest.TestCase):
    """T15: the true size comes from the ranged peek, not usedStorage."""

    def test_content_range_total_on_206(self):
        self.assertEqual(
            apis._total_from_headers(
                {"Content-Range": "bytes 0-8191/525629"}, 206),
            525629)

    def test_content_length_total_on_200(self):
        self.assertEqual(
            apis._total_from_headers({"Content-Length": "525629"}, 200),
            525629)

    def test_206_content_length_is_the_range_not_the_total(self):
        self.assertEqual(
            apis._total_from_headers(
                {"Content-Length": "8192",
                 "Content-Range": "bytes 0-8191/525629"}, 206),
            525629)

    def test_unknown_total_is_none(self):
        self.assertIsNone(
            apis._total_from_headers(
                {"Content-Range": "bytes 0-8191/*"}, 206))
        self.assertIsNone(apis._total_from_headers({}, 404))

    def test_sniff_prefers_the_peeked_total(self):
        detail = {"siblings": [{"rfilename": "train.jsonl"}],
                  "usedStorage": 5000000}  # wrong by ~10x
        peek = FakeResponse(
            JSONL_A, status=206,
            headers={"Content-Range": "bytes 0-8191/525629"})
        mapping = [
            ("api/datasets/acme/tiny", FakeResponse(detail)),
            ("resolve/main/train.jsonl", peek),
        ]
        with mock.patch("urllib.request.urlopen",
                        side_effect=_route(mapping)):
            result = apis.sniff_huggingface("acme/tiny")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["total_size"], 525629)


# ---------------------------------------------------------------- T4, T5, T7 (CLI)

class CliSearchTests(CliTestCase):
    """T7: one dead source never kills a CLI search."""

    def test_github_raises_huggingface_results_still_shown(self):
        fake_hf = [{"title": "hf", "url": "https://example.com/hf",
                    "category": "all", "source": "Hugging Face",
                    "stars": 2, "updated": "2026-11-01T00:00:00Z",
                    "description": "", "license": ""}]
        with mock.patch.object(
                apis, "search_github",
                side_effect=RuntimeError("boom")), \
             mock.patch.object(
                apis, "search_huggingface", return_value=fake_hf):
            code, text = self.run_cli(["search", "math"])
        self.assertEqual(code, 0)
        self.assertIn("hf [Hugging Face]", text)


class CliCheckTests(CliTestCase):
    """T5/T7: a check that raises is "unknown", never a crash."""

    def _seed(self):
        core.save_list([_item("https://example.com/a", "A"),
                        _item("https://example.com/b", "B")])

    def test_check_link_raising_is_unknown(self):
        self._seed()

        def fake_check(url):
            if url.endswith("/a"):
                raise RuntimeError("boom")
            return "ok"

        with mock.patch.object(apis, "check_link",
                               side_effect=fake_check):
            code, text = self.run_cli(["check"])
        self.assertEqual(code, 0)
        self.assertIn("A: unknown", text)
        self.assertIn("B: ok", text)
        self.assertIn("1 working, 0 dead, 1 unknown.", text)

    def test_check_link_rate_limited_is_unknown(self):
        self._seed()
        with mock.patch.object(
                apis, "check_link",
                side_effect=core.RateLimitedError(None)):
            code, text = self.run_cli(["check"])
        self.assertEqual(code, 0)
        self.assertIn("0 working, 0 dead, 2 unknown.", text)


class CliNoticeTests(CliTestCase):
    """T4: the CLI says what was recovered, like the GUI."""

    def test_list_reports_the_backup_recovery(self):
        core.save_list([_item("https://example.com/a", "A")])
        core.save_list([_item("https://example.com/b", "B")])
        core.list_path().write_text("{not json", encoding="utf-8")
        core.list_load_notice()
        code, text = self.run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertIn("backup", text)
        self.assertIn("A", text)


class CliUpdateCheckTests(CliTestCase):
    """T5: a rate limit never crashes the update check."""

    def test_rate_limit_is_a_plain_failure(self):
        with mock.patch.object(
                apis, "latest_release_tag",
                side_effect=core.RateLimitedError(None)):
            code, text = self.run_cli(["update-check"])
        self.assertEqual(code, 1)
        self.assertIn("Could not reach GitHub", text)


# ---------------------------------------------------------------- T3, T4, T5, T7, T11, T13, T14, T16 (GUI)

class LinkCheckedMatchingTests(GuiTestCase):
    """T3: link-check results land by URL, never by list index."""

    def setUp(self):
        super().setUp()
        self.window.my_list = [
            _item("https://example.com/a", "A"),
            _item("https://example.com/b", "B"),
            _item("https://example.com/c", "C"),
        ]
        core.save_list(self.window.my_list)
        self.window._note_list_loaded()
        self.window._refresh_list_tab()
        self.window._check_total = 3

    def test_result_lands_on_its_url(self):
        self.window._link_checked("https://example.com/b", "ok")
        statuses = {item["url"]: item["status"]
                    for item in self.window.my_list}
        self.assertEqual(statuses["https://example.com/b"], "ok")
        self.assertEqual(statuses["https://example.com/a"], "")
        self.assertEqual(statuses["https://example.com/c"], "")

    def test_result_for_a_removed_item_is_dropped(self):
        del self.window.my_list[1]  # B is gone mid-check
        before = self.window._check_done_count
        self.window._link_checked("https://example.com/b", "dead")
        statuses = [item["status"] for item in self.window.my_list]
        self.assertNotIn("dead", statuses)
        # The count still advances, so the run can finish.
        self.assertEqual(self.window._check_done_count, before + 1)

    def test_normalised_url_still_matches(self):
        self.window._link_checked("https://www.example.com/a/", "dead")
        self.assertEqual(self.window.my_list[0]["status"], "dead")


class StellaCheckedMatchingTests(GuiTestCase):
    """T3: STELLA badges land by URL, never by list index."""

    def _results(self):
        return [
            {"title": "A", "url": "https://example.com/a",
             "category": "all", "source": "GitHub", "stars": 1,
             "updated": "2026-11-01T00:00:00Z", "description": "",
             "license": ""},
            {"title": "B", "url": "https://example.com/b",
             "category": "all", "source": "Hugging Face", "stars": 2,
             "updated": "2026-11-01T00:00:00Z", "description": "",
             "license": ""},
        ]

    def test_badge_lands_on_the_checked_result(self):
        self.window._search_done(self._results())
        self.window._stella_target = "results"
        self.window._stella_checked("https://example.com/b", "ready",
                                    "shape ok", ["train.jsonl"], 123)
        by_url = {item["url"]: item for item in self.window.results}
        self.assertEqual(by_url["https://example.com/b"]
                         ["stella_status"], "ready")
        self.assertEqual(by_url["https://example.com/b"]
                         ["stella_files"], ["train.jsonl"])
        self.assertEqual(by_url["https://example.com/b"]
                         ["stella_size"], 123)
        self.assertIsNone(by_url["https://example.com/a"].get(
            "stella_status"))

    def test_badge_dropped_when_a_new_search_replaces_results(self):
        self.window._search_done(self._results())
        self.window._stella_target = "results"
        fresh = [
            {"title": "C", "url": "https://example.com/c",
             "category": "all", "source": "GitHub", "stars": 1,
             "updated": "2026-11-01T00:00:00Z", "description": "",
             "license": ""},
            {"title": "D", "url": "https://example.com/d",
             "category": "all", "source": "GitHub", "stars": 1,
             "updated": "2026-11-01T00:00:00Z", "description": "",
             "license": ""},
        ]
        self.window._search_done(fresh)
        # A late result for the old page must not badge the new one.
        self.window._stella_checked("https://example.com/a", "ready",
                                    "shape ok", ["train.jsonl"], 123)
        self.assertTrue(all(item.get("stella_status") is None
                            for item in self.window.results))

    def test_badge_dropped_for_a_removed_list_item(self):
        self.window.my_list = [_item("https://example.com/a", "A"),
                               _item("https://example.com/b", "B")]
        core.save_list(self.window.my_list)
        self.window._note_list_loaded()
        self.window._refresh_list_tab()
        self.window._stella_target = "list"
        del self.window.my_list[1]  # B is gone mid-check
        self.window._stella_checked("https://example.com/b", "ready",
                                    "shape ok", ["train.jsonl"], 123)
        # No badge lands on the surviving item (normalize_item
        # defaults stella_status to "", so falsy is the check).
        self.assertFalse(self.window.my_list[0].get("stella_status"))
        self.window._stella_checked("https://example.com/a", "ready",
                                    "shape ok", ["train.jsonl"], 50)
        self.assertEqual(
            self.window.my_list[0]["stella_status"], "ready")


class CheckGuardTests(GuiTestCase):
    """T3: remove, reorder, collection switch, and a new search
    wait while a check runs instead of moving the ground under it."""

    def setUp(self):
        super().setUp()
        self.window.my_list = [
            _item(f"https://example.com/{n}", f"Item {n}")
            for n in (1, 2, 3)]
        core.save_list(self.window.my_list)
        self.window._note_list_loaded()
        self.window._refresh_list_tab()

    def test_everything_waits_during_a_link_check(self):
        release = threading.Event()

        def slow_check(url):
            release.wait(timeout=10)
            return "ok"

        with mock.patch.object(apis, "check_link",
                               side_effect=slow_check):
            self.window.start_link_check()
            QTest.qWait(100)
            self.assertFalse(self.window.remove_button.isEnabled())
            self.assertFalse(self.window.clear_button.isEnabled())
            self.assertFalse(self.window.collection_combo.isEnabled())
            self.assertFalse(self.window.search_button.isEnabled())
            before = [item["url"] for item in self.window.my_list]
            self.window.list_table.selectRow(0)
            self.window.remove_selected()
            self.assertEqual(
                [item["url"] for item in self.window.my_list], before)
            self.assertIn("Wait for the check",
                          self.window.statusBar().currentMessage())
            self.window.clear_list()
            self.assertEqual(len(self.window.my_list), 3)
            self.window._move_selected(1)
            self.assertEqual(
                [item["url"] for item in self.window.my_list], before)
            self.window.start_search()
            self.assertIsNone(self.window.search_worker)
            self.assertIn("Wait for the check",
                          self.window.statusBar().currentMessage())
            self.window._switch_collection("Nope")
            self.assertEqual(self.window._active_collection, "My List")
            self.window.new_collection()
            _active, names = core.load_collections()
            self.assertEqual(names, ["My List"])
            release.set()
            self._wait_for_check_button()
        statuses = [item["status"] for item in self.window.my_list]
        self.assertEqual(statuses, ["ok", "ok", "ok"])
        self.assertTrue(self.window.remove_button.isEnabled())
        self.assertTrue(self.window.search_button.isEnabled())


class LinkCheckWorkerTests(GuiTestCase):
    """T3: the worker reports results by URL, not by index."""

    def test_emissions_carry_the_url(self):
        urls = ["https://example.com/a", "https://example.com/b",
                "https://example.com/c"]
        wanted = {"https://example.com/a": "ok",
                  "https://example.com/b": "dead",
                  "https://example.com/c": "unknown"}
        got = []
        done = []
        with mock.patch.object(
                apis, "check_link",
                side_effect=lambda url: wanted[url]):
            worker = LinkCheckWorker(urls)
            worker.item_checked.connect(
                lambda url, status: got.append((url, status)))
            worker.all_done.connect(lambda stopped: done.append(stopped))
            worker.start()
            deadline = 100
            while not done and deadline > 0:
                QTest.qWait(50)
                deadline -= 1
            worker.wait(5000)
        self.assertEqual(done, [False])
        self.assertEqual(dict(got), wanted)


class UpdateCheckWorkerTests(GuiTestCase):
    """T5: the update check always answers, never hangs."""

    def test_unexpected_error_becomes_none(self):
        got = []
        with mock.patch.object(
                apis, "latest_release_tag",
                side_effect=RuntimeError("boom")):
            worker = UpdateCheckWorker()
            worker.finished.connect(got.append)
            worker.start()
            deadline = 100
            while not got and deadline > 0:
                QTest.qWait(50)
                deadline -= 1
            worker.wait(5000)
        self.assertEqual(got, [None])


class SearchWorkerDeadSourceTests(GuiTestCase):
    """T7: a source that dies oddly is reported, not silent."""

    def test_unexpected_exception_is_a_named_failure(self):
        fake_hf = [{"title": "hf", "url": "https://example.com/hf",
                    "category": "all", "source": "Hugging Face",
                    "stars": 2, "updated": "2026-11-01T00:00:00Z",
                    "description": "", "license": ""}]
        with mock.patch.object(
                apis, "search_github",
                side_effect=RuntimeError("boom")), \
             mock.patch.object(
                apis, "search_huggingface", return_value=fake_hf):
            self.window.start_search()
            self._wait_for_search_button()
        self.assertEqual(self.window.results_table.rowCount(), 1)
        self.assertIn(
            "GitHub search did not answer, showing Hugging Face "
            "results only.",
            self.window.statusBar().currentMessage())


class ListNoticeGuiTests(GuiTestCase):
    """T4: the window says what was recovered from the backup."""

    def test_corrupt_list_notice_shows_in_status_bar(self):
        core.save_list([_item("https://example.com/a", "A")])
        core.save_list([_item("https://example.com/b", "B")])
        core.list_path().write_text("{not json", encoding="utf-8")
        core.list_load_notice()
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertIn("backup",
                      second.statusBar().currentMessage())
        self.assertEqual(
            [item["title"] for item in second.my_list], ["A"])


class SaveFailureGuiTests(GuiTestCase):
    """T11: a failed save warns in plain words; closing saves."""

    def test_save_failure_warns_plainly(self):
        self.window.my_list = [_item("https://example.com/a", "A")]
        with mock.patch.object(
                core, "save_list",
                side_effect=OSError("disk full")), \
             mock.patch.object(QMessageBox, "warning") as warning:
            saved = self.window._save_list()
        self.assertFalse(saved)
        self.assertTrue(warning.called)
        self.assertEqual(warning.call_args[0][1],
                         "Could not save My List")
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Could not save My List.")

    def test_close_saves_the_list(self):
        self.window.my_list = [_item("https://example.com/a", "A")]
        self.window._save_list()
        self.window.my_list[0]["status"] = "ok"
        self.window.close()
        loaded = core.load_list()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["status"], "ok")


class GuiMergeTests(GuiTestCase):
    """T13: a GUI save keeps CLI additions instead of clobbering
    them, and links removed here stay removed."""

    def test_gui_save_keeps_cli_additions(self):
        self.window.my_list = [_item("https://example.com/a", "A")]
        self.window._save_list()
        # A CLI save while the GUI was open adds B.
        core.save_list([_item("https://example.com/a", "A"),
                        _item("https://example.com/b", "B")])
        # The GUI user adds C in the meantime.
        self.window.my_list.append(
            _item("https://example.com/c", "C"))
        self.assertTrue(self.window._save_list())
        urls = {item["url"] for item in core.load_list()}
        self.assertEqual(urls, {"https://example.com/a",
                                "https://example.com/b",
                                "https://example.com/c"})
        self.assertIn("kept as well",
                      self.window.statusBar().currentMessage())

    def test_removed_links_stay_removed(self):
        self.window.my_list = [_item("https://example.com/a", "A"),
                               _item("https://example.com/b", "B")]
        self.window._save_list()
        # The CLI adds C while the GUI still shows A and B.
        core.save_list([_item("https://example.com/a", "A"),
                        _item("https://example.com/b", "B"),
                        _item("https://example.com/c", "C")])
        # The GUI user removes B, then saves.
        self.window.my_list = [_item("https://example.com/a", "A")]
        self.assertTrue(self.window._save_list())
        urls = {item["url"] for item in core.load_list()}
        self.assertEqual(urls, {"https://example.com/a",
                                "https://example.com/c"})


class RateMeterGuiTests(GuiTestCase):
    """T14: the meter shows the search quota and its reset time."""

    def setUp(self):
        super().setUp()
        self._saved = dict(apis.GITHUB_RATE)
        for key in apis.GITHUB_RATE:
            apis.GITHUB_RATE[key] = None
        self.addCleanup(self._restore_rate)

    def _restore_rate(self):
        apis.GITHUB_RATE.update(self._saved)

    def test_unknown_when_no_search_data(self):
        self.window._refresh_rate()
        self.assertEqual(self.window.rate_label.text(),
                         "GitHub: searches left unknown")

    def test_shows_searches_left_and_reset(self):
        reset = datetime(2026, 11, 15, 15, 30, tzinfo=timezone.utc)
        apis.GITHUB_RATE["search_remaining"] = 7
        apis.GITHUB_RATE["search_reset"] = reset
        self.window._refresh_rate()
        when = reset.astimezone().strftime("%H:%M")
        self.assertEqual(self.window.rate_label.text(),
                         f"GitHub: 7 searches left, resets {when}")

    def test_one_search_left_is_singular(self):
        apis.GITHUB_RATE["search_remaining"] = 1
        self.window._refresh_rate()
        self.assertEqual(self.window.rate_label.text(),
                         "GitHub: 1 search left")

    def test_core_quota_never_shows_as_searches(self):
        apis.GITHUB_RATE["core_remaining"] = 42
        apis.GITHUB_RATE["core_reset"] = datetime(
            2026, 11, 15, 15, 30, tzinfo=timezone.utc)
        self.window._refresh_rate()
        self.assertEqual(self.window.rate_label.text(),
                         "GitHub: searches left unknown")

    def test_update_check_refreshes_the_meter(self):
        apis.GITHUB_RATE["search_remaining"] = 5
        self.window._update_check_done("v0.1.2")
        self.assertEqual(self.window.rate_label.text(),
                         "GitHub: 5 searches left")


class CloseMidCheckTests(GuiTestCase):
    """T16: closing mid-link-check keeps the checked-so-far
    statuses on disk."""

    def test_checked_statuses_survive_the_close(self):
        self.window.my_list = [
            _item(f"https://example.com/{n}", f"Item {n}")
            for n in (1, 2, 3)]
        core.save_list(self.window.my_list)
        self.window._note_list_loaded()
        self.window._refresh_list_tab()
        self.window.show()
        QTest.qWait(50)
        release = threading.Event()

        def fake_check(url):
            if url.endswith("/1"):
                return "ok"
            release.wait(timeout=30)
            return "ok"

        with mock.patch.object(apis, "check_link",
                               side_effect=fake_check):
            self.window.start_link_check()
            deadline = 100
            while self.window._check_done_count < 1 and deadline > 0:
                QTest.qWait(50)
                deadline -= 1
            self.assertGreaterEqual(self.window._check_done_count, 1)
            timer = threading.Timer(1.0, release.set)
            timer.start()
            self.window.close()
            timer.join(timeout=5)
        # No event pumping from here on: read the disk state as
        # the app leaves it at exit.
        loaded = core.load_list()
        self.assertEqual(
            [item["status"] for item in loaded], ["ok", "ok", "ok"])


if __name__ == "__main__":
    unittest.main()
