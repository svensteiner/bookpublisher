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


# Sane outer bounds for an Amstad-FRE target band. Below 10 the text is
# pure academic prose (no popular nonfiction reads that low); above 100
# the Amstad formula stops producing meaningful values for German.
_FRE_TARGET_HARD_MIN: int = 10
_FRE_TARGET_HARD_MAX: int = 100
_FRE_TARGET_DEFAULT_MIN: int = 50
_FRE_TARGET_DEFAULT_MAX: int = 80


def _readability_target_band(data: dict[str, Any]) -> tuple[int, int]:
    """Resolve the readability target band from raw YAML data.

    Returns ``(min, max)`` after clamping each side into the Amstad sane
    range. When the resolved band is degenerate (``min >= max``) — either
    from a typo or from clamping collapsing two adjacent values — the
    band falls back to the canonical 50/80 default so the QA gate stays
    usable instead of failing the run with a confusing error.
    """

    raw_min = data.get("readability_target_min", _FRE_TARGET_DEFAULT_MIN)
    raw_max = data.get("readability_target_max", _FRE_TARGET_DEFAULT_MAX)
    try:
        target_min = int(raw_min)
    except (TypeError, ValueError):
        target_min = _FRE_TARGET_DEFAULT_MIN
    try:
        target_max = int(raw_max)
    except (TypeError, ValueError):
        target_max = _FRE_TARGET_DEFAULT_MAX
    target_min = max(_FRE_TARGET_HARD_MIN, min(_FRE_TARGET_HARD_MAX, target_min))
    target_max = max(_FRE_TARGET_HARD_MIN, min(_FRE_TARGET_HARD_MAX, target_max))
    if target_min >= target_max:
        return _FRE_TARGET_DEFAULT_MIN, _FRE_TARGET_DEFAULT_MAX
    return target_min, target_max


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
    # Chapter-balance outlier detection. A chapter is flagged as SPLIT
    # when its word count > median * oversized_factor, as MERGE when
    # < median * undersized_factor. Lesson-style nonfiction (~30 short
    # chapters) should lower oversized_factor to ~2.0 and raise
    # undersized_factor to ~0.5 so micro-lessons don't get flagged.
    balance_oversized_factor: float = 3.0
    balance_undersized_factor: float = 0.3
    balance_min_chapters: int = 3
    # Number of competitive-positioning differentiation angles to surface
    # in beginner_summary.md ("## Positionierung"). 1 = strongest angle
    # only (default, keeps the summary compact). 2-3 = also show secondary
    # angles for books where multiple differentiation hooks need to land
    # in the Amazon description.
    beginner_summary_positioning_limit: int = 1
    # Number of KDP keyword slots surfaced in beginner_summary.md
    # ("## KDP-Keywords (Top-3)"). 3 = canonical default that matches the
    # subject_audience + audience_format + anchor_pair spread. KDP allows
    # at most 7 keywords total, so values above 7 are clamped — picking
    # all 7 turns the summary into the full keyword report.
    beginner_summary_kdp_keyword_limit: int = 3
    # Number of weakest chapters surfaced in beginner_summary.md
    # ("## Schwächste Kapitel"). 3 = canonical default that keeps the
    # summary focused on the top fix-candidates. Authors with many short
    # chapters (>20) can raise this so cluster-issues become visible at a
    # glance. Clamped to [1, 10]: more than 10 stops being a "weakest"
    # signal and turns the summary into the full chapter report.
    beginner_summary_weakest_limit: int = 3
    # Number of weakest Kindle-Sample sections surfaced in beginner_summary.md
    # ("## Schwächster Sample-Abschnitt" / "## Schwächste Sample-Abschnitte").
    # 1 = canonical default (only the highest drop-off risk). Raise to 2-3 for
    # long nonfiction (>100k words) where multiple FIX-flagged sections in the
    # Kindle preview signal a cluster issue rather than a single weak passage.
    # Clamped to [1, sample_scan_max_sections] downstream (max 10 here).
    beginner_summary_weakest_sample_limit: int = 1
    # Amstad-FRE readability target band. Default 50-80 fits popular German
    # nonfiction (B1/B2 reading level). Authors of academic Fachbuecher can
    # lower the band (e.g. 30-50 — Wissenschaftssprache) so the QA gate
    # stops flagging dense paragraphs as "too hard". Authors of lifestyle
    # nonfiction can raise it (e.g. 60-90 — sehr leicht) so the gate flags
    # passages that feel too academic for the audience. The loader enforces
    # ``min < max`` and clamps both into a sane Amstad range — out-of-band
    # values fall back to the default 50/80 instead of failing the run.
    readability_target_min: int = 50
    readability_target_max: int = 80
    # Optional LLM-Pass for the Amazon-Description HTML bullets. When
    # enabled AND ``ANTHROPIC_API_KEY`` is set, the pipeline asks the LLM
    # to extract 5 book-specific sales bullets from the metadata + chapter
    # titles instead of using the deterministic anti-hype template. Off by
    # default so the QA gate stays usable without an API key, and so the
    # heuristic template (which is good) ships first. Any LLM failure
    # silently falls back to the template — never an aborted run.
    amazon_html_llm_bullets_enabled: bool = False
    # Optional matplotlib-backed PNG render of score_history.json.
    # Off by default: matplotlib is not in the install set, enabling it
    # bloats the Windows EXE for users who only consume the markdown
    # report. When enabled, the pipeline writes
    # ``artifacts/<project>/score_history.png`` after each round; if
    # matplotlib is missing the run continues and a single warning is
    # logged instead of crashing.
    score_history_graph_enabled: bool = False
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

    readability_min, readability_max = _readability_target_band(data)

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
        balance_oversized_factor=_clamp_float(data.get("balance_oversized_factor", 3.0), low=1.1, high=20.0),
        balance_undersized_factor=_clamp_float(data.get("balance_undersized_factor", 0.3), low=0.01, high=0.9),
        balance_min_chapters=max(2, int(data.get("balance_min_chapters", 3))),
        beginner_summary_positioning_limit=max(
            1,
            min(3, int(data.get("beginner_summary_positioning_limit", 1))),
        ),
        beginner_summary_kdp_keyword_limit=max(
            1,
            min(7, int(data.get("beginner_summary_kdp_keyword_limit", 3))),
        ),
        beginner_summary_weakest_limit=max(
            1,
            min(10, int(data.get("beginner_summary_weakest_limit", 3))),
        ),
        beginner_summary_weakest_sample_limit=max(
            1,
            min(10, int(data.get("beginner_summary_weakest_sample_limit", 1))),
        ),
        readability_target_min=readability_min,
        readability_target_max=readability_max,
        amazon_html_llm_bullets_enabled=bool(
            data.get("amazon_html_llm_bullets_enabled", False)
        ),
        score_history_graph_enabled=bool(data.get("score_history_graph_enabled", False)),
        raw=data,
    )
