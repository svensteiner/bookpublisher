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
from typing import Any, Callable, Iterable, Sequence

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

# Provenance label for keyword slots filled by the optional LLM long-tail
# pass. Downstream tooling reads ``source`` from kdp_keywords.json to tell
# manuscript-derived phrases apart from the deterministic template paths.
KDP_KEYWORD_SOURCE_LLM: str = "llm_longtail"

# Maximum number of the 7 KDP slots the LLM long-tail phrases may occupy.
# Capped below the total so the strongest deterministic subject/audience
# slots always survive — the author gets a mix of manuscript-derived
# long-tail phrases and the proven template paths, never 7 LLM guesses.
LLM_KEYWORDS_MAX_SLOTS: int = 5

# Maximum number of chapter titles forwarded to the LLM. Keeps the prompt
# cheap and forces the model to mine the strongest search intents instead
# of mirroring the whole table of contents.
LLM_KEYWORDS_MAX_CHAPTER_TITLES: int = 20

# A real long-tail search phrase carries at least two words. Single-word
# LLM output ("finanzen") is dropped — those head terms are already covered
# by the deterministic subject pipeline.
LLM_KEYWORDS_MIN_WORDS: int = 2

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

# Tokens too generic to count as a "real" category overlap. A keyword
# sharing only the word "buch" with the chosen category is not actually
# a conflict — Amazon's overlap rule applies to substantive subject
# tokens.
KEYWORD_CONFLICT_STOP_TOKENS: frozenset[str] = frozenset(
    {
        "buch", "ratgeber", "praxis", "anleitung", "schritt",
        "ohne", "fuer", "und", "der", "die", "das", "den", "des",
        "mit", "ein", "eine", "einer", "von", "ueber", "im", "auf",
        "leitfaden", "handbuch", "guide", "sachbuch",
    }
)

# Minimum number of substantive overlap tokens to call a keyword a
# conflict with a category. One real subject token (e.g. "finanzen") is
# enough — that's the slot Amazon will likely de-prioritize.
KEYWORD_CONFLICT_MIN_SHARED: int = 1

# Section headers in notes/metadata that declare the author's chosen KDP
# categories. Case-insensitive match. ``Kategorien`` covers both
# singular and plural forms via ``Kategorie(n)``.
_CATEGORY_HEADER_RE = re.compile(
    r"^##\s*(?:kdp[\s-]+)?(?:kategorie[n]?|categor(?:y|ies))\b.*$",
    flags=re.I,
)
_NEXT_SECTION_RE = re.compile(r"^##\s+", flags=re.I)
_LIST_PREFIX_RE = re.compile(r"^[\-\*•‣●\d\.\)\>\s]+")


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


@dataclass(frozen=True)
class KeywordConflict:
    """A keyword slot that overlaps with a declared KDP category.

    Amazon's targeting rule says a category-token already in your
    chosen KDP category gets reduced weight when repeated in a keyword
    slot. The conflict report surfaces these so the author can replace
    the slot with a long-tail phrase instead.
    """

    keyword_text: str
    category: str
    shared_tokens: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "keyword_text": self.keyword_text,
            "category": self.category,
            "shared_tokens": list(self.shared_tokens),
        }


def extract_kdp_categories(project: BookProject) -> list[str]:
    """Return author-declared KDP categories from project metadata.

    Reads every ``.md`` / ``.txt`` file in ``project.metadata_files +
    project.notes_files`` and scrapes any section whose header matches
    ``## Kategorie``, ``## Kategorien``, ``## KDP Kategorie``,
    ``## Category``, ``## Categories`` (case-insensitive). Returns one
    string per non-empty body line, with list markers (``-``, ``*``,
    ``1.``, ``>``) stripped.

    Empty result is normal — most books carry KDP categories in the
    KDP backend only and not in metadata. Callers should treat the
    empty list as "no conflict check possible" rather than "no
    conflicts found".
    """

    out: list[str] = []
    seen: set[str] = set()
    sources: list[Any] = list(getattr(project, "metadata_files", []) or [])
    sources.extend(getattr(project, "notes_files", []) or [])

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
            if _CATEGORY_HEADER_RE.match(line):
                idx += 1
                while idx < len(lines):
                    body = lines[idx].rstrip()
                    if _NEXT_SECTION_RE.match(body):
                        break
                    cleaned = _LIST_PREFIX_RE.sub("", body).strip()
                    if cleaned and cleaned not in seen:
                        seen.add(cleaned)
                        out.append(cleaned)
                    idx += 1
                continue
            idx += 1
    return out


def _keyword_token_set(text: str) -> set[str]:
    """Return the substantive token set of an already-normalized keyword."""
    return {tok for tok in text.split() if tok}


def _category_token_set(category: str) -> set[str]:
    """Return the substantive token set of a raw category string."""
    normalized = _normalize_phrase(category)
    return {tok for tok in normalized.split() if tok}


