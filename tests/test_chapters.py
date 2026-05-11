"""Tests for the per-chapter analysis module."""

from __future__ import annotations

from modules.chapters import (
    Chapter,
    ChapterReport,
    build_chapter_report,
    render_chapter_report_markdown,
    score_chapter,
    split_paragraphs_into_chapters,
)


def _para(text: str, style: str = "Normal") -> dict:
    return {"text": text, "style": style}


def _heading(text: str) -> dict:
    return {"text": text, "style": "Heading 1"}


def _body(words: int, *, marker: str = "") -> str:
    base = ("Wir bauen praktische Systeme im Alltag. " * max(1, words // 7)) + " " + marker
    return base.strip()


def test_split_paragraphs_groups_by_heading():
    paragraphs = [
        _heading("Kapitel 1"),
        _para(_body(120)),
        _heading("Kapitel 2"),
        _para(_body(120)),
    ]

    chapters = split_paragraphs_into_chapters(paragraphs)

    assert len(chapters) == 2
    assert chapters[0].title == "Kapitel 1"
    assert chapters[1].title == "Kapitel 2"
    assert chapters[0].word_count > 50


def test_split_paragraphs_creates_vorwort_for_pre_heading_body():
    chapters = split_paragraphs_into_chapters([
        _para(_body(120)),
        _heading("Kapitel 1"),
        _para(_body(120)),
    ])
    assert chapters[0].title == "Vorwort"


def test_tiny_chapters_merge_into_previous():
    chapters = split_paragraphs_into_chapters([
        _heading("Kapitel 1"),
        _para(_body(200)),
        _heading("Danke"),
        _para("Vielen Dank."),
    ])
    assert len(chapters) == 1
    assert "Vielen Dank" in chapters[0].body


def test_empty_paragraphs_yields_no_chapters():
    assert split_paragraphs_into_chapters([]) == []


def test_score_chapter_returns_dimensions_in_range():
    chapter = Chapter(
        index=1,
        title="Kapitel 1",
        body=_body(200, marker="In diesem Kapitel lernst du das System. Beispiel: 5 Stunden."),
        word_count=200,
    )

    score = score_chapter(chapter)

    assert 1 <= score.promise <= 10
    assert 1 <= score.proof <= 10
    assert 1 <= score.value <= 10
    assert 1 <= score.transition <= 10
    assert 0 <= score.overall <= 100
    assert score.status in {"READY", "REVIEW", "FIX"}
    assert score.fix  # non-empty


def test_strong_chapter_scores_higher_than_weak():
    weak = Chapter(
        index=1,
        title="Schwach",
        body="Dies ist ein Text ohne klaren Nutzen oder Beweis. " * 30,
        word_count=210,
    )
    strong = Chapter(
        index=2,
        title="Stark",
        body=(
            "In diesem Kapitel lernst du die Methode. Ziel: Du kannst sie sofort umsetzen. "
            "Beispiel: 12 Stunden gespart, 3 Kunden gewonnen, 25 Prozent mehr Umsatz. "
            "Hier ist die Checkliste: Schritt-fuer-Schritt Anleitung mit Vorlage. "
            "Im naechsten Kapitel zeigen wir, wie du das System skalierst. Fazit: Es funktioniert."
        ) * 3,
        word_count=240,
    )

    weak_score = score_chapter(weak)
    strong_score = score_chapter(strong)

    assert strong_score.overall > weak_score.overall


def test_short_chapter_fix_mentions_length():
    chapter = Chapter(index=1, title="Kurz", body="Nur ein Satz.", word_count=4)
    score = score_chapter(chapter)
    assert "kurz" in score.fix.lower() or "woert" in score.fix.lower()


def test_build_chapter_report_aggregates_scores():
    chapters = [
        Chapter(index=1, title="A", body=_body(200), word_count=200),
        Chapter(index=2, title="B", body=_body(200), word_count=200),
    ]
    report = build_chapter_report(chapters)
    assert isinstance(report, ChapterReport)
    assert len(report.chapters) == 2
    assert 0 <= report.average_score <= 100
    assert report.weakest_chapter_index in {1, 2}


def test_build_chapter_report_empty():
    report = build_chapter_report([])
    assert report.chapters == []
    assert report.average_score == 0
    assert report.weakest_chapter_index is None


def test_render_markdown_lists_every_chapter():
    chapters = [
        Chapter(index=1, title="Erstes Kapitel", body=_body(200), word_count=200),
        Chapter(index=2, title="Zweites Kapitel", body=_body(200), word_count=200),
    ]
    report = build_chapter_report(chapters)
    md = render_chapter_report_markdown("Mein Buch", report)
    assert "# Kapitel-Analyse" in md
    assert "Erstes Kapitel" in md
    assert "Zweites Kapitel" in md
    assert "Mein Buch" in md


def test_render_markdown_handles_empty_report():
    report = build_chapter_report([])
    md = render_chapter_report_markdown("Leeres Buch", report)
    assert "Leeres Buch" in md
    assert "keine kapitel" in md.lower()


def test_chapter_report_json_is_serializable():
    chapters = [Chapter(index=1, title="A", body=_body(200), word_count=200)]
    payload = build_chapter_report(chapters).to_json()
    assert "chapters" in payload
    assert payload["chapters"][0]["scores"].keys() == {"promise", "proof", "value", "transition"}
    assert payload["average_score"] == payload["chapters"][0]["overall"]
