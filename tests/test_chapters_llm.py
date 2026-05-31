"""Tests for the optional per-chapter LLM-fix-enrichment pass.

All functions under test are pure: they take a ChapterReport plus a
``{index: body}`` mapping and an injected ``llm_completer`` callable, so
no DOCX, filesystem, or network access is required. The pipeline wiring
(gates + happy path) is covered separately in test_pipeline_chapter_llm.py.
"""

from __future__ import annotations

from modules.chapters import (
    FIX_SOURCE_LLM,
    LLM_FIXES_MAX_CHAPTERS,
    LLM_FIXES_MAX_FIX_CHARS,
    LLM_FIXES_MIN_FIX_CHARS,
    ChapterReport,
    ChapterScore,
    apply_chapter_fixes,
    build_chapter_fixes_user_prompt,
    chapter_bodies_from_paragraphs,
    extract_chapter_fixes_via_llm,
    render_chapter_report_markdown,
    _parse_chapter_fixes_payload,
)


def _score(
    index: int,
    *,
    overall: int,
    status: str,
    title: str = "Kapitel",
    promise: int = 5,
    proof: int = 2,
    value: int = 5,
    transition: int = 5,
    llm_fix: str = "",
    fix_source: str = "",
) -> ChapterScore:
    return ChapterScore(
        index=index,
        title=title,
        word_count=500,
        promise=promise,
        proof=proof,
        value=value,
        transition=transition,
        overall=overall,
        status=status,
        fix=f"Heuristischer Fix fuer Kapitel {index}.",
        llm_fix=llm_fix,
        fix_source=fix_source,
    )


def _report(*scores: ChapterScore) -> ChapterReport:
    chapters = list(scores)
    avg = round(sum(s.overall for s in chapters) / len(chapters)) if chapters else 0
    weakest = min(chapters, key=lambda s: s.overall).index if chapters else None
    return ChapterReport(
        chapters=chapters,
        average_score=avg,
        weakest_chapter_index=weakest,
        fixes=[s.fix for s in chapters if s.status != "READY"],
    )


# --- ChapterScore field + JSON --------------------------------------------


def test_chapter_score_defaults_llm_fix_empty():
    score = _score(1, overall=90, status="READY")
    assert score.llm_fix == ""
    assert score.fix_source == ""
    payload = score.to_json()
    assert "llm_fix" not in payload
    assert "fix_source" not in payload


def test_chapter_score_json_includes_llm_fix_when_set():
    score = _score(
        1, overall=50, status="FIX", llm_fix="Bring eine Zahl in Absatz 2.",
        fix_source=FIX_SOURCE_LLM,
    )
    payload = score.to_json()
    assert payload["llm_fix"] == "Bring eine Zahl in Absatz 2."
    assert payload["fix_source"] == FIX_SOURCE_LLM


def test_chapter_score_json_omits_source_without_fix():
    score = _score(1, overall=50, status="FIX", fix_source=FIX_SOURCE_LLM)
    payload = score.to_json()
    assert "llm_fix" not in payload
    assert "fix_source" not in payload


# --- Prompt builder --------------------------------------------------------


def test_prompt_lists_only_weak_chapters():
    report = _report(
        _score(1, overall=92, status="READY", title="Starkes Kapitel"),
        _score(2, overall=48, status="FIX", title="Schwaches Kapitel"),
    )
    bodies = {1: "Sehr guter Text.", 2: "Schwacher Text ohne Beweis."}
    prompt = build_chapter_fixes_user_prompt(report, bodies)
    assert "Schwaches Kapitel" in prompt
    assert "Starkes Kapitel" not in prompt


def test_prompt_empty_when_no_weak_chapters():
    report = _report(_score(1, overall=90, status="READY"))
    assert build_chapter_fixes_user_prompt(report, {1: "x"}) == ""


def test_prompt_respects_limit_and_sorts_worst_first():
    report = _report(
        _score(1, overall=60, status="REVIEW", title="K1"),
        _score(2, overall=40, status="FIX", title="K2"),
        _score(3, overall=50, status="FIX", title="K3"),
    )
    bodies = {1: "a", 2: "b", 3: "c"}
    prompt = build_chapter_fixes_user_prompt(report, bodies, limit=2)
    # Worst (40) and second-worst (50) included; the 60 chapter is dropped.
    assert "K2" in prompt
    assert "K3" in prompt
    assert "K1" not in prompt
    # Worst chapter listed before the second-worst.
    assert prompt.index("K2") < prompt.index("K3")


def test_prompt_includes_weakest_dimension_label():
    report = _report(
        _score(1, overall=45, status="FIX", proof=1, promise=8, value=8, transition=8),
    )
    prompt = build_chapter_fixes_user_prompt(report, {1: "Text"})
    # proof is the weakest dimension → German label "Beweis".
    assert "Beweis" in prompt


# --- Payload parser --------------------------------------------------------


def test_parse_valid_payload():
    payload = {"fixes": [{"index": 2, "fix": "Setze in Absatz 1 eine konkrete Zahl ein."}]}
    assert _parse_chapter_fixes_payload(payload) == {
        2: "Setze in Absatz 1 eine konkrete Zahl ein."
    }


def test_parse_drops_exclamation_and_too_short():
    payload = {
        "fixes": [
            {"index": 1, "fix": "Mach es besser!"},
            {"index": 2, "fix": "kurz"},
            {"index": 3, "fix": "Verankere das Kapitel mit einer echten Fallstudie."},
        ]
    }
    result = _parse_chapter_fixes_payload(payload)
    assert 1 not in result  # exclamation mark
    assert 2 not in result  # under min length
    assert 3 in result


