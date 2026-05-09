from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from modules.discovery import BookProject


class SkillRegistry:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills = self._load_skills()

    def _load_skills(self) -> list[dict[str, Any]]:
        if not self.skills_dir.exists():
            return []
        skills: list[dict[str, Any]] = []
        for path in sorted(self.skills_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data["path"] = str(path)
            skills.append(data)
        return skills

    def to_json(self) -> list[dict[str, Any]]:
        return self.skills

    def names(self) -> list[str]:
        return [str(skill.get("name", "unknown")) for skill in self.skills]

    def prompt_context(self) -> str:
        if not self.skills:
            return "No skills loaded."
        lines = ["# Loaded Publisher Skills"]
        for skill in self.skills:
            lines.extend([
                "",
                f"## {skill.get('name', 'unknown')}",
                f"Purpose: {skill.get('purpose', 'n/a')}",
            ])
            checks = skill.get("checks") or []
            if checks:
                lines.append("Checks:")
                lines.extend(f"- {item}" for item in checks)
            heuristics = skill.get("heuristics") or []
            if heuristics:
                lines.append("Heuristics:")
                lines.extend(f"- {item}" for item in heuristics)
        return "\n".join(lines)


class AgentMemory:
    def __init__(self, memory_path: Path):
        self.memory_path = memory_path
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if self.memory_path.exists():
            try:
                return json.loads(self.memory_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {
            "version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "agent_profile": {
                "role": "industrial German Kindle publisher agent",
                "operating_principles": [
                    "protect author voice",
                    "prefer release gates over vague advice",
                    "optimize Kindle sellability without hype",
                    "write only artifacts and logs",
                ],
            },
            "projects": {},
        }

    def save(self) -> None:
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    def project_memory(self, project_id: str) -> dict[str, Any]:
        projects = self.data.setdefault("projects", {})
        return projects.setdefault(project_id, {
            "first_seen_at": datetime.now().isoformat(timespec="seconds"),
            "latest_project": {},
            "rounds": [],
            "stable_facts": [],
            "open_risks": [],
        })

    def remember_project(self, project: BookProject) -> None:
        memory = self.project_memory(project.project_id)
        memory["latest_project"] = {
            "root": str(project.root),
            "title": project.title,
            "subtitle": project.subtitle,
            "author": project.author,
            "manuscript": str(project.manuscript) if project.manuscript else None,
            "cover": str(project.cover) if project.cover else None,
            "missing_assets": project.missing_assets,
        }
        facts = {
            f"title={project.title}" if project.title else None,
            f"author={project.author}" if project.author else None,
            "amazon_description_present" if project.amazon_description else None,
        }
        for fact in sorted(item for item in facts if item):
            if fact not in memory["stable_facts"]:
                memory["stable_facts"].append(fact)

    def remember_qa(self, project: BookProject, qa_report: dict[str, Any], round_id: str | None = None) -> None:
        memory = self.project_memory(project.project_id)
        risks = qa_report.get("all_required_fixes", [])
        memory["open_risks"] = risks
        memory["rounds"].append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "round_id": round_id,
            "decision": qa_report.get("decision"),
            "industrial_score": qa_report.get("industrial_score"),
            "investor_grade": qa_report.get("investor_grade"),
            "required_fixes": risks,
        })
        memory["rounds"] = memory["rounds"][-20:]

    def prompt_context(self, project_id: str) -> str:
        payload = {
            "agent_profile": self.data.get("agent_profile", {}),
            "project_memory": self.project_memory(project_id),
        }
        return "# Agent Memory\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    def snapshot(self, project_id: str) -> dict[str, Any]:
        return {
            "agent_profile": self.data.get("agent_profile", {}),
            "project_memory": self.project_memory(project_id),
        }
