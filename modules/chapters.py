"""Per-chapter analysis for German nonfiction manuscripts.

Splits a DOCX (or generic paragraph stream) into chapters using heading
styles, then scores each chapter on four reader-impact dimensions:
Versprechen (promise), Beweis (proof), Wert (value), Übergang (transition).

Designed to run fully offline so tests don't need an LLM or filesystem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Heading style markers used by Word in German + English templates.
HEADING_STYLE_TOKENS: tuple[str, ...] = ("heading", "überschrift", "uberschrift")

# Minimum body words for a real chapter — anything below is treated as a
# section divider / front matter and merged into the previous chapter.
MIN_CHAPTER_WORDS = 80

# Score thresholds (kept identical to industrial.py for cross-report
# consistency).
SCORE_READY = 85
SCORE_REVIEW = 65

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
class ChapterReport:
    chapters: list[ChapterScore]
    average_score: int
    weakest_chapter_index: int | None
    fixes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "chapters": [c.to_json() for c in self.chapters],
            "average_score": self.average_score,
            "weakest_chapter_index": self.weakest_chapter_index,
            "fixes": list(self.fixes),
        }


def build_chapter_report(chapters: list[Chapter]) -> ChapterReport:
    """Score every chapter and aggregate into a ChapterReport."""

    if not chapters:
        return ChapterReport(chapters=[], average_score=0, weakest_chapter_index=None, fixes=[])
    scores = [score_chapter(ch) for ch in chapters]
    avg = round(sum(s.overall for s in scores) / len(scores))
    weakest = min(scores, key=lambda s: s.overall)
    fixes = [s.fix for s in scores if s.status != "READY"]
    return ChapterReport(
        chapters=scores,
        average_score=avg,
        weakest_chapter_index=weakest.index,
        fixes=fixes,
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
        emoji = {"READY": "🟢", "REVIEW": "🟡", "FIX": "🔴"}.get(chap.status, "⚪")
        lines.append(f"### {emoji} Kapitel {chap.index} — {chap.title}")
        lines.append("")
        lines.append(f"- Score: **{chap.overall}/100** ({chap.status})")
        lines.append(f"- Fix: {chap.fix}")
        lines.append("")
    return "\n".join(lines)
