"""Structured score history across QA rounds.

Tracks every industrial QA run for a project in a graph-ready JSON file so the
author can see how the score evolves over rounds, which gates moved, and what
the top fixes were at each point.

Pure-Python, no LLM. Append-only with a soft cap, immutable entry records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.discovery import BookProject


SCORE_HISTORY_VERSION = 1
MAX_HISTORY_ENTRIES = 50
TOP_FIX_COUNT = 3
SPARK_WIDTH = 20


@dataclass(frozen=True)
class ScoreHistoryEntry:
    timestamp: str
    round_id: str | None
    mode: str
    decision: str
    industrial_score: int
    investor_grade: float | None
    gates: tuple[dict[str, Any], ...]
    top_fixes: tuple[str, ...]
    score_delta: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "round_id": self.round_id,
            "mode": self.mode,
            "decision": self.decision,
            "industrial_score": self.industrial_score,
            "investor_grade": self.investor_grade,
            "gates": [dict(gate) for gate in self.gates],
            "top_fixes": list(self.top_fixes),
            "score_delta": self.score_delta,
        }


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_grade(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gate_summary(qa: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    gates = qa.get("gates") or []
    out: list[dict[str, Any]] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        out.append({
            "name": str(gate.get("name", "unknown")),
            "status": str(gate.get("status", "UNKNOWN")),
            "score": _coerce_int(gate.get("score")),
        })
    return tuple(out)


def _top_fixes(qa: dict[str, Any], count: int = TOP_FIX_COUNT) -> tuple[str, ...]:
    fixes = qa.get("all_required_fixes") or []
    out: list[str] = []
    seen: set[str] = set()
    for fix in fixes:
        text = str(fix).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= count:
            break
    return tuple(out)


def load_score_history(history_path: Path, project_id: str) -> dict[str, Any]:
    """Return existing history dict for the project, or a fresh one."""
    if history_path.exists():
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("project_id") == project_id:
                entries = data.get("entries")
                if isinstance(entries, list):
                    return {
                        "version": data.get("version", SCORE_HISTORY_VERSION),
                        "project_id": project_id,
                        "created_at": data.get(
                            "created_at",
                            datetime.now().isoformat(timespec="seconds"),
                        ),
                        "entries": [entry for entry in entries if isinstance(entry, dict)],
                    }
        except json.JSONDecodeError:
            pass
    return {
        "version": SCORE_HISTORY_VERSION,
        "project_id": project_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "entries": [],
    }


def append_score_history(
    history: dict[str, Any],
    project: BookProject,
    qa: dict[str, Any],
    round_id: str | None = None,
    mode: str = "quick_qa",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a new history dict with one entry appended.

    Immutable: never mutates the input dict. The new entry computes its own
    score_delta against the most recent prior entry.
    """
    timestamp = (now or datetime.now()).isoformat(timespec="seconds")
    previous_entries = list(history.get("entries") or [])
    previous_score = (
        _coerce_int(previous_entries[-1].get("industrial_score"))
        if previous_entries
        else None
    )
    current_score = _coerce_int(qa.get("industrial_score"))
    score_delta = (
        current_score - previous_score if previous_score is not None else None
    )

    entry = ScoreHistoryEntry(
        timestamp=timestamp,
        round_id=round_id,
        mode=str(mode or "quick_qa"),
        decision=str(qa.get("decision", "HOLD")),
        industrial_score=current_score,
        investor_grade=_coerce_grade(qa.get("investor_grade")),
        gates=_gate_summary(qa),
        top_fixes=_top_fixes(qa),
        score_delta=score_delta,
    )

    new_entries = previous_entries + [entry.to_json()]
    new_entries = new_entries[-MAX_HISTORY_ENTRIES:]

    return {
        "version": history.get("version", SCORE_HISTORY_VERSION),
        "project_id": history.get("project_id", project.project_id),
        "created_at": history.get(
            "created_at",
            datetime.now().isoformat(timespec="seconds"),
        ),
        "entries": new_entries,
    }


def _sparkline(scores: list[int]) -> str:
    if not scores:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    out: list[str] = []
    for score in scores[-SPARK_WIDTH:]:
        clamped = max(0, min(100, score))
        idx = min(len(blocks) - 1, clamped * (len(blocks) - 1) // 100)
        out.append(blocks[idx])
    return "".join(out)


def _format_delta(delta: int | None) -> str:
    if delta is None:
        return "  –"
    if delta > 0:
        return f"+{delta:>2}"
    return f"{delta:>3}"


def render_score_history_markdown(
    project: BookProject,
    history: dict[str, Any],
) -> str:
    entries = list(history.get("entries") or [])
    title = project.title or project.project_id

    lines: list[str] = [
        "# Score-Verlauf",
        "",
        f"Buch: **{title}**",
        f"Eintraege: {len(entries)}",
        "",
    ]

    if not entries:
        lines.append("Noch keine Pruef-Runden aufgezeichnet. Starte eine Schnellrunde, um den Verlauf zu beginnen.")
        return "\n".join(lines)

    scores = [_coerce_int(entry.get("industrial_score")) for entry in entries]
    spark = _sparkline(scores)
    if spark:
        lines.append(f"Trend: `{spark}` ({scores[0]} -> {scores[-1]})")
        lines.append("")

    lines.append("| Datum | Runde | Modus | Score | Delta | Decision |")
    lines.append("|---|---|---|---|---|---|")
    for entry in entries:
        ts = str(entry.get("timestamp", ""))
        round_id = entry.get("round_id") or "-"
        mode = str(entry.get("mode", "quick_qa"))
        score = _coerce_int(entry.get("industrial_score"))
        delta = entry.get("score_delta")
        delta_text = _format_delta(delta if delta is None else _coerce_int(delta))
        decision = str(entry.get("decision", "HOLD"))
        lines.append(f"| {ts} | {round_id} | {mode} | {score}/100 | {delta_text} | {decision} |")

    latest = entries[-1]
    top = latest.get("top_fixes") or []
    if top:
        lines.append("")
        lines.append("## Top-Fixes der letzten Runde")
        for fix in top:
            lines.append(f"- {fix}")

    return "\n".join(lines)
