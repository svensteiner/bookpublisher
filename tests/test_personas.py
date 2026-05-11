"""Tests for the Leser-Persona-Generator."""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.personas import (
    MAX_MOTIVE_CHARS,
    MAX_PERSONAS,
    MAX_PROBLEM_CHARS,
    MAX_QUOTE_CHARS,
    BuyerPersona,
    PersonaReport,
    build_persona_report,
    render_persona_brief_section,
    render_persona_report_markdown,
)


def _ki_project(
    *,
    title: str | None = "KI im Mittelstand: Was wirklich funktioniert",
    subtitle: str | None = "Ein ehrlicher Leitfaden für Operatoren und CFOs",
    description: str | None = (
        "Aus 10 Jahren operativer Praxis: In diesem Buch zeige ich Schritt für Schritt, "
        "wie kuenstliche Intelligenz in 30 Tagen im Mittelstand wirklich Umsatz bringt. "
        "Keine Hype-Versprechen, sondern Checklisten und konkrete Zahlen aus 12 Projekten."
    ),
) -> BookProject:
    return BookProject(
        project_id="ki_mittelstand",
        root=Path("."),
        title=title,
        subtitle=subtitle,
        amazon_description=description,
    )


def _empty_project() -> BookProject:
    return BookProject(
        project_id="leer",
        root=Path("."),
        title=None,
        subtitle=None,
        amazon_description=None,
    )


def test_build_report_returns_frozen_report_with_three_personas():
    report = build_persona_report(_ki_project())

    assert isinstance(report, PersonaReport)
    assert report.niche_key == "ki_und_ai"
    assert len(report.personas) == MAX_PERSONAS
    for persona in report.personas:
        assert isinstance(persona, BuyerPersona)
        assert persona.label
        assert persona.age_range
        assert persona.job
        assert persona.problem
        assert persona.buying_motive
        assert persona.anchor_quote


def test_personas_are_immutable_frozen_dataclasses():
    report = build_persona_report(_ki_project())
    persona = report.personas[0]

    try:
        persona.label = "anders"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("BuyerPersona should be frozen and reject mutation")


def test_persona_strings_respect_character_caps():
    report = build_persona_report(_ki_project())

    for persona in report.personas:
        assert len(persona.problem) <= MAX_PROBLEM_CHARS
        assert len(persona.buying_motive) <= MAX_MOTIVE_CHARS
        assert len(persona.anchor_quote) <= MAX_QUOTE_CHARS


def test_empty_project_falls_back_to_general_personas():
    report = build_persona_report(_empty_project())

    assert report.niche_key == "allgemeines_sachbuch"
    assert len(report.personas) == MAX_PERSONAS
    # Anchors derived from no metadata should be empty.
    assert report.anchors == []


def test_proof_signal_appears_in_motive_when_metadata_has_numbers():
    report = build_persona_report(_ki_project())

    assert "proof_signal" in report.signal_flags
    # At least one motive should reference numbers / cases per refinement.
    assert any("Zahlen" in p.buying_motive for p in report.personas)


def test_b2b_signal_flag_is_detected_for_mittelstand_book():
    report = build_persona_report(_ki_project())

    assert "b2b" in report.signal_flags


def test_self_employed_signal_for_solo_book():
    project = BookProject(
        project_id="solo",
        root=Path("."),
        title="Solopreneur skalieren",
        subtitle="Für Selbständige und Freelancer",
        amazon_description="Wie du als selbststaendiger Berater planbare Kunden bekommst.",
    )

    report = build_persona_report(project)

    assert "selbststaendig" in report.signal_flags
    assert any("Solo" in p.buying_motive for p in report.personas)


def test_finance_niche_picks_finance_personas():
    project = BookProject(
        project_id="finanz",
        root=Path("."),
        title="Liquidität steuern als CFO",
        subtitle="Praxis-Playbook für die Finanzführung im Mittelstand",
        amazon_description=(
            "Cashflow, Forecast und Controlling — wie du in 12 Wochen die Finanzführung "
            "in den Griff bekommst. Aus 15 Jahren Praxis als CFO im Mittelstand."
        ),
    )

    report = build_persona_report(project)

    assert report.niche_key == "finanzen_und_cfo"
    labels = " ".join(p.label for p in report.personas)
    assert "CFO" in labels or "Controller" in labels


def test_toc_anchors_extend_the_anchor_list():
    project = _ki_project()
    chapter_titles = [
        "Einleitung in die KI-Praxis",
        "Datenstrategie für den Mittelstand",
        "Tooling-Auswahl und Pilotprojekt",
    ]

    report = build_persona_report(project, chapter_titles=chapter_titles)

    # At least one TOC word should appear (lower-cased) in the anchor list.
    toc_lower = {"einleitung", "praxis", "datenstrategie", "tooling", "pilotprojekt"}
    assert toc_lower.intersection(report.anchors), report.anchors


def test_render_markdown_contains_all_personas_and_metadata():
    project = _ki_project()
    report = build_persona_report(project)

    md = render_persona_report_markdown(project, report)

    assert "# Leser-Personas" in md
    assert "Nische:" in md
    for persona in report.personas:
        assert persona.label in md
        assert persona.age_range in md
        assert persona.job in md


def test_brief_section_renders_three_numbered_personas():
    report = build_persona_report(_ki_project())

    block = render_persona_brief_section(report)

    assert "1." in block and "2." in block and "3." in block
    assert "buyer_personas.md" in block


def test_brief_section_is_empty_when_no_personas():
    report = PersonaReport(
        niche_key="x",
        niche_label="y",
        niche_confidence=0,
        audience="z",
        subject="s",
        personas=[],
        anchors=[],
        signal_flags=[],
    )

    assert render_persona_brief_section(report) == ""


def test_json_roundtrip_is_serialisable():
    import json

    report = build_persona_report(_ki_project())

    payload = report.to_json()
    encoded = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(encoded)

    assert decoded["niche_key"] == report.niche_key
    assert len(decoded["personas"]) == len(report.personas)
    assert decoded["personas"][0]["label"] == report.personas[0].label


def test_audience_uses_subtitle_for_marker():
    project = BookProject(
        project_id="x",
        root=Path("."),
        title="Test",
        subtitle="Ein Buch für ambitionierte Praktiker und Operatoren",
        amazon_description=None,
    )

    report = build_persona_report(project)

    # _extract_audience should yield "ambitionierte Praktiker" (truncated at "und andere")
    assert "Praktiker" in report.audience


def test_anchor_quote_contains_audience_and_subject():
    report = build_persona_report(_ki_project())
    persona = report.personas[0]

    assert report.audience in persona.anchor_quote or report.subject in persona.anchor_quote
