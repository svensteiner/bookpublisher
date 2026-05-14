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


def test_render_industrial_qa_markdown_includes_gate_overview_table():
    """Power-user table: Gate | Badge | Score | Status before the per-gate sections."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    markdown = render_industrial_qa_markdown(result)

    assert "## Gate-Übersicht" in markdown
    assert "| Gate | Badge | Score | Status |" in markdown
    assert "|---|---|---|---|" in markdown
    # Skala line surfaces the unified threshold scheme
    assert "Skala: 🟢 ≥85 · 🟡 65–84 · 🔴 <65" in markdown


def test_gate_overview_table_appears_before_per_gate_sections():
    """Power users scan the table first; ## Gates header must come AFTER it."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    markdown = render_industrial_qa_markdown(result)

    overview_idx = markdown.index("## Gate-Übersicht")
    gates_idx = markdown.index("## Gates")
    assert overview_idx < gates_idx


def test_gate_overview_table_contains_one_row_per_gate():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    markdown = render_industrial_qa_markdown(result)

    # Each gate should appear as a row in the overview table — by the
    # German display label (so the table reads cleanly for the author).
    for gate in result["gates"]:
        name = gate["name"]
        label = {
            "asset_completeness": "Dateien vollständig",
            "metadata_and_storefront": "Amazon-Metadaten",
            "kindle_ebook_readiness": "Kindle-Lesbarkeit",
            "production_package": "Produktionspaket",
            "amazon_sellability": "Amazon-Verkaufbarkeit",
        }.get(name, name.replace("_", " ").title())
        # Look for a pipe-separated row that contains the label
        row_present = any(
            line.startswith("| ") and label in line and "/100" in line
            for line in markdown.splitlines()
        )
        assert row_present, f"Gate {name} ({label}) missing from overview table"


def test_gate_overview_table_uses_score_badge_per_row():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    markdown = render_industrial_qa_markdown(result)
    badge_chars = (SCORE_BADGE_READY, SCORE_BADGE_REVIEW, SCORE_BADGE_FIX)

    # Find the overview table block and check that every row carries
    # a unified score-badge emoji
    lines = markdown.splitlines()
    in_table = False
    rows_seen = 0
    for line in lines:
        if line.startswith("| Gate | Badge |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|") or line.startswith("|---"):
                if line.startswith("|---"):
                    continue
                break
            rows_seen += 1
            assert any(b in line for b in badge_chars), f"Row missing badge: {line}"
    assert rows_seen >= 1


def test_gate_overview_table_omitted_when_no_gates():
    """Defensive: empty gates list must not produce a stub table."""
    from modules.industrial import _render_gate_overview_table

    assert _render_gate_overview_table({"gates": []}) == []
    assert _render_gate_overview_table({}) == []


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


# ─── Top-Positioning section ────────────────────────────────────────────


def test_render_beginner_summary_top_positioning_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_positioning = {
        "angle_key": "zahlen_beweis",
        "angle_claim": "Beweisführung mit Zahlen statt Behauptungen.",
        "angle_evidence": "Beschreibung enthält 30 Tage und 12 Kennzahlen.",
        "angle_strength": 80,
        "pitch": "Dieses Buch liefert ein Liquiditäts-Playbook für CFOs in KMU.",
        "niche_label": "Finanzen / CFO / Controlling",
        "niche_confidence": 92,
        "audience": "CFOs in mittelständischen Firmen",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning=top_positioning
    )
    assert "## Positionierung" in summary
    assert "Beweisführung mit Zahlen" in summary
    assert "Stärke: 80/100" in summary
    assert "Beschreibung enthält 30 Tage" in summary
    assert "Finanzen / CFO / Controlling" in summary
    assert "Konfidenz: 92/100" in summary
    assert "CFOs in mittelständischen Firmen" in summary
    assert "Dieses Buch liefert ein Liquiditäts-Playbook" in summary
    assert "`competitive_positioning.md`" in summary
    # Strength 80 must use the READY badge.
    assert SCORE_BADGE_READY in summary


