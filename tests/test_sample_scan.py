"""Tests for the First-10%-Deep-Scan (Kindle-Sample) module."""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.sample_scan import (
    MAX_SECTIONS,
    SAMPLE_RATIO,
    SCORE_READY,
    SampleScanReport,
    SampleSectionScore,
    build_sample_scan_report,
    build_sample_scan_report_from_paragraphs,
    extract_sample_sections,
    render_sample_scan_markdown,
)


def _paragraph(text: str, style: str = "Normal") -> dict:
    return {"text": text, "style": style}


def _hooked_paragraphs() -> list[dict]:
    """A 10-section manuscript with a strong sample."""

    paras = [
        _paragraph("Kapitel 1: Der Einstieg", style="Heading 1"),
        _paragraph(
            "Stell dir vor, du verlierst 12.000 Euro in einem einzigen Quartal — "
            "und niemand merkt es. Genau das passierte mir 2019. "
            "In diesem Kapitel lernst du, wie du diese Falle vermeidest."
        ),
        _paragraph(
            "Du wirst eine Checkliste mit 7 Punkten bekommen, und ein konkretes "
            "Beispiel aus 3 echten Projekten. Schritt fuer Schritt, ohne Theorie."
        ),
        _paragraph("Kapitel 2: Die Methode", style="Heading 1"),
        _paragraph(
            "Hier liest du eine Methode, die in 18 Monaten 240 Stunden eingespart hat. "
            "Beispiel: ein CFO setzt die Schritt-fuer-Schritt-Vorlage in 30 Minuten um."
        ),
        _paragraph(
            "Am Ende dieses Kapitels weisst du genau, welche 3 Fehler du vermeidest "
            "und wie die Checkliste aussieht."
        ),
    ]
    # Pad with later chapters so the sample is genuinely ~10%.
    for idx in range(3, 11):
        paras.append(_paragraph(f"Kapitel {idx}: Inhalt", style="Heading 1"))
        paras.append(
            _paragraph(
                f"Spaeterer Inhalt fuer Kapitel {idx}. " * 30,
            )
        )
    return paras


def _hypey_paragraphs() -> list[dict]:
    """A weak sample: hype, filler, no concrete value, no hook."""

    paras = [
        _paragraph("Kapitel 1: Einleitung", style="Heading 1"),
        _paragraph(
            "Dieses unglaubliche, revolutionaere Buch wird dein Leben veraendern. "
            "Es ist absolut sensationell und tatsaechlich einzigartig. "
            "Eigentlich grundsaetzlich tatsaechlich absolut bahnbrechend."
        ),
        _paragraph(
            "Dieses Buch ist atemberaubend. Es ist sensationell. "
            "Es ist unglaublich. Es ist bahnbrechend."
        ),
    ]
    for idx in range(2, 11):
        paras.append(_paragraph(f"Kapitel {idx}: Inhalt", style="Heading 1"))
        paras.append(_paragraph(f"Spaeter mehr fuer Kapitel {idx}. " * 30))
    return paras


def _project_with_manuscript() -> BookProject:
    return BookProject(
        project_id="sample-test",
        root=Path("."),
        manuscript=None,
        title="Solidität: Wie ich Geschäfte führe",
    )


def test_extract_sample_sections_returns_first_10_percent():
    paras = _hooked_paragraphs()
    sections, total_words, sample_words = extract_sample_sections(paras)

    assert total_words > 0
    assert sample_words > 0
    # Sample must not consume the whole manuscript.
    assert sample_words < total_words
    # And should land somewhere near 10% (allow generous tolerance).
    assert sample_words <= int(total_words * 0.30)
    assert sections, "expected at least one section"


def test_sections_bucketed_by_heading():
    paras = _hooked_paragraphs()
    sections, _, _ = extract_sample_sections(paras)

    # First section should carry the Kapitel 1 heading text.
    assert sections[0].label.lower().startswith("kapitel 1")


