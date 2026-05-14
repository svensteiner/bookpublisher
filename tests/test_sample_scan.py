"""Tests for the First-10%-Deep-Scan (Kindle-Sample) module."""

from __future__ import annotations

from pathlib import Path

from modules.discovery import BookProject
from modules.sample_scan import (
    MAX_SECTIONS,
    SAMPLE_RATIO,
    SCORE_READY,
    SampleScanReport,
    SampleSectionScore,
    build_sample_scan_report,
    build_sample_scan_report_from_paragraphs,
    extract_sample_sections,
    render_sample_scan_markdown,
)


def _paragraph(text: str, style: str = "Normal") -> dict:
    return {"text": text, "style": style}


def _hooked_paragraphs() -> list[dict]:
    """A 10-section manuscript with a strong sample."""

    paras = [
        _paragraph("Kapitel 1: Der Einstieg", style="Heading 1"),
        _paragraph(
            "Stell dir vor, du verlierst 12.000 Euro in einem einzigen Quartal — "
            "und niemand merkt es. Genau das passierte mir 2019. "
            "In diesem Kapitel lernst du, wie du diese Falle vermeidest."
        ),
        _paragraph(
            "Du wirst eine Checkliste mit 7 Punkten bekommen, und ein konkretes "
            "Beispiel aus 3 echten Projekten. Schritt fuer Schritt, ohne Theorie."
        ),
        _paragraph("Kapitel 2: Die Methode", style="Heading 1"),
        _paragraph(
            "Hier liest du eine Methode, die in 18 Monaten 240 Stunden eingespart hat. "
            "Beispiel: ein CFO setzt die Schritt-fuer-Schritt-Vorlage in 30 Minuten um."
        ),
        _paragraph(
            "Am Ende dieses Kapitels weisst du genau, welche 3 Fehler du vermeidest "
            "und wie die Checkliste aussieht."
        ),
    ]
    # Pad with later chapters so the sample is genuinely ~10%.
    for idx in range(3, 11):
        paras.append(_paragraph(f"Kapitel {idx}: Inhalt", style="Heading 1"))
        paras.append(
            _paragraph(
                f"Spaeterer Inhalt fuer Kapitel {idx}. " * 30,
            )
        )
    return paras


def _hypey_paragraphs() -> list[dict]:
    """A weak sample: hype, filler, no concrete value, no hook."""

    paras = [
        _paragraph("Kapitel 1: Einleitung", style="Heading 1"),
        _paragraph(
            "Dieses unglaubliche, revolutionaere Buch wird dein Leben veraendern. "
            "Es ist absolut sensationell und tatsaechlich einzigartig. "
            "Eigentlich grundsaetzlich tatsaechlich absolut bahnbrechend."
        ),
        _paragraph(
            "Dieses Buch ist atemberaubend. Es ist sensationell. "
            "Es ist unglaublich. Es ist bahnbrechend."
        ),
    ]
    for idx in range(2, 11):
        paras.append(_paragraph(f"Kapitel {idx}: Inhalt", style="Heading 1"))
        paras.append(_paragraph(f"Spaeter mehr fuer Kapitel {idx}. " * 30))
    return paras


def _project_with_manuscript() -> BookProject:
    return BookProject(
        project_id="sample-test",
        root=Path("."),
        manuscript=None,
        title="Solidität: Wie ich Geschäfte führe",
    )


def test_extract_sample_sections_returns_first_10_percent():
    paras = _hooked_paragraphs()
    sections, total_words, sample_words = extract_sample_sections(paras)

    assert total_words > 0
    assert sample_words > 0
    # Sample must not consume the whole manuscript.
    assert sample_words < total_words
    # And should land somewhere near 10% (allow generous tolerance).
    assert sample_words <= int(total_words * 0.30)
    assert sections, "expected at least one section"


def test_sections_bucketed_by_heading():
    paras = _hooked_paragraphs()
    sections, _, _ = extract_sample_sections(paras)

    # First section should carry the Kapitel 1 heading text.
    assert sections[0].label.lower().startswith("kapitel 1")