def find_keyword_conflicts(
    keywords: list[KDPKeyword],
    categories: list[str],
) -> list[KeywordConflict]:
    """Return one :class:`KeywordConflict` per keyword that overlaps a category.

    Overlap is counted only against *substantive* tokens — generic
    nonfiction filler ("buch", "ratgeber", "fuer", …) lives in
    :data:`KEYWORD_CONFLICT_STOP_TOKENS` and is excluded so the report
    does not surface false-positive conflicts.

    At most one conflict is reported per keyword (the first matching
    category in declaration order). Returns an empty list when either
    side is empty — callers should distinguish "no conflicts" from
    "no categories declared" via the input list.
    """

    if not keywords or not categories:
        return []
    out: list[KeywordConflict] = []
    for keyword in keywords:
        kw_tokens = _keyword_token_set(keyword.text)
        if not kw_tokens:
            continue
        for category in categories:
            cat_tokens = _category_token_set(category)
            shared = (kw_tokens & cat_tokens) - KEYWORD_CONFLICT_STOP_TOKENS
            if len(shared) >= KEYWORD_CONFLICT_MIN_SHARED:
                out.append(
                    KeywordConflict(
                        keyword_text=keyword.text,
                        category=category,
                        shared_tokens=tuple(sorted(shared)),
                    )
                )
                break
    return out


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


def _llm_phrases(
    llm_phrases: Sequence[str] | None,
) -> Iterable[tuple[str, str, str]]:
    """Yield validated LLM long-tail phrases, capped at LLM_KEYWORDS_MAX_SLOTS.

    Each phrase must survive normalization and carry at least
    ``LLM_KEYWORDS_MIN_WORDS`` words so single-word head terms (already
    covered by the deterministic subject pipeline) are dropped. Non-string
    items are ignored. The final KDP rule-check (char limit, forbidden
    tokens, title repetition, dedupe) still runs in :func:`_make_keyword`.
    """

    if not llm_phrases:
        return
    emitted = 0
    for raw in llm_phrases:
        if emitted >= LLM_KEYWORDS_MAX_SLOTS:
            return
        if not isinstance(raw, str):
            continue
        normalized = _normalize_phrase(raw)
        if len(normalized.split()) < LLM_KEYWORDS_MIN_WORDS:
            continue
        emitted += 1
        yield (
            normalized,
            KDP_KEYWORD_SOURCE_LLM,
            "Long-Tail-Phrase aus dem Manuskript — echte Amazon-Suchanfrage statt Template.",
        )


