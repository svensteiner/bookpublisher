"""Tests for the optional LLM-Pass on concrete rewrite suggestions.

The pass rewrites the author's actual title / subtitle / description-lead
directly (only for fields that carry a diagnosis finding) and APPENDS those
variants next to the deterministic bestseller-pattern templates. It is gated
in the pipeline by ``AppConfig.rewrite_llm_variants_enabled`` AND the presence
of an ``ANTHROPIC_API_KEY``. These tests cover the pure functions; the
pipeline wiring lives in tests/test_pipeline_rewrite_llm.py.
"""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.rewrites import (
    DESCRIPTION_LEAD_MAX_CHARS,
    LLM_VARIANTS_DEFAULT_MOTIVATION,
    LLM_VARIANTS_PER_FIELD,
    REWRITE_HYPE_TOKENS,
    REWRITE_REJECT_DUPLICATE_OPENING,
    REWRITE_REJECT_EMPTY,
    REWRITE_REJECT_HYPE,
    REWRITE_REJECT_NO_ANCHOR,
    REWRITE_SOURCE_LLM,
    REWRITE_SOURCE_TEMPLATE,
    REWRITE_SOURCES,
    RewriteBundle,
    RewriteOption,
    RewriteReport,
    RewriteVariantQualityResult,
    TITLE_MAX_CHARS,
    TITLE_MIN_CHARS,
    apply_rewrite_variants,
    build_rewrite_report,
    build_rewrite_variants_user_prompt,
    extract_rewrite_variants_via_llm,
    validate_rewrite_variants,
    _parse_rewrite_variants_payload,
)


def _project() -> BookProject:
    return BookProject(
        project_id="solidity",
        root=Path("."),
        title="Solid",  # short → title diagnosis fires
        subtitle="Eine kompakte Einfuehrung",  # no audience → subtitle diagnosis
        amazon_description="Ein knappes Sachbuch ohne Zahlen.",  # short + no number
    )


def _clean_report() -> RewriteReport:
    """A report whose bundles have no diagnosis findings."""

    bundle = RewriteBundle(
        field="title",
        original="Ein sauberer Titel ohne Probleme",
        diagnosis=[],
        options=[
            RewriteOption(
                text="Template-Variante",
                char_count=17,
                keyword_score=0,
                motivation="m",
            )
        ],
    )
    return RewriteReport(anchors=["titel"], bundles=[bundle])


# --- RewriteOption.source -------------------------------------------------


def test_rewrite_option_defaults_to_template_source():
    option = RewriteOption(text="x", char_count=1, keyword_score=0, motivation="m")
    assert option.source == REWRITE_SOURCE_TEMPLATE


def test_rewrite_option_to_json_emits_source():
    option = RewriteOption(
        text="x", char_count=1, keyword_score=0, motivation="m", source=REWRITE_SOURCE_LLM
    )
    payload = option.to_json()
    assert payload["source"] == REWRITE_SOURCE_LLM


def test_rewrite_sources_are_distinct():
    assert REWRITE_SOURCE_TEMPLATE != REWRITE_SOURCE_LLM
    assert REWRITE_SOURCE_TEMPLATE in REWRITE_SOURCES
    assert REWRITE_SOURCE_LLM in REWRITE_SOURCES


# --- build_rewrite_variants_user_prompt -----------------------------------


def test_prompt_empty_when_no_field_needs_rewrite():
    assert build_rewrite_variants_user_prompt(_clean_report()) == ""


def test_prompt_lists_weak_fields_with_original_and_limit():
    report = build_rewrite_report(_project())
    prompt = build_rewrite_variants_user_prompt(report)

    assert prompt  # something to rewrite
    assert "field: title" in prompt
    assert "Original:" in prompt
    assert "Zeichen-Limit:" in prompt
    assert "Anker-Keywords:" in prompt
    assert str(LLM_VARIANTS_PER_FIELD) in prompt


