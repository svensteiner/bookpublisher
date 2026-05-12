from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modules.discovery import BookProject
from modules.industrial import (
    GATE_DISPLAY_LABELS,
    SCORE_BADGE_FIX,
    SCORE_BADGE_READY,
    SCORE_BADGE_REVIEW,
    Gate,
    _asset_gate,
    _kindle_gate,
    _metadata_gate,
    _production_gate,
    _sellability_gate,
    _status,
    build_industrial_qa,
    render_beginner_summary,
    render_industrial_qa_markdown,
    score_badge,
)


# ─── Helpers ──────────────────────────────────────────────────────────

def _project(**kwargs) -> BookProject:
    defaults = dict(
        project_id="test-book",
        root=Path("/tmp/test-book"),
        manuscript=Path("/tmp/test-book/manuscript.docx"),
        cover=Path("/tmp/test-book/cover.png"),
        title="Mein Sachbuch für Gründer",
        subtitle="Praktische Methoden für Selbstständige in 30 Tagen",
        author="Maria Mustermann",
        amazon_description=(
            "Dieses Buch richtet sich an Gründer und Selbstständige, die ihre Prozesse "
            "in 30 Tagen praktisch verbessern wollen. Mit konkreten Schritt-für-Schritt-Anleitungen, "
            "echten Fallstudien und Checklisten. Kein Hype, keine Theorie — direkt anwendbar.\n"
            "- Methode 1\n- Methode 2\n- Methode 3\n- Methode 4\n- Methode 5\n"
        ),
    )
    defaults.update(kwargs)
    return BookProject(**defaults)


def _docx_profile(**kwargs) -> dict:
    defaults = dict(
        available=True,
        word_count=18000,
        heading_count=12,
        average_body_paragraph_words=55,
        long_paragraph_ratio=0.08,
        table_count=0,
        toc_detected=True,
        sample_heading_count=3,
        sample_sentence_count=45,
        inline_shape_count=0,
    )
    defaults.update(kwargs)
    return defaults


# ─── _status ──────────────────────────────────────────────────────────

def test_status_ready():
    assert _status(90) == "READY"
    assert _status(85) == "READY"


def test_status_review():
    assert _status(70) == "REVIEW"
    assert _status(65) == "REVIEW"


def test_status_fix_low_score():
    assert _status(50) == "FIX"


def test_status_blocking_always_fix():
    assert _status(95, blocking=True) == "FIX"


# ─── _asset_gate ──────────────────────────────────────────────────────

def test_asset_gate_all_present():
    gate = _asset_gate(_project())
    assert gate.score == 100
    assert gate.status == "READY"
    assert not gate.fixes


def test_asset_gate_missing_manuscript_blocks():
    gate = _asset_gate(_project(manuscript=None))
    assert gate.status == "FIX"
    assert gate.score < 100
    assert any("manuscript" in f.lower() for f in gate.fixes)


def test_asset_gate_missing_cover_blocks():
    gate = _asset_gate(_project(cover=None))
    assert gate.status == "FIX"


def test_asset_gate_missing_description_reduces_score():
    gate = _asset_gate(_project(amazon_description=None))
    assert gate.score < 100
    assert any("amazon_description" in f.lower() for f in gate.fixes)


# ─── _metadata_gate ───────────────────────────────────────────────────

def test_metadata_gate_good_metadata():
    notes = "## Amazon Keywords\nGründer, Methode, Praxis, Selbstständige, Schritt, System, Anleitung\n## Kategorie\nSachbuch"
    gate = _metadata_gate(_project(), notes)
    assert gate.score >= 70
    assert gate.status in ("READY", "REVIEW")


def test_metadata_gate_short_title_penalised():
    gate = _metadata_gate(_project(title="Hi"), "")
    assert gate.score < 100
    assert any("title" in f.lower() for f in gate.fixes)


def test_metadata_gate_no_keywords_penalised():
    gate = _metadata_gate(_project(), "")
    assert any("keyword" in f.lower() for f in gate.fixes)


