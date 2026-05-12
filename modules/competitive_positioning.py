"""Competitive Positioning Report — what makes this book unique.

Generates a heuristic positioning analysis for a German nonfiction KDP
book based on title + subtitle + Amazon description:

* detects the most likely niche (KI, Finance, Sales, Productivity, …),
* names 3-4 typical competitor archetypes in that niche with their
  known weaknesses,
* derives the 3 strongest *differentiation angles* the book already
  exposes through its metadata (proof, voice, audience focus, …),
* flags *collision risks* where the book would be hard to distinguish
  from generic competitors,
* synthesises a single-sentence positioning pitch the author can
  copy-paste into the Amazon description.

Pure-Python and deterministic so the report runs in QA mode without
an LLM API key and stays comparable across rounds. The output is also
designed to be injected as a dedicated section into the LLM-based
``publisher_board_review`` to give that pass a concrete starting
point instead of recreating the niche analysis from scratch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from modules.discovery import BookProject
from modules.rewrites import _extract_audience, _extract_subject, extract_anchor_keywords

# --- Detection vocabularies -------------------------------------------------

NicheKey = str

NICHE_LABELS: dict[NicheKey, str] = {
    "ki_und_ai": "KI / Künstliche Intelligenz",
    "finanzen_und_cfo": "Finanzen / CFO / Controlling",
    "vertrieb_und_marketing": "Vertrieb / Marketing",
    "produktivitaet_fokus": "Produktivität / Fokus",
    "fuehrung_team": "Führung / Team",
    "selbststaendigkeit": "Selbständigkeit / Gründer",
    "immobilien_einkommen": "Immobilien / Nebeneinkommen",
    "mindset_gesundheit": "Mindset / Gesundheit",
    "allgemeines_sachbuch": "Allgemeines Sachbuch",
}

# Substring patterns (already lower-cased, ASCII-folded). Order matters
# only as documentation — the scoring below counts hits per niche.
NICHE_TERMS: dict[NicheKey, tuple[str, ...]] = {
    "ki_und_ai": (
        "ki ", "ki,", "ki-", "k.i.", " ai ", "ai,", "ai-",
        "kuenstliche intelligenz", "kunstliche intelligenz",
        "chatgpt", "claude", " llm ", "agent", "automatisier",
        "machine learning", "deep learning", "prompt",
    ),
    "finanzen_und_cfo": (
        "finanz", "geld", "cfo", "controlling", "buchhaltung",
        "kapital", "investier", "vermoegen", "vermogen",
        "bilanz", "cashflow", "liquiditaet", "liquiditat",
        "steuer", "bank",
    ),
    "vertrieb_und_marketing": (
        "vertrieb", "verkauf", "sales", "marketing", "kunden",
        "akquise", "leadgenerierung", "umsatz", "conversion",
        "branding", "positionierung",
    ),
    "produktivitaet_fokus": (
        "produktivit", "fokus", "zeitmanagement", "deep work",
        "konzentration", "routine", "gewohnheit",
    ),
    "fuehrung_team": (
        "fuehrung", "fuhrung", "leadership", "manager", "team",
        "mitarbeit", "delegier", "ceo", "geschaeftsfuehr",
        "geschaftsfuhr",
    ),
    "selbststaendigkeit": (
        "selbststaendig", "selbststandig", "freelanc",
        "solopreneur", "gruender", "grunder", "startup",
        "unternehm",
    ),
    "immobilien_einkommen": (
        "immobil", "miete", "passives einkommen",
        "nebeneinkommen", "fuenfzig euro", "funfzig euro",
        "dividende",
    ),
    "mindset_gesundheit": (
        "mindset", "achtsamkeit", "stress", "schlaf", "gesundheit",
        "psyche", "burnout", "resilienz", "meditation",
    ),
}

# Competitor archetypes per niche. Three to four per niche so the
# author can see exactly which kind of book they are competing with.
CompetitorArchetypeSpec = tuple[str, str, str]

NICHE_ARCHETYPES: dict[NicheKey, tuple[CompetitorArchetypeSpec, ...]] = {
    "ki_und_ai": (
        ("Hype-KI-Bestseller", "Allgemeine Begeisterung für KI ohne konkrete Umsetzung.", "Liefert keine wiederholbare Methode oder Prozessanleitung."),
        ("Akademisches KI-Buch", "Theoretische Grundlagen und Modellarchitekturen.", "Hat keinen Anwendungsbezug für Praktiker oder Operatoren."),
        ("Berater-KI-Promotion", "Positioniert KI-Beratung als Endkunden-Service.", "Verkauft am Ende Beratung statt einer umsetzbaren Methode."),
        ("Tool-Tutorial-Buch", "Listet Tools und Prompts auf.", "Wird durch Tool-Updates schnell veraltet und liefert keinen strategischen Rahmen."),
    ),
    "finanzen_und_cfo": (
        ("Klassisches Lehrbuch", "Umfassend, aber theoretisch.", "Keine direkten Praxis-Checklisten oder Erfahrungswerte aus echten Bilanzen."),
        ("Motivationsbuch Reichtum", "Erfolgsgeschichten und Mindset-Tipps.", "Liefert kein Steuerungswerkzeug für Liquidität und Kapital."),
        ("Steuer-Ratgeber", "Fokus auf Tax-Tricks und Abschreibungen.", "Decken nur einen Teilbereich der Finanzführung ab."),
        ("Berater-Positionierungsbuch", "Schreibt CFO-Beratung an.", "Endet in einer Service-Anfrage statt Methode für den Leser."),
    ),
    "vertrieb_und_marketing": (
        ("Funnel-Hype-Buch", "Aggressive Online-Marketing-Methoden.", "Funktioniert nicht in B2B / klassischen Branchen, kurze Halbwertszeit."),
        ("Klassiker-Verkaufsbuch", "Standardrhetorik aus den 90ern.", "Wirkt heute manipulativ und passt nicht zu skeptischen Käufern."),
        ("Akademisches Marketingbuch", "Strategie-Frameworks ohne Branchenbezug.", "Schwer in tägliche Umsetzung zu übersetzen."),
        ("Influencer-Coaching-Buch", "Verkauft Coachings über das Buch.", "Endet im Upsell, liefert keine vollständige Methode im Buch selbst."),
    ),
    "produktivitaet_fokus": (
        ("Allen / Newport-Klone", "Wiederholt bekannte Klassiker.", "Kein neuer Anwendungsfall, kaum Differenzierung."),
        ("Habit-Stacking-Buch", "Fokus auf Mikro-Gewohnheiten.", "Greift nicht für komplexere Operator- oder CFO-Arbeit."),
        ("App- und Tool-Listenbuch", "Listet Tools und Setups auf.", "Veraltet schnell und ignoriert den Arbeitskontext."),
    ),
    "fuehrung_team": (
        ("Konzern-Manager-Memoiren", "Allgemeine Führungslektionen aus großen Firmen.", "Übersetzt schlecht auf KMU-/Operator-Realität."),
        ("Akademisches Leadership-Buch", "Modelle und Studien.", "Keine konkreten Sätze, Mails oder Meetings für den Alltag."),
        ("New-Work-Hype-Buch", "Verspricht radikale Kultur-Wendung.", "Funktioniert nicht in operativen oder regulierten Umfeldern."),
        ("Coaching-Promotion-Buch", "Bewirbt Führungs-Coachings.", "Endet im Programm-Verkauf statt im fertigen Werkzeugkasten."),
    ),
    "selbststaendigkeit": (
        ("Startup-Mythos-Buch", "Erzählt Unicorn-Geschichten.", "Übersetzt schlecht auf 1-Personen-Unternehmen oder klassische Branchen."),
        ("Side-Hustle-Sammlung", "Listet Geschäftsideen auf.", "Liefert keine ehrliche Operations- oder Finanzsicht."),
        ("Berater-Programm-Buch", "Verkauft ein Mentoring-Programm.", "Buch ist nur Lead-Magnet, Methode steht nicht vollständig drin."),
    ),
    "immobilien_einkommen": (
        ("Reich-mit-Immobilien-Bestseller", "Verspricht Reichtum durch Immobilien.", "Unterschätzt Cashflow-, Steuer- und Zins-Realität."),
        ("Passives-Einkommen-Buch", "Listet 'passive' Einkommensquellen.", "Verschweigt operative Arbeit und Risiko."),
        ("Akademisches Investitions-Buch", "Theoretische Anlageklassen.", "Keine konkreten Schritte oder Excel-Vorlagen."),
    ),
    "mindset_gesundheit": (
        ("Motivationsbestseller", "Allgemeine Selbstoptimierung.", "Keine konkreten Methoden für berufstätige Erwachsene."),
        ("Therapie-Ratgeber", "Klinische Selbsthilfe.", "Schwer für Operator/CFO-Leser, schreckt unsensible Käufer ab."),
        ("Esoterische Mindset-Bücher", "Manifestation und Energie.", "Verliert skeptische Leser sofort, Reviewrisiko hoch."),
    ),
    "allgemeines_sachbuch": (
        ("Standard-Ratgeberbuch", "Allgemeine 'Anleitung für ein besseres Leben'.", "Keine spitze Zielgruppe oder spezifische Methode."),
        ("Lehrbuch / Wissenschaftsbuch", "Vollständig, aber theoretisch.", "Keine direkte Umsetzbarkeit im Alltag."),
        ("Motivations- und Lifestyle-Buch", "Allgemeine Inspiration.", "Kein konkretes Versprechen, hohes Reviewrisiko bei skeptischen Käufern."),
    ),
}

# Differentiation-angle catalogue. Each angle defines:
#   key   — internal identifier
#   claim — the positioning claim, ready for the report
#   probe — function(project, joined_text) -> int strength (0-100)
# Strength scores aggregate signals from title/subtitle/description.

PROOF_PATTERNS: re.Pattern[str] = re.compile(
    r"\b(\d+\s*(?:euro|€|stunden|tage|wochen|monate|jahre|%|prozent|"
    r"seiten|kunden|projekte|fehler|minuten|sekunden|punkte)"
    r"|\d{2,}\+? jahre|aus \d+ jahren|in \d+ projekten)",
    flags=re.IGNORECASE,
)

ANTI_HYPE_PATTERNS: re.Pattern[str] = re.compile(
    r"\b(ohne hype|kein hype|anti[- ]hype|ehrlich|skeptisch|"
    r"ohne floskel|keine motivation|nuechtern|nuchtern)\b",
    flags=re.IGNORECASE,
)

OPERATOR_VOICE_PATTERNS: re.Pattern[str] = re.compile(
    r"\b(operator|cfo|ceo|geschaeftsfuehr|geschaftsfuhr|"
    r"praktiker|aus der praxis|feldnotiz|feldbericht|"
    r"erfahrungsbericht|ich habe|wir haben|in meiner praxis)\b",
    flags=re.IGNORECASE,
)

METHOD_PATTERNS: re.Pattern[str] = re.compile(
    r"\b(methode|framework|playbook|prinzip|"
    r"schritt[- ]?fuer[- ]?schritt|schritt[- ]?fur[- ]?schritt|"
    r"checkliste|vorlage|leitfaden|system|algorithmus)\b",
    flags=re.IGNORECASE,
)

ANTI_CONSULTANT_PATTERNS: re.Pattern[str] = re.compile(
    r"\b(kein berater|ohne berater|nicht beraten|"
    r"keine beratungs|kein consulting|"
    r"ohne consulting|"
    r"jenseits der theorie)\b",
    flags=re.IGNORECASE,
)

AUDIENCE_FOR_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:fuer|fur|für)\s+[A-Za-zÄÖÜäöüß][\w\s-]+",
    flags=re.IGNORECASE,
)

HYPE_TITLE_PATTERNS: re.Pattern[str] = re.compile(
    r"\b(geheimnis|geheimformel|reich|millionaer|millionar|"
    r"erfolg|revolution|revolutionaer|revolutionar|"
    r"bahnbrechend|durchbruch|unglaublich)\b",
    flags=re.IGNORECASE,
)

# --- Data records -----------------------------------------------------------


@dataclass(frozen=True)
class CompetitorArchetype:
    """A representative competitor type the book is up against."""

    name: str
    why_it_competes: str
    typical_weakness: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "why_it_competes": self.why_it_competes,
            "typical_weakness": self.typical_weakness,
        }


@dataclass(frozen=True)
class DifferentiationAngle:
    """One concrete angle that sets the book apart from competitors."""

    key: str
    claim: str
    evidence: str
    strength: int  # 0-100

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "claim": self.claim,
            "evidence": self.evidence,
            "strength": self.strength,
        }


@dataclass(frozen=True)
class PositioningReport:
    """Aggregated competitive-positioning analysis for a single book."""

    niche_key: str
    niche_label: str
    niche_confidence: int
    audience: str
    subject: str
    archetypes: list[CompetitorArchetype]
    unique_angles: list[DifferentiationAngle]
    collision_risks: list[str]
    positioning_pitch: str
    anchors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "niche_key": self.niche_key,
            "niche_label": self.niche_label,
            "niche_confidence": self.niche_confidence,
            "audience": self.audience,
            "subject": self.subject,
            "archetypes": [a.to_json() for a in self.archetypes],
            "unique_angles": [a.to_json() for a in self.unique_angles],
            "collision_risks": list(self.collision_risks),
            "positioning_pitch": self.positioning_pitch,
            "anchors": list(self.anchors),
        }


# --- Helpers ---------------------------------------------------------------


def _ascii_fold(text: str) -> str:
    return (
        text.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def _joined_metadata(project: BookProject) -> str:
    parts = [project.title or "", project.subtitle or "", project.amazon_description or ""]
    return " ".join(part for part in parts if part)


# Niche-key + label aliases the author may write in metadata. Stored
# ascii-folded lowercase. The map must include both the canonical key
# (so writing the technical key works) and the prominent display labels.
_NICHE_KEY_ALIASES: dict[str, NicheKey] = {
    # ki_und_ai
    "ki_und_ai": "ki_und_ai",
    "ki": "ki_und_ai",
    "ai": "ki_und_ai",
    "ki/ai": "ki_und_ai",
    "ki / ai": "ki_und_ai",
    "ki und ai": "ki_und_ai",
    "kuenstliche intelligenz": "ki_und_ai",
    "kuenstliche intelligenz / ki": "ki_und_ai",
    # finanzen_und_cfo
    "finanzen_und_cfo": "finanzen_und_cfo",
    "finanzen": "finanzen_und_cfo",
    "cfo": "finanzen_und_cfo",
    "finanzen / cfo": "finanzen_und_cfo",
    "finanzen / cfo / controlling": "finanzen_und_cfo",
    "controlling": "finanzen_und_cfo",
    # vertrieb_und_marketing
    "vertrieb_und_marketing": "vertrieb_und_marketing",
    "vertrieb": "vertrieb_und_marketing",
    "marketing": "vertrieb_und_marketing",
    "vertrieb / marketing": "vertrieb_und_marketing",
    "sales": "vertrieb_und_marketing",
    # produktivitaet_fokus
    "produktivitaet_fokus": "produktivitaet_fokus",
    "produktivitaet": "produktivitaet_fokus",
    "produktivitaet / fokus": "produktivitaet_fokus",
    "fokus": "produktivitaet_fokus",
    # fuehrung_team
    "fuehrung_team": "fuehrung_team",
    "fuehrung": "fuehrung_team",
    "fuehrung / team": "fuehrung_team",
    "leadership": "fuehrung_team",
    "team": "fuehrung_team",
    # selbststaendigkeit
    "selbststaendigkeit": "selbststaendigkeit",
    "selbststaendigkeit / gruender": "selbststaendigkeit",
    "gruender": "selbststaendigkeit",
    "selbststaendig": "selbststaendigkeit",
    # immobilien_einkommen
    "immobilien_einkommen": "immobilien_einkommen",
    "immobilien": "immobilien_einkommen",
    "immobilien / nebeneinkommen": "immobilien_einkommen",
    "nebeneinkommen": "immobilien_einkommen",
    # mindset_gesundheit
    "mindset_gesundheit": "mindset_gesundheit",
    "mindset": "mindset_gesundheit",
    "mindset / gesundheit": "mindset_gesundheit",
    "gesundheit": "mindset_gesundheit",
    # allgemeines_sachbuch
    "allgemeines_sachbuch": "allgemeines_sachbuch",
    "allgemein": "allgemeines_sachbuch",
    "allgemeines sachbuch": "allgemeines_sachbuch",
    "sachbuch": "allgemeines_sachbuch",
}

# Section header that holds per-niche vocabulary additions.
_NICHE_VOCAB_HEADER_RE = re.compile(
    r"^##\s*(?:nischen[\s-]+begriffe|nischen[\s-]+vokabular|niche[\s-]+terms|niche[\s-]+vocab(?:ulary)?)\b.*$",
    flags=re.I,
)
# A line like "KI: agentic, llm, ragstack" or "Finanzen: ebit, cogs".
_NICHE_VOCAB_INLINE_RE = re.compile(
    r"^\s*[\-\*\s>]*([\w\säöüß/]+?)\s*:\s*(.+?)\s*$",
    flags=re.I,
)
# Subheader inside the section — e.g. "### KI" or "### Finanzen / CFO".
_NICHE_VOCAB_SUBHEADER_RE = re.compile(r"^###\s+(.+?)\s*$")
_NEXT_SECTION_RE_NICHE = re.compile(r"^##\s+", flags=re.I)


def _normalize_niche_key_alias(raw: str) -> NicheKey | None:
    cleaned = _ascii_fold(raw).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -*\t")
    return _NICHE_KEY_ALIASES.get(cleaned)


def _split_vocab_value(value: str) -> list[str]:
    """Split a "term1, term2, term3" string into normalized tokens."""

    tokens: list[str] = []
    for raw in re.split(r"[,;]+", value):
        token = _ascii_fold(raw).strip()
        # Strip enclosing quotes/backticks/brackets and surrounding markers
        token = re.sub(r"^[\-\*`'\"<\[]+|[`'\">\]]+$", "", token).strip()
        if not token or len(token) < 2:
            continue
        tokens.append(token)
    return tokens


def extract_niche_vocab_overrides(project: BookProject) -> dict[NicheKey, tuple[str, ...]]:
    """Return author-supplied vocabulary additions per niche.

    Reads every ``.md`` / ``.txt`` file in
    ``project.metadata_files + project.notes_files`` and scrapes a
    ``## Nischen-Begriffe`` (or ``## Niche-Terms``) section. Two
    formats are accepted inside the section:

    1. **Inline** — ``KI: agentic, llm, ragstack`` (one niche per line)
    2. **Subblock** — ``### KI`` followed by ``- agentic`` / ``- llm``

    Niche names are matched via :data:`_NICHE_KEY_ALIASES` so authors
    can write either the technical key (``ki_und_ai``) or the display
    label (``KI``, ``Finanzen / CFO``). Tokens are ascii-folded so the
    detection sees them with the same shape as :data:`NICHE_TERMS`.

    Returns an empty dict when no override section exists — overrides
    only matter when the heuristic's vocabulary misses a domain-specific
    term and the author wants the detector to catch it.
    """

    sources: list[Any] = list(getattr(project, "metadata_files", []) or [])
    sources.extend(getattr(project, "notes_files", []) or [])
    additions: dict[NicheKey, list[str]] = {}

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
            if _NICHE_VOCAB_HEADER_RE.match(line):
                idx += 1
                current_key: NicheKey | None = None
                while idx < len(lines):
                    body = lines[idx].rstrip()
                    if _NEXT_SECTION_RE_NICHE.match(body) and not _NICHE_VOCAB_HEADER_RE.match(body):
                        break
                    sub_match = _NICHE_VOCAB_SUBHEADER_RE.match(body)
                    if sub_match:
                        current_key = _normalize_niche_key_alias(sub_match.group(1))
                        idx += 1
                        continue
                    inline_match = _NICHE_VOCAB_INLINE_RE.match(body)
                    if inline_match:
                        # "key: token, token" — preferred form, sets the
                        # current bucket explicitly and adds the tokens.
                        key = _normalize_niche_key_alias(inline_match.group(1))
                        if key:
                            tokens = _split_vocab_value(inline_match.group(2))
                            if tokens:
                                additions.setdefault(key, []).extend(tokens)
                            idx += 1
                            continue
                    # Bare list line under a subheader — collect into the
                    # currently-active niche bucket.
                    if current_key and body.strip():
                        bare = re.sub(r"^[\-\*\s>•]+", "", body).strip()
                        if bare:
                            tokens = _split_vocab_value(bare)
                            if tokens:
                                additions.setdefault(current_key, []).extend(tokens)
                    idx += 1
                continue
            idx += 1

    # Dedup per niche while preserving first-occurrence order.
    result: dict[NicheKey, tuple[str, ...]] = {}
    for key, tokens in additions.items():
        seen: set[str] = set()
        ordered: list[str] = []
        for tok in tokens:
            if tok in seen:
                continue
            seen.add(tok)
            ordered.append(tok)
        if ordered:
            result[key] = tuple(ordered)
    return result


def detect_niche(project: BookProject) -> tuple[NicheKey, int]:
    """Return (niche_key, confidence_0_100).

    Confidence is the share of hits attributed to the winning niche.
    Falls back to ``allgemeines_sachbuch`` with zero confidence when no
    niche terms hit at all.

    Honors author-declared vocabulary additions via
    :func:`extract_niche_vocab_overrides` — terms in a project's
    ``## Nischen-Begriffe`` section are scored alongside the built-in
    :data:`NICHE_TERMS` so domain-specific acronyms (e.g. "ebit",
    "ragstack", "icp") tilt the detection toward the right niche.
    """

    haystack = _ascii_fold(_joined_metadata(project))
    if not haystack.strip():
        return "allgemeines_sachbuch", 0

    overrides = extract_niche_vocab_overrides(project)

    counts: dict[NicheKey, int] = {}
    for niche_key, terms in NICHE_TERMS.items():
        extra_terms = overrides.get(niche_key, ())
        count = sum(1 for term in terms if term in haystack)
        count += sum(1 for term in extra_terms if term in haystack)
        if count > 0:
            counts[niche_key] = count

    if not counts:
        return "allgemeines_sachbuch", 0

    total = sum(counts.values())
    top_key = max(counts, key=lambda k: (counts[k], -list(NICHE_TERMS).index(k)))
    confidence = max(1, min(100, round(100 * counts[top_key] / total)))
    return top_key, confidence


def _archetypes_for(niche_key: NicheKey) -> list[CompetitorArchetype]:
    specs = NICHE_ARCHETYPES.get(niche_key) or NICHE_ARCHETYPES["allgemeines_sachbuch"]
    return [
        CompetitorArchetype(name=name, why_it_competes=why, typical_weakness=weakness)
        for name, why, weakness in specs
    ]


def _score_proof(joined_text: str) -> int:
    matches = PROOF_PATTERNS.findall(joined_text)
    if not matches:
        return 0
    return max(40, min(100, 40 + 20 * len(matches)))


def _score_anti_hype(joined_text: str) -> int:
    matches = ANTI_HYPE_PATTERNS.findall(joined_text)
    if not matches:
        return 0
    return max(50, min(100, 50 + 25 * len(matches)))


def _score_operator_voice(joined_text: str) -> int:
    matches = OPERATOR_VOICE_PATTERNS.findall(joined_text)
    if not matches:
        return 0
    return max(45, min(100, 45 + 18 * len(matches)))


def _score_method(joined_text: str) -> int:
    matches = METHOD_PATTERNS.findall(joined_text)
    if not matches:
        return 0
    return max(45, min(100, 45 + 18 * len(matches)))


def _score_anti_consultant(joined_text: str) -> int:
    matches = ANTI_CONSULTANT_PATTERNS.findall(joined_text)
    if not matches:
        return 0
    return max(60, min(100, 60 + 20 * len(matches)))


def _score_audience_focus(project: BookProject) -> int:
    subtitle = project.subtitle or ""
    description = project.amazon_description or ""
    if AUDIENCE_FOR_PATTERN.search(subtitle):
        return 80
    if AUDIENCE_FOR_PATTERN.search(description[:400]):
        return 55
    return 0


def _build_angles(project: BookProject, joined_text: str, audience: str) -> list[DifferentiationAngle]:
    """Return all angles whose strength > 0, sorted by strength desc."""

    candidates: list[DifferentiationAngle] = []

    score = _score_proof(joined_text)
    if score:
        candidates.append(
            DifferentiationAngle(
                key="zahlen_beweis",
                claim="Beweisführung mit Zahlen statt Behauptungen.",
                evidence="Titel, Untertitel oder Beschreibung enthalten konkrete Zahlen, Zeiträume oder Mengenangaben.",
                strength=score,
            )
        )

    score = _score_anti_hype(joined_text)
    if score:
        candidates.append(
            DifferentiationAngle(
                key="anti_hype",
                claim="Anti-Hype-Positionierung gegen Motivations- und Versprechen-Bücher.",
                evidence="Metadaten signalisieren ehrliche, skeptische, nüchterne Sprache.",
                strength=score,
            )
        )

    score = _score_operator_voice(joined_text)
    if score:
        candidates.append(
            DifferentiationAngle(
                key="operator_stimme",
                claim="Operator-/CFO-Praxisstimme statt Berater- oder Theorie-Perspektive.",
                evidence="Metadaten nennen Operator-, CFO- oder Feldnotiz-Begriffe.",
                strength=score,
            )
        )

    score = _score_method(joined_text)
    if score:
        candidates.append(
            DifferentiationAngle(
                key="methode_playbook",
                claim="Wiederholbare Methode oder Playbook statt narrativer Inspiration.",
                evidence="Metadaten kündigen Methode, Framework, Checkliste oder Schritt-für-Schritt-Vorgehen an.",
                strength=score,
            )
        )

    score = _score_anti_consultant(joined_text)
    if score:
        candidates.append(
            DifferentiationAngle(
                key="anti_berater",
                claim="Vollständige Methode im Buch statt Beratungs-Lead-Magnet.",
                evidence="Metadaten distanzieren das Buch explizit von Berater- oder Coaching-Programmen.",
                strength=score,
            )
        )

    score = _score_audience_focus(project)
    if score:
        candidates.append(
            DifferentiationAngle(
                key="spitze_zielgruppe",
                claim=f"Spitze Zielgruppe ({audience}) statt 'für alle'.",
                evidence="Untertitel oder Beschreibung nennen eine konkrete Berufs- oder Lebenslage.",
                strength=score,
            )
        )

    candidates.sort(key=lambda a: (-a.strength, a.key))
    return candidates


def _collision_risks(
    project: BookProject,
    joined_text: str,
    angles: list[DifferentiationAngle],
    niche_key: NicheKey,
) -> list[str]:
    risks: list[str] = []
    angle_keys = {angle.key for angle in angles}

    if "zahlen_beweis" not in angle_keys:
        risks.append(
            "Ohne sichtbare Zahlen oder Zeiträume kaum von Motivationsliteratur abgrenzbar — "
            "mindestens eine konkrete Zahl in die ersten 3 Description-Zeilen."
        )
    if "operator_stimme" not in angle_keys and "anti_hype" not in angle_keys:
        risks.append(
            "Stimme ist in den Metadaten nicht erkennbar — Leser kann nicht entscheiden, "
            "ob es ein Lehrbuch, ein Erfahrungsbericht oder ein Motivationsbuch ist."
        )
    if "spitze_zielgruppe" not in angle_keys:
        risks.append(
            "Keine konkrete Zielgruppe sichtbar — kollidiert direkt mit dem allgemeinen "
            "Ratgeber-Regal."
        )
    if "methode_playbook" not in angle_keys:
        risks.append(
            "Methoden-Versprechen fehlt — Käufer erkennt nicht, ob er etwas Wiederholbares "
            "oder eine Geschichte bekommt."
        )

    title = project.title or ""
    if HYPE_TITLE_PATTERNS.search(_ascii_fold(title)):
        risks.append(
            "Titel enthält Hype-Begriffe — das schwächt eine seriöse Positionierung und "
            "lockt Käufer mit falschen Erwartungen (hohes Review-Risiko)."
        )

    if niche_key == "ki_und_ai" and "zahlen_beweis" not in angle_keys:
        risks.append(
            "KI-Nische ist mit Hype-Büchern überflutet — ohne harte Zahlen oder Fallbeispiele "
            "verschwindet das Buch in der Masse."
        )
    if niche_key == "immobilien_einkommen" and "zahlen_beweis" not in angle_keys:
        risks.append(
            "Immobilien-/Einkommens-Nische lebt von Zahlen — ohne konkrete Beträge wirkt das "
            "Buch wie ein weiteres Reichtums-Versprechen."
        )

    return risks


def _build_pitch(
    subject: str,
    audience: str,
    angles: list[DifferentiationAngle],
    niche_label: str,
) -> str:
    """One-sentence positioning the author can paste into the description."""

    top_keys = [angle.key for angle in angles[:2]]
    descriptors: list[str] = []
    if "operator_stimme" in top_keys:
        descriptors.append("aus operativer Praxis")
    if "zahlen_beweis" in top_keys:
        descriptors.append("mit konkreten Zahlen")
    if "anti_hype" in top_keys and "ohne Hype" not in descriptors:
        descriptors.append("ohne Hype")
    if "methode_playbook" in top_keys:
        descriptors.append("als wiederholbare Methode")
    if not descriptors:
        descriptors.append("klar, konkret und ohne Floskeln")

    body = ", ".join(descriptors[:3])
    subject = subject or niche_label or "Sachbuch"
    return (
        f"Dieses Buch liefert {subject} für {audience} — {body}. "
        "Es ersetzt allgemeine Ratgeber und Motivationsliteratur durch ein umsetzbares "
        "Vorgehen, das ein Leser nach 60 Minuten anwenden kann."
    )


# --- Public API ------------------------------------------------------------


def build_positioning_report(project: BookProject) -> PositioningReport:
    """Produce the deterministic positioning report for a book."""

    joined = _joined_metadata(project)
    folded = _ascii_fold(joined)

    niche_key, niche_confidence = detect_niche(project)
    niche_label = NICHE_LABELS.get(niche_key, NICHE_LABELS["allgemeines_sachbuch"])
    audience = _extract_audience(project)
    subject = _extract_subject(project)

    angles = _build_angles(project, folded, audience)
    top_angles = angles[:3]
    if not top_angles:
        # Always return at least one angle so the report is never empty.
        top_angles = [
            DifferentiationAngle(
                key="kein_signal",
                claim="Kein klares Differenzierungssignal in den Metadaten erkennbar.",
                evidence="Titel, Untertitel und Beschreibung sind zu allgemein.",
                strength=0,
            )
        ]

    archetypes = _archetypes_for(niche_key)
    risks = _collision_risks(project, folded, angles, niche_key)
    pitch = _build_pitch(subject, audience, top_angles, niche_label)
    anchors = extract_anchor_keywords(project, limit=6)

    return PositioningReport(
        niche_key=niche_key,
        niche_label=niche_label,
        niche_confidence=niche_confidence,
        audience=audience,
        subject=subject,
        archetypes=archetypes,
        unique_angles=top_angles,
        collision_risks=risks,
        positioning_pitch=pitch,
        anchors=anchors,
    )


def render_positioning_markdown(project: BookProject, report: PositioningReport) -> str:
    """Format the positioning report as KDP-friendly Markdown."""

    title = project.title or project.project_id
    lines: list[str] = [
        "# Wettbewerbs-Positionierung",
        "",
        f"Projekt: `{project.project_id}`",
        f"Titel: {title}",
        f"Nische: **{report.niche_label}** (Konfidenz: {report.niche_confidence}/100)",
        f"Zielgruppe: {report.audience}",
        f"Thema: {report.subject}",
        "",
        "## Wettbewerber-Archetypen in dieser Nische",
        "",
    ]
    for archetype in report.archetypes:
        lines.append(f"- **{archetype.name}** — {archetype.why_it_competes} _Schwäche: {archetype.typical_weakness}_")

    lines.extend(["", "## Was macht dieses Buch einzigartig?", ""])
    for angle in report.unique_angles:
        lines.append(
            f"- **{angle.claim}** (Stärke: {angle.strength}/100)"
        )
        lines.append(f"  - Beleg: {angle.evidence}")

    lines.extend(["", "## Kollisions-Risiken", ""])
    if report.collision_risks:
        for risk in report.collision_risks:
            lines.append(f"- {risk}")
    else:
        lines.append("- Keine erkennbaren Überschneidungen mit generischen Wettbewerbern.")

    lines.extend([
        "",
        "## Positionierungs-Satz",
        "",
        report.positioning_pitch,
    ])
    return "\n".join(lines)