def test_render_beginner_summary_top_positioning_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning=None
    )
    assert "## Positionierung" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning={}
    )
    assert "## Positionierung" not in summary_empty


def test_render_beginner_summary_top_positioning_section_absent_when_no_signal():
    """Empty pitch and empty claim must skip the section — no empty quote."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_positioning = {
        "angle_key": "kein_signal",
        "angle_claim": "",
        "angle_evidence": "",
        "angle_strength": 0,
        "pitch": "   ",
        "niche_label": "Allgemeines Sachbuch",
        "niche_confidence": 0,
        "audience": "",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning=top_positioning
    )
    assert "## Positionierung" not in summary


def test_render_beginner_summary_top_positioning_handles_partial_payload():
    """Missing optional fields must not crash the renderer."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_positioning = {
        "angle_claim": "Operator-Stimme statt Berater-Sicht.",
        "pitch": "Dieses Buch liefert eine Methode für Solopreneure.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning=top_positioning
    )
    assert "## Positionierung" in summary
    assert "Operator-Stimme" in summary
    # No niche line when label missing
    assert "Konfidenz" not in summary
    # No evidence line when evidence missing
    assert "Beleg:" not in summary
    # Strength 0 falls back to FIX badge — but section still rendered.
    assert "Stärke: 0/100" in summary


def test_render_beginner_summary_top_positioning_renders_additional_angles():
    """When ``additional_angles`` is set, the renderer must surface each
    secondary angle under the strongest one — same Beleg-line layout but
    with the ``Weiterer Angle`` label so the author can tell the
    hierarchy at a glance."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_positioning = {
        "angle_key": "zahlen_beweis",
        "angle_claim": "Beweisführung mit Zahlen statt Behauptungen.",
        "angle_evidence": "Beschreibung enthält 30 Tage und 12 Kennzahlen.",
        "angle_strength": 80,
        "additional_angles": [
            {
                "angle_key": "operator_stimme",
                "angle_claim": "Operator-Praxisstimme statt Berater-Sicht.",
                "angle_evidence": "Beschreibung nennt CFO-Begriffe.",
                "angle_strength": 63,
            },
            {
                "angle_key": "anti_hype",
                "angle_claim": "Anti-Hype: keine Buzzwords.",
                "angle_evidence": "Beschreibung ohne 'revolutionär'.",
                "angle_strength": 55,
            },
        ],
        "pitch": "Pitch.",
        "niche_label": "Finanzen / CFO / Controlling",
        "niche_confidence": 80,
        "audience": "CFOs",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning=top_positioning
    )
    assert "Stärkster Angle" in summary
    assert summary.count("**Weiterer Angle:**") == 2
    assert "Operator-Praxisstimme statt Berater-Sicht." in summary
    assert "Stärke: 63/100" in summary
    assert "CFO-Begriffe" in summary
    assert "Anti-Hype: keine Buzzwords." in summary
    assert "Stärke: 55/100" in summary


def test_render_beginner_summary_top_positioning_no_extras_when_list_empty():
    """Empty ``additional_angles`` must not produce a 'Weiterer Angle' line —
    keeps the default summary compact."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_positioning = {
        "angle_claim": "X.",
        "angle_strength": 80,
        "additional_angles": [],
        "pitch": "Pitch.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning=top_positioning
    )
    assert "Stärkster Angle" in summary
    assert "Weiterer Angle" not in summary


