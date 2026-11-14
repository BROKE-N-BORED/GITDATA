"""Tests for download + convert (T35).

The network is mocked at the apis boundary: sniff_* returns a
canned file list, download_file writes canned bytes. No live
network, no Qt.
"""

import json
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from scout import apis, download

JSONL = ('{"prompt": "a", "completion": "b"}\n'
         '{"prompt": "c", "completion": "d"}\n')
CSV = "prompt,completion\nWhat?,That.\nWho?,Them.\n"
INSTRUCTION_JSON = json.dumps([
    {"instruction": "Add", "input": "1+1", "output": "2"},
    {"instruction": "Add", "input": "2+2", "output": "4"},
])


def _hf_item():
    return {"title": "Tiny Data", "source": "Hugging Face",
            "url": "https://huggingface.co/datasets/acme/tiny-data"}


def _gh_item():
    return {"title": "Tiny Repo", "source": "GitHub",
            "url": "https://github.com/acme/tiny-repo"}


class SafeNameTests(unittest.TestCase):
    def test_spaces_and_slashes_become_dashes(self):
        self.assertEqual(download.safe_name("My Cool/Data: v2"),
                         "My-Cool-Data-v2")

    def test_empty_name_gets_a_fallback(self):
        self.assertEqual(download.safe_name("///"), "dataset")


class HuggingFaceDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_jsonl_file_converts_to_stella_jsonl(self):
        def fake_download(url, dest, progress=None, chunk=262144):
            Path(dest).write_text(JSONL, encoding="utf-8")
            return len(JSONL)

        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["data/train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                unittest.mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["state"], "converted")
        self.assertEqual(report["rows"], 2)
        self.assertEqual(len(report["outputs"]), 1)
        out_path = Path(report["outputs"][0])
        self.assertTrue(out_path.exists())
        self.assertEqual(out_path.suffix, ".jsonl")
        lines = out_path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["prompt"], "a")
        # The temp work dir is cleaned up.
        self.assertEqual(list(self.out.glob(".tmp-*")), [])

    def test_parquet_is_named_as_skipped_not_converted(self):
        def fake_download(url, dest, progress=None, chunk=262144):
            Path(dest).write_text(JSONL, encoding="utf-8")
            return len(JSONL)

        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "convert",
                              "files": ["data/train.parquet",
                                        "data/train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                unittest.mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertTrue(report["ok"])
        self.assertIn("train.parquet", report["message"])
        self.assertIn("Parquet", report["message"])

    def test_no_convertible_files_reports_plainly(self):
        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "none",
                              "files": ["data/train.parquet"],
                              "total_size": 100, "detail": ""}):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "found")
        self.assertIn("No convertible files", report["message"])

    def test_sniff_failure_reports_plainly(self):
        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "unknown", "files": [],
                              "total_size": None,
                              "detail": "the dataset was not found"}):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertIn("not found", report["message"])

    def test_one_row_is_not_enough(self):
        def fake_download(url, dest, progress=None, chunk=262144):
            Path(dest).write_text(
                '{"prompt": "a", "completion": "b"}\n', encoding="utf-8")
            return 30

        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["data/train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                unittest.mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertEqual(report["outputs"], [])
        self.assertIn("at least 2", report["message"])


class GitHubDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_archive_download(self, files):
        """A download_file stand-in that writes a zip of files."""
        def fake_download(url, dest, progress=None, chunk=262144):
            with zipfile.ZipFile(dest, "w") as zf:
                for name, text in files.items():
                    zf.writestr(name, text)
            return Path(dest).stat().st_size
        return fake_download

    def test_repo_archive_converts_its_json(self):
        with unittest.mock.patch.object(
                apis, "sniff_github",
                return_value={"status": "convert",
                              "files": ["data.json"],
                              "total_size": None, "detail": "",
                              "branch": "main"}), \
                unittest.mock.patch.object(
                    apis, "download_file",
                    side_effect=self._fake_archive_download(
                        {"tiny-repo-main/data.json": INSTRUCTION_JSON})):
            report = download.download_and_convert(_gh_item(), self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["state"], "converted")
        self.assertEqual(report["rows"], 2)
        out_path = Path(report["outputs"][0])
        rows = [json.loads(line)
                for line in out_path.read_text(
                    encoding="utf-8").strip().split("\n")]
        self.assertEqual(rows[0]["prompt"], "Add\n\n1+1")

    def test_zip_slip_member_is_ignored(self):
        with unittest.mock.patch.object(
                apis, "sniff_github",
                return_value={"status": "convert",
                              "files": ["data.json"],
                              "total_size": None, "detail": "",
                              "branch": "main"}), \
                unittest.mock.patch.object(
                    apis, "download_file",
                    side_effect=self._fake_archive_download(
                        {"tiny-repo-main/data.json": INSTRUCTION_JSON,
                         "../../escape.jsonl": JSONL})):
            report = download.download_and_convert(_gh_item(), self.out)
        self.assertTrue(report["ok"])
        # The escape file must not land outside the work dir.
        self.assertFalse((self.out.parent / "escape.jsonl").exists())
        self.assertFalse((self.out / "escape.jsonl").exists())

    def test_bad_archive_reports_plainly(self):
        def fake_download(url, dest, progress=None, chunk=262144):
            Path(dest).write_bytes(b"this is not a zip")
            return 17

        with unittest.mock.patch.object(
                apis, "sniff_github",
                return_value={"status": "convert",
                              "files": ["data.json"],
                              "total_size": None, "detail": "",
                              "branch": "main"}), \
                unittest.mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(_gh_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertIn("not a zip", report["message"])


class DirectLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_direct_csv_link_converts(self):
        def fake_download(url, dest, progress=None, chunk=262144):
            Path(dest).write_text(CSV, encoding="utf-8")
            return len(CSV)

        item = {"title": "Direct CSV", "source": "Found by hand",
                "url": "https://example.com/data.csv"}
        with unittest.mock.patch.object(
                apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(item, self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], 2)

    def test_direct_parquet_link_refuses_plainly(self):
        item = {"title": "Direct Parquet", "source": "Found by hand",
                "url": "https://example.com/data.parquet"}
        report = download.download_and_convert(item, self.out)
        self.assertFalse(report["ok"])
        self.assertIn("only download", report["message"])


class StopTests(unittest.TestCase):
    def test_stop_event_halts_between_files(self):
        import threading
        stop = threading.Event()
        stop.set()
        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["a.jsonl", "b.jsonl"],
                              "total_size": 100, "detail": ""}):
            with tempfile.TemporaryDirectory() as tmp:
                report = download.download_and_convert(
                    _hf_item(), Path(tmp), stop=stop)
        self.assertFalse(report["ok"])
        self.assertEqual(report["outputs"], [])


if __name__ == "__main__":
    unittest.main()
