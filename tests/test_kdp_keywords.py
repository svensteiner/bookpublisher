"""Tests for the 7-slot KDP keyword generator."""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.kdp_keywords import (
    KDP_KEYWORD_MAX_CHARS,
    KDP_KEYWORD_SLOTS,
    KDPKeyword,
    build_kdp_keywords,
    render_kdp_keywords_report_markdown,
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


def test_build_kdp_keywords_fills_seven_slots():
    keywords = build_kdp_keywords(_project())

    assert len(keywords) == KDP_KEYWORD_SLOTS
    for kw in keywords:
        assert isinstance(kw, KDPKeyword)


def test_keywords_respect_kdp_character_limit():
    keywords = build_kdp_keywords(_project())

    for kw in keywords:
        assert 0 < kw.char_count <= KDP_KEYWORD_MAX_CHARS
        assert kw.char_count == len(kw.text)


def test_keywords_are_unique_and_lowercase():
    keywords = build_kdp_keywords(_project())

    texts = [kw.text for kw in keywords]
    assert len(set(texts)) == len(texts)
    for text in texts:
        assert text == text.lower()


def test_keywords_strip_umlauts_to_ascii_variants():
    keywords = build_kdp_keywords(_project(
        title="Glück und Erfolg",
        subtitle="Für Anfänger und Fortgeschrittene",
        description="Buch mit Übungen und Übersicht.",
    ))

    for kw in keywords:
        for forbidden in ("ä", "ö", "ü", "ß"):
            assert forbidden not in kw.text


def test_keywords_skip_forbidden_marketing_tokens():
    keywords = build_kdp_keywords(_project(
        title="Bestseller Marketing",
        subtitle="Kostenlos für alle",
        description="Free amazon kindle guide.",
    ))

    forbidden = {"bestseller", "kostenlos", "free", "amazon", "kindle", "gratis", "new", "neu", "sale"}
    for kw in keywords:
        words = set(kw.text.split())
        assert not (words & forbidden), f"forbidden token slipped through: {kw.text}"


def test_keywords_handle_empty_project_gracefully():
    keywords = build_kdp_keywords(BookProject(project_id="bare", root=Path(".")))

    # Even with no metadata the fallback ladder should provide some slots.
    assert keywords, "expected fallback keywords for an empty project"
    assert all(0 < kw.char_count <= KDP_KEYWORD_MAX_CHARS for kw in keywords)


def test_keywords_serialize_to_json():
    keywords = build_kdp_keywords(_project())
    payload = [kw.to_json() for kw in keywords]

    for entry in payload:
        assert {"text", "char_count", "source", "rationale"} <= entry.keys()


def test_render_markdown_contains_all_slots():
    project = _project()
    keywords = build_kdp_keywords(project)
    md = render_kdp_keywords_report_markdown(project, keywords)

    assert "7 KDP-Keywords" in md
    for idx in range(1, len(keywords) + 1):
        assert f"Slot {idx}" in md
    for kw in keywords:
        assert kw.text in md


def test_render_markdown_handles_empty_keyword_list():
    project = BookProject(project_id="bare", root=Path("."))
    md = render_kdp_keywords_report_markdown(project, [])

    assert "Es konnten keine Keywords" in md


def test_keywords_do_not_duplicate_title_verbatim():
    # Title is single word — anchor pipelines should not produce that
    # exact single-word phrase as a standalone slot.
    project = _project(
        title="Resilienz",
        subtitle="Praxisbuch fuer Fuehrungskraefte",
        description="Ratgeber fuer Resilienz im Berufsalltag mit konkreten Uebungen.",
    )
    keywords = build_kdp_keywords(project)

    # No slot should be exactly the title.
    title_lower = "resilienz"
    for kw in keywords:
        assert kw.text != title_lower
