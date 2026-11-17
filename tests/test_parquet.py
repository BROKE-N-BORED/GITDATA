"""Tests for the Parquet reader (T62) and Parquet sniffing.

Parquet tests need pyarrow; they skip where it is not installed.
The missing-reader path is tested by patching the lazy import,
so it runs everywhere.
"""

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from scout import apis, convert, parquet

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    HAVE_PYARROW = True
except ImportError:
    HAVE_PYARROW = False


def _write_parquet(path: Path, rows: list[dict]) -> None:
    # from_pylist drops a column that only appears in later rows
    # with a nested type (the messages column vanished from a
    # mixed file), so build the schema from every key up front.
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    fields = []
    for key in keys:
        sample = next((row[key] for row in rows
                       if row.get(key) is not None), None)
        if isinstance(sample, list):
            fields.append((key, pa.list_(pa.struct([
                ("role", pa.string()), ("content", pa.string())]))))
        else:
            fields.append((key, pa.string()))
    schema = pa.schema(fields)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


@unittest.skipUnless(HAVE_PYARROW, "pyarrow is not installed")
class ParquetConvertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, rows, name="data.parquet"):
        path = self.dir / name
        _write_parquet(path, rows)
        return path

    def test_prompt_rows_convert(self):
        path = self._file([
            {"prompt": "a", "completion": "b"},
            {"prompt": "c", "completion": "d"},
        ])
        rows, report = parquet.convert_parquet_file(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["prompt"], "a")
        self.assertEqual(report["seen"], 2)
        self.assertEqual(report["kept"], 2)
        self.assertEqual(report["format"], "prompt")
        ok, _problem = convert.validate_rows(rows)
        self.assertTrue(ok)

    def test_messages_rows_convert(self):
        convo = [{"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "hello"}]
        path = self._file([{"messages": convo}, {"messages": convo}])
        rows, report = parquet.convert_parquet_file(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(report["format"], "messages")

    def test_text_only_rows_are_skipped_never_faked(self):
        path = self._file([
            {"prompt": "a", "completion": "b"},
            {"prompt": "c", "completion": "d"},
            {"text": "just some words"},
        ])
        rows, report = parquet.convert_parquet_file(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(report["seen"], 3)
        self.assertIn("text only, no prompt to pair it with",
                      report["skipped"])

    def test_second_format_is_skipped(self):
        convo = [{"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "hello"}]
        path = self._file([
            {"prompt": "a", "completion": "b"},
            {"messages": convo},
            {"prompt": "c", "completion": "d"},
        ])
        rows, report = parquet.convert_parquet_file(path)
        self.assertEqual(len(rows), 2)
        self.assertIn("different shape from the rest of the file",
                      report["skipped"])

    def test_one_row_is_not_enough(self):
        path = self._file([{"prompt": "a", "completion": "b"}])
        rows, _report = parquet.convert_parquet_file(path)
        ok, problem = convert.validate_rows(rows)
        self.assertFalse(ok)
        self.assertIn("2", problem)

    def test_instruction_rows_map(self):
        path = self._file([
            {"instruction": "Add", "output": "2"},
            {"instruction": "Add", "output": "4"},
        ])
        rows, _report = parquet.convert_parquet_file(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["prompt"], "Add")

    def test_corrupt_file_raises_plain_error(self):
        path = self.dir / "bad.parquet"
        path.write_bytes(b"this is not a parquet file")
        with self.assertRaises(parquet.ParquetError):
            parquet.convert_parquet_file(path)

    def test_reader_is_available(self):
        self.assertTrue(parquet.reader_available())


class MissingReaderTests(unittest.TestCase):
    def test_reader_available_is_false_without_pyarrow(self):
        with unittest.mock.patch.object(
                parquet, "_load_pyarrow",
                side_effect=ImportError("no pyarrow")):
            self.assertFalse(parquet.reader_available())

    def test_convert_without_reader_raises_plain_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.parquet"
            path.write_bytes(b"PAR1fake")
            with unittest.mock.patch.object(
                    parquet, "_load_pyarrow",
                    side_effect=ImportError("no pyarrow")):
                with self.assertRaises(parquet.ParquetError) as caught:
                    parquet.convert_parquet_file(path)
        self.assertIn("Parquet reader", str(caught.exception))
        self.assertIn("not installed", str(caught.exception))


class ParquetSniffTests(unittest.TestCase):
    def test_parquet_only_converts_with_reader(self):
        with unittest.mock.patch.object(
                parquet, "reader_available", return_value=True):
            result = apis._sniff_files(
                ["data/train.parquet"], 100,
                lambda name: ("unknown", "no jsonl"))
        self.assertEqual(result["status"], "convert")
        self.assertIn("parquet", result["detail"])

    def test_parquet_only_needs_reader_without_it(self):
        with unittest.mock.patch.object(
                parquet, "reader_available", return_value=False):
            result = apis._sniff_files(
                ["data/train.parquet"], 100,
                lambda name: ("unknown", "no jsonl"))
        self.assertEqual(result["status"], "needs-reader")
        self.assertIn("not installed", result["detail"])

    def test_mixed_parquet_and_csv_still_converts(self):
        # Only a Parquet-only listing gets the reader treatment;
        # a dataset with a convertible text file converts either way.
        with unittest.mock.patch.object(
                parquet, "reader_available", return_value=False):
            result = apis._sniff_files(
                ["data/train.parquet", "data/train.csv"], 100,
                lambda name: ("unknown", "no jsonl"))
        self.assertEqual(result["status"], "convert")


class _RangeResponse:
    """A fake HTTP response serving byte ranges of one file."""

    def __init__(self, body, status=200, headers=None):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        return self._body if size is None or size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _range_server(data, advertise_size=True):
    """A fake urlopen that serves HEAD and Range GETs of data."""
    fetched = []

    def fake_urlopen(request, timeout=None):
        method = request.get_method()
        if method == "HEAD":
            headers = {}
            if advertise_size:
                headers["Content-Length"] = str(len(data))
            return _RangeResponse(b"", 200, headers)
        range_header = request.get_header("Range")
        if range_header and range_header.startswith("bytes="):
            start, _, end = range_header[6:].partition("-")
            start = int(start or 0)
            end = int(end) if end else len(data) - 1
            body = data[start:end + 1]
            fetched.append((start, end))
            return _RangeResponse(
                body, 206,
                {"Content-Length": str(len(body)),
                 "Content-Range":
                     f"bytes {start}-{end}/{len(data)}"})
        fetched.append((0, len(data) - 1))
        return _RangeResponse(data, 200,
                              {"Content-Length": str(len(data))})

    fake_urlopen.fetched = fetched
    return fake_urlopen


@unittest.skipUnless(HAVE_PYARROW, "pyarrow is not installed")
class RemoteColumnsTests(unittest.TestCase):
    # v0.1.4 T10: the Parquet peek reads only the footer over
    # HTTP Range requests — never the rows.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _data(self, rows, name="data.parquet"):
        path = self.dir / name
        _write_parquet(path, rows)
        return path.read_bytes()

    def test_reads_column_names_without_the_rows(self):
        # The rows are long random text, so the data section is
        # far bigger than the footer: a footer-only peek fetches
        # a fraction of the file. (A tiny file fits in one
        # pyarrow read, so a small fixture cannot prove this.)
        import random
        big_text = "".join(
            random.Random(7).choices("abcdefghij0123456789",
                                     k=200000))
        data = self._data(
            [{"question": "2+2?", "ground_truth": "4",
              "text": big_text}])
        server = _range_server(data)
        with unittest.mock.patch("urllib.request.urlopen", server):
            columns = parquet.read_remote_columns(
                "https://example.com/data.parquet")
        self.assertEqual(columns, ["question", "ground_truth", "text"])
        fetched = sum(end - start + 1 for start, end in server.fetched)
        self.assertLess(fetched, len(data) // 2)

    def test_unknown_size_is_a_plain_error(self):
        data = self._data([{"prompt": "a", "completion": "b"}])
        server = _range_server(data, advertise_size=False)
        with unittest.mock.patch("urllib.request.urlopen", server):
            with self.assertRaises(parquet.ParquetError) as caught:
                parquet.read_remote_columns(
                    "https://example.com/data.parquet")
        self.assertIn("could not read", str(caught.exception).lower())

    def test_unreadable_footer_is_a_plain_error(self):
        server = _range_server(b"not a parquet file at all")
        with unittest.mock.patch("urllib.request.urlopen", server):
            with self.assertRaises(parquet.ParquetError):
                parquet.read_remote_columns(
                    "https://example.com/data.parquet")


if __name__ == "__main__":
    unittest.main()
