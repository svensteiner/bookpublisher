"""KDP Amazon-Description HTML generator.

Produces a copy-paste-ready Amazon book-description HTML snippet that
respects the KDP-allowed subset of tags:

    <b>, <strong>, <em>, <i>, <u>, <br>, <p>, <ul>, <ol>, <li>,
    <h4>, <h5>, <h6>, <hr>

No CSS, no images, no links, no <div>/<span>. Output is deterministic
and pure-Python so the generator can run without an API key.

The generator derives every part from existing project metadata
(title, subtitle, amazon_description). When the project already has a
hand-written description, its bullet-like lines are reused; otherwise
the bullets fall back to anti-hype operator-flavoured value statements
that match the rewrites module's bestseller patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from typing import Any, Callable, Iterable, Sequence

from modules.discovery import BookProject
from modules.rewrites import (
    FALLBACK_AUDIENCES,
    _extract_audience,
    _extract_subject,
    extract_anchor_keywords,
    score_keywords,
)


HEADLINE_MAX_CHARS: int = 140
LEAD_MAX_CHARS: int = 360
BULLET_MAX_CHARS: int = 140
MIN_BULLETS: int = 4
MAX_BULLETS: int = 6
DEFAULT_BULLET_COUNT: int = 5

# Provenance labels for the bullet selection. Downstream tools (release
# packager, CI checks, beginner_summary highlight) read ``bullets_source``
# from amazon_description.json to decide which path produced the bullets
# without having to parse the HTML.
BULLETS_SOURCE_LLM: str = "llm"
BULLETS_SOURCE_EXISTING: str = "existing"
BULLETS_SOURCE_TEMPLATE: str = "template"
BULLETS_SOURCES: tuple[str, ...] = (
    BULLETS_SOURCE_LLM,
    BULLETS_SOURCE_EXISTING,
    BULLETS_SOURCE_TEMPLATE,
)

# Maximum number of chapter titles to forward to the LLM bullet extractor.
# Keeping this small keeps the prompt cheap and forces the LLM to pick the
# strongest selling points instead of mirroring the table of contents.
LLM_BULLETS_MAX_CHAPTER_TITLES: int = 20
# Hard sanity floor: an LLM bullet shorter than this is almost certainly a
# fragment ("Praxis", "Methode") and gets dropped before we hit the
# clipping/dedup path. Avoids one-word bullets sneaking into the HTML.
LLM_BULLETS_MIN_CHARS: int = 16

# Quality-check thresholds for LLM-generated bullets. If the LLM output
# violates any of these rules, the caller falls back to the deterministic
# template path so the Amazon listing never ships hype-y, repetitive or
# proof-free bullets that the LLM hallucinated.
LLM_BULLETS_MIN_NUMBER_HITS: int = 1
# Tokens that signal Marketing-Hype — KDP-Sachbuch-Bestseller verzichten
# bewusst auf diese Wörter. Matched case-insensitively on word boundaries
# (so "fantastische" matches "fantastisch" only via the lemma prefix).
LLM_BULLETS_HYPE_TOKENS: tuple[str, ...] = (
    "ultimativ",
    "unglaublich",
    "perfekt",
    "garantiert",
    "revolutionaer",
    "revolutionär",
    "weltbeste",
    "weltklasse",
    "einzigartig",
    "exklusiv",
    "sensationell",
    "fantastisch",
    "wunderbar",
    "magisch",
    "geheim",
    "bestseller",
    "phaenomenal",
    "phänomenal",
    "atemberaubend",
    "lebensveraendernd",
    "lebensverändernd",
    "must-have",
    "must have",
    "no-brainer",
    "game-changer",
    "gamechanger",
)

_BULLET_MARKERS: tuple[str, ...] = ("- ", "* ", "• ", "‣ ", "– ", "— ")
_SECTION_HEADINGS: dict[str, str] = {
    "headline": "Versprechen",
    "lead": "Einstieg",
    "bullets": "Was du in diesem Buch findest",
    "audience": "Fuer wen ist dieses Buch?",
    "cta": "Probelesen und entscheiden",
}


@dataclass(frozen=True)
class AmazonDescriptionHtml:
    """Structured KDP-HTML output plus a plain-text fallback."""

    headline: str
    lead: str
    bullets: tuple[str, ...]
    audience: str
    cta: str
    html: str
    char_count: int
    keyword_score: int
    anchors: tuple[str, ...] = field(default_factory=tuple)
    bullets_source: str = BULLETS_SOURCE_TEMPLATE

    def to_json(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "lead": self.lead,
            "bullets": list(self.bullets),
            "audience": self.audience,
            "cta": self.cta,
            "html": self.html,
            "char_count": self.char_count,
            "keyword_score": self.keyword_score,
            "anchors": list(self.anchors),
            "bullets_source": self.bullets_source,
        }


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clip(text: str, max_chars: int) -> str:
    text = _normalize_whitespace(text)
    if len(text) <= max_chars:
        return text
    cut = text.rfind(" ", 0, max_chars)
    if cut < int(max_chars * 0.6):
        cut = max_chars
    return text[:cut].rstrip(" ,;:-")


def _split_lead_and_rest(description: str) -> tuple[str, str]:
    cleaned = description.strip()
    if not cleaned:
        return "", ""
    parts = re.split(r"\n\s*\n", cleaned, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=2)
    if len(sentences) >= 2:
        lead = " ".join(sentences[:2]).strip()
        rest = cleaned[len(lead):].strip()
        return lead, rest
    return cleaned, ""


def _strip_bullet_marker(line: str) -> str:
    stripped = line.strip()
    for marker in _BULLET_MARKERS:
        if stripped.startswith(marker):
            return stripped[len(marker):].strip()
    if re.match(r"^\d+[.)]\s+", stripped):
        return re.sub(r"^\d+[.)]\s+", "", stripped).strip()
    return stripped


def _extract_existing_bullets(description: str) -> list[str]:
    bullets: list[str] = []
    seen: set[str] = set()
    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue
        is_marker = any(line.startswith(marker) for marker in _BULLET_MARKERS) or bool(
            re.match(r"^\d+[.)]\s+", line)
        )
        if not is_marker:
            continue
        text = _strip_bullet_marker(line)
        if len(text) < 8:
            continue
        clipped = _clip(text, BULLET_MAX_CHARS)
        key = clipped.lower()
        if key in seen:
            continue
        seen.add(key)
        bullets.append(clipped)
    return bullets


def _fallback_bullets(subject: str, audience: str) -> list[str]:
    subject_l = (subject or "dem Thema").lower()
    return [
        f"Konkretes Schritt-fuer-Schritt-Vorgehen zu {subject_l} — ohne Hype, ohne Floskeln",
        f"Entscheidungsregeln und Checklisten, die {audience} sofort anwenden koennen",
        "Beispiele aus echten Projekten mit Zahlen, Stolperfallen und Loesungen",
        "Klare Sprache, keine Berater-Phrasen — geschrieben fuer Menschen, die umsetzen",
        "Kompakter Lesefluss: kurze Kapitel, klare Versprechen, kein Fueller",
        f"Hinweise, wo {audience} bei {subject_l} typischerweise scheitern und wie sie das vermeiden",
    ]


def _build_headline(project: BookProject, subject: str, audience: str) -> str:
    title = (project.title or "").strip()
    subtitle = (project.subtitle or "").strip()
    if title and subtitle:
        candidate = f"{title} — {subtitle}"
    elif title:
        candidate = title
    elif subtitle:
        candidate = subtitle
    else:
        candidate = f"{subject}: Was wirklich funktioniert — fuer {audience}"
    return _clip(candidate, HEADLINE_MAX_CHARS)


def _build_lead(project: BookProject, subject: str, audience: str) -> str:
    description = (project.amazon_description or "").strip()
    if description:
        lead, _ = _split_lead_and_rest(description)
        if len(lead) >= 60:
            return _clip(lead, LEAD_MAX_CHARS)
    subject_l = (subject or "diesem Thema").lower()
    fallback = (
        f"Dieses Buch zeigt {audience}, wie {subject_l} im Alltag wirklich funktioniert. "
        "Keine Theorie, keine Motivationsspruechen — sondern Schritt-fuer-Schritt-Vorgehen, "
        "Checklisten und Beispiele aus echten Projekten."
    )
    return _clip(fallback, LEAD_MAX_CHARS)


def _normalize_bullet_candidates(
    candidates: Iterable[str], *, min_chars: int = LLM_BULLETS_MIN_CHARS
) -> list[str]:
    """Clip, dedupe, and drop too-short candidates — pure function."""

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        clipped = _clip(raw, BULLET_MAX_CHARS)
        if len(clipped) < min_chars:
            continue
        key = clipped.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(clipped)
    return normalized


# Pre-compile the hype regex once. We anchor each token at a word boundary
# so "geheim" matches "geheime" / "geheim-tipp" but not embedded substrings
# inside unrelated words. Sorted by length descending so the longer phrase
# "must have" matches before "must-have".
_LLM_BULLETS_HYPE_RE = re.compile(
    r"\b(?:" + "|".join(
        re.escape(token) for token in sorted(LLM_BULLETS_HYPE_TOKENS, key=len, reverse=True)
    ) + r")",
    re.IGNORECASE,
)
_LLM_BULLETS_DIGIT_RE = re.compile(r"\d")
# First word of a bullet — strip surrounding punctuation, keep only letters
# so "Drei" and "Drei," collapse to the same start-word key.
_LLM_BULLETS_FIRST_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class BulletQualityResult:
    """Outcome of the LLM-bullet sanity check.

    ``accepted`` carries the bullets that passed the per-bullet filters
    (no hype, no exclamation). ``rejected`` carries `(bullet, reason)`
    pairs for traceability. ``violations`` lists aggregate-level issues
    (duplicate first-word, no number in any bullet). ``passed`` is the
    single bit the caller uses to decide: True → use ``accepted``, False
    → fall back to the deterministic template path.
    """

    accepted: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    violations: tuple[str, ...]
    passed: bool


def _first_word_key(bullet: str) -> str:
    match = _LLM_BULLETS_FIRST_WORD_RE.search(bullet)
    return match.group(0).lower() if match else ""


def validate_llm_bullets(bullets: Sequence[str]) -> BulletQualityResult:
    """Run anti-hype + numerical-proof + duplicate-start checks.

    Pure function. Returns a ``BulletQualityResult`` describing which
    bullets survived the per-bullet filters and which aggregate-level
    rules tripped. The caller (``_select_bullets``) treats ``passed=False``
    as a fall-through signal to the template path so the Amazon listing
    never ships a hallucinated, hype-heavy or proof-free bullet list.
    """

    accepted: list[str] = []
    rejected: list[tuple[str, str]] = []
    for raw in bullets:
        if not isinstance(raw, str):
            rejected.append((str(raw), "non_string"))
            continue
        bullet = raw.strip()
        if not bullet:
            continue
        if "!" in bullet:
            rejected.append((bullet, "contains_exclamation"))
            continue
        hype_match = _LLM_BULLETS_HYPE_RE.search(bullet)
        if hype_match is not None:
            rejected.append((bullet, f"contains_hype:{hype_match.group(0).lower()}"))
            continue
        accepted.append(bullet)

    violations: list[str] = []
    if accepted:
        digit_hits = sum(1 for bullet in accepted if _LLM_BULLETS_DIGIT_RE.search(bullet))
        if digit_hits < LLM_BULLETS_MIN_NUMBER_HITS:
            violations.append("missing_number")
        first_words = [_first_word_key(bullet) for bullet in accepted]
        non_empty_starts = [word for word in first_words if word]
        if non_empty_starts and len(set(non_empty_starts)) < len(non_empty_starts):
            violations.append("duplicate_start_word")
    else:
        violations.append("all_rejected")

    passed = not violations and len(accepted) >= MIN_BULLETS
    return BulletQualityResult(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        violations=tuple(violations),
        passed=passed,
    )


def _select_bullets(
    project: BookProject,
    subject: str,
    audience: str,
    *,
    llm_bullets: Sequence[str] | None = None,
) -> tuple[list[str], str]:
    """Pick the final bullet list and report its provenance.

    Returns ``(bullets, source)`` where ``source`` is one of
    ``BULLETS_SOURCE_LLM``, ``BULLETS_SOURCE_EXISTING`` or
    ``BULLETS_SOURCE_TEMPLATE``. The source label lets downstream tools
    (release packager, CI, beginner_summary) detect the bullet origin
    without re-parsing the HTML.

    When ``llm_bullets`` carries at least ``MIN_BULLETS`` usable items
    AND passes the quality check, the LLM output wins. When the LLM
    output is too short or fails the quality check, we fall back to the
    existing description's bullet lines if rich enough, then to the
    deterministic anti-hype template so the Amazon page never ships a
    half-empty bullet list.
    """

    if llm_bullets:
        llm_clean = _normalize_bullet_candidates(llm_bullets)
        if len(llm_clean) >= MIN_BULLETS:
            quality = validate_llm_bullets(llm_clean)
            if quality.passed:
                return list(quality.accepted)[:MAX_BULLETS], BULLETS_SOURCE_LLM
    existing = _extract_existing_bullets(project.amazon_description or "")
    if len(existing) >= MIN_BULLETS:
        return existing[:MAX_BULLETS], BULLETS_SOURCE_EXISTING
    bullets = list(existing)
    seen = {item.lower() for item in bullets}
    for candidate in _fallback_bullets(subject, audience):
        clipped = _clip(candidate, BULLET_MAX_CHARS)
        if clipped.lower() in seen:
            continue
        bullets.append(clipped)
        seen.add(clipped.lower())
        if len(bullets) >= DEFAULT_BULLET_COUNT:
            break
    return bullets[:MAX_BULLETS], BULLETS_SOURCE_TEMPLATE


def _build_audience(audience: str) -> str:
    audience = audience.strip() or FALLBACK_AUDIENCES[0]
    return (
        f"Geschrieben fuer {audience}, die konkrete Methoden statt Motivationsspruechen suchen — "
        "und die ihre Zeit nicht mit Theorie verbringen wollen."
    )


def _build_cta() -> str:
    return (
        "Lies die kostenlose Leseprobe und entscheide nach den ersten Seiten selbst, "
        "ob dieses Buch zu deiner Situation passt."
    )


def _render_html(headline: str, lead: str, bullets: Iterable[str], audience: str, cta: str) -> str:
    bullet_html = "\n".join(f"  <li>{escape(item)}</li>" for item in bullets)
    blocks = [
        f"<p><b>{escape(headline)}</b></p>",
        f"<p>{escape(lead)}</p>",
        f"<p><b>{escape(_SECTION_HEADINGS['bullets'])}</b></p>",
        f"<ul>\n{bullet_html}\n</ul>",
        f"<p><b>{escape(_SECTION_HEADINGS['audience'])}</b><br>{escape(audience)}</p>",
        f"<p><b>{escape(_SECTION_HEADINGS['cta'])}</b><br>{escape(cta)}</p>",
    ]
    return "\n".join(blocks)


def build_amazon_description_html(
    project: BookProject,
    *,
    llm_bullets: Sequence[str] | None = None,
) -> AmazonDescriptionHtml:
    """Generate the KDP-compliant Amazon description HTML for a project.

    When ``llm_bullets`` is provided and contains at least ``MIN_BULLETS``
    usable items, those bullets replace the template-derived ones so the
    Amazon description reflects book-specific selling points pulled from
    the manuscript instead of generic anti-hype lines. The rest of the
    snippet (headline, lead, audience, CTA, keyword score) stays
    deterministic.
    """

    subject = _extract_subject(project)
    audience = _extract_audience(project)
    anchors = extract_anchor_keywords(project)

    headline = _build_headline(project, subject, audience)
    lead = _build_lead(project, subject, audience)
    bullets, bullets_source = _select_bullets(
        project, subject, audience, llm_bullets=llm_bullets
    )
    audience_text = _build_audience(audience)
    cta = _build_cta()

    html = _render_html(headline, lead, bullets, audience_text, cta)
    scored_blob = " ".join([headline, lead, *bullets, audience_text])
    return AmazonDescriptionHtml(
        headline=headline,
        lead=lead,
        bullets=tuple(bullets),
        audience=audience_text,
        cta=cta,
        html=html,
        char_count=len(html),
        keyword_score=score_keywords(scored_blob, anchors),
        anchors=tuple(anchors),
        bullets_source=bullets_source,
    )


def render_amazon_description_report_markdown(
    project: BookProject, snippet: AmazonDescriptionHtml
) -> str:
    """Render a beginner-friendly walk-through around the HTML output."""

    title = project.title or project.project_id
    lines: list[str] = [
        "# Amazon-Beschreibung (KDP-HTML)",
        "",
        f"Buch: **{title}**",
        "",
        "Diese Beschreibung ist copy-paste-fertig fuer das KDP-Backend. "
        "Alle Tags entsprechen der erlaubten Liste (`<b>`, `<br>`, `<p>`, `<ul>`, `<li>`).",
        "",
        f"- Zeichen (inkl. HTML): **{snippet.char_count}**",
        f"- Keyword-Score: **{snippet.keyword_score}/100**",
        f"- Bullet-Punkte: **{len(snippet.bullets)}**",
        "",
        "## So nutzt du diese Beschreibung",
        "",
        "1. Oeffne KDP > Buchdetails > Beschreibung.",
        "2. Klicke auf 'Buchbeschreibung formatieren' und waehle die HTML-Ansicht.",
        "3. Fuege den HTML-Block unten ein. KDP zeigt automatisch fette Headline, "
        "Bullet-Liste und Absaetze an.",
        "4. Speichern, Vorschau ansehen, ggf. Headline kuerzen wenn sie umbricht.",
        "",
        "## Komponenten im Klartext",
        "",
        f"- **Headline:** {snippet.headline}",
        f"- **Einstieg:** {snippet.lead}",
        f"- **Zielgruppe:** {snippet.audience}",
        f"- **CTA:** {snippet.cta}",
        "",
        "Bullet-Liste:",
        "",
    ]
    lines.extend(f"- {item}" for item in snippet.bullets)
    lines.extend([
        "",
        "## HTML zum Einfuegen",
        "",
        "```html",
        snippet.html,
        "```",
    ])
    return "\n".join(lines)


# --- Optional LLM-Pass for richer bullet extraction -----------------------

LLM_BULLETS_SYSTEM_PROMPT: str = (
    "Du bist ein erfahrener Sachbuch-Lektor fuer den deutschen KDP-Markt. "
    "Deine Aufgabe: aus Titel, Untertitel, Beschreibung und Kapitel-Titeln "
    "die 5 staerksten Verkaufs-Bullets fuer die Amazon-Buchbeschreibung "
    "extrahieren. Jeder Bullet ist ein einzelner Satz auf Deutsch, max "
    "140 Zeichen, ohne Hype, ohne Marketing-Floskeln, ohne Ausrufezeichen. "
    "Antworte ausschliesslich als JSON mit dem Schluessel 'bullets' "
    "(Array aus 5 Strings). Kein zusaetzlicher Text."
)


def build_llm_bullets_user_prompt(
    project: BookProject,
    chapter_titles: Sequence[str],
) -> str:
    """Render the user prompt for the LLM bullet extractor."""

    title = (project.title or "").strip() or "(kein Titel)"
    subtitle = (project.subtitle or "").strip() or "(kein Untertitel)"
    description = (project.amazon_description or "").strip() or "(keine Beschreibung)"
    chapters_capped = list(chapter_titles)[:LLM_BULLETS_MAX_CHAPTER_TITLES]
    if chapters_capped:
        chapter_block = "\n".join(f"- {c}" for c in chapters_capped if c)
    else:
        chapter_block = "(keine Kapitel-Titel verfuegbar)"
    return (
        f"Titel: {title}\n"
        f"Untertitel: {subtitle}\n\n"
        "Amazon-Beschreibung:\n"
        f"{description}\n\n"
        "Kapitel-Titel:\n"
        f"{chapter_block}\n\n"
        "Liefere genau 5 Bullets im geforderten JSON-Format."
    )


def _parse_llm_bullets_payload(payload: Any) -> list[str]:
    """Extract the bullet strings from the LLM JSON response — robust to shape drift."""

    if not isinstance(payload, dict):
        return []
    raw = payload.get("bullets")
    if not isinstance(raw, list):
        return []
    bullets: list[str] = []
    for item in raw:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                bullets.append(stripped)
    return bullets


def extract_amazon_bullets_via_llm(
    project: BookProject,
    chapter_titles: Sequence[str],
    llm_completer: Callable[[str, str], dict[str, Any]],
) -> list[str]:
    """Call the LLM to extract book-specific sales bullets.

    ``llm_completer`` is expected to behave like ``LLMClient.complete_json``
    — take a system+user prompt pair and return a parsed JSON dict. Any
    exception (network, API key, malformed JSON) is swallowed and turned
    into an empty list so the caller can fall back to the deterministic
    template path without aborting the pipeline.
    """

    user_prompt = build_llm_bullets_user_prompt(project, chapter_titles)
    try:
        payload = llm_completer(LLM_BULLETS_SYSTEM_PROMPT, user_prompt)
    except Exception:
        return []
    return _parse_llm_bullets_payload(payload)
