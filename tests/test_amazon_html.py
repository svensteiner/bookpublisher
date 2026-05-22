"""Tests for the KDP Amazon-description HTML generator."""

from __future__ import annotations

import re
from pathlib import Path

from modules.amazon_html import (
    BULLET_MAX_CHARS,
    HEADLINE_MAX_CHARS,
    LEAD_MAX_CHARS,
    LLM_BULLETS_HYPE_TOKENS,
    LLM_BULLETS_MAX_CHAPTER_TITLES,
    LLM_BULLETS_MIN_CHARS,
    LLM_BULLETS_MIN_NUMBER_HITS,
    LLM_BULLETS_SYSTEM_PROMPT,
    MAX_BULLETS,
    MIN_BULLETS,
    AmazonDescriptionHtml,
    BulletQualityResult,
    _parse_llm_bullets_payload,
    build_amazon_description_html,
    build_llm_bullets_user_prompt,
    extract_amazon_bullets_via_llm,
    render_amazon_description_report_markdown,
    validate_llm_bullets,
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


# --- LLM-Pass tests --------------------------------------------------------


_GOOD_LLM_BULLETS = [
    "Drei Methoden mit echten Zahlen aus 12 Projekten — sofort einsetzbar",
    "Entscheidungsregeln statt Floskeln: was bei knapper Liquiditaet wirklich zaehlt",
    "Checklisten fuer den Monatsabschluss, die CFOs in 30 Minuten durchziehen",
    "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
    "Ehrliche Risiken und Stolperfallen statt Erfolgs-Storytelling",
]


def test_build_uses_llm_bullets_when_provided():
    snippet = build_amazon_description_html(_project(), llm_bullets=_GOOD_LLM_BULLETS)
    assert any("Liquiditaet" in bullet for bullet in snippet.bullets)
    assert all(len(bullet) <= BULLET_MAX_CHARS for bullet in snippet.bullets)
    assert MIN_BULLETS <= len(snippet.bullets) <= MAX_BULLETS


def test_build_falls_back_to_template_when_llm_bullets_too_few():
    snippet = build_amazon_description_html(
        _project(description="Praktisches Sachbuch."),
        llm_bullets=["Nur ein einzelner Bullet"],
    )
    # The template path always returns >= MIN_BULLETS — the LLM fallback
    # must not leave the description with one solitary bullet.
    assert len(snippet.bullets) >= MIN_BULLETS
    assert not any("Nur ein einzelner Bullet" == b for b in snippet.bullets)


def test_build_handles_none_llm_bullets_as_default_path():
    default_snippet = build_amazon_description_html(_project())
    explicit_none = build_amazon_description_html(_project(), llm_bullets=None)
    assert default_snippet.bullets == explicit_none.bullets


def test_build_clips_overlong_llm_bullets():
    long_bullet = "Konkrete Schritt-fuer-Schritt-Anleitung " * 10
    bullets = [long_bullet] + _GOOD_LLM_BULLETS[:4]
    snippet = build_amazon_description_html(_project(), llm_bullets=bullets)
    for bullet in snippet.bullets:
        assert len(bullet) <= BULLET_MAX_CHARS


def test_build_dedupes_repeated_llm_bullets():
    duplicate_bullet = "Drei Methoden mit echten Zahlen aus 12 Projekten heute"
    bullets = [duplicate_bullet] * 6
    snippet = build_amazon_description_html(_project(), llm_bullets=bullets)
    lowered = [b.lower() for b in snippet.bullets]
    # Dedup collapses to 1 → fewer than MIN_BULLETS → falls back to template.
    assert len(set(lowered)) == len(lowered)
    assert len(snippet.bullets) >= MIN_BULLETS


def test_build_drops_too_short_llm_bullets():
    bullets = ["Toll", "Bingo", "Praxis"] + _GOOD_LLM_BULLETS
    snippet = build_amazon_description_html(_project(), llm_bullets=bullets)
    for bullet in snippet.bullets:
        assert len(bullet) >= LLM_BULLETS_MIN_CHARS


def test_build_drops_non_string_llm_bullet_entries():
    bullets: list = list(_GOOD_LLM_BULLETS) + [None, 42, {"x": 1}]
    snippet = build_amazon_description_html(_project(), llm_bullets=bullets)
    assert MIN_BULLETS <= len(snippet.bullets) <= MAX_BULLETS
    assert all(isinstance(b, str) for b in snippet.bullets)


# --- Bullet-Quality-Check tests -------------------------------------------


def test_validate_accepts_good_bullets():
    result = validate_llm_bullets(_GOOD_LLM_BULLETS)
    assert isinstance(result, BulletQualityResult)
    assert result.passed is True
    assert len(result.accepted) == len(_GOOD_LLM_BULLETS)
    assert result.rejected == ()
    assert result.violations == ()


def test_validate_rejects_exclamation_marks():
    bullets = [
        "Wahnsinn! Mit diesem Buch wirst du sofort erfolgreich",
        "Drei Methoden mit echten Zahlen aus 12 Projekten — sofort einsetzbar",
        "Checklisten fuer den Monatsabschluss, die CFOs in 30 Minuten durchziehen",
        "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
    ]
    result = validate_llm_bullets(bullets)
    rejected_texts = [text for text, _reason in result.rejected]
    assert any(text.startswith("Wahnsinn!") for text in rejected_texts)
    assert any("contains_exclamation" in reason for _t, reason in result.rejected)


def test_validate_rejects_hype_tokens():
    bullets = [
        "Das ultimative Werk fuer alle die endlich Erfolg wollen ohne Umweg",
        "Drei Methoden mit echten Zahlen aus 12 Projekten — sofort einsetzbar",
        "Checklisten fuer den Monatsabschluss, die CFOs in 30 Minuten durchziehen",
        "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
    ]
    result = validate_llm_bullets(bullets)
    rejection_reasons = [reason for _text, reason in result.rejected]
    assert any(reason.startswith("contains_hype:") for reason in rejection_reasons)
    assert any("ultimativ" in reason for reason in rejection_reasons)


def test_validate_flags_duplicate_start_words():
    bullets = [
        "Drei Methoden mit echten Zahlen aus 12 Projekten — sofort einsetzbar",
        "Drei Schritte fuer den naechsten Monat, alle aus Kapitel zwei abgeleitet",
        "Checklisten fuer den Monatsabschluss, die CFOs in 30 Minuten durchziehen",
        "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
    ]
    result = validate_llm_bullets(bullets)
    assert "duplicate_start_word" in result.violations
    assert result.passed is False


def test_validate_flags_missing_number():
    bullets = [
        "Konkrete Methoden mit echten Beispielen — sofort einsetzbar im Alltag",
        "Entscheidungsregeln statt Floskeln fuer die naechste Krise",
        "Checklisten fuer den Monatsabschluss, die CFOs durchziehen koennen",
        "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
    ]
    result = validate_llm_bullets(bullets)
    assert "missing_number" in result.violations
    assert result.passed is False


def test_validate_passes_with_single_number_bullet():
    bullets = [
        "Konkrete Methoden mit echten Beispielen — sofort einsetzbar im Alltag",
        "Entscheidungsregeln statt Floskeln fuer die naechste Krise",
        "Checklisten fuer den Monatsabschluss, die CFOs in 30 Minuten durchziehen",
        "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
    ]
    result = validate_llm_bullets(bullets)
    assert result.passed is True
    assert "missing_number" not in result.violations


def test_validate_first_word_ignores_trailing_punctuation():
    bullets = [
        "Drei, klare Methoden mit Zahlen aus 12 echten Projekten",
        "Drei klare Schritte fuer den naechsten Monat aus dem Buch",
        "Checklisten fuer den Monatsabschluss, die CFOs in 30 Minuten durchziehen",
        "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
    ]
    result = validate_llm_bullets(bullets)
    assert "duplicate_start_word" in result.violations


def test_validate_empty_bullets_returns_all_rejected():
    result = validate_llm_bullets([])
    assert result.passed is False
    assert "all_rejected" in result.violations
    assert result.accepted == ()


def test_validate_below_min_bullets_does_not_pass():
    bullets = [
        "Drei Methoden mit echten Zahlen aus 12 Projekten — sofort einsetzbar",
        "Entscheidungsregeln statt Floskeln fuer die naechste Krise",
    ]
    result = validate_llm_bullets(bullets)
    # Per-bullet filters all pass and there's no aggregate violation, but
    # fewer than MIN_BULLETS bullets is not a "pass" — the caller relies on
    # this to fall back to the template path.
    assert result.passed is False
    assert result.violations == ()
    assert len(result.accepted) < MIN_BULLETS


def test_validate_returns_frozen_dataclass():
    result = validate_llm_bullets(_GOOD_LLM_BULLETS)
    try:
        result.passed = False  # type: ignore[misc]
    except Exception as exc:
        assert "frozen" in str(exc).lower() or "can't set" in str(exc).lower() or "cannot assign" in str(exc).lower()
    else:
        raise AssertionError("BulletQualityResult must be frozen — assignment should raise")


def test_validate_handles_non_string_entries():
    bullets: list = list(_GOOD_LLM_BULLETS) + [None, 42]
    result = validate_llm_bullets(bullets)
    rejected_reasons = [reason for _text, reason in result.rejected]
    assert rejected_reasons.count("non_string") == 2


def test_build_falls_back_to_template_when_quality_fails_hype():
    """LLM bullets containing hype tokens must NOT reach the HTML — even
    when there are enough of them to clear the MIN_BULLETS gate."""
    hype_bullets = [
        "Ultimative Methoden mit echten Zahlen aus 12 Projekten — Bestseller-Tipps",
        "Garantierter Erfolg in 30 Minuten pro Tag — unglaublich einfach",
        "Perfekter Einstieg fuer alle die endlich erfolgreich sein wollen",
        "Das geheime Wissen der besten CFOs aus 50 Projekten gebuendelt",
        "Revolutionaere Checklisten die jeder Operator sofort anwenden kann",
    ]
    snippet = build_amazon_description_html(_project(), llm_bullets=hype_bullets)
    joined = " ".join(snippet.bullets).lower()
    for token in ("ultimativ", "garantier", "perfekt", "geheim", "bestseller"):
        assert token not in joined, f"hype token '{token}' leaked into HTML bullets"


def test_build_falls_back_to_template_when_quality_fails_missing_number():
    bullets = [
        "Konkrete Methoden mit echten Beispielen — sofort einsetzbar im Alltag",
        "Entscheidungsregeln statt Floskeln fuer die naechste schwere Krise",
        "Checklisten fuer den Monatsabschluss, die CFOs durchziehen koennen",
        "Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
        "Ehrliche Risiken und Stolperfallen statt Erfolgs-Storytelling-Pose",
    ]
    snippet = build_amazon_description_html(_project(), llm_bullets=bullets)
    # The supplied bullets carry no digits → quality check fails → template
    # path runs. The template fallback uses a different sentence shape so
    # at least one supplied bullet must NOT appear verbatim.
    supplied_set = {b for b in bullets}
    assert not supplied_set.issubset(set(snippet.bullets))


def test_build_falls_back_to_template_when_quality_fails_duplicate_start():
    bullets = [
        "Drei Methoden mit echten Zahlen aus 12 Projekten — sofort einsetzbar",
        "Drei Schritte fuer den naechsten Monat, alle aus Kapitel zwei abgeleitet",
        "Drei Checklisten fuer den Monatsabschluss, die CFOs durchziehen koennen",
        "Drei Praxisbeispiele aus dem Mittelstand mit dokumentierten Ergebnissen",
    ]
    snippet = build_amazon_description_html(_project(), llm_bullets=bullets)
    # All bullets start with "Drei" → quality check fails → template path
    # runs. The template never produces 4 bullets that all start with the
    # same word.
    first_words = [b.split()[0].lower().rstrip(",.:;") for b in snippet.bullets]
    assert len(set(first_words)) > 1


def test_hype_token_constants_are_normalized():
    """Drift-Schutz: each hype token is lowercase + non-empty."""
    assert LLM_BULLETS_HYPE_TOKENS, "hype token list must not be empty"
    for token in LLM_BULLETS_HYPE_TOKENS:
        assert token == token.lower(), f"token '{token}' must be lowercased"
        assert token.strip() == token, f"token '{token}' must not be whitespace-padded"


def test_min_number_hits_constant_is_positive():
    assert LLM_BULLETS_MIN_NUMBER_HITS >= 1


def test_build_llm_bullets_user_prompt_includes_all_metadata():
    project = _project()
    prompt = build_llm_bullets_user_prompt(
        project, chapter_titles=["Einstieg", "Methode", "Praxis"]
    )
    assert project.title in prompt
    assert project.subtitle in prompt
    assert "Einstieg" in prompt and "Methode" in prompt and "Praxis" in prompt


def test_build_llm_bullets_user_prompt_caps_chapter_titles():
    project = _project()
    chapters = [f"Kapitel {i}" for i in range(100)]
    prompt = build_llm_bullets_user_prompt(project, chapter_titles=chapters)
    assert f"Kapitel {LLM_BULLETS_MAX_CHAPTER_TITLES - 1}" in prompt
    assert f"Kapitel {LLM_BULLETS_MAX_CHAPTER_TITLES}" not in prompt


def test_build_llm_bullets_user_prompt_handles_missing_metadata():
    project = BookProject(project_id="bare", root=Path("."))
    prompt = build_llm_bullets_user_prompt(project, chapter_titles=[])
    # No crash, sensible placeholders so the LLM still gets a valid prompt.
    assert "kein Titel" in prompt
    assert "keine Beschreibung" in prompt
    assert "keine Kapitel-Titel" in prompt


def test_parse_llm_bullets_payload_extracts_string_array():
    payload = {"bullets": _GOOD_LLM_BULLETS}
    parsed = _parse_llm_bullets_payload(payload)
    assert parsed == _GOOD_LLM_BULLETS


def test_parse_llm_bullets_payload_ignores_non_string_items():
    payload = {"bullets": ["valid", 42, None, "  ", "second"]}
    parsed = _parse_llm_bullets_payload(payload)
    assert parsed == ["valid", "second"]


def test_parse_llm_bullets_payload_returns_empty_for_wrong_shape():
    assert _parse_llm_bullets_payload({"foo": "bar"}) == []
    assert _parse_llm_bullets_payload({"bullets": "not a list"}) == []
    assert _parse_llm_bullets_payload("not a dict") == []
    assert _parse_llm_bullets_payload(None) == []


def test_extract_amazon_bullets_via_llm_returns_parsed_bullets():
    captured: dict = {}

    def fake_completer(system: str, user: str) -> dict:
        captured["system"] = system
        captured["user"] = user
        return {"bullets": _GOOD_LLM_BULLETS}

    bullets = extract_amazon_bullets_via_llm(_project(), ["Kap 1", "Kap 2"], fake_completer)
    assert bullets == _GOOD_LLM_BULLETS
    assert captured["system"] == LLM_BULLETS_SYSTEM_PROMPT
    assert "Kap 1" in captured["user"]


def test_extract_amazon_bullets_via_llm_swallows_exceptions():
    def failing_completer(system: str, user: str) -> dict:
        raise RuntimeError("boom")

    bullets = extract_amazon_bullets_via_llm(_project(), ["Kap 1"], failing_completer)
    assert bullets == []


def test_extract_amazon_bullets_via_llm_returns_empty_on_invalid_payload():
    def bad_completer(system: str, user: str) -> dict:
        return {"bullets": "not a list"}

    bullets = extract_amazon_bullets_via_llm(_project(), ["Kap 1"], bad_completer)
    assert bullets == []
