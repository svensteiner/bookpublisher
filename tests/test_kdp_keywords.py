"""Tests for the 7-slot KDP keyword generator."""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.kdp_keywords import (
    KDP_KEYWORD_MAX_CHARS,
    KDP_KEYWORD_SLOTS,
    KDPKeyword,
    KeywordConflict,
    build_kdp_keywords,
    extract_kdp_categories,
    find_keyword_conflicts,
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


# --- Conflict check --------------------------------------------------------


def _kw(text: str) -> KDPKeyword:
    return KDPKeyword(text=text, char_count=len(text), source="t", rationale="r")


def test_find_keyword_conflicts_returns_empty_for_no_keywords():
    assert find_keyword_conflicts([], ["Wirtschaft / Finanzen"]) == []


def test_find_keyword_conflicts_returns_empty_for_no_categories():
    assert find_keyword_conflicts([_kw("finanzen cfo praxis")], []) == []


def test_find_keyword_conflicts_flags_subject_overlap():
    keywords = [
        _kw("finanzen cfo praxis"),
        _kw("schritt fuer schritt anleitung"),
    ]
    categories = ["Sachbuch / Wirtschaft / Finanzen"]

    conflicts = find_keyword_conflicts(keywords, categories)

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.keyword_text == "finanzen cfo praxis"
    assert "finanzen" in conflict.shared_tokens


def test_find_keyword_conflicts_ignores_generic_filler_tokens():
    """A keyword sharing only 'ratgeber' or 'buch' is NOT a conflict."""
    keywords = [_kw("ratgeber praxis anleitung")]
    categories = ["Sachbuch / Ratgeber / Beruf"]

    conflicts = find_keyword_conflicts(keywords, categories)

    # 'ratgeber' is in the stop-token set, so no conflict despite overlap.
    assert conflicts == []


def test_find_keyword_conflicts_one_per_keyword_first_match_wins():
    keywords = [_kw("finanzen controlling")]
    categories = [
        "Sachbuch / Wirtschaft / Finanzen",
        "Sachbuch / Beruf / Controlling",
    ]

    conflicts = find_keyword_conflicts(keywords, categories)

    assert len(conflicts) == 1
    # First declared category wins
    assert conflicts[0].category == "Sachbuch / Wirtschaft / Finanzen"


def test_find_keyword_conflicts_shared_tokens_are_sorted():
    keywords = [_kw("finanzen cfo controlling")]
    categories = ["Sachbuch / Cfo / Finanzen / Controlling"]

    conflicts = find_keyword_conflicts(keywords, categories)

    assert len(conflicts) == 1
    shared = conflicts[0].shared_tokens
    assert list(shared) == sorted(shared)


def test_find_keyword_conflicts_skips_empty_keyword_text():
    """Defensive: an empty-text keyword should not crash the helper."""
    keywords = [_kw("")]
    categories = ["Sachbuch / Wirtschaft"]

    conflicts = find_keyword_conflicts(keywords, categories)

    assert conflicts == []


def test_extract_kdp_categories_returns_empty_when_no_metadata_files(tmp_path: Path):
    project = BookProject(
        project_id="x",
        root=tmp_path,
        metadata_files=[],
        notes_files=[],
    )

    assert extract_kdp_categories(project) == []


def test_extract_kdp_categories_parses_kdp_kategorien_section(tmp_path: Path):
    meta_file = tmp_path / "metadata.md"
    meta_file.write_text(
        "# Buch\n\n"
        "## KDP Kategorien\n\n"
        "- Sachbuch / Wirtschaft / Finanzen\n"
        "- Sachbuch / Ratgeber / Beruf & Karriere\n\n"
        "## Sonstiges\n\n"
        "Hier kommt etwas anderes.\n",
        encoding="utf-8",
    )
    project = BookProject(
        project_id="x",
        root=tmp_path,
        metadata_files=[meta_file],
        notes_files=[],
    )

    categories = extract_kdp_categories(project)

    assert categories == [
        "Sachbuch / Wirtschaft / Finanzen",
        "Sachbuch / Ratgeber / Beruf & Karriere",
    ]


def test_extract_kdp_categories_accepts_singular_form(tmp_path: Path):
    meta_file = tmp_path / "metadata.md"
    meta_file.write_text(
        "## Kategorie\n\nWirtschaft / Finanzen\n",
        encoding="utf-8",
    )
    project = BookProject(
        project_id="x",
        root=tmp_path,
        metadata_files=[meta_file],
        notes_files=[],
    )

    assert extract_kdp_categories(project) == ["Wirtschaft / Finanzen"]


def test_extract_kdp_categories_strips_list_prefixes(tmp_path: Path):
    meta_file = tmp_path / "metadata.md"
    meta_file.write_text(
        "## Kategorien\n\n"
        "1. Wirtschaft / Finanzen\n"
        "* Ratgeber / Beruf\n"
        "> Mindset / Resilienz\n",
        encoding="utf-8",
    )
    project = BookProject(
        project_id="x",
        root=tmp_path,
        metadata_files=[meta_file],
        notes_files=[],
    )

    categories = extract_kdp_categories(project)

    assert categories == [
        "Wirtschaft / Finanzen",
        "Ratgeber / Beruf",
        "Mindset / Resilienz",
    ]


def test_extract_kdp_categories_dedupes_across_files(tmp_path: Path):
    file_a = tmp_path / "metadata.md"
    file_a.write_text("## Kategorien\n- Wirtschaft / Finanzen\n", encoding="utf-8")
    file_b = tmp_path / "notes.md"
    file_b.write_text("## Kategorien\n- Wirtschaft / Finanzen\n", encoding="utf-8")
    project = BookProject(
        project_id="x",
        root=tmp_path,
        metadata_files=[file_a],
        notes_files=[file_b],
    )

    assert extract_kdp_categories(project) == ["Wirtschaft / Finanzen"]


def test_render_includes_conflict_section_with_categories():
    keywords = [
        _kw("finanzen cfo praxis"),
        _kw("schritt fuer schritt anleitung"),
    ]
    categories = ["Sachbuch / Wirtschaft / Finanzen"]
    conflicts = find_keyword_conflicts(keywords, categories)

    rendered = render_kdp_keywords_report_markdown(
        _project(),
        keywords,
        categories=categories,
        conflicts=conflicts,
    )

    assert "## Konflikt-Check (Kategorie vs. Keyword)" in rendered
    assert "Sachbuch / Wirtschaft / Finanzen" in rendered
    assert "🔴" in rendered
    assert "finanzen cfo praxis" in rendered


def test_render_shows_clean_message_when_no_conflicts():
    keywords = [_kw("schritt fuer schritt anleitung")]
    categories = ["Sachbuch / Mindset / Resilienz"]

    rendered = render_kdp_keywords_report_markdown(
        _project(),
        keywords,
        categories=categories,
    )

    assert "🟢" in rendered
    assert "Kein Konflikt" in rendered


def test_render_prompts_for_categories_when_metadata_missing():
    """When no categories are declared, the renderer must surface a hint."""
    keywords = [_kw("finanzen cfo")]

    rendered = render_kdp_keywords_report_markdown(
        _project(),
        keywords,
        categories=[],
    )

    assert "## Konflikt-Check" in rendered
    assert "Keine KDP-Kategorien" in rendered
    assert "## KDP Kategorien" in rendered


def test_keyword_conflict_to_json_roundtrip():
    conflict = KeywordConflict(
        keyword_text="finanzen cfo praxis",
        category="Sachbuch / Wirtschaft / Finanzen",
        shared_tokens=("finanzen",),
    )

    data = conflict.to_json()

    assert data == {
        "keyword_text": "finanzen cfo praxis",
        "category": "Sachbuch / Wirtschaft / Finanzen",
        "shared_tokens": ["finanzen"],
    }
