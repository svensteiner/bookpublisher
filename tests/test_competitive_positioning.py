"""Tests for the competitive positioning heuristic."""

from __future__ import annotations

from pathlib import Path

from modules.competitive_positioning import (
    NICHE_LABELS,
    CompetitorArchetype,
    DifferentiationAngle,
    PositioningReport,
    build_positioning_report,
    detect_niche,
    render_positioning_markdown,
)
from modules.discovery import BookProject


def _project(
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


def test_detect_niche_picks_ki_for_ki_book():
    niche_key, confidence = detect_niche(_project())

    assert niche_key == "ki_und_ai"
    assert 1 <= confidence <= 100


def test_detect_niche_falls_back_to_general_without_signals():
    project = _project(title="Mein Buch", subtitle="", description="")

    niche_key, confidence = detect_niche(project)

    assert niche_key == "allgemeines_sachbuch"
    assert confidence == 0


def test_detect_niche_handles_empty_metadata():
    project = BookProject(project_id="x", root=Path("."), title=None, subtitle=None, amazon_description=None)

    niche_key, confidence = detect_niche(project)

    assert niche_key == "allgemeines_sachbuch"
    assert confidence == 0


def test_build_report_returns_frozen_report_with_archetypes_and_angles():
    report = build_positioning_report(_project())

    assert isinstance(report, PositioningReport)
    assert report.niche_key in NICHE_LABELS
    assert report.niche_label == NICHE_LABELS[report.niche_key]
    assert 3 <= len(report.archetypes) <= 4
    for archetype in report.archetypes:
        assert isinstance(archetype, CompetitorArchetype)
        assert archetype.name
        assert archetype.typical_weakness
    assert 1 <= len(report.unique_angles) <= 3
    for angle in report.unique_angles:
        assert isinstance(angle, DifferentiationAngle)
        assert 0 <= angle.strength <= 100


def test_strong_signals_produce_known_angle_keys():
    report = build_positioning_report(_project())
    keys = {angle.key for angle in report.unique_angles}

    # Description has numbers, "schritt für schritt", "keine Hype", operator voice, audience.
    expected_subset = {"zahlen_beweis", "methode_playbook"}
    assert expected_subset.issubset(keys), (
        f"Expected {expected_subset} ⊆ {keys}"
    )


def test_top_3_angles_are_sorted_by_strength():
    report = build_positioning_report(_project())

    strengths = [angle.strength for angle in report.unique_angles]
    assert strengths == sorted(strengths, reverse=True)


def test_unique_angles_capped_at_three():
    report = build_positioning_report(_project())

    assert len(report.unique_angles) <= 3


def test_empty_metadata_returns_fallback_angle():
    project = BookProject(project_id="x", root=Path("."), title="", subtitle="", amazon_description="")

    report = build_positioning_report(project)

    assert len(report.unique_angles) == 1
    assert report.unique_angles[0].key == "kein_signal"
    assert report.unique_angles[0].strength == 0


def test_collision_risks_called_out_when_signals_missing():
    project = _project(
        title="Buch über KI",
        subtitle="Eine Einführung",
        description="Dies ist ein Buch.",
    )

    report = build_positioning_report(project)

    assert any("Zahlen" in risk for risk in report.collision_risks)
    assert any("Zielgruppe" in risk for risk in report.collision_risks)


def test_hype_title_is_flagged_as_collision_risk():
    project = _project(
        title="Das Geheimnis der KI-Revolution",
        subtitle="Wie du reich wirst",
        description="Bahnbrechende Methoden für mehr Erfolg.",
    )

    report = build_positioning_report(project)

    assert any("Hype" in risk for risk in report.collision_risks)


def test_pitch_mentions_subject_and_audience():
    report = build_positioning_report(_project())

    assert report.positioning_pitch
    # Subject ("KI im Mittelstand") and audience are interpolated into the pitch.
    assert "Operatoren" in report.positioning_pitch or "Operator" in report.positioning_pitch


def test_to_json_roundtrip_is_serialisable():
    report = build_positioning_report(_project())

    payload = report.to_json()

    assert payload["niche_key"] == report.niche_key
    assert isinstance(payload["archetypes"], list)
    assert isinstance(payload["unique_angles"], list)
    assert isinstance(payload["collision_risks"], list)
    assert payload["positioning_pitch"] == report.positioning_pitch


def test_render_markdown_includes_all_sections():
    report = build_positioning_report(_project())
    md = render_positioning_markdown(_project(), report)

    assert "# Wettbewerbs-Positionierung" in md
    assert "## Wettbewerber-Archetypen" in md
    assert "## Was macht dieses Buch einzigartig" in md
    assert "## Kollisions-Risiken" in md
    assert "## Positionierungs-Satz" in md
    assert report.positioning_pitch in md


def test_render_markdown_handles_no_collision_risks():
    # Pack every detectable signal into the metadata so the risk list is empty.
    project = _project(
        title="KI-Playbook für CFOs aus 10 Jahren operativer Praxis",
        subtitle=(
            "Schritt für Schritt für Operatoren und CFOs — ohne Hype, "
            "mit Checklisten und Zahlen aus 12 Projekten"
        ),
        description=(
            "In diesem Buch zeige ich aus der Praxis, wie KI im Mittelstand in 30 Tagen "
            "Umsatz bringt. Methode, Framework und Checklisten. Keine Beratungs-Floskeln, "
            "kein Hype, nur Zahlen aus 12 echten Projekten."
        ),
    )
    report = build_positioning_report(project)
    md = render_positioning_markdown(project, report)

    if not report.collision_risks:
        assert "Keine erkennbaren Überschneidungen" in md


def test_finance_niche_is_detected():
    project = _project(
        title="Cashflow für Geschäftsführer",
        subtitle="Liquidität steuern ohne Berater",
        description="Praxis-Methode für CFO und Geschäftsführung mit Bilanz-Checklisten.",
    )

    report = build_positioning_report(project)

    assert report.niche_key == "finanzen_und_cfo"
    assert report.niche_label == NICHE_LABELS["finanzen_und_cfo"]