def test_render_beginner_summary_top_positioning_skips_extras_without_claim():
    """Whitespace/missing claim in an extra angle must not produce a
    stub bullet — guard against malformed payloads from older runs."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_positioning = {
        "angle_claim": "Top.",
        "angle_strength": 80,
        "additional_angles": [
            {"angle_claim": "   ", "angle_strength": 50},
            {"angle_claim": "Echter Zweit-Angle.", "angle_strength": 55},
        ],
        "pitch": "Pitch.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning=top_positioning
    )
    assert summary.count("**Weiterer Angle:**") == 1
    assert "Echter Zweit-Angle." in summary


def test_render_beginner_summary_top_persona_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_persona = {
        "label": "Die operative CFO",
        "age_range": "40–55",
        "job": "CFO oder kaufmännische Leiterin in einem KMU",
        "problem": "Liquidität, Forecast, Reporting — alles gleichzeitig.",
        "buying_motive": "Sucht ein Praxis-Playbook mit Checklisten.",
        "anchor_quote": "liquiditaet cfo playbook",
        "niche_label": "Finanzen / CFO / Controlling",
        "niche_confidence": 80,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_persona=top_persona
    )
    assert "## Top-Persona" in summary
    assert "Die operative CFO" in summary
    assert "40–55" in summary
    assert "kaufmännische Leiterin" in summary
    assert "Liquidität, Forecast" in summary
    assert "Praxis-Playbook" in summary
    assert "liquiditaet cfo playbook" in summary
    assert "`buyer_personas.md`" in summary


def test_render_beginner_summary_top_persona_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_persona=None
    )
    assert "## Top-Persona" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_persona={}
    )
    assert "## Top-Persona" not in summary_empty


def test_render_beginner_summary_top_persona_section_absent_when_no_content():
    """Both problem and buying_motive empty → no actionable signal → skip."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_persona = {
        "label": "Persona ohne Inhalt",
        "age_range": "30",
        "job": "Job",
        "problem": "   ",
        "buying_motive": "",
        "anchor_quote": "",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_persona=top_persona
    )
    assert "## Top-Persona" not in summary


def test_render_beginner_summary_top_persona_handles_partial_payload():
    """Missing optional fields must not crash the renderer."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_persona = {
        "label": "Solo",
        "problem": "Hat keine Zeit, jeden Tag neu zu starten.",
        "buying_motive": "Will eine schnelle Methode.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_persona=top_persona
    )
    assert "## Top-Persona" in summary
    assert "Solo" in summary
    assert "Hat keine Zeit" in summary
    # No "Alter:" line when age_range missing
    assert "Alter:" not in summary
    # No "Mögliche Suchanfrage:" line when anchor_quote missing
    assert "Mögliche Suchanfrage" not in summary


def test_render_beginner_summary_top_persona_falls_back_to_default_label():
    """Empty label still surfaces problem/motive with a generic headline."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_persona = {
        "label": "",
        "problem": "Pain X",
        "buying_motive": "Motive Y",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_persona=top_persona
    )
    assert "## Top-Persona" in summary
    assert "Persona 1" in summary
    assert "Pain X" in summary
    assert "Motive Y" in summary


def test_render_beginner_summary_persona_match_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    persona_match = {
        "overall_score": 78,
        "status": "READY",
        "description_present": True,
        "lead_lines_present": True,
        "total_personas": 3,
        "measurable_personas": 3,
        "weakest_label": "Die skeptische CFO",
        "weakest_score": 55,
        "weakest_missing": ("zahlen", "cases", "bilanz"),
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, persona_match=persona_match
    )
    assert "## Persona-Match" in summary
    assert "78/100" in summary
    assert "READY" in summary
    assert "Die skeptische CFO" in summary
    assert "55/100" in summary
    assert "zahlen" in summary
    assert "`buyer_personas.md`" in summary


def test_render_beginner_summary_persona_match_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, persona_match=None
    )
    assert "## Persona-Match" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, persona_match={}
    )
    assert "## Persona-Match" not in summary_empty


def test_render_beginner_summary_persona_match_section_absent_when_no_personas():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    persona_match = {
        "overall_score": 0,
        "status": "FIX",
        "description_present": True,
        "lead_lines_present": True,
        "total_personas": 0,
        "measurable_personas": 0,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, persona_match=persona_match
    )
    assert "## Persona-Match" not in summary


def test_render_beginner_summary_persona_match_shows_missing_description_hint():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    persona_match = {
        "overall_score": 0,
        "status": "FIX",
        "description_present": False,
        "lead_lines_present": False,
        "total_personas": 3,
        "measurable_personas": 3,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, persona_match=persona_match
    )
    assert "## Persona-Match" in summary
    assert "Keine Amazon-Beschreibung" in summary
    # No score number should be surfaced in the missing-description path
    assert "0/100" not in summary.split("## Persona-Match")[1].split("##")[0]


