from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.artifacts import safe_slug
from modules.discovery import BookProject


QUICK_ROUND_ARTIFACTS = [
    "beginner_summary.md",
    "industrial_qa_report.md",
    "industrial_qa_report.json",
    "kindle_preview_check.md",
    "amazon_research_brief.md",
    "competitor_research_template.csv",
    "cover_review.md",
    "agent_memory_snapshot.json",
]

FULL_ROUND_ARTIFACTS = [
    *QUICK_ROUND_ARTIFACTS,
    "manuscript_review.md",
    "manuscript_score.json",
    "voice_preservation_report.md",
    "amazon_conversion_review.md",
    "publisher_board_review.md",
    "kdp_publish_checklist.md",
    "launch_content.md",
    "final_publisher_summary.md",
]


def make_round_id() -> str:
    return datetime.now().strftime("round_%Y%m%d_%H%M%S")


def snapshot_round(
    artifact_dir: Path,
    projects: list[BookProject],
    round_id: str,
    mode: str,
) -> dict[str, Any]:
    round_root = artifact_dir / "rounds"
    copied: list[dict[str, str]] = []

    for project in projects:
        project_source = artifact_dir / safe_slug(project.project_id)
        project_target = round_root / safe_slug(project.project_id) / round_id
        project_target.mkdir(parents=True, exist_ok=True)

        artifact_names = FULL_ROUND_ARTIFACTS if mode == "full_review" else QUICK_ROUND_ARTIFACTS
        for filename in artifact_names:
            source = project_source / filename
            if source.exists():
                target = project_target / filename
                shutil.copy2(source, target)
                copied.append({"project_id": project.project_id, "file": str(target)})

        for filename in ["discovery_report.md", "discovery_report.json"]:
            source = artifact_dir / filename
            if source.exists():
                target = project_target / filename
                shutil.copy2(source, target)
                copied.append({"project_id": project.project_id, "file": str(target)})

    summary = {
        "round_id": round_id,
        "mode": mode,
        "project_count": len(projects),
        "projects": [project.to_json() for project in projects],
        "files": copied,
    }
    summary_path = round_root / round_id / "round_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return summary
