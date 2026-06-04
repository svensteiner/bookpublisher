"""Tests for the optional KDP-keyword LLM long-tail pass.

These cover the pure helpers in ``modules.kdp_keywords`` (prompt builder,
payload parser, the ``extract_kdp_keywords_via_llm`` short-circuit /
exception swallow) and the integration of ``llm_phrases`` into
``build_kdp_keywords`` — all without touching the network or a real key.
"""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.kdp_keywords import (
    KDP_KEYWORD_MAX_CHARS,
    KDP_KEYWORD_SLOTS,
    KDP_KEYWORD_SOURCE_LLM,
    KeywordQualityResult,
    LLM_KEYWORDS_HYPE_TOKENS,
    LLM_KEYWORDS_MAX_CHAPTER_TITLES,
    LLM_KEYWORDS_MAX_SLOTS,
    LLM_KEYWORDS_MIN_WORDS,
    build_kdp_keywords,
    build_kdp_keywords_user_prompt,
    extract_kdp_keywords_via_llm,
    validate_llm_keywords,
    _parse_llm_keywords_payload,
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


# --- _parse_llm_keywords_payload -----------------------------------------


def test_parse_payload_extracts_string_keywords():
    payload = {"keywords": ["liquiditaet planen mittelstand", "cfo checklisten"]}
    assert _parse_llm_keywords_payload(payload) == [
        "liquiditaet planen mittelstand",
        "cfo checklisten",
    ]


def test_parse_payload_skips_non_strings_and_empty():
    payload = {"keywords": ["valide phrase", 42, "", "   ", None, "zweite phrase"]}
    assert _parse_llm_keywords_payload(payload) == ["valide phrase", "zweite phrase"]


def test_parse_payload_tolerates_shape_drift():
    assert _parse_llm_keywords_payload(None) == []
    assert _parse_llm_keywords_payload([]) == []
    assert _parse_llm_keywords_payload({"keywords": "not a list"}) == []
    assert _parse_llm_keywords_payload({"other": ["x"]}) == []


def test_parse_payload_strips_whitespace():
    payload = {"keywords": ["  phrase mit rand  "]}
    assert _parse_llm_keywords_payload(payload) == ["phrase mit rand"]


# --- build_kdp_keywords_user_prompt --------------------------------------


def test_user_prompt_contains_metadata_and_chapters():
    prompt = build_kdp_keywords_user_prompt(
        _project(), ["Liquiditaet steuern", "Monatsabschluss"]
    )
    assert "Soliditä" in prompt or "Solidit" in prompt
    assert "Liquiditaet steuern" in prompt
    assert "Monatsabschluss" in prompt
    assert "JSON" in prompt


def test_user_prompt_handles_missing_metadata():
    project = _project(title=None, subtitle=None, description=None)
    prompt = build_kdp_keywords_user_prompt(project, [])
    assert "(kein Titel)" in prompt
    assert "(kein Untertitel)" in prompt
    assert "(keine Beschreibung)" in prompt
    assert "(keine Kapitel-Titel verfuegbar)" in prompt


def test_user_prompt_caps_chapter_titles():
    titles = [f"Kapitel {i}" for i in range(LLM_KEYWORDS_MAX_CHAPTER_TITLES + 10)]
    prompt = build_kdp_keywords_user_prompt(_project(), titles)
    # Only the first N titles survive the cap.
    assert f"Kapitel {LLM_KEYWORDS_MAX_CHAPTER_TITLES - 1}" in prompt
    assert f"Kapitel {LLM_KEYWORDS_MAX_CHAPTER_TITLES + 5}" not in prompt


def test_user_prompt_skips_empty_chapter_titles():
    prompt = build_kdp_keywords_user_prompt(_project(), ["", "  ", "Echtes Kapitel"])
    assert "Echtes Kapitel" in prompt
    assert "- \n" not in prompt


# --- extract_kdp_keywords_via_llm ----------------------------------------


def test_extract_returns_parsed_phrases():
    calls: list[tuple[str, str]] = []

    def completer(system: str, user: str) -> dict:
        calls.append((system, user))
        return {"keywords": ["liquiditaet mittelstand", "cfo monatsabschluss"]}

    result = extract_kdp_keywords_via_llm(_project(), ["Kap 1"], completer)
    assert result == ["liquiditaet mittelstand", "cfo monatsabschluss"]
    assert len(calls) == 1


def test_extract_swallows_exceptions():
    def completer(system: str, user: str) -> dict:
        raise RuntimeError("network down")

    assert extract_kdp_keywords_via_llm(_project(), ["Kap 1"], completer) == []


def test_extract_handles_garbage_payload():
    def completer(system: str, user: str) -> dict:
        return {"unexpected": True}

    assert extract_kdp_keywords_via_llm(_project(), [], completer) == []


# --- build_kdp_keywords with llm_phrases ---------------------------------


def test_build_without_llm_phrases_is_template_only():
    template = build_kdp_keywords(_project())
    with_none = build_kdp_keywords(_project(), llm_phrases=None)
    assert [k.to_json() for k in template] == [k.to_json() for k in with_none]
    assert all(k.source != KDP_KEYWORD_SOURCE_LLM for k in template)


def test_llm_phrases_claim_leading_slots():
    keywords = build_kdp_keywords(
        _project(),
        llm_phrases=[
            "liquiditaet steuern mittelstand",
            "cfo monatsabschluss schnell",
        ],
    )
    assert keywords[0].source == KDP_KEYWORD_SOURCE_LLM
    assert keywords[1].source == KDP_KEYWORD_SOURCE_LLM
    assert keywords[0].text == "liquiditaet steuern mittelstand"
    # Deterministic template paths fill the remaining slots.
    assert any(k.source != KDP_KEYWORD_SOURCE_LLM for k in keywords)


def test_llm_phrases_are_capped_at_max_slots():
    many = [f"longtail phrase nummer {i}" for i in range(LLM_KEYWORDS_MAX_SLOTS + 4)]
    keywords = build_kdp_keywords(_project(), llm_phrases=many)
    llm_count = sum(1 for k in keywords if k.source == KDP_KEYWORD_SOURCE_LLM)
    assert llm_count <= LLM_KEYWORDS_MAX_SLOTS


def test_llm_single_word_phrases_dropped():
    # A single-word phrase has fewer than LLM_KEYWORDS_MIN_WORDS words.
    assert LLM_KEYWORDS_MIN_WORDS == 2
    keywords = build_kdp_keywords(_project(), llm_phrases=["finanzen", "buch"])
    assert all(k.source != KDP_KEYWORD_SOURCE_LLM for k in keywords)


def test_llm_phrases_respect_kdp_rules():
    keywords = build_kdp_keywords(
        _project(),
        llm_phrases=[
            "bestseller geheim",  # forbidden token -> dropped
            "Liquidität steuern Mittelstand",  # umlaut -> normalized, kept
            "liquiditaet steuern mittelstand",  # duplicate of above -> deduped
        ],
    )
    llm_slots = [k for k in keywords if k.source == KDP_KEYWORD_SOURCE_LLM]
    # Only one valid, unique, allowed phrase survives.
    assert len(llm_slots) == 1
    kw = llm_slots[0]
    assert "ä" not in kw.text
    assert kw.char_count <= KDP_KEYWORD_MAX_CHARS
    assert "bestseller" not in kw.text


def test_total_still_capped_at_seven_slots():
    keywords = build_kdp_keywords(
        _project(),
        llm_phrases=[f"long tail phrase {i}" for i in range(LLM_KEYWORDS_MAX_SLOTS)],
    )
    assert len(keywords) <= KDP_KEYWORD_SLOTS


def test_llm_phrases_ignore_non_string_items():
    keywords = build_kdp_keywords(
        _project(),
        llm_phrases=["echte phrase hier", 123, None, "zweite echte phrase"],  # type: ignore[list-item]
    )
    llm_slots = [k for k in keywords if k.source == KDP_KEYWORD_SOURCE_LLM]
    assert len(llm_slots) == 2


# --- validate_llm_keywords (anti-hype quality gate) ----------------------


def test_validate_keeps_clean_phrases_in_order():
    result = validate_llm_keywords(
        ["liquiditaet planen mittelstand", "cfo monatsabschluss schritt"]
    )
    assert isinstance(result, KeywordQualityResult)
    assert result.accepted == (
        "liquiditaet planen mittelstand",
        "cfo monatsabschluss schritt",
    )
    assert result.rejected == ()


def test_validate_rejects_hype_lemma():
    result = validate_llm_keywords(["garantiert mehr umsatz", "saubere phrase hier"])
    assert result.accepted == ("saubere phrase hier",)
    assert result.rejected == (("garantiert mehr umsatz", "contains_hype:garantiert"),)


def test_validate_rejects_umlaut_hype_lemma():
    # "revolutionär" lives in the shared token list next to its ae/oe fold,
    # so the gate catches it on the RAW phrase before normalization folds it.
    result = validate_llm_keywords(["revolutionäre methode buch"])
    assert result.accepted == ()
    assert result.rejected[0][1].startswith("contains_hype:")


def test_validate_rejects_exclamation():
    result = validate_llm_keywords(["jetzt kaufen!"])
    assert result.accepted == ()
    assert result.rejected == (("jetzt kaufen!", "contains_exclamation"),)


def test_validate_skips_empty_and_non_strings():
    result = validate_llm_keywords(["", "   ", 7, None, "echte phrase"])  # type: ignore[list-item]
    assert result.accepted == ("echte phrase",)
    # Empty / whitespace-only are skipped silently; non-strings are recorded.
    assert ("7", "non_string") in result.rejected
    assert ("None", "non_string") in result.rejected


def test_validate_trims_surviving_phrases():
    result = validate_llm_keywords(["  phrase mit rand  "])
    assert result.accepted == ("phrase mit rand",)


def test_validate_result_is_immutable():
    result = validate_llm_keywords(["saubere phrase hier"])
    assert isinstance(result.accepted, tuple)
    assert isinstance(result.rejected, tuple)


def test_validate_hype_tokens_cover_known_lemmas():
    # Spot-check the shared contract is wired through.
    assert "garantiert" in LLM_KEYWORDS_HYPE_TOKENS
    assert "ultimativ" in LLM_KEYWORDS_HYPE_TOKENS


def test_extract_filters_hype_phrases():
    def completer(system: str, user: str) -> dict:
        return {"keywords": ["garantiert mehr umsatz", "liquiditaet planen mittelstand"]}

    result = extract_kdp_keywords_via_llm(_project(), ["Kap 1"], completer)
    assert result == ["liquiditaet planen mittelstand"]


def test_hype_phrase_never_reaches_kdp_slots():
    keywords = build_kdp_keywords(
        _project(),
        llm_phrases=list(
            extract_kdp_keywords_via_llm(
                _project(),
                ["Kap 1"],
                lambda s, u: {"keywords": ["ultimativer ratgeber finanzen"]},
            )
        ),
    )
    assert all(k.source != KDP_KEYWORD_SOURCE_LLM for k in keywords)
    assert all("ultimativ" not in k.text for k in keywords)
