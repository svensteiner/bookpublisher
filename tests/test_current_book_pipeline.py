from pathlib import Path

import pytest

from modules.config import AppConfig
from modules.pipeline import PublisherPipeline
from modules.run_logger import RunLogger
from tests.helpers import runtime_dir


CURRENT_BOOK_PATH = Path(r"C:\Users\svens\OneDrive\Desktop\Buch für Amazon\AI_Studioxyz_KDP\endversion")


class FakePublisherLLM:
    def require_api_key(self) -> None:
        return None

    def complete_json(self, system: str, user: str) -> dict:
        return {
            "scores": {
                "amazon_purchase_appeal": 8,
                "opening_strength": 8,
                "title_fit": 8,
                "reader_promise": 8,
                "differentiation": 9,
                "pacing": 8,
                "repetition_control": 8,
                "credibility": 9,
                "voice_consistency": 9,
                "business_relevance": 9,
                "structure_and_chapter_logic": 8,
                "sample_page_pull": 8,
                "nonfiction_argument_quality": 8,
                "review_risk": 7,
                "refund_risk": 8,
                "kindle_sample_conversion": 8,
                "ebook_readability": 8,
            },
            "final_score": 8,
            "verdict": "publish",
            "top_strengths": ["klare operative Stimme"],
            "top_risks": ["Kindle-Sample muss stark bleiben"],
            "top_fixes": ["Look-Inside und Kindle Previewer final prüfen"],
            "acquisition_note": "GO_AFTER_FIXES",
            "reader_positioning": "Praktiker, die KI operativ bewerten.",
            "one_sentence_sales_handle": "Ein nüchternes Feldbuch über KI-Agenten ohne Hype.",
        }

    def complete(self, system: str, user: str, model: str | None = None) -> str:
        if "editorial board report" in user:
            return "# Publisher Board Review\n\nKindle ebook mechanics checked. Final board decision: GO_AFTER_FIXES."
        if "Amazon conversion review" in user:
            return "# Amazon Conversion Review\n\nKindle sample and product-page conversion checked."
        if "voice preservation report" in user:
            return "# Voice Preservation Report\n\nVoice preserved."
        if "launch assets" in user:
            return "# Launch Content\n\nKindle sample diagnosis: strong enough after final preview."
        if "KDP pre-publish checklist" in user:
            return "# KDP Checklist\n\nKindle Previewer/device QA ready? REVIEW."
        return "# Executive Publisher Summary\n\nPublish after Kindle QA."


def test_current_book_pipeline_with_fake_llm_builds_full_artifact_set():
    if not CURRENT_BOOK_PATH.exists():
        pytest.skip(f"Current book folder not available: {CURRENT_BOOK_PATH}")

    workspace = runtime_dir("current_book_pipeline")
    config = AppConfig(
        project_root=workspace,
        default_input_path=CURRENT_BOOK_PATH,
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
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))
    pipeline.llm = FakePublisherLLM()

    projects = pipeline.run_all(CURRENT_BOOK_PATH)

    assert len(projects) == 1
    assert projects[0].manuscript and projects[0].manuscript.name.endswith("KORRIGIERT.docx")
    assert projects[0].cover and projects[0].cover.name == "Cover_KDP_Final.jpg"
    assert projects[0].amazon_description

    artifact_dir = workspace / "artifacts" / projects[0].project_id
    expected = {
        "manuscript_review.md",
        "manuscript_score.json",
        "voice_preservation_report.md",
        "amazon_conversion_review.md",
        "publisher_board_review.md",
        "industrial_qa_report.md",
        "industrial_qa_report.json",
        "cover_review.md",
        "kdp_publish_checklist.md",
        "launch_content.md",
        "final_publisher_summary.md",
    }
    assert expected.issubset({path.name for path in artifact_dir.iterdir()})
    assert "Kindle" in (artifact_dir / "publisher_board_review.md").read_text(encoding="utf-8")


def test_current_book_industrial_qa_without_llm():
    if not CURRENT_BOOK_PATH.exists():
        pytest.skip(f"Current book folder not available: {CURRENT_BOOK_PATH}")

    workspace = runtime_dir("current_book_qa")
    config = AppConfig(
        project_root=workspace,
        default_input_path=CURRENT_BOOK_PATH,
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
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))

    projects = pipeline.run_qa(CURRENT_BOOK_PATH)

    artifact_dir = workspace / "artifacts" / projects[0].project_id
    qa_json = (artifact_dir / "industrial_qa_report.json").read_text(encoding="utf-8")
    qa_md = (artifact_dir / "industrial_qa_report.md").read_text(encoding="utf-8")

    assert "industrial_score" in qa_json
    assert "kindle_ebook_readiness" in qa_json
    assert "Industrial Publisher QA" in qa_md


def test_current_book_quick_round_creates_snapshot_without_llm():
    if not CURRENT_BOOK_PATH.exists():
        pytest.skip(f"Current book folder not available: {CURRENT_BOOK_PATH}")

    workspace = runtime_dir("current_book_round")
    config = AppConfig(
        project_root=workspace,
        default_input_path=CURRENT_BOOK_PATH,
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
    pipeline = PublisherPipeline(config, RunLogger(workspace / "logs"))

    summary = pipeline.run_round(CURRENT_BOOK_PATH)

    project_id = summary["projects"][0]["project_id"]
    round_dir = workspace / "artifacts" / "rounds" / project_id / summary["round_id"]
    assert summary["mode"] == "quick_qa"
    assert (round_dir / "industrial_qa_report.md").exists()
    assert (round_dir / "cover_review.md").exists()
    assert not (round_dir / "manuscript_review.md").exists()
    assert (workspace / "artifacts" / "latest_round_summary.json").exists()
