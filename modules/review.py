from __future__ import annotations

import json
from pathlib import Path

from modules.config import AppConfig
from modules.discovery import BookProject
from modules.llm import LLMClient
from modules.prompts import (
    AMAZON_PROMPT,
    CHECKLIST_PROMPT,
    LAUNCH_PROMPT,
    MANUSCRIPT_SCORE_PROMPT,
    PUBLISHER_BOARD_PROMPT,
    SUMMARY_PROMPT,
    SYSTEM_PROMPT,
    VOICE_PROMPT,
)
from modules.readers import read_any_text


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: int(max_chars * 0.65)]
    tail = text[-int(max_chars * 0.35):]
    return head + "\n\n[... MANUSCRIPT TRUNCATED FOR MODEL CONTEXT ...]\n\n" + tail


def project_metadata(project: BookProject) -> str:
    payload = project.to_json()
    return json.dumps(payload, ensure_ascii=False, indent=2)


def load_project_text(project: BookProject, config: AppConfig) -> str:
    chunks: list[str] = []
    if project.manuscript:
        chunks.append("# Manuscript\n" + read_any_text(project.manuscript))
    supplemental = project.metadata_files + project.notes_files[:8]
    seen: set[Path] = set()
    for path in supplemental:
        if path in seen:
            continue
        seen.add(path)
        if path.exists() and path.suffix.lower() in {".md", ".txt"}:
            chunks.append(f"# {path.name}\n" + read_any_text(path))
    return trim_text("\n\n".join(chunks), config.max_manuscript_chars)


def _score_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(1, min(10, round(value)))
    if isinstance(value, str):
        try:
            return max(1, min(10, round(float(value.strip()))))
        except ValueError:
            return None
    return None


def normalize_score_payload(score: dict) -> dict:
    expected_areas = [
        "amazon_purchase_appeal",
        "opening_strength",
        "title_fit",
        "reader_promise",
        "differentiation",
        "pacing",
        "repetition_control",
        "credibility",
        "voice_consistency",
        "business_relevance",
        "structure_and_chapter_logic",
        "sample_page_pull",
        "nonfiction_argument_quality",
        "review_risk",
        "refund_risk",
        "kindle_sample_conversion",
        "ebook_readability",
    ]
    raw_scores = score.get("scores") if isinstance(score.get("scores"), dict) else {}
    normalized_scores: dict[str, int] = {}
    for area in expected_areas:
        value = _score_value(raw_scores.get(area))
        if value is not None:
            normalized_scores[area] = value

    if normalized_scores:
        score["scores"] = normalized_scores
        final = _score_value(score.get("final_score"))
        score["final_score"] = final if final is not None else round(sum(normalized_scores.values()) / len(normalized_scores))
    else:
        score["scores"] = {}
        score["final_score"] = _score_value(score.get("final_score")) or 1

    verdict = str(score.get("verdict", "revise")).lower().strip()
    score["verdict"] = verdict if verdict in {"publish", "revise", "do_not_publish"} else "revise"

    for key in ("top_strengths", "top_risks", "top_fixes"):
        value = score.get(key)
        if not isinstance(value, list):
            score[key] = []
    for key in ("acquisition_note", "reader_positioning", "one_sentence_sales_handle"):
        if not isinstance(score.get(key), str):
            score[key] = ""
    return score


def render_score_markdown(project: BookProject, score: dict) -> str:
    lines = [
        "# Manuscript Review",
        "",
        f"Project: `{project.project_id}`",
        f"Title: {project.title or 'Unknown'}",
        "",
        "## Scores",
        "",
    ]
    for key, value in (score.get("scores") or {}).items():
        lines.append(f"- {key}: **{value}/10**")
    lines.extend([
        "",
        f"Final score: **{score.get('final_score', 'n/a')}/10**",
        f"Verdict: **{score.get('verdict', 'n/a')}**",
        "",
        "## Top Strengths",
        "",
    ])
    lines.extend(f"- {item}" for item in score.get("top_strengths", []))
    lines.extend(["", "## Top Risks", ""])
    lines.extend(f"- {item}" for item in score.get("top_risks", []))
    lines.extend(["", "## Top Fixes", ""])
    lines.extend(f"- {item}" for item in score.get("top_fixes", []))
    lines.extend([
        "",
        "## Publisher Notes",
        "",
        f"- Acquisition note: {score.get('acquisition_note') or 'n/a'}",
        f"- Reader positioning: {score.get('reader_positioning') or 'n/a'}",
        f"- Sales handle: {score.get('one_sentence_sales_handle') or 'n/a'}",
    ])
    return "\n".join(lines)


def manuscript_review(project: BookProject, config: AppConfig, llm: LLMClient) -> tuple[str, dict]:
    text = load_project_text(project, config)
    score = llm.complete_json(
        SYSTEM_PROMPT,
        MANUSCRIPT_SCORE_PROMPT.format(metadata=project_metadata(project), manuscript=text),
    )
    score = normalize_score_payload(score)
    return render_score_markdown(project, score), score


def voice_report(project: BookProject, config: AppConfig, llm: LLMClient) -> str:
    text = load_project_text(project, config)
    return llm.complete(
        SYSTEM_PROMPT,
        VOICE_PROMPT.format(metadata=project_metadata(project), manuscript=text),
    )


def amazon_review(project: BookProject, config: AppConfig, llm: LLMClient) -> str:
    text = load_project_text(project, config)
    return llm.complete(
        SYSTEM_PROMPT,
        AMAZON_PROMPT.format(metadata=project_metadata(project), text=text),
    )


def publisher_board_review(project: BookProject, context: str, llm: LLMClient) -> str:
    return llm.complete(
        SYSTEM_PROMPT,
        PUBLISHER_BOARD_PROMPT.format(context=context),
    )


def checklist(project: BookProject, context: str, llm: LLMClient) -> str:
    return llm.complete(
        SYSTEM_PROMPT,
        CHECKLIST_PROMPT.format(context=context),
    )


def launch_content(project: BookProject, config: AppConfig, llm: LLMClient) -> str:
    text = load_project_text(project, config)
    context = project_metadata(project) + "\n\n" + text
    return llm.complete(
        SYSTEM_PROMPT,
        LAUNCH_PROMPT.format(context=trim_text(context, config.max_manuscript_chars)),
    )


def executive_summary(project: BookProject, context: str, llm: LLMClient) -> str:
    return llm.complete(
        SYSTEM_PROMPT,
        SUMMARY_PROMPT.format(context=context),
    )