def test_prompt_handles_blank_anchors_gracefully():
    bundle = RewriteBundle(
        field="title",
        original="Kurz",
        diagnosis=["Titel ist sehr kurz."],
        options=[],
    )
    report = RewriteReport(anchors=[], bundles=[bundle])
    prompt = build_rewrite_variants_user_prompt(report)
    assert "keine erkannt" in prompt


# --- _parse_rewrite_variants_payload --------------------------------------


def test_parse_accepts_valid_entries():
    payload = {
        "variants": [
            {"field": "title", "text": "Solide fuehren: Was Operatoren wirklich brauchen", "motivation": "Klarer Nutzen."},
        ]
    }
    parsed = _parse_rewrite_variants_payload(payload)
    assert "title" in parsed
    text, motivation = parsed["title"][0]
    assert "Solide" in text
    assert motivation == "Klarer Nutzen."


def test_parse_skips_unknown_field():
    payload = {"variants": [{"field": "tagline", "text": "x" * 30}]}
    assert _parse_rewrite_variants_payload(payload) == {}


def test_parse_rejects_exclamation_text():
    payload = {"variants": [{"field": "title", "text": "Kaufe dieses Buch jetzt sofort!"}]}
    assert _parse_rewrite_variants_payload(payload) == {}


def test_parse_rejects_too_short_text():
    payload = {"variants": [{"field": "title", "text": "ab"}]}
    assert _parse_rewrite_variants_payload(payload) == {}


def test_parse_clips_too_long_text():
    long_text = "Wort " * 40  # well over TITLE_MAX_CHARS
    payload = {"variants": [{"field": "title", "text": long_text}]}
    parsed = _parse_rewrite_variants_payload(payload)
    assert len(parsed["title"][0][0]) <= TITLE_MAX_CHARS


def test_parse_caps_variants_per_field():
    payload = {
        "variants": [
            {"field": "title", "text": f"Solider Titel Nummer {i} fuer Operatoren"}
            for i in range(5)
        ]
    }
    parsed = _parse_rewrite_variants_payload(payload)
    assert len(parsed["title"]) == LLM_VARIANTS_PER_FIELD


def test_parse_defaults_missing_motivation():
    payload = {"variants": [{"field": "title", "text": "Solider Titel fuer Operatoren"}]}
    parsed = _parse_rewrite_variants_payload(payload)
    assert parsed["title"][0][1] == LLM_VARIANTS_DEFAULT_MOTIVATION


def test_parse_handles_non_dict_payload():
    assert _parse_rewrite_variants_payload(["nope"]) == {}
    assert _parse_rewrite_variants_payload(None) == {}


def test_parse_handles_missing_variants_key():
    assert _parse_rewrite_variants_payload({"other": []}) == {}


def test_parse_skips_non_string_text():
    payload = {"variants": [{"field": "title", "text": 123}]}
    assert _parse_rewrite_variants_payload(payload) == {}


# --- extract_rewrite_variants_via_llm -------------------------------------


def test_extract_short_circuits_without_llm_call_when_clean():
    calls: list[tuple[str, str]] = []

    def completer(system: str, user: str) -> dict:
        calls.append((system, user))
        return {}

    result = extract_rewrite_variants_via_llm(_clean_report(), completer)
    assert result == {}
    assert calls == []


def test_extract_swallows_exceptions():
    report = build_rewrite_report(_project())

    def boom(system: str, user: str) -> dict:
        raise RuntimeError("network down")

    assert extract_rewrite_variants_via_llm(report, boom) == {}


def test_extract_returns_mapping_on_valid_response():
    report = build_rewrite_report(_project())

    def completer(system: str, user: str) -> dict:
        return {"variants": [{"field": "title", "text": "Solide fuehren ohne Hype, mit Methode"}]}

    result = extract_rewrite_variants_via_llm(report, completer)
    assert "title" in result


# --- apply_rewrite_variants -----------------------------------------------


