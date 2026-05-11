"""Tests for the per-chapter analysis module."""

from __future__ import annotations

from modules.chapters import (
    Chapter,
    ChapterBalanceOutlier,
    ChapterBalanceReport,
    ChapterReport,
    ChapterScore,
    analyze_chapter_balance,
    build_chapter_report,
    render_chapter_report_markdown,
    score_chapter,
    split_paragraphs_into_chapters,
    top_weakest_chapters,
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


def _score(index: int, overall: int) -> ChapterScore:
    return ChapterScore(
        index=index,
        title=f"K{index}",
        word_count=500,
        promise=5,
        proof=5,
        value=5,
        transition=5,
        overall=overall,
        status="REVIEW",
        fix=f"fix-{index}",
    )


def test_top_weakest_chapters_returns_n_lowest_ascending():
    report = ChapterReport(
        chapters=[_score(1, 80), _score(2, 40), _score(3, 60), _score(4, 90)],
        average_score=68,
        weakest_chapter_index=2,
    )
    weakest = top_weakest_chapters(report, limit=3)
    assert [c.index for c in weakest] == [2, 3, 1]
    assert [c.overall for c in weakest] == [40, 60, 80]


def test_top_weakest_chapters_tie_breaks_by_index():
    report = ChapterReport(
        chapters=[_score(3, 50), _score(1, 50), _score(2, 50)],
        average_score=50,
        weakest_chapter_index=1,
    )
    weakest = top_weakest_chapters(report, limit=2)
    assert [c.index for c in weakest] == [1, 2]


def test_top_weakest_chapters_clamps_limit_to_chapter_count():
    report = ChapterReport(
        chapters=[_score(1, 30), _score(2, 60)],
        average_score=45,
        weakest_chapter_index=1,
    )
    assert len(top_weakest_chapters(report, limit=10)) == 2


def test_top_weakest_chapters_handles_empty_and_zero_limit():
    empty = ChapterReport(chapters=[], average_score=0, weakest_chapter_index=None)
    assert top_weakest_chapters(empty, limit=3) == []
    report = ChapterReport(
        chapters=[_score(1, 30)],
        average_score=30,
        weakest_chapter_index=1,
    )
    assert top_weakest_chapters(report, limit=0) == []


def test_top_weakest_chapters_does_not_mutate_report():
    chapters = [_score(1, 80), _score(2, 40)]
    report = ChapterReport(
        chapters=chapters,
        average_score=60,
        weakest_chapter_index=2,
    )
    snapshot = [c.index for c in report.chapters]
    top_weakest_chapters(report, limit=2)
    assert [c.index for c in report.chapters] == snapshot


def test_chapter_report_json_is_serializable():
    chapters = [Chapter(index=1, title="A", body=_body(200), word_count=200)]
    payload = build_chapter_report(chapters).to_json()
    assert "chapters" in payload
    assert payload["chapters"][0]["scores"].keys() == {"promise", "proof", "value", "transition"}
    assert payload["average_score"] == payload["chapters"][0]["overall"]


def _chapter(index: int, words: int, title: str | None = None) -> Chapter:
    return Chapter(
        index=index,
        title=title or f"Kapitel {index}",
        body="text " * words,
        word_count=words,
    )


def test_balance_flags_oversized_chapter():
    chapters = [
        _chapter(1, 800),
        _chapter(2, 1000),
        _chapter(3, 4000),  # 4x median -> split candidate
        _chapter(4, 900),
        _chapter(5, 1100),
    ]
    balance = analyze_chapter_balance(chapters)
    assert balance.median_word_count == 1000
    assert [o.index for o in balance.oversized] == [3]
    assert balance.oversized[0].kind == "oversized"
    assert balance.oversized[0].ratio == 4.0
    assert balance.undersized == []


def test_balance_flags_undersized_chapter():
    chapters = [
        _chapter(1, 1000),
        _chapter(2, 200),  # 0.2x median -> merge candidate
        _chapter(3, 1000),
        _chapter(4, 1100),
        _chapter(5, 900),
    ]
    balance = analyze_chapter_balance(chapters)
    assert balance.median_word_count == 1000
    assert [o.index for o in balance.undersized] == [2]
    assert balance.undersized[0].kind == "undersized"
    assert balance.undersized[0].ratio == 0.2
    assert balance.oversized == []


def test_balance_sorts_oversized_by_descending_word_count():
    chapters = [
        _chapter(1, 1000),
        _chapter(2, 1000),
        _chapter(3, 1000),
        _chapter(4, 1000),
        _chapter(5, 3500),
        _chapter(6, 5000),
        _chapter(7, 4200),
    ]
    balance = analyze_chapter_balance(chapters)
    assert balance.median_word_count == 1000
    # 5000 > 4200 > 3500, all above median*3=3000.
    assert [o.index for o in balance.oversized] == [6, 7, 5]


def test_balance_sorts_undersized_by_ascending_word_count():
    chapters = [
        _chapter(1, 1000),
        _chapter(2, 1000),
        _chapter(3, 1000),
        _chapter(4, 1000),
        _chapter(5, 50),
        _chapter(6, 200),
        _chapter(7, 100),
    ]
    balance = analyze_chapter_balance(chapters)
    assert balance.median_word_count == 1000
    # All below median*0.3=300, ascending by word count.
    assert [o.index for o in balance.undersized] == [5, 7, 6]


def test_balance_ignores_tiny_chapter_sets():
    chapters = [_chapter(1, 100), _chapter(2, 5000)]
    balance = analyze_chapter_balance(chapters)
    assert balance.has_findings is False
    assert balance.median_word_count == 0


def test_balance_empty_input():
    balance = analyze_chapter_balance([])
    assert balance.median_word_count == 0
    assert balance.oversized == []
    assert balance.undersized == []
    assert balance.has_findings is False


def test_balance_no_outliers_returns_empty_lists():
    chapters = [_chapter(i, 1000) for i in range(1, 6)]
    balance = analyze_chapter_balance(chapters)
    assert balance.median_word_count == 1000
    assert balance.oversized == []
    assert balance.undersized == []
    assert balance.has_findings is False


def test_balance_does_not_mutate_input():
    chapters = [_chapter(1, 1000), _chapter(2, 1000), _chapter(3, 5000)]
    word_counts_before = [c.word_count for c in chapters]
    analyze_chapter_balance(chapters)
    assert [c.word_count for c in chapters] == word_counts_before


def test_build_chapter_report_attaches_balance():
    chapters = [
        _chapter(1, 1000),
        _chapter(2, 1000),
        _chapter(3, 1000),
        _chapter(4, 4000),
    ]
    report = build_chapter_report(chapters)
    assert isinstance(report.balance, ChapterBalanceReport)
    assert report.balance.median_word_count == 1000
    assert any(o.index == 4 for o in report.balance.oversized)


def test_build_chapter_report_balance_fix_appended_to_fixes():
    chapters = [
        _chapter(1, 1000),
        _chapter(2, 1000),
        _chapter(3, 1000),
        _chapter(4, 4000),
    ]
    report = build_chapter_report(chapters)
    assert any("splitten" in fix.lower() or "split" in fix.lower() for fix in report.fixes)


def test_build_chapter_report_empty_carries_empty_balance():
    report = build_chapter_report([])
    assert isinstance(report.balance, ChapterBalanceReport)
    assert report.balance.median_word_count == 0


def test_render_markdown_shows_balance_section_when_outlier_exists():
    chapters = [
        _chapter(1, 1000),
        _chapter(2, 1000),
        _chapter(3, 1000),
        _chapter(4, 4000, title="Riesenkapitel"),
    ]
    report = build_chapter_report(chapters)
    md = render_chapter_report_markdown("Mein Buch", report)
    assert "## Kapitel-Balance" in md
    assert "Split-Kandidaten" in md
    assert "Riesenkapitel" in md


def test_render_markdown_hides_balance_section_when_no_outliers():
    chapters = [_chapter(i, 1000) for i in range(1, 5)]
    report = build_chapter_report(chapters)
    md = render_chapter_report_markdown("Mein Buch", report)
    assert "## Kapitel-Balance" not in md


def test_balance_outlier_to_json_is_serializable():
    outlier = ChapterBalanceOutlier(
        index=3,
        title="Riesenkapitel",
        word_count=4000,
        median=1000,
        ratio=4.0,
        kind="oversized",
        fix="split it",
    )
    payload = outlier.to_json()
    assert payload["index"] == 3
    assert payload["kind"] == "oversized"
    assert payload["ratio"] == 4.0


def test_chapter_report_to_json_includes_balance_when_present():
    chapters = [_chapter(i, 1000) for i in range(1, 5)]
    chapters.append(_chapter(5, 4500))
    report = build_chapter_report(chapters)
    payload = report.to_json()
    assert "balance" in payload
    assert payload["balance"]["median_word_count"] > 0


def test_chapter_report_to_json_omits_balance_when_none():
    report = ChapterReport(
        chapters=[_score(1, 80)],
        average_score=80,
        weakest_chapter_index=1,
    )
    payload = report.to_json()
    assert "balance" not in payload
