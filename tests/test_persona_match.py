"""Unit tests for modules.persona_match."""

from __future__ import annotations

from modules.persona_match import (
    LEAD_LINE_COUNT,
    SCORE_READY,
    SCORE_REVIEW,
    build_persona_match_report,
    render_persona_match_section,
)
from modules.personas import BuyerPersona, PersonaReport


def _persona(
    label: str = "Test Persona",
    problem: str = "Operative Finanzfuehrung ist ein Dauerbrand.",
    buying_motive: str = "Sucht ein Praxis-Playbook mit Checklisten.",
    anchor_quote: str = "praxis playbook cfo",
) -> BuyerPersona:
    return BuyerPersona(
        label=label,
        age_range="40-55",
        job="CFO im KMU",
        problem=problem,
        buying_motive=buying_motive,
        anchor_quote=anchor_quote,
    )


def _report(personas: list[BuyerPersona] | None = None) -> PersonaReport:
    return PersonaReport(
        niche_key="finanzen_und_cfo",
        niche_label="Finanzen & CFO",
        niche_confidence=80,
        audience="CFOs im KMU",
        subject="Operative Finanzfuehrung",
        personas=personas if personas is not None else [_persona()],
    )


def test_build_match_returns_empty_for_none_report():
    result = build_persona_match_report(None, "Beschreibung mit Worten.")

    assert result.entries == ()
    assert result.overall_score == 0
    assert result.description_present is True


def test_build_match_returns_empty_for_no_personas():
    report = _report(personas=[])

    result = build_persona_match_report(report, "Beschreibung mit Worten.")

    assert result.entries == ()
    assert result.overall_score == 0


def test_build_match_flags_missing_description():
    report = _report()

    result = build_persona_match_report(report, None)

    assert result.description_present is False
    assert result.lead_lines_present is False
    assert result.entries  # entries are still produced so the table renders
    assert all(entry.score == 0 for entry in result.entries)


def test_build_match_perfect_overlap_scores_high():
    persona = _persona(
        problem="Praxis Playbook mit Checklisten",
        buying_motive="Praxis Playbook Checklisten",
        anchor_quote="praxis playbook checklisten",
    )
    report = _report(personas=[persona])
    description = "Das Praxis Playbook fuer CFOs mit Checklisten und Tools."

    result = build_persona_match_report(report, description)

    assert result.overall_score >= SCORE_READY
    assert result.status == "READY"
    entry = result.entries[0]
    assert "praxis" in entry.matched_tokens
    assert "playbook" in entry.matched_tokens
    assert "checklisten" in entry.matched_tokens


def test_build_match_zero_overlap_scores_low():
    persona = _persona(
        problem="Marathonlauf Berge Wanderung",
        buying_motive="Wandern Berghuetten Schlafsack",
        anchor_quote="wanderlust bergstiefel",
    )
    report = _report(personas=[persona])
    description = "Ein Buch ueber Buchhaltung und Bilanzanalyse fuer CFOs."

    result = build_persona_match_report(report, description)

    assert result.overall_score < SCORE_REVIEW
    assert result.status == "FIX"
    assert result.entries[0].missing_tokens  # at least one missing token


def test_build_match_lead_only_ignores_text_after_third_line():
    persona = _persona(
        problem="praxis playbook",
        buying_motive="checklisten cfo",
        anchor_quote="praxis cfo",
    )
    report = _report(personas=[persona])
    # Persona-relevant tokens only appear AFTER the lead lines
    description = (
        "Zeile eins ohne Treffer.\n"
        "Zeile zwei ohne Treffer.\n"
        "Zeile drei ohne Treffer.\n"
        "Zeile vier mit praxis playbook checklisten cfo Treffer."
    )

    result = build_persona_match_report(report, description, lead_only=True)

    # Lead-only mode must not find tokens that live below line 3
    assert result.overall_score == 0


def test_build_match_full_mode_finds_tokens_below_lead():
    persona = _persona(
        problem="praxis playbook",
        buying_motive="checklisten cfo",
        anchor_quote="praxis cfo",
    )
    report = _report(personas=[persona])
    description = (
        "Zeile eins.\nZeile zwei.\nZeile drei.\n"
        "Zeile vier mit praxis playbook checklisten cfo."
    )

    result = build_persona_match_report(report, description, lead_only=False)

    assert result.overall_score > 0


