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

# The download path asks for each file's declared size before
# downloading it (v0.1.4 T25). The network is mocked at the apis
# boundary in this module, so the size lookup answers "unknown"
# by default; tests that need a declared size patch it
# themselves.
_CONTENT_LENGTH_PATCHER = None


def setUpModule():
    global _CONTENT_LENGTH_PATCHER
    _CONTENT_LENGTH_PATCHER = unittest.mock.patch.object(
        apis, "content_length", return_value=None)
    _CONTENT_LENGTH_PATCHER.start()


def tearDownModule():
    if _CONTENT_LENGTH_PATCHER is not None:
        _CONTENT_LENGTH_PATCHER.stop()

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
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
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

    def test_parquet_converts_when_the_reader_is_installed(self):
        # T62: Parquet is no longer skipped — with the reader
        # installed it converts like any other kind. A fake
        # download writes a real Parquet file (built with
        # pyarrow); the test is skipped where pyarrow is absent.
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow is not installed")
        parquet_path = Path(self.tmp.name) / "fixture.parquet"
        table = pa.Table.from_pylist([
            {"prompt": "a", "completion": "b"},
            {"prompt": "c", "completion": "d"},
        ])
        pq.write_table(table, parquet_path)
        blob = parquet_path.read_bytes()

        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            if str(dest).endswith(".parquet"):
                Path(dest).write_bytes(blob)
                return len(blob)
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
        self.assertEqual(report["rows"], 4)
        self.assertEqual(len(report["outputs"]), 2)

    def test_parquet_without_the_reader_is_refused_plainly(self):
        # T62: without the reader installed, a Parquet-only
        # dataset is refused with the missing-reader message,
        # before anything is downloaded.
        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "needs-reader",
                              "files": ["data/train.parquet"],
                              "total_size": 100,
                              "detail": "has Parquet files, but the "
                                        "Parquet reader is not "
                                        "installed, so Scout cannot "
                                        "convert them yet"}), \
                unittest.mock.patch.object(
                    apis, "download_file") as fake_download:
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertIn("Parquet reader", report["message"])
        fake_download.assert_not_called()

    def test_no_convertible_files_reports_plainly(self):
        # An Arrow file is genuinely not convertible (T62 made
        # Parquet convertible, so it no longer serves here).
        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "none",
                              "files": ["data/train.arrow"],
                              "total_size": 100, "detail": ""}):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "found")
        self.assertIn("No convertible files", report["message"])

    def test_image_only_dataset_names_what_it_holds(self):
        # v0.1.4 T30d: the message names the image files instead
        # of listing the formats the dataset does not hold.
        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "none",
                              "files": ["a.png", "b.jpg"],
                              "total_size": 100, "detail": ""}):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertIn("The dataset holds only .jpg and .png files.",
                      report["message"])

    def test_over_cap_file_is_refused_before_downloading(self):
        # v0.1.4 T25: the declared size is checked first, so a
        # 300 MB file is refused without downloading any of it.
        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["data/train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                unittest.mock.patch.object(
                    apis, "content_length",
                    return_value=download.MAX_DATA_FILE_BYTES + 1), \
                unittest.mock.patch.object(
                    apis, "download_file") as fake_download:
            report = download.download_and_convert(_hf_item(), self.out)
        fake_download.assert_not_called()
        self.assertFalse(report["ok"])
        self.assertIn("over the 200 MB file limit", report["message"])

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
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
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
        # The archive pre-check HEADs the archive URL; tests answer
        # "unknown size" so no live request escapes.
        self._cl = unittest.mock.patch.object(
            apis, "content_length", return_value=None)
        self._cl.start()

    def tearDown(self):
        self._cl.stop()
        self.tmp.cleanup()

    def _fake_archive_download(self, files):
        """A download_file stand-in that writes a zip of files."""
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
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
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
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

    def test_extraction_byte_cap_refuses_plainly(self):
        # v0.1.3 T29: an archive that would expand past the cap is
        # refused, in plain words naming the cap.
        with unittest.mock.patch.object(
                apis, "sniff_github",
                return_value={"status": "convert",
                              "files": ["data.json"],
                              "total_size": None, "detail": "",
                              "branch": "main"}), \
                unittest.mock.patch.object(
                    apis, "download_file",
                    side_effect=self._fake_archive_download(
                        {"tiny-repo-main/data.json": INSTRUCTION_JSON})), \
                unittest.mock.patch.object(
                    download, "MAX_EXTRACTED_BYTES", 10):
            report = download.download_and_convert(_gh_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertIn("expand to over", report["message"])

    def test_extraction_member_cap_refuses_plainly(self):
        # v0.1.3 T29: an archive with too many members is refused.
        with unittest.mock.patch.object(
                apis, "sniff_github",
                return_value={"status": "convert",
                              "files": ["a.json", "b.json"],
                              "total_size": None, "detail": "",
                              "branch": "main"}), \
                unittest.mock.patch.object(
                    apis, "download_file",
                    side_effect=self._fake_archive_download(
                        {"tiny-repo-main/a.json": INSTRUCTION_JSON,
                         "tiny-repo-main/b.json": INSTRUCTION_JSON})), \
                unittest.mock.patch.object(
                    download, "MAX_EXTRACTED_MEMBERS", 1):
            report = download.download_and_convert(_gh_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertIn("too many files", report["message"])

    def test_sibling_prefix_member_is_not_extracted(self):
        # v0.1.3 T38: a member resolving to a sibling folder whose
        # name starts with the extract folder's name ("repo-evil")
        # is outside the extract folder, even though a naive
        # startswith check would pass it.
        extracted = []
        real_extract = zipfile.ZipFile.extract

        def recording_extract(self, member, path=None, pwd=None):
            extracted.append(member.filename)
            return real_extract(self, member, path, pwd)

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
                         "../repo-evil/evil.jsonl": JSONL})), \
                unittest.mock.patch.object(
                    zipfile.ZipFile, "extract", recording_extract):
            report = download.download_and_convert(_gh_item(), self.out)
        self.assertTrue(report["ok"])
        self.assertIn("tiny-repo-main/data.json", extracted)
        self.assertNotIn("../repo-evil/evil.jsonl", extracted)


class DirectLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_direct_csv_link_converts(self):
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            Path(dest).write_text(CSV, encoding="utf-8")
            return len(CSV)

        item = {"title": "Direct CSV", "source": "Found by hand",
                "url": "https://example.com/data.csv"}
        with unittest.mock.patch.object(
                apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(item, self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], 2)

    def test_success_message_is_one_line_with_one_number(self):
        # v0.1.4 T50: "Converted 2 rows into 1 file." — the count
        # once, no full path, no "(s)".
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            Path(dest).write_text(JSONL, encoding="utf-8")
            return len(JSONL)

        item = {"title": "Direct JSONL", "source": "Found by hand",
                "url": "https://example.com/data.jsonl"}
        with unittest.mock.patch.object(
                apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(item, self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["message"],
                         "Converted 2 rows into 1 file.")
        self.assertNotIn(str(self.out), report["message"])
        self.assertNotIn("(s)", report["message"])

    def test_clean_convert_adds_no_note_but_skips_are_named(self):
        # v0.1.4 T50: a clean file adds no per-file note (the
        # summary carries the count), but skipped rows stay named.
        clean = Path(self.tmp.name) / "clean.jsonl"
        clean.write_text(JSONL, encoding="utf-8")
        out = Path(self.tmp.name) / "clean-out.jsonl"
        ok, note, rows = download._convert_file(clean, out)
        self.assertTrue(ok)
        self.assertEqual(note, "")
        self.assertEqual(rows, 2)
        mixed = Path(self.tmp.name) / "mixed.jsonl"
        mixed.write_text(
            JSONL + '{"text": "no prompt or completion here"}\n',
            encoding="utf-8")
        out2 = Path(self.tmp.name) / "mixed-out.jsonl"
        ok, note, rows = download._convert_file(mixed, out2)
        self.assertTrue(ok)
        self.assertEqual(rows, 2)
        self.assertIn("1 row skipped", note)
        self.assertNotIn("2 rows", note)

    def test_direct_parquet_link_converts(self):
        # T62: a direct link to a Parquet file converts when the
        # reader is installed.
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow is not installed")
        parquet_path = Path(self.tmp.name) / "fixture.parquet"
        table = pa.Table.from_pylist([
            {"prompt": "a", "completion": "b"},
            {"prompt": "c", "completion": "d"},
        ])
        pq.write_table(table, parquet_path)
        blob = parquet_path.read_bytes()

        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            Path(dest).write_bytes(blob)
            return len(blob)

        item = {"title": "Direct Parquet", "source": "Found by hand",
                "url": "https://example.com/data.parquet"}
        with unittest.mock.patch.object(
                apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(item, self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], 2)

    def test_direct_parquet_link_without_reader_refuses_plainly(self):
        # T62: without the reader, the refusal comes before any
        # download, and names the missing reader.
        item = {"title": "Direct Parquet", "source": "Found by hand",
                "url": "https://example.com/data.parquet"}
        with unittest.mock.patch(
                "scout.parquet.reader_available",
                return_value=False), \
                unittest.mock.patch.object(
                    apis, "download_file") as fake_download:
            report = download.download_and_convert(item, self.out)
        self.assertFalse(report["ok"])
        self.assertIn("Parquet reader", report["message"])
        fake_download.assert_not_called()

    def test_direct_over_cap_file_is_refused_before_downloading(self):
        # v0.1.4 T25: the declared size is checked first, so a
        # 300 MB direct link is refused without downloading
        # any of it.
        item = {"title": "Direct CSV", "source": "Found by hand",
                "url": "https://example.com/data.csv"}
        with unittest.mock.patch.object(
                apis, "content_length",
                return_value=download.MAX_DATA_FILE_BYTES + 1), \
                unittest.mock.patch.object(
                    apis, "download_file") as fake_download:
            report = download.download_and_convert(item, self.out)
        fake_download.assert_not_called()
        self.assertFalse(report["ok"])
        self.assertIn("bigger than 200.0 MB", report["message"])


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


class ExperienceTests(unittest.TestCase):
    """v0.1.3 T26/T27/T28/T34/T43: honest compressed-file messages,
    resume state kept, mid-file stop, stale-output cleanup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _hf_download(self, files, sniff_status="ready"):
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            Path(dest).write_text(JSONL, encoding="utf-8")
            return len(JSONL)

        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": sniff_status, "files": files,
                              "total_size": 100, "detail": ""}), \
                unittest.mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            return download.download_and_convert(_hf_item(), self.out)

    def test_gzipped_jsonl_only_names_the_file(self):
        report = self._hf_download(["train.jsonl.gz"],
                                   sniff_status="unsupported")
        self.assertFalse(report["ok"])
        self.assertIn("No convertible files found", report["message"])
        self.assertIn("gzipped .jsonl (train.jsonl.gz)",
                      report["message"])

    def test_direct_gzipped_link_names_the_file(self):
        item = {"title": "Direct GZ", "source": "Found by hand",
                "url": "https://example.com/train.jsonl.gz"}
        report = download.download_and_convert(item, self.out)
        self.assertFalse(report["ok"])
        self.assertIn("That link points to a gzipped .jsonl "
                      "(train.jsonl.gz)", report["message"])

    def test_stale_outputs_are_removed_on_rerun(self):
        report = self._hf_download(["data/a.jsonl"])
        self.assertTrue(report["ok"])
        folder = self.out / download.dataset_folder_name(_hf_item())
        self.assertTrue((folder / "data-a.jsonl").exists())
        report = self._hf_download(["data/b.jsonl"])
        self.assertTrue(report["ok"])
        self.assertTrue((folder / "data-b.jsonl").exists())
        self.assertFalse((folder / "data-a.jsonl").exists())

    def test_all_conversions_failing_keeps_state_found(self):
        # v0.1.3 T43: the fetched files are deleted after the run,
        # so "downloaded" would be a lie.
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
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
        self.assertEqual(report["state"], "found")

    def test_source_ids_are_cleaned(self):
        item = {"title": "x",
                "url": "https://huggingface.co/datasets/acme/tiny?tab=x"}
        self.assertEqual(download._hf_dataset_id(item), "acme/tiny")
        item = {"title": "x",
                "url": "https://github.com/acme/tiny.git"}
        self.assertEqual(download._github_repo(item), "acme/tiny")

    def test_download_stopped_reports_plainly(self):
        # v0.1.3 T28: a stop mid-download ends the run with a plain
        # message, not an error.
        def fake_download(url, dest, progress=None, chunk=262144,
                          limit=None, stop=None):
            raise apis.DownloadStopped("stopped")

        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "ready",
                              "files": ["data/train.jsonl"],
                              "total_size": 100, "detail": ""}), \
                unittest.mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
            report = download.download_and_convert(_hf_item(), self.out)
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "found")
        self.assertEqual(report["message"], "Stopped part way through.")

    def test_cleanup_keeps_part_files(self):
        work = Path(self.tmp.name) / "work"
        (work / "sub").mkdir(parents=True)
        (work / "sub" / "a.jsonl.part").write_text("half")
        (work / "sub" / "b.jsonl").write_text("done")
        (work / "repo.zip").write_text("zip")
        download._cleanup_work_dir(work)
        self.assertTrue((work / "sub" / "a.jsonl.part").exists())
        self.assertFalse((work / "sub" / "b.jsonl").exists())
        self.assertFalse((work / "repo.zip").exists())
        self.assertTrue(work.is_dir())

    def test_cleanup_removes_work_dir_without_parts(self):
        work = Path(self.tmp.name) / "work"
        (work / "sub").mkdir(parents=True)
        (work / "sub" / "b.jsonl").write_text("done")
        download._cleanup_work_dir(work)
        self.assertFalse(work.exists())

    def test_unknown_with_files_still_downloads(self):
        # v0.1.3 T26: a failed peek is "unknown", but the file list
        # is known, so the download is still tried.
        with unittest.mock.patch.object(
                apis, "sniff_huggingface",
                return_value={"status": "unknown",
                              "files": ["data/train.jsonl"],
                              "total_size": None,
                              "detail": "has .jsonl (data/train.jsonl), "
                                        "shape not checked: could not "
                                        "read the file"}):
            def fake_download(url, dest, progress=None, chunk=262144,
                              limit=None, stop=None):
                Path(dest).write_text(JSONL, encoding="utf-8")
                return len(JSONL)

            with unittest.mock.patch.object(
                    apis, "download_file", side_effect=fake_download):
                report = download.download_and_convert(
                    _hf_item(), self.out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], 2)


if __name__ == "__main__":
    unittest.main()
