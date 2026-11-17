"""Core logic tests — no network, no Qt."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scout import core


class TempHomeMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SCOUT_HOME"] = self.tmp.name
        core._RING.clear()

    def tearDown(self):
        os.environ.pop("SCOUT_HOME", None)


class QueryTests(unittest.TestCase):
    def test_category_suffix_appended(self):
        self.assertEqual(
            core.build_query("math", "reasoning"),
            "math reasoning dataset",
        )

    def test_category_alone_when_box_empty(self):
        self.assertEqual(
            core.build_query("", "instruction-tuning"),
            "instruction tuning dataset",
        )
        self.assertEqual(
            core.build_query("  ", "post-training"),
            "post-training dataset",
        )

    def test_all_uses_text_as_is(self):
        self.assertEqual(core.build_query("math", "all"), "math")

    def test_empty_all_falls_back_to_dataset(self):
        self.assertEqual(core.build_query("", "all"), "dataset")

    def test_unknown_category_is_safe(self):
        self.assertEqual(core.build_query("math", "nope"), "math")


class ActiveFilterTests(unittest.TestCase):
    NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)

    def test_recent_is_active(self):
        self.assertTrue(core.is_active("2026-11-01T00:00:00Z", 12, self.NOW))

    def test_old_is_not_active(self):
        self.assertFalse(core.is_active("2024-01-01T00:00:00Z", 12, self.NOW))

    def test_boundary_exactly_at_cutoff_is_active(self):
        # 12 months = 360 days; the cutoff day itself counts as active.
        self.assertTrue(core.is_active("2025-11-18T12:00:00Z", 12, self.NOW))

    def test_one_day_before_cutoff_is_not_active(self):
        self.assertFalse(core.is_active("2025-11-17T11:59:59Z", 12, self.NOW))

    def test_any_age_always_active(self):
        self.assertTrue(core.is_active("2019-01-01T00:00:00Z", None, self.NOW))

    def test_missing_date_is_not_active_under_a_window(self):
        self.assertFalse(core.is_active("", 12, self.NOW))
        self.assertFalse(core.is_active(None, 12, self.NOW))

    def test_bad_date_is_not_active(self):
        self.assertFalse(core.is_active("not a date", 12, self.NOW))

    def test_format_date(self):
        self.assertEqual(core.format_date("2026-11-13T09:00:00Z"), "2026-11-13")
        self.assertEqual(core.format_date(""), "-")
        self.assertEqual(core.format_date(None), "-")


class FilterResultsTests(unittest.TestCase):
    NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)

    def _item(self, stars, updated):
        return {"title": "x", "url": "u", "stars": stars, "updated": updated}

    def test_min_stars_floor(self):
        items = [self._item(5, "2026-11-01T00:00:00Z"),
                 self._item(50, "2026-11-01T00:00:00Z")]
        kept = core.filter_results(items, False, None, 10, self.NOW)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["stars"], 50)

    def test_active_only_drops_stale(self):
        items = [self._item(50, "2020-01-01T00:00:00Z"),
                 self._item(50, "2026-11-01T00:00:00Z")]
        kept = core.filter_results(items, True, 12, 0, self.NOW)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["updated"], "2026-11-01T00:00:00Z")


class GithubParseTests(unittest.TestCase):
    def test_parse_full_item(self):
        payload = {
            "items": [{
                "full_name": "org/repo",
                "html_url": "https://github.com/org/repo",
                "description": "A reasoning dataset",
                "stargazers_count": 42,
                "pushed_at": "2026-11-01T00:00:00Z",
                "updated_at": "2026-11-02T00:00:00Z",
                "license": {"spdx_id": "MIT"},
                "topics": ["nlp", "dataset"],
            }]
        }
        (item,) = core.parse_github_results(payload, "reasoning")
        self.assertEqual(item["title"], "org/repo")
        self.assertEqual(item["source"], "GitHub")
        self.assertEqual(item["category"], "reasoning")
        self.assertEqual(item["stars"], 42)
        self.assertEqual(item["updated"], "2026-11-01T00:00:00Z")
        self.assertEqual(item["license"], "MIT")
        self.assertEqual(item["topics"], ["nlp", "dataset"])

    def test_parse_missing_fields_are_safe(self):
        (item,) = core.parse_github_results(
            {"items": [{"full_name": "a/b"}]}, "all")
        self.assertEqual(item["url"], "")
        self.assertEqual(item["stars"], 0)
        self.assertEqual(item["license"], "")
        self.assertEqual(item["topics"], [])

    def test_parse_empty_payload(self):
        self.assertEqual(core.parse_github_results({}, "all"), [])

    def test_rate_limit_message_names_reset_time(self):
        reset = datetime(2026, 11, 13, 15, 30, tzinfo=timezone.utc)
        err = core.RateLimitedError(reset)
        self.assertIn("GitHub search limit reached", str(err))
        self.assertIn("token", str(err))
        # Reset time is shown in local time.
        self.assertIn(reset.astimezone().strftime("%H:%M"), str(err))

    def test_rate_limit_without_reset_time(self):
        err = core.RateLimitedError(None)
        self.assertIn("GitHub search limit reached", str(err))

    def test_github_url_shape(self):
        url = core.github_search_url("reasoning dataset", 30)
        self.assertIn("api.github.com/search/repositories", url)
        self.assertIn("sort=stars", url)
        self.assertIn("per_page=30", url)
        self.assertIn("reasoning+dataset", url)


class HfParseTests(unittest.TestCase):
    def test_parse_full_item(self):
        payload = [{
            "id": "org/dataset",
            "likes": 7,
            "downloads": 1234,
            "lastModified": "2026-10-01T00:00:00.000Z",
            "tags": ["task_categories:text-generation"],
        }]
        (item,) = core.parse_hf_results(payload, "training")
        self.assertEqual(item["title"], "org/dataset")
        self.assertEqual(item["url"],
                         "https://huggingface.co/datasets/org/dataset")
        self.assertEqual(item["source"], "Hugging Face")
        self.assertEqual(item["stars"], 7)
        self.assertEqual(item["downloads"], 1234)
        self.assertEqual(item["category"], "training")

    def test_parse_missing_fields_are_safe(self):
        (item,) = core.parse_hf_results([{"id": "x/y"}], "all")
        self.assertEqual(item["stars"], 0)
        self.assertEqual(item["downloads"], 0)

    def test_hf_url_shape(self):
        url = core.hf_search_url("math word problems", 30)
        self.assertIn("huggingface.co/api/datasets", url)
        self.assertIn("search=math+word+problems", url)
        self.assertIn("limit=30", url)

    def test_license_tag_shows_display_case(self):
        # v0.1.3 T43: Hugging Face says "mit", GitHub says "MIT".
        # The column shows one case for both.
        payload = [{"id": "a/b", "tags": ["license:mit"]},
                   {"id": "c/d", "tags": ["license:apache-2.0"]},
                   {"id": "e/f", "tags": ["license:odc-by"]}]
        items = core.parse_hf_results(payload, "all")
        self.assertEqual(items[0]["license"], "MIT")
        self.assertEqual(items[1]["license"], "Apache-2.0")
        self.assertEqual(items[2]["license"], "odc-by")


class SourceForUrlTests(unittest.TestCase):
    # v0.1.4 T3: a hand-added link gets its source from its URL,
    # because the sniff and download dispatch on source.
    def test_huggingface_dataset_url(self):
        self.assertEqual(
            core.source_for_url(
                "https://huggingface.co/datasets/acme/tiny"),
            "Hugging Face")

    def test_github_url(self):
        self.assertEqual(
            core.source_for_url("https://github.com/acme/tiny"),
            "GitHub")

    def test_huggingface_non_dataset_url_is_hand_found(self):
        self.assertEqual(
            core.source_for_url("https://huggingface.co/acme/tiny"),
            "Found by hand")

    def test_direct_file_url_is_hand_found(self):
        self.assertEqual(
            core.source_for_url("https://example.com/train.jsonl"),
            "Found by hand")

    def test_blank_or_broken_url_is_hand_found(self):
        self.assertEqual(core.source_for_url(""), "Found by hand")
        self.assertEqual(core.source_for_url("not a link"),
                         "Found by hand")


class SourcesTests(TempHomeMixin, unittest.TestCase):
    def test_defaults_on_first_run(self):
        sites = core.load_sources()
        self.assertEqual(len(sites), 2)
        names = [s["name"] for s in sites]
        self.assertIn("Hugging Face Datasets", names)
        self.assertIn("Kaggle Datasets", names)

    def test_dead_papers_with_code_entry_is_dropped(self):
        # T31: Papers With Code is gone entirely (site + API both
        # redirect to Hugging Face), so a saved entry for it is a
        # dead button and is dropped on load.
        core.save_sources([
            {"name": "Hugging Face Datasets",
             "url": "https://huggingface.co/datasets?search={q}"},
            {"name": "Papers With Code",
             "url": "https://paperswithcode.com/search?q={q}"},
        ])
        names = [s["name"] for s in core.load_sources()]
        self.assertEqual(names, ["Hugging Face Datasets"])

    def test_add_remove_persist(self):
        sites = core.load_sources()
        sites.append({"name": "Example", "url": "https://example.com/?q={q}"})
        core.save_sources(sites)
        self.assertEqual(len(core.load_sources()), 3)
        kept = [s for s in core.load_sources() if s["name"] != "Example"]
        core.save_sources(kept)
        self.assertEqual(len(core.load_sources()), 2)

    def test_restore_defaults(self):
        core.save_sources([{"name": "Only", "url": "https://only.example/"}])
        restored = core.restore_default_sources()
        self.assertEqual(len(restored), 2)
        self.assertEqual(len(core.load_sources()), 2)

    def test_corrupt_file_falls_back_to_defaults(self):
        core.sources_path().parent.mkdir(parents=True, exist_ok=True)
        core.sources_path().write_text("not json", encoding="utf-8")
        self.assertEqual(len(core.load_sources()), 2)

    def test_build_site_url_fills_query(self):
        url = core.build_site_url(
            "https://example.com/search?q={q}", "math problems")
        self.assertEqual(url, "https://example.com/search?q=math+problems")

    def test_build_site_url_plain_url_unchanged(self):
        url = core.build_site_url("https://example.com/datasets", "math")
        self.assertEqual(url, "https://example.com/datasets")


class ListTests(TempHomeMixin, unittest.TestCase):
    def _item(self, url="https://example.com/a"):
        return {"title": "A", "url": url, "category": "reasoning",
                "source": "GitHub", "stars": 5, "updated": "",
                "license": "", "notes": ""}

    def test_add_and_persist(self):
        items, added = core.add_to_list([], self._item())
        self.assertTrue(added)
        core.save_list(items)
        loaded = core.load_list()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["title"], "A")

    def test_duplicate_url_not_added(self):
        items, _ = core.add_to_list([], self._item())
        items, added = core.add_to_list(items, self._item())
        self.assertFalse(added)
        self.assertEqual(len(items), 1)

    def test_remove_from_list(self):
        items, _ = core.add_to_list([], self._item())
        items, _ = core.add_to_list(
            items, self._item("https://example.com/b"))
        items = core.remove_from_list(items, "https://example.com/a")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/b")

    def test_normalize_fills_defaults(self):
        item = core.normalize_item({"url": "https://example.com/x"})
        self.assertEqual(item["title"], "https://example.com/x")
        self.assertEqual(item["category"], "all")
        self.assertEqual(item["stars"], 0)
        self.assertEqual(item["status"], "")


class ExportTests(TempHomeMixin, unittest.TestCase):
    def _items(self):
        return [
            {"title": "Repo A", "url": "https://github.com/a/b",
             "category": "reasoning", "source": "GitHub", "stars": 10,
             "updated": "2026-11-01T00:00:00Z", "license": "MIT",
             "notes": "good one"},
            {"title": "Data B", "url": "https://huggingface.co/datasets/x/y",
             "category": "training", "source": "Hugging Face", "stars": 3,
             "updated": "", "license": "", "notes": ""},
        ]

    def test_markdown_groups_by_category_and_links(self):
        text = core.export_markdown(self._items())
        self.assertIn("## Reasoning", text)
        self.assertIn("## Training datasets", text)
        self.assertIn("[Repo A](https://github.com/a/b)", text)
        self.assertIn("[Data B](https://huggingface.co/datasets/x/y)", text)
        self.assertIn("10 stars", text)
        self.assertIn("Note: good one", text)

    def test_markdown_keeps_unknown_categories(self):
        # Categories are data, not a hardcoded list: an item whose
        # category is not one of the five search presets still gets
        # its own section, never dropped.
        items = self._items() + [
            {"title": "Odd One", "url": "https://example.com/odd",
             "category": "my-own-thing", "source": "Found by hand",
             "stars": 0, "updated": "", "license": "", "notes": ""},
        ]
        text = core.export_markdown(items)
        self.assertIn("## My Own Thing", text)
        self.assertIn("[Odd One](https://example.com/odd)", text)

    def test_category_label(self):
        self.assertEqual(core.category_label("reasoning"), "Reasoning")
        self.assertEqual(core.category_label("my-own-thing"),
                         "My Own Thing")
        self.assertEqual(core.category_label(""), "All")

    def test_every_export_carries_the_stella_note(self):
        for text in (core.export_markdown(self._items()),
                     core.export_json(self._items()),
                     core.export_stella_manifest(self._items())):
            self.assertIn(".jsonl", text)
            self.assertIn("prompt", text)
            self.assertIn("messages", text)

    def test_json_round_trip(self):
        text = core.export_json(self._items())
        payload = json.loads(text)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["title"], "Repo A")

    def test_stella_manifest_shape_and_round_trip(self):
        text = core.export_stella_manifest(self._items(), name="My picks")
        payload = json.loads(text)
        self.assertEqual(payload["stella_dataset_sources_version"], 1)
        self.assertEqual(payload["name"], "My picks")
        self.assertIn("created", payload)
        self.assertIn("stella_format_note", payload)
        self.assertEqual(len(payload["items"]), 2)
        item = payload["items"][0]
        self.assertEqual(item["title"], "Repo A")
        self.assertEqual(item["url"], "https://github.com/a/b")
        self.assertEqual(item["category"], "reasoning")
        self.assertEqual(item["source"], "GitHub")
        self.assertEqual(item["stars"], 10)
        self.assertEqual(item["license"], "MIT")
        self.assertEqual(item["notes"], "good one")


class TokenScrubTests(TempHomeMixin, unittest.TestCase):
    TOKEN = "ghp_SECRET_TOKEN_12345"

    def test_scrub_replaces_token(self):
        self.assertEqual(
            core.scrub_token(f"Authorization: Bearer {self.TOKEN}",
                             token=self.TOKEN),
            "Authorization: Bearer ***",
        )

    def test_token_stored_only_in_config_file(self):
        core.set_github_token(self.TOKEN)
        self.assertEqual(core.get_github_token(), self.TOKEN)
        raw = core.config_path().read_text(encoding="utf-8")
        self.assertIn(self.TOKEN, raw)

    def test_log_scrubs_token(self):
        core.set_github_token(self.TOKEN)
        core.log(f"tried with token {self.TOKEN}")
        self.assertTrue(all(self.TOKEN not in line for line in core.ring_lines()))
        log_file = core._log_file()
        self.assertNotIn(self.TOKEN, log_file.read_text(encoding="utf-8"))

    def test_debug_report_scrubs_token(self):
        core.set_github_token(self.TOKEN)
        core.log(f"leak {self.TOKEN} here")
        report = core.debug_report()
        self.assertNotIn(self.TOKEN, report)
        self.assertIn("***", report)
        self.assertIn("Dataset Scout", report)

    def test_debug_report_reads_log_from_disk(self):
        # v0.1.4 T7: the report shows the on-disk log, so a
        # crash from a previous session is visible even though
        # the in-memory ring is empty.
        core.log("first session crashed here")
        core._RING.clear()
        report = core.debug_report()
        self.assertIn("first session crashed here", report)

    def test_debug_report_says_when_log_is_empty(self):
        report = core.debug_report()
        self.assertIn("(No log lines yet.)", report)

    def test_log_error_captures_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            core.log_error("something failed", exc)
        lines = core.ring_lines()
        self.assertTrue(any("something failed" in line for line in lines))
        self.assertTrue(any("ValueError: boom" in line for line in lines))


class BadDataTests(TempHomeMixin, unittest.TestCase):
    """T1: bad numbers and bad rows never crash the app."""

    def test_bad_stars_becomes_zero(self):
        item = core.normalize_item({"title": "x", "url": "https://e.com",
                                    "stars": "five"})
        self.assertEqual(item["stars"], 0)

    def test_bad_downloads_becomes_zero(self):
        item = core.normalize_item({"title": "x", "url": "https://e.com",
                                    "downloads": "lots"})
        self.assertEqual(item["downloads"], 0)

    def test_safe_int_handles_none_and_words(self):
        self.assertEqual(core._safe_int(None), 0)
        self.assertEqual(core._safe_int("12"), 12)
        self.assertEqual(core._safe_int(3.9), 3)

    def test_bad_rows_are_skipped_and_the_list_still_loads(self):
        raw = {"items": [
            {"title": "Good", "url": "https://example.com/good",
             "stars": "five"},
            "not a dict at all",
            {"title": "No URL here"},
            {"title": "Also good", "url": "https://example.com/good2"},
        ]}
        core.list_path().parent.mkdir(parents=True, exist_ok=True)
        core.list_path().write_text(json.dumps(raw), encoding="utf-8")
        items = core.load_list()
        self.assertEqual([item["title"] for item in items],
                         ["Good", "Also good"])
        self.assertEqual(items[0]["stars"], 0)

    def test_corrupt_list_file_loads_empty(self):
        core.list_path().parent.mkdir(parents=True, exist_ok=True)
        core.list_path().write_text("{not json", encoding="utf-8")
        self.assertEqual(core.load_list(), [])


class AtomicSaveTests(TempHomeMixin, unittest.TestCase):
    """T2: saves are atomic and keep a backup."""

    def test_second_save_keeps_a_backup(self):
        first = [{"title": "One", "url": "https://example.com/1"}]
        second = [{"title": "Two", "url": "https://example.com/2"}]
        core.save_list(first)
        core.save_list(second)
        self.assertEqual(core.load_list()[0]["title"], "Two")
        bak = core.list_path().with_name("mylist.json.bak")
        self.assertTrue(bak.is_file())
        data = json.loads(bak.read_text(encoding="utf-8"))
        titles = [item["title"]
                  for col in data["collections"] for item in col["items"]]
        self.assertEqual(titles, ["One"])

    def test_no_tmp_file_left_behind(self):
        core.save_list([{"title": "One", "url": "https://example.com/1"}])
        self.assertEqual(list(core.home_dir().glob("*.tmp")), [])


class NormalizeUrlTests(unittest.TestCase):
    """T7: the same link in different shapes is one link."""

    def test_trailing_slash_dropped(self):
        self.assertEqual(
            core.normalize_url("https://example.com/a/"),
            "https://example.com/a")

    def test_www_dropped_and_host_lowercased(self):
        self.assertEqual(
            core.normalize_url("https://WWW.Example.com/a"),
            "https://example.com/a")

    def test_default_port_dropped(self):
        self.assertEqual(
            core.normalize_url("https://example.com:443/a"),
            "https://example.com/a")

    def test_http_and_https_are_the_same_link(self):
        # v0.1.3 T24: the scheme is not part of the identity.
        self.assertEqual(
            core.normalize_url("http://example.com/a"),
            core.normalize_url("https://example.com/a"))

    def test_http_default_port_matches_https(self):
        self.assertEqual(
            core.normalize_url("http://example.com:80/a"),
            core.normalize_url("https://example.com:443/a"))

    def test_bare_host_is_the_root_path(self):
        # v0.1.3 T24: example.com and example.com/ are one link.
        self.assertEqual(
            core.normalize_url("https://example.com"),
            core.normalize_url("https://example.com/"))

    def test_fragment_dropped(self):
        self.assertEqual(
            core.normalize_url("https://example.com/a#section"),
            "https://example.com/a")

    def test_query_kept(self):
        self.assertEqual(
            core.normalize_url("https://example.com/a?x=1"),
            "https://example.com/a?x=1")

    def test_add_to_list_dedups_by_normalised_url(self):
        items, added = core.add_to_list(
            [], {"title": "A", "url": "https://example.com/a"})
        self.assertTrue(added)
        items, added = core.add_to_list(
            items, {"title": "A again",
                    "url": "https://www.example.com/a/"})
        self.assertFalse(added)
        self.assertEqual(len(items), 1)

    def test_remove_from_list_matches_normalised_url(self):
        items = [{"title": "A", "url": "https://example.com/a/"}]
        self.assertEqual(
            core.remove_from_list(items, "https://www.example.com/a"),
            [])


class LogCleanupTests(TempHomeMixin, unittest.TestCase):
    """T8: logs older than a week are deleted on startup."""

    def test_old_log_deleted_recent_log_kept(self):
        logs = core.home_dir() / "logs"
        logs.mkdir(parents=True)
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        old = datetime.now(timezone.utc) - timedelta(days=9)
        old_name = old.strftime("%Y%m%d")
        (logs / f"scout-{today}.log").write_text("new", encoding="utf-8")
        (logs / f"scout-{old_name}.log").write_text("old", encoding="utf-8")
        core._LOG_CLEANUP_DONE = False
        core._cleanup_old_logs()
        self.assertFalse((logs / f"scout-{old_name}.log").exists())
        self.assertTrue((logs / f"scout-{today}.log").exists())

    def test_cleanup_runs_once_per_process(self):
        core._LOG_CLEANUP_DONE = False
        core._cleanup_old_logs()
        self.assertTrue(core._LOG_CLEANUP_DONE)
        core._cleanup_old_logs()  # second call is a no-op, not an error
        self.assertTrue(core._LOG_CLEANUP_DONE)


class ImportTests(unittest.TestCase):
    """T13: exports read back in."""

    def _items(self):
        return [core.normalize_item(
            {"title": "Alpha", "url": "https://example.com/a",
             "category": "reasoning", "source": "GitHub",
             "stars": 5, "notes": "good one"}),
            core.normalize_item(
            {"title": "Beta", "url": "https://example.com/b",
             "category": "training", "source": "Hugging Face",
             "stars": 0})]

    def test_markdown_round_trip(self):
        text = core.export_markdown(self._items())
        back = core.import_file_text(text)
        self.assertEqual([item["url"] for item in back],
                         ["https://example.com/a",
                          "https://example.com/b"])
        self.assertEqual(back[0]["category"], "reasoning")
        self.assertEqual(back[0]["notes"], "good one")
        self.assertEqual(back[1]["category"], "training")

    def test_json_round_trip(self):
        text = core.export_json(self._items())
        back = core.import_file_text(text)
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0]["title"], "Alpha")

    def test_stella_manifest_round_trip(self):
        text = core.export_stella_manifest(self._items())
        back = core.import_file_text(text)
        self.assertEqual(len(back), 2)
        self.assertEqual(back[1]["url"], "https://example.com/b")

    def test_empty_file_is_a_plain_error(self):
        with self.assertRaises(ValueError):
            core.import_file_text("nothing here at all")

    def test_bad_json_is_a_plain_error(self):
        with self.assertRaises(ValueError):
            core.import_file_text('{"items": nope}')


class SearchCacheTests(TempHomeMixin, unittest.TestCase):
    """T14: search cache + history."""

    def test_cache_round_trip(self):
        results = [{"title": "A", "url": "https://example.com/a"}]
        core.save_search_cache("math", "reasoning", results)
        self.assertEqual(
            core.load_search_cache("math", "reasoning"), results)

    def test_cache_miss_on_different_query(self):
        core.save_search_cache("math", "reasoning", [])
        self.assertIsNone(core.load_search_cache("other", "reasoning"))

    def test_expired_cache_returns_none(self):
        core.save_search_cache("math", "reasoning", [])
        path = core.home_dir() / "cache.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data["entries"].values():
            entry["saved"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(core.load_search_cache("math", "reasoning"))

    def test_history_dedups_and_caps(self):
        core.save_history("math", "reasoning")
        core.save_history("code", "all")
        core.save_history("math", "reasoning")
        history = core.load_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["query"], "math")
        for n in range(12):
            core.save_history(f"q{n}", "all")
        self.assertEqual(len(core.load_history()), 10)
        self.assertEqual(core.load_history()[0]["query"], "q11")

    def test_empty_history_query_is_ignored(self):
        core.save_history("   ", "all")
        self.assertEqual(core.load_history(), [])


class CollectionsTests(TempHomeMixin, unittest.TestCase):
    """T30: named collections, with old lists migrating."""

    def test_old_format_migrates_to_my_list(self):
        raw = {"items": [{"title": "A",
                          "url": "https://example.com/a"}]}
        core.list_path().parent.mkdir(parents=True, exist_ok=True)
        core.list_path().write_text(json.dumps(raw), encoding="utf-8")
        active, names = core.load_collections()
        self.assertEqual(active, "My List")
        self.assertEqual(names, ["My List"])
        self.assertEqual(len(core.load_list()), 1)

    def test_collections_save_and_switch(self):
        first = [{"title": "A", "url": "https://example.com/a"}]
        second = [{"title": "B", "url": "https://example.com/b"}]
        core.save_collections("One", [
            {"name": "One", "items": first},
            {"name": "Two", "items": second},
        ])
        self.assertEqual(core.load_list()[0]["title"], "A")
        core.save_collections("Two", [
            {"name": "One", "items": first},
            {"name": "Two", "items": second},
        ])
        self.assertEqual(core.load_list()[0]["title"], "B")

    def test_save_list_keeps_other_collections(self):
        core.save_collections("One", [
            {"name": "One", "items": []},
            {"name": "Two", "items": [
                {"title": "B", "url": "https://example.com/b"}]},
        ])
        core.save_list([{"title": "A", "url": "https://example.com/a"}])
        _active, names = core.load_collections()
        self.assertEqual(names, ["One", "Two"])
        data = core.load_list_data()
        two = [col for col in data["collections"]
               if col["name"] == "Two"][0]
        self.assertEqual(len(two["items"]), 1)


class LicenseFilterTests(unittest.TestCase):
    """T32: the license filter keeps exact matches only."""

    def _results(self):
        return [
            {"title": "A", "license": "Apache-2.0", "stars": 0,
             "updated": ""},
            {"title": "B", "license": "MIT", "stars": 0, "updated": ""},
            {"title": "C", "license": "", "stars": 0, "updated": ""},
        ]

    def test_no_filter_keeps_everything(self):
        kept = core.filter_results(self._results(), False, None, 0)
        self.assertEqual(len(kept), 3)

    def test_filter_keeps_exact_match_case_insensitive(self):
        kept = core.filter_results(self._results(), False, None, 0,
                                   license_filter="apache-2.0")
        self.assertEqual([item["title"] for item in kept], ["A"])

    def test_filter_hides_unknown_licenses(self):
        kept = core.filter_results(self._results(), False, None, 0,
                                   license_filter="MIT")
        self.assertEqual([item["title"] for item in kept], ["B"])


class HelpersTests(unittest.TestCase):
    def test_category_key_for_label(self):
        self.assertEqual(core.category_key_for_label("Reasoning"),
                         "reasoning")
        self.assertEqual(core.category_key_for_label("reasoning"),
                         "reasoning")
        self.assertEqual(core.category_key_for_label("My Custom Stuff"),
                         "my-custom-stuff")
        self.assertEqual(core.category_key_for_label(""), "all")

    def test_parse_version(self):
        self.assertEqual(core.parse_version("v0.1.2"), (0, 1, 2, 1))
        self.assertEqual(core.parse_version("1.0"), (1, 0, 1))
        self.assertEqual(core.parse_version("garbage"), (0,))
        self.assertLess(core.parse_version("v0.1.1"),
                        core.parse_version("v0.1.2"))

    def test_parse_version_prerelease_orders_between_releases(self):
        # v0.1.3 T43: a prerelease is older than its own release
        # but newer than the previous release.
        beta = core.parse_version("v0.1.2-beta")
        self.assertLess(beta, core.parse_version("v0.1.2"))
        self.assertGreater(beta, core.parse_version("v0.1.1"))
        self.assertLess(core.parse_version("v0.1.2-alpha"),
                        core.parse_version("v0.1.2"))

    def test_parse_version_build_metadata_does_not_change_order(self):
        self.assertEqual(core.parse_version("v1.0+build"),
                         core.parse_version("v1.0"))

    def test_format_size(self):
        self.assertEqual(core.format_size(500), "500 bytes")
        self.assertEqual(core.format_size(2048), "2.0 KB")
        self.assertEqual(core.format_size(3 * 1024 * 1024), "3.0 MB")
        self.assertEqual(core.format_size("nope"), "")


class ExperienceTests(TempHomeMixin, unittest.TestCase):
    """v0.1.3 T25/T35/T43: description + topics, BOM imports,
    source-id cleaning, atomic text writes, parsed-time sort."""

    def test_description_and_topics_survive_save_and_reload(self):
        item = {"title": "A", "url": "https://example.com/a",
                "description": "A fine dataset.",
                "topics": ["math", "word-problems"]}
        core.save_list([core.normalize_item(item)])
        loaded = core.load_list()
        self.assertEqual(loaded[0]["description"], "A fine dataset.")
        self.assertEqual(loaded[0]["topics"], ["math", "word-problems"])

    def test_normalize_item_guards_non_list_topics(self):
        item = core.normalize_item({"url": "https://example.com/a",
                                    "topics": "not-a-list"})
        self.assertEqual(item["topics"], [])

    def test_bom_json_imports(self):
        text = ('\ufeff{"items": [{"title": "A", '
                '"url": "https://example.com/a"}]}')
        items = core.import_file_text(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.com/a")

    def test_bom_markdown_imports(self):
        text = ("\ufeff# Dataset Scout list\n\n"
                "- [A](https://example.com/a)\n")
        items = core.import_file_text(text)
        self.assertEqual(len(items), 1)

    def test_clean_source_id(self):
        self.assertEqual(
            core.clean_source_id("owner/repo.git?tab=readme#x"),
            "owner/repo")
        self.assertEqual(core.clean_source_id("acme/tiny/"), "acme/tiny")
        self.assertEqual(core.clean_source_id("acme/tiny"), "acme/tiny")

    def test_write_text_atomic_round_trip(self):
        path = Path(self.tmp.name) / "sub" / "out.jsonl"
        core.write_text_atomic(path, "hello\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")
        self.assertEqual(list(path.parent.glob("*.tmp-*")), [])

    def test_updated_sort_key_orders_by_instant_not_string(self):
        # The audit's pair: +12:00 sorts after Z as a raw string,
        # but it is the earlier instant.
        later = {"updated": "2026-01-01T00:00:00Z"}
        earlier = {"updated": "2026-01-01T00:00:00+12:00"}
        self.assertLess(core.updated_sort_key(earlier),
                        core.updated_sort_key(later))
        items = [earlier, later, {"updated": "garbage"}]
        items.sort(key=core.updated_sort_key, reverse=True)
        self.assertEqual(items[0], later)
        self.assertEqual(items[-1], {"updated": "garbage"})


if __name__ == "__main__":
    unittest.main()
