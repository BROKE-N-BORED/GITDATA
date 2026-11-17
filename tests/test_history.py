"""Download history (T61): record, load, cap, clear."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scout import core, history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SCOUT_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("SCOUT_HOME", None)

    def test_empty_history_loads_empty(self):
        self.assertEqual(history.load_history(), [])

    def test_record_then_load_round_trip(self):
        entry = history.record_conversion(
            "Math Set", "https://example.com/math", "Hugging Face",
            120, ["/home/user/.dataset-scout/stella-ready/math-abc/"
                  "train.jsonl"])
        self.assertEqual(entry["title"], "Math Set")
        self.assertEqual(entry["rows"], 120)
        self.assertEqual(entry["files"], ["train.jsonl"])
        self.assertTrue(entry["when"])
        self.assertEqual(
            entry["folder"],
            "/home/user/.dataset-scout/stella-ready/math-abc")
        loaded = history.load_history()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["title"], "Math Set")
        self.assertEqual(loaded[0]["url"], "https://example.com/math")

    def test_newest_first(self):
        history.record_conversion("First", "", "", 1, ["a.jsonl"])
        history.record_conversion("Second", "", "", 2, ["b.jsonl"])
        titles = [entry["title"] for entry in history.load_history()]
        self.assertEqual(titles, ["Second", "First"])

    def test_history_is_capped(self):
        for number in range(history.MAX_ENTRIES + 5):
            history.record_conversion(f"Set {number}", "", "", 1,
                                      ["a.jsonl"])
        self.assertEqual(len(history.load_history()), history.MAX_ENTRIES)
        self.assertEqual(history.load_history()[0]["title"],
                         f"Set {history.MAX_ENTRIES + 4}")

    def test_clear_history(self):
        history.record_conversion("One", "", "", 1, ["a.jsonl"])
        history.clear_history()
        self.assertEqual(history.load_history(), [])

    def test_corrupt_file_loads_empty(self):
        history.history_path().write_text("not json", encoding="utf-8")
        self.assertEqual(history.load_history(), [])

    def test_bad_rows_are_cleaned_not_crashed(self):
        history.history_path().write_text(json.dumps(
            {"entries": [{"title": "Bad rows", "rows": "many",
                          "files": "not-a-list"},
                         "not-a-dict"]}), encoding="utf-8")
        loaded = history.load_history()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["rows"], 0)
        self.assertEqual(loaded[0]["files"], [])


if __name__ == "__main__":
    unittest.main()
