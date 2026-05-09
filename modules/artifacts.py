from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from modules.run_logger import RunLogger


def safe_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9äöüß_-]+", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "book_project"


class ArtifactWriter:
    def __init__(self, artifact_dir: Path, logger: RunLogger):
        self.artifact_dir = artifact_dir
        self.logger = logger
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        path = self.artifact_dir / safe_slug(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_text(self, filename: str, content: str, project_id: str | None = None) -> Path:
        path = (self.project_dir(project_id) if project_id else self.artifact_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        self.logger.log("artifact_created", path=str(path))
        return path

    def write_json(self, filename: str, payload: Any, project_id: str | None = None) -> Path:
        path = (self.project_dir(project_id) if project_id else self.artifact_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        self.logger.log("artifact_created", path=str(path))
        return path

    def mirror_single_project_file(self, project_id: str, filename: str) -> None:
        source = self.project_dir(project_id) / filename
        if not source.exists():
            return
        target = self.artifact_dir / filename
        target.write_bytes(source.read_bytes())
        self.logger.log("artifact_mirrored", source=str(source), target=str(target))
