from __future__ import annotations

import json
from pathlib import Path

from modules.agent_core import AgentMemory, SkillRegistry
from modules.amazon_html import (
    build_amazon_description_html,
    render_amazon_description_report_markdown,
)
from modules.artifacts import ArtifactWriter
from modules.config import AppConfig
from modules.cover import render_cover_review
from modules.discovery import BookProject, discover_books, render_discovery_markdown
from modules.industrial import build_industrial_qa, render_beginner_summary, render_industrial_qa_markdown
from modules.kdp_keywords import build_kdp_keywords, render_kdp_keywords_report_markdown
from modules.llm import LLMClient
from modules.review import (
    amazon_review,
    chapter_review,
    checklist,
    executive_summary,
    launch_content,
    manuscript_review,
    publisher_board_review,
    project_metadata,
    voice_report,
)
from modules.release_assets import (
    render_amazon_research_brief,
    render_competitor_template_csv,
    render_kindle_preview_check,
)
from modules.rewrites import build_rewrite_report, render_rewrite_report_markdown
from modules.round_delta import render_round_delta_markdown
from modules.rounds import make_round_id, snapshot_round
from modules.run_logger import RunLogger
from modules.sample_scan import build_sample_scan_report, render_sample_scan_markdown
from modules.score_history import (
    append_score_history,
    load_score_history,
    render_score_history_markdown,
)


