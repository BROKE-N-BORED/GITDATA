"""Core logic tests — no network, no Qt."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
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


class SourcesTests(TempHomeMixin, unittest.TestCase):
    def test_defaults_on_first_run(self):
        sites = core.load_sources()
        self.assertEqual(len(sites), 3)
        names = [s["name"] for s in sites]
        self.assertIn("Hugging Face Datasets", names)
        self.assertIn("Kaggle Datasets", names)
        self.assertIn("Papers With Code", names)

    def test_add_remove_persist(self):
        sites = core.load_sources()
        sites.append({"name": "Example", "url": "https://example.com/?q={q}"})
        core.save_sources(sites)
        self.assertEqual(len(core.load_sources()), 4)
        kept = [s for s in core.load_sources() if s["name"] != "Example"]
        core.save_sources(kept)
        self.assertEqual(len(core.load_sources()), 3)

    def test_restore_defaults(self):
        core.save_sources([{"name": "Only", "url": "https://only.example/"}])
        restored = core.restore_default_sources()
        self.assertEqual(len(restored), 3)
        self.assertEqual(len(core.load_sources()), 3)

    def test_corrupt_file_falls_back_to_defaults(self):
        core.sources_path().parent.mkdir(parents=True, exist_ok=True)
        core.sources_path().write_text("not json", encoding="utf-8")
        self.assertEqual(len(core.load_sources()), 3)

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

    def test_log_error_captures_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            core.log_error("something failed", exc)
        lines = core.ring_lines()
        self.assertTrue(any("something failed" in line for line in lines))
        self.assertTrue(any("ValueError: boom" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