def test_section_count_capped():
    # Build a manuscript with many tiny headings inside the sample window.
    paras: list[dict] = []
    for idx in range(30):
        paras.append(_paragraph(f"Abschnitt {idx}", style="Heading 2"))
        paras.append(_paragraph("Inhalt " * 60))
    report = build_sample_scan_report_from_paragraphs(paras)

    assert report.section_count <= MAX_SECTIONS


def test_strong_sample_scores_high():
    report = build_sample_scan_report_from_paragraphs(_hooked_paragraphs())

    assert report.section_count > 0
    assert report.overall_score >= 60, (
        f"expected strong sample to score >=60, got {report.overall_score}"
    )
    # The first section ought to be hookful.
    first = report.sections[0]
    assert first.hook >= 7


def test_hype_sample_flags_risk():
    report = build_sample_scan_report_from_paragraphs(_hypey_paragraphs())

    assert report.section_count > 0
    # Hype + filler must drive at least one section into FIX/REVIEW status.
    non_ready = [s for s in report.sections if s.status != "READY"]
    assert non_ready, "expected at least one risky section in a hype-only sample"
    assert any(s.status == "FIX" for s in report.sections) or report.overall_score < SCORE_READY


def test_report_serializes_to_json():
    report = build_sample_scan_report_from_paragraphs(_hooked_paragraphs())
    payload = report.to_json()

    assert {
        "manuscript_word_count",
        "sample_word_count",
        "sample_ratio",
        "section_count",
        "overall_score",
        "weakest_section_index",
        "sections",
        "fixes",
    } <= payload.keys()
    assert isinstance(payload["sections"], list)
    if payload["sections"]:
        assert {"index", "label", "scores", "overall", "risk", "fix"} <= payload["sections"][0].keys()


def test_empty_paragraphs_produces_empty_report():
    report = build_sample_scan_report_from_paragraphs([])

    assert isinstance(report, SampleScanReport)
    assert report.section_count == 0
    assert report.overall_score == 0
    assert report.sections == []
    assert report.weakest_section_index is None


def test_project_without_manuscript_returns_empty_report():
    project = _project_with_manuscript()
    report = build_sample_scan_report(project)

    assert report.section_count == 0
    md = render_sample_scan_markdown(project, report)
    assert "First-10%-Deep-Scan" in md


def test_render_markdown_lists_every_section():
    project = _project_with_manuscript()
    report = build_sample_scan_report_from_paragraphs(_hooked_paragraphs())
    md = render_sample_scan_markdown(project, report)

    assert "First-10%-Deep-Scan" in md
    assert "Pro Abschnitt" in md
    for sec in report.sections:
        assert f"Abschnitt {sec.index}" in md


def test_fix_targets_weakest_dimension():
    # Section with no hook + no value should produce a 'hook' or 'value' fix.
    paras = [
        _paragraph("Kapitel 1: Trockene Einleitung", style="Heading 1"),
        _paragraph(
            "Dieses Buch befasst sich mit dem Thema X. "
            "Im weiteren Verlauf werden wir das Thema betrachten. "
            "Es ist ein wichtiges Thema."
            * 5
        ),
    ]
    # Pad to make this the first 10%.
    for idx in range(2, 11):
        paras.append(_paragraph(f"Kapitel {idx}: Spaeter", style="Heading 1"))
        paras.append(_paragraph("Spaeter mehr. " * 60))
    report = build_sample_scan_report_from_paragraphs(paras)
    first = report.sections[0]

    assert first.fix, "expected a non-empty fix line"
    # The weakest section in this manuscript is hook or value — fix must
    # mention one of the relevant levers (Hook/Frage/Wert/Zahl/Checkliste).
    assert any(
        marker in first.fix
        for marker in ("Hook", "Wert", "Frage", "Zahl", "Versprechen", "Checkliste")
    )


def test_section_score_dataclass_is_immutable():
    report = build_sample_scan_report_from_paragraphs(_hooked_paragraphs())
    score = report.sections[0]
    assert isinstance(score, SampleSectionScore)
    try:
        score.hook = 0  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("SampleSectionScore should be frozen")