def test_render_beginner_summary_llm_fallback_notice_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    llm_fallback = {
        "fallback_used": True,
        "primary_model": "claude-sonnet-4-6",
        "fallback_model": "claude-haiku-4-5-20251001",
        "primary_calls": 0,
        "fallback_calls": 4,
        "total_calls": 4,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, llm_fallback=llm_fallback
    )
    assert "## ⚠️ Modell-Fallback aktiv" in summary
    assert "claude-sonnet-4-6" in summary
    assert "claude-haiku-4-5-20251001" in summary
    assert "4 via Fallback" in summary
    assert "weitere Runde" in summary


def test_render_beginner_summary_llm_fallback_notice_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, llm_fallback=None
    )
    assert "Modell-Fallback" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, llm_fallback={}
    )
    assert "Modell-Fallback" not in summary_empty


def test_render_beginner_summary_llm_fallback_notice_absent_when_not_used():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    llm_fallback = {
        "fallback_used": False,
        "primary_model": "claude-sonnet-4-6",
        "fallback_model": "claude-haiku-4-5-20251001",
        "primary_calls": 3,
        "fallback_calls": 0,
        "total_calls": 3,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, llm_fallback=llm_fallback
    )
    assert "Modell-Fallback" not in summary


def test_render_beginner_summary_llm_fallback_notice_absent_when_zero_fallback_calls():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    llm_fallback = {
        "fallback_used": True,  # set but counts say otherwise
        "primary_model": "claude-sonnet-4-6",
        "fallback_model": "claude-haiku-4-5-20251001",
        "primary_calls": 2,
        "fallback_calls": 0,
        "total_calls": 2,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, llm_fallback=llm_fallback
    )
    assert "Modell-Fallback" not in summary


def test_render_beginner_summary_llm_fallback_omits_primary_line_when_unknown():
    """Partial payload without primary_model still renders the headline cleanly."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    llm_fallback = {
        "fallback_used": True,
        "fallback_model": "claude-haiku-4-5-20251001",
        "primary_calls": 0,
        "fallback_calls": 1,
        "total_calls": 1,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, llm_fallback=llm_fallback
    )
    assert "## ⚠️ Modell-Fallback aktiv" in summary
    assert "claude-haiku-4-5-20251001" in summary
    # No "**Primär:**" line if primary_model is missing
    assert "**Primär:**" not in summary


def test_render_beginner_summary_persona_match_omits_weakest_line_when_data_partial():
    """Partial payload without weakest_label still renders the headline cleanly."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    persona_match = {
        "overall_score": 60,
        "status": "REVIEW",
        "description_present": True,
        "lead_lines_present": True,
        "total_personas": 2,
        "measurable_personas": 2,
        # no weakest_* fields
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, persona_match=persona_match
    )
    assert "## Persona-Match" in summary
    assert "60/100" in summary
    # Renderer should not crash and should not invent a "Schwächste Persona" line
    assert "Schwächste Persona" not in summary


def test_render_beginner_summary_amazon_html_preview_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    preview = {
        "headline": "Solides Sachbuch fuer CFOs",
        "lead": "Aus 10 Jahren operativer Praxis: konkrete Methoden mit Zahlen.",
        "bullets": ("Praxis-Playbook mit Checklisten", "Echte Zahlen aus 12 Projekten"),
        "char_count": 1240,
        "keyword_score": 65,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, amazon_html_preview=preview
    )

    assert "## Amazon-Beschreibung (Vorschau)" in summary
    assert "Solides Sachbuch fuer CFOs" in summary
    assert "Aus 10 Jahren operativer Praxis" in summary
    assert "Praxis-Playbook mit Checklisten" in summary
    assert "Echte Zahlen aus 12 Projekten" in summary
    assert "Gesamt-Zeichen: 1240" in summary
    assert "Keyword-Score: 65" in summary
    assert "amazon_description.html" in summary


