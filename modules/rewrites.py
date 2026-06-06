"""Concrete rewrite suggestions for German nonfiction KDP metadata.

Generates 3 actionable alternatives per field (title, subtitle, Amazon
description short-form) instead of generic "title too short" feedback.

Each alternative carries:
  * the rewritten text (clipped to a sensible KDP length),
  * a keyword score (0-100) measuring overlap with the book's anchor
    keywords — so the author can pick a variant that still matches their
    discoverability surface,
  * a one-line buyer motivation explaining *why* a stranger on Amazon
    might click this variant.

Pure-Python, no LLM dependency. The variants follow proven German
nonfiction bestseller patterns (method, authority, anti-hype, pain,
proof, audience) parameterised with the project's own anchor keywords so
output stays specific instead of generic.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from modules.discovery import BookProject

# KDP-friendly maximum character counts. Amazon's hard limits are higher
# than these, but the shorter cutoff produces variants that fit the
# thumbnail surface where buying happens.
TITLE_MAX_CHARS: int = 60
SUBTITLE_MAX_CHARS: int = 150
DESCRIPTION_LEAD_MAX_CHARS: int = 320

# Minimum lengths below which a field is flagged as "zu kurz".
TITLE_MIN_CHARS: int = 12
SUBTITLE_MIN_CHARS: int = 24
DESCRIPTION_LEAD_MIN_CHARS: int = 120

# German + English stopwords that we strip from anchor-keyword
# extraction. Tuned for nonfiction metadata rather than running prose.
STOPWORDS: frozenset[str] = frozenset(
    {
        "der", "die", "das", "den", "dem", "des",
        "ein", "eine", "einen", "einem", "eines", "einer",
        "und", "oder", "aber", "auch", "sich", "nicht",
        "mit", "von", "fuer", "für", "ueber", "über",
        "wie", "was", "ich", "wir", "haben", "wird", "kann",
        "wenn", "dass", "als", "aus", "bei", "nach", "seit",
        "ohne", "ist", "sind", "wurde", "werden", "sein",
        "an", "in", "im", "am", "zum", "zur", "zu", "auf",
        "the", "and", "for", "with", "how", "what", "that",
        "this", "from", "your", "you", "are", "have", "has",
        "buch", "kapitel",
    }
)

# Anti-hype, operator-flavoured hooks proven to convert on German KDP.
ANTI_HYPE_TAGS: tuple[str, ...] = (
    "ohne Hype",
    "ehrlich gemacht",
    "aus der Praxis",
)

# Generic fallback audience labels used only when the subtitle does not
# expose a real reader persona.
FALLBACK_AUDIENCES: tuple[str, ...] = (
    "Praktiker",
    "Selbstaendige",
    "Operatoren",
)

# Canonical anti-hype vocabulary. Defined here in the lowest-level metadata
# module so amazon_html (which imports from this module) can alias it as
# ``LLM_BULLETS_HYPE_TOKENS`` and kdp_keywords can reuse it transitively —
# one single source of truth, no circular import. Each entry is lowercase
# and carries both the ASCII-folded and umlaut spelling where relevant so a
# raw, un-normalised LLM string is still caught.
REWRITE_HYPE_TOKENS: tuple[str, ...] = (
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

# Provenance labels for a rewrite option. ``template`` is the default
# (deterministic bestseller-pattern variant); ``llm`` marks a variant the
# optional LLM-Pass produced by rewriting the author's own original.
REWRITE_SOURCE_TEMPLATE: str = "template"
REWRITE_SOURCE_LLM: str = "llm"
REWRITE_SOURCES: tuple[str, ...] = (REWRITE_SOURCE_TEMPLATE, REWRITE_SOURCE_LLM)


@dataclass(frozen=True)
class RewriteOption:
    """One concrete rewrite candidate the author can copy-paste."""

    text: str
    char_count: int
    keyword_score: int
    motivation: str
    # Provenance: ``"template"`` for the deterministic bestseller-pattern
    # variants, ``"llm"`` for variants the optional LLM-Pass rewrote
    # directly from the author's original metadata. Mirrors the
    # ``rewrite_source`` convention in modules/sample_scan.py so downstream
    # tools detect the LLM pathway from a stable label, not from prose.
    source: str = REWRITE_SOURCE_TEMPLATE

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "char_count": self.char_count,
            "keyword_score": self.keyword_score,
            "motivation": self.motivation,
            "source": self.source,
        }


@dataclass(frozen=True)
class RewriteBundle:
    """All rewrite candidates for one metadata field plus its diagnosis."""

    field: str
    original: str
    diagnosis: list[str]
    options: list[RewriteOption]

    def to_json(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "original": self.original,
            "diagnosis": list(self.diagnosis),
            "options": [option.to_json() for option in self.options],
        }


@dataclass(frozen=True)
class RewriteReport:
    """Aggregated rewrite report across title, subtitle, and description."""

    anchors: list[str]
    bundles: list[RewriteBundle] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "anchors": list(self.anchors),
            "bundles": [bundle.to_json() for bundle in self.bundles],
        }


def _normalize_token(word: str) -> str:
    return word.strip(".,!?;:\"'()[]–—-").lower()


def extract_anchor_keywords(project: BookProject, *, limit: int = 8) -> list[str]:
    """Return up to ``limit`` distinctive substantive words from metadata.

    Anchors are the author's existing discoverability surface — variants
    that drop too many of them risk losing organic search traffic.
    """

    raw = " ".join(
        part
        for part in (project.title, project.subtitle, project.amazon_description)
        if part
    )
    counts: dict[str, int] = {}
    for raw_token in re.findall(r"[\wÄÖÜäöüß-]{4,}", raw, flags=re.UNICODE):
        token = _normalize_token(raw_token)
        if not token or token.isdigit() or token in STOPWORDS:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [word for word, _ in ranked[:limit]]


def score_keywords(text: str, anchors: list[str]) -> int:
    """0-100 keyword overlap score against the project's anchor keywords."""

    if not anchors:
        return 0
    text_l = text.lower()
    hits = sum(1 for anchor in anchors if anchor in text_l)
    denominator = max(3, len(anchors))
    return max(0, min(100, round(100 * hits / denominator)))


