"""Chapter-arc check for German nonfiction manuscripts.

Classic nonfiction follows a four-phase reader arc:

* **PROBLEM**     — Wake the reader up. Name the pain, status quo, cost
                    of inaction.
* **LÖSUNG**      — Introduce the method, framework, principle. Why
                    *this* book / approach.
* **BEWEIS**      — Prove it works. Case studies, numbers, evidence.
* **TRANSFORMATION** — Show what changes once the reader applies it.
                    Next steps, integration, outcomes.

This module classifies every chapter into one of these four phases by
counting deterministic marker hits, then evaluates whether the chapter
*order* roughly follows the canonical PROBLEM → LÖSUNG → BEWEIS →
TRANSFORMATION arc. Deviations are flagged with concrete fix lines for
the author.

Pure-Python, no LLM, no filesystem — safe in QA mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from modules.chapters import Chapter
from modules.discovery import BookProject


# Canonical phases in arc order. The order *is* the spec; do not
# reorder without updating the scoring math.
PHASE_PROBLEM: str = "PROBLEM"
PHASE_SOLUTION: str = "LÖSUNG"
PHASE_PROOF: str = "BEWEIS"
PHASE_TRANSFORMATION: str = "TRANSFORMATION"

CANONICAL_PHASES: tuple[str, ...] = (
    PHASE_PROBLEM,
    PHASE_SOLUTION,
    PHASE_PROOF,
    PHASE_TRANSFORMATION,
)

PHASE_RANK: dict[str, int] = {phase: idx for idx, phase in enumerate(CANONICAL_PHASES)}

# Beginner-summary friendly emoji set, identical scheme as other
# reports.
PHASE_EMOJI: dict[str, str] = {
    PHASE_PROBLEM: "🩹",
    PHASE_SOLUTION: "🧭",
    PHASE_PROOF: "📊",
    PHASE_TRANSFORMATION: "🚀",
}

SCORE_READY: int = 85
SCORE_REVIEW: int = 65

# Marker vocabularies. Patterns are intentionally narrow to keep
# classification stable — broad words like "problem" alone are not
# enough; we want phrases that *signal* the phase intent.
PROBLEM_MARKERS: str = (
    r"\b(problem(?:e|atik)?|herausforderung|schmerz|leid(?:en)?|"
    r"frustration|kosten|verlust|risiko|gefahr|fehler|stolperstein|"
    r"status quo|warum scheitert|warum klappt|woran liegt|warum kein|"
    r"kein erfolg|kein durchbruch|teufelskreis|sackgasse|hindernis|"
    r"die meisten (?:scheitern|verlieren|geben auf)|"
    r"verlorene (?:zeit|jahre|chancen))\b"
)
SOLUTION_MARKERS: str = (
    r"\b(l[öo]sung|methode|framework|system|prinzip|ansatz|"
    r"strategie|konzept|modell|denkweise|paradigma|"
    r"leitfaden|playbook|schritt-?f[üu]r-?schritt|"
    r"so geht (?:es|es richtig)|so funktioniert|hier ist (?:der|die) weg|"
    r"die (?:drei|vier|f[üu]nf|sechs|sieben) schritte|"
    r"unser ansatz|mein ansatz|der weg|die formel)\b"
)
PROOF_MARKERS: str = (
    r"\b(fallstudie|case study|aus eigener erfahrung|selbst getestet|"
    r"live-?projekt|in der praxis|beispiel:|fakten|statistik|studie|"
    r"messung|zahlen|kpi|ergebnis(?:se)?|abbildung\s*\d+|tabelle\s*\d+|"
    r"kundenstimme|testimonial|interview|untersuchung|nachweis|beweis)\b"
    r"|\d+\s*(?:euro|€|\$|stunden|tage|wochen|monate|jahre|%|prozent|"
    r"kunden|projekte|teilnehmer|f[äa]lle)"
)
TRANSFORMATION_MARKERS: str = (
    r"\b(transformation|veränderung|verwandlung|wandel|"
    r"n[äa]chster schritt|n[äa]chste schritte|umsetzung|"
    r"in deinem alltag|in deinem leben|in deinem unternehmen|"
    r"in 30 tagen|in 90 tagen|in einem jahr|"
    r"das neue (?:du|ich|leben)|so wirst du|so erreichst du|"
    r"dein weg nach vorne|ausblick|fazit f[üu]r dich|"
    r"jetzt anwenden|jetzt umsetzen|integration|skalieren)\b"
)

PHASE_PATTERNS: dict[str, str] = {
    PHASE_PROBLEM: PROBLEM_MARKERS,
    PHASE_SOLUTION: SOLUTION_MARKERS,
    PHASE_PROOF: PROOF_MARKERS,
    PHASE_TRANSFORMATION: TRANSFORMATION_MARKERS,
}

# Position-based prior — a chapter at the very start of the book is
# slightly biased toward PROBLEM, the very last toward TRANSFORMATION.
# Bias is small enough that strong marker counts always win.
POSITION_BIAS: int = 1


@dataclass(frozen=True)
class ChapterPhase:
    """A chapter labeled with its detected arc phase."""

    index: int
    title: str
    phase: str
    marker_counts: dict[str, int]
    confidence: int

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "phase": self.phase,
            "marker_counts": dict(self.marker_counts),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ArcReport:
    """Arc-conformance report for the full chapter list."""

    sequence: tuple[ChapterPhase, ...]
    arc_score: int
    status: str
    inversions: tuple[tuple[int, int], ...]
    missing_phases: tuple[str, ...]
    fixes: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "sequence": [item.to_json() for item in self.sequence],
            "arc_score": self.arc_score,
            "status": self.status,
            "inversions": [list(pair) for pair in self.inversions],
            "missing_phases": list(self.missing_phases),
            "fixes": list(self.fixes),
        }


def _count_markers(body: str, pattern: str) -> int:
    return len(re.findall(pattern, body, flags=re.I))


def _status_for(score: int) -> str:
    if score >= SCORE_READY:
        return "READY"
    if score >= SCORE_REVIEW:
        return "REVIEW"
    return "FIX"


def _classify_chapter(
    chapter: Chapter, position_ratio: float
) -> ChapterPhase:
    """Pick the dominant arc phase for a chapter.

    Marker counts decide the phase. Ties are broken by the position
    bias: early chapters lean PROBLEM, mid chapters lean LÖSUNG/BEWEIS,
    late chapters lean TRANSFORMATION. Confidence reflects how decisive
    the dominant phase was vs the runner-up (0-100).
    """

    counts = {
        phase: _count_markers(chapter.body, pattern)
        for phase, pattern in PHASE_PATTERNS.items()
    }

    biased = dict(counts)
    if position_ratio < 0.25:
        biased[PHASE_PROBLEM] += POSITION_BIAS
    elif position_ratio < 0.55:
        biased[PHASE_SOLUTION] += POSITION_BIAS
    elif position_ratio < 0.80:
        biased[PHASE_PROOF] += POSITION_BIAS
    else:
        biased[PHASE_TRANSFORMATION] += POSITION_BIAS

    # Stable order via CANONICAL_PHASES so ties at zero default to PROBLEM
    # for an opening chapter and TRANSFORMATION for a closing chapter
    # (because position bias added 1 to that bucket).
    ranked = sorted(
        CANONICAL_PHASES,
        key=lambda phase: (-biased[phase], PHASE_RANK[phase]),
    )
    top = ranked[0]
    runner = ranked[1]
    top_count = biased[top]
    runner_count = biased[runner]
    if top_count == 0:
        confidence = 0
    else:
        ratio = (top_count - runner_count) / top_count
        confidence = max(0, min(100, round(ratio * 100)))

    return ChapterPhase(
        index=chapter.index,
        title=chapter.title,
        phase=top,
        marker_counts=counts,
        confidence=confidence,
    )


def _arc_score(sequence: tuple[ChapterPhase, ...]) -> tuple[int, tuple[tuple[int, int], ...]]:
    """Compute 0-100 conformance score for the canonical arc.

    The metric: for every ordered pair (i, j) where i < j, the pair is
    "in order" if the phase rank of i is <= phase rank of j. We return
    the percentage of in-order pairs plus a tuple of inversions
    (chapter indexes that violate the arc).
    """

    if len(sequence) < 2:
        return 100, ()

    pairs_total = 0
    pairs_in_order = 0
    inversions: list[tuple[int, int]] = []
    for i, left in enumerate(sequence):
        for right in sequence[i + 1 :]:
            pairs_total += 1
            if PHASE_RANK[left.phase] <= PHASE_RANK[right.phase]:
                pairs_in_order += 1
            else:
                inversions.append((left.index, right.index))
    score = round(pairs_in_order / pairs_total * 100)
    return score, tuple(inversions)


def _missing_phases(sequence: tuple[ChapterPhase, ...]) -> tuple[str, ...]:
    present = {item.phase for item in sequence}
    return tuple(phase for phase in CANONICAL_PHASES if phase not in present)


def _fix_for_inversion(
    sequence: tuple[ChapterPhase, ...], inversion: tuple[int, int]
) -> str:
    left_idx, right_idx = inversion
    left = next((item for item in sequence if item.index == left_idx), None)
    right = next((item for item in sequence if item.index == right_idx), None)
    if left is None or right is None:
        return (
            f"Kapitel-Reihenfolge pruefen: Kapitel {left_idx} kommt nach "
            f"Kapitel {right_idx} entgegen dem klassischen Bogen."
        )
    return (
        f"Kapitel {left.index} ('{left.title}') liegt in der Phase {left.phase}, "
        f"steht aber vor Kapitel {right.index} ('{right.title}') in Phase "
        f"{right.phase}. Im klassischen Sachbuch-Bogen kommt {right.phase} "
        f"vor {left.phase} — pruefe, ob du das Kapitel verschieben oder "
        f"umetikettieren willst."
    )


def _fix_for_missing(phase: str) -> str:
    fixes = {
        PHASE_PROBLEM: (
            "Es fehlt ein klares Problem-Kapitel. Eroeffne das Buch mit der "
            "konkreten Schmerzlage, die der Leser heute hat — sonst weiss "
            "er nicht, warum er weiterlesen soll."
        ),
        PHASE_SOLUTION: (
            "Es fehlt ein erkennbares Loesungs-/Methoden-Kapitel. Benenne "
            "deinen Ansatz mit Namen (Framework, System, Prinzip) — sonst "
            "wirkt das Buch wie eine Sammlung guter Tipps."
        ),
        PHASE_PROOF: (
            "Es fehlt ein Beweis-Kapitel. Streue mindestens eine Fallstudie "
            "mit Zahlen, Namen, Ergebnissen ein — sonst bleibt die Methode "
            "Behauptung."
        ),
        PHASE_TRANSFORMATION: (
            "Es fehlt ein Transformations-Kapitel am Ende. Zeige dem Leser, "
            "wie sein Alltag in 30/90 Tagen aussieht, wenn er die Methode "
            "anwendet — sonst legt er das Buch ohne Schub weg."
        ),
    }
    return fixes.get(
        phase,
        f"Phase '{phase}' fehlt in der Kapitel-Reihe. Pruefe den Aufbau.",
    )


def build_arc_report(chapters: list[Chapter]) -> ArcReport:
    """Classify all chapters and score arc conformance."""

    if not chapters:
        return ArcReport(
            sequence=(),
            arc_score=0,
            status="FIX",
            inversions=(),
            missing_phases=CANONICAL_PHASES,
            fixes=(
                "Keine Kapitel erkannt — Pruefe, ob das Manuskript "
                "Word-Ueberschriften-Stile (Heading 1/Ueberschrift 1) verwendet.",
            ),
        )

    total = len(chapters)
    sequence: list[ChapterPhase] = []
    for chapter in chapters:
        position_ratio = (chapter.index - 1) / max(1, total - 1) if total > 1 else 0.0
        sequence.append(_classify_chapter(chapter, position_ratio))
    seq_tuple = tuple(sequence)

    score, inversions = _arc_score(seq_tuple)
    missing = _missing_phases(seq_tuple)

    fixes: list[str] = []
    for inversion in inversions[:5]:
        fixes.append(_fix_for_inversion(seq_tuple, inversion))
    for phase in missing:
        fixes.append(_fix_for_missing(phase))

    # Penalize missing phases — each missing phase removes 15 points so
    # a perfectly-ordered set of 3 phases caps at 85, leaving headroom
    # for "READY" only when all four phases are present and in order.
    adjusted = max(0, score - 15 * len(missing))
    status = _status_for(adjusted)

    return ArcReport(
        sequence=seq_tuple,
        arc_score=adjusted,
        status=status,
        inversions=inversions,
        missing_phases=missing,
        fixes=tuple(fixes),
    )


def render_arc_report_markdown(project: BookProject, report: ArcReport) -> str:
    """Author-facing Kapitel-Reihungscheck markdown."""

    title = project.title or project.project_id
    lines: list[str] = [
        "# Kapitel-Reihungscheck",
        "",
        f"Buch: **{title}**",
        f"Arc-Score: **{report.arc_score}/100** ({report.status})",
        "",
        "## Klassischer Sachbuch-Bogen",
        "",
        "1. **PROBLEM** — Was geht heute schief?",
        "2. **LÖSUNG** — Welche Methode loest es?",
        "3. **BEWEIS** — Warum funktioniert sie wirklich?",
        "4. **TRANSFORMATION** — Wie sieht der Leser-Alltag danach aus?",
        "",
    ]

    if not report.sequence:
        lines.extend([
            "## Status",
            "",
            "Keine Kapitel erkannt — der Bogen kann nicht bewertet werden. ",
            "Stelle sicher, dass das Manuskript Word-Ueberschriften "
            "(Heading 1 / Ueberschrift 1) verwendet.",
        ])
        return "\n".join(lines)

    lines.extend([
        "## Phase pro Kapitel",
        "",
        "| # | Kapitel | Phase | Konfidenz |",
        "|---|---------|-------|-----------|",
    ])
    for item in report.sequence:
        emoji = PHASE_EMOJI.get(item.phase, "•")
        title_safe = item.title.replace("|", "/")[:60]
        lines.append(
            f"| {item.index} | {title_safe} | {emoji} {item.phase} | {item.confidence}% |"
        )

    if report.inversions:
        lines.extend(["", "## Reihenfolge-Konflikte", ""])
        for left_idx, right_idx in report.inversions[:10]:
            lines.append(
                f"- Kapitel {left_idx} liegt vor Kapitel {right_idx}, obwohl "
                f"die Phase erst spaeter dran waere."
            )
    else:
        lines.extend(["", "## Reihenfolge-Konflikte", "", "Keine — der Bogen ist konsistent."])

    if report.missing_phases:
        lines.extend(["", "## Fehlende Phasen", ""])
        for phase in report.missing_phases:
            lines.append(f"- {PHASE_EMOJI.get(phase, '•')} **{phase}** fehlt im Buch.")
    else:
        lines.extend(["", "## Fehlende Phasen", "", "Alle vier Phasen sind im Buch vertreten."])

    if report.fixes:
        lines.extend(["", "## Konkrete Fixes", ""])
        for fix in report.fixes:
            lines.append(f"- {fix}")

    return "\n".join(lines)
