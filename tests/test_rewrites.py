"""Tests for the concrete rewrite-suggestions module."""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.rewrites import (
    DESCRIPTION_LEAD_MAX_CHARS,
    RewriteBundle,
    RewriteOption,
    RewriteReport,
    SUBTITLE_MAX_CHARS,
    TITLE_MAX_CHARS,
    build_rewrite_report,
    diagnose_description,
    diagnose_subtitle,
    diagnose_title,
    extract_anchor_keywords,
    render_rewrite_report_markdown,
    score_keywords,
)


def _project(
    title: str | None = "Solidität: Wie ich Geschäfte führe",
    subtitle: str | None = "Eine ehrliche Anleitung für Operatoren und CFOs",
    description: str | None = (
        "Praktisches Sachbuch fuer Operator und CFO mit konkreten Beispielen. "
        "Drei Methoden, 12 Checklisten und Zahlen aus echten Projekten."
    ),
) -> BookProject:
    return BookProject(
        project_id="solidity",
        root=Path("."),
        title=title,
        subtitle=subtitle,
        amazon_description=description,
    )


def test_extract_anchor_keywords_returns_substantive_words():
    anchors = extract_anchor_keywords(_project())

    assert anchors, "expected at least one anchor keyword"
    # Stopwords must not surface.
    assert "fuer" not in anchors
    assert "eine" not in anchors
    # Distinctive substantive words should.
    assert "operator" in anchors or "operatoren" in anchors or "cfos" in anchors


def test_extract_anchor_keywords_handles_empty_project():
    project = BookProject(project_id="empty", root=Path("."))
    assert extract_anchor_keywords(project) == []


def test_score_keywords_is_between_zero_and_hundred():
    anchors = ["operator", "cfo", "methode"]
    assert score_keywords("Operator-Playbook fuer CFO", anchors) > 0
    assert 0 <= score_keywords("zufallswort", anchors) <= 100
    assert score_keywords("", anchors) == 0
    assert score_keywords("text", []) == 0


def test_diagnose_title_flags_short_input():
    findings = diagnose_title("Kurz")
    assert any("kurz" in line.lower() for line in findings)


def test_diagnose_title_flags_missing_value():
    findings = diagnose_title("")
    assert any("fehlt" in line.lower() for line in findings)


def test_diagnose_subtitle_flags_missing_audience_marker():
    findings = diagnose_subtitle("Eine kompakte Einführung ins Thema")
    assert any("zielgruppe" in line.lower() for line in findings)


def test_diagnose_description_flags_no_numbers():
    findings = diagnose_description("Ein praktisches Sachbuch fuer Praktiker, ohne Hype geschrieben.")
    assert any("zahl" in line.lower() for line in findings)


def test_build_rewrite_report_returns_three_options_per_field():
    report = build_rewrite_report(_project())

    assert isinstance(report, RewriteReport)
    assert len(report.bundles) == 3
    fields = {bundle.field for bundle in report.bundles}
    assert fields == {"title", "subtitle", "description_lead"}
    for bundle in report.bundles:
        assert isinstance(bundle, RewriteBundle)
        assert len(bundle.options) == 3


def test_rewrite_options_respect_max_lengths():
    report = build_rewrite_report(_project())
    by_field = {bundle.field: bundle for bundle in report.bundles}

    for option in by_field["title"].options:
        assert option.char_count <= TITLE_MAX_CHARS
    for option in by_field["subtitle"].options:
        assert option.char_count <= SUBTITLE_MAX_CHARS
    for option in by_field["description_lead"].options:
        assert option.char_count <= DESCRIPTION_LEAD_MAX_CHARS


def test_rewrite_options_have_motivation_and_score_bounds():
    report = build_rewrite_report(_project())
    for bundle in report.bundles:
        for option in bundle.options:
            assert isinstance(option, RewriteOption)
            assert option.text.strip()
            assert option.motivation.strip()
            assert 0 <= option.keyword_score <= 100
            assert option.char_count == len(option.text)


def test_rewrite_report_handles_empty_metadata():
    report = build_rewrite_report(BookProject(project_id="bare", root=Path(".")))
    # Variants must still be produced even without metadata input.
    assert len(report.bundles) == 3
    for bundle in report.bundles:
        assert len(bundle.options) == 3
        assert bundle.diagnosis  # missing fields surface as diagnosis


def test_rewrite_report_json_round_trips():
    report = build_rewrite_report(_project())
    payload = report.to_json()
    assert "anchors" in payload
    assert len(payload["bundles"]) == 3
    bundle_payload = payload["bundles"][0]
    assert {"field", "original", "diagnosis", "options"} <= bundle_payload.keys()
    option_payload = bundle_payload["options"][0]
    assert {"text", "char_count", "keyword_score", "motivation"} <= option_payload.keys()


def test_render_rewrite_report_markdown_includes_all_sections():
    project = _project()
    report = build_rewrite_report(project)
    md = render_rewrite_report_markdown(project, report)

    assert "# Konkrete Rewrite-Vorschlaege" in md
    assert "Titel-Varianten" in md
    assert "Untertitel-Varianten" in md
    assert "Beschreibungs-Einstieg" in md
    assert "Variante 1" in md and "Variante 2" in md and "Variante 3" in md
    assert "Keyword-Score" in md
    assert "Kauf-Motivation" in md


def test_render_rewrite_report_markdown_handles_blank_anchors():
    project = BookProject(project_id="bare", root=Path("."))
    report = build_rewrite_report(project)
    md = render_rewrite_report_markdown(project, report)
    assert "Keine Anker-Keywords" in md