def _clip(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text.rfind(" ", 0, max_chars)
    if cut < int(max_chars * 0.6):
        cut = max_chars
    return text[:cut].rstrip(" ,;:-")


def _extract_subject(project: BookProject) -> str:
    """Best single-noun-ish subject extracted from the title (or fallback)."""

    title = (project.title or "").strip()
    if not title:
        return "Sachbuch"
    if ":" in title:
        head, tail = (part.strip() for part in title.split(":", 1))
        # Prefer the longer, more substantive side.
        candidate = head if len(head) >= len(tail) else tail
        return candidate or title
    # Otherwise drop trailing audience clause if present.
    cleaned = re.sub(r"\s+(?:fuer|für)\s+.+$", "", title, flags=re.IGNORECASE)
    return cleaned.strip() or title


def _extract_audience(project: BookProject) -> str:
    """Best single audience descriptor from subtitle, falling back to a default."""

    subtitle = (project.subtitle or "").strip()
    if subtitle:
        match = re.search(
            r"\b(?:fuer|für)\s+([^,.;–—]+)",
            subtitle,
            flags=re.IGNORECASE,
        )
        if match:
            audience = match.group(1).strip()
            audience = re.sub(r"\s+und\s+andere.*$", "", audience, flags=re.IGNORECASE)
            if audience:
                return audience
    return FALLBACK_AUDIENCES[0]


def _diagnose_field(value: str, *, min_chars: int, max_chars: int, label: str) -> list[str]:
    findings: list[str] = []
    if not value:
        findings.append(f"{label} fehlt komplett — Amazon-Listing waere nicht conversion-faehig.")
        return findings
    length = len(value)
    if length < min_chars:
        findings.append(
            f"{label} ist sehr kurz ({length} Zeichen) — bietet kaum Suchflaeche und Versprechen."
        )
    if length > max_chars:
        findings.append(
            f"{label} ist zu lang ({length} Zeichen) — wird im Thumbnail abgeschnitten."
        )
    if value.isupper():
        findings.append(f"{label} ist komplett in Grossbuchstaben — wirkt schreiend, nicht serioes.")
    if value.endswith("..."):
        findings.append(f"{label} endet mit '...' — wirkt unfertig und reduziert Glaubwuerdigkeit.")
    return findings


def diagnose_title(original: str) -> list[str]:
    return _diagnose_field(original, min_chars=TITLE_MIN_CHARS, max_chars=TITLE_MAX_CHARS, label="Titel")


def diagnose_subtitle(original: str) -> list[str]:
    findings = _diagnose_field(
        original, min_chars=SUBTITLE_MIN_CHARS, max_chars=SUBTITLE_MAX_CHARS, label="Untertitel"
    )
    if original and not re.search(r"\b(?:fuer|für)\b", original, flags=re.IGNORECASE):
        findings.append("Untertitel nennt keine konkrete Zielgruppe ('fuer ...').")
    return findings


def diagnose_description(original: str) -> list[str]:
    findings = _diagnose_field(
        original,
        min_chars=DESCRIPTION_LEAD_MIN_CHARS,
        max_chars=10_000,
        label="Beschreibungs-Einstieg",
    )
    if original and "!" in original[:DESCRIPTION_LEAD_MAX_CHARS] and original.count("!") >= 3:
        findings.append("Einstieg enthaelt mehrere Ausrufezeichen — wirkt nach Hype und schadet Vertrauen.")
    if original and not re.search(r"\d", original[:DESCRIPTION_LEAD_MAX_CHARS]):
        findings.append("Einstieg enthaelt keine einzige Zahl — kein konkreter Beweis im Kindle-Sample.")
    return findings


def _title_candidates(subject: str, audience: str) -> list[tuple[str, str]]:
    """Return (text, motivation) pairs for title variants."""

    subject = subject or "Sachbuch"
    return [
        (
            f"{subject}: Was wirklich funktioniert",
            (
                "Buyer-Click: Direkte Substanz-Versprechen-Formel. Skeptische Leser klicken, "
                "weil das Buch sich sofort vom Motivations-Sachbuch absetzt."
            ),
        ),
        (
            f"Das {subject}-Playbook fuer {audience}",
            (
                "Buyer-Click: 'Playbook' signalisiert umsetzbare Methode statt Theorie — "
                "typischer Hook bei deutschen Nonfiction-Bestsellern."
            ),
        ),
        (
            f"{subject} {ANTI_HYPE_TAGS[0]}",
            (
                "Buyer-Click: Spricht Leser an, die genug von Hype-Buechern haben. "
                "Funktioniert besonders im operator-/CFO-Segment."
            ),
        ),
    ]


def _subtitle_candidates(subject: str, audience: str) -> list[tuple[str, str]]:
    subject_l = (subject or "die Methode").lower()
    return [
        (
            f"Die ehrliche Anleitung fuer {audience}, die {subject_l} wirklich umsetzen wollen",
            (
                "Buyer-Click: Nennt Zielgruppe und Outcome im selben Satz — Amazon-Algorithmus "
                "und Mensch sehen sofort, fuer wen das Buch ist."
            ),
        ),
        (
            f"Praxisnahes Vorgehen fuer {audience} — Schritt fuer Schritt, ohne Floskeln",
            (
                "Buyer-Click: 'Schritt fuer Schritt' ist einer der staerksten Conversion-Hooks "
                "im deutschen Sachbuch-Markt, ohne Hype-Floskel."
            ),
        ),
        (
            f"Was {audience} ueber {subject_l} wissen muessen — kurz, konkret, aus der Praxis",
            (
                "Buyer-Click: Setzt das Versprechen 'kurz + konkret' — gut fuer Berufstaetige, "
                "die kein theoretisches Buch wollen."
            ),
        ),
    ]


def _description_candidates(subject: str, audience: str) -> list[tuple[str, str]]:
    subject_l = (subject or "diesem Thema").lower()
    return [
        (
            (
                f"Dieses Buch zeigt {audience}, wie {subject_l} im Alltag wirklich funktioniert. "
                "Keine Theorie, keine Motivationsspruechen — sondern Schritt-fuer-Schritt-Vorgehen, "
                "Checklisten und Beispiele aus echten Projekten. Lies die ersten zehn Seiten und "
                "entscheide selbst, ob es zu deiner Situation passt."
            ),
            (
                "Buyer-Click: Promise-First-Eroeffnung — der Leser weiss in einem Satz, was er "
                "bekommt. 'Lies die ersten zehn Seiten und entscheide selbst' senkt die "
                "Kaufhuerde auf Amazon spuerbar."
            ),
        ),
        (
            (
                f"Viele {audience} kennen das Problem: Sie wissen, dass {subject_l} wichtig ist, "
                "aber jede Anleitung klingt nach Hype. Dieses Buch macht es anders. Es liefert "
                "klare Entscheidungsregeln, konkrete Vorlagen und Beispiele aus der Praxis. "
                "Geschrieben fuer Menschen, die umsetzen statt motivieren wollen."
            ),
            (
                "Buyer-Click: Pain-First-Eroeffnung — spiegelt die Frustration des Lesers mit "
                "Hype-Buechern und positioniert das eigene Buch sofort als Alternative."
            ),
        ),
        (
            (
                f"Aus 10+ Jahren operativer Praxis: Hier liest du, wie {subject_l} echt "
                "umgesetzt wird. Kein Berater-Sprech, keine ueberzogenen Versprechen. Stattdessen "
                "Methoden, die in realen Projekten funktioniert haben — mit Zahlen, Beispielen "
                f"und klaren Schritten fuer {audience}."
            ),
            (
                "Buyer-Click: Proof-First-Eroeffnung — Erfahrung + Zahlen + Klartext. "
                "Funktioniert besonders bei B2B-/Operator-Lesern und reduziert das Review-Risiko."
            ),
        ),
    ]


def _build_options(
    candidates: list[tuple[str, str]],
    *,
    max_chars: int,
    anchors: list[str],
) -> list[RewriteOption]:
    options: list[RewriteOption] = []
    for text, motivation in candidates:
        clipped = _clip(text, max_chars)
        options.append(
            RewriteOption(
                text=clipped,
                char_count=len(clipped),
                keyword_score=score_keywords(clipped, anchors),
                motivation=motivation,
            )
        )
    return options


def build_rewrite_report(project: BookProject) -> RewriteReport:
    """Generate the rewrite report for a project's metadata."""

    anchors = extract_anchor_keywords(project)
    subject = _extract_subject(project)
    audience = _extract_audience(project)

    title_bundle = RewriteBundle(
        field="title",
        original=project.title or "",
        diagnosis=diagnose_title(project.title or ""),
        options=_build_options(
            _title_candidates(subject, audience),
            max_chars=TITLE_MAX_CHARS,
            anchors=anchors,
        ),
    )
    subtitle_bundle = RewriteBundle(
        field="subtitle",
        original=project.subtitle or "",
        diagnosis=diagnose_subtitle(project.subtitle or ""),
        options=_build_options(
            _subtitle_candidates(subject, audience),
            max_chars=SUBTITLE_MAX_CHARS,
            anchors=anchors,
        ),
    )
    description_bundle = RewriteBundle(
        field="description_lead",
        original=project.amazon_description or "",
        diagnosis=diagnose_description(project.amazon_description or ""),
        options=_build_options(
            _description_candidates(subject, audience),
            max_chars=DESCRIPTION_LEAD_MAX_CHARS,
            anchors=anchors,
        ),
    )
    return RewriteReport(
        anchors=anchors,
        bundles=[title_bundle, subtitle_bundle, description_bundle],
    )


_FIELD_HEADINGS: dict[str, str] = {
    "title": "Titel-Varianten",
    "subtitle": "Untertitel-Varianten",
    "description_lead": "Beschreibungs-Einstieg (erste 3 Zeilen)",
}


def render_rewrite_report_markdown(project: BookProject, report: RewriteReport) -> str:
    """Render the rewrite report as beginner-friendly German markdown."""

    title = project.title or project.project_id
    lines: list[str] = [
        "# Konkrete Rewrite-Vorschlaege",
        "",
        f"Buch: **{title}**",
        "",
        "Pro Feld bekommst du 3 alternative Texte mit Keyword-Score und Kauf-Motivation. "
        "Du kannst sie direkt ins KDP-Backend kopieren oder als Inspiration nutzen.",
        "",
        "## Anker-Keywords",
        "",
    ]
    if report.anchors:
        lines.append(", ".join(f"`{anchor}`" for anchor in report.anchors))
    else:
        lines.append("_Keine Anker-Keywords erkannt — Titel und Beschreibung pflegen._")
    lines.append("")

    for bundle in report.bundles:
        heading = _FIELD_HEADINGS.get(bundle.field, bundle.field)
        lines.extend([f"## {heading}", ""])
        original = bundle.original.strip() or "_(nicht gesetzt)_"
        lines.extend([
            f"**Original:** {original}",
            f"**Zeichen:** {len(bundle.original)}",
            "",
        ])
        if bundle.diagnosis:
            lines.extend(["**Diagnose:**", ""])
            lines.extend(f"- {item}" for item in bundle.diagnosis)
            lines.append("")
        for idx, option in enumerate(bundle.options, start=1):
            lines.extend([
                f"### Variante {idx}",
                "",
                f"> {option.text}",
                "",
                f"- Zeichen: **{option.char_count}**",
                f"- Keyword-Score: **{option.keyword_score}/100**",
                f"- Kauf-Motivation: {option.motivation}",
            ])
            if option.source == REWRITE_SOURCE_LLM:
                lines.append(
                    "- Quelle: LLM-Pass (direkt aus deinem Original umgeschrieben)"
                )
            lines.append("")
    return "\n".join(lines)


# --- Optional LLM-Pass: direct rewrites from the author's original --------
#
# The deterministic variants above follow proven bestseller patterns but
# are template-shaped. When the toggle + API key are present, this pass
# asks the LLM to rewrite the author's actual title / subtitle /
# description-lead — staying closer to the book's real voice. The LLM
# variants are APPENDED to the existing template options (never replace
# them) so the author can compare both. Any failure falls back to the
# template-only report — never an aborted run.

LLM_VARIANTS_SYSTEM_PROMPT: str = (
    "Du bist ein erfahrener Sachbuch-Lektor und KDP-Copywriter fuer den "
    "deutschen Markt. Deine Aufgabe: das uebergebene Original-Metadatenfeld "
    "(Titel, Untertitel oder Beschreibungs-Einstieg) so umschreiben, dass es "
    "staerker konvertiert, ohne den Kern des Buches zu veraendern. Bleibe nah "
    "am Original, behalte zentrale Begriffe (Anker-Keywords) bei und respektiere "
    "das genannte Zeichen-Limit. Kein Hype, keine Ausrufezeichen, keine "
    "Marketing-Floskeln wie 'ultimativ' oder 'garantiert'. "
    "Antworte ausschliesslich als JSON mit dem Schluessel 'variants' "
    "(Array von Objekten {field: str, text: str, motivation: str}). "
    "Nutze exakt die uebergebenen 'field'-Werte. Kein zusaetzlicher Text."
)

# How many LLM variants to keep per field (cost + report-length cap).
LLM_VARIANTS_PER_FIELD: int = 2
# Default buyer motivation when the LLM omits one for a variant.
LLM_VARIANTS_DEFAULT_MOTIVATION: str = (
    "Buyer-Click: LLM-Variante direkt aus deinem Original umgeschrieben — "
    "naeher an Stimme und Inhalt des Buches als die Template-Vorschlaege."
)
LLM_VARIANTS_MAX_MOTIVATION_CHARS: int = 280

# Per-field maximum / minimum character budgets, reused from the
# deterministic path so the LLM variants obey the same KDP-surface rules.
_FIELD_MAX_CHARS: dict[str, int] = {
    "title": TITLE_MAX_CHARS,
    "subtitle": SUBTITLE_MAX_CHARS,
    "description_lead": DESCRIPTION_LEAD_MAX_CHARS,
}
_FIELD_MIN_CHARS: dict[str, int] = {
    "title": TITLE_MIN_CHARS,
    "subtitle": SUBTITLE_MIN_CHARS,
    "description_lead": DESCRIPTION_LEAD_MIN_CHARS,
}


def _bundles_needing_rewrite(report: RewriteReport) -> list[RewriteBundle]:
    """Return bundles whose field has at least one diagnosis finding.

    Fields already in good shape (empty diagnosis) are skipped so the LLM
    budget only hits the metadata that actually needs copy work.
    """

    return [bundle for bundle in report.bundles if bundle.diagnosis]


def build_rewrite_variants_user_prompt(report: RewriteReport) -> str:
    """Render the user prompt for the LLM rewrite-variant generator.

    Only fields with a diagnosis are listed, each carrying the original
    text, the diagnosis findings and the hard character limit so the LLM
    grounds its rewrite in the real metadata. Returns an empty string when
    no field needs a rewrite — the caller short-circuits and never makes
    the LLM call.
    """

    candidates = _bundles_needing_rewrite(report)
    if not candidates:
        return ""
    anchor_line = (
        ", ".join(report.anchors)
        if report.anchors
        else "(keine erkannt — Begriffe aus dem Original beibehalten)"
    )
    blocks: list[str] = []
    for bundle in candidates:
        heading = _FIELD_HEADINGS.get(bundle.field, bundle.field)
        max_chars = _FIELD_MAX_CHARS.get(bundle.field, SUBTITLE_MAX_CHARS)
        original = bundle.original.strip() or "(nicht gesetzt)"
        block_lines = [
            f"field: {bundle.field} ({heading})",
            f"- Original: {original}",
            f"- Zeichen-Limit: {max_chars}",
        ]
        if bundle.diagnosis:
            block_lines.append("- Probleme: " + "; ".join(bundle.diagnosis))
        blocks.append("\n".join(block_lines))
    body_block = "\n\n".join(blocks)
    return (
        "Hier sind die Metadatenfelder mit Verbesserungsbedarf. Schreibe fuer "
        f"jedes Feld {LLM_VARIANTS_PER_FIELD} alternative Texte im geforderten "
        "JSON-Format. Behalte zentrale Begriffe bei.\n\n"
        f"Anker-Keywords: {anchor_line}\n\n"
        f"{body_block}"
    )


def _clean_variant_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip().strip('"“”«»')


def _parse_rewrite_variants_payload(
    payload: Any,
) -> dict[str, list[tuple[str, str]]]:
    """Extract ``{field: [(text, motivation), ...]}`` from the LLM response.

    Tolerant to shape drift: skips non-dict entries, unknown fields,
    non-string texts, texts with an exclamation mark (anti-hype), and texts
    shorter than the field minimum. Texts longer than the field maximum are
    clipped. At most ``LLM_VARIANTS_PER_FIELD`` variants survive per field.
    """

    if not isinstance(payload, dict):
        return {}
    raw_entries = payload.get("variants")
    if not isinstance(raw_entries, list):
        return {}
    out: dict[str, list[tuple[str, str]]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        field_key = entry.get("field")
        if field_key not in _FIELD_MAX_CHARS:
            continue
        if len(out.get(field_key, [])) >= LLM_VARIANTS_PER_FIELD:
            continue
        raw_text = entry.get("text")
        if not isinstance(raw_text, str):
            continue
        text = _clean_variant_text(raw_text)
        if "!" in text:
            continue
        if len(text) < _FIELD_MIN_CHARS.get(field_key, TITLE_MIN_CHARS):
            continue
        text = _clip(text, _FIELD_MAX_CHARS[field_key])
        if not text:
            continue
        raw_motivation = entry.get("motivation")
        motivation = (
            _clean_variant_text(raw_motivation)
            if isinstance(raw_motivation, str)
            else ""
        )
        if not motivation:
            motivation = LLM_VARIANTS_DEFAULT_MOTIVATION
        elif len(motivation) > LLM_VARIANTS_MAX_MOTIVATION_CHARS:
            motivation = (
                motivation[:LLM_VARIANTS_MAX_MOTIVATION_CHARS].rstrip(" ,;:-") + "…"
            )
        out.setdefault(field_key, []).append((text, motivation))
    return out


def extract_rewrite_variants_via_llm(
    report: RewriteReport,
    llm_completer: Callable[[str, str], dict[str, Any]],
) -> dict[str, list[tuple[str, str]]]:
    """Call the LLM to rewrite fields that need copy work.

    ``llm_completer`` behaves like ``LLMClient.complete_json`` — takes a
    system+user prompt pair and returns a parsed JSON dict. Any exception
    (network, API key, malformed JSON) is swallowed and turned into an empty
    mapping so the caller falls back to the deterministic template options
    without aborting the pipeline.
    """

    user_prompt = build_rewrite_variants_user_prompt(report)
    if not user_prompt:
        return {}
    try:
        payload = llm_completer(LLM_VARIANTS_SYSTEM_PROMPT, user_prompt)
    except Exception:
        return {}
    parsed = _parse_rewrite_variants_payload(payload)
    return validate_rewrite_variants(parsed, report).accepted


def apply_rewrite_variants(
    report: RewriteReport,
    variants: Mapping[str, Sequence[tuple[str, str]]],
    *,
    source: str = REWRITE_SOURCE_LLM,
) -> RewriteReport:
    """Return a new report with LLM variants appended to matching bundles.

    Pure function — neither ``report`` nor any frozen option is mutated.
    LLM variants are appended after the existing template options of the
    same field so the author can compare both. Bundles without a matching
    variant keep their options untouched. Returns the original ``report``
    instance when nothing was appended so the immutability guarantee holds
    without a wasted allocation. ``source`` is stamped on every appended
    option; unknown sources fall back to ``REWRITE_SOURCE_LLM``.
    """

    if not variants:
        return report
    label = source if source in REWRITE_SOURCES else REWRITE_SOURCE_LLM
    new_bundles: list[RewriteBundle] = []
    any_change = False
    for bundle in report.bundles:
        field_variants = variants.get(bundle.field)
        if not field_variants:
            new_bundles.append(bundle)
            continue
        added: list[RewriteOption] = []
        for text, motivation in field_variants:
            clipped = _clip(text, _FIELD_MAX_CHARS.get(bundle.field, SUBTITLE_MAX_CHARS))
            if not clipped:
                continue
            added.append(
                RewriteOption(
                    text=clipped,
                    char_count=len(clipped),
                    keyword_score=score_keywords(clipped, report.anchors),
                    motivation=motivation or LLM_VARIANTS_DEFAULT_MOTIVATION,
                    source=label,
                )
            )
        if not added:
            new_bundles.append(bundle)
            continue
        new_bundles.append(
            replace(bundle, options=[*bundle.options, *added])
        )
        any_change = True
    if not any_change:
        return report
    return replace(report, bundles=new_bundles)


# --- Quality gate for LLM rewrite variants --------------------------------
#
# ``_parse_rewrite_variants_payload`` only filters exclamation marks and raw
# length. This gate adds the three semantic checks the author actually cares
# about before a generated variant is allowed into the report:
#   1. anti-hype: no token from ``REWRITE_HYPE_TOKENS`` (single source of
#      truth shared with amazon_html / kdp_keywords);
#   2. anchor retention: when the project exposes anchor keywords, the variant
#      must keep at least one — a rewrite that drops the whole discoverability
#      surface is worse than the original;
#   3. no duplicate opening: the variant's first sentence must differ from the
#      original's first sentence, otherwise the "rewrite" did no real work.
# A variant that trips any rule is dropped (not repaired) so the template
# options remain the fallback.

# Word-boundary hype matcher built from the shared token list. Longest tokens
# first so "must have" wins over "must". Runs on the raw variant text so both
# the ASCII-folded and umlaut spellings are caught.
_REWRITE_HYPE_RE = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(token)
        for token in sorted(REWRITE_HYPE_TOKENS, key=len, reverse=True)
    )
    + r")",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]")
