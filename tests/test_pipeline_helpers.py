"""Tests for small pure-Python helpers in modules.pipeline.

We deliberately do not import or exercise the full pipeline here — it would
need a filesystem and an LLM client. The helpers tested below are pure and
must stay isolated so beginner_summary stays trustworthy.
"""

from __future__ import annotations

from modules.pipeline import (
    _top_rewrite_payload,
    _weakest_chapter_payload,
    _weakest_sample_payload,
)


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


def _bundle(
    field_key: str,
    *,
    diagnosis: list[str] | None,
    options: list[dict],
) -> dict:
    return {
        "field": field_key,
        "original": "",
        "diagnosis": diagnosis or [],
        "options": options,
    }


def _option(text: str, keyword_score: int, char_count: int | None = None) -> dict:
    return {
        "text": text,
        "char_count": char_count if char_count is not None else len(text),
        "keyword_score": keyword_score,
        "motivation": f"Motivation für {text[:20]}",
    }


def test_top_rewrite_payload_returns_none_when_no_report():
    assert _top_rewrite_payload(None) is None


def test_top_rewrite_payload_returns_none_when_no_bundles():
    assert _top_rewrite_payload({"bundles": []}) is None
    assert _top_rewrite_payload({}) is None


def test_top_rewrite_payload_returns_none_when_no_diagnosis_findings():
    """If every field is already in good shape, no rewrite is suggested."""
    payload = {
        "bundles": [
            _bundle("title", diagnosis=[], options=[_option("Foo", 80)]),
            _bundle("subtitle", diagnosis=[], options=[_option("Bar", 70)]),
        ]
    }
    assert _top_rewrite_payload(payload) is None


def test_top_rewrite_payload_picks_highest_keyword_score():
    payload = {
        "bundles": [
            _bundle(
                "title",
                diagnosis=["Titel zu kurz"],
                options=[_option("Title-A", 40), _option("Title-B", 80)],
            ),
            _bundle(
                "subtitle",
                diagnosis=["Untertitel ohne Zielgruppe"],
                options=[_option("Sub-A", 60)],
            ),
        ]
    }
    top = _top_rewrite_payload(payload)
    assert top is not None
    assert top["field"] == "title"
    assert top["text"] == "Title-B"
    assert top["keyword_score"] == 80


def test_top_rewrite_payload_breaks_score_tie_by_shorter_char_count():
    payload = {
        "bundles": [
            _bundle(
                "subtitle",
                diagnosis=["x"],
                options=[
                    _option("Long-Subtitle-Variante", 60, char_count=40),
                    _option("Punchy", 60, char_count=10),
                ],
            ),
        ]
    }
    top = _top_rewrite_payload(payload)
    assert top is not None
    assert top["text"] == "Punchy"
    assert top["char_count"] == 10


def test_top_rewrite_payload_tie_breaks_field_priority_title_first():
    payload = {
        "bundles": [
            _bundle("subtitle", diagnosis=["x"], options=[_option("Sub", 75, 12)]),
            _bundle("title", diagnosis=["y"], options=[_option("Tit", 75, 12)]),
        ]
    }
    top = _top_rewrite_payload(payload)
    assert top is not None
    assert top["field"] == "title"


def test_top_rewrite_payload_skips_bundles_without_diagnosis():
    payload = {
        "bundles": [
            _bundle("title", diagnosis=[], options=[_option("Strong", 99)]),
            _bundle(
                "description_lead",
                diagnosis=["Beschreibung kurz"],
                options=[_option("Desc", 30)],
            ),
        ]
    }
    top = _top_rewrite_payload(payload)
    assert top is not None
    assert top["field"] == "description_lead"
    assert top["keyword_score"] == 30


def test_top_rewrite_payload_skips_options_with_empty_text():
    payload = {
        "bundles": [
            _bundle(
                "title",
                diagnosis=["x"],
                options=[
                    {"text": "  ", "char_count": 0, "keyword_score": 99, "motivation": ""},
                    _option("Real", 50),
                ],
            ),
        ]
    }
    top = _top_rewrite_payload(payload)
    assert top is not None
    assert top["text"] == "Real"


def test_top_rewrite_payload_returns_immutable_safe_dict():
    """Mutating the returned payload must not affect the source bundle."""
    src_option = _option("Tit", 80)
    payload = {"bundles": [_bundle("title", diagnosis=["x"], options=[src_option])]}
    top = _top_rewrite_payload(payload)
    assert top is not None
    top["text"] = "MUTATED"
    assert src_option["text"] == "Tit"