# ─── _kindle_gate ─────────────────────────────────────────────────────

def test_kindle_gate_healthy_profile():
    gate = _kindle_gate(_docx_profile())
    assert gate.score >= 80
    assert gate.status in ("READY", "REVIEW")


def test_kindle_gate_too_few_headings():
    gate = _kindle_gate(_docx_profile(heading_count=3))
    assert gate.score < 100
    assert any("heading" in f.lower() for f in gate.fixes)


def test_kindle_gate_low_word_count():
    gate = _kindle_gate(_docx_profile(word_count=8000))
    assert gate.score < 100


def test_kindle_gate_unavailable_profile():
    gate = _kindle_gate({"available": False})
    assert gate.status == "FIX"
    assert gate.score == 0


# ─── _sellability_gate ────────────────────────────────────────────────

def test_sellability_gate_generic_nonfiction_passes():
    """A generic nonfiction book with standard signals should not be penalised."""
    profile = _docx_profile()
    notes = "Kategorie: Sachbuch für Selbstständige"
    gate = _sellability_gate(_project(), profile, notes)
    assert gate.score >= 60, f"Score too low for generic nonfiction: {gate.score}, fixes: {gate.fixes}"


def test_sellability_gate_no_sven_specific_terms_required():
    """Proof patterns must NOT require Sven-specific terms like 'tradingbot' or 'prompt-leak'."""
    profile = _docx_profile()
    # Description with generic proof (numbers + units) but no Sven-specific phrases
    project = _project(
        title="Kochen in 20 Minuten",
        subtitle="50 Rezepte für Berufstätige",
        amazon_description=(
            "50 einfache Rezepte für Berufstätige und Einsteiger. "
            "In 20 Minuten auf dem Tisch — ohne Vorkenntnisse. "
            "Schritt-für-Schritt-Anleitungen, Einkaufslisten und Meal-Prep-Tipps.\n"
            "- Rezept 1\n- Rezept 2\n- Rezept 3\n- Rezept 4\n"
        ),
    )
    gate = _sellability_gate(project, profile, "Kategorie: Kochbuch")
    # proof_or_specificity should fire on "50 Rezepte", "20 Minuten"
    proof_finding = any("proof" in f.lower() for f in gate.findings)
    assert proof_finding, f"Generic proof numbers not recognised. Findings: {gate.findings}"


def test_sellability_gate_missing_reader_fix_suggested():
    project = _project(title="Ein Buch", subtitle="Ohne Zielgruppe", amazon_description="Kurze Beschreibung.")
    profile = _docx_profile(sample_heading_count=0, sample_sentence_count=10)
    gate = _sellability_gate(project, profile, "")
    assert gate.score < 100
    assert gate.fixes


# ─── build_industrial_qa ──────────────────────────────────────────────

def test_build_industrial_qa_structure():
    """build_industrial_qa must return all required keys."""
    # manuscript=None and cover=None so no filesystem access is attempted
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    assert "decision" in result
    assert "industrial_score" in result
    assert "investor_grade" in result
    assert "gates" in result
    assert len(result["gates"]) == 5
    assert result["decision"] in ("GO", "GO_AFTER_FIXES", "HOLD")


def test_build_industrial_qa_score_range():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    assert 0 <= result["industrial_score"] <= 100
    assert 0.0 <= result["investor_grade"] <= 10.0


# ─── render_beginner_summary ──────────────────────────────────────────

def test_render_beginner_summary_contains_ampel():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(_project(manuscript=None, cover=None), result)
    assert "Ampel" in summary
    assert "Buch" in summary
    assert "Score" in summary


# ─── score_badge — einheitliche Score-Darstellung ─────────────────────


def test_score_badge_ready_at_threshold_85():
    badge, status = score_badge(85)
    assert badge == SCORE_BADGE_READY
    assert status == "READY"


def test_score_badge_review_between_65_and_84():
    for score in (65, 70, 84):
        badge, status = score_badge(score)
        assert badge == SCORE_BADGE_REVIEW
        assert status == "REVIEW"


