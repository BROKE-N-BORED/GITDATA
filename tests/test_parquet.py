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


if __name__ == "__main__":
    unittest.main()
