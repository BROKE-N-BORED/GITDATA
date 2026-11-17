"""A plain-words quality score per search result (T65).

Presentation, not new data: every input is already parsed when
a result arrives (updated date, stars/downloads, license, STELLA
shape). The score is 0-100, from four parts that add up in the
open, so the user can see why a result scored what it did:

- Activity, 0-35: how recently the dataset was updated.
- Popularity, 0-25: stars and downloads, on a log scale, so a
  dataset with 100 stars does not need 10x the stars to matter.
- License clarity, 0-20: a named license vs none shown.
- STELLA shape, 0-20: already in STELLA's shape beats convertible
  beats no data files; not checked yet sits in the middle instead
  of being punished for something the user has not asked about.

A dataset with no usable files never reaches the top bands
(v0.1.4 T20): however active or popular it is, its score stops
below "Promising", so the band reflects usability, not just
popularity.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from . import core

# (lowest score in the band, label). The label is what the number
# means in plain words.
_SCORE_BANDS = (
    (80, "Worth a look"),
    (50, "Promising"),
    (25, "Maybe"),
    (0, "Probably skip"),
)


def score_label(score: int) -> str:
    """The plain-words band for a score."""
    for floor, label in _SCORE_BANDS:
        if score >= floor:
            return label
    return _SCORE_BANDS[-1][1]


def _activity_points(item: dict, now: datetime) -> tuple[int, str]:
    dt = core.parse_iso_date(item.get("updated"))
    if dt is None:
        return 0, "No update date shown"
    age_days = max(0, (now - dt).days)
    if age_days <= 90:
        points = 35
    elif age_days <= 180:
        points = 28
    elif age_days <= 365:
        points = 20
    elif age_days <= 730:
        points = 10
    else:
        points = 0
    if age_days < 7:
        when = "this week"
    elif age_days < 30:
        count = age_days // 7
        when = f"{count} week ago" if count == 1 else f"{count} weeks ago"
    elif age_days < 365:
        count = age_days // 30
        when = f"{count} month ago" if count == 1 else f"{count} months ago"
    else:
        count = age_days // 365
        when = f"{count} year ago" if count == 1 else f"{count} years ago"
    return points, f"Updated {when}"


def _popularity_points(item: dict) -> tuple[int, str]:
    stars = core._safe_int(item.get("stars"))
    downloads = core._safe_int(item.get("downloads"))
    total = stars + downloads
    if total <= 0:
        return 0, "No stars or downloads shown"
    points = min(25, int(5 * math.log10(total + 1)))
    bits = []
    if stars:
        bits.append(f"{stars} stars")
    if downloads:
        bits.append(f"{downloads} downloads")
    return points, " and ".join(bits)


def _license_points(item: dict) -> tuple[int, str]:
    license_text = str(item.get("license") or "").strip()
    if license_text:
        return 20, f"License shown: {license_text}"
    return 0, "No license shown"


def _shape_points(item: dict) -> tuple[int, str]:
    status = item.get("stella_status") or ""
    if status == "ready":
        return 20, "Already in STELLA shape"
    if status == "convert":
        return 12, "Converts to STELLA shape"
    if status == "none":
        return 0, "No data files found, so the score stays below Promising"
    if status == "unsupported":
        return 0, ("Cannot convert these files, "
                   "so the score stays below Promising")
    if status == "no-fields":
        return 0, ("Checked, no fields Scout can use, "
                   "so the score stays below Promising")
    if status == "needs-reader":
        return 5, "Needs the Parquet reader to check"
    if status == "unknown":
        return 5, "STELLA check could not read it"
    return 5, "STELLA shape not checked yet"


# A dataset with no usable files never scores above this (v0.1.4
# T20): 49 is the top of "Maybe", so the top bands stay out of
# reach however active or popular the dataset is.
_UNUSABLE_SCORE_CAP = 49

_UNUSABLE_STATUSES = ("none", "unsupported", "no-fields")


def score_item(item: dict, now: datetime | None = None) -> dict:
    """Score one result: {"score", "label", "reasons"}.

    Reasons are one plain line per part, in the same order as the
    parts above, so the detail view can show the working.
    """
    now = now or datetime.now(timezone.utc)
    parts = [
        _activity_points(item, now),
        _popularity_points(item),
        _license_points(item),
        _shape_points(item),
    ]
    total = sum(points for points, _reason in parts)
    if item.get("stella_status") in _UNUSABLE_STATUSES:
        total = min(total, _UNUSABLE_SCORE_CAP)
    score = max(0, min(100, total))
    return {
        "score": score,
        "label": score_label(score),
        "reasons": [reason for _points, reason in parts],
    }


def describe_score(result: dict) -> str:
    """One line for the detail view: the score, its band, and why."""
    reasons = "; ".join(result.get("reasons") or [])
    text = f"Score {result.get('score', 0)} ({result.get('label', '')})"
    if reasons:
        text += f": {reasons}"
    return text + "."