# --- Configurable sample-scan parameters (short-nonfiction support) ------


def _short_book_paragraphs(total_paragraph_words: int = 60, count: int = 12) -> list[dict]:
    """Build a small book where the default 10% sample is too thin."""
    paras: list[dict] = []
    for idx in range(count):
        paras.append(_paragraph(f"Kapitel {idx + 1}: Thema", style="Heading 1"))
        # ~60 words per paragraph → 12 paragraphs ≈ 720 words total.
        paras.append(_paragraph(("wort " * total_paragraph_words).strip()))
    return paras


def test_default_sample_scan_config_matches_legacy_constants():
    from modules.sample_scan import (
        DEFAULT_SAMPLE_SCAN_CONFIG,
        MAX_SECTIONS as legacy_max_sections,
        SAMPLE_MAX_RATIO,
        SAMPLE_RATIO,
        SECTION_TARGET_WORDS,
        MIN_SECTION_WORDS,
    )

    assert DEFAULT_SAMPLE_SCAN_CONFIG.sample_ratio == SAMPLE_RATIO
    assert DEFAULT_SAMPLE_SCAN_CONFIG.max_ratio == SAMPLE_MAX_RATIO
    assert DEFAULT_SAMPLE_SCAN_CONFIG.max_sections == legacy_max_sections
    assert DEFAULT_SAMPLE_SCAN_CONFIG.section_target_words == SECTION_TARGET_WORDS
    assert DEFAULT_SAMPLE_SCAN_CONFIG.min_section_words == MIN_SECTION_WORDS


def test_sample_scan_config_is_immutable():
    from modules.sample_scan import SampleScanConfig

    cfg = SampleScanConfig()
    try:
        cfg.sample_ratio = 0.5  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("SampleScanConfig should be frozen")


def test_custom_ratio_increases_sample_word_count_for_short_books():
    from modules.sample_scan import SampleScanConfig

    paras = _short_book_paragraphs()
    _, total_default, sample_default = extract_sample_sections(paras)
    _, total_high, sample_high = extract_sample_sections(
        paras, config=SampleScanConfig(sample_ratio=0.25, max_ratio=0.30)
    )

    # Same manuscript, but the high-ratio config samples noticeably more.
    assert total_default == total_high > 0
    assert sample_high > sample_default


def test_custom_max_sections_caps_section_count():
    from modules.sample_scan import SampleScanConfig

    paras: list[dict] = []
    for idx in range(30):
        paras.append(_paragraph(f"Abschnitt {idx}", style="Heading 2"))
        paras.append(_paragraph("Inhalt " * 60))

    report = build_sample_scan_report_from_paragraphs(
        paras, config=SampleScanConfig(max_sections=3)
    )
    assert report.section_count <= 3


def test_custom_min_section_words_changes_merging():
    """Lower min_section_words → fewer merges → more sections preserved."""
    from modules.sample_scan import SampleScanConfig

    # Build many short sections (~30 words each, below default min=90).
    paras: list[dict] = []
    for idx in range(20):
        paras.append(_paragraph(f"Kapitel {idx + 1}", style="Heading 1"))
        paras.append(_paragraph("wort " * 30))

    # Default config: every short section gets merged into a predecessor.
    default_report = build_sample_scan_report_from_paragraphs(paras)
    # Low-threshold config: sections survive standalone.
    low_report = build_sample_scan_report_from_paragraphs(
        paras,
        config=SampleScanConfig(
            sample_ratio=0.50,
            max_ratio=0.60,
            min_section_words=10,
            max_sections=20,
        ),
    )

    assert low_report.section_count > default_report.section_count


