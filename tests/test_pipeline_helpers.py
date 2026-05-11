"""Tests for small pure-Python helpers in modules.pipeline.

We deliberately do not import or exercise the full pipeline here — it would
need a filesystem and an LLM client. The helpers tested below are pure and
must stay isolated so beginner_summary stays trustworthy.
"""

from __future__ import annotations

from modules.pipeline import _weakest_chapter_payload


def _chap(index: int, title: str, overall: int, fix: str = "fix me") -> dict:
    return {
        "index": index,
        "title": title,
        "overall": overall,
        "status": "FIX" if overall < 65 else "REVIEW",
        "fix": fix,
        "scores": {"promise": 5, "proof": 5, "value": 5, "transition": 5},
    }


def test_weakest_chapter_payload_returns_none_when_no_report():
    assert _weakest_chapter_payload(None) is None


def test_weakest_chapter_payload_empty_chapter_list_returns_empty_list():
    assert _weakest_chapter_payload({"chapters": []}) == []


def test_weakest_chapter_payload_picks_lowest_scores_ascending():
    payload = {
        "chapters": [
            _chap(1, "Eins", 80, "fix1"),
            _chap(2, "Zwei", 40, "fix2"),
            _chap(3, "Drei", 60, "fix3"),
            _chap(4, "Vier", 90, "fix4"),
        ]
    }
    weakest = _weakest_chapter_payload(payload, limit=3)
    assert weakest is not None
    assert [c["index"] for c in weakest] == [2, 3, 1]
    assert weakest[0]["fix"] == "fix2"


def test_weakest_chapter_payload_clamps_to_available_chapters():
    payload = {"chapters": [_chap(1, "Eins", 40), _chap(2, "Zwei", 60)]}
    weakest = _weakest_chapter_payload(payload, limit=5)
    assert len(weakest) == 2


def test_weakest_chapter_payload_zero_limit_returns_empty():
    payload = {"chapters": [_chap(1, "Eins", 40)]}
    assert _weakest_chapter_payload(payload, limit=0) == []


def test_weakest_chapter_payload_tolerates_partial_chapter_dicts():
    payload = {"chapters": [{"index": 1}]}
    weakest = _weakest_chapter_payload(payload)
    assert weakest is not None
    assert weakest[0]["index"] == 1
    assert weakest[0]["overall"] == 0
    assert weakest[0]["title"] == ""
    assert weakest[0]["fix"] == ""
