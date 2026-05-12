"""Buyer-Persona Generator for German nonfiction KDP books.

Derives three concrete reader personas from Title + Subtitle +
Amazon-Description + (optional) chapter titles. Each persona carries
the four fields the author needs to write conversion-ready metadata:

* ``age_range`` — realistic age band for this niche / signal mix,
* ``job`` — concrete role / context,
* ``problem`` — the pain that drives this reader to search Amazon,
* ``buying_motive`` — *why* this reader clicks "Kaufen" on this book.

The generator is pure-Python and deterministic so it runs offline in
the QA pipeline (no LLM API key required) and stays reproducible
across rounds. It selects a baseline persona set per detected niche
and refines each persona using signals from the book's metadata
(audience markers, anchor keywords, proof patterns, chapter titles).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from modules.competitive_positioning import (
    NICHE_LABELS,
    PROOF_PATTERNS,
    _ascii_fold,
    _joined_metadata,
    detect_niche,
)
from modules.discovery import BookProject
from modules.rewrites import _extract_audience, _extract_subject, extract_anchor_keywords


# --- Constants -------------------------------------------------------------

MAX_PERSONAS: int = 3
MAX_QUOTE_CHARS: int = 140
MAX_PROBLEM_CHARS: int = 180
MAX_MOTIVE_CHARS: int = 180

# Markers we look for in metadata / TOC to nudge persona refinement.
_B2B_MARKERS: tuple[str, ...] = (
    "mittelstand", "kmu", "b2b", "vertrieb", "geschaeftsfuehr",
    "cfo", "ceo", "controlling", "kunde", "umsatz", "branche",
    "konzern", "unternehm",
)
_SELF_EMPLOYED_MARKERS: tuple[str, ...] = (
    "selbststaendig", "freelanc", "solopreneur", "gruender",
    "einzelunternehm", "nebenberuf", "side hustle",
)
_BEGINNER_MARKERS: tuple[str, ...] = (
    "einsteig", "anfaeng", "grundlag", "schritt", "leitfaden",
)
_TIME_PRESSURE_MARKERS: tuple[str, ...] = (
    "stunde", "tag", "woche", "monat", "minuten", "30 tag", "14 tag",
)


# --- Data records ----------------------------------------------------------


@dataclass(frozen=True)
class BuyerPersona:
    """A single concrete buyer persona derived from book metadata."""

    label: str
    age_range: str
    job: str
    problem: str
    buying_motive: str
    anchor_quote: str
    channels: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "age_range": self.age_range,
            "job": self.job,
            "problem": self.problem,
            "buying_motive": self.buying_motive,
            "anchor_quote": self.anchor_quote,
            "channels": list(self.channels),
        }


@dataclass(frozen=True)
class PersonaReport:
    """Aggregated buyer persona analysis for a single book."""

    niche_key: str
    niche_label: str
    niche_confidence: int
    audience: str
    subject: str
    personas: list[BuyerPersona]
    anchors: list[str] = field(default_factory=list)
    signal_flags: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "niche_key": self.niche_key,
            "niche_label": self.niche_label,
            "niche_confidence": self.niche_confidence,
            "audience": self.audience,
            "subject": self.subject,
            "personas": [p.to_json() for p in self.personas],
            "anchors": list(self.anchors),
            "signal_flags": list(self.signal_flags),
        }


# --- Niche baseline persona templates --------------------------------------

# Each entry: (label, age_range, job, problem, buying_motive)
PersonaSpec = tuple[str, str, str, str, str]

_NICHE_PERSONAS: dict[str, tuple[PersonaSpec, PersonaSpec, PersonaSpec]] = {
    "ki_und_ai": (
        (
            "Der pragmatische Mittelstands-Operator",
            "38–52",
            "Geschäftsführer oder Bereichsleiter im Mittelstand (20–250 Mitarbeitende)",
            "Sieht den KI-Hype, weiß aber nicht, wo er anfangen soll, ohne Geld in nutzlose Tools zu verbrennen.",
            "Will eine ehrliche, getestete Methode statt Berater-Pitch — am liebsten ein Buch, das Schritt für Schritt zeigt, wo KI im Mittelstand wirklich Umsatz oder Zeit bringt.",
        ),
        (
            "Die skeptische CFO",
            "42–58",
            "CFO, Controllerin oder kaufmännische Leiterin",
            "Wird intern gefragt, KI-Investitionen zu bewerten, hat aber keine belastbaren Zahlen oder Cases.",
            "Sucht ein Buch mit echten Zahlen, Cases und Bilanz-Auswirkungen — nicht noch ein Manifest von einem Beratungshaus.",
        ),
        (
            "Der ambitionierte Praktiker",
            "28–40",
            "Senior-Sachbearbeiter, Projektleiter oder ambitionierte Fachkraft",
            "Spürt, dass KI seinen Job verändert, und will vorne dabei sein, statt zugucken zu müssen.",
            "Will eine umsetzbare Anleitung, mit der er innerhalb von 30 Tagen ein konkretes KI-Projekt im eigenen Team starten kann.",
        ),
    ),
    "finanzen_und_cfo": (
        (
            "Die operative CFO",
            "40–55",
            "CFO oder kaufmännische Leiterin in einem KMU",
            "Operative Finanzführung ist ein Dauerbrand: Liquidität, Forecast, Reporting — alles gleichzeitig.",
            "Sucht ein Praxis-Playbook mit Checklisten, das Steuerungswerkzeuge liefert, statt theoretischer Modelle.",
        ),
        (
            "Der Inhaber-Geschäftsführer",
            "45–60",
            "Inhaber oder Geschäftsführer eines mittelständischen Unternehmens",
            "Versteht sein Produkt, aber Bilanz und Cashflow bleiben eine Blackbox — und der Steuerberater erklärt es nicht plausibel.",
            "Will ein Buch, das die Finanzführung in seine Sprache übersetzt und ihm zeigt, wo das Geld wirklich verloren geht.",
        ),
        (
            "Der ambitionierte Controller",
            "30–42",
            "Controller, Finanzanalyst oder Senior-Buchhalter mit Ambition zur CFO-Rolle",
            "Braucht mehr als Lehrbuch-Wissen, um den Sprung in die Führungsebene zu schaffen.",
            "Will Frameworks und Praxis-Cases, um in Meetings überzeugend zu argumentieren — und intern zur nächsten Karrierestufe aufzusteigen.",
        ),
    ),
    "vertrieb_und_marketing": (
        (
            "Die B2B-Vertriebsleiterin",
            "35–50",
            "Vertriebsleiterin oder Head of Sales in einem mittelständischen B2B-Unternehmen",
            "Klassische Verkaufsbücher passen nicht zu skeptischen Einkäufern; Funnel-Hype passt nicht zu B2B-Zyklen.",
            "Sucht ein Buch, das echten B2B-Vertrieb beschreibt: Termin, Demo, Einwand, Abschluss — ohne Show-Verkäufer-Rhetorik.",
        ),
        (
            "Der Solopreneur mit Akquise-Lücke",
            "30–48",
            "Selbständige Beraterin, Trainerin oder Agentur-Inhaber",
            "Liefert hervorragende Arbeit, aber kommt nicht an genug Kunden — und Hard-Sell fühlt sich falsch an.",
            "Will eine respektvolle Akquise-Methode, die zur eigenen Marke passt und planbar neue Kunden bringt.",
        ),
        (
            "Die Marketing-Verantwortliche",
            "28–42",
            "Marketing-Managerin oder Head of Marketing im Mittelstand",
            "Generiert Leads, aber Vertrieb beschwert sich über Qualität — und der Geschäftsführer fragt nach ROI.",
            "Sucht ein Buch, das Marketing und Vertrieb messbar verbindet und konkrete Methoden statt Buzzwords liefert.",
        ),
    ),
    "produktivitaet_fokus": (
        (
            "Der überlastete Operator",
            "32–48",
            "Bereichsleiter, Projektleiter oder Operator mit Personalverantwortung",
            "Kalender voll, Output stagniert, Klassiker wie GTD greifen für komplexe Operator-Arbeit nicht ausreichend.",
            "Will ein Buch, das Produktivität in komplexen Verantwortungsbereichen organisiert, statt nur Mikro-Gewohnheiten zu predigen.",
        ),
        (
            "Die Wissensarbeiterin im Home-Office",
            "26–42",
            "Wissensarbeiterin, Beraterin oder Senior-Fachkraft im Remote-Setup",
            "Verliert Fokus zwischen Tools, Meetings und ständigen Pings.",
            "Sucht konkrete Routinen und Werkzeuge, um Deep Work tatsächlich planbar in den Alltag zu integrieren.",
        ),
        (
            "Der Solo-Selbständige",
            "30–50",
            "Solo-Selbständiger oder Freelancer mit mehreren Projekten parallel",
            "Macht alles selbst — Akquise, Lieferung, Buchhaltung — und verliert Zeit an Kontextwechsel.",
            "Will ein System, das ihm sagt, womit er heute, diese Woche, diesen Monat anfangen soll — ohne ein neues Tool-Set zu erfordern.",
        ),
    ),
    "fuehrung_team": (
        (
            "Die frischgebackene Team-Leiterin",
            "28–40",
            "Erstmalige Team-Leiterin oder Abteilungsleiterin",
            "War gerade noch beste Fachkraft, soll jetzt führen — ohne Anleitung und mit Konflikten im Team.",
            "Sucht konkrete Sätze, Mails und Meeting-Strukturen, die sie ab Montag verwenden kann.",
        ),
        (
            "Der Mittelstands-Geschäftsführer",
            "38–58",
            "Geschäftsführer oder Inhaber-Geschäftsführer im Mittelstand",
            "Wachstum stockt, weil das Team nicht skaliert — Konzern-Bücher passen nicht zur Realität von 30 Mitarbeitenden.",
            "Will ein Führungsbuch, das KMU-Realität ernst nimmt und konkrete Prozesse liefert, statt Konzern-Folklore.",
        ),
        (
            "Die erfahrene Bereichsleiterin",
            "40–55",
            "Bereichsleiterin oder Senior-Managerin mit mehreren Teams",
            "Routinen funktionieren, aber Innovationsdruck und Generationenwechsel im Team überfordern alte Muster.",
            "Sucht moderne Führungs-Frameworks, die zur eigenen operativen Erfahrung passen — kein New-Work-Manifest.",
        ),
    ),
    "selbststaendigkeit": (
        (
            "Der Wechsel-Willige",
            "30–45",
            "Angestellter, der ernsthaft über den Schritt in die Selbständigkeit nachdenkt",
            "Sieht Side-Hustle-Versprechen im Netz, traut der Sache aber nicht — und will keinen unbezahlten Mentor-Pitch.",
            "Sucht ein ehrliches Buch, das Zahlen, Risiken und realistische Startpfade zeigt — nicht den nächsten Unicorn-Mythos.",
        ),
        (
            "Die Solo-Selbständige im 2. Jahr",
            "28–42",
            "Selbständige Beraterin, Trainerin oder Kreative im zweiten oder dritten Geschäftsjahr",
            "Erste Kunden da, aber Cashflow schwankt, Akquise zerfasert, und es fehlt eine wiederholbare Methode.",
            "Will ein Operations-Buch, das die unsexy Themen (Pipeline, Pricing, Lieferung) statt der nächsten Mindset-Lektion adressiert.",
        ),
        (
            "Der nebenberufliche Gründer",
            "32–48",
            "Angestellter mit Nebenprojekt, das er in Hauptprojekt überführen will",
            "Hat Zeit nur in Randstunden — und braucht einen Plan, der dazu passt.",
            "Sucht klare Meilensteine und konkrete Schritte, mit denen der Wechsel finanzierbar wird.",
        ),
    ),
    "immobilien_einkommen": (
        (
            "Die einkommensstarke Angestellte",
            "30–50",
            "Angestellte oder Fachkraft mit gutem Einkommen und Eigenkapital",
            "Will langfristig Vermögen aufbauen, traut sich aber nicht an die erste Immobilie ohne klare Methode.",
            "Sucht eine Schritt-für-Schritt-Anleitung mit echten Zahlen, statt 'Reich-mit-Immobilien'-Versprechen.",
        ),
        (
            "Der ambitionierte Familienvater",
            "32–48",
            "Familienvater, oft Doppelverdiener, mit Eigenkapital aus Erbschaft oder Sparphase",
            "Will dem Familienvermögen ein zweites Standbein geben, aber Cashflow- und Steuer-Themen wirken undurchschaubar.",
            "Will ein Buch, das Cashflow, Tilgung, Nebenkosten und Steuer-Effekte ehrlich rechnet — kein Coaching-Lead-Magnet.",
        ),
        (
            "Der bestehende Vermieter",
            "40–60",
            "Bereits Vermieter mit 1–3 Einheiten, will Portfolio strukturieren",
            "Verwaltung wächst, Rendite stagniert, und der Steuerberater liefert keine strategische Sicht.",
            "Sucht ein Buch, das Portfolio-Steuerung und Optimierung in seine konkrete Realität übersetzt.",
        ),
    ),
    "mindset_gesundheit": (
        (
            "Die berufstätige Hochleisterin",
            "30–48",
            "Senior-Fachkraft, Führungskraft oder Selbständige mit hoher Auslastung",
            "Funktioniert beruflich, aber Schlaf, Erholung und Energie geraten aus dem Gleichgewicht.",
            "Sucht ein Buch, das konkrete Routinen für Berufstätige liefert — kein Wellness-Manifest, keine Esoterik.",
        ),
        (
            "Der Mid-Career-Wechsler",
            "35–52",
            "Mid-Career-Berufstätiger nach Reorganisation, Wechsel oder Lebensereignis",
            "Spürt, dass etwas grundlegend nachgesteuert werden muss, will aber keine generische Selbstfindung.",
            "Will ein Buch, das ehrliche Methoden statt Motivations-Sprüche bietet und in den Berufsalltag passt.",
        ),
        (
            "Die ambitionierte Praktikerin",
            "26–40",
            "Junge Berufstätige oder Studierende mit klaren Karriere-Zielen",
            "Will mental belastbar bleiben, ohne sich aufzureiben — und ist allergisch gegen Pop-Psychologie.",
            "Sucht wissenschaftlich anschlussfähige Methoden, die in einen vollen Wochenplan passen.",
        ),
    ),
    "allgemeines_sachbuch": (
        (
            "Der ambitionierte Praktiker",
            "30–50",
            "Berufstätiger mit Bereichsverantwortung oder Selbständiger",
            "Will sich in einem konkreten Thema schnell auf Senior-Level bringen — ohne Lehrbuch-Umweg.",
            "Sucht ein konzentriertes Buch, das die Essenz liefert und in der eigenen Arbeit sofort einsetzbar ist.",
        ),
        (
            "Die Wiedereinsteigerin",
            "35–55",
            "Wieder- oder Quereinsteigerin in eine neue Disziplin",
            "Braucht einen verlässlichen Einstieg ohne überflüssige Theorie.",
            "Will einen klaren Lernpfad mit Checkpoints, statt verstreute Blogposts und Tutorials.",
        ),
        (
            "Der entscheidungsstarke Käufer",
            "40–60",
            "Senior-Berufstätiger oder Inhaber, der vor einer konkreten Entscheidung steht",
            "Hat wenig Zeit und will eine fundierte Position aufbauen, bevor er handelt.",
            "Sucht ein Buch, das die wichtigsten Argumente in unter 4 Lesestunden serviert.",
        ),
    ),
}


# --- Refinement helpers ----------------------------------------------------


def _detect_signal_flags(folded: str) -> list[str]:
    flags: list[str] = []
    if any(m in folded for m in _B2B_MARKERS):
        flags.append("b2b")
    if any(m in folded for m in _SELF_EMPLOYED_MARKERS):
        flags.append("selbststaendig")
    if any(m in folded for m in _BEGINNER_MARKERS):
        flags.append("einsteiger")
    if any(m in folded for m in _TIME_PRESSURE_MARKERS):
        flags.append("zeit_knapp")
    if PROOF_PATTERNS.search(folded):
        flags.append("proof_signal")
    return flags


def _format_anchor_quote(audience: str, subject: str, anchors: list[str]) -> str:
    """Build a short imagined search query the persona might use."""

    anchor_text = " ".join(anchors[:2]) if anchors else subject.lower()
    raw = f"{subject} für {audience} – {anchor_text}"
    raw = re.sub(r"\s+", " ", raw).strip(" -–")
    return raw[:MAX_QUOTE_CHARS]


def _refine_problem(base: str, anchors: list[str], flags: list[str]) -> str:
    """Append signal-tailored detail to the baseline problem statement."""

    extras: list[str] = []
    if "zeit_knapp" in flags:
        extras.append("Zeitfenster ist eng — alles muss neben dem operativen Geschäft passieren")
    if "einsteiger" in flags:
        extras.append("klassische Quellen wirken zu komplex oder zu akademisch")
    if anchors:
        anchor_phrase = ", ".join(anchors[:3])
        extras.append(f"sucht aktiv nach Inhalten zu {anchor_phrase}")
    if not extras:
        return base[:MAX_PROBLEM_CHARS]
    suffix = " — und " + "; ".join(extras) + "."
    candidate = base.rstrip(".") + "." + suffix
    return candidate[:MAX_PROBLEM_CHARS]


def _refine_motive(base: str, subject: str, flags: list[str]) -> str:
    """Append signal-tailored detail to the buying motive."""

    extras: list[str] = []
    if "proof_signal" in flags:
        extras.append(f"will Zahlen und Fallbeispiele zu {subject} sehen, keine Behauptungen")
    if "b2b" in flags:
        extras.append("erwartet B2B-Realität statt Consumer-Tipps")
    if "selbststaendig" in flags:
        extras.append("braucht eine Lösung, die zu Solo-Strukturen passt")
    if not extras:
        return base[:MAX_MOTIVE_CHARS]
    suffix = " — und " + "; ".join(extras) + "."
    candidate = base.rstrip(".") + "." + suffix
    return candidate[:MAX_MOTIVE_CHARS]


def _normalize_audience(audience: str) -> str:
    cleaned = re.sub(r"\s+", " ", audience or "").strip(" -–.,")
    return cleaned or "Praktiker"


# Default marketing channels per niche. Used as a baseline when the
# persona-job text doesn't carry a stronger role-specific signal.
MAX_CHANNELS: int = 3
_NICHE_DEFAULT_CHANNELS: dict[str, tuple[str, ...]] = {
    "ki_und_ai": ("LinkedIn (KI-Communities)", "X / Twitter (AI-Bubble)", "Fachblog-Gastposts"),
    "finanzen_und_cfo": ("LinkedIn (CFO/Controller-Gruppen)", "XING (DACH-B2B)", "Finance-Newsletter"),
    "vertrieb_und_marketing": ("LinkedIn (Sales-Navigator)", "XING (DACH-B2B)", "Sales-Podcasts"),
    "produktivitaet": ("LinkedIn", "Newsletter-Cross-Promotion", "Produktivitaets-Podcasts"),
    "fuehrung": ("LinkedIn (Leadership-Communities)", "Newsletter-Empfehlungen", "Executive-Podcasts"),
    "selbststaendigkeit": ("LinkedIn", "X / Twitter (Solo-Business)", "Solopreneur-Newsletter"),
    "immobilien": ("YouTube (Immobilien-Kanäle)", "Instagram (Immobilien-Influencer)", "Facebook-Gruppen"),
    "mindset": ("Instagram", "YouTube", "Podcast-Interviews"),
    "allgemeines_sachbuch": ("Amazon-Anzeigen", "LinkedIn", "Themen-Newsletter"),
}

# Job-keyword → primary-channel override. The first hit wins; further hits
# fall through to the niche default. Keys are ascii-folded lowercase.
# Order matters: more-specific solo/sales roles must be checked BEFORE the
# generic "inhaber"/"geschaeftsfuehr" override, otherwise "Agentur-Inhaber"
# would hit the executive override before the solo override.
_JOB_CHANNEL_OVERRIDES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cfo", "controller", "controlling", "kaufmaennisch", "kaufmännisch"), "LinkedIn (CFO/Controller-Gruppen)"),
    (("vertriebsleiter", "head of sales", "sales-leiter"), "LinkedIn (Sales-Navigator)"),
    (("solopreneur", "freelanc", "agentur", "trainer", "berater"), "LinkedIn + X (Solo-Business)"),
    (("geschaeftsfuehr", "geschäftsführ", "inhaber", "ceo"), "LinkedIn (DACH-B2B-Decision-Maker)"),
    (("projektleiter", "praktiker", "fachkraft", "senior-sachbearbeiter"), "LinkedIn (Branchen-Gruppen)"),
)


def _suggest_channels(
    job: str,
    niche_key: str,
    flags: list[str],
) -> tuple[str, ...]:
    """Return up to ``MAX_CHANNELS`` marketing channels for a persona.

    Deterministic. First applies the job-keyword override (one primary
    channel for the persona's role), then fills with niche-default
    channels, then dedupes while preserving order. Signal flags add
    secondary channels: ``b2b`` ⇒ XING, ``einsteiger`` ⇒ Reddit, etc.
    Returns an empty tuple only when neither the job nor the niche
    yield any channel — which currently cannot happen because every
    niche has at least one default — but the helper stays defensive.
    """

    job_folded = _ascii_fold(job or "")
    picks: list[str] = []

    for keywords, channel in _JOB_CHANNEL_OVERRIDES:
        if any(keyword in job_folded for keyword in keywords):
            picks.append(channel)
            break

    # Flag-driven channels come before niche defaults so a flagged signal
    # (B2B, beginner) still surfaces even when niche defaults already fill
    # the MAX_CHANNELS cap.
    if "b2b" in flags and "XING (DACH-B2B)" not in picks:
        picks.append("XING (DACH-B2B)")
    if "einsteiger" in flags and "Reddit (Themen-Subreddits)" not in picks:
        picks.append("Reddit (Themen-Subreddits)")

    for channel in _NICHE_DEFAULT_CHANNELS.get(niche_key, _NICHE_DEFAULT_CHANNELS["allgemeines_sachbuch"]):
        if channel not in picks:
            picks.append(channel)

    return tuple(picks[:MAX_CHANNELS])


# --- Manual persona overrides ----------------------------------------------


# Section header that holds author-declared personas. Match singular and
# plural forms ("## Persona" / "## Personas") case-insensitive.
_PERSONA_SECTION_HEADER_RE = re.compile(
    r"^##\s+(?:personas?|buyer[\s-]+personas?)\b.*$",
    flags=re.I,
)
# Subheader inside the section — e.g. "### Persona 1: Label" or "### Label".
_PERSONA_BLOCK_HEADER_RE = re.compile(r"^###\s+(.+?)\s*$")
# One field line inside a persona block — "- Field: value" or "Field: value".
_PERSONA_FIELD_LINE_RE = re.compile(
    r"^[\-\*\s>]*([\wäöüß\s/]+?)\s*[:=]\s*(.+?)\s*$",
    flags=re.I,
)
_NEXT_PERSONA_SECTION_RE = re.compile(r"^##\s+", flags=re.I)

# Field-name aliases (ascii-folded lowercase keys) → canonical
# ``BuyerPersona`` field name.
_PERSONA_FIELD_ALIASES: dict[str, str] = {
    "alter": "age_range",
    "altersband": "age_range",
    "age": "age_range",
    "age range": "age_range",
    "job": "job",
    "rolle": "job",
    "role": "job",
    "beruf": "job",
    "job/rolle": "job",
    "job / rolle": "job",
    "problem": "problem",
    "pain": "problem",
    "schmerz": "problem",
    "kaufmotiv": "buying_motive",
    "motiv": "buying_motive",
    "motive": "buying_motive",
    "buying motive": "buying_motive",
    "buying_motive": "buying_motive",
    "suchanfrage": "anchor_quote",
    "suche": "anchor_quote",
    "anchor": "anchor_quote",
    "anchor_quote": "anchor_quote",
    "query": "anchor_quote",
    "search": "anchor_quote",
    "moegliche suchanfrage": "anchor_quote",
    "mögliche suchanfrage": "anchor_quote",
}


def _normalize_field_key(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    cleaned = (
        cleaned.replace("ä", "ae").replace("ö", "oe")
        .replace("ü", "ue").replace("ß", "ss")
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -*\t")
    return _PERSONA_FIELD_ALIASES.get(cleaned)


def _parse_persona_block_label(header_text: str) -> str:
    """Strip leading 'Persona N: ' / 'Persona N - ' from the subheader."""

    cleaned = header_text.strip()
    # Match patterns like "Persona 1", "Persona 1:", "Persona 1 - " and
    # remove the leading numbered label so what remains is the author's
    # actual persona name.
    match = re.match(
        r"^persona\s*\d+\s*[:\-–]?\s*(.*)$",
        cleaned,
        flags=re.I,
    )
    if match and match.group(1):
        return match.group(1).strip()
    return cleaned


def extract_persona_overrides(project: BookProject) -> list[dict[str, str]]:
    """Return author-declared persona overrides from project metadata.

    Reads every ``.md`` / ``.txt`` file in
    ``project.metadata_files + project.notes_files`` and scrapes a
    ``## Personas`` section. Each persona is a subblock starting with
    ``### Persona N: <label>`` (or just ``### <label>``) followed by
    field lines like ``- Alter: 30-45``, ``Job: CFO``, ``Problem: …``,
    ``Kaufmotiv: …``, ``Suchanfrage: …``.

    The parser is forgiving: unknown field keys are silently ignored
    (author may add their own notes inside the block), missing fields
    fall back to defaults at build time (see ``build_persona_report``),
    and personas without a usable label/job/problem are skipped.

    Returns at most ``MAX_PERSONAS`` overrides. Empty list when no
    override section exists — overrides only kick in when the author
    explicitly declares them.
    """

    sources: list[Any] = list(getattr(project, "metadata_files", []) or [])
    sources.extend(getattr(project, "notes_files", []) or [])
    overrides: list[dict[str, str]] = []
    for path in sources:
        try:
            if not path.exists() or path.suffix.lower() not in {".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        idx = 0
        while idx < len(lines):
            line = lines[idx].rstrip()
            if _PERSONA_SECTION_HEADER_RE.match(line):
                idx += 1
                current: dict[str, str] | None = None
                while idx < len(lines):
                    body = lines[idx].rstrip()
                    if _NEXT_PERSONA_SECTION_RE.match(body) and not _PERSONA_SECTION_HEADER_RE.match(body):
                        break
                    block_match = _PERSONA_BLOCK_HEADER_RE.match(body)
                    if block_match:
                        if current is not None:
                            overrides.append(current)
                        label = _parse_persona_block_label(block_match.group(1))
                        current = {"label": label} if label else {}
                        idx += 1
                        continue
                    if current is None:
                        idx += 1
                        continue
                    field_match = _PERSONA_FIELD_LINE_RE.match(body)
                    if field_match:
                        key = _normalize_field_key(field_match.group(1))
                        if key:
                            value = field_match.group(2).strip(" \t-_*'\"")
                            if value and key not in current:
                                current[key] = value
                    idx += 1
                if current is not None:
                    overrides.append(current)
                continue
            idx += 1
    # Drop personas that carry nothing actionable; cap at MAX_PERSONAS.
    actionable = [
        persona
        for persona in overrides
        if persona.get("label") or persona.get("problem") or persona.get("job")
    ]
    return actionable[:MAX_PERSONAS]


def _toc_anchors(chapter_titles: Iterable[str] | None) -> list[str]:
    """Lower-cased, deduplicated significant words from chapter titles."""

    if not chapter_titles:
        return []
    words: list[str] = []
    seen: set[str] = set()
    for title in chapter_titles:
        for raw in re.findall(r"[\wÄÖÜäöüß-]{5,}", title or "", flags=re.UNICODE):
            token = raw.lower()
            if token in seen:
                continue
            seen.add(token)
            words.append(token)
            if len(words) >= 8:
                return words
    return words


# --- Public API ------------------------------------------------------------


def build_persona_report(
    project: BookProject,
    chapter_titles: Iterable[str] | None = None,
) -> PersonaReport:
    """Produce a deterministic buyer-persona report for a book."""

    niche_key, niche_confidence = detect_niche(project)
    niche_label = NICHE_LABELS.get(niche_key, NICHE_LABELS["allgemeines_sachbuch"])
    audience = _normalize_audience(_extract_audience(project))
    subject = _extract_subject(project) or "Sachbuch"

    folded = _ascii_fold(_joined_metadata(project))
    flags = _detect_signal_flags(folded)

    anchors = extract_anchor_keywords(project, limit=6)
    toc_anchors = _toc_anchors(chapter_titles)
    combined_anchors: list[str] = []
    for word in list(anchors) + toc_anchors:
        if word not in combined_anchors:
            combined_anchors.append(word)
        if len(combined_anchors) >= 8:
            break

    specs = _NICHE_PERSONAS.get(niche_key) or _NICHE_PERSONAS["allgemeines_sachbuch"]
    overrides = extract_persona_overrides(project)
    personas: list[BuyerPersona] = []
    if overrides:
        flags = flags + ["persona_override"]
        # Author-supplied overrides replace the niche baseline entirely.
        # Each override may carry only a subset of fields; missing fields
        # fall back to (a) the matching baseline persona by position,
        # then (b) safe defaults so the report stays renderable.
        for idx, override in enumerate(overrides[:MAX_PERSONAS]):
            baseline = specs[idx] if idx < len(specs) else specs[0]
            base_label, base_age, base_job, base_problem, base_motive = baseline
            label = override.get("label") or base_label
            age = override.get("age_range") or base_age
            job = override.get("job") or base_job
            problem_text = override.get("problem") or _refine_problem(
                base_problem, combined_anchors, flags
            )
            motive_text = override.get("buying_motive") or _refine_motive(
                base_motive, subject, flags
            )
            quote = override.get("anchor_quote") or _format_anchor_quote(
                audience, subject, combined_anchors
            )
            channels = _suggest_channels(job, niche_key, flags)
            personas.append(
                BuyerPersona(
                    label=label[:MAX_QUOTE_CHARS],
                    age_range=age,
                    job=job,
                    problem=problem_text[:MAX_PROBLEM_CHARS],
                    buying_motive=motive_text[:MAX_MOTIVE_CHARS],
                    anchor_quote=quote[:MAX_QUOTE_CHARS],
                    channels=channels,
                )
            )
    else:
        for label, age, job, base_problem, base_motive in specs[:MAX_PERSONAS]:
            problem = _refine_problem(base_problem, combined_anchors, flags)
            motive = _refine_motive(base_motive, subject, flags)
            quote = _format_anchor_quote(audience, subject, combined_anchors)
            channels = _suggest_channels(job, niche_key, flags)
            personas.append(
                BuyerPersona(
                    label=label,
                    age_range=age,
                    job=job,
                    problem=problem,
                    buying_motive=motive,
                    anchor_quote=quote,
                    channels=channels,
                )
            )

    return PersonaReport(
        niche_key=niche_key,
        niche_label=niche_label,
        niche_confidence=niche_confidence,
        audience=audience,
        subject=subject,
        personas=personas,
        anchors=combined_anchors,
        signal_flags=flags,
    )


def render_persona_report_markdown(project: BookProject, report: PersonaReport) -> str:
    """Format the persona report as KDP-friendly Markdown."""

    title = project.title or project.project_id
    lines: list[str] = [
        "# Leser-Personas",
        "",
        f"Projekt: `{project.project_id}`",
        f"Titel: {title}",
        f"Nische: **{report.niche_label}** (Konfidenz: {report.niche_confidence}/100)",
        f"Zielgruppe (Metadaten): {report.audience}",
        f"Thema: {report.subject}",
        "",
        "## Ziel",
        "",
        "Diese drei Personas sind die realistischsten Käufer auf Amazon für dieses Buch.",
        "Schreibe Beschreibung, Bullets und A+-Content so, dass mindestens eine dieser Personas",
        "sich in den ersten drei Zeilen wiedererkennt.",
        "",
    ]

    for idx, persona in enumerate(report.personas, start=1):
        lines.extend([
            f"## Persona {idx} — {persona.label}",
            "",
            f"- **Alter:** {persona.age_range}",
            f"- **Job / Rolle:** {persona.job}",
            f"- **Problem:** {persona.problem}",
            f"- **Kaufmotiv:** {persona.buying_motive}",
            f"- **Mögliche Suchanfrage:** _{persona.anchor_quote}_",
        ])
        if persona.channels:
            lines.append(
                f"- **Marketing-Kanäle:** {', '.join(persona.channels)}"
            )
        lines.append("")

    if report.signal_flags:
        lines.extend([
            "## Erkannte Signale",
            "",
            ", ".join(report.signal_flags),
            "",
        ])
    if report.anchors:
        lines.extend([
            "## Anker-Begriffe für die Beschreibung",
            "",
            ", ".join(report.anchors),
            "",
        ])
    return "\n".join(lines)


def render_persona_brief_section(report: PersonaReport) -> str:
    """Compact insertion block for ``amazon_research_brief.md``."""

    if not report.personas:
        return ""
    lines: list[str] = [
        "## Leser-Personas (3 wahrscheinlichste Käufer)",
        "",
        "Schreibe Beschreibung und Bullets so, dass mindestens eine dieser Personas sich",
        "in den ersten drei Zeilen wiedererkennt. Details im Report `buyer_personas.md`.",
        "",
    ]
    for idx, persona in enumerate(report.personas, start=1):
        lines.append(
            f"{idx}. **{persona.label}** ({persona.age_range}) — {persona.job}. "
            f"Problem: {persona.problem} Kaufmotiv: {persona.buying_motive}"
        )
    lines.append("")
    return "\n".join(lines)
