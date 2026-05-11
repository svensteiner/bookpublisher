"""Round-over-round delta reporting.

Compares two QA rounds for a project and renders an author-facing report that
shows which previously-flagged fixes were addressed, which persist, which are
new, and how the industrial score evolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from modules.discovery import BookProject


SCORE_NA = "n/a"


@dataclass(frozen=True)
class RoundDelta:
    project_id: str
    previous_round: dict[str, Any] | None
    current_round: dict[str, Any]
    score_delta: int | None
    investor_grade_delta: float | None
    decision_changed: bool
    resolved_fixes: tuple[str, ...]
    persistent_fixes: tuple[str, ...]
    new_fixes: tuple[str, ...]

    @property
    def has_previous(self) -> bool:
        return self.previous_round is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "previous_round": self.previous_round,
            "current_round": self.current_round,
            "score_delta": self.score_delta,
            "investor_grade_delta": self.investor_grade_delta,
            "decision_changed": self.decision_changed,
            "resolved_fixes": list(self.resolved_fixes),
            "persistent_fixes": list(self.persistent_fixes),
            "new_fixes": list(self.new_fixes),
        }


def _normalize_fixes(items: Any) -> tuple[str, ...]:
    if not isinstance(items, list):
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return tuple(out)


def compute_round_delta(
    project_id: str,
    rounds: list[dict[str, Any]],
    current_round_id: str | None = None,
    previous_round_id: str | None = None,
) -> RoundDelta | None:
    """Build a RoundDelta from a chronological list of round records.

    Returns None if there are no rounds at all.
    If only one round exists, returns a delta with previous_round=None.
    """

    if not rounds:
        return None

    current = _pick_round(rounds, current_round_id, default_index=-1)
    if current is None:
        return None

    current_idx = rounds.index(current)
    previous: dict[str, Any] | None
    if previous_round_id is not None:
        previous = _pick_round(rounds, previous_round_id, default_index=None)
    elif current_idx > 0:
        previous = rounds[current_idx - 1]
    else:
        previous = None

    current_fixes = _normalize_fixes(current.get("required_fixes"))
    previous_fixes = _normalize_fixes(previous.get("required_fixes")) if previous else ()
    current_set = set(current_fixes)
    previous_set = set(previous_fixes)
    resolved = tuple(item for item in previous_fixes if item not in current_set)
    persistent = tuple(item for item in current_fixes if item in previous_set)
    new = tuple(item for item in current_fixes if item not in previous_set)

    score_delta = _numeric_delta(current.get("industrial_score"), previous.get("industrial_score") if previous else None)
    grade_delta = _numeric_delta(current.get("investor_grade"), previous.get("investor_grade") if previous else None)
    decision_changed = bool(previous) and current.get("decision") != previous.get("decision")

    return RoundDelta(
        project_id=project_id,
        previous_round=previous,
        current_round=current,
        score_delta=int(score_delta) if isinstance(score_delta, (int, float)) else None,
        investor_grade_delta=float(grade_delta) if isinstance(grade_delta, (int, float)) else None,
        decision_changed=decision_changed,
        resolved_fixes=resolved,
        persistent_fixes=persistent,
        new_fixes=new,
    )


def _pick_round(
    rounds: list[dict[str, Any]],
    round_id: str | None,
    default_index: int | None,
) -> dict[str, Any] | None:
    if round_id is not None:
        for entry in rounds:
            if entry.get("round_id") == round_id:
                return entry
        return None
    if default_index is None:
        return None
    try:
        return rounds[default_index]
    except IndexError:
        return None


def _numeric_delta(current: Any, previous: Any) -> float | int | None:
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return None
    return current - previous


def _format_score(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value}"
    return SCORE_NA


def _format_delta(value: int | float | None, suffix: str = "") -> str:
    if value is None:
        return ""
    if value > 0:
        return f" (+{value}{suffix})"
    if value < 0:
        return f" ({value}{suffix})"
    return f" (±0{suffix})"


def render_round_delta_markdown(project: BookProject, delta: RoundDelta) -> str:
    title = project.title or project.project_id
    lines: list[str] = [f"# Runden-Delta – {title}", ""]

    if not delta.has_previous:
        lines.extend([
            "Dies ist die erste aufgezeichnete Runde für dieses Buch.",
            "Es gibt noch keine Vor-Runde zum Vergleichen.",
            "",
            "## Aktuelle Runde",
            f"- Runde: `{delta.current_round.get('round_id') or 'unbekannt'}`",
            f"- Industrial-Score: {_format_score(delta.current_round.get('industrial_score'))}",
            f"- Entscheidung: {delta.current_round.get('decision') or 'unbekannt'}",
            f"- Offene Fixes: {len(_normalize_fixes(delta.current_round.get('required_fixes')))}",
        ])
        return "\n".join(lines)

    previous = delta.previous_round or {}
    current = delta.current_round
    score_line = f"{_format_score(previous.get('industrial_score'))} → {_format_score(current.get('industrial_score'))}{_format_delta(delta.score_delta)}"
    grade_line = f"{_format_score(previous.get('investor_grade'))} → {_format_score(current.get('investor_grade'))}{_format_delta(delta.investor_grade_delta)}"
    decision_line = f"{previous.get('decision') or 'unbekannt'} → {current.get('decision') or 'unbekannt'}"
    if delta.decision_changed:
        decision_line += "  *(geändert)*"

    lines.extend([
        f"- Vorherige Runde: `{previous.get('round_id') or 'unbekannt'}` ({previous.get('ts') or 'ohne Zeitstempel'})",
        f"- Aktuelle Runde:  `{current.get('round_id') or 'unbekannt'}` ({current.get('ts') or 'ohne Zeitstempel'})",
        f"- Industrial-Score: {score_line}",
        f"- Investor-Grade:   {grade_line}",
        f"- Entscheidung:     {decision_line}",
        "",
    ])

    lines.append("## Erledigte Fixes (waren in der Vorrunde offen, jetzt erledigt)")
    if delta.resolved_fixes:
        lines.extend(f"- ✅ {item}" for item in delta.resolved_fixes)
    else:
        lines.append("- Keine Fixes aus der Vorrunde wurden umgesetzt.")
    lines.append("")

    lines.append("## Persistente Fixes (in beiden Runden offen)")
    if delta.persistent_fixes:
        lines.extend(f"- ⚠️ {item}" for item in delta.persistent_fixes)
    else:
        lines.append("- Keine persistenten Fixes.")
    lines.append("")

    lines.append("## Neue Fixes (heute zum ersten Mal aufgetaucht)")
    if delta.new_fixes:
        lines.extend(f"- 🆕 {item}" for item in delta.new_fixes)
    else:
        lines.append("- Keine neuen Fixes.")

    return "\n".join(lines)