def test_score_badge_fix_below_65():
    for score in (0, 40, 64):
        badge, status = score_badge(score)
        assert badge == SCORE_BADGE_FIX
        assert status == "FIX"


def test_score_badge_blocking_forces_fix_regardless_of_score():
    badge, status = score_badge(95, blocking=True)
    assert badge == SCORE_BADGE_FIX
    assert status == "FIX"


def test_render_beginner_summary_contains_gate_overview_with_badges():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(_project(manuscript=None, cover=None), result)
    assert "Gate-Übersicht" in summary
    assert "Skala: 🟢" in summary
    # At least one badge from the unified set must appear.
    assert any(b in summary for b in (SCORE_BADGE_READY, SCORE_BADGE_REVIEW, SCORE_BADGE_FIX))


def test_render_beginner_summary_uses_german_gate_labels():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(_project(manuscript=None, cover=None), result)
    # At least the asset gate (always reported) should appear with its German label.
    assert GATE_DISPLAY_LABELS["asset_completeness"] in summary


def test_render_industrial_qa_markdown_prefixes_each_gate_with_badge():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    markdown = render_industrial_qa_markdown(result)
    badge_chars = (SCORE_BADGE_READY, SCORE_BADGE_REVIEW, SCORE_BADGE_FIX)
    for gate in result["gates"]:
        header_present = any(
            f"### {b} {gate['name']}" in markdown for b in badge_chars
        )
        assert header_present, f"No badge for gate {gate['name']} in markdown"


def test_overall_score_line_has_unified_badge():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(_project(manuscript=None, cover=None), result)
    badge_chars = (SCORE_BADGE_READY, SCORE_BADGE_REVIEW, SCORE_BADGE_FIX)
    score_line = next(line for line in summary.splitlines() if line.startswith("Score:"))
    assert any(b in score_line for b in badge_chars)


def test_render_beginner_summary_weakest_chapters_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    weakest = [
        {
            "index": 3,
            "title": "Kapitelchen Drei",
            "overall": 42,
            "fix": "Verankere Kapitel mit Beweis.",
            "status": "FIX",
        },
        {
            "index": 5,
            "title": "Kapitel Fünf",
            "overall": 70,
            "fix": "Klares Versprechen oben einfügen.",
            "status": "REVIEW",
        },
    ]
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, weakest_chapters=weakest
    )
    assert "## Schwächste Kapitel" in summary
    assert "Kapitel 3 — Kapitelchen Drei" in summary
    assert "42/100" in summary
    assert "Verankere Kapitel mit Beweis." in summary
    assert "Kapitel 5 — Kapitel Fünf" in summary


def test_render_beginner_summary_weakest_chapters_section_absent_when_empty():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, weakest_chapters=None
    )
    assert "## Schwächste Kapitel" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, weakest_chapters=[]
    )
    assert "## Schwächste Kapitel" not in summary_empty


def test_render_beginner_summary_weakest_chapters_handles_missing_fields():
    """Robust against partial dicts — no crash, sensible fallback text."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    weakest = [{"index": 1}]  # no title, no overall, no fix
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, weakest_chapters=weakest
    )
    assert "## Schwächste Kapitel" in summary
    assert "Kapitel 1" in summary
    assert "0/100" in summary


def test_render_beginner_summary_weakest_sample_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    weakest = {
        "index": 2,
        "label": "Eroeffnung",
        "overall": 48,
        "status": "FIX",
        "risk": "ABBRUCH-RISIKO",
        "fix": "Setze einen Hook-Satz mit konkreter Zahl an den Anfang.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, weakest_sample=weakest
    )
    assert "## Schwächster Sample-Abschnitt" in summary
    assert "Abschnitt 2 — Eroeffnung" in summary
    assert "48/100" in summary
    assert "ABBRUCH-RISIKO" in summary
    assert "Setze einen Hook-Satz" in summary


def test_render_beginner_summary_weakest_sample_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, weakest_sample=None
    )
    assert "## Schwächster Sample-Abschnitt" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, weakest_sample={}
    )
    assert "## Schwächster Sample-Abschnitt" not in summary_empty


def test_render_beginner_summary_weakest_sample_handles_missing_fields():
    """Robust against partial payloads — no crash, sensible fallback."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    weakest = {"index": 1}
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, weakest_sample=weakest
    )
    assert "## Schwächster Sample-Abschnitt" in summary
    assert "Abschnitt 1" in summary
    assert "0/100" in summary
    assert "Kein Fix-Vorschlag" in summary


