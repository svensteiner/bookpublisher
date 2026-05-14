from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class ConfigError(RuntimeError):
    pass


def _clamp_float(value: Any, *, low: float, high: float) -> float:
    """Coerce ``value`` to float and clamp into ``[low, high]``."""
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        coerced = low
    return max(low, min(high, coerced))


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    default_input_path: Path
    default_model: str
    fallback_model: str
    temperature: float = 0.2
    max_manuscript_chars: int = 60000
    read_only: bool = True
    artifact_mirror_single_project: bool = True
    skip_directories: set[str] = field(default_factory=set)
    supplemental_text_directories: set[str] = field(default_factory=set)
    skills_directory: Path = Path("skills")
    memory_path: Path = Path("artifacts/agent_memory.json")
    supported_files: dict[str, list[str]] = field(default_factory=dict)
    # Number of total attempts per model (primary AND fallback) before
    # switching/giving up. 1 = no retry (legacy behavior). Production
    # config.yaml sets this to 2 so transient rate-limit/timeout errors
    # do not immediately drop the run onto the cheaper fallback model.
    llm_retry_attempts: int = 1
    # Base delay in seconds between retries; doubled per attempt
    # (exponential backoff). Set to 0 in tests to avoid real sleeps.
    llm_retry_backoff_seconds: float = 0.5
    # First-N%-Deep-Scan tuning. Short nonfiction books (<25k words)
    # benefit from a higher ratio and lower min-section-words so the
    # Kindle-Sample diagnostic gets enough sections to be meaningful.
    sample_scan_ratio: float = 0.10
    sample_scan_max_ratio: float = 0.14
    sample_scan_max_sections: int = 8
    sample_scan_section_target_words: int = 350
    sample_scan_min_section_words: int = 90
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(config_path: Path | None = None) -> AppConfig:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    load_dotenv(PROJECT_ROOT / ".env")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = ["default_model", "fallback_model"]
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise ConfigError(f"Missing required config values: {', '.join(missing)}")

    skip = {str(item).lower() for item in data.get("skip_directories", [])}
    supplemental = {str(item).lower() for item in data.get("supplemental_text_directories", [])}
    supported = data.get("supported_files", {}) or {}
    normalized_supported = {
        key: [ext.lower() for ext in value]
        for key, value in supported.items()
    }

    return AppConfig(
        project_root=PROJECT_ROOT,
        default_input_path=Path(data["default_input_path"]) if data.get("default_input_path") else Path.home(),
        default_model=str(data["default_model"]),
        fallback_model=str(data["fallback_model"]),
        temperature=float(data.get("temperature", 0.2)),
        max_manuscript_chars=int(data.get("max_manuscript_chars", 60000)),
        read_only=bool(data.get("read_only", True)),
        artifact_mirror_single_project=bool(data.get("artifact_mirror_single_project", True)),
        skip_directories=skip,
        supplemental_text_directories=supplemental,
        skills_directory=Path(data.get("skills_directory", "skills")),
        memory_path=Path(data.get("memory_path", "artifacts/agent_memory.json")),
        supported_files=normalized_supported,
        llm_retry_attempts=max(1, int(data.get("llm_retry_attempts", 2))),
        llm_retry_backoff_seconds=max(0.0, float(data.get("llm_retry_backoff_seconds", 0.5))),
        sample_scan_ratio=_clamp_float(data.get("sample_scan_ratio", 0.10), low=0.01, high=1.0),
        sample_scan_max_ratio=_clamp_float(data.get("sample_scan_max_ratio", 0.14), low=0.01, high=1.0),
        sample_scan_max_sections=max(1, int(data.get("sample_scan_max_sections", 8))),
        sample_scan_section_target_words=max(20, int(data.get("sample_scan_section_target_words", 350))),
        sample_scan_min_section_words=max(10, int(data.get("sample_scan_min_section_words", 90))),
        raw=data,
    )
