"""Tests for modules.chapter_arc — Kapitel-Reihungscheck."""

from __future__ import annotations

from pathlib import Path

from modules.chapter_arc import (
    CANONICAL_PHASES,
    PHASE_PROBLEM,
    PHASE_PROOF,
    PHASE_SOLUTION,
    PHASE_TRANSFORMATION,
    ArcReport,
    ChapterPhase,
    build_arc_report,
    render_arc_report_markdown,
)
from modules.chapters import Chapter
from modules.discovery import BookProject


def _chapter(index: int, title: str, body: str) -> Chapter:
    return Chapter(index=index, title=title, body=body, word_count=len(body.split()))


PROBLEM_BODY = (
    "Viele glauben, der Status quo sei alternativlos. Doch das Problem "
    "ist groesser: Frustration, Verlust und Risiko bestimmen den Alltag. "
    "Die meisten scheitern an demselben Hindernis. " * 3
)
SOLUTION_BODY = (
    "Hier ist der Weg: ein klares Framework mit drei Schritten. "
    "Mein Ansatz folgt einem einfachen Prinzip — schrittweise umsetzen. "
    "So funktioniert die Methode in der Praxis. " * 3
)
PROOF_BODY = (
    "Fallstudie aus der Praxis: 12 Kunden, 25 Prozent mehr Umsatz, "
    "30 Stunden gespart. Die Studie zeigt klare Ergebnisse. "
    "Hier sind die Zahlen aus dem Live-Projekt. " * 3
)
TRANSFORMATION_BODY = (
    "Dein naechster Schritt: umsetzen. In 30 Tagen siehst du den Wandel. "
    "In deinem Alltag wirst du es spueren. Skalieren und integrieren. "
    "Das neue Du beginnt jetzt. " * 3
)


def _project() -> BookProject:
    return BookProject(project_id="testproj", root=Path("/tmp/none"), title="Mein Buch")


def test_canonical_phase_ordering_yields_full_score():
    chapters = [
        _chapter(1, "Das Problem", PROBLEM_BODY),
        _chapter(2, "Die Loesung", SOLUTION_BODY),
        _chapter(3, "Beweis aus der Praxis", PROOF_BODY),
        _chapter(4, "Deine Transformation", TRANSFORMATION_BODY),
    ]

    report = build_arc_report(chapters)

    phases = [item.phase for item in report.sequence]
    assert phases == list(CANONICAL_PHASES)
    assert report.arc_score == 100
    assert report.status == "READY"
    assert report.inversions == ()
    assert report.missing_phases == ()


def test_inverted_arc_penalizes_score_and_lists_inversions():
    chapters = [
        _chapter(1, "Transformation zuerst", TRANSFORMATION_BODY),
        _chapter(2, "Beweise", PROOF_BODY),
        _chapter(3, "Loesung", SOLUTION_BODY),
        _chapter(4, "Problem ganz am Ende", PROBLEM_BODY),
    ]

    report = build_arc_report(chapters)

    assert report.arc_score < 60
    assert report.status == "FIX"
    assert len(report.inversions) > 0
    # First chapter is transformation, last is problem — that pair must
    # be flagged.
    assert (1, 4) in report.inversions


def test_missing_phase_penalty_caps_arc_score():
    # Only PROBLEM and LÖSUNG present — no proof, no transformation.
    chapters = [
        _chapter(1, "Problem", PROBLEM_BODY),
        _chapter(2, "Loesung", SOLUTION_BODY),
    ]

    report = build_arc_report(chapters)

    assert PHASE_PROOF in report.missing_phases
    assert PHASE_TRANSFORMATION in report.missing_phases
    # Two missing phases → at least 30 points penalty.
    assert report.arc_score <= 70


def test_classifier_uses_position_bias_for_blank_chapters():
    # Body without any markers — classification should fall back to
    # position bias (first → PROBLEM, last → TRANSFORMATION).
    blank = "Lorem ipsum dolor sit amet, consetetur sadipscing elitr. " * 5
    chapters = [
        _chapter(1, "Erstes", blank),
        _chapter(2, "Mitte", blank),
        _chapter(3, "Letztes", blank),
    ]

    report = build_arc_report(chapters)

    phases = [item.phase for item in report.sequence]
    assert phases[0] == PHASE_PROBLEM
    assert phases[-1] == PHASE_TRANSFORMATION


