"""KDP 7-keyword slot generator for German nonfiction.

Amazon KDP lets authors fill up to 7 keyword slots (each up to 50
characters). This module produces the 7 concrete strings — not a
warning that "keywords are missing" — so the author can copy them
straight into the KDP backend.

The generator is pure-Python and deterministic. It derives keyword
phrases from the project's title, subtitle and Amazon description by:

* extracting subject + audience (re-using the rewrite-module heuristics
  so the surface stays consistent),
* combining them with German nonfiction search modifiers
  ("ratgeber", "praxis", "schritt fuer schritt", ...),
* enriching with anchor-keyword pairs for organic-search coverage,
* enforcing KDP rules: max 50 chars, lowercase, deduplicated, no
  subjective claims ("bestseller", "kostenlos"), no overlap with the
  book title (KDP forbids repeating title words in keyword slots).

Output is a stable, ordered list of ``KDPKeyword`` records — each with a
``rationale`` so the author understands *why* a particular slot is
filled the way it is and can swap individual rows out before saving.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from modules.discovery import BookProject
from modules.rewrites import (
    FALLBACK_AUDIENCES,
    STOPWORDS,
    _extract_audience,
    _extract_subject,
    extract_anchor_keywords,
)

KDP_KEYWORD_SLOTS: int = 7
KDP_KEYWORD_MAX_CHARS: int = 50
KDP_KEYWORD_MIN_CHARS: int = 4

FORMAT_MODIFIERS: tuple[str, ...] = (
    "ratgeber",
    "buch",
    "praxis",
    "anleitung",
)

DIFFERENTIATOR_MODIFIERS: tuple[str, ...] = (
    "schritt fuer schritt",
    "ohne hype",
    "aus der praxis",
    "fuer einsteiger",
)

GENERIC_FALLBACKS: tuple[str, ...] = (
    "sachbuch ratgeber praxis",
    "ratgeber selbsthilfe alltag",
    "buch ohne hype",
    "schritt fuer schritt anleitung",
    "sachbuch fuer praktiker",
    "ratgeber konkret umsetzbar",
    "buch fuer berufstaetige",
)

FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        "bestseller",
        "amazon",
        "kindle",
        "kostenlos",
        "gratis",
        "free",
        "neu",
        "new",
        "sale",
    }
)


@dataclass(frozen=True)
class KDPKeyword:
    """One filled keyword slot, copy-paste ready for KDP."""

    text: str
    char_count: int
    source: str
    rationale: str

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "char_count": self.char_count,
            "source": self.source,
            "rationale": self.rationale,
        }


def _normalize_phrase(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip().lower()
    cleaned = cleaned.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    cleaned = re.sub(r"[^a-z0-9 \-]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -")


def _title_tokens(project: BookProject) -> frozenset[str]:
    title = project.title or ""
    tokens = {
        _normalize_phrase(part)
        for part in re.findall(r"[\wÄÖÜäöüß-]{3,}", title, flags=re.UNICODE)
    }
    return frozenset(token for token in tokens if token)


def _looks_valid(phrase: str, title_tokens: frozenset[str]) -> bool:
    if not phrase or len(phrase) < KDP_KEYWORD_MIN_CHARS or len(phrase) > KDP_KEYWORD_MAX_CHARS:
        return False
    words = phrase.split()
    if not words:
        return False
    if any(word in FORBIDDEN_TOKENS for word in words):
        return False
    if not any(word.isalpha() for word in words):
        return False
    # KDP forbids repeating the book title verbatim. If *every* word in
    # the phrase already appears in the title, skip it — but a phrase
    # that only shares one token (e.g. the subject) is fine.
    if words and title_tokens and all(word in title_tokens for word in words):
        return False
    return True


def _make_keyword(
    *,
    text: str,
    source: str,
    rationale: str,
    title_tokens: frozenset[str],
    seen: set[str],
) -> KDPKeyword | None:
    phrase = _normalize_phrase(text)
    if not _looks_valid(phrase, title_tokens) or phrase in seen:
        return None
    seen.add(phrase)
    return KDPKeyword(
        text=phrase,
        char_count=len(phrase),
        source=source,
        rationale=rationale,
    )


def _subject_phrases(subject: str, audience: str) -> Iterable[tuple[str, str, str]]:
    subject_l = _normalize_phrase(subject)
    audience_l = _normalize_phrase(audience)
    if subject_l:
        for modifier in FORMAT_MODIFIERS:
            yield (
                f"{subject_l} {modifier}",
                "subject_format",
                f"Subject + Format-Modifier '{modifier}' — typischer KDP-Suchpfad.",
            )
        for modifier in DIFFERENTIATOR_MODIFIERS:
            yield (
                f"{subject_l} {modifier}",
                "subject_differentiator",
                f"Subject + Differenzierung '{modifier}' — hebt Buch vom Hype-Segment ab.",
            )
        if audience_l:
            yield (
                f"{subject_l} fuer {audience_l}",
                "subject_audience",
                "Subject + Zielgruppe — engt das Suchergebnis auf die Buyer-Persona ein.",
            )


def _audience_phrases(audience: str) -> Iterable[tuple[str, str, str]]:
    audience_l = _normalize_phrase(audience)
    if not audience_l:
        return
    for modifier in FORMAT_MODIFIERS:
        yield (
            f"{modifier} fuer {audience_l}",
            "audience_format",
            f"Format '{modifier}' + Zielgruppe — beliebter Long-Tail-Suchbegriff.",
        )


def _anchor_phrases(anchors: list[str]) -> Iterable[tuple[str, str, str]]:
    filtered = [
        a for a in anchors
        if a and a not in STOPWORDS and len(a) >= 4 and a not in FORBIDDEN_TOKENS
    ]
    if len(filtered) >= 2:
        yield (
            f"{filtered[0]} {filtered[1]}",
            "anchor_pair",
            "Anker-Keyword-Paar — deckt deine organischen Suchbegriffe ab.",
        )
    if len(filtered) >= 3:
        yield (
            f"{filtered[0]} {filtered[2]}",
            "anchor_pair",
            "Zweites Anker-Paar — alternative Such-Kombination.",
        )
    if len(filtered) >= 4:
        yield (
            f"{filtered[1]} {filtered[3]}",
            "anchor_pair",
            "Drittes Anker-Paar — erweitert die Long-Tail-Abdeckung.",
        )


def _fallback_phrases() -> Iterable[tuple[str, str, str]]:
    for phrase in GENERIC_FALLBACKS:
        yield (
            phrase,
            "fallback",
            "Generischer Sachbuch-Suchbegriff — fuellt einen Slot, falls Anker fehlen.",
        )


def build_kdp_keywords(project: BookProject) -> list[KDPKeyword]:
    """Return up to 7 KDP-ready keyword strings for a project."""

    subject = _extract_subject(project)
    audience = _extract_audience(project) or FALLBACK_AUDIENCES[0]
    anchors = extract_anchor_keywords(project)
    title_tokens = _title_tokens(project)

    keywords: list[KDPKeyword] = []
    seen: set[str] = set()

    pipelines: list[Iterable[tuple[str, str, str]]] = [
        _subject_phrases(subject, audience),
        _audience_phrases(audience),
        _anchor_phrases(anchors),
        _fallback_phrases(),
    ]

    for pipeline in pipelines:
        for text, source, rationale in pipeline:
            if len(keywords) >= KDP_KEYWORD_SLOTS:
                return keywords
            keyword = _make_keyword(
                text=text,
                source=source,
                rationale=rationale,
                title_tokens=title_tokens,
                seen=seen,
            )
            if keyword:
                keywords.append(keyword)

    return keywords


def render_kdp_keywords_report_markdown(
    project: BookProject, keywords: list[KDPKeyword]
) -> str:
    """Beginner-friendly walk-through with the 7 ready-to-paste strings."""

    title = project.title or project.project_id
    lines: list[str] = [
        "# 7 KDP-Keywords (copy-paste fertig)",
        "",
        f"Buch: **{title}**",
        "",
        "Trage diese Keywords im KDP-Backend unter "
        "'Buchdetails > Schluesselwoerter' ein. KDP erlaubt 7 Slots mit je "
        f"maximal {KDP_KEYWORD_MAX_CHARS} Zeichen. Jeder Slot kann ein Wort oder eine kurze "
        "Phrase sein — Phrasen ranken in der Regel besser.",
        "",
        "## Die 7 Slots",
        "",
    ]
    if not keywords:
        lines.append(
            "_Es konnten keine Keywords abgeleitet werden — pflege Titel, Untertitel und "
            "Beschreibung mit ein paar konkreten Begriffen._"
        )
    else:
        for idx, keyword in enumerate(keywords, start=1):
            lines.extend([
                f"### Slot {idx}",
                "",
                f"`{keyword.text}`",
                "",
                f"- Zeichen: **{keyword.char_count}/{KDP_KEYWORD_MAX_CHARS}**",
                f"- Quelle: `{keyword.source}`",
                f"- Begruendung: {keyword.rationale}",
                "",
            ])
        if len(keywords) < KDP_KEYWORD_SLOTS:
            lines.extend([
                f"_Hinweis: nur {len(keywords)} von {KDP_KEYWORD_SLOTS} Slots befuellt. Pflege Titel, "
                "Untertitel oder Beschreibung mit mehr substantiellen Begriffen, um die "
                "fehlenden Slots zu fuellen._",
                "",
            ])
    lines.extend([
        "## Spielregeln, die hier eingehalten werden",
        "",
        f"- Keine Slot-Phrase ueber {KDP_KEYWORD_MAX_CHARS} Zeichen.",
        "- Keine subjektiven Begriffe ('bestseller', 'kostenlos').",
        "- Keine reine Wiederholung des Buchtitels.",
        "- Alles in Kleinschreibung — KDP normalisiert ohnehin.",
        "- Umlaute zu ae/oe/ue/ss konvertiert — die KDP-Suche findet beide Varianten "
        "und du sparst Zeichen.",
    ])
    return "\n".join(lines)