def test_render_beginner_summary_amazon_html_preview_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))

    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, amazon_html_preview=None
    )
    assert "Amazon-Beschreibung (Vorschau)" not in summary

    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, amazon_html_preview={}
    )
    assert "Amazon-Beschreibung (Vorschau)" not in summary_empty


def test_render_beginner_summary_amazon_html_preview_skips_when_all_text_empty():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    preview = {
        "headline": "  ",
        "lead": "",
        "bullets": (),
        "char_count": 0,
        "keyword_score": 0,
    }

    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, amazon_html_preview=preview
    )

    assert "Amazon-Beschreibung (Vorschau)" not in summary


def test_render_beginner_summary_amazon_html_preview_works_with_bullets_only():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    preview = {
        "headline": "",
        "lead": "",
        "bullets": ("punkt eins", "punkt zwei"),
        "char_count": 0,
        "keyword_score": 0,
    }

    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, amazon_html_preview=preview
    )

    assert "## Amazon-Beschreibung (Vorschau)" in summary
    assert "punkt eins" in summary
    assert "punkt zwei" in summary


def test_render_beginner_summary_amazon_html_preview_omits_meta_when_zero():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    preview = {
        "headline": "Headline",
        "lead": "Lead text.",
        "bullets": (),
        "char_count": 0,
        "keyword_score": 0,
    }

    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, amazon_html_preview=preview
    )

    assert "## Amazon-Beschreibung (Vorschau)" in summary
    # Both meta values zero → meta line is skipped entirely
    assert "Gesamt-Zeichen" not in summary
    assert "Keyword-Score" not in summary


def test_render_beginner_summary_top_positioning_low_strength_uses_review_badge():
    """A strength of 70 must render with the REVIEW (yellow) badge."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_positioning = {
        "angle_claim": "Spitze Zielgruppe (CFOs) statt 'für alle'.",
        "angle_evidence": "Untertitel nennt CFO.",
        "angle_strength": 70,
        "pitch": "Dieses Buch liefert ... für CFOs.",
        "niche_label": "Finanzen / CFO / Controlling",
        "niche_confidence": 60,
        "audience": "CFOs",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_positioning=top_positioning
    )
    section_start = summary.index("## Positionierung")
    section_end = summary.index("competitive_positioning.md", section_start)
    section = summary[section_start:section_end]
    assert SCORE_BADGE_REVIEW in section


# ─── render_beginner_summary: top_collision_risk ─────────────────────


def test_render_beginner_summary_top_collision_risk_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_collision_risk = {
        "risk": "Ohne sichtbare Zahlen kaum von Motivationsliteratur abgrenzbar.",
        "niche_label": "KI / Künstliche Intelligenz",
        "niche_confidence": 75,
        "total_risks": 3,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_collision_risk=top_collision_risk,
    )
    assert "## ⚠️ Kollisions-Risiko" in summary
    assert "Ohne sichtbare Zahlen" in summary
    assert "**Nische:** KI / Künstliche Intelligenz" in summary
    # total_risks=3 → 2 further risks announced
    assert "Außerdem 2 weitere Risiken" in summary
    assert "`competitive_positioning.md`" in summary
    section_start = summary.index("## ⚠️ Kollisions-Risiko")
    section_end = summary.index("`competitive_positioning.md`", section_start)
    section = summary[section_start:section_end]
    assert SCORE_BADGE_FIX in section


def test_render_beginner_summary_top_collision_risk_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_collision_risk=None
    )
    assert "## ⚠️ Kollisions-Risiko" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_collision_risk={}
    )
    assert "## ⚠️ Kollisions-Risiko" not in summary_empty


def test_render_beginner_summary_top_collision_risk_section_absent_when_blank_text():
    """A whitespace-only risk payload must not render an empty warning."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_collision_risk = {
        "risk": "   ",
        "niche_label": "Finanzen",
        "niche_confidence": 80,
        "total_risks": 1,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_collision_risk=top_collision_risk,
    )
    assert "## ⚠️ Kollisions-Risiko" not in summary