def test_render_beginner_summary_top_rewrite_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_rewrite = {
        "field": "title",
        "text": "Sachbuch: Was wirklich funktioniert",
        "keyword_score": 67,
        "char_count": 34,
        "motivation": "Buyer-Click: Direkte Substanz-Versprechen-Formel.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_rewrite=top_rewrite
    )
    assert "## Top-Rewrite-Pick" in summary
    assert "Sachbuch: Was wirklich funktioniert" in summary
    assert "67/100" in summary
    assert "Buyer-Click" in summary
    assert "Titel" in summary
    assert "`rewrite_suggestions.md`" in summary


def test_render_beginner_summary_top_rewrite_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_rewrite=None
    )
    assert "## Top-Rewrite-Pick" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_rewrite={}
    )
    assert "## Top-Rewrite-Pick" not in summary_empty


def test_render_beginner_summary_top_rewrite_section_absent_when_text_empty():
    """An option without text content must not produce an empty quote block."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_rewrite = {
        "field": "title",
        "text": "   ",
        "keyword_score": 99,
        "char_count": 0,
        "motivation": "Doesn't matter",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_rewrite=top_rewrite
    )
    assert "## Top-Rewrite-Pick" not in summary


def test_render_beginner_summary_top_rewrite_handles_missing_motivation():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_rewrite = {
        "field": "description_lead",
        "text": "Dieser Lead überzeugt.",
        "keyword_score": 33,
        "char_count": 22,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_rewrite=top_rewrite
    )
    assert "## Top-Rewrite-Pick" in summary
    assert "Dieser Lead überzeugt." in summary
    assert "Beschreibungs-Einstieg" in summary
    # No "Warum: " line should appear when no motivation is provided.
    assert "Warum:" not in summary


def test_render_beginner_summary_round_delta_highlight_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {
        "resolved_count": 3,
        "persistent_count": 1,
        "new_count": 2,
        "score_delta": 15,
        "decision_changed": True,
        "previous_decision": "GO_AFTER_FIXES",
        "current_decision": "GO",
        "top_resolved": ["Cover-Format korrigiert.", "Beschreibung gekürzt."],
        "top_persistent": ["7 Amazon-Keywords festlegen."],
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, round_delta_highlight=highlight
    )
    assert "## Runden-Fortschritt" in summary
    assert "3 Fix(es) umgesetzt" in summary
    assert "1 Fix(es) weiterhin offen" in summary
    assert "2 neue(r) Fix(es)" in summary
    assert "+15 Punkte" in summary
    assert "GO_AFTER_FIXES → **GO**" in summary
    assert "Cover-Format korrigiert." in summary
    assert "7 Amazon-Keywords festlegen." in summary
    assert "`round_delta.md`" in summary


def test_render_beginner_summary_round_delta_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, round_delta_highlight=None
    )
    assert "## Runden-Fortschritt" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, round_delta_highlight={}
    )
    assert "## Runden-Fortschritt" not in summary_empty


def test_render_beginner_summary_round_delta_handles_missing_fields():
    """Robust against partial payloads — no crash, sensible defaults."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {"resolved_count": 0}  # minimal
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, round_delta_highlight=highlight
    )
    assert "## Runden-Fortschritt" in summary
    assert "0 Fix(es) umgesetzt" in summary
    assert "0 Fix(es) weiterhin offen" in summary
    # decision_changed False ⇒ no Entscheidung line
    assert "🔁 Entscheidung" not in summary
    # No score_delta ⇒ "kein Vergleich möglich"
    assert "kein Vergleich möglich" in summary


