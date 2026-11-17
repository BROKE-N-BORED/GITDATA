"""Tests for the bulk download queue (T66).

Storage only: the queue file, its states, and the resume-across-
sessions behaviour. The worker that runs the queue is covered by
the GUI tests; downloads themselves are covered by test_download.
"""

import json
import os
import tempfile
import unittest

from scout import core, queue


def _item(url, title="Dataset"):
    return {"title": title, "url": url, "source": "Hugging Face",
            "stars": 0, "updated": "", "license": ""}


class QueueStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old = os.environ.get("SCOUT_HOME")
        os.environ["SCOUT_HOME"] = self.tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SCOUT_HOME", None)
        else:
            os.environ["SCOUT_HOME"] = self._old

    def test_empty_queue_loads_empty(self):
        entries, interrupted = queue.load_queue()
        self.assertEqual(entries, [])
        self.assertEqual(interrupted, 0)

    def test_save_and_load_round_trip(self):
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        entries, _added = queue.enqueue(entries, _item("https://example.com/b"))
        queue.save_queue(entries)
        loaded, interrupted = queue.load_queue()
        self.assertEqual(interrupted, 0)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["state"], queue.QUEUED)
        self.assertEqual(loaded[1]["item"]["url"], "https://example.com/b")

    def test_enqueue_dedups_a_waiting_item(self):
        entries, added = queue.enqueue([], _item("https://example.com/a"))
        self.assertTrue(added)
        entries, added = queue.enqueue(entries, _item("https://example.com/a"))
        self.assertFalse(added)
        self.assertEqual(len(entries), 1)

    def test_enqueue_requeues_a_done_item(self):
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        entries[0]["state"] = queue.DONE
        entries[0]["rows"] = 10
        entries, added = queue.enqueue(entries, _item("https://example.com/a"))
        self.assertTrue(added)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["state"], queue.QUEUED)
        self.assertEqual(entries[0]["rows"], 0)

    def test_interrupted_item_comes_back_as_queued(self):
        # Resume across sessions: an item left "running" when the
        # app closed runs again instead of staying stuck.
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        entries[0]["state"] = queue.RUNNING
        queue.save_queue(entries)
        loaded, interrupted = queue.load_queue()
        self.assertEqual(interrupted, 1)
        self.assertEqual(loaded[0]["state"], queue.QUEUED)
        self.assertIn("queued again", loaded[0]["error"])
        # The reset is persisted: a second load reports nothing.
        _loaded, interrupted = queue.load_queue()
        self.assertEqual(interrupted, 0)

    def test_done_items_survive_a_reload(self):
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        entries, _added = queue.enqueue(entries, _item("https://example.com/b"))
        entries[0]["state"] = queue.DONE
        entries[0]["rows"] = 42
        entries[0]["outputs"] = ["/tmp/out/train.jsonl"]
        queue.save_queue(entries)
        loaded, _interrupted = queue.load_queue()
        self.assertEqual(loaded[0]["state"], queue.DONE)
        self.assertEqual(loaded[0]["rows"], 42)
        self.assertEqual(loaded[1]["state"], queue.QUEUED)

    def test_remove_and_clear_finished(self):
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        entries, _added = queue.enqueue(entries, _item("https://example.com/b"))
        entries, _added = queue.enqueue(entries, _item("https://example.com/c"))
        entries[0]["state"] = queue.DONE
        entries[1]["state"] = queue.FAILED
        kept = queue.clear_finished(entries)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["item"]["url"], "https://example.com/c")
        kept = queue.remove_entry(kept, kept[0]["id"])
        self.assertEqual(kept, [])

    def test_next_queued_and_counts(self):
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        entries, _added = queue.enqueue(entries, _item("https://example.com/b"))
        entries[0]["state"] = queue.DONE
        self.assertEqual(queue.next_queued(entries)["item"]["url"],
                         "https://example.com/b")
        total = queue.counts(entries)
        self.assertEqual(total, {"queued": 1, "running": 0, "done": 1,
                                 "failed": 0, "total": 2})

    def test_corrupt_file_loads_empty(self):
        queue.queue_path().write_text("{not json", encoding="utf-8")
        entries, interrupted = queue.load_queue()
        self.assertEqual(entries, [])
        self.assertEqual(interrupted, 0)

    def test_items_are_normalized_on_load(self):
        queue.queue_path().write_text(json.dumps({"entries": [
            {"id": "x", "item": {"url": "https://example.com/a"},
             "state": "bogus", "outputs": "not-a-list",
             "rows": "seven", "error": "", "added": ""},
        ]}), encoding="utf-8")
        entries, _interrupted = queue.load_queue()
        self.assertEqual(entries[0]["state"], queue.QUEUED)
        self.assertEqual(entries[0]["outputs"], [])
        self.assertEqual(entries[0]["rows"], 0)
        self.assertEqual(entries[0]["item"]["title"],
                         "https://example.com/a")

    def test_retry_failed_requeues_failed_entries(self):
        # v0.1.4 T45: failed entries go back to waiting, with the
        # old error, outputs, and row count cleared.
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        entries, _added = queue.enqueue(entries, _item("https://example.com/b"))
        entries, _added = queue.enqueue(entries, _item("https://example.com/c"))
        entries[0]["state"] = queue.FAILED
        entries[0]["error"] = "Download failed."
        entries[0]["rows"] = 5
        entries[0]["outputs"] = ["stale.jsonl"]
        entries[1]["state"] = queue.DONE
        count = queue.retry_failed(entries)
        self.assertEqual(count, 1)
        self.assertEqual(entries[0]["state"], queue.QUEUED)
        self.assertEqual(entries[0]["error"], "")
        self.assertEqual(entries[0]["rows"], 0)
        self.assertEqual(entries[0]["outputs"], [])
        self.assertEqual(entries[1]["state"], queue.DONE)
        self.assertEqual(entries[2]["state"], queue.QUEUED)

    def test_retry_failed_only_retries_the_picked_ids(self):
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        entries, _added = queue.enqueue(entries, _item("https://example.com/b"))
        entries[0]["state"] = queue.FAILED
        entries[1]["state"] = queue.FAILED
        count = queue.retry_failed(entries, [entries[1]["id"]])
        self.assertEqual(count, 1)
        self.assertEqual(entries[0]["state"], queue.FAILED)
        self.assertEqual(entries[1]["state"], queue.QUEUED)

    def test_retry_failed_with_nothing_failed_retries_nothing(self):
        entries, _added = queue.enqueue([], _item("https://example.com/a"))
        self.assertEqual(queue.retry_failed(entries), 0)


if __name__ == "__main__":
    unittest.main()
