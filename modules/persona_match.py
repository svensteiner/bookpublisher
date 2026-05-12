"""Persona vs. Amazon-Description Match Score.

Measures how well the Amazon description targets each generated buyer
persona by computing token-overlap between the persona's anchor signals
(problem, buying motive, search query) and the description text.

Pure-Python, deterministic. The score is a heuristic 0-100 signal that
flags whether the author wrote the description for the audience the
persona generator identified — not whether the description is good.
A low match score means the author should restate the persona's pain
(or buying motive) in the first three description lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from modules.competitive_positioning import _ascii_fold
from modules.personas import BuyerPersona, PersonaReport


SCORE_READY: int = 70
SCORE_REVIEW: int = 40

LEAD_LINE_COUNT: int = 3
MIN_TOKEN_LEN: int = 4
MAX_MISSING_KEYWORDS: int = 5

# German + English filler tokens that carry no targeting signal. Ascii-folded
# (umlauts already mapped via _ascii_fold) and lowercase.
STOP_WORDS: frozenset[str] = frozenset({
    "aber", "alle", "alles", "auch", "auf", "aus", "bei", "bist",
    "dann", "dass", "dein", "deine", "deinem", "deinen", "deiner",
    "deines", "dem", "den", "der", "des", "die", "doch", "dort",
    "durch", "ein", "eine", "einem", "einen", "einer", "eines",
    "etwas", "fuer", "ganz", "gar", "gibt", "haben", "hast", "hat",
    "hier", "ich", "ihm", "ihn", "ihre", "ihrem", "ihren", "ihrer",
    "immer", "ins", "ist", "kann", "keine", "keinem", "keinen",
    "kein", "konnte", "lassen", "machen", "macht", "mehr", "mein",
    "meine", "meinem", "meinen", "meiner", "muss", "musst", "nach",
    "nicht", "noch", "nur", "oder", "ohne", "schon", "sehr", "sein",
    "seine", "seinem", "seinen", "seiner", "selbst", "sich", "sie",
    "sind", "soll", "sollte", "sondern", "ueber", "und", "unser",
    "unsere", "unter", "viel", "viele", "vom", "von", "vor", "war",
    "waren", "warst", "was", "weil", "wenn", "wer", "werden",
    "wird", "wirst", "wo", "zur", "zum", "zwar",
    # English fillers that often slip into KDP descriptions
    "the", "and", "you", "your", "with", "from", "this", "that",
    "for", "are", "have", "will", "what", "but", "all", "any",
    "out", "not", "into", "than", "then", "more", "less", "very",
    "able", "also", "such", "they", "them", "their", "theirs",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class PersonaMatchEntry:
    """Per-persona match breakdown."""

    label: str
    matched_tokens: tuple[str, ...]
    missing_tokens: tuple[str, ...]
    total_tokens: int
    score: int

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "matched_tokens": list(self.matched_tokens),
            "missing_tokens": list(self.missing_tokens),
            "total_tokens": self.total_tokens,
            "score": self.score,
        }


@dataclass(frozen=True)
class PersonaMatchReport:
    """Aggregate match report for all personas vs. description."""

    overall_score: int
    status: str
    entries: tuple[PersonaMatchEntry, ...]
    description_present: bool
    lead_lines_present: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "status": self.status,
            "description_present": self.description_present,
            "lead_lines_present": self.lead_lines_present,
            "entries": [entry.to_json() for entry in self.entries],
        }


def _tokenize(text: str) -> tuple[str, ...]:
    """Return ordered, deduplicated, stop-word-filtered tokens."""
    if not text:
        return ()
    folded = _ascii_fold(text)
    seen: set[str] = set()
    out: list[str] = []
    for match in _TOKEN_RE.finditer(folded):
        token = match.group(0)
        if len(token) < MIN_TOKEN_LEN:
            continue
        if token in STOP_WORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return tuple(out)


def _lead_lines(text: str, count: int = LEAD_LINE_COUNT) -> str:
    """Return the first ``count`` non-empty lines joined by space."""
    if not text:
        return ""
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        lines.append(stripped)
        if len(lines) >= count:
            break
    return " ".join(lines)


def _status_for(score: int) -> str:
    if score >= SCORE_READY:
        return "READY"
    if score >= SCORE_REVIEW:
        return "REVIEW"
    return "FIX"


def _persona_signal_text(persona: BuyerPersona) -> str:
    """Return the concatenated signal text used to derive persona tokens."""
    return " ".join(
        part
        for part in (persona.problem, persona.buying_motive, persona.anchor_quote)
        if part
    )


def build_persona_match_report(
    persona_report: PersonaReport | None,
    amazon_description: str | None,
    *,
    lead_only: bool = True,
) -> PersonaMatchReport:
    """Compute per-persona match scores against the Amazon description.

    Pure helper, no I/O. ``lead_only=True`` (default) only considers the
    first ``LEAD_LINE_COUNT`` non-empty lines of the description — those
    are the lines the Kindle shopper sees before "Mehr lesen". When the
    description is missing, every persona scores 0 and the report is
    marked ``description_present=False`` so callers can show a clear
    "Beschreibung fehlt — Match nicht messbar" rather than a misleading
    zero-as-failure.
    """

    description = (amazon_description or "").strip()
    description_present = bool(description)
    lead_text = _lead_lines(description) if lead_only else description
    lead_lines_present = bool(lead_text)

    if persona_report is None or not persona_report.personas:
        return PersonaMatchReport(
            overall_score=0,
            status=_status_for(0),
            entries=(),
            description_present=description_present,
            lead_lines_present=lead_lines_present,
        )

    description_tokens: set[str] = set(_tokenize(lead_text)) if lead_text else set()

    entries: list[PersonaMatchEntry] = []
    score_sum = 0
    for persona in persona_report.personas:
        signal_tokens = _tokenize(_persona_signal_text(persona))
        total = len(signal_tokens)
        if total == 0:
            entry = PersonaMatchEntry(
                label=persona.label,
                matched_tokens=(),
                missing_tokens=(),
                total_tokens=0,
                score=0,
            )
            entries.append(entry)
            continue
        matched: list[str] = []
        missing: list[str] = []
        for token in signal_tokens:
            if token in description_tokens:
                matched.append(token)
            else:
                missing.append(token)
        score = int(round(100 * len(matched) / total)) if total else 0
        score_sum += score
        entry = PersonaMatchEntry(
            label=persona.label,
            matched_tokens=tuple(matched),
            missing_tokens=tuple(missing[:MAX_MISSING_KEYWORDS]),
            total_tokens=total,
            score=score,
        )
        entries.append(entry)

    overall = int(round(score_sum / len(entries))) if entries else 0
    return PersonaMatchReport(
        overall_score=overall,
        status=_status_for(overall),
        entries=tuple(entries),
        description_present=description_present,
        lead_lines_present=lead_lines_present,
    )


def render_persona_match_section(report: PersonaMatchReport) -> str:
    """Return a Markdown section to append to ``buyer_personas.md``.

    Returns an empty string when there are no entries (nothing useful to
    show) — callers can simply concatenate the result without guarding.
    """

    if not report.entries:
        return ""

    lines: list[str] = [
        "## Match-Score gegen Amazon-Beschreibung",
        "",
    ]
    if not report.description_present:
        lines.extend([
            "Keine Amazon-Beschreibung in den Metadaten gefunden — Match nicht messbar.",
            "Trage die Beschreibung in `metadata.md` ein, dann taucht hier ein Score auf.",
            "",
        ])
        return "\n".join(lines)
    if not report.lead_lines_present:
        lines.extend([
            "Die Beschreibung enthält keine sichtbaren Zeilen — bitte Inhalt prüfen.",
            "",
        ])
        return "\n".join(lines)

    lines.append(
        f"Gesamt: **{report.overall_score}/100** ({report.status})"
        " — gemittelt über alle Personas, gemessen nur an den ersten drei Beschreibungs-Zeilen."
    )
    lines.append("")
    lines.append("| Persona | Score | Treffer | Fehlt (Top 5) |")
    lines.append("|---|---|---|---|")
    for entry in report.entries:
        missing_text = ", ".join(entry.missing_tokens) if entry.missing_tokens else "—"
        lines.append(
            f"| {entry.label} | {entry.score}/100 |"
            f" {len(entry.matched_tokens)}/{entry.total_tokens} |"
            f" {missing_text} |"
        )
    lines.append("")
    return "\n".join(lines)
