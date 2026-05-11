"""End-to-end test for the customer download journey.

A customer downloads BookPublisher.zip from the homepage, extracts it,
and double-clicks the executable. The bundled `release/beispielbuch/`
folder contains the author's real book as a cross-selling demo.

This test simulates exactly that path: a fresh, isolated workspace runs
the pipeline (quick mode, no API key) against the shipped sample book.
It must produce a readable `beginner_summary.md` with traffic-light,
chapter analysis, KDP keywords, Amazon description and a clear next
step - the same output the customer will see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.config import AppConfig
from modules.pipeline import PublisherPipeline
from modules.run_logger import RunLogger
from tests.helpers import runtime_dir


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BOOK_DIR = PROJECT_ROOT / "release" / "beispielbuch"
SAMPLE_MANUSCRIPT = SAMPLE_BOOK_DIR / "Unter_Fuenfzig_Euro.docx"
SAMPLE_METADATA = SAMPLE_BOOK_DIR / "metadata.md"
SAMPLE_READ_ME = SAMPLE_BOOK_DIR / "LIES_MICH.txt"


def _build_config(workspace: Path) -> AppConfig:
    return AppConfig(
        project_root=workspace,
        default_input_path=SAMPLE_BOOK_DIR,
        default_model="fake",
        fallback_model="fake",
        skip_directories={"nicht_hochladen", "redaktion", "kundenausgabe"},
        supplemental_text_directories={"nicht_hochladen", "redaktion"},
        supported_files={
            "manuscripts": [".docx"],
            "text": [".md", ".txt"],
            "pdf": [".pdf"],
            "covers": [".png", ".jpg", ".jpeg"],
            "archives": [".zip"],
        },
    )


def test_sample_book_files_are_shipped_with_repo():
    """Customer ZIP must contain the actual book, metadata and welcome note."""
    assert SAMPLE_BOOK_DIR.is_dir(), (
        "release/beispielbuch/ is missing - the customer ZIP would ship empty."
    )
    assert SAMPLE_MANUSCRIPT.exists(), "Beispielbuch DOCX is missing."
    assert SAMPLE_METADATA.exists(), "Beispielbuch metadata.md is missing."
    assert SAMPLE_READ_ME.exists(), "Beispielbuch LIES_MICH.txt is missing."

    assert SAMPLE_MANUSCRIPT.stat().st_size > 10_000, (
        "Beispielbuch DOCX is suspiciously small - did the manuscript get truncated?"
    )

    metadata_text = SAMPLE_METADATA.read_text(encoding="utf-8")
    assert "Unter Fünfzig Euro" in metadata_text
    assert "## KDP Titel" in metadata_text
    assert "## KDP Untertitel" in metadata_text
    assert "## Amazon Beschreibung" in metadata_text
    assert "aistudioxyz" in metadata_text.lower(), (
        "Cross-selling Amazon-link is missing from metadata.md."
    )


def test_customer_quick_round_produces_beginner_summary():
    """Simulates: customer extracts ZIP, double-clicks, picks beispielbuch folder.

    The quick round (no API key) must complete without exceptions and
    produce a readable beginner summary - that is what the customer
    sees on their very first interaction with the product.
    """
    if not SAMPLE_MANUSCRIPT.exists():
        pytest.skip("Sample manuscript not present in this checkout.")

    workspace = runtime_dir("customer_journey")
    config = _build_config(workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))

    summary = pipeline.run_round(SAMPLE_BOOK_DIR, full_review=False)

    assert summary["mode"] == "quick_qa"
    assert summary["projects"], "Pipeline did not detect the sample book project."

    project_id = summary["projects"][0]["project_id"]
    round_dir = workspace / "artifacts" / "rounds" / project_id / summary["round_id"]
    beginner_path = round_dir / "beginner_summary.md"
    assert beginner_path.exists(), (
        f"Customer would not see a beginner_summary.md after the first run "
        f"(expected at {beginner_path})."
    )

    beginner = beginner_path.read_text(encoding="utf-8")
    assert "Einfache Buch-Pruefung" in beginner
    assert "Ampel" in beginner
    assert "Naechster Klick" in beginner


def test_customer_journey_detects_real_book_metadata():
    """Sample book metadata must round-trip through discovery so the
    customer sees a recognisable, populated report (not 'Unknown title')."""
    if not SAMPLE_MANUSCRIPT.exists():
        pytest.skip("Sample manuscript not present in this checkout.")

    workspace = runtime_dir("customer_journey_meta")
    config = _build_config(workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))

    projects = pipeline.discover(SAMPLE_BOOK_DIR)

    assert len(projects) == 1, "Sample folder must be detected as exactly one project."
    project = projects[0]
    assert project.manuscript is not None
    assert project.manuscript.name == "Unter_Fuenfzig_Euro.docx"
    assert project.title is not None and "Unter" in project.title
    assert project.subtitle is not None and len(project.subtitle) > 10
    assert project.author is not None and "Steiner" in project.author
    assert project.amazon_description is not None
    assert len(project.amazon_description) > 80


def test_customer_quick_round_runs_without_api_key(monkeypatch):
    """The first-contact flow must work even if the user never opened .env."""
    if not SAMPLE_MANUSCRIPT.exists():
        pytest.skip("Sample manuscript not present in this checkout.")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = runtime_dir("customer_journey_nokey")
    config = _build_config(workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))
    pipeline.llm.api_key = ""

    summary = pipeline.run_round(SAMPLE_BOOK_DIR, full_review=False)

    assert summary["projects"], "Quick round must succeed without API key."
    project_id = summary["projects"][0]["project_id"]
    round_dir = workspace / "artifacts" / "rounds" / project_id / summary["round_id"]
    assert (round_dir / "beginner_summary.md").exists()
    assert (round_dir / "industrial_qa_report.md").exists()


def test_customer_quick_round_writes_cross_sell_assets():
    """The customer's first run produces concrete copy-paste-ready KDP
    assets: keywords, Amazon-HTML, rewrite suggestions, chapter scoring.
    This is what hooks the customer into the upsell."""
    if not SAMPLE_MANUSCRIPT.exists():
        pytest.skip("Sample manuscript not present in this checkout.")

    workspace = runtime_dir("customer_journey_assets")
    config = _build_config(workspace)
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))

    summary = pipeline.run_round(SAMPLE_BOOK_DIR, full_review=False)
    project_id = summary["projects"][0]["project_id"]
    round_dir = workspace / "artifacts" / "rounds" / project_id / summary["round_id"]

    expected_artifacts = {
        "beginner_summary.md",
        "industrial_qa_report.md",
        "kindle_preview_check.md",
        "amazon_research_brief.md",
        "competitor_research_template.csv",
        "kdp_keywords.md",
        "amazon_description.html",
        "rewrite_suggestions.md",
        "sample_scan.md",
    }
    present = {path.name for path in round_dir.iterdir() if path.is_file()}
    missing = expected_artifacts - present
    assert not missing, (
        f"Customer would not see these expected assets after the first run: {missing}"
    )
