"""Per-chapter analysis for German nonfiction manuscripts.

Splits a DOCX (or generic paragraph stream) into chapters using heading
styles, then scores each chapter on four reader-impact dimensions:
Versprechen (promise), Beweis (proof), Wert (value), Übergang (transition).

Designed to run fully offline so tests don't need an LLM or filesystem.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from modules.scoring import (
    SCORE_BADGE_FIX,
    SCORE_BADGE_READY,
    SCORE_BADGE_REVIEW,
    SCORE_READY,
    SCORE_REVIEW,
)

# Word-count balance thresholds. A chapter is flagged as a SPLIT candidate
# when its word count exceeds the median by ``OVERSIZED_FACTOR`` and as a
# MERGE candidate when it falls below the median by ``UNDERSIZED_FACTOR``.
# Below ``BALANCE_MIN_CHAPTERS`` chapters the median is not meaningful, so
# the balance analysis returns an empty report.
OVERSIZED_FACTOR: float = 3.0
UNDERSIZED_FACTOR: float = 0.3
BALANCE_MIN_CHAPTERS: int = 3


@dataclass(frozen=True)
class BalanceThresholds:
    """Tunable parameters for chapter-balance outlier detection.

    Defaults match the module-level constants used historically — pass
    a custom instance when authors write lesson-style nonfiction (many
    short chapters) where the legacy 3.0×/0.3× thresholds would flag
    nearly every chapter as undersized.
    """

    oversized_factor: float = OVERSIZED_FACTOR
    undersized_factor: float = UNDERSIZED_FACTOR
    min_chapters: int = BALANCE_MIN_CHAPTERS


DEFAULT_BALANCE_THRESHOLDS = BalanceThresholds()


def balance_thresholds_from_app(app_config: Any) -> BalanceThresholds:
    """Build BalanceThresholds from an AppConfig — single wiring point."""

    return BalanceThresholds(
        oversized_factor=float(
            getattr(app_config, "balance_oversized_factor", OVERSIZED_FACTOR)
        ),
        undersized_factor=float(
            getattr(app_config, "balance_undersized_factor", UNDERSIZED_FACTOR)
        ),
        min_chapters=int(
            getattr(app_config, "balance_min_chapters", BALANCE_MIN_CHAPTERS)
        ),
    )

# Status → unified score-badge emoji mapping. Single source of truth in
# modules.scoring so all reports stay in lockstep when the scheme changes.
_STATUS_EMOJI: dict[str, str] = {
    "READY": SCORE_BADGE_READY,
    "REVIEW": SCORE_BADGE_REVIEW,
    "FIX": SCORE_BADGE_FIX,
}

# Heading style markers used by Word in German + English templates.
HEADING_STYLE_TOKENS: tuple[str, ...] = ("heading", "überschrift", "uberschrift")

# Minimum body words for a real chapter — anything below is treated as a
# section divider / front matter and merged into the previous chapter.
MIN_CHAPTER_WORDS = 80

# Heuristic vocabularies tuned for German nonfiction. Kept small and
# pattern-only — the LLM-driven fix lines live in modules/review.py.
PROMISE_MARKERS = (
    r"\b(du\s+(?:wirst|lernst|erf[äa]hrst|bekommst)|in diesem kapitel|am ende|ziel|"
    r"versprechen|verstehen|umsetzen|zeige dir|zeigt dir|hier liest du|"
    r"darum geht es|warum|was du|was dich)\b"
)
PROOF_MARKERS = (
    r"(\d+\s*(?:euro|€|\$|stunden|tage|wochen|monate|jahre|%|prozent|"
    r"seiten|kunden|projekte|fehler|minuten|sekunden|punkte|beispiele|"
    r"f[äa]lle|teilnehmer)|fallstudie|case study|aus eigener erfahrung|"
    r"selbst getestet|live-projekt|in der praxis|beispiel:|fakten|"
    r"statistik|studie|messung|zahlen|kpi|ergebnis|abbildung\s*\d+|"
    r"tabelle\s*\d+)"
)
VALUE_MARKERS = (
    r"\b(checkliste|vorlage|schritt-?f[üu]r-?schritt|methode|framework|"
    r"system|leitfaden|werkzeug|tool|anleitung|prinzip|regel|rezept|"
    r"merksatz|kontrollpunkt|aufgabe|[üu]bung|template|matrix|prozess|"
    r"playbook)\b"
)
TRANSITION_MARKERS = (
    r"\b(im n[äa]chsten kapitel|als n[äa]chstes|weiter geht es|"
    r"darauf bauen wir auf|n[äa]chster schritt|n[äa]chste seite|"
    r"jetzt wissen wir|wir haben gesehen|halten wir fest|"
    r"zusammenfassung|kurz gesagt|merke dir|kapitelende|fazit|"
    r"checkpoint|kontrollfrage|reflexionsfrage)\b"
)


@dataclass(frozen=True)
class Chapter:
    """A single manuscript chapter with its body text and position."""

    index: int
    title: str
    body: str
    word_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "word_count": self.word_count,
        }


@dataclass(frozen=True)
class ChapterScore:
    """Heuristic score for one chapter on four reader-impact dimensions."""

    index: int
    title: str
    word_count: int
    promise: int
    proof: int
    value: int
    transition: int
    overall: int
    status: str
    fix: str

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "word_count": self.word_count,
            "scores": {
                "promise": self.promise,
                "proof": self.proof,
                "value": self.value,
                "transition": self.transition,
            },
            "overall": self.overall,
            "status": self.status,
            "fix": self.fix,
        }


def _is_heading(style: str) -> bool:
    style_l = (style or "").lower()
    return any(token in style_l for token in HEADING_STYLE_TOKENS)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÄÖÜäöüß-]+\b", text, flags=re.UNICODE))


def split_paragraphs_into_chapters(
    paragraphs: Iterable[dict[str, Any]],
) -> list[Chapter]:
    """Walk a paragraph stream and group bodies under heading boundaries.

    Each paragraph dict must carry ``text`` and ``style``. The output is
    deterministic and never mutates inputs. Tiny sections (under
    ``MIN_CHAPTER_WORDS``) are merged into the preceding chapter so that
    front matter / blurbs don't pollute the report.
    """

    raw_chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for item in paragraphs:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        style = item.get("style") or ""
        if _is_heading(style):
            if current is not None:
                raw_chapters.append(current)
            current = {"title": text, "body_parts": []}
        else:
            if current is None:
                current = {"title": "Vorwort", "body_parts": []}
            current["body_parts"].append(text)
    if current is not None:
        raw_chapters.append(current)

    # Merge tiny tail-chapters back into the previous chapter so a
    # heading-only "Danke" doesn't get its own score row.
    merged: list[dict[str, Any]] = []
    for chap in raw_chapters:
        body = "\n".join(chap["body_parts"])
        words = _word_count(body)
        if words < MIN_CHAPTER_WORDS and merged:
            merged[-1]["body_parts"].extend(chap["body_parts"])
        else:
            merged.append({"title": chap["title"], "body_parts": chap["body_parts"]})

    chapters: list[Chapter] = []
    for idx, chap in enumerate(merged, start=1):
        body = "\n".join(chap["body_parts"])
        chapters.append(
            Chapter(index=idx, title=chap["title"], body=body, word_count=_word_count(body))
        )
    return chapters


def extract_docx_chapters(path: Any) -> list[Chapter]:
    """Read a DOCX file and return its chapters. Pure I/O wrapper."""

    from modules.readers import open_docx_paragraphs

    doc = open_docx_paragraphs(path)
    paragraphs: list[dict[str, Any]] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style else ""
        paragraphs.append({"text": text, "style": style})
    return split_paragraphs_into_chapters(paragraphs)


# Default cap for the first-paragraph snippet shipped to the LLM
# bullet-extractor prompt. 400 characters captures the opening claim
# without bloating the prompt or leaking large portions of the manuscript.
CHAPTER_INTRO_MAX_CHARS: int = 400


def _first_paragraph(body: str) -> str:
    """Return the first non-empty line from a chapter body, stripped."""

    if not body:
        return ""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _clip_intro(text: str, max_chars: int) -> str:
    """Hard-cap an intro to ``max_chars`` without splitting a UTF-8 codepoint.

    The cap is character-based, ASCII ellipsis is appended when the
    text was truncated so the LLM gets a clear signal the snippet ends
    mid-thought. ``max_chars`` <= 0 returns an empty string.
    """

    if max_chars <= 0 or not text:
        return ""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars].rstrip()
    return f"{head}…"


def extract_chapter_intros(
    chapters: Sequence[Chapter],
    *,
    max_chars: int = CHAPTER_INTRO_MAX_CHARS,
) -> list[tuple[str, str]]:
    """Build (title, intro) pairs for the LLM bullet prompt.

    The intro is the first non-empty paragraph of each chapter body,
    clipped to ``max_chars`` characters. Chapters without a usable body
    are still returned with an empty intro so the caller can decide
    whether to show only the title or skip the chapter entirely.
    Pure function: never reads from disk, never mutates inputs.
    """

    return [
        (chapter.title, _clip_intro(_first_paragraph(chapter.body), max_chars))
        for chapter in chapters
    ]


def _marker_score(text: str, pattern: str, target_hits: int) -> int:
    """Translate marker-hit count into a 1-10 score with diminishing returns."""

    hits = len(re.findall(pattern, text, flags=re.I))
    if hits == 0:
        return 2
    if hits >= target_hits:
        return 10
    ratio = hits / target_hits
    return max(2, min(10, round(2 + ratio * 8)))


def _status_for(score: int) -> str:
    if score >= SCORE_READY:
        return "READY"
    if score >= SCORE_REVIEW:
        return "REVIEW"
    return "FIX"


def _fix_for(chapter: Chapter, weakest: str) -> str:
    title = chapter.title.strip() or f"Kapitel {chapter.index}"
    fixes = {
        "promise": (
            f"Setze in den ersten drei Saetzen von '{title}' ein klares Leser-Versprechen: "
            "Was lernt der Leser, fuer wen, und warum jetzt?"
        ),
        "proof": (
            f"Verankere '{title}' mit mindestens einem konkreten Beweis: Zahl, Fallstudie, "
            "Praxisbeispiel oder Messung. Vermeide reine Behauptungen."
        ),
        "value": (
            f"Liefere in '{title}' mindestens eine umsetzbare Sache: Checkliste, Vorlage, "
            "Schritt-fuer-Schritt-Anleitung oder klare Regel."
        ),
        "transition": (
            f"Beende '{title}' mit einem expliziten Uebergang ins naechste Kapitel oder einer "
            "kurzen Zusammenfassung, damit der Leser weiterliest."
        ),
        "length": (
            f"'{title}' ist sehr kurz ({chapter.word_count} Woerter). Erweitere die Substanz "
            "oder verschmelze das Kapitel mit einem benachbarten Thema."
        ),
    }
    if chapter.word_count < 200:
        return fixes["length"]
    return fixes.get(weakest, fixes["value"])


def score_chapter(chapter: Chapter) -> ChapterScore:
    """Score one chapter on the four reader-impact dimensions (1-10 each)."""

    body = chapter.body
    promise = _marker_score(body, PROMISE_MARKERS, target_hits=2)
    proof = _marker_score(body, PROOF_MARKERS, target_hits=3)
    value = _marker_score(body, VALUE_MARKERS, target_hits=2)
    transition = _marker_score(body, TRANSITION_MARKERS, target_hits=1)

    # Convert per-dimension 1-10 into a single 0-100 score weighted by
    # reader impact: proof and value carry slightly more weight than the
    # framing dimensions.
    weighted = promise * 2 + proof * 3 + value * 3 + transition * 2  # max = 100
    overall = max(0, min(100, weighted))

    dims = {
        "promise": promise,
        "proof": proof,
        "value": value,
        "transition": transition,
    }
    weakest = min(dims, key=dims.get)
    fix = _fix_for(chapter, weakest)

    return ChapterScore(
        index=chapter.index,
        title=chapter.title,
        word_count=chapter.word_count,
        promise=promise,
        proof=proof,
        value=value,
        transition=transition,
        overall=overall,
        status=_status_for(overall),
        fix=fix,
    )


@dataclass(frozen=True)
class ChapterBalanceOutlier:
    """A chapter whose word count diverges sharply from the median.

    ``kind`` is either ``"oversized"`` (split candidate) or
    ``"undersized"`` (merge candidate). ``ratio`` is the multiple of the
    median word count, rounded to one decimal — surfaced to the author
    so they immediately see "Kapitel X ist 4.2× so lang wie der
    Durchschnitt".
    """

    index: int
    title: str
    word_count: int
    median: int
    ratio: float
    kind: str
    fix: str

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "word_count": self.word_count,
            "median": self.median,
            "ratio": self.ratio,
            "kind": self.kind,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class ChapterBalanceReport:
    """Aggregate of word-count outliers for a chapter set."""

    median_word_count: int
    oversized: list[ChapterBalanceOutlier] = field(default_factory=list)
    undersized: list[ChapterBalanceOutlier] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "median_word_count": self.median_word_count,
            "oversized": [o.to_json() for o in self.oversized],
            "undersized": [o.to_json() for o in self.undersized],
        }

    @property
    def has_findings(self) -> bool:
        return bool(self.oversized) or bool(self.undersized)


def _balance_fix(chapter: Chapter, kind: str, ratio: float) -> str:
    title = chapter.title.strip() or f"Kapitel {chapter.index}"
    ratio_str = f"{ratio:.1f}×".replace(".0×", "×")
    if kind == "oversized":
        return (
            f"'{title}' ist {ratio_str} so lang wie der Durchschnitt "
            f"({chapter.word_count} Woerter). Erwaege das Kapitel in zwei "
            "Teile zu splitten — z.B. ein eigenes Kapitel fuer den "
            "Beweisteil oder das Praxisbeispiel."
        )
    return (
        f"'{title}' ist nur {ratio_str} der durchschnittlichen Laenge "
        f"({chapter.word_count} Woerter). Erwaege das Kapitel mit einem "
        "benachbarten Thema zusammenzulegen, damit der Leser einen "
        "echten Block bekommt."
    )


def analyze_chapter_balance(
    chapters: list[Chapter],
    *,
    thresholds: BalanceThresholds | None = None,
    oversized_factor: float | None = None,
    undersized_factor: float | None = None,
    min_chapters: int | None = None,
) -> ChapterBalanceReport:
    """Detect chapters whose word count diverges sharply from the median.

    Pure function: never mutates ``chapters``. Returns an empty report
    when there are fewer than ``thresholds.min_chapters`` real chapters,
    when the median word count is zero, or when no outliers are found.
    Outliers are returned in deterministic order: oversized by decreasing
    word count (split the longest first), undersized by ascending word
    count (merge the shortest first). Ties break by chapter index.

    Legacy single-factor kwargs ``oversized_factor``/``undersized_factor``/
    ``min_chapters`` remain accepted and override the corresponding
    ``thresholds`` fields when explicitly provided — preserves backwards
    compatibility for in-tree callers.
    """

    base = thresholds or DEFAULT_BALANCE_THRESHOLDS
    eff_oversized = oversized_factor if oversized_factor is not None else base.oversized_factor
    eff_undersized = (
        undersized_factor if undersized_factor is not None else base.undersized_factor
    )
    eff_min_chapters = min_chapters if min_chapters is not None else base.min_chapters

    real = [c for c in chapters if c.word_count > 0]
    if len(real) < eff_min_chapters:
        return ChapterBalanceReport(median_word_count=0)
    median = int(statistics.median(c.word_count for c in real))
    if median <= 0:
        return ChapterBalanceReport(median_word_count=0)

    oversized: list[ChapterBalanceOutlier] = []
    undersized: list[ChapterBalanceOutlier] = []
    upper = median * eff_oversized
    lower = median * eff_undersized
    for chap in real:
        ratio = round(chap.word_count / median, 1)
        if chap.word_count > upper:
            oversized.append(
                ChapterBalanceOutlier(
                    index=chap.index,
                    title=chap.title,
                    word_count=chap.word_count,
                    median=median,
                    ratio=ratio,
                    kind="oversized",
                    fix=_balance_fix(chap, "oversized", ratio),
                )
            )
        elif chap.word_count < lower:
            undersized.append(
                ChapterBalanceOutlier(
                    index=chap.index,
                    title=chap.title,
                    word_count=chap.word_count,
                    median=median,
                    ratio=ratio,
                    kind="undersized",
                    fix=_balance_fix(chap, "undersized", ratio),
                )
            )
    oversized.sort(key=lambda o: (-o.word_count, o.index))
    undersized.sort(key=lambda o: (o.word_count, o.index))
    return ChapterBalanceReport(
        median_word_count=median,
        oversized=oversized,
        undersized=undersized,
    )


@dataclass(frozen=True)
class ChapterReport:
    chapters: list[ChapterScore]
    average_score: int
    weakest_chapter_index: int | None
    fixes: list[str] = field(default_factory=list)
    balance: ChapterBalanceReport | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chapters": [c.to_json() for c in self.chapters],
            "average_score": self.average_score,
            "weakest_chapter_index": self.weakest_chapter_index,
            "fixes": list(self.fixes),
        }
        if self.balance is not None:
            payload["balance"] = self.balance.to_json()
        return payload


def top_weakest_chapters(report: ChapterReport, limit: int = 3) -> list[ChapterScore]:
    """Return the N weakest chapters by overall score (ascending).

    Pure function: never mutates ``report``. ``limit`` is clamped to the
    actual chapter count so callers don't need to guard against empty
    manuscripts. Ties are broken by chapter index to keep output stable.
    """

    if limit <= 0 or not report.chapters:
        return []
    ordered = sorted(report.chapters, key=lambda c: (c.overall, c.index))
    return ordered[: min(limit, len(ordered))]


def build_chapter_report(
    chapters: list[Chapter],
    *,
    balance_thresholds: BalanceThresholds | None = None,
) -> ChapterReport:
    """Score every chapter and aggregate into a ChapterReport."""

    if not chapters:
        return ChapterReport(
            chapters=[],
            average_score=0,
            weakest_chapter_index=None,
            fixes=[],
            balance=ChapterBalanceReport(median_word_count=0),
        )
    scores = [score_chapter(ch) for ch in chapters]
    avg = round(sum(s.overall for s in scores) / len(scores))
    weakest = min(scores, key=lambda s: s.overall)
    balance = analyze_chapter_balance(chapters, thresholds=balance_thresholds)
    fixes = [s.fix for s in scores if s.status != "READY"]
    fixes.extend(o.fix for o in balance.oversized)
    fixes.extend(o.fix for o in balance.undersized)
    return ChapterReport(
        chapters=scores,
        average_score=avg,
        weakest_chapter_index=weakest.index,
        fixes=fixes,
        balance=balance,
    )


def render_chapter_report_markdown(title: str, report: ChapterReport) -> str:
    """Human-readable per-chapter report — beginner-friendly, German."""

    if not report.chapters:
        return (
            "# Kapitel-Analyse\n\n"
            f"Buch: **{title}**\n\n"
            "Es konnten keine Kapitel erkannt werden. Pruefe, ob das Manuskript "
            "Word-Ueberschriften-Stile (Heading 1/Ueberschrift 1) verwendet."
        )

    lines = [
        "# Kapitel-Analyse",
        "",
        f"Buch: **{title}**",
        f"Erkannte Kapitel: **{len(report.chapters)}**",
        f"Durchschnitts-Score: **{report.average_score}/100**",
        "",
        "## Pro Kapitel",
        "",
        "| # | Kapitel | Woerter | Versprechen | Beweis | Wert | Uebergang | Score | Status |",
        "|---|---------|---------|-------------|--------|------|-----------|-------|--------|",
    ]
    for chap in report.chapters:
        title_safe = chap.title.replace("|", "/")[:60]
        lines.append(
            f"| {chap.index} | {title_safe} | {chap.word_count} | "
            f"{chap.promise}/10 | {chap.proof}/10 | {chap.value}/10 | "
            f"{chap.transition}/10 | {chap.overall}/100 | {chap.status} |"
        )

    lines.extend(["", "## Konkrete Fixes pro Kapitel", ""])
    for chap in report.chapters:
        emoji = _STATUS_EMOJI.get(chap.status, "⚪")
        lines.append(f"### {emoji} Kapitel {chap.index} — {chap.title}")
        lines.append("")
        lines.append(f"- Score: **{chap.overall}/100** ({chap.status})")
        lines.append(f"- Fix: {chap.fix}")
        lines.append("")

    lines.extend(_render_balance_section(report.balance))
    return "\n".join(lines)


def _render_balance_section(balance: ChapterBalanceReport | None) -> list[str]:
    """Render the Kapitel-Balance section. Empty list when nothing to flag."""

    if balance is None or not balance.has_findings:
        return []
    lines: list[str] = [
        "## Kapitel-Balance",
        "",
        (
            f"Median-Wortzahl pro Kapitel: **{balance.median_word_count}**. "
            "Schwellen: Split-Kandidat ab "
            f"{OVERSIZED_FACTOR:g}× Median, Merge-Kandidat unter "
            f"{UNDERSIZED_FACTOR:g}× Median."
        ),
        "",
    ]
    if balance.oversized:
        lines.append("### Split-Kandidaten (zu lang)")
        lines.append("")
        for outlier in balance.oversized:
            title_safe = outlier.title.replace("|", "/")[:80]
            lines.append(
                f"- 🔴 **Kapitel {outlier.index} — {title_safe}** "
                f"({outlier.word_count} Woerter, {outlier.ratio:g}× Median)"
            )
            lines.append(f"  Fix: {outlier.fix}")
        lines.append("")
    if balance.undersized:
        lines.append("### Merge-Kandidaten (zu kurz)")
        lines.append("")
        for outlier in balance.undersized:
            title_safe = outlier.title.replace("|", "/")[:80]
            lines.append(
                f"- 🟡 **Kapitel {outlier.index} — {title_safe}** "
                f"({outlier.word_count} Woerter, {outlier.ratio:g}× Median)"
            )
            lines.append(f"  Fix: {outlier.fix}")
        lines.append("")
    return lines
