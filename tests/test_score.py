"""Tests for the plain-words quality score (T65)."""

import unittest
from datetime import datetime, timezone

from scout import score

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def _item(**overrides):
    item = {
        "title": "Some Dataset",
        "url": "https://example.com/some-data",
        "source": "Hugging Face",
        "stars": 0,
        "downloads": 0,
        "updated": "",
        "license": "",
        "stella_status": "",
    }
    item.update(overrides)
    return item


class ScoreTests(unittest.TestCase):
    def test_fresh_popular_licensed_ready_scores_high(self):
        result = score.score_item(_item(
            updated="2026-11-10T00:00:00Z",
            stars=500, downloads=10000,
            license="MIT", stella_status="ready"), now=NOW)
        self.assertGreaterEqual(result["score"], 80)
        self.assertEqual(result["label"], "Worth a look")
        self.assertEqual(len(result["reasons"]), 4)

    def test_stale_unlicensed_no_files_scores_low(self):
        result = score.score_item(_item(
            updated="2020-01-01T00:00:00Z",
            stella_status="none"), now=NOW)
        self.assertLess(result["score"], 25)
        self.assertEqual(result["label"], "Probably skip")

    def test_unusable_dataset_never_reaches_top_bands(self):
        # v0.1.4 T20: fresh, popular and licensed — but with no
        # usable files the score stays below Promising.
        for status in ("none", "unsupported", "no-fields"):
            result = score.score_item(_item(
                updated="2026-11-10T00:00:00Z",
                stars=500, downloads=10000,
                license="MIT", stella_status=status), now=NOW)
            self.assertLessEqual(result["score"], 49,
                                 f"status {status} scored "
                                 f"{result['score']}")
            self.assertNotIn(result["label"],
                             ("Worth a look", "Promising"))

    def test_unusable_shape_reason_says_so(self):
        # v0.1.4 T20: the shape reason for an unusable dataset
        # names the cap instead of "STELLA shape not checked
        # yet".
        for status, words in (
                ("unsupported", "Cannot convert these files"),
                ("no-fields", "no fields Scout can use")):
            result = score.score_item(_item(stella_status=status),
                                      now=NOW)
            joined = " ".join(result["reasons"])
            self.assertIn(words, joined)
            self.assertIn("stays below Promising", joined)

    def test_reasons_name_each_part(self):
        result = score.score_item(_item(
            updated="2026-11-14T00:00:00Z",
            stars=10, license="Apache-2.0",
            stella_status="convert"), now=NOW)
        joined = " ".join(result["reasons"])
        self.assertIn("Updated", joined)
        self.assertIn("10 stars", joined)
        self.assertIn("Apache-2.0", joined)
        self.assertIn("Converts", joined)

    def test_missing_date_scores_zero_for_activity(self):
        fresh = score.score_item(
            _item(updated="2026-11-14T00:00:00Z"), now=NOW)
        dateless = score.score_item(_item(updated=""), now=NOW)
        self.assertGreater(fresh["score"], dateless["score"])
        self.assertIn("No update date shown", dateless["reasons"][0])

    def test_no_license_scores_zero_for_license(self):
        licensed = score.score_item(_item(license="MIT"), now=NOW)
        bare = score.score_item(_item(license=""), now=NOW)
        self.assertEqual(licensed["score"] - bare["score"], 20)
        self.assertIn("No license shown", bare["reasons"][2])

    def test_unchecked_shape_is_neutral_not_punished(self):
        unchecked = score.score_item(_item(stella_status=""), now=NOW)
        none = score.score_item(_item(stella_status="none"), now=NOW)
        ready = score.score_item(_item(stella_status="ready"), now=NOW)
        self.assertGreater(unchecked["score"], none["score"])
        self.assertLess(unchecked["score"], ready["score"])

    def test_score_stays_in_range(self):
        huge = score.score_item(_item(
            updated="2026-11-15T00:00:00Z",
            stars=10 ** 9, downloads=10 ** 9,
            license="MIT", stella_status="ready"), now=NOW)
        self.assertLessEqual(huge["score"], 100)
        empty = score.score_item(_item(), now=NOW)
        self.assertGreaterEqual(empty["score"], 0)

    def test_bands(self):
        self.assertEqual(score.score_label(100), "Worth a look")
        self.assertEqual(score.score_label(80), "Worth a look")
        self.assertEqual(score.score_label(79), "Promising")
        self.assertEqual(score.score_label(50), "Promising")
        self.assertEqual(score.score_label(49), "Maybe")
        self.assertEqual(score.score_label(25), "Maybe")
        self.assertEqual(score.score_label(24), "Probably skip")

    def test_describe_score_is_one_plain_line(self):
        result = score.score_item(_item(
            updated="2026-11-14T00:00:00Z", license="MIT"), now=NOW)
        text = score.describe_score(result)
        self.assertIn(f"Score {result['score']}", text)
        self.assertIn(result["label"], text)
        self.assertIn("MIT", text)


class PluralizationTests(unittest.TestCase):
    """v0.1.4 T38: "1 week ago", not "1 week(s) ago"."""

    def test_singular_and_plural_forms(self):
        cases = [
            ("2026-11-08T00:00:00Z", "Updated 1 week ago"),
            ("2026-11-01T00:00:00Z", "Updated 2 weeks ago"),
            ("2026-10-16T00:00:00Z", "Updated 1 month ago"),
            ("2026-09-16T00:00:00Z", "Updated 2 months ago"),
            ("2025-11-15T00:00:00Z", "Updated 1 year ago"),
            ("2024-11-15T00:00:00Z", "Updated 2 years ago"),
        ]
        for updated, expected in cases:
            result = score.score_item(_item(updated=updated), now=NOW)
            self.assertEqual(result["reasons"][0], expected)
            self.assertNotIn("(s)", score.describe_score(result))


if __name__ == "__main__":
    unittest.main()
