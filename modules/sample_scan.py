"""First-10%-Deep-Scan — Kindle-Sample drop-off-risk analysis.

Amazon serves the first ~10% of every Kindle book as a free sample. If
the reader bounces inside that sample, the book never converts — no
matter how strong chapter twelve might be. This module performs a
section-by-section drop-off-risk scan over the sample so the author
sees exactly *where* a real reader would hit the back button.

Each section is scored on four reader-retention dimensions:

* **Hook** — does the opening grab a skeptical Kindle browser?
* **Versprechen (Promise)** — is it clear *what* the reader gets?
* **Wert (Value)** — does the reader receive at least one concrete,
  usable thing inside the sample (a number, a checklist, a method)?
* **Lesbarkeit (Readability)** — sentence length, filler words,
  hype-phrasing; the inverse of "klingt nach Berater-Sprech".

The scoring is deterministic and heuristic so the module runs without
an LLM API key and produces the same artefact on every QA round.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from modules.chapters import _is_heading
from modules.discovery import BookProject

# Amazon Kindle Sample is 10% by default. We accept slightly more so a
# 9-chapter book whose chapter-1 ends at 11% still gets a clean section.
SAMPLE_RATIO: float = 0.10
SAMPLE_MAX_RATIO: float = 0.14

# Target / minimum / maximum words per scanned section. Sections shorter
# than the minimum are merged into their predecessor so a heading-only
# paragraph never scores zero.
SECTION_TARGET_WORDS: int = 350
MIN_SECTION_WORDS: int = 90
MAX_SECTIONS: int = 8

# Score thresholds shared with industrial.py / chapters.py for cross-
# report consistency.
SCORE_READY: int = 85
SCORE_REVIEW: int = 65

RISK_LABELS: dict[str, str] = {
    "READY": "WEITERLESEN",
    "REVIEW": "GRENZWERTIG",
    "FIX": "ABBRUCH-RISIKO",
}

# Regex inventory tuned for German nonfiction openings. Patterns stay
# small and pattern-only — the LLM-driven enrichment lives elsewhere.
HOOK_MARKERS: str = (
    r"(\?|\bstell dir vor\b|\bstellen sie sich vor\b|"
    r"\bwas waere\b|\bwas wäre\b|"
    r"\bdie meisten\b|\bviele glauben\b|"
    r"\bkennst du das\b|\bkennen sie das\b|"
    r"\bjeder kennt\b|\bjeden tag\b)"
)
PROMISE_MARKERS: str = (
    r"\b(du\s+(?:wirst|lernst|erf[äa]hrst|bekommst)|"
    r"in diesem (?:buch|kapitel)|"
    r"am ende (?:des kapitels|dieses buches)|"
    r"ich zeige dir|hier liest du|"
    r"darum geht es|warum dieses buch|"
    r"was du (?:in diesem|nach diesem|hier))\b"
)
VALUE_MARKERS: str = (
    r"(\b(checkliste|vorlage|schritt-?f[üu]r-?schritt|methode|framework|"
    r"prinzip|regel|merksatz|beispiel:|so geht|so funktioniert)\b"
    r"|\d+\s*(?:euro|€|\$|stunden|tage|wochen|monate|jahre|%|prozent|"
    r"seiten|kunden|projekte|fehler|minuten|sekunden|punkte))"
)
FILLER_MARKERS: str = (
    r"\b(eigentlich|grunds[äa]tzlich|tats[äa]chlich|sozusagen|prinzipiell|"
    r"absolut|wirklich|nat[üu]rlich|durchaus|gewisserma[sß]en|"
    r"letztendlich|im grunde)\b"
)
HYPE_MARKERS: str = (
    r"\b(unglaublich|revolution[äa]r|sensationell|legend[äa]r|geheimformel|"
    r"einzigartig|atemberaubend|bahnbrechend|game[- ]changer|"
    r"bestseller|millionen[- ]?fach|weltweit f[üu]hrend)\b"
)

# Long-sentence threshold: anything beyond is flagged as a readability
# debit. Tuned for German nonfiction — operators read fast and bail on
# 40-word colossi.
LONG_SENTENCE_WORDS: int = 35


@dataclass(frozen=True)
class SampleSection:
    """A bucketed slice of the Kindle sample with its body text."""

    index: int
    label: str
    body: str
    word_count: int
    starts_at_word: int

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "word_count": self.word_count,
            "starts_at_word": self.starts_at_word,
        }


@dataclass(frozen=True)
class SampleSectionScore:
    """Drop-off-risk score for a single sample section."""

    index: int
    label: str
    word_count: int
    starts_at_word: int
    hook: int
    promise: int
    value: int
    readability: int
    overall: int
    status: str
    risk: str
    fix: str

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "word_count": self.word_count,
            "starts_at_word": self.starts_at_word,
            "scores": {
                "hook": self.hook,
                "promise": self.promise,
                "value": self.value,
                "readability": self.readability,
            },
            "overall": self.overall,
            "status": self.status,
            "risk": self.risk,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class SampleScanReport:
    """Aggregated Kindle-sample scan across all sections."""

    manuscript_word_count: int
    sample_word_count: int
    sample_ratio: float
    section_count: int
    overall_score: int
    weakest_section_index: int | None
    sections: list[SampleSectionScore]
    fixes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "manuscript_word_count": self.manuscript_word_count,
            "sample_word_count": self.sample_word_count,
            "sample_ratio": round(self.sample_ratio, 4),
            "section_count": self.section_count,
            "overall_score": self.overall_score,
            "weakest_section_index": self.weakest_section_index,
            "sections": [s.to_json() for s in self.sections],
            "fixes": list(self.fixes),
        }


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÄÖÜäöüß-]+\b", text, flags=re.UNICODE))


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def _total_word_count(paragraphs: Iterable[dict[str, Any]]) -> int:
    return sum(_word_count((p.get("text") or "")) for p in paragraphs)


def _take_sample_paragraphs(
    paragraphs: list[dict[str, Any]],
    *,
    target_words: int,
    max_words: int,
) -> list[dict[str, Any]]:
    """Return the leading paragraphs whose cumulative words ~= target.

    We allow up to ``max_words`` so we don't cut mid-paragraph; that
    keeps section bodies coherent.
    """

    taken: list[dict[str, Any]] = []
    accumulated = 0
    for para in paragraphs:
        text = (para.get("text") or "").strip()
        if not text:
            continue
        words = _word_count(text)
        if accumulated >= target_words and accumulated + words > max_words:
            break
        taken.append(para)
        accumulated += words
        if accumulated >= max_words:
            break
    return taken


def _bucket_into_sections(
    paragraphs: list[dict[str, Any]],
) -> list[SampleSection]:
    """Group paragraphs into sample sections by heading or word window."""

    buckets: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    starts_at = 0
    running = 0

    def _flush() -> None:
        nonlocal current
        if current and current["body_parts"]:
            buckets.append(current)
        current = None

    for para in paragraphs:
        text = (para.get("text") or "").strip()
        if not text:
            continue
        style = para.get("style") or ""
        if _is_heading(style):
            _flush()
            current = {
                "label": text[:80],
                "body_parts": [],
                "starts_at": running,
                "word_count": 0,
            }
            continue
        if current is None:
            current = {
                "label": "Auftakt",
                "body_parts": [],
                "starts_at": running,
                "word_count": 0,
            }
        words = _word_count(text)
        # Split very long heading-less stretches into target-sized windows.
        if current["word_count"] >= SECTION_TARGET_WORDS:
            _flush()
            current = {
                "label": f"Abschnitt ab Wort {running}",
                "body_parts": [],
                "starts_at": running,
                "word_count": 0,
            }
        current["body_parts"].append(text)
        current["word_count"] += words
        running += words

    _flush()

    # Merge tiny tail-buckets back into the previous one so we never emit
    # a heading-only section of, say, 8 words.
    merged: list[dict[str, Any]] = []
    for bucket in buckets:
        if bucket["word_count"] < MIN_SECTION_WORDS and merged:
            merged[-1]["body_parts"].extend(bucket["body_parts"])
            merged[-1]["word_count"] += bucket["word_count"]
        else:
            merged.append(bucket)

    # Cap the number of sections to keep the markdown digestible.
    if len(merged) > MAX_SECTIONS:
        head = merged[: MAX_SECTIONS - 1]
        tail = merged[MAX_SECTIONS - 1 :]
        combined_parts: list[str] = []
        combined_words = 0
        for bucket in tail:
            combined_parts.extend(bucket["body_parts"])
            combined_words += bucket["word_count"]
        head.append(
            {
                "label": tail[0]["label"] + " (zusammengefasst)",
                "body_parts": combined_parts,
                "starts_at": tail[0]["starts_at"],
                "word_count": combined_words,
            }
        )
        merged = head

    sections: list[SampleSection] = []
    for idx, bucket in enumerate(merged, start=1):
        body = "\n".join(bucket["body_parts"])
        sections.append(
            SampleSection(
                index=idx,
                label=bucket["label"] or f"Abschnitt {idx}",
                body=body,
                word_count=bucket["word_count"],
                starts_at_word=bucket["starts_at"],
            )
        )
    return sections


def extract_sample_sections(
    paragraphs: Iterable[dict[str, Any]],
    *,
    sample_ratio: float = SAMPLE_RATIO,
    max_ratio: float = SAMPLE_MAX_RATIO,
) -> tuple[list[SampleSection], int, int]:
    """Return ``(sections, total_words, sample_words)`` for a paragraph stream."""

    paras = [p for p in paragraphs if (p.get("text") or "").strip()]
    total_words = _total_word_count(paras)
    if total_words == 0:
        return [], 0, 0
    target = max(MIN_SECTION_WORDS, int(total_words * sample_ratio))
    ceiling = max(target, int(total_words * max_ratio))
    sample = _take_sample_paragraphs(paras, target_words=target, max_words=ceiling)
    sections = _bucket_into_sections(sample)
    sample_words = sum(s.word_count for s in sections)
    return sections, total_words, sample_words


def _hook_score(section: SampleSection) -> int:
    sentences = _sentences(section.body)
    if not sentences:
        return 2
    opener = " ".join(sentences[:2]).lower()
    hits = len(re.findall(HOOK_MARKERS, opener, flags=re.I))
    if section.index == 1:
        # The first section carries the only real Kindle-Sample hook.
        if hits == 0:
            return 3
        if hits == 1:
            return 7
        return 10
    if hits == 0:
        return 6
    if hits == 1:
        return 8
    return 10


def _promise_score(body: str) -> int:
    hits = len(re.findall(PROMISE_MARKERS, body, flags=re.I))
    if hits == 0:
        return 3
    if hits == 1:
        return 7
    return 10


def _value_score(body: str) -> int:
    hits = len(re.findall(VALUE_MARKERS, body, flags=re.I))
    if hits == 0:
        return 2
    if hits == 1:
        return 6
    if hits == 2:
        return 8
    return 10


def _readability_score(body: str) -> int:
    sentences = _sentences(body)
    if not sentences:
        return 4
    long_sentences = sum(1 for s in sentences if _word_count(s) > LONG_SENTENCE_WORDS)
    filler_hits = len(re.findall(FILLER_MARKERS, body, flags=re.I))
    hype_hits = len(re.findall(HYPE_MARKERS, body, flags=re.I))
    long_ratio = long_sentences / max(1, len(sentences))
    raw = 10
    raw -= round(long_ratio * 6)
    raw -= min(4, filler_hits)
    raw -= min(4, hype_hits * 2)
    return max(1, min(10, raw))


def _status_for(score: int) -> str:
    if score >= SCORE_READY:
        return "READY"
    if score >= SCORE_REVIEW:
        return "REVIEW"
    return "FIX"


def _fix_for(section: SampleSection, weakest: str) -> str:
    label = section.label.strip() or f"Abschnitt {section.index}"
    fixes = {
        "hook": (
            f"'{label}' eroeffnet ohne Hook. Setze einen Satz an den Anfang, der "
            "den Leser direkt anspricht — Frage, Mini-Szene oder eine ueberraschende Zahl."
        ),
        "promise": (
            f"In '{label}' fehlt ein klares Leser-Versprechen. Sag in zwei Saetzen, "
            "was der Leser bis Ende dieses Abschnitts mitnimmt."
        ),
        "value": (
            f"'{label}' liefert noch keinen konkreten Wert. Bring eine Zahl, ein "
            "Beispiel oder eine Checkliste in den Abschnitt — sonst bricht der "
            "Kindle-Sample-Leser hier ab."
        ),
        "readability": (
            f"'{label}' ist sprachlich schwer: lange Saetze, Fueller oder Hype-Worte. "
            "Kuerze Saetze unter 25 Woerter und entferne 'eigentlich/grundsaetzlich/absolut'."
        ),
    }
    return fixes.get(weakest, fixes["value"])


def score_section(section: SampleSection) -> SampleSectionScore:
    hook = _hook_score(section)
    promise = _promise_score(section.body)
    value = _value_score(section.body)
    readability = _readability_score(section.body)

    # Reader-retention weighting: hook + value matter most in the sample.
    weighted = hook * 3 + promise * 2 + value * 3 + readability * 2  # max = 100
    overall = max(0, min(100, weighted))
    status = _status_for(overall)
    risk = RISK_LABELS.get(status, status)

    dims = {
        "hook": hook,
        "promise": promise,
        "value": value,
        "readability": readability,
    }
    weakest = min(dims, key=dims.get)
    return SampleSectionScore(
        index=section.index,
        label=section.label,
        word_count=section.word_count,
        starts_at_word=section.starts_at_word,
        hook=hook,
        promise=promise,
        value=value,
        readability=readability,
        overall=overall,
        status=status,
        risk=risk,
        fix=_fix_for(section, weakest),
    )


def build_sample_scan_report_from_paragraphs(
    paragraphs: Iterable[dict[str, Any]],
) -> SampleScanReport:
    """Pure-Python entry point: build a SampleScanReport from a paragraph stream."""

    sections, total_words, sample_words = extract_sample_sections(paragraphs)
    if not sections:
        return SampleScanReport(
            manuscript_word_count=total_words,
            sample_word_count=sample_words,
            sample_ratio=0.0,
            section_count=0,
            overall_score=0,
            weakest_section_index=None,
            sections=[],
            fixes=[],
        )
    scores = [score_section(s) for s in sections]
    overall = round(sum(s.overall for s in scores) / len(scores))
    weakest = min(scores, key=lambda s: s.overall)
    fixes = [s.fix for s in scores if s.status != "READY"]
    ratio = sample_words / total_words if total_words else 0.0
    return SampleScanReport(
        manuscript_word_count=total_words,
        sample_word_count=sample_words,
        sample_ratio=ratio,
        section_count=len(scores),
        overall_score=overall,
        weakest_section_index=weakest.index,
        sections=scores,
        fixes=fixes,
    )


def _docx_paragraph_stream(path: Any) -> list[dict[str, Any]]:
    """Read a DOCX into the paragraph-stream format used everywhere else."""

    from modules.readers import open_docx_paragraphs

    doc = open_docx_paragraphs(path)
    paragraphs: list[dict[str, Any]] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style else ""
        paragraphs.append({"text": text, "style": style})
    return paragraphs


def build_sample_scan_report(project: BookProject) -> SampleScanReport:
    """Run the First-10%-Deep-Scan against a project's manuscript."""

    if not project.manuscript:
        return SampleScanReport(
            manuscript_word_count=0,
            sample_word_count=0,
            sample_ratio=0.0,
            section_count=0,
            overall_score=0,
            weakest_section_index=None,
            sections=[],
            fixes=[],
        )
    paragraphs = _docx_paragraph_stream(project.manuscript)
    return build_sample_scan_report_from_paragraphs(paragraphs)


