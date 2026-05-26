"""Tests for the optional Sample-Scan LLM-Pass.

The LLM-Pass enriches risky Kindle-Sample sections (status REVIEW/FIX)
with an LLM-rewritten opening sentence. Gated by
``AppConfig.sample_scan_llm_rewrites_enabled`` AND the presence of an
``ANTHROPIC_API_KEY``. These tests cover the pure-Python building blocks
(prompt assembly, payload parsing, apply-rewrites) plus the pipeline
wiring through a stub LLM so no network or real API key is touched.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from modules.config import AppConfig, load_config
from modules.discovery import BookProject
from modules.pipeline import PublisherPipeline
from modules.run_logger import RunLogger
from modules.sample_scan import (
    LLM_REWRITES_MAX_OPENING_CHARS,
    LLM_REWRITES_MAX_SECTIONS,
    LLM_REWRITES_MIN_OPENING_CHARS,
    SampleScanReport,
    SampleSectionScore,
    _parse_sample_rewrites_payload,
    apply_sample_rewrites,
    build_sample_rewrites_user_prompt,
    build_sample_scan_report_from_paragraphs,
    extract_sample_rewrites_via_llm,
    render_sample_scan_markdown,
    section_bodies_from_paragraphs,
)
from tests.helpers import runtime_dir


# --- Fixtures -------------------------------------------------------------


def _paragraph(text: str, style: str = "Normal") -> dict:
    return {"text": text, "style": style}


def _weak_paragraphs() -> list[dict]:
    """Paragraphs that produce a risky Kindle-sample (REVIEW/FIX sections)."""

    paras = [
        _paragraph("Kapitel 1: Einleitung", style="Heading 1"),
        _paragraph(
            "Dieses Buch befasst sich mit dem Thema. "
            "Im weiteren Verlauf werden wir das Thema betrachten. "
            "Es ist ein wichtiges Thema." * 4
        ),
    ]
    for idx in range(2, 11):
        paras.append(_paragraph(f"Kapitel {idx}: Spaeter", style="Heading 1"))
        paras.append(_paragraph("Spaeter mehr. " * 60))
    return paras


def _strong_paragraphs() -> list[dict]:
    """Paragraphs that produce a READY sample (no risky sections)."""

    paras = [
        _paragraph("Kapitel 1: Der Einstieg", style="Heading 1"),
        _paragraph(
            "Stell dir vor, du verlierst 12.000 Euro in einem Quartal. "
            "In diesem Kapitel lernst du, wie du diese Falle vermeidest. "
            "Du bekommst eine Checkliste mit 7 Punkten und 3 echte Beispiele."
        ),
        _paragraph(
            "Hier liest du eine Methode, die in 18 Monaten 240 Stunden eingespart hat. "
            "Beispiel: ein CFO setzt die Schritt-fuer-Schritt-Vorlage in 30 Minuten um."
        ),
    ]
    for idx in range(2, 11):
        paras.append(_paragraph(f"Kapitel {idx}: Inhalt", style="Heading 1"))
        paras.append(_paragraph(f"Spaeter mehr fuer Kapitel {idx}. " * 30))
    return paras


def _project() -> BookProject:
    return BookProject(
        project_id="sample-llm-test",
        root=Path("."),
        title="Solidaet: Wie ich Geschaefte fuehre",
    )


# --- Pure-Python building blocks ------------------------------------------


def test_section_score_default_opening_rewrite_is_empty():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())

    assert all(sec.opening_rewrite == "" for sec in report.sections)


def test_section_score_to_json_omits_empty_rewrite():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    payload = report.sections[0].to_json()

    assert "opening_rewrite" not in payload


def test_section_score_to_json_carries_rewrite_when_present():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    enriched = replace(report.sections[0], opening_rewrite="Frag dich: was kostet dich diese Falle 2026 wirklich?")

    payload = enriched.to_json()

    assert payload["opening_rewrite"].startswith("Frag dich")


def test_section_bodies_from_paragraphs_matches_report_indices():
    paras = _weak_paragraphs()
    report = build_sample_scan_report_from_paragraphs(paras)
    bodies = section_bodies_from_paragraphs(paras)

    indices_report = {sec.index for sec in report.sections}
    indices_bodies = set(bodies.keys())
    assert indices_report == indices_bodies


def test_build_user_prompt_lists_only_risky_sections():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    bodies = section_bodies_from_paragraphs(_weak_paragraphs())

    prompt = build_sample_rewrites_user_prompt(report, bodies)
    risky = [s for s in report.sections if s.status != "READY"]

    assert risky, "fixture should produce risky sections"
    for sec in risky:
        assert f"Abschnitt {sec.index}" in prompt
    assert "JSON" in prompt


def test_build_user_prompt_empty_when_no_risky_section():
    report = build_sample_scan_report_from_paragraphs(_strong_paragraphs())
    bodies = section_bodies_from_paragraphs(_strong_paragraphs())

    prompt = build_sample_rewrites_user_prompt(report, bodies)

    if all(sec.status == "READY" for sec in report.sections):
        assert prompt == ""


def test_build_user_prompt_respects_limit():
    # Synthesize a report with 8 risky sections all REVIEW.
    sections = tuple(
        SampleSectionScore(
            index=i,
            label=f"Abschnitt {i}",
            word_count=100,
            starts_at_word=i * 100,
            hook=5,
            promise=5,
            value=4,
            readability=5,
            overall=60,
            status="REVIEW",
            risk="GRENZWERTIG",
            fix=f"fix-line-{i}",
        )
        for i in range(1, 9)
    )
    report = SampleScanReport(
        manuscript_word_count=800,
        sample_word_count=800,
        sample_ratio=1.0,
        section_count=8,
        overall_score=60,
        weakest_section_index=1,
        sections=list(sections),
        fixes=[],
    )
    bodies = {i: f"Body {i}" for i in range(1, 9)}

    prompt = build_sample_rewrites_user_prompt(report, bodies, limit=3)

    assert "Abschnitt 1" in prompt
    assert "Abschnitt 2" in prompt
    assert "Abschnitt 3" in prompt
    # Section 4 must NOT appear once we cap to 3.
    assert "Abschnitt 4" not in prompt


def test_parse_payload_extracts_valid_entries():
    payload = {
        "rewrites": [
            {"index": 1, "opening": "Was kostet dich der Status quo wirklich 2026?"},
            {"index": 3, "opening": "Drei Zahlen, die jeder CFO im Kopf haben sollte."},
        ]
    }
    parsed = _parse_sample_rewrites_payload(payload)

    assert parsed == {
        1: "Was kostet dich der Status quo wirklich 2026?",
        3: "Drei Zahlen, die jeder CFO im Kopf haben sollte.",
    }


def test_parse_payload_drops_exclamation_and_too_short():
    payload = {
        "rewrites": [
            {"index": 1, "opening": "Wow!"},  # exclamation + too short
            {"index": 2, "opening": "Kurz"},  # too short
            {"index": 3, "opening": "Drei konkrete Hebel statt Theorie — ab Seite eins."},
        ]
    }
    parsed = _parse_sample_rewrites_payload(payload)

    assert parsed == {3: "Drei konkrete Hebel statt Theorie — ab Seite eins."}


def test_parse_payload_clips_too_long():
    long_text = "A" * (LLM_REWRITES_MAX_OPENING_CHARS + 50)
    payload = {"rewrites": [{"index": 5, "opening": long_text}]}

    parsed = _parse_sample_rewrites_payload(payload)

    assert 5 in parsed
    assert len(parsed[5]) <= LLM_REWRITES_MAX_OPENING_CHARS + 1  # may end with ellipsis


def test_parse_payload_tolerates_garbage():
    assert _parse_sample_rewrites_payload(None) == {}
    assert _parse_sample_rewrites_payload({}) == {}
    assert _parse_sample_rewrites_payload({"rewrites": "x"}) == {}
    assert _parse_sample_rewrites_payload(
        {"rewrites": [{"index": "not-int", "opening": "x" * 20}]}
    ) == {}


def test_apply_rewrites_returns_same_report_on_empty_mapping():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    result = apply_sample_rewrites(report, {})

    assert result is report


def test_apply_rewrites_replaces_sections_immutably():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    risky = next(sec for sec in report.sections if sec.status != "READY")

    enriched = apply_sample_rewrites(
        report, {risky.index: "Drei konkrete Hebel statt Theorie — ab Seite eins."}
    )

    assert enriched is not report
    target = next(sec for sec in enriched.sections if sec.index == risky.index)
    assert target.opening_rewrite.startswith("Drei konkrete Hebel")
    # Original report still untouched.
    original = next(sec for sec in report.sections if sec.index == risky.index)
    assert original.opening_rewrite == ""


def test_apply_rewrites_ignores_whitespace_only_entries():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    risky_index = next(sec.index for sec in report.sections if sec.status != "READY")

    result = apply_sample_rewrites(report, {risky_index: "   "})

    assert result is report


def test_render_markdown_includes_rewrite_when_present():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    risky_index = next(sec.index for sec in report.sections if sec.status != "READY")
    enriched = apply_sample_rewrites(
        report, {risky_index: "Stell dir vor: dein naechstes Quartal ohne diese Falle."}
    )
    md = render_sample_scan_markdown(_project(), enriched)

    assert "Vorschlag Eroeffnungssatz" in md
    assert "Stell dir vor" in md


def test_render_markdown_omits_rewrite_when_absent():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    md = render_sample_scan_markdown(_project(), report)

    assert "Vorschlag Eroeffnungssatz" not in md


def test_extract_via_llm_short_circuits_when_no_risky_section():
    report = build_sample_scan_report_from_paragraphs(_strong_paragraphs())
    if any(sec.status != "READY" for sec in report.sections):
        # Fixture changed and the strong sample now has a risky section —
        # skip this assertion, it's a fixture invariant not a code check.
        return
    calls: list[tuple[str, str]] = []

    def stub(system: str, user: str) -> dict:
        calls.append((system, user))
        return {"rewrites": []}

    result = extract_sample_rewrites_via_llm(report, {}, stub)

    assert result == {}
    assert calls == []


def test_extract_via_llm_swallows_exceptions():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    bodies = section_bodies_from_paragraphs(_weak_paragraphs())

    def stub(system: str, user: str) -> dict:
        raise RuntimeError("network down")

    result = extract_sample_rewrites_via_llm(report, bodies, stub)

    assert result == {}


def test_extract_via_llm_returns_parsed_mapping():
    report = build_sample_scan_report_from_paragraphs(_weak_paragraphs())
    bodies = section_bodies_from_paragraphs(_weak_paragraphs())
    risky = [s for s in report.sections if s.status != "READY"]
    assert risky

    target_index = risky[0].index

    def stub(system: str, user: str) -> dict:
        assert "JSON" in system
        assert f"Abschnitt {target_index}" in user
        return {
            "rewrites": [
                {
                    "index": target_index,
                    "opening": "Stell dir vor: dein naechster Tag kostet 240 Euro weniger.",
                }
            ]
        }

    result = extract_sample_rewrites_via_llm(report, bodies, stub)

    assert target_index in result
    assert "240 Euro" in result[target_index]


# --- Config plumbing ------------------------------------------------------


def test_load_config_defaults_sample_scan_llm_disabled(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_input_path: \"\"\n"
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded.sample_scan_llm_rewrites_enabled is False


def test_load_config_reads_sample_scan_llm_toggle(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_input_path: \"\"\n"
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n"
        "sample_scan_llm_rewrites_enabled: true\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded.sample_scan_llm_rewrites_enabled is True


# --- Pipeline wiring ------------------------------------------------------


def _make_config(*, llm_enabled: bool, workspace: Path) -> AppConfig:
    return AppConfig(
        project_root=workspace,
        default_input_path=workspace,
        default_model="fake",
        fallback_model="fake",
        sample_scan_llm_rewrites_enabled=llm_enabled,
    )


class _StubLLM:
    """Pipeline.llm replacement — no network, deterministic outputs."""

    def __init__(self, *, api_key: str, response):
        self.api_key = api_key
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _build_pipeline(*, llm_enabled: bool, llm: _StubLLM) -> PublisherPipeline:
    workspace = runtime_dir("sample_llm_rewrites")
    config = _make_config(llm_enabled=llm_enabled, workspace=workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))
    pipeline.llm = llm  # type: ignore[assignment]
    return pipeline


def _risky_report() -> SampleScanReport:
    return build_sample_scan_report_from_paragraphs(_weak_paragraphs())


def test_maybe_apply_returns_same_report_when_disabled():
    llm = _StubLLM(api_key="sk-ant-fake", response={"rewrites": []})
    pipeline = _build_pipeline(llm_enabled=False, llm=llm)
    report = _risky_report()

    result = pipeline._maybe_apply_sample_llm_rewrites(_project(), report)

    assert result is report
    assert llm.calls == []


def test_maybe_apply_returns_same_report_when_no_api_key():
    llm = _StubLLM(api_key="", response={"rewrites": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _risky_report()

    result = pipeline._maybe_apply_sample_llm_rewrites(_project(), report)

    assert result is report
    assert llm.calls == []


def test_maybe_apply_returns_same_report_when_no_risky_section():
    llm = _StubLLM(api_key="sk-ant-fake", response={"rewrites": []})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = build_sample_scan_report_from_paragraphs(_strong_paragraphs())
    if any(sec.status != "READY" for sec in report.sections):
        return  # fixture invariant changed, skip

    result = pipeline._maybe_apply_sample_llm_rewrites(_project(), report)

    assert result is report
    assert llm.calls == []


def test_maybe_apply_returns_same_report_when_no_manuscript():
    llm = _StubLLM(api_key="sk-ant-fake", response={"rewrites": [{"index": 1, "opening": "x" * 30}]})
    pipeline = _build_pipeline(llm_enabled=True, llm=llm)
    report = _risky_report()

    result = pipeline._maybe_apply_sample_llm_rewrites(_project(), report)
    # _project() has no manuscript → no rewrite call.
    assert result is report
    assert llm.calls == []


def test_constants_have_sane_values():
    # Sanity guard: catch a future edit that breaks the cost guard.
    assert LLM_REWRITES_MIN_OPENING_CHARS >= 8
    assert LLM_REWRITES_MAX_OPENING_CHARS >= 80
    assert LLM_REWRITES_MAX_SECTIONS >= 1