def test_render_beginner_summary_top_collision_risk_singular_remaining_risk():
    """total_risks=2 must announce '1 weiteres Risiko' (singular)."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_collision_risk = {
        "risk": "Top-Risiko-Text.",
        "niche_label": "Vertrieb",
        "niche_confidence": 60,
        "total_risks": 2,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_collision_risk=top_collision_risk,
    )
    assert "Außerdem 1 weiteres Risiko" in summary


def test_render_beginner_summary_top_collision_risk_omits_remainder_when_total_is_one():
    """A single recorded risk must not announce 'außerdem 0 weitere'."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_collision_risk = {
        "risk": "Einziges Risiko.",
        "niche_label": "Mindset",
        "niche_confidence": 50,
        "total_risks": 1,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_collision_risk=top_collision_risk,
    )
    assert "## ⚠️ Kollisions-Risiko" in summary
    assert "Außerdem" not in summary[summary.index("## ⚠️ Kollisions-Risiko"):]


def test_render_beginner_summary_top_collision_risk_handles_partial_payload():
    """Missing niche/total fields must not crash and must skip those lines."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_collision_risk = {"risk": "Nur das Risiko ist da."}
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_collision_risk=top_collision_risk,
    )
    section_start = summary.index("## ⚠️ Kollisions-Risiko")
    section_end = summary.index("`competitive_positioning.md`", section_start)
    section = summary[section_start:section_end]
    assert "Nur das Risiko ist da." in section
    assert "**Nische:**" not in section
    assert "Außerdem" not in section


def test_render_beginner_summary_top_collision_risk_renders_after_positioning():
    """Per backlog: the risk must land directly below the positioning pitch."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_positioning = {
        "angle_claim": "Operator-Stimme.",
        "angle_strength": 80,
        "pitch": "Pitch-Satz.",
        "niche_label": "Vertrieb",
        "niche_confidence": 80,
        "audience": "Vertriebsleiter",
    }
    top_collision_risk = {
        "risk": "Test-Risiko.",
        "niche_label": "Vertrieb",
        "niche_confidence": 80,
        "total_risks": 1,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_positioning=top_positioning,
        top_collision_risk=top_collision_risk,
    )
    pos_idx = summary.index("## Positionierung")
    risk_idx = summary.index("## ⚠️ Kollisions-Risiko")
    assert pos_idx < risk_idx


# ─── render_beginner_summary: top_arc ──────────────────────────────────


def test_render_beginner_summary_top_arc_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_arc = {
        "arc_score": 60,
        "status": "FIX",
        "top_fix": "Kapitel 3 vor Kapitel 2 ziehen — LÖSUNG kommt vor BEWEIS.",
        "inversion_count": 2,
        "missing_count": 1,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_arc=top_arc
    )
    assert "## Kapitel-Reihung" in summary
    assert "60/100" in summary
    assert "Kapitel 3 vor Kapitel 2 ziehen" in summary
    assert "Reihenfolge-Konflikte: **2**" in summary
    assert "Fehlende Phasen: **1**" in summary
    assert "`chapter_arc.md`" in summary
    section_start = summary.index("## Kapitel-Reihung")
    section_end = summary.index("`chapter_arc.md`", section_start)
    section = summary[section_start:section_end]
    assert SCORE_BADGE_FIX in section


def test_render_beginner_summary_top_arc_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_arc=None
    )
    assert "## Kapitel-Reihung" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_arc={}
    )
    assert "## Kapitel-Reihung" not in summary_empty


def test_render_beginner_summary_top_arc_section_absent_when_top_fix_empty():
    """Without an actionable fix, the structural section must stay hidden."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_arc = {
        "arc_score": 95,
        "status": "READY",
        "top_fix": "   ",
        "inversion_count": 0,
        "missing_count": 0,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_arc=top_arc
    )
    assert "## Kapitel-Reihung" not in summary


def test_render_beginner_summary_top_arc_hides_zero_counts():
    """Inversion/missing rows must only appear when counts > 0."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_arc = {
        "arc_score": 70,
        "status": "REVIEW",
        "top_fix": "Es fehlt ein Beweis-Kapitel — eine Fallstudie einsetzen.",
        "inversion_count": 0,
        "missing_count": 1,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_arc=top_arc
    )
    section_start = summary.index("## Kapitel-Reihung")
    section_end = summary.index("`chapter_arc.md`", section_start)
    section = summary[section_start:section_end]
    assert "Reihenfolge-Konflikte" not in section
    assert "Fehlende Phasen: **1**" in section
    # 70 sits in the REVIEW band — badge must be yellow.
    assert SCORE_BADGE_REVIEW in section