_STATUS_EMOJI: dict[str, str] = {"READY": "🟢", "REVIEW": "🟡", "FIX": "🔴"}


def render_sample_scan_markdown(project: BookProject, report: SampleScanReport) -> str:
    """Render the sample-scan as beginner-friendly German markdown."""

    title = project.title or project.project_id

    if not report.sections:
        return (
            "# First-10%-Deep-Scan (Kindle-Sample)\n\n"
            f"Buch: **{title}**\n\n"
            "Es konnten keine Abschnitte fuer die Kindle-Sample-Analyse extrahiert werden. "
            "Pruefe, ob das Manuskript existiert und ueber genug Text verfuegt."
        )

    lines: list[str] = [
        "# First-10%-Deep-Scan (Kindle-Sample)",
        "",
        f"Buch: **{title}**",
        f"Manuskript: **{report.manuscript_word_count}** Woerter",
        f"Analysierte Leseprobe: **{report.sample_word_count}** Woerter "
        f"(~{round(report.sample_ratio * 100)}%)",
        f"Erkannte Abschnitte: **{report.section_count}**",
        f"Gesamt-Score: **{report.overall_score}/100**",
        "",
        "## Warum das wichtig ist",
        "",
        "Amazon zeigt potenziellen Kaeufern die ersten ~10% deines Buchs als kostenlose "
        "Leseprobe. Wenn der Leser hier abbricht, ist der Verkauf verloren — egal wie gut "
        "Kapitel 12 ist. Diese Tabelle zeigt fuer jeden Abschnitt der Leseprobe, wie hoch "
        "das Abbruch-Risiko ist.",
        "",
        "## Pro Abschnitt",
        "",
        "| # | Abschnitt | Woerter | Hook | Versprechen | Wert | Lesbarkeit | Score | Risiko |",
        "|---|-----------|---------|------|-------------|------|------------|-------|--------|",
    ]
    for sec in report.sections:
        label_safe = sec.label.replace("|", "/")[:50]
        lines.append(
            f"| {sec.index} | {label_safe} | {sec.word_count} | "
            f"{sec.hook}/10 | {sec.promise}/10 | {sec.value}/10 | "
            f"{sec.readability}/10 | {sec.overall}/100 | {sec.risk} |"
        )

    lines.extend(["", "## Konkrete Fixes", ""])
    for sec in report.sections:
        emoji = _STATUS_EMOJI.get(sec.status, "⚪")
        lines.append(f"### {emoji} Abschnitt {sec.index} — {sec.label}")
        lines.append("")
        lines.append(f"- Score: **{sec.overall}/100** ({sec.risk})")
        lines.append(f"- Fix: {sec.fix}")
        lines.append("")
    return "\n".join(lines)