def build_kdp_keywords(
    project: BookProject,
    *,
    llm_phrases: Sequence[str] | None = None,
) -> list[KDPKeyword]:
    """Return up to 7 KDP-ready keyword strings for a project.

    When ``llm_phrases`` is provided, up to ``LLM_KEYWORDS_MAX_SLOTS`` of the
    LLM-derived long-tail phrases claim the first slots (after the same KDP
    rule-check as every other source), and the deterministic pipelines fill
    the remaining slots. With ``llm_phrases=None`` the result is identical to
    the pure-template path, so the generator stays usable without an API key.
    """

    subject = _extract_subject(project)
    audience = _extract_audience(project) or FALLBACK_AUDIENCES[0]
    anchors = extract_anchor_keywords(project)
    title_tokens = _title_tokens(project)

    keywords: list[KDPKeyword] = []
    seen: set[str] = set()

    pipelines: list[Iterable[tuple[str, str, str]]] = [
        _llm_phrases(llm_phrases),
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


def _render_conflict_section(
    categories: list[str],
    conflicts: list[KeywordConflict],
) -> list[str]:
    """Return Markdown lines for the conflict-check section."""

    lines: list[str] = ["## Konflikt-Check (Kategorie vs. Keyword)", ""]
    if not categories:
        lines.extend([
            "Keine KDP-Kategorien in deinen Metadaten gefunden — Konflikt-Check übersprungen.",
            "",
            "Trage in `metadata.md` deine zwei KDP-Kategorien ein, zum Beispiel:",
            "",
            "```",
            "## KDP Kategorien",
            "- Sachbuch / Wirtschaft / Unternehmensführung",
            "- Sachbuch / Ratgeber / Beruf & Karriere",
            "```",
            "",
            "Sobald die Kategorien dokumentiert sind, prüft dieser Block automatisch, "
            "ob ein Keyword-Slot mit deiner Kategorie überlappt und damit von Amazon "
            "ignoriert würde.",
            "",
        ])
        return lines

    lines.append("Erkannte KDP-Kategorien:")
    for category in categories:
        lines.append(f"- {category}")
    lines.append("")

    if not conflicts:
        lines.extend([
            "🟢 **Kein Konflikt.** Keiner der 7 Keyword-Slots überlappt mit deinen "
            "Kategorien — Amazon zählt alle Slots als zusätzliche Targeting-Signale.",
            "",
        ])
        return lines

    lines.extend([
        f"🔴 **{len(conflicts)} Konflikt(e) gefunden.** Diese Slots wiederholen Tokens, "
        "die bereits in deiner Kategorie stehen — Amazon entwertet solche Slots, "
        "weil die Kategorie schon das Targeting macht.",
        "",
        "| Keyword | Kategorie | Überlappende Begriffe |",
        "|---|---|---|",
    ])
    for conflict in conflicts:
        shared = ", ".join(conflict.shared_tokens) or "-"
        lines.append(
            f"| `{conflict.keyword_text}` | {conflict.category} | {shared} |"
        )
    lines.extend([
        "",
        "Empfehlung: Tausche jedes Konflikt-Keyword gegen eine Long-Tail-Phrase aus, "
        "die mindestens zwei *zusätzliche* Begriffe trägt (z.B. eine konkrete "
        "Anwendung oder eine Zielgruppe), die nicht schon in der Kategorie stehen.",
        "",
    ])
    return lines


def render_kdp_keywords_report_markdown(
    project: BookProject,
    keywords: list[KDPKeyword],
    categories: list[str] | None = None,
    conflicts: list[KeywordConflict] | None = None,
) -> str:
    """Beginner-friendly walk-through with the 7 ready-to-paste strings.

    When ``categories`` is provided (or read from project metadata by
    the caller), an additional ``## Konflikt-Check`` section is rendered
    showing which slots overlap with the chosen KDP category. If
    ``conflicts`` is ``None``, it is recomputed from the keyword list
    and categories so callers can pass just the categories.
    """

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
    categories_list = list(categories) if categories is not None else extract_kdp_categories(project)
    if conflicts is None:
        conflicts_list = find_keyword_conflicts(keywords, categories_list)
    else:
        conflicts_list = list(conflicts)
    lines.extend(_render_conflict_section(categories_list, conflicts_list))

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


# --- Optional LLM-Pass for long-tail keyword extraction -------------------

LLM_KEYWORDS_SYSTEM_PROMPT: str = (
    "Du bist ein KDP-Keyword-Stratege fuer den deutschen Sachbuchmarkt. "
    "Deine Aufgabe: aus Titel, Untertitel, Beschreibung und Kapitel-Titeln "
    "die staerksten Long-Tail-Suchphrasen ableiten, die ein Leser bei Amazon "
    "tatsaechlich eintippt. Jede Phrase besteht aus 2 bis 5 Woertern, ist "
    "konkret und thematisch (kein Hype, keine Marketing-Floskeln, keine "
    "subjektiven Begriffe wie 'bestseller' oder 'kostenlos'), wiederholt nicht "
    "nur den Buchtitel und ist hoechstens 50 Zeichen lang. Antworte "
    "ausschliesslich als JSON mit dem Schluessel 'keywords' (Array aus bis zu "
    "7 Strings). Kein zusaetzlicher Text."
)


def build_kdp_keywords_user_prompt(
    project: BookProject,
    chapter_titles: Sequence[str],
) -> str:
    """Render the user prompt for the LLM long-tail keyword extractor.

    Chapter titles are capped via ``LLM_KEYWORDS_MAX_CHAPTER_TITLES`` so the
    prompt stays bounded even on books with many chapters.
    """

    title = (project.title or "").strip() or "(kein Titel)"
    subtitle = (project.subtitle or "").strip() or "(kein Untertitel)"
    description = (project.amazon_description or "").strip() or "(keine Beschreibung)"
    chapters_capped = [
        c for c in list(chapter_titles)[:LLM_KEYWORDS_MAX_CHAPTER_TITLES] if c
    ]
    if chapters_capped:
        chapter_block = "\n".join(f"- {c}" for c in chapters_capped)
    else:
        chapter_block = "(keine Kapitel-Titel verfuegbar)"
    return (
        f"Titel: {title}\n"
        f"Untertitel: {subtitle}\n\n"
        "Amazon-Beschreibung:\n"
        f"{description}\n\n"
        "Kapitel-Titel:\n"
        f"{chapter_block}\n\n"
        "Liefere bis zu 7 Long-Tail-Suchphrasen im geforderten JSON-Format."
    )


def _parse_llm_keywords_payload(payload: Any) -> list[str]:
    """Extract keyword phrase strings from the LLM JSON response.

    Robust to shape drift: a non-dict payload, a missing/non-list
    ``keywords`` key, or non-string / empty items all collapse to a clean
    list (possibly empty) without raising.
    """

    if not isinstance(payload, dict):
        return []
    raw = payload.get("keywords")
    if not isinstance(raw, list):
        return []
    phrases: list[str] = []
    for item in raw:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                phrases.append(stripped)
    return phrases


def extract_kdp_keywords_via_llm(
    project: BookProject,
    chapter_titles: Sequence[str],
    llm_completer: Callable[[str, str], dict[str, Any]],
) -> list[str]:
    """Call the LLM to extract book-specific long-tail keyword phrases.

    ``llm_completer`` is expected to behave like ``LLMClient.complete_json``
    — take a system+user prompt pair and return a parsed JSON dict. Any
    exception (network, API key, malformed JSON) is swallowed and turned
    into an empty list so the caller can fall back to the deterministic
    template path without aborting the pipeline.
    """

    user_prompt = build_kdp_keywords_user_prompt(project, chapter_titles)
    try:
        payload = llm_completer(LLM_KEYWORDS_SYSTEM_PROMPT, user_prompt)
    except Exception:
        return []
    return _parse_llm_keywords_payload(payload)
