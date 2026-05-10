from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modules.discovery import BookProject
from modules.industrial import (
    Gate,
    _asset_gate,
    _kindle_gate,
    _metadata_gate,
    _production_gate,
    _sellability_gate,
    _status,
    build_industrial_qa,
    render_beginner_summary,
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