def test_apply_returns_same_instance_for_empty_mapping():
    report = build_rewrite_report(_project())
    assert apply_rewrite_variants(report, {}) is report


def test_apply_appends_llm_option_with_source_and_score():
    report = build_rewrite_report(_project())
    original_title_count = len(
        next(b for b in report.bundles if b.field == "title").options
    )
    variants = {"title": [("Solide fuehren ohne Hype, mit Methode", "Klarer Nutzen.")]}

    result = apply_rewrite_variants(report, variants)
    title_bundle = next(b for b in result.bundles if b.field == "title")

    assert len(title_bundle.options) == original_title_count + 1
    appended = title_bundle.options[-1]
    assert appended.source == REWRITE_SOURCE_LLM
    assert appended.char_count == len(appended.text)
    assert 0 <= appended.keyword_score <= 100
    assert appended.motivation == "Klarer Nutzen."


def test_apply_does_not_mutate_original_report():
    report = build_rewrite_report(_project())
    title_bundle = next(b for b in report.bundles if b.field == "title")
    before = len(title_bundle.options)

    apply_rewrite_variants(report, {"title": [("Solide fuehren mit Methode", "m")]})

    after = len(next(b for b in report.bundles if b.field == "title").options)
    assert before == after  # original untouched


def test_apply_unknown_source_falls_back_to_llm():
    report = build_rewrite_report(_project())
    result = apply_rewrite_variants(
        report,
        {"title": [("Solide fuehren mit Methode", "m")]},
        source="garbage",
    )
    title_bundle = next(b for b in result.bundles if b.field == "title")
    assert title_bundle.options[-1].source == REWRITE_SOURCE_LLM


def test_apply_leaves_untargeted_bundles_unchanged():
    report = build_rewrite_report(_project())
    subtitle_before = next(b for b in report.bundles if b.field == "subtitle")
    result = apply_rewrite_variants(report, {"title": [("Solide fuehren mit Methode", "m")]})
    subtitle_after = next(b for b in result.bundles if b.field == "subtitle")
    assert subtitle_after.options == subtitle_before.options


def test_apply_skips_whitespace_only_variant():
    report = build_rewrite_report(_project())
    # A whitespace-only text clips to empty and must not append an option.
    result = apply_rewrite_variants(report, {"title": [("   ", "m")]})
    assert result is report


def test_apply_description_variant_respects_max_chars():
    report = build_rewrite_report(_project())
    long_text = "Dieses Buch zeigt konkret, wie es geht. " * 20
    result = apply_rewrite_variants(report, {"description_lead": [(long_text, "m")]})
    desc_bundle = next(b for b in result.bundles if b.field == "description_lead")
    appended = desc_bundle.options[-1]
    assert appended.char_count <= DESCRIPTION_LEAD_MAX_CHARS


# --- render markdown surfaces LLM provenance ------------------------------


def test_render_marks_llm_variant_in_markdown():
    from modules.rewrites import render_rewrite_report_markdown

    project = _project()
    report = build_rewrite_report(project)
    report = apply_rewrite_variants(
        report, {"title": [("Solide fuehren ohne Hype, mit Methode", "Klarer Nutzen.")]}
    )
    md = render_rewrite_report_markdown(project, report)
    assert "Quelle: LLM-Pass" in md


def test_render_template_only_has_no_llm_marker():
    from modules.rewrites import render_rewrite_report_markdown

    project = _project()
    report = build_rewrite_report(project)
    md = render_rewrite_report_markdown(project, report)
    assert "Quelle: LLM-Pass" not in md


# --- validate_rewrite_variants (quality gate) -----------------------------


def _gate_report() -> RewriteReport:
    """Report with two anchors and a known title original for gate tests."""

    bundle = RewriteBundle(
        field="title",
        original="Vertrieb mit Methode",
        diagnosis=["Titel ist sehr kurz"],
        options=[],
    )
    return RewriteReport(anchors=["methode", "vertrieb"], bundles=[bundle])