def test_build_match_aggregates_across_personas():
    perfect = _persona(
        label="Perfect",
        problem="praxis playbook",
        buying_motive="checklisten cfo",
        anchor_quote="praxis cfo",
    )
    zero = _persona(
        label="Zero",
        problem="bergsteiger wanderlust",
        buying_motive="schlafsack zelten",
        anchor_quote="wandern berghuetten",
    )
    report = _report(personas=[perfect, zero])
    description = "Praxis Playbook Checklisten fuer CFOs."

    result = build_persona_match_report(report, description)

    scores = [entry.score for entry in result.entries]
    assert scores[0] > scores[1]
    expected_overall = int(round(sum(scores) / 2))
    assert result.overall_score == expected_overall


def test_build_match_handles_persona_with_only_stop_words():
    persona = BuyerPersona(
        label="Stop only",
        age_range="-",
        job="-",
        problem="die der das",
        buying_motive="und mit von",
        anchor_quote="auf bei aus",
    )
    report = _report(personas=[persona])

    result = build_persona_match_report(report, "Praxis Playbook")

    entry = result.entries[0]
    assert entry.total_tokens == 0
    assert entry.score == 0
    assert entry.matched_tokens == ()
    assert entry.missing_tokens == ()


def test_build_match_caps_missing_tokens_at_five():
    persona = _persona(
        problem="alpha bravo charlie delta echo foxtrot golf hotel",
        buying_motive="india juliett kilo lima",
        anchor_quote="mike november",
    )
    report = _report(personas=[persona])

    result = build_persona_match_report(report, "Buchhaltung fuer Anfaenger.")

    assert len(result.entries[0].missing_tokens) <= 5


def test_build_match_is_immutable():
    report = _report()

    result1 = build_persona_match_report(report, "Praxis Playbook.")
    result2 = build_persona_match_report(report, "Praxis Playbook.")

    # Both calls produce identical frozen reports (deterministic).
    assert result1.to_json() == result2.to_json()


def test_render_section_returns_empty_string_for_empty_entries():
    report = build_persona_match_report(None, "Beschreibung")

    rendered = render_persona_match_section(report)

    assert rendered == ""


def test_render_section_shows_message_when_description_missing():
    report = _report()
    match = build_persona_match_report(report, None)

    rendered = render_persona_match_section(match)

    assert "Keine Amazon-Beschreibung" in rendered
    assert "metadata.md" in rendered


def test_render_section_includes_table_and_overall_score():
    persona = _persona(
        problem="praxis playbook",
        buying_motive="checklisten",
        anchor_quote="praxis",
    )
    report = _report(personas=[persona])
    match = build_persona_match_report(report, "Praxis Playbook Checklisten.")

    rendered = render_persona_match_section(match)

    assert "## Match-Score gegen Amazon-Beschreibung" in rendered
    assert "Gesamt: **" in rendered
    assert "| Persona |" in rendered
    assert persona.label in rendered


def test_render_section_uses_dash_for_no_missing_tokens():
    persona = _persona(
        problem="praxis playbook",
        buying_motive="praxis playbook",
        anchor_quote="praxis playbook",
    )
    report = _report(personas=[persona])
    match = build_persona_match_report(report, "Das Praxis Playbook ist da.")

    rendered = render_persona_match_section(match)

    # Persona has 100% coverage so the missing column should show the em dash
    assert " — " in rendered


def test_status_thresholds():
    """Confirm SCORE_READY/SCORE_REVIEW match the documented behaviour."""
    # READY threshold
    persona = _persona(
        problem="alpha bravo charlie",
        buying_motive="alpha bravo",
        anchor_quote="alpha",
    )
    report = _report(personas=[persona])
    perfect = build_persona_match_report(report, "alpha bravo charlie")
    assert perfect.status == "READY"
    assert perfect.overall_score == 100

    # REVIEW threshold: ~50% match
    half = _persona(
        problem="alpha bravo",
        buying_motive="charlie delta",
        anchor_quote="alpha",
    )
    half_report = _report(personas=[half])
    half_result = build_persona_match_report(half_report, "alpha bravo")
    assert SCORE_REVIEW <= half_result.overall_score < SCORE_READY
    assert half_result.status == "REVIEW"

    # FIX threshold: 0% match
    miss = _persona(
        problem="alpha bravo",
        buying_motive="charlie delta",
        anchor_quote="echo",
    )
    miss_report = _report(personas=[miss])
    miss_result = build_persona_match_report(miss_report, "xxxx yyyy zzzz")
    assert miss_result.status == "FIX"


def test_lead_line_count_is_three():
    """Document the lead-line window via the constant."""
    assert LEAD_LINE_COUNT == 3
