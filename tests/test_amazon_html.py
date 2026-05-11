"""Tests for the KDP Amazon-description HTML generator."""

from __future__ import annotations

import re
from pathlib import Path

from modules.amazon_html import (
    BULLET_MAX_CHARS,
    HEADLINE_MAX_CHARS,
    LEAD_MAX_CHARS,
    MAX_BULLETS,
    MIN_BULLETS,
    AmazonDescriptionHtml,
    build_amazon_description_html,
    render_amazon_description_report_markdown,
)
from modules.discovery import BookProject


def _project(
    title: str | None = "Soliditaet: Wie ich Geschaefte fuehre",
    subtitle: str | None = "Eine ehrliche Anleitung fuer Operatoren und CFOs",
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


# KDP-allowed tags (the safe subset we restrict the generator to).
_ALLOWED_TAGS: frozenset[str] = frozenset(
    {"b", "strong", "em", "i", "u", "br", "p", "ul", "ol", "li", "h4", "h5", "h6", "hr"}
)


def _tags_in(html: str) -> set[str]:
    return {match.lower() for match in re.findall(r"<\s*/?\s*([a-zA-Z0-9]+)", html)}


def test_build_returns_html_snippet_with_required_blocks():
    snippet = build_amazon_description_html(_project())

    assert isinstance(snippet, AmazonDescriptionHtml)
    assert "<b>" in snippet.html
    assert "<ul>" in snippet.html and "<li>" in snippet.html
    assert snippet.headline.strip()
    assert snippet.lead.strip()
    assert snippet.audience.strip()
    assert snippet.cta.strip()


def test_html_only_uses_kdp_allowed_tags():
    snippet = build_amazon_description_html(_project())
    tags = _tags_in(snippet.html)
    forbidden = tags - _ALLOWED_TAGS
    assert not forbidden, f"forbidden KDP tags emitted: {forbidden}"


def test_html_escapes_user_supplied_content():
    project = _project(
        title="Foo <script>alert('x')</script> & Co.",
        subtitle="Eine Anleitung fuer Skeptiker",
        description="Operator-Praxis & Zahlen aus 10+ Projekten. Drei Methoden.",
    )
    snippet = build_amazon_description_html(project)
    assert "<script>" not in snippet.html
    assert "&lt;script&gt;" in snippet.html
    assert "&amp;" in snippet.html


def test_bullets_respect_min_and_max_counts():
    snippet = build_amazon_description_html(_project())
    assert MIN_BULLETS <= len(snippet.bullets) <= MAX_BULLETS


def test_bullet_lengths_are_clipped():
    snippet = build_amazon_description_html(_project())
    for bullet in snippet.bullets:
        assert len(bullet) <= BULLET_MAX_CHARS


def test_headline_and_lead_respect_length_caps():
    snippet = build_amazon_description_html(_project())
    assert len(snippet.headline) <= HEADLINE_MAX_CHARS
    assert len(snippet.lead) <= LEAD_MAX_CHARS


def test_reuses_existing_bullet_markers_from_description():
    description = (
        "Praktisches Sachbuch fuer Operatoren — die kompakte Anleitung.\n"
        "\n"
        "- Drei Methoden mit echten Beispielen und Zahlen aus 12 Projekten\n"
        "- 12 Checklisten als sofort nutzbare Vorlagen fuer den Alltag\n"
        "- Klare Entscheidungsregeln statt Theorie-Wuerfeln und Motivationssprueche\n"
        "- Konkrete Schritte fuer CFOs und Operatoren in der ersten Woche\n"
    )
    project = _project(description=description)
    snippet = build_amazon_description_html(project)

    assert any("Drei Methoden" in bullet for bullet in snippet.bullets)
    assert any("Checklisten" in bullet for bullet in snippet.bullets)


def test_handles_empty_metadata_with_fallback_copy():
    project = BookProject(project_id="bare", root=Path("."))
    snippet = build_amazon_description_html(project)
    assert snippet.html
    assert len(snippet.bullets) >= MIN_BULLETS
    assert "Sachbuch" in snippet.headline or "Praktiker" in snippet.headline or snippet.headline


def test_keyword_score_is_zero_to_hundred():
    snippet = build_amazon_description_html(_project())
    assert 0 <= snippet.keyword_score <= 100


def test_to_json_round_trips_required_keys():
    snippet = build_amazon_description_html(_project())
    payload = snippet.to_json()
    expected = {"headline", "lead", "bullets", "audience", "cta", "html", "char_count", "keyword_score", "anchors"}
    assert expected <= payload.keys()
    assert isinstance(payload["bullets"], list)
    assert isinstance(payload["anchors"], list)


def test_render_report_markdown_contains_html_block_and_components():
    project = _project()
    snippet = build_amazon_description_html(project)
    md = render_amazon_description_report_markdown(project, snippet)

    assert "# Amazon-Beschreibung (KDP-HTML)" in md
    assert "```html" in md
    assert snippet.html in md
    assert snippet.headline in md
    assert "Bullet-Liste" in md


def test_char_count_matches_html_length():
    snippet = build_amazon_description_html(_project())
    assert snippet.char_count == len(snippet.html)