class PublisherPipeline:
    def __init__(self, config: AppConfig, logger: RunLogger):
        self.config = config
        self.logger = logger
        self.writer = ArtifactWriter(config.project_root / "artifacts", logger)
        self.llm = LLMClient(config, logger)
        skills_dir = config.skills_directory
        if not skills_dir.is_absolute():
            skills_dir = config.project_root / skills_dir
        memory_path = config.memory_path
        if not memory_path.is_absolute():
            memory_path = config.project_root / memory_path
        self.skills = SkillRegistry(skills_dir)
        self.memory = AgentMemory(memory_path)

    def discover(self, input_path: Path) -> list[BookProject]:
        projects = discover_books(
            input_path,
            self.config.skip_directories,
            self.config.supported_files,
            self.config.supplemental_text_directories,
        )
        self.logger.log("discovery_completed", input_path=str(input_path), project_count=len(projects))
        for project in projects:
            self.logger.log("project_discovered", **project.to_json())

        self.writer.write_text("discovery_report.md", render_discovery_markdown(projects, input_path))
        self.writer.write_json("discovery_report.json", {
            "input_path": str(input_path),
            "project_count": len(projects),
            "projects": [project.to_json() for project in projects],
        })
        for project in projects:
            self.memory.remember_project(project)
        self.memory.save()
        return projects

    def _mirror_if_single(self, projects: list[BookProject], filename: str) -> None:
        if self.config.artifact_mirror_single_project and len(projects) == 1:
            self.writer.mirror_single_project_file(projects[0].project_id, filename)

    def run_review(self, input_path: Path) -> list[BookProject]:
        projects = self.discover(input_path)
        if not projects:
            return projects
        self.llm.require_api_key()
        for project in projects:
            agent_context = "\n\n".join([
                self.skills.prompt_context(),
                self.memory.prompt_context(project.project_id),
            ])
            self.logger.log("stage_started", project_id=project.project_id, stage="manuscript_review")
            review_md, score_json = manuscript_review(project, self.config, self.llm)
            self.writer.write_text("manuscript_review.md", review_md, project.project_id)
            self.writer.write_json("manuscript_score.json", score_json, project.project_id)

            self.logger.log("stage_started", project_id=project.project_id, stage="voice_preservation")
            voice_md = voice_report(project, self.config, self.llm)
            self.writer.write_text("voice_preservation_report.md", voice_md, project.project_id)

            self.logger.log("stage_started", project_id=project.project_id, stage="amazon_conversion")
            amazon_md = amazon_review(project, self.config, self.llm)
            self.writer.write_text("amazon_conversion_review.md", amazon_md, project.project_id)

            context = "\n\n".join([agent_context, project_metadata(project), review_md, voice_md, amazon_md])
            self.logger.log("stage_started", project_id=project.project_id, stage="publisher_board_review")
            board_md = publisher_board_review(project, context, self.llm)
            self.writer.write_text("publisher_board_review.md", board_md, project.project_id)

            context = "\n\n".join([context, board_md])
            self.logger.log("stage_started", project_id=project.project_id, stage="kdp_checklist")
            checklist_md = checklist(project, context, self.llm)
            self.writer.write_text("kdp_publish_checklist.md", checklist_md, project.project_id)

            summary_context = "\n\n".join([context, checklist_md])
            self.logger.log("stage_started", project_id=project.project_id, stage="executive_summary")
            summary_md = executive_summary(project, summary_context, self.llm)
            self.writer.write_text("final_publisher_summary.md", summary_md, project.project_id)

        for filename in [
            "manuscript_review.md",
            "manuscript_score.json",
            "voice_preservation_report.md",
            "amazon_conversion_review.md",
            "publisher_board_review.md",
            "kdp_publish_checklist.md",
            "final_publisher_summary.md",
        ]:
            self._mirror_if_single(projects, filename)
        return projects

    def run_cover(self, input_path: Path) -> list[BookProject]:
        projects = self.discover(input_path)
        for project in projects:
            self.logger.log("stage_started", project_id=project.project_id, stage="cover_review")
            self.writer.write_text("cover_review.md", render_cover_review(project), project.project_id)
        self._mirror_if_single(projects, "cover_review.md")
        return projects

    def run_qa(
        self,
        input_path: Path,
        round_id: str | None = None,
        mode: str = "quick_qa",
    ) -> list[BookProject]:
        projects = self.discover(input_path)
        for project in projects:
            self.logger.log("stage_started", project_id=project.project_id, stage="industrial_qa")
            agent_context = {
                "skills": self.skills.to_json(),
                "memory": self.memory.snapshot(project.project_id),
            }
            qa = build_industrial_qa(project, agent_context)
            self.memory.remember_qa(project, qa, round_id=round_id)
            self.memory.save()
            self.writer.write_json("industrial_qa_report.json", qa, project.project_id)
            self.writer.write_text("industrial_qa_report.md", render_industrial_qa_markdown(qa), project.project_id)
            self.writer.write_text("beginner_summary.md", render_beginner_summary(project, qa), project.project_id)
            try:
                chapter_md, chapter_json = chapter_review(project)
                self.writer.write_text("chapter_review.md", chapter_md, project.project_id)
                self.writer.write_json("chapter_review.json", chapter_json, project.project_id)
            except RuntimeError as exc:
                self.logger.log(
                    "chapter_review_skipped",
                    project_id=project.project_id,
                    reason=str(exc),
                )
            self.writer.write_text("kindle_preview_check.md", render_kindle_preview_check(project), project.project_id)
            self.writer.write_text("amazon_research_brief.md", render_amazon_research_brief(project), project.project_id)
            self.writer.write_text("competitor_research_template.csv", render_competitor_template_csv(project), project.project_id)
            rewrite_report = build_rewrite_report(project)
            self.writer.write_text(
                "rewrite_suggestions.md",
                render_rewrite_report_markdown(project, rewrite_report),
                project.project_id,
            )
            self.writer.write_json(
                "rewrite_suggestions.json",
                rewrite_report.to_json(),
                project.project_id,
            )
            amazon_html_snippet = build_amazon_description_html(project)
            self.writer.write_text(
                "amazon_description.html",
                amazon_html_snippet.html,
                project.project_id,
            )
            self.writer.write_text(
                "amazon_description_report.md",
                render_amazon_description_report_markdown(project, amazon_html_snippet),
                project.project_id,
            )
            self.writer.write_json(
                "amazon_description.json",
                amazon_html_snippet.to_json(),
                project.project_id,
            )
            kdp_keywords = build_kdp_keywords(project)
            self.writer.write_text(
                "kdp_keywords.md",
                render_kdp_keywords_report_markdown(project, kdp_keywords),
                project.project_id,
            )
            self.writer.write_json(
                "kdp_keywords.json",
                {"keywords": [kw.to_json() for kw in kdp_keywords]},
                project.project_id,
            )
            try:
                sample_scan = build_sample_scan_report(project)
                self.writer.write_text(
                    "sample_scan.md",
                    render_sample_scan_markdown(project, sample_scan),
                    project.project_id,
                )
                self.writer.write_json(
                    "sample_scan.json",
                    sample_scan.to_json(),
                    project.project_id,
                )
            except RuntimeError as exc:
                self.logger.log(
                    "sample_scan_skipped",
                    project_id=project.project_id,
                    reason=str(exc),
                )
            self.writer.write_json("agent_memory_snapshot.json", self.memory.snapshot(project.project_id), project.project_id)

            history_path = self.writer.project_dir(project.project_id) / "score_history.json"
            history = load_score_history(history_path, project.project_id)
            history = append_score_history(history, project, qa, round_id=round_id, mode=mode)
            self.writer.write_json("score_history.json", history, project.project_id)
            self.writer.write_text(
                "score_history.md",
                render_score_history_markdown(project, history),
                project.project_id,
            )
            self.logger.log(
                "score_history_updated",
                project_id=project.project_id,
                entry_count=len(history.get("entries") or []),
                industrial_score=qa.get("industrial_score"),
            )

            delta = self.memory.compare_rounds(project.project_id, current_round_id=round_id)
            if delta is not None:
                self.writer.write_text(
                    "round_delta.md",
                    render_round_delta_markdown(project, delta),
                    project.project_id,
                )
                self.writer.write_json(
                    "round_delta.json",
                    delta.to_json(),
                    project.project_id,
                )
                self.logger.log(
                    "round_delta_recorded",
                    project_id=project.project_id,
                    has_previous=delta.has_previous,
                    score_delta=delta.score_delta,
                    resolved_count=len(delta.resolved_fixes),
                    persistent_count=len(delta.persistent_fixes),
                    new_count=len(delta.new_fixes),
                )
            self.logger.log(
                "industrial_qa_completed",
                project_id=project.project_id,
                decision=qa["decision"],
                industrial_score=qa["industrial_score"],
            )
        for filename in [
            "industrial_qa_report.json",
            "industrial_qa_report.md",
            "beginner_summary.md",
            "chapter_review.md",
            "chapter_review.json",
            "kindle_preview_check.md",
            "amazon_research_brief.md",
            "competitor_research_template.csv",
            "rewrite_suggestions.md",
            "rewrite_suggestions.json",
            "amazon_description.html",
            "amazon_description_report.md",
            "amazon_description.json",
            "kdp_keywords.md",
            "kdp_keywords.json",
            "sample_scan.md",
            "sample_scan.json",
            "agent_memory_snapshot.json",
            "round_delta.md",
            "round_delta.json",
            "score_history.json",
            "score_history.md",
        ]:
            self._mirror_if_single(projects, filename)
        return projects

    def run_launch(self, input_path: Path) -> list[BookProject]:
        projects = self.discover(input_path)
        if not projects:
            return projects
        self.llm.require_api_key()
        for project in projects:
            self.logger.log("stage_started", project_id=project.project_id, stage="launch_content")
            launch_md = launch_content(project, self.config, self.llm)
            self.writer.write_text("launch_content.md", launch_md, project.project_id)
        self._mirror_if_single(projects, "launch_content.md")
        return projects

    def run_round(self, input_path: Path, full_review: bool = False) -> dict:
        round_id = make_round_id()
        mode = "full_review" if full_review else "quick_qa"
        self.logger.log("round_started", round_id=round_id, mode=mode, input_path=str(input_path))

        if full_review:
            projects = self.run_all(input_path, round_id=round_id)
        else:
            projects = self.run_qa(input_path, round_id=round_id, mode=mode)
            if projects:
                self.run_cover(input_path)

        summary = snapshot_round(self.writer.artifact_dir, projects, round_id, mode)
        self.writer.write_json("latest_round_summary.json", summary)
        self.logger.log("round_completed", round_id=round_id, mode=mode, project_count=len(projects))
        return summary

    def run_all(self, input_path: Path, round_id: str | None = None) -> list[BookProject]:
        self.run_qa(input_path, round_id=round_id, mode="full_review")
        projects = self.run_review(input_path)
        if not projects:
            return projects
        self.run_cover(input_path)
        self.run_launch(input_path)
        # Final summary already exists from review. Refresh after launch.
        for project in projects:
            parts = [project_metadata(project)]
            project_artifact_dir = self.writer.project_dir(project.project_id)
            for name in [
                "manuscript_review.md",
                "voice_preservation_report.md",
                "amazon_conversion_review.md",
                "publisher_board_review.md",
                "industrial_qa_report.md",
                "cover_review.md",
                "kdp_publish_checklist.md",
                "launch_content.md",
            ]:
                path = project_artifact_dir / name
                if path.exists():
                    parts.append(path.read_text(encoding="utf-8"))
            summary_md = executive_summary(project, "\n\n".join(parts), self.llm)
            self.writer.write_text("final_publisher_summary.md", summary_md, project.project_id)
        self._mirror_if_single(projects, "final_publisher_summary.md")
        return projects