def test_render_beginner_summary_top_arc_ready_badge_for_high_score():
    """A high arc_score must render the READY badge even if a top_fix exists."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_arc = {
        "arc_score": 90,
        "status": "READY",
        "top_fix": "Optional: BEWEIS-Phase mit zusätzlicher Zahl untermauern.",
        "inversion_count": 0,
        "missing_count": 0,
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_arc=top_arc
    )
    section_start = summary.index("## Kapitel-Reihung")
    section_end = summary.index("`chapter_arc.md`", section_start)
    section = summary[section_start:section_end]
    assert SCORE_BADGE_READY in section
    assert "90/100" in section


# ─── render_beginner_summary: top_chapter_balance ─────────────────────


def test_render_beginner_summary_top_chapter_balance_oversized_section_present():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_balance = {
        "kind": "oversized",
        "index": 4,
        "title": "Die Methode in der Praxis",
        "word_count": 5200,
        "median": 1000,
        "ratio": 5.2,
        "fix": "Kapitel 4 splitten — eigenes Kapitel fuer Fallstudie.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_chapter_balance=top_balance,
    )
    assert "## Kapitel-Balance" in summary
    assert "Split-Kandidat" in summary
    assert "Kapitel 4 — Die Methode in der Praxis" in summary
    assert "5200 Wörter" in summary
    assert "Median 1000" in summary
    assert "5.2×" in summary
    assert "Kapitel 4 splitten" in summary
    assert "`chapter_review.md`" in summary


def test_render_beginner_summary_top_chapter_balance_undersized_uses_yellow_label():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_balance = {
        "kind": "undersized",
        "index": 8,
        "title": "Anhang",
        "word_count": 120,
        "median": 1000,
        "ratio": 0.1,
        "fix": "Kapitel 8 mit Kapitel 7 zusammenlegen.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_chapter_balance=top_balance,
    )
    section_start = summary.index("## Kapitel-Balance")
    section_end = summary.index("`chapter_review.md`", section_start)
    section = summary[section_start:section_end]
    assert "Merge-Kandidat" in section
    assert "0.1×" in section
    assert SCORE_BADGE_REVIEW in section


def test_render_beginner_summary_top_chapter_balance_section_absent_when_none():
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_chapter_balance=None
    )
    assert "## Kapitel-Balance" not in summary
    summary_empty = render_beginner_summary(
        _project(manuscript=None, cover=None), result, top_chapter_balance={}
    )
    assert "## Kapitel-Balance" not in summary_empty


def test_render_beginner_summary_top_chapter_balance_absent_when_fix_blank():
    """A whitespace fix must not produce an empty quote block."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_balance = {
        "kind": "oversized",
        "index": 1,
        "title": "X",
        "word_count": 9000,
        "median": 1000,
        "ratio": 9.0,
        "fix": "   ",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_chapter_balance=top_balance,
    )
    assert "## Kapitel-Balance" not in summary


def test_render_beginner_summary_top_chapter_balance_handles_partial_payload():
    """Missing title falls back to a Kapitel-N placeholder."""
    result = build_industrial_qa(_project(manuscript=None, cover=None))
    top_balance = {
        "kind": "oversized",
        "index": 6,
        "word_count": 4000,
        "median": 1000,
        "ratio": 4.0,
        "fix": "Splitten.",
    }
    summary = render_beginner_summary(
        _project(manuscript=None, cover=None),
        result,
        top_chapter_balance=top_balance,
    )
    section_start = summary.index("## Kapitel-Balance")
    section_end = summary.index("`chapter_review.md`", section_start)
    section = summary[section_start:section_end]
    assert "Kapitel 6 — Kapitel 6" in section
    assert "4×" in section
    assert "Splitten." in section
