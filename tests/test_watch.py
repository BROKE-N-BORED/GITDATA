"""Tests for dataset watch (T63).

No live network: the search step is mocked at the apis boundary.
Watches live in SCOUT_HOME, pointed at a temp dir here.
"""

import os
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

from scout import apis, watch

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


def _result(url, title="Dataset"):
    return {"title": title, "url": url, "source": "Hugging Face",
            "stars": 0, "updated": "", "license": ""}


class WatchStoreTests(unittest.TestCase):
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

    def test_no_watches_by_default(self):
        # Off by default: a fresh home watches nothing.
        self.assertEqual(watch.load_watches(), [])

    def test_save_and_load_round_trip(self):
        made = watch.new_watch("math word problems", "reasoning")
        watch.save_watches([made])
        loaded = watch.load_watches()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["query"], "math word problems")
        self.assertEqual(loaded[0]["category"], "reasoning")
        self.assertTrue(loaded[0]["enabled"])

    def test_add_watch_dedups_the_same_search(self):
        watches, added = watch.add_watch(
            [], watch.new_watch("math", "reasoning"))
        self.assertTrue(added)
        watches, added = watch.add_watch(
            watches, watch.new_watch("math", "reasoning"))
        self.assertFalse(added)
        self.assertEqual(len(watches), 1)
        watches, added = watch.add_watch(
            watches, watch.new_watch("math", "training"))
        self.assertTrue(added)
        self.assertEqual(len(watches), 2)

    def test_remove_watch(self):
        first = watch.new_watch("one")
        second = watch.new_watch("two")
        kept = watch.remove_watch([first, second], first["id"])
        self.assertEqual([entry["id"] for entry in kept], [second["id"]])

    def test_corrupt_file_loads_as_no_watches(self):
        watch.watches_path().write_text("{not json", encoding="utf-8")
        self.assertEqual(watch.load_watches(), [])


class WatchDueTests(unittest.TestCase):
    def test_never_run_is_due(self):
        self.assertTrue(watch.watch_due(watch.new_watch("math"), now=NOW))

    def test_recent_run_is_not_due(self):
        made = watch.new_watch("math")
        made["last_run"] = (NOW - timedelta(hours=1)).isoformat()
        self.assertFalse(watch.watch_due(made, now=NOW))

    def test_old_run_is_due(self):
        made = watch.new_watch("math")
        made["last_run"] = (NOW - timedelta(hours=25)).isoformat()
        self.assertTrue(watch.watch_due(made, now=NOW))

    def test_disabled_watch_is_never_due(self):
        made = watch.new_watch("math")
        made["enabled"] = False
        self.assertFalse(watch.watch_due(made, now=NOW))


class WatchRunTests(unittest.TestCase):
    def test_first_run_sets_baseline_and_reports_nothing_new(self):
        made = watch.new_watch("math")
        found = watch.apply_watch_results(
            made, [_result("https://example.com/a"),
                   _result("https://example.com/b")], now=NOW)
        self.assertEqual(found, [])
        self.assertEqual(made["new"], [])
        self.assertEqual(len(made["last_seen"]), 2)
        self.assertTrue(made["last_run"])

    def test_second_run_flags_only_new_links(self):
        made = watch.new_watch("math")
        watch.apply_watch_results(
            made, [_result("https://example.com/a")], now=NOW)
        found = watch.apply_watch_results(
            made, [_result("https://example.com/a"),
                   _result("https://example.com/b")],
            now=NOW + timedelta(hours=25))
        self.assertEqual([item["url"] for item in found],
                         ["https://example.com/b"])
        self.assertEqual([item["url"] for item in made["new"]],
                         ["https://example.com/b"])

    def test_trailing_slash_counts_as_the_same_link(self):
        # The watch compares links with core.normalize_url, so a
        # link that only differs by a trailing slash is not new.
        # (http-vs-https dedup arrives with T24's normaliser fix;
        # the watch inherits it automatically through normalize_url.)
        made = watch.new_watch("math")
        watch.apply_watch_results(
            made, [_result("https://example.com/a")], now=NOW)
        found = watch.apply_watch_results(
            made, [_result("https://example.com/a/")],
            now=NOW + timedelta(hours=25))
        self.assertEqual(found, [])

    def test_run_search_names_failed_sources(self):
        with unittest.mock.patch.object(
                apis, "search_github",
                side_effect=apis.SearchError("GitHub is down")), \
                unittest.mock.patch.object(
                    apis, "search_huggingface",
                    return_value=[_result("https://example.com/a")]):
            results, failures = watch.run_watch_search("math", "all")
        self.assertEqual(len(results), 1)
        self.assertEqual(failures, [("GitHub", "GitHub is down")])


class WatchNewFlagsTests(unittest.TestCase):
    """v0.1.4 T42: "new" flags survive runs until the user sees them."""

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

    def test_quiet_run_keeps_earlier_new_items(self):
        made = watch.new_watch("math")
        watch.apply_watch_results(
            made, [_result("https://example.com/a")], now=NOW)
        watch.apply_watch_results(
            made, [_result("https://example.com/a"),
                   _result("https://example.com/b")],
            now=NOW + timedelta(hours=25))
        self.assertEqual([item["url"] for item in made["new"]],
                         ["https://example.com/b"])
        # A later run finds nothing new: the flag must survive.
        found = watch.apply_watch_results(
            made, [_result("https://example.com/a"),
                   _result("https://example.com/b")],
            now=NOW + timedelta(hours=50))
        self.assertEqual(found, [])
        self.assertEqual([item["url"] for item in made["new"]],
                         ["https://example.com/b"])
        self.assertFalse(made["new_seen"])

    def test_fresh_items_join_unseen_ones_without_duplicates(self):
        made = watch.new_watch("math")
        watch.apply_watch_results(
            made, [_result("https://example.com/a")], now=NOW)
        watch.apply_watch_results(
            made, [_result("https://example.com/a"),
                   _result("https://example.com/b")],
            now=NOW + timedelta(hours=25))
        found = watch.apply_watch_results(
            made, [_result("https://example.com/a"),
                   _result("https://example.com/b"),
                   _result("https://example.com/c")],
            now=NOW + timedelta(hours=50))
        self.assertEqual([item["url"] for item in found],
                         ["https://example.com/c"])
        self.assertEqual([item["url"] for item in made["new"]],
                         ["https://example.com/b",
                          "https://example.com/c"])

    def test_new_seen_flag_round_trips_through_disk(self):
        made = watch.new_watch("math")
        made["new"] = [_result("https://example.com/b")]
        made["new_seen"] = False
        watch.save_watches([made])
        loaded = watch.load_watches()
        self.assertEqual(len(loaded), 1)
        self.assertFalse(loaded[0]["new_seen"])
        self.assertEqual(len(loaded[0]["new"]), 1)


if __name__ == "__main__":
    unittest.main()
