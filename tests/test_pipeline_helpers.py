"""Tests for small pure-Python helpers in modules.pipeline.

We deliberately do not import or exercise the full pipeline here — it would
need a filesystem and an LLM client. The helpers tested below are pure and
must stay isolated so beginner_summary stays trustworthy.
"""

from __future__ import annotations

from modules.pipeline import _weakest_chapter_payload, _weakest_sample_payload


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


def _section(
    index: int,
    overall: int,
    status: str,
    *,
    label: str = "Abschnitt",
    fix: str = "fix me",
    risk: str = "RISK",
) -> dict:
    return {
        "index": index,
        "label": label,
        "overall": overall,
        "status": status,
        "risk": risk,
        "fix": fix,
    }


def test_weakest_sample_payload_returns_none_when_no_report():
    assert _weakest_sample_payload(None) is None


def test_weakest_sample_payload_returns_none_when_no_sections():
    assert _weakest_sample_payload({"sections": []}) is None
    assert _weakest_sample_payload({}) is None


def test_weakest_sample_payload_returns_none_when_all_ready():
    payload = {
        "sections": [
            _section(1, 90, "READY"),
            _section(2, 88, "READY"),
        ]
    }
    assert _weakest_sample_payload(payload) is None


def test_weakest_sample_payload_picks_lowest_scoring_risky_section():
    payload = {
        "sections": [
            _section(1, 80, "REVIEW", label="Auftakt", fix="fix-1"),
            _section(2, 40, "FIX", label="Eroeffnung", fix="fix-2", risk="ABBRUCH-RISIKO"),
            _section(3, 70, "REVIEW", label="Methode", fix="fix-3"),
        ]
    }
    weakest = _weakest_sample_payload(payload)
    assert weakest is not None
    assert weakest["index"] == 2
    assert weakest["label"] == "Eroeffnung"
    assert weakest["overall"] == 40
    assert weakest["status"] == "FIX"
    assert weakest["risk"] == "ABBRUCH-RISIKO"
    assert weakest["fix"] == "fix-2"


def test_weakest_sample_payload_tolerates_partial_section_dicts():
    payload = {"sections": [{"index": 1, "status": "FIX"}]}
    weakest = _weakest_sample_payload(payload)
    assert weakest is not None
    assert weakest["index"] == 1
    assert weakest["overall"] == 0
    assert weakest["label"] == ""
    assert weakest["fix"] == ""


def test_weakest_sample_payload_treats_missing_status_as_risky():
    """Defensive: a section without a status field should not be suppressed."""
    payload = {"sections": [_section(1, 50, "")]}
    weakest = _weakest_sample_payload(payload)
    assert weakest is not None
    assert weakest["overall"] == 50