def test_validate_keeps_clean_variant_and_preserves_order():
    report = _gate_report()
    variants = {
        "title": [
            ("Vertrieb endlich mit klarer Methode steuern", "m1"),
            ("Methode statt Bauchgefuehl im Vertrieb", "m2"),
        ]
    }
    result = validate_rewrite_variants(variants, report)
    assert isinstance(result, RewriteVariantQualityResult)
    assert result.accepted["title"] == variants["title"]
    assert result.rejected == ()


def test_validate_drops_hype_variant():
    report = _gate_report()
    variants = {"title": [("Die ultimative Methode fuer Vertrieb", "m")]}
    result = validate_rewrite_variants(variants, report)
    assert "title" not in result.accepted
    assert result.rejected[0][0] == "title"
    assert result.rejected[0][2].startswith(REWRITE_REJECT_HYPE)


def test_validate_drops_variant_without_any_anchor():
    report = _gate_report()
    variants = {"title": [("Ein voellig anderer Ratgeber ohne Bezug", "m")]}
    result = validate_rewrite_variants(variants, report)
    assert "title" not in result.accepted
    assert result.rejected[0][2] == REWRITE_REJECT_NO_ANCHOR


def test_validate_drops_duplicate_opening_variant():
    report = _gate_report()
    # First sentence equals the original first sentence → no real rewrite.
    variants = {"title": [("Vertrieb mit Methode.", "m")]}
    result = validate_rewrite_variants(variants, report)
    assert "title" not in result.accepted
    assert result.rejected[0][2] == REWRITE_REJECT_DUPLICATE_OPENING


def test_validate_skips_anchor_check_when_no_anchors():
    report = RewriteReport(
        anchors=[],
        bundles=[RewriteBundle(field="title", original="Kurz", diagnosis=["x"], options=[])],
    )
    variants = {"title": [("Ein ganz neuer Titel ohne Anker", "m")]}
    result = validate_rewrite_variants(variants, report)
    assert result.accepted["title"] == variants["title"]


def test_validate_rejects_empty_and_non_string_text():
    report = _gate_report()
    variants = {"title": [("   ", "m"), (123, "m")]}  # type: ignore[list-item]
    result = validate_rewrite_variants(variants, report)
    assert "title" not in result.accepted
    assert {reason for _, _, reason in result.rejected} == {REWRITE_REJECT_EMPTY}


def test_validate_hype_check_runs_before_anchor_check():
    # Variant has hype AND lacks anchors — must be reported as hype, since
    # the hype gate runs first.
    report = _gate_report()
    variants = {"title": [("Das garantiert beste Buch ueberhaupt", "m")]}
    result = validate_rewrite_variants(variants, report)
    assert result.rejected[0][2].startswith(REWRITE_REJECT_HYPE)


def test_validate_does_not_mutate_input_report():
    report = _gate_report()
    before = report.to_json()
    validate_rewrite_variants({"title": [("Methode fuer besseren Vertrieb heute", "m")]}, report)
    assert report.to_json() == before


def test_hype_token_list_is_normalized_and_nonempty():
    assert REWRITE_HYPE_TOKENS
    for token in REWRITE_HYPE_TOKENS:
        assert token == token.lower()
        assert token.strip() == token


def test_amazon_html_hype_tokens_alias_rewrites_single_source():
    from modules.amazon_html import LLM_BULLETS_HYPE_TOKENS

    assert LLM_BULLETS_HYPE_TOKENS is REWRITE_HYPE_TOKENS


def test_extract_applies_quality_gate_dropping_hype():
    report = build_rewrite_report(_project())

    def completer(system: str, user: str) -> dict:
        # "Solid" anchor present but "ultimative" is hype → must be dropped.
        return {"variants": [{"field": "title", "text": "Die ultimative Solid-Methode fuer alle"}]}

    result = extract_rewrite_variants_via_llm(report, completer)
    assert result == {}
