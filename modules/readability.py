"""German readability analysis (Amstad's Flesch-Reading-Ease).

Computes a deterministic, LLM-free readability score per chapter and for
the manuscript as a whole. Uses the Amstad German adaptation of the
Flesch-Reading-Ease formula::

    FRE = 180 - ASL - (58.5 * ASW)

where ``ASL`` is the average sentence length in words and ``ASW`` is the
average number of syllables per word. Higher values are easier; popular
nonfiction targets the 60-80 band.

Pure-Python, no external dependencies, safe to run in offline QA mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from modules.chapters import Chapter, extract_docx_chapters
from modules.scoring import (
    SCORE_BADGE_FIX,
    SCORE_BADGE_READY,
    SCORE_BADGE_REVIEW,
)


# Target band for popular German nonfiction (Sachbuch). FRE 60-80
# corresponds roughly to B1/B2 reading level — the band most KDP
# nonfiction buyers expect. Below 50 the text starts to feel academic;
# above 90 it reads like children's literature.
DEFAULT_TARGET_MIN: int = 50
DEFAULT_TARGET_MAX: int = 80

# Minimum body words for a chapter readability metric to be meaningful.
# Below this we still compute the metric but flag the chapter as
# "zu_kurz" so the author isn't told to simplify a 30-word foreword.
MIN_BODY_WORDS_FOR_SIGNAL: int = 60

# Hard floor for a single-word manuscript so the report doesn't crash
# on empty input. ``compute_amstad_fre`` returns sentinel zeros when
# no words are present.
EMPTY_FRE: float = 0.0

# Amstad German FRE level bands. Keys match the JSON output; the German
# labels surface in the markdown report. Bands follow the original
# Amstad (1978) classification.
LEVEL_BANDS: tuple[tuple[float, str, str], ...] = (
    (90.0, "sehr_leicht", "Sehr leicht (A1/A2)"),
    (80.0, "leicht", "Leicht (A2/B1)"),
    (70.0, "mittel_leicht", "Mittel leicht (B1)"),
    (60.0, "mittel", "Mittel (B1/B2)"),
    (50.0, "mittel_schwer", "Mittel schwer (B2)"),
    (30.0, "schwer", "Schwer (C1)"),
    (0.0, "sehr_schwer", "Sehr schwer (C2/akademisch)"),
)


_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*")
# Conservative German sentence splitter. Treats . ! ? as sentence
# terminators when followed by whitespace + capital letter OR end of
# string. Avoids splitting on ellipses ("...") by collapsing them.
_SENTENCE_TERMINATORS_RE = re.compile(r"[.!?]+")
# Vowel groups for syllable estimation (German). Diphthongs (ei, ie,
# eu, äu, au) are treated as a single vowel group via the regex which
# matches one or more adjacent vowels.
_VOWEL_GROUP_RE = re.compile(r"[aeiouäöüyAEIOUÄÖÜY]+")


def count_words(text: str) -> int:
    """Count word-like tokens in ``text`` (letters + optional hyphen).

    Conservative: digits-only tokens are excluded because they have no
    syllable count under the Amstad formula. ASCII apostrophes/quotes
    are word separators.
    """

    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def iter_words(text: str) -> Iterable[str]:
    """Yield word tokens in ``text`` (lower-cased for syllable counting)."""

    if not text:
        return
    for match in _WORD_RE.finditer(text):
        yield match.group(0).lower()


def count_sentences(text: str) -> int:
    """Count sentences in ``text``. Floor of 1 when any words are present.

    The Amstad formula divides by sentence count, so a chapter with text
    but no terminator must still produce a usable metric — treat the
    whole body as one sentence in that case. Returns 0 for empty input.
    """

    if not text or not text.strip():
        return 0
    cleaned = re.sub(r"\.{2,}", ".", text)
    raw_count = len(_SENTENCE_TERMINATORS_RE.findall(cleaned))
    if raw_count <= 0:
        return 1 if count_words(text) > 0 else 0
    return raw_count


def count_syllables_de(word: str) -> int:
    """Estimate syllables in a German word via vowel-group counting.

    Each contiguous run of vowels (a/e/i/o/u/ä/ö/ü/y) counts as one
    syllable. Returns at least 1 for any non-empty token. This is the
    standard heuristic for German FRE implementations — it matches the
    Amstad reference within ±10% for typical Sachbuch text.
    """

    if not word:
        return 0
    groups = _VOWEL_GROUP_RE.findall(word)
    if not groups:
        return 1
    return len(groups)


@dataclass(frozen=True)
class ReadabilityMetric:
    """FRE metric for one body of text (chapter or aggregate)."""

    label: str
    index: int | None
    word_count: int
    sentence_count: int
    syllable_count: int
    avg_sentence_length: float
    avg_syllables_per_word: float
    fre_score: float
    level_key: str
    level_label: str
    fix: str

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "index": self.index,
            "word_count": self.word_count,
            "sentence_count": self.sentence_count,
            "syllable_count": self.syllable_count,
            "avg_sentence_length": round(self.avg_sentence_length, 2),
            "avg_syllables_per_word": round(self.avg_syllables_per_word, 2),
            "fre_score": round(self.fre_score, 1),
            "level_key": self.level_key,
            "level_label": self.level_label,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class ReadabilityReport:
    """Aggregate readability report for a manuscript."""

    overall: ReadabilityMetric
    chapters: tuple[ReadabilityMetric, ...] = field(default_factory=tuple)
    target_min: int = DEFAULT_TARGET_MIN
    target_max: int = DEFAULT_TARGET_MAX
    weakest_index: int | None = None
    fixes: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "overall": self.overall.to_json(),
            "chapters": [c.to_json() for c in self.chapters],
            "target_min": self.target_min,
            "target_max": self.target_max,
            "weakest_index": self.weakest_index,
            "fixes": list(self.fixes),
        }

    @property
    def is_in_target(self) -> bool:
        return self.target_min <= self.overall.fre_score <= self.target_max


def classify_fre(fre: float) -> tuple[str, str]:
    """Map an FRE score to ``(level_key, level_label)``.

    Bands follow Amstad's (1978) German Flesch classification. Scores
    below 0 are clamped to ``sehr_schwer``; scores above 100 to
    ``sehr_leicht`` — the formula can produce out-of-band values for
    extreme inputs.
    """

    for threshold, key, label in LEVEL_BANDS:
        if fre >= threshold:
            return key, label
    return LEVEL_BANDS[-1][1], LEVEL_BANDS[-1][2]


def compute_amstad_fre(
    text: str,
) -> tuple[float, int, int, int, float, float]:
    """Compute the Amstad German FRE score for ``text``.

    Returns ``(fre, words, sentences, syllables, asl, asw)``. When
    ``text`` has no words, returns ``(0.0, 0, 0, 0, 0.0, 0.0)`` so
    callers can render a "kein Signal" hint instead of crashing on a
    division by zero.
    """

    words = list(iter_words(text))
    word_count = len(words)
    if word_count == 0:
        return EMPTY_FRE, 0, 0, 0, 0.0, 0.0
    sentence_count = max(1, count_sentences(text))
    syllable_count = sum(count_syllables_de(w) for w in words)
    asl = word_count / sentence_count
    asw = syllable_count / word_count
    fre = 180.0 - asl - (58.5 * asw)
    return fre, word_count, sentence_count, syllable_count, asl, asw


def _fix_for_metric(
    metric_word_count: int,
    fre: float,
    label: str,
    *,
    target_min: int,
    target_max: int,
) -> str:
    """Build a concrete fix line for a chapter outside the target band."""

    safe_label = label.strip() or "Dieses Kapitel"
    if metric_word_count < MIN_BODY_WORDS_FOR_SIGNAL:
        return (
            f"'{safe_label}' ist zu kurz fuer eine belastbare Lesbarkeits-Messung "
            f"({metric_word_count} Woerter). Inhalt ausbauen oder mit Nachbarkapitel verschmelzen."
        )
    if fre < target_min:
        return (
            f"'{safe_label}' liest sich zu schwer (FRE {fre:.0f} < {target_min}). "
            "Kuerze lange Saetze, ersetze Fachjargon und nutze mehr aktive Verben."
        )
    if fre > target_max:
        return (
            f"'{safe_label}' liest sich sehr einfach (FRE {fre:.0f} > {target_max}). "
            "Pruefe, ob die Zielgruppe mehr Tiefe und Fachpraezision erwartet."
        )
    return ""


def _metric_for_text(
    body: str,
    *,
    label: str,
    index: int | None,
    target_min: int,
    target_max: int,
) -> ReadabilityMetric:
    fre, words, sentences, syllables, asl, asw = compute_amstad_fre(body)
    level_key, level_label = classify_fre(fre)
    fix = _fix_for_metric(
        words, fre, label, target_min=target_min, target_max=target_max
    )
    return ReadabilityMetric(
        label=label,
        index=index,
        word_count=words,
        sentence_count=sentences,
        syllable_count=syllables,
        avg_sentence_length=asl,
        avg_syllables_per_word=asw,
        fre_score=fre,
        level_key=level_key,
        level_label=level_label,
        fix=fix,
    )


def build_readability_report(
    chapters: list[Chapter],
    *,
    target_min: int = DEFAULT_TARGET_MIN,
    target_max: int = DEFAULT_TARGET_MAX,
) -> ReadabilityReport:
    """Build a per-chapter readability report from a chapter list.

    The ``overall`` metric is computed over the concatenated bodies so a
    long, easy chapter does not get out-voted by a short, hard one.
    ``weakest_index`` points to the chapter whose FRE score is furthest
    from the target band — that is the most impactful chapter to fix.
    """

    if not chapters:
        empty_overall = ReadabilityMetric(
            label="Gesamt",
            index=None,
            word_count=0,
            sentence_count=0,
            syllable_count=0,
            avg_sentence_length=0.0,
            avg_syllables_per_word=0.0,
            fre_score=EMPTY_FRE,
            level_key="sehr_schwer",
            level_label="Sehr schwer (C2/akademisch)",
            fix="Kein Manuskript gefunden — Lesbarkeit kann nicht gemessen werden.",
        )
        return ReadabilityReport(
            overall=empty_overall,
            chapters=tuple(),
            target_min=target_min,
            target_max=target_max,
            weakest_index=None,
            fixes=tuple(),
        )

    chapter_metrics: list[ReadabilityMetric] = []
    for chap in chapters:
        chapter_metrics.append(
            _metric_for_text(
                chap.body,
                label=chap.title or f"Kapitel {chap.index}",
                index=chap.index,
                target_min=target_min,
                target_max=target_max,
            )
        )

    full_body = "\n\n".join(chap.body for chap in chapters)
    overall = _metric_for_text(
        full_body,
        label="Gesamt",
        index=None,
        target_min=target_min,
        target_max=target_max,
    )

    weakest_index = _pick_weakest_chapter(
        chapter_metrics, target_min=target_min, target_max=target_max
    )
    fixes = tuple(m.fix for m in chapter_metrics if m.fix)
    return ReadabilityReport(
        overall=overall,
        chapters=tuple(chapter_metrics),
        target_min=target_min,
        target_max=target_max,
        weakest_index=weakest_index,
        fixes=fixes,
    )


def _pick_weakest_chapter(
    metrics: list[ReadabilityMetric],
    *,
    target_min: int,
    target_max: int,
) -> int | None:
    """Return the index of the chapter furthest outside the target band.

    Chapters with too few words to measure are skipped (no signal).
    When all chapters are in the target band, returns ``None`` — there
    is no weakest chapter to flag.
    """

    actionable = [
        m for m in metrics if m.word_count >= MIN_BODY_WORDS_FOR_SIGNAL and m.fix
    ]
    if not actionable:
        return None

    def deviation(metric: ReadabilityMetric) -> tuple[float, int]:
        if metric.fre_score < target_min:
            dev = target_min - metric.fre_score
        elif metric.fre_score > target_max:
            dev = metric.fre_score - target_max
        else:
            dev = 0.0
        return (-dev, metric.index or 0)

    worst = min(actionable, key=deviation)
    return worst.index


def readability_analysis_from_project(
    project: Any,
    *,
    target_min: int = DEFAULT_TARGET_MIN,
    target_max: int = DEFAULT_TARGET_MAX,
) -> ReadabilityReport:
    """Build a readability report from a discovered project.

    Falls back to an empty report when no manuscript file is present —
    keeps the pipeline running without crashing on incomplete projects.
    """

    manuscript = getattr(project, "manuscript", None)
    if not manuscript:
        return build_readability_report(
            [], target_min=target_min, target_max=target_max
        )
    chapters = extract_docx_chapters(manuscript)
    return build_readability_report(
        chapters, target_min=target_min, target_max=target_max
    )


def _badge_for_metric(
    fre: float, *, target_min: int, target_max: int
) -> str:
    if fre < target_min - 20 or fre > target_max + 20:
        return SCORE_BADGE_FIX
    if fre < target_min or fre > target_max:
        return SCORE_BADGE_REVIEW
    return SCORE_BADGE_READY


def render_readability_markdown(
    project_title: str, report: ReadabilityReport
) -> str:
    """Render the readability report as a beginner-friendly markdown page."""

    safe_title = project_title or "(ohne Titel)"
    overall = report.overall
    overall_badge = _badge_for_metric(
        overall.fre_score,
        target_min=report.target_min,
        target_max=report.target_max,
    )

    lines: list[str] = [
        "# Lesbarkeit (Amstad-FRE)",
        "",
        f"Buch: **{safe_title}**",
        f"Ziel-Band: **FRE {report.target_min}-{report.target_max}** "
        f"(populaeres deutsches Sachbuch, B1-B2)",
        "",
        f"Skala: {SCORE_BADGE_READY} im Ziel · {SCORE_BADGE_REVIEW} knapp daneben · "
        f"{SCORE_BADGE_FIX} weit ausserhalb",
        "",
        "## Gesamt",
        "",
        f"{overall_badge} **FRE {overall.fre_score:.1f}** — {overall.level_label}",
        f"- Woerter: {overall.word_count}",
        f"- Saetze: {overall.sentence_count}",
        f"- Silben: {overall.syllable_count}",
        f"- Durchschnittliche Satzlaenge: {overall.avg_sentence_length:.1f} Woerter",
        f"- Silben pro Wort: {overall.avg_syllables_per_word:.2f}",
        "",
    ]

    if overall.fix and overall.word_count >= MIN_BODY_WORDS_FOR_SIGNAL:
        lines.extend(["**Top-Fix Gesamt:** " + overall.fix, ""])

    if not report.chapters:
        lines.extend([
            "Es konnten keine Kapitel erkannt werden — die Lesbarkeit wurde nur "
            "auf das Gesamtmanuskript angewendet."
        ])
        return "\n".join(lines)

    lines.extend([
        "## Pro Kapitel",
        "",
        "| # | Kapitel | Woerter | Satzlaenge | Silben/Wort | FRE | Niveau | Status |",
        "|---|---------|---------|------------|-------------|-----|--------|--------|",
    ])
    for metric in report.chapters:
        badge = _badge_for_metric(
            metric.fre_score,
            target_min=report.target_min,
            target_max=report.target_max,
        )
        title_safe = (metric.label or f"Kapitel {metric.index}").replace("|", "/")[:60]
        lines.append(
            f"| {metric.index} | {title_safe} | {metric.word_count} | "
            f"{metric.avg_sentence_length:.1f} | "
            f"{metric.avg_syllables_per_word:.2f} | "
            f"{metric.fre_score:.0f} | {metric.level_label} | {badge} |"
        )

    if report.fixes:
        lines.extend(["", "## Konkrete Fixes", ""])
        for fix in report.fixes:
            lines.append(f"- {fix}")

    if report.weakest_index is not None:
        weakest = next(
            (m for m in report.chapters if m.index == report.weakest_index),
            None,
        )
        if weakest is not None:
            lines.extend([
                "",
                "## Schwaechstes Kapitel (Lesbarkeit)",
                "",
                f"{_badge_for_metric(weakest.fre_score, target_min=report.target_min, target_max=report.target_max)} "
                f"**{weakest.label}** — FRE {weakest.fre_score:.0f} ({weakest.level_label})",
                "",
                f"> {weakest.fix}" if weakest.fix else "",
            ])

    return "\n".join(line for line in lines if line is not None)