def test_render_beginner_summary_round_delta_negative_score_uses_fix_badge():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {
        "resolved_count": 0,
        "persistent_count": 3,
        "new_count": 1,
        "score_delta": -10,
        "decision_changed": False,
        "previous_decision": "GO_AFTER_FIXES",
        "current_decision": "GO_AFTER_FIXES",
        "top_resolved": [],
        "top_persistent": [],
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, round_delta_highlight=highlight
    )
    assert "## Runden-Fortschritt" in summary
    assert "-10 Punkte" in summary
    # The negative-score badge line should be present
    assert SCORE_BADGE_FIX in summary
    # No "Erledigt seit der Vorrunde" block when top_resolved is empty
    assert "Erledigt seit der Vorrunde" not in summary


def test_render_beginner_summary_round_delta_zero_score_delta():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {
        "resolved_count": 1,
        "persistent_count": 1,
        "new_count": 0,
        "score_delta": 0,
        "decision_changed": False,
        "previous_decision": "GO_AFTER_FIXES",
        "current_decision": "GO_AFTER_FIXES",
        "top_resolved": ["Eine Sache erledigt."],
        "top_persistent": ["Eine bleibt offen."],
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, round_delta_highlight=highlight
    )
    assert "## Runden-Fortschritt" in summary
    assert "±0 Punkte" in summary
    assert "Erledigt seit der Vorrunde" in summary
    assert "Weiterhin offen — jetzt anpacken" in summary


def test_render_beginner_summary_score_history_highlight_rising_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {
        "series": [
            {"timestamp": "2025-05-10", "score": 70, "delta": None},
            {"timestamp": "2025-05-11", "score": 78, "delta": 8},
            {"timestamp": "2025-05-12", "score": 85, "delta": 7},
        ],
        "first_score": 70,
        "latest_score": 85,
        "delta_total": 15,
        "trend": "rising",
        "entry_count": 3,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        score_history_highlight=highlight,
    )
    assert "## Score-Verlauf" in summary
    assert "70/100" in summary
    assert "85/100" in summary
    assert "(+8)" in summary
    assert "(+7)" in summary
    assert "+15 Punkte" in summary
    assert "über 3 Runden" in summary
    assert "steigt" in summary
    assert "`score_history.md`" in summary
    # Rising trend uses the READY badge in the trend summary
    assert SCORE_BADGE_READY in summary


def test_render_beginner_summary_score_history_falling_uses_fix_badge():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {
        "series": [
            {"timestamp": "2025-05-11", "score": 85, "delta": None},
            {"timestamp": "2025-05-12", "score": 70, "delta": -15},
        ],
        "first_score": 85,
        "latest_score": 70,
        "delta_total": -15,
        "trend": "falling",
        "entry_count": 2,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        score_history_highlight=highlight,
    )
    assert "## Score-Verlauf" in summary
    assert "-15 Punkte" in summary
    assert "sinkt" in summary
    assert "(-15)" in summary
    assert SCORE_BADGE_FIX in summary


def test_render_beginner_summary_score_history_stable_uses_review_badge():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {
        "series": [
            {"timestamp": "2025-05-11", "score": 80, "delta": None},
            {"timestamp": "2025-05-12", "score": 80, "delta": 0},
        ],
        "first_score": 80,
        "latest_score": 80,
        "delta_total": 0,
        "trend": "stable",
        "entry_count": 2,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        score_history_highlight=highlight,
    )
    assert "## Score-Verlauf" in summary
    assert "±0 Punkte" in summary
    assert "stabil" in summary
    # Per-entry zero delta also rendered with ±0 marker
    assert "(±0)" in summary


def test_render_beginner_summary_score_history_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        score_history_highlight=None,
    )
    assert "## Score-Verlauf" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        score_history_highlight={},
    )
    assert "## Score-Verlauf" not in summary_empty