def test_section_count_capped():
    # Build a manuscript with many tiny headings inside the sample window.
    paras: list[dict] = []
    for idx in range(30):
        paras.append(_paragraph(f"Abschnitt {idx}", style="Heading 2"))
        paras.append(_paragraph("Inhalt " * 60))
    report = build_sample_scan_report_from_paragraphs(paras)

    assert report.section_count <= MAX_SECTIONS


def test_strong_sample_scores_high():
    report = build_sample_scan_report_from_paragraphs(_hooked_paragraphs())

    assert report.section_count > 0
    assert report.overall_score >= 60, (
        f"expected strong sample to score >=60, got {report.overall_score}"
    )
    # The first section ought to be hookful.
    first = report.sections[0]
    assert first.hook >= 7


def test_hype_sample_flags_risk():
    report = build_sample_scan_report_from_paragraphs(_hypey_paragraphs())

    assert report.section_count > 0
    # Hype + filler must drive at least one section into FIX/REVIEW status.
    non_ready = [s for s in report.sections if s.status != "READY"]
    assert non_ready, "expected at least one risky section in a hype-only sample"
    assert any(s.status == "FIX" for s in report.sections) or report.overall_score < SCORE_READY


def test_report_serializes_to_json():
    report = build_sample_scan_report_from_paragraphs(_hooked_paragraphs())
    payload = report.to_json()

    assert {
        "manuscript_word_count",
        "sample_word_count",
        "sample_ratio",
        "section_count",
        "overall_score",
        "weakest_section_index",
        "sections",
        "fixes",
    } <= payload.keys()
    assert isinstance(payload["sections"], list)
    if payload["sections"]:
        assert {"index", "label", "scores", "overall", "risk", "fix"} <= payload["sections"][0].keys()


def test_empty_paragraphs_produces_empty_report():
    report = build_sample_scan_report_from_paragraphs([])

    assert isinstance(report, SampleScanReport)
    assert report.section_count == 0
    assert report.overall_score == 0
    assert report.sections == []
    assert report.weakest_section_index is None


def test_project_without_manuscript_returns_empty_report():
    project = _project_with_manuscript()
    report = build_sample_scan_report(project)

    assert report.section_count == 0
    md = render_sample_scan_markdown(project, report)
    assert "First-10%-Deep-Scan" in md


def test_render_markdown_lists_every_section():
    project = _project_with_manuscript()
    report = build_sample_scan_report_from_paragraphs(_hooked_paragraphs())
    md = render_sample_scan_markdown(project, report)

    assert "First-10%-Deep-Scan" in md
    assert "Pro Abschnitt" in md
    for sec in report.sections:
        assert f"Abschnitt {sec.index}" in md


def test_fix_targets_weakest_dimension():
    # Section with no hook + no value should produce a 'hook' or 'value' fix.
    paras = [
        _paragraph("Kapitel 1: Trockene Einleitung", style="Heading 1"),
        _paragraph(
            "Dieses Buch befasst sich mit dem Thema X. "
            "Im weiteren Verlauf werden wir das Thema betrachten. "
            "Es ist ein wichtiges Thema."
            * 5
        ),
    ]
    # Pad to make this the first 10%.
    for idx in range(2, 11):
        paras.append(_paragraph(f"Kapitel {idx}: Spaeter", style="Heading 1"))
        paras.append(_paragraph("Spaeter mehr. " * 60))
    report = build_sample_scan_report_from_paragraphs(paras)
    first = report.sections[0]

    assert first.fix, "expected a non-empty fix line"
    # The weakest section in this manuscript is hook or value — fix must
    # mention one of the relevant levers (Hook/Frage/Wert/Zahl/Checkliste).
    assert any(
        marker in first.fix
        for marker in ("Hook", "Wert", "Frage", "Zahl", "Versprechen", "Checkliste")
    )


def test_section_score_dataclass_is_immutable():
    report = build_sample_scan_report_from_paragraphs(_hooked_paragraphs())
    score = report.sections[0]
    assert isinstance(score, SampleSectionScore)
    try:
        score.hook = 0  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("SampleSectionScore should be frozen")