_COMPARE_NONWORD_RE = re.compile(r"[^\wÄÖÜäöüß]+", re.UNICODE)

# Rejection reason labels (stable strings for tests / future logging).
REWRITE_REJECT_HYPE: str = "contains_hype"
REWRITE_REJECT_NO_ANCHOR: str = "no_anchor_keyword"
REWRITE_REJECT_DUPLICATE_OPENING: str = "duplicate_opening"
REWRITE_REJECT_EMPTY: str = "empty_text"


@dataclass(frozen=True)
class RewriteVariantQualityResult:
    """Outcome of the LLM rewrite-variant quality gate.

    ``accepted`` maps each field to the variants that survived all three
    checks (same shape as ``_parse_rewrite_variants_payload`` so the caller
    can pass it straight to ``apply_rewrite_variants``). ``rejected`` lists
    ``(field, text, reason)`` triples for traceability and testing.
    """

    accepted: dict[str, list[tuple[str, str]]]
    rejected: tuple[tuple[str, str, str], ...]


def _opening_key(text: str) -> str:
    """Normalised first sentence used for the duplicate-opening check."""

    first = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)[0]
    return _COMPARE_NONWORD_RE.sub(" ", first.lower()).strip()


def validate_rewrite_variants(
    variants: Mapping[str, Sequence[tuple[str, str]]],
    report: RewriteReport,
) -> RewriteVariantQualityResult:
    """Drop hype, anchor-less, or original-echoing LLM rewrite variants.

    Pure function. ``report`` supplies the anchor keywords and per-field
    original text the checks need. Returns a ``RewriteVariantQualityResult``;
    the caller uses ``.accepted`` and may inspect ``.rejected`` for logging.
    """

    anchors = [anchor.lower() for anchor in report.anchors if anchor]
    originals = {bundle.field: bundle.original for bundle in report.bundles}
    accepted: dict[str, list[tuple[str, str]]] = {}
    rejected: list[tuple[str, str, str]] = []
    for field_key, items in variants.items():
        original_key = _opening_key(originals.get(field_key, "") or "")
        for text, motivation in items:
            if not isinstance(text, str) or not text.strip():
                rejected.append((field_key, str(text), REWRITE_REJECT_EMPTY))
                continue
            hype = _REWRITE_HYPE_RE.search(text)
            if hype is not None:
                rejected.append(
                    (field_key, text, f"{REWRITE_REJECT_HYPE}:{hype.group(0).lower()}")
                )
                continue
            if anchors and not any(anchor in text.lower() for anchor in anchors):
                rejected.append((field_key, text, REWRITE_REJECT_NO_ANCHOR))
                continue
            if original_key and _opening_key(text) == original_key:
                rejected.append((field_key, text, REWRITE_REJECT_DUPLICATE_OPENING))
                continue
            accepted.setdefault(field_key, []).append((text, motivation))
    return RewriteVariantQualityResult(accepted=accepted, rejected=tuple(rejected))
