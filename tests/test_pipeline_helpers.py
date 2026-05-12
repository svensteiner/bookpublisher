"""Tests for small pure-Python helpers in modules.pipeline.

We deliberately do not import or exercise the full pipeline here — it would
need a filesystem and an LLM client. The helpers tested below are pure and
must stay isolated so beginner_summary stays trustworthy.
"""

from __future__ import annotations

from modules.competitive_positioning import (
    CompetitorArchetype,
    DifferentiationAngle,
    PositioningReport,
)
from modules.kdp_keywords import KDPKeyword
from modules.personas import BuyerPersona, PersonaReport
from modules.pipeline import (
    _round_delta_payload,
    _score_history_payload,
    _top_arc_payload,
    _top_chapter_balance_payload,
    _top_kdp_keywords_payload,
    _top_persona_payload,
    _top_positioning_payload,
    _top_rewrite_payload,
    _weakest_chapter_payload,
    _weakest_sample_payload,
)
from modules.round_delta import compute_round_delta


def _kw(text: str, source: str, *, char_count: int | None = None, rationale: str = "r") -> KDPKeyword:
    return KDPKeyword(
        text=text,
        char_count=char_count if char_count is not None else len(text),
        source=source,
        rationale=rationale,
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


# ─── _round_delta_payload ─────────────────────────────────────────────


def _round(
    round_id: str,
    score: int,
    decision: str,
    fixes: list[str],
) -> dict:
    return {
        "round_id": round_id,
        "decision": decision,
        "industrial_score": score,
        "investor_grade": score / 10,
        "required_fixes": fixes,
    }


def test_round_delta_payload_returns_none_when_no_delta():
    assert _round_delta_payload(None) is None


def test_round_delta_payload_returns_none_for_first_round():
    """Round 1 has no previous round — no progress to celebrate."""
    rounds = [_round("r1", 70, "GO_AFTER_FIXES", ["fix-a", "fix-b"])]
    delta = compute_round_delta("book", rounds)
    assert delta is not None
    assert delta.has_previous is False
    assert _round_delta_payload(delta) is None


def test_round_delta_payload_counts_resolved_persistent_new():
    rounds = [
        _round("r1", 70, "GO_AFTER_FIXES", ["fix-a", "fix-b", "fix-c"]),
        _round("r2", 85, "GO", ["fix-b", "fix-d"]),
    ]
    delta = compute_round_delta("book", rounds)
    payload = _round_delta_payload(delta)
    assert payload is not None
    assert payload["resolved_count"] == 2  # fix-a, fix-c
    assert payload["persistent_count"] == 1  # fix-b
    assert payload["new_count"] == 1  # fix-d
    assert payload["score_delta"] == 15
    assert payload["decision_changed"] is True
    assert payload["previous_decision"] == "GO_AFTER_FIXES"
    assert payload["current_decision"] == "GO"


def test_round_delta_payload_caps_top_fix_lists():
    rounds = [
        _round("r1", 70, "GO_AFTER_FIXES", ["a", "b", "c", "d", "e"]),
        _round("r2", 80, "GO_AFTER_FIXES", ["a", "b"]),  # c, d, e resolved
    ]
    delta = compute_round_delta("book", rounds)
    payload = _round_delta_payload(delta, fix_limit=2)
    assert payload is not None
    assert payload["top_resolved"] == ["c", "d"]  # capped to 2
    assert payload["top_persistent"] == ["a", "b"]
    assert payload["resolved_count"] == 3  # but count reflects all


def test_round_delta_payload_zero_fix_limit_empties_lists():
    rounds = [
        _round("r1", 70, "GO_AFTER_FIXES", ["a", "b"]),
        _round("r2", 75, "GO_AFTER_FIXES", []),
    ]
    delta = compute_round_delta("book", rounds)
    payload = _round_delta_payload(delta, fix_limit=0)
    assert payload is not None
    assert payload["top_resolved"] == []
    assert payload["resolved_count"] == 2


def test_round_delta_payload_score_drop_reported_as_negative():
    rounds = [
        _round("r1", 85, "GO", []),
        _round("r2", 70, "GO_AFTER_FIXES", ["regression"]),
    ]
    delta = compute_round_delta("book", rounds)
    payload = _round_delta_payload(delta)
    assert payload is not None
    assert payload["score_delta"] == -15
    assert payload["new_count"] == 1
    assert payload["decision_changed"] is True


def test_round_delta_payload_decision_unchanged_flag_false():
    rounds = [
        _round("r1", 70, "GO_AFTER_FIXES", ["a"]),
        _round("r2", 72, "GO_AFTER_FIXES", ["a"]),
    ]
    delta = compute_round_delta("book", rounds)
    payload = _round_delta_payload(delta)
    assert payload is not None
    assert payload["decision_changed"] is False


def test_round_delta_payload_does_not_mutate_delta_tuples():
    """Returned lists are independent — mutating the payload must not affect the delta."""
    rounds = [
        _round("r1", 70, "GO_AFTER_FIXES", ["a", "b"]),
        _round("r2", 75, "GO_AFTER_FIXES", []),
    ]
    delta = compute_round_delta("book", rounds)
    payload = _round_delta_payload(delta)
    assert payload is not None
    payload["top_resolved"].append("MUTATED")
    # Re-querying the delta tuple must still be clean
    assert "MUTATED" not in delta.resolved_fixes


# ─── _score_history_payload ───────────────────────────────────────────


def _history_entry(timestamp: str, score: int) -> dict:
    return {
        "timestamp": timestamp,
        "round_id": "r-" + timestamp,
        "mode": "quick_qa",
        "decision": "GO_AFTER_FIXES",
        "industrial_score": score,
        "investor_grade": score / 10,
        "gates": [],
        "top_fixes": [],
        "score_delta": None,
    }


def test_score_history_payload_returns_none_when_no_history():
    assert _score_history_payload(None) is None


def test_score_history_payload_returns_none_when_empty_entries():
    assert _score_history_payload({"entries": []}) is None
    assert _score_history_payload({}) is None


def test_score_history_payload_returns_none_for_single_entry():
    """A single round has no trend to plot — no point cluttering the summary."""
    history = {"entries": [_history_entry("2025-05-10", 70)]}
    assert _score_history_payload(history) is None


def test_score_history_payload_rising_trend():
    history = {
        "entries": [
            _history_entry("2025-05-10", 70),
            _history_entry("2025-05-11", 78),
            _history_entry("2025-05-12", 85),
        ]
    }
    payload = _score_history_payload(history)
    assert payload is not None
    assert payload["trend"] == "rising"
    assert payload["first_score"] == 70
    assert payload["latest_score"] == 85
    assert payload["delta_total"] == 15
    assert payload["entry_count"] == 3
    series = payload["series"]
    assert len(series) == 3
    assert series[0]["delta"] is None  # first in window has no prior
    assert series[1]["delta"] == 8
    assert series[2]["delta"] == 7


def test_score_history_payload_falling_trend():
    history = {
        "entries": [
            _history_entry("2025-05-10", 85),
            _history_entry("2025-05-11", 70),
        ]
    }
    payload = _score_history_payload(history)
    assert payload is not None
    assert payload["trend"] == "falling"
    assert payload["delta_total"] == -15


def test_score_history_payload_stable_trend_when_endpoints_match():
    history = {
        "entries": [
            _history_entry("2025-05-10", 80),
            _history_entry("2025-05-11", 70),
            _history_entry("2025-05-12", 80),
        ]
    }
    payload = _score_history_payload(history)
    assert payload is not None
    assert payload["trend"] == "stable"
    assert payload["delta_total"] == 0


def test_score_history_payload_limit_caps_window():
    """Older entries beyond the window are dropped; entry_count stays full."""
    history = {
        "entries": [
            _history_entry("2025-05-08", 50),  # excluded by window
            _history_entry("2025-05-09", 55),  # excluded by window
            _history_entry("2025-05-10", 70),
            _history_entry("2025-05-11", 80),
            _history_entry("2025-05-12", 85),
        ]
    }
    payload = _score_history_payload(history, limit=3)
    assert payload is not None
    assert len(payload["series"]) == 3
    assert payload["first_score"] == 70  # window start, not history start
    assert payload["latest_score"] == 85
    assert payload["entry_count"] == 5  # full history count preserved


def test_score_history_payload_treats_limit_below_two_as_two():
    """A one-entry window is meaningless — clamp to at least two."""
    history = {
        "entries": [
            _history_entry("2025-05-10", 70),
            _history_entry("2025-05-11", 80),
            _history_entry("2025-05-12", 85),
        ]
    }
    payload = _score_history_payload(history, limit=1)
    assert payload is not None
    assert len(payload["series"]) == 2
    assert payload["first_score"] == 80
    assert payload["latest_score"] == 85


def test_score_history_payload_tolerates_missing_score_fields():
    history = {
        "entries": [
            {"timestamp": "2025-05-10"},
            {"timestamp": "2025-05-11", "industrial_score": 70},
        ]
    }
    payload = _score_history_payload(history)
    assert payload is not None
    assert payload["first_score"] == 0
    assert payload["latest_score"] == 70
    assert payload["trend"] == "rising"


def test_score_history_payload_is_immutable_against_caller_mutation():
    """Mutating the returned series must not affect the source history."""
    history = {
        "entries": [
            _history_entry("2025-05-10", 70),
            _history_entry("2025-05-11", 85),
        ]
    }
    payload = _score_history_payload(history)
    assert payload is not None
    payload["series"][0]["score"] = 999
    assert history["entries"][0]["industrial_score"] == 70



# ─── _top_kdp_keywords_payload ─────────────────────────────────────────

def test_top_kdp_keywords_payload_returns_none_when_no_keywords():
    assert _top_kdp_keywords_payload(None) is None
    assert _top_kdp_keywords_payload([]) is None


def test_top_kdp_keywords_payload_zero_limit_returns_empty_list():
    kws = [_kw("a b c", "subject_format")]
    assert _top_kdp_keywords_payload(kws, limit=0) == []


def test_top_kdp_keywords_payload_prefers_source_diversity_over_order():
    """Three subject_format variants must not crowd out other sources."""
    kws = [
        _kw("subject ratgeber", "subject_format", rationale="r-sf-1"),
        _kw("subject buch", "subject_format", rationale="r-sf-2"),
        _kw("subject praxis", "subject_format", rationale="r-sf-3"),
        _kw("ratgeber fuer x", "audience_format", rationale="r-af"),
        _kw("anker eins", "anchor_pair", rationale="r-ap"),
    ]
    top = _top_kdp_keywords_payload(kws, limit=3)
    assert top is not None
    sources = [item["source"] for item in top]
    assert sources == ["subject_format", "audience_format", "anchor_pair"]
    assert top[0]["text"] == "subject ratgeber"
    assert top[1]["text"] == "ratgeber fuer x"
    assert top[2]["text"] == "anker eins"


def test_top_kdp_keywords_payload_falls_back_to_order_when_diversity_insufficient():
    """If only one source family exists, fill remaining slots in order."""
    kws = [
        _kw("variant eins", "subject_format"),
        _kw("variant zwei", "subject_format"),
        _kw("variant drei", "subject_format"),
    ]
    top = _top_kdp_keywords_payload(kws, limit=3)
    assert top is not None
    assert [item["text"] for item in top] == ["variant eins", "variant zwei", "variant drei"]


def test_top_kdp_keywords_payload_clamps_to_available_keywords():
    kws = [_kw("a", "subject_format"), _kw("b", "anchor_pair")]
    top = _top_kdp_keywords_payload(kws, limit=5)
    assert top is not None
    assert len(top) == 2


def test_top_kdp_keywords_payload_payload_carries_renderer_fields():
    kws = [_kw("ratgeber praxis", "subject_format", char_count=15, rationale="why")]
    top = _top_kdp_keywords_payload(kws, limit=1)
    assert top is not None
    assert top[0] == {
        "text": "ratgeber praxis",
        "char_count": 15,
        "source": "subject_format",
        "rationale": "why",
    }


def test_top_kdp_keywords_payload_is_immutable_against_caller_mutation():
    """Mutating the returned payload must not affect the KDPKeyword source."""
    kws = [_kw("ratgeber", "subject_format", rationale="why")]
    top = _top_kdp_keywords_payload(kws, limit=1)
    assert top is not None
    top[0]["text"] = "mutated"
    assert kws[0].text == "ratgeber"


def test_top_kdp_keywords_payload_dedups_when_same_text_repeated():
    """If two keywords share the same text but different sources, we keep one."""
    kws = [
        _kw("a", "subject_format"),
        _kw("a", "anchor_pair"),  # same text — skipped after diversity round
        _kw("b", "audience_format"),
    ]
    top = _top_kdp_keywords_payload(kws, limit=3)
    assert top is not None
    texts = [item["text"] for item in top]
    assert texts.count("a") == 1


# ─── _top_positioning_payload ───────────────────────────────────────────


def _positioning(
    *,
    angles: list[DifferentiationAngle] | None = None,
    pitch: str = "Pitch text.",
    niche_label: str = "Finanzen / CFO / Controlling",
    niche_confidence: int = 80,
    audience: str = "CFOs in mittelständischen Firmen",
    subject: str = "ein Liquiditäts-Playbook",
) -> PositioningReport:
    if angles is None:
        angles = [
            DifferentiationAngle(
                key="zahlen_beweis",
                claim="Beweisführung mit Zahlen statt Behauptungen.",
                evidence="Beschreibung enthält 30 Tage und 12 Kennzahlen.",
                strength=80,
            ),
            DifferentiationAngle(
                key="operator_stimme",
                claim="Operator-/CFO-Praxisstimme.",
                evidence="Beschreibung nennt CFO-Begriffe.",
                strength=63,
            ),
        ]
    return PositioningReport(
        niche_key="finanzen_und_cfo",
        niche_label=niche_label,
        niche_confidence=niche_confidence,
        audience=audience,
        subject=subject,
        archetypes=[
            CompetitorArchetype(
                name="Klassisches Lehrbuch",
                why_it_competes="Theoretisch.",
                typical_weakness="Keine Praxis.",
            )
        ],
        unique_angles=angles,
        collision_risks=[],
        positioning_pitch=pitch,
        anchors=["cfo", "liquiditaet"],
    )


def test_top_positioning_payload_returns_none_when_report_missing():
    assert _top_positioning_payload(None) is None


def test_top_positioning_payload_returns_none_when_no_angles():
    report = _positioning(angles=[])
    assert _top_positioning_payload(report) is None


def test_top_positioning_payload_returns_none_when_only_kein_signal():
    """The fallback ``kein_signal`` angle must not surface in beginner_summary."""
    report = _positioning(angles=[
        DifferentiationAngle(
            key="kein_signal",
            claim="Kein klares Differenzierungssignal in den Metadaten erkennbar.",
            evidence="Titel, Untertitel und Beschreibung sind zu allgemein.",
            strength=0,
        )
    ])
    assert _top_positioning_payload(report) is None


def test_top_positioning_payload_returns_none_when_top_strength_zero():
    """Even a real angle key with zero strength is no signal worth showing."""
    report = _positioning(angles=[
        DifferentiationAngle(
            key="zahlen_beweis",
            claim="x",
            evidence="y",
            strength=0,
        )
    ])
    assert _top_positioning_payload(report) is None


def test_top_positioning_payload_picks_first_angle_as_top():
    """``unique_angles`` is already sorted by strength desc — first wins."""
    report = _positioning()
    payload = _top_positioning_payload(report)
    assert payload is not None
    assert payload["angle_key"] == "zahlen_beweis"
    assert payload["angle_strength"] == 80
    assert "Beweisführung" in payload["angle_claim"]
    assert "30 Tage" in payload["angle_evidence"]


def test_top_positioning_payload_carries_pitch_and_niche():
    report = _positioning(
        pitch="Dieses Buch liefert ein Liquiditäts-Playbook für CFOs.",
        niche_label="Finanzen / CFO / Controlling",
        niche_confidence=92,
        audience="CFOs in KMU",
    )
    payload = _top_positioning_payload(report)
    assert payload is not None
    assert payload["pitch"].startswith("Dieses Buch liefert")
    assert payload["niche_label"] == "Finanzen / CFO / Controlling"
    assert payload["niche_confidence"] == 92
    assert payload["audience"] == "CFOs in KMU"


def test_top_positioning_payload_is_immutable_against_caller_mutation():
    """Mutating the returned dict must not affect the source report."""
    report = _positioning()
    payload = _top_positioning_payload(report)
    assert payload is not None
    payload["angle_claim"] = "mutated"
    payload["pitch"] = "mutated"
    # source angle is frozen and still carries the original claim
    assert report.unique_angles[0].claim != "mutated"
    assert report.positioning_pitch != "mutated"


# ─── _top_persona_payload ───────────────────────────────────────────────


def _persona(
    *,
    label: str = "Die operative CFO",
    age_range: str = "40–55",
    job: str = "CFO in einem KMU",
    problem: str = "Liquidität, Forecast, Reporting — alles gleichzeitig.",
    buying_motive: str = "Sucht ein Praxis-Playbook mit Checklisten.",
    anchor_quote: str = "liquiditaet cfo playbook",
) -> BuyerPersona:
    return BuyerPersona(
        label=label,
        age_range=age_range,
        job=job,
        problem=problem,
        buying_motive=buying_motive,
        anchor_quote=anchor_quote,
    )


def _persona_report(
    *,
    personas: list[BuyerPersona] | None = None,
    niche_label: str = "Finanzen / CFO / Controlling",
    niche_confidence: int = 82,
) -> PersonaReport:
    if personas is None:
        personas = [
            _persona(),
            _persona(label="Der ambitionierte Controller", age_range="30–42"),
            _persona(label="Die selbstständige Beraterin", age_range="35–50"),
        ]
    return PersonaReport(
        niche_key="finanzen_und_cfo",
        niche_label=niche_label,
        niche_confidence=niche_confidence,
        audience="CFOs in KMU",
        subject="ein Liquiditäts-Playbook",
        personas=personas,
        anchors=["cfo", "liquiditaet"],
        signal_flags=[],
    )


def test_top_persona_payload_returns_none_when_report_missing():
    assert _top_persona_payload(None) is None


def test_top_persona_payload_returns_none_when_no_personas():
    report = _persona_report(personas=[])
    assert _top_persona_payload(report) is None


def test_top_persona_payload_picks_first_persona():
    """Persona #1 is by convention the most-likely buyer — it must win."""
    report = _persona_report()
    payload = _top_persona_payload(report)
    assert payload is not None
    assert payload["label"] == "Die operative CFO"
    assert payload["age_range"] == "40–55"
    assert "CFO" in payload["job"]
    assert "Liquidität" in payload["problem"]
    assert "Praxis-Playbook" in payload["buying_motive"]
    assert payload["anchor_quote"] == "liquiditaet cfo playbook"


def test_top_persona_payload_carries_niche_label_and_confidence():
    report = _persona_report(niche_label="KI / Künstliche Intelligenz", niche_confidence=44)
    payload = _top_persona_payload(report)
    assert payload is not None
    assert payload["niche_label"] == "KI / Künstliche Intelligenz"
    assert payload["niche_confidence"] == 44


def test_top_persona_payload_is_immutable_against_caller_mutation():
    """Mutating the returned dict must not affect the source persona."""
    report = _persona_report()
    payload = _top_persona_payload(report)
    assert payload is not None
    payload["label"] = "mutated"
    payload["problem"] = "mutated"
    # source persona is frozen — the original fields remain intact
    assert report.personas[0].label != "mutated"
    assert report.personas[0].problem != "mutated"


def test_top_persona_payload_returns_first_persona_even_with_one_only():
    """If the report carries a single persona we still surface it."""
    report = _persona_report(personas=[_persona(label="Solo")])
    payload = _top_persona_payload(report)
    assert payload is not None
    assert payload["label"] == "Solo"


# ─── _top_arc_payload ──────────────────────────────────────────────────


def _arc_json(
    *,
    arc_score: int = 70,
    status: str = "REVIEW",
    fixes: list[str] | None = None,
    inversions: list[list[int]] | None = None,
    missing_phases: list[str] | None = None,
) -> dict:
    return {
        "arc_score": arc_score,
        "status": status,
        "fixes": list(fixes if fixes is not None else ["Top fix line."]),
        "inversions": list(inversions if inversions is not None else []),
        "missing_phases": list(missing_phases if missing_phases is not None else []),
    }


def test_top_arc_payload_returns_none_when_no_report():
    assert _top_arc_payload(None) is None
    assert _top_arc_payload({}) is None


def test_top_arc_payload_returns_none_when_no_fixes():
    """A clean arc (canonical order, all phases) has nothing to surface."""
    payload = _top_arc_payload(_arc_json(arc_score=100, status="READY", fixes=[]))
    assert payload is None


def test_top_arc_payload_returns_none_when_top_fix_is_blank():
    """A blank or whitespace-only fix must not produce a noisy section."""
    payload = _top_arc_payload(_arc_json(fixes=["   "]))
    assert payload is None


def test_top_arc_payload_picks_first_fix_with_counts():
    arc = _arc_json(
        arc_score=58,
        status="FIX",
        fixes=[
            "Kapitel 3 vor Kapitel 2 ziehen — LÖSUNG kommt vor BEWEIS.",
            "Es fehlt ein klares Problem-Kapitel.",
        ],
        inversions=[[3, 2], [4, 2]],
        missing_phases=["PROBLEM"],
    )
    payload = _top_arc_payload(arc)
    assert payload is not None
    assert payload["arc_score"] == 58
    assert payload["status"] == "FIX"
    assert payload["top_fix"].startswith("Kapitel 3 vor Kapitel 2")
    assert payload["inversion_count"] == 2
    assert payload["missing_count"] == 1


def test_top_arc_payload_strips_whitespace_from_top_fix():
    arc = _arc_json(fixes=["  Es fehlt ein Beweis-Kapitel.  "])
    payload = _top_arc_payload(arc)
    assert payload is not None
    assert payload["top_fix"] == "Es fehlt ein Beweis-Kapitel."


def test_top_arc_payload_tolerates_missing_optional_fields():
    """Partial dicts should not crash — counts default to 0."""
    payload = _top_arc_payload({"fixes": ["Sorting fix"]})
    assert payload is not None
    assert payload["top_fix"] == "Sorting fix"
    assert payload["arc_score"] == 0
    assert payload["status"] == ""
    assert payload["inversion_count"] == 0
    assert payload["missing_count"] == 0


def test_top_arc_payload_is_immutable_against_caller_mutation():
    """Mutating the returned dict must not affect a later call's behaviour."""
    arc = _arc_json(fixes=["First"], inversions=[[1, 2]])
    payload = _top_arc_payload(arc)
    assert payload is not None
    payload["top_fix"] = "TAMPERED"
    payload["inversion_count"] = 999
    fresh = _top_arc_payload(arc)
    assert fresh is not None
    assert fresh["top_fix"] == "First"
    assert fresh["inversion_count"] == 1


# ─── _top_chapter_balance_payload ──────────────────────────────────────


def _outlier(
    *,
    kind: str,
    index: int,
    word_count: int,
    median: int,
    title: str = "Ein Kapitel",
    fix: str = "Default fix",
    ratio: float | None = None,
) -> dict:
    return {
        "kind": kind,
        "index": index,
        "title": title,
        "word_count": word_count,
        "median": median,
        "ratio": ratio if ratio is not None else round(word_count / median, 1),
        "fix": fix,
    }


def _chapter_json_with_balance(
    *,
    oversized: list[dict] | None = None,
    undersized: list[dict] | None = None,
    median: int = 1000,
) -> dict:
    return {
        "chapters": [],
        "average_score": 0,
        "weakest_chapter_index": None,
        "fixes": [],
        "balance": {
            "median_word_count": median,
            "oversized": list(oversized or []),
            "undersized": list(undersized or []),
        },
    }


def test_top_chapter_balance_payload_returns_none_when_no_report():
    assert _top_chapter_balance_payload(None) is None
    assert _top_chapter_balance_payload({}) is None


def test_top_chapter_balance_payload_returns_none_when_no_balance_key():
    assert _top_chapter_balance_payload({"chapters": []}) is None


def test_top_chapter_balance_payload_returns_none_when_both_lists_empty():
    payload = _top_chapter_balance_payload(_chapter_json_with_balance())
    assert payload is None


def test_top_chapter_balance_payload_picks_top_oversized_when_only_oversized():
    chapter_json = _chapter_json_with_balance(
        oversized=[
            _outlier(
                kind="oversized",
                index=4,
                word_count=5200,
                median=1000,
                title="Die Methode",
                fix="Kapitel 4 splitten.",
            ),
        ],
    )
    payload = _top_chapter_balance_payload(chapter_json)
    assert payload is not None
    assert payload["kind"] == "oversized"
    assert payload["index"] == 4
    assert payload["title"] == "Die Methode"
    assert payload["word_count"] == 5200
    assert payload["median"] == 1000
    assert payload["ratio"] == 5.2
    assert payload["fix"] == "Kapitel 4 splitten."


def test_top_chapter_balance_payload_picks_top_undersized_when_only_undersized():
    chapter_json = _chapter_json_with_balance(
        undersized=[
            _outlier(
                kind="undersized",
                index=2,
                word_count=120,
                median=1000,
                fix="Kapitel 2 mergen.",
            ),
        ],
    )
    payload = _top_chapter_balance_payload(chapter_json)
    assert payload is not None
    assert payload["kind"] == "undersized"
    assert payload["index"] == 2
    assert payload["ratio"] == 0.1


def test_top_chapter_balance_payload_picks_larger_deviation_when_both_present():
    """Oversized 4.5× (dev 3.5) beats undersized 0.2× (dev 0.8) on deviation."""
    chapter_json = _chapter_json_with_balance(
        oversized=[_outlier(kind="oversized", index=3, word_count=4500, median=1000)],
        undersized=[_outlier(kind="undersized", index=5, word_count=200, median=1000)],
    )
    payload = _top_chapter_balance_payload(chapter_json)
    assert payload is not None
    assert payload["kind"] == "oversized"
    assert payload["index"] == 3


def test_top_chapter_balance_payload_prefers_oversized_on_deviation_tie():
    """When both sides deviate equally from 1.0, oversized wins the tie-break."""
    chapter_json = _chapter_json_with_balance(
        oversized=[
            _outlier(
                kind="oversized",
                index=2,
                word_count=2000,
                median=1000,
                ratio=2.0,
            ),
        ],
        undersized=[
            _outlier(
                kind="undersized",
                index=7,
                word_count=0,
                median=1000,
                ratio=0.0,
            ),
        ],
    )
    payload = _top_chapter_balance_payload(chapter_json)
    assert payload is not None
    assert payload["kind"] == "oversized"
    assert payload["index"] == 2


def test_top_chapter_balance_payload_returns_none_when_fix_blank():
    """A whitespace-only fix must not produce a noisy beginner_summary block."""
    chapter_json = _chapter_json_with_balance(
        oversized=[
            _outlier(
                kind="oversized",
                index=1,
                word_count=4000,
                median=1000,
                fix="   ",
            ),
        ],
    )
    payload = _top_chapter_balance_payload(chapter_json)
    assert payload is None


def test_top_chapter_balance_payload_is_immutable_against_caller_mutation():
    chapter_json = _chapter_json_with_balance(
        oversized=[
            _outlier(
                kind="oversized",
                index=4,
                word_count=5000,
                median=1000,
                fix="Original fix",
            ),
        ],
    )
    payload = _top_chapter_balance_payload(chapter_json)
    assert payload is not None
    payload["fix"] = "TAMPERED"
    payload["index"] = 999
    fresh = _top_chapter_balance_payload(chapter_json)
    assert fresh is not None
    assert fresh["fix"] == "Original fix"
    assert fresh["index"] == 4