def test_parse_clips_too_long():
    long_fix = "A" * (LLM_FIXES_MAX_FIX_CHARS + 50)
    payload = {"fixes": [{"index": 1, "fix": long_fix}]}
    result = _parse_chapter_fixes_payload(payload)
    assert len(result[1]) <= LLM_FIXES_MAX_FIX_CHARS + 1  # +1 for the ellipsis
    assert result[1].endswith("…")


def test_parse_tolerates_garbage():
    assert _parse_chapter_fixes_payload(None) == {}
    assert _parse_chapter_fixes_payload({"nope": []}) == {}
    assert _parse_chapter_fixes_payload({"fixes": "not a list"}) == {}
    assert _parse_chapter_fixes_payload(
        {"fixes": [{"index": "x", "fix": "valid enough fix here"}]}
    ) == {}
    assert _parse_chapter_fixes_payload(
        {"fixes": [{"index": 1, "fix": 42}]}
    ) == {}


def test_min_fix_chars_bound_is_sane():
    # Regression guard: the min/max thresholds must stay ordered so the
    # parser never accepts garbage or rejects everything.
    assert 0 < LLM_FIXES_MIN_FIX_CHARS < LLM_FIXES_MAX_FIX_CHARS
    assert LLM_FIXES_MAX_CHAPTERS >= 1


# --- apply_chapter_fixes (immutability) ------------------------------------


def test_apply_returns_same_instance_for_empty_mapping():
    report = _report(_score(1, overall=50, status="FIX"))
    assert apply_chapter_fixes(report, {}) is report


def test_apply_creates_new_immutable_report():
    original = _report(
        _score(1, overall=50, status="FIX", title="K1"),
        _score(2, overall=90, status="READY", title="K2"),
    )
    enriched = apply_chapter_fixes(original, {1: "Bring eine konkrete Zahl in Absatz 1."})
    # Original untouched.
    assert original.chapters[0].llm_fix == ""
    assert original.chapters[0].fix_source == ""
    # New report carries the fix + source.
    assert enriched is not original
    assert enriched.chapters[0].llm_fix == "Bring eine konkrete Zahl in Absatz 1."
    assert enriched.chapters[0].fix_source == FIX_SOURCE_LLM
    # Chapter without a fix is unchanged.
    assert enriched.chapters[1].llm_fix == ""


def test_apply_no_change_returns_same_instance():
    report = _report(_score(1, overall=50, status="FIX"))
    # whitespace-only payload contributes nothing → no allocation.
    assert apply_chapter_fixes(report, {1: "   "}) is report


def test_apply_unknown_source_falls_back_to_llm():
    report = _report(_score(1, overall=50, status="FIX"))
    enriched = apply_chapter_fixes(report, {1: "Konkrete Fallstudie ergaenzen."}, source="bogus")
    assert enriched.chapters[0].fix_source == FIX_SOURCE_LLM


# --- extract_chapter_fixes_via_llm (injected completer) --------------------


def test_extract_short_circuits_without_weak_chapters():
    calls: list = []

    def completer(system: str, user: str) -> dict:
        calls.append((system, user))
        return {"fixes": []}

    report = _report(_score(1, overall=90, status="READY"))
    assert extract_chapter_fixes_via_llm(report, {1: "x"}, completer) == {}
    assert calls == []  # never called the LLM


def test_extract_swallows_exceptions():
    def completer(system: str, user: str) -> dict:
        raise RuntimeError("boom")

    report = _report(_score(1, overall=50, status="FIX"))
    assert extract_chapter_fixes_via_llm(report, {1: "x"}, completer) == {}


def test_extract_returns_parsed_mapping():
    def completer(system: str, user: str) -> dict:
        return {"fixes": [{"index": 1, "fix": "Verankere das Kapitel mit einer Zahl."}]}

    report = _report(_score(1, overall=50, status="FIX"))
    result = extract_chapter_fixes_via_llm(report, {1: "Schwacher Text"}, completer)
    assert result == {1: "Verankere das Kapitel mit einer Zahl."}


# --- chapter_bodies_from_paragraphs ---------------------------------------


def test_chapter_bodies_indices_match_report():
    paragraphs = [
        {"text": "Kapitel 1", "style": "Heading 1"},
        {"text": "Das ist der Koerper des ersten Kapitels. " * 20, "style": "Normal"},
        {"text": "Kapitel 2", "style": "Heading 1"},
        {"text": "Der zweite Kapitelkoerper mit ganz anderem Inhalt. " * 20, "style": "Normal"},
    ]
    bodies = chapter_bodies_from_paragraphs(paragraphs)
    assert set(bodies.keys()) == {1, 2}
    assert "ersten Kapitels" in bodies[1]
    assert "zweite Kapitelkoerper" in bodies[2]


# --- markdown rendering ----------------------------------------------------


def test_markdown_shows_llm_fix_line():
    report = _report(
        _score(
            1, overall=50, status="FIX",
            llm_fix="Bring in Absatz 2 eine konkrete Zahl.", fix_source=FIX_SOURCE_LLM,
        )
    )
    md = render_chapter_report_markdown("Mein Buch", report)
    assert "- LLM-Fix: Bring in Absatz 2 eine konkrete Zahl." in md


def test_markdown_omits_llm_fix_line_when_empty():
    report = _report(_score(1, overall=50, status="FIX"))
    md = render_chapter_report_markdown("Mein Buch", report)
    assert "LLM-Fix" not in md