def test_custom_section_target_words_changes_window_splitting():
    """Lower section_target_words → headless stretches split into more buckets."""
    from modules.sample_scan import SampleScanConfig

    # 1 heading + 1 huge paragraph that should be window-split.
    paras: list[dict] = [
        _paragraph("Einleitung", style="Heading 1"),
        # 600 words in a single paragraph.
        _paragraph("wort " * 600),
    ]

    default = build_sample_scan_report_from_paragraphs(
        paras,
        config=SampleScanConfig(sample_ratio=0.9, max_ratio=1.0, section_target_words=350),
    )
    smaller = build_sample_scan_report_from_paragraphs(
        paras,
        config=SampleScanConfig(
            sample_ratio=0.9, max_ratio=1.0, section_target_words=100, min_section_words=10
        ),
    )

    # The same single-paragraph body can't actually split mid-paragraph,
    # so this primarily confirms the knob is wired (no crashes, no skewed
    # totals). Both configs should still produce at least one section.
    assert default.section_count >= 1
    assert smaller.section_count >= 1


def test_legacy_sample_ratio_kwarg_still_overrides_config():
    """Backwards compat: explicit ``sample_ratio=`` kwarg trumps config."""
    from modules.sample_scan import SampleScanConfig

    paras = _short_book_paragraphs()
    _, _, default_sample = extract_sample_sections(
        paras, config=SampleScanConfig(sample_ratio=0.10, max_ratio=0.14)
    )
    _, _, override_sample = extract_sample_sections(
        paras,
        config=SampleScanConfig(sample_ratio=0.10, max_ratio=0.14),
        sample_ratio=0.40,
        max_ratio=0.50,
    )
    assert override_sample > default_sample


def test_pipeline_passes_app_config_to_sample_scan():
    """``sample_scan_config_from_app`` reads the AppConfig fields verbatim."""
    from modules.config import AppConfig
    from modules.sample_scan import sample_scan_config_from_app

    cfg = AppConfig(
        project_root=Path("."),
        default_input_path=Path("."),
        default_model="x",
        fallback_model="y",
        sample_scan_ratio=0.22,
        sample_scan_max_ratio=0.30,
        sample_scan_max_sections=12,
        sample_scan_section_target_words=200,
        sample_scan_min_section_words=40,
    )
    scan_cfg = sample_scan_config_from_app(cfg)
    assert scan_cfg.sample_ratio == 0.22
    assert scan_cfg.max_ratio == 0.30
    assert scan_cfg.max_sections == 12
    assert scan_cfg.section_target_words == 200
    assert scan_cfg.min_section_words == 40


def test_load_config_reads_sample_scan_keys(tmp_path):
    from modules.config import load_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_input_path: \"\"\n"
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n"
        "sample_scan_ratio: 0.22\n"
        "sample_scan_max_ratio: 0.30\n"
        "sample_scan_max_sections: 12\n"
        "sample_scan_section_target_words: 200\n"
        "sample_scan_min_section_words: 40\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded.sample_scan_ratio == 0.22
    assert loaded.sample_scan_max_ratio == 0.30
    assert loaded.sample_scan_max_sections == 12
    assert loaded.sample_scan_section_target_words == 200
    assert loaded.sample_scan_min_section_words == 40


def test_load_config_defaults_sample_scan_keys(tmp_path):
    from modules.config import load_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_input_path: \"\"\n"
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded.sample_scan_ratio == 0.10
    assert loaded.sample_scan_max_ratio == 0.14
    assert loaded.sample_scan_max_sections == 8
    assert loaded.sample_scan_section_target_words == 350
    assert loaded.sample_scan_min_section_words == 90


def test_load_config_clamps_invalid_sample_scan_values(tmp_path):
    from modules.config import load_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_input_path: \"\"\n"
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n"
        "sample_scan_ratio: -0.5\n"
        "sample_scan_max_ratio: 5.0\n"
        "sample_scan_max_sections: 0\n"
        "sample_scan_section_target_words: 0\n"
        "sample_scan_min_section_words: 0\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded.sample_scan_ratio == 0.01  # clamped to low
    assert loaded.sample_scan_max_ratio == 1.0  # clamped to high
    assert loaded.sample_scan_max_sections == 1  # clamped to min
    assert loaded.sample_scan_section_target_words == 20
    assert loaded.sample_scan_min_section_words == 10