def test_empty_chapter_list_returns_zero_score_and_all_phases_missing():
    report = build_arc_report([])
    assert isinstance(report, ArcReport)
    assert report.arc_score == 0
    assert report.status == "FIX"
    assert report.sequence == ()
    assert set(report.missing_phases) == set(CANONICAL_PHASES)
    assert report.fixes  # at least one user-facing hint


def test_single_chapter_yields_perfect_pair_score_but_phase_penalty():
    # One chapter — no pair inversions possible, so base score is 100,
    # but three phases are missing so penalty subtracts 45.
    chapters = [_chapter(1, "Einzelnes Kapitel", PROBLEM_BODY)]
    report = build_arc_report(chapters)
    assert len(report.sequence) == 1
    # 100 base − 45 missing penalty = 55.
    assert report.arc_score == 55
    assert report.status == "FIX"


def test_confidence_is_high_when_one_phase_dominates():
    chapter = _chapter(1, "Klare Loesung", SOLUTION_BODY)
    report = build_arc_report([chapter])
    assert report.sequence[0].phase == PHASE_SOLUTION
    assert report.sequence[0].confidence >= 50


def test_report_to_json_is_serializable():
    chapters = [
        _chapter(1, "Problem", PROBLEM_BODY),
        _chapter(2, "Loesung", SOLUTION_BODY),
    ]
    report = build_arc_report(chapters)
    payload = report.to_json()
    assert "sequence" in payload
    assert "arc_score" in payload
    assert "inversions" in payload
    assert "missing_phases" in payload
    assert isinstance(payload["sequence"], list)
    assert payload["sequence"][0]["phase"] in CANONICAL_PHASES


def test_render_markdown_contains_table_and_phase_emojis():
    chapters = [
        _chapter(1, "Das Problem", PROBLEM_BODY),
        _chapter(2, "Die Loesung", SOLUTION_BODY),
        _chapter(3, "Beweis", PROOF_BODY),
        _chapter(4, "Transformation", TRANSFORMATION_BODY),
    ]
    report = build_arc_report(chapters)
    md = render_arc_report_markdown(_project(), report)

    assert "Kapitel-Reihungscheck" in md
    assert "Mein Buch" in md
    assert "PROBLEM" in md
    assert "LÖSUNG" in md
    assert "BEWEIS" in md
    assert "TRANSFORMATION" in md
    assert "Arc-Score" in md
    # Confidence column rendered.
    assert "%" in md


def test_render_markdown_for_empty_report_explains_missing_headings():
    report = build_arc_report([])
    md = render_arc_report_markdown(_project(), report)
    assert "Keine Kapitel erkannt" in md
    assert "Ueberschriften" in md or "Heading" in md


def test_inversion_fix_message_mentions_both_chapters():
    chapters = [
        _chapter(1, "Erst Beweis", PROOF_BODY),
        _chapter(2, "Dann Problem", PROBLEM_BODY),
    ]
    report = build_arc_report(chapters)
    assert report.inversions
    # At least one fix line should refer to the two clashing chapters.
    joined = " ".join(report.fixes)
    assert "Kapitel 1" in joined
    assert "Kapitel 2" in joined


def test_chapter_phase_marker_counts_are_present_for_all_phases():
    chapter = _chapter(1, "Mixed", PROBLEM_BODY + SOLUTION_BODY)
    report = build_arc_report([chapter])
    item = report.sequence[0]
    assert isinstance(item, ChapterPhase)
    for phase in CANONICAL_PHASES:
        assert phase in item.marker_counts
        assert item.marker_counts[phase] >= 0


def test_missing_phase_fix_text_is_actionable():
    chapters = [_chapter(1, "Nur Problem", PROBLEM_BODY)]
    report = build_arc_report(chapters)
    assert PHASE_SOLUTION in report.missing_phases
    joined = " ".join(report.fixes)
    # Author-friendly guidance, not just a flag.
    assert "Loesung" in joined or "Methode" in joined or "Framework" in joined