def test_render_beginner_summary_score_history_section_absent_when_single_point():
    """A series with one entry has no trend — section is omitted."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {
        "series": [{"timestamp": "2025-05-10", "score": 70, "delta": None}],
        "first_score": 70,
        "latest_score": 70,
        "delta_total": 0,
        "trend": "stable",
        "entry_count": 1,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        score_history_highlight=highlight,
    )
    assert "## Score-Verlauf" not in summary


def test_render_beginner_summary_score_history_tolerates_partial_entries():
    """Missing/invalid score fields should not crash the renderer."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    highlight = {
        "series": [
            {"timestamp": "2025-05-10"},
            {"timestamp": "2025-05-12", "score": 85, "delta": 15},
        ],
        "first_score": 0,
        "latest_score": 85,
        "delta_total": 85,
        "trend": "rising",
        "entry_count": 2,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        score_history_highlight=highlight,
    )
    assert "## Score-Verlauf" in summary
    assert "0/100" in summary
    assert "85/100" in summary


def test_render_beginner_summary_top_kdp_keywords_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_keywords = [
        {
            "text": "selbststaendigkeit ratgeber",
            "char_count": 27,
            "source": "subject_format",
            "rationale": "Subject + Format — typischer KDP-Suchpfad.",
        },
        {
            "text": "ratgeber fuer gruender",
            "char_count": 22,
            "source": "audience_format",
            "rationale": "Format + Zielgruppe — Long-Tail-Treffer.",
        },
        {
            "text": "methode checkliste",
            "char_count": 18,
            "source": "anchor_pair",
            "rationale": "Anker-Keyword-Paar — organische Suche.",
        },
    ]
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_kdp_keywords=top_keywords
    )
    assert "## KDP-Keywords (Top-3)" in summary
    assert "`selbststaendigkeit ratgeber`" in summary
    assert "`ratgeber fuer gruender`" in summary
    assert "`methode checkliste`" in summary
    assert "Zeichen: 27/50" in summary
    assert "Long-Tail-Treffer" in summary
    assert "`kdp_keywords.md`" in summary


def test_render_beginner_summary_top_kdp_keywords_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary_none = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_kdp_keywords=None
    )
    assert "## KDP-Keywords (Top-3)" not in summary_none
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_kdp_keywords=[]
    )
    assert "## KDP-Keywords (Top-3)" not in summary_empty


def test_render_beginner_summary_top_kdp_keywords_handles_partial_dicts():
    """Missing rationale must not yield an empty 'Warum:' line."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_keywords = [
        {"text": "ratgeber praxis", "char_count": 15, "source": "fallback"},
    ]
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_kdp_keywords=top_keywords
    )
    assert "## KDP-Keywords (Top-3)" in summary
    assert "`ratgeber praxis`" in summary
    assert "Warum:" not in summary


def test_render_beginner_summary_top_kdp_keywords_skips_empty_text_entries():
    """A whitespace-only ``text`` entry must not produce a backtick-only line."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_keywords = [
        {"text": "   ", "char_count": 0, "source": "fallback", "rationale": "skip"},
        {"text": "buch ratgeber", "char_count": 13, "source": "fallback", "rationale": "ok"},
    ]
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_kdp_keywords=top_keywords
    )
    assert "## KDP-Keywords (Top-3)" in summary
    assert "`buch ratgeber`" in summary
    # Empty text must not appear as a numbered item.
    assert "1. ``" not in summary
    assert "2. ``" not in summary


def test_render_beginner_summary_handles_empty_gate_list():
    minimal_report = {
        "decision": "GO_AFTER_FIXES",
        "industrial_score": 70,
        "gates": [],
        "all_required_fixes": ["Fix something"],
        "docx_profile": {},
    }
    summary = render_beginner_summary(_project(manuscript=None, cover=None), minimal_report)
    # Without gates we still get the structure, just no Gate-Übersicht.
    assert "Gate-Übersicht" not in summary
    assert "Score:" in summary
