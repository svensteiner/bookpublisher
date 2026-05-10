from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from modules.discovery import BookProject


def find_kindle_previewer() -> Path | None:
    candidates = [
        Path(r"C:\Users") / Path.home().name / r"AppData\Local\Amazon\Kindle Previewer 3\Kindle Previewer 3.exe",
        Path(r"C:\Program Files\Amazon\Kindle Previewer 3\Kindle Previewer 3.exe"),
        Path(r"C:\Program Files (x86)\Amazon\Kindle Previewer 3\Kindle Previewer 3.exe"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def render_kindle_preview_check(project: BookProject) -> str:
    previewer = find_kindle_previewer()
    status = "FOUND" if previewer else "NOT_FOUND"
    lines = [
        "# Kindle-Vorschau-Pruefung",
        "",
        f"Projekt: `{project.project_id}`",
        f"Status: **{status}**",
        f"Kindle Previewer: `{previewer}`" if previewer else "Kindle Previewer wurde in den ueblichen Windows-Pfaden nicht gefunden.",
        "",
        "## Was du pruefen musst",
        "",
        "1. Oeffne das DOCX-Manuskript im Kindle Previewer.",
        "2. Pruefe Handy-, Tablet- und Kindle-Ansicht.",
        "3. Pruefe, ob das Inhaltsverzeichnis klickbar ist.",
        "4. Pruefe Ueberschriften, Abstaende und Absatzumbrueche.",
        "5. Pruefe, ob die ersten 10 Prozent genug Kaufinteresse erzeugen.",
        "6. Pruefe das Cover als kleines Vorschaubild auf weissem Hintergrund.",
        "7. Exportiere erst dann einen Proof, wenn die Vorschau sauber aussieht.",
        "",
        "## Einfache Anleitung",
        "",
        "Wenn Kindle Previewer nicht installiert ist, installiere ihn ueber Amazon KDP und oeffne danach die DOCX-Datei manuell. Der Agent laedt nichts hoch und veroeffentlicht nichts.",
        "",
        "## Dateien",
        "",
        f"- Manuskript: `{project.manuscript or 'FEHLT'}`",
        f"- Cover: `{project.cover or 'FEHLT'}`",
    ]
    return "\n".join(lines)


_STOP_WORDS = {
    "eine", "einen", "einem", "eines", "der", "die", "das", "und", "oder",
    "aber", "auch", "sich", "nicht", "mit", "von", "fuer", "über", "meine",
    "mein", "wie", "was", "ich", "wir", "haben", "wird", "kann", "wenn",
    "dass", "als", "aus", "bei", "nach", "seit", "ohne", "the", "and", "for",
    "with", "how", "what", "that", "this", "from",
}


def _derive_search_queries(project: BookProject, max_queries: int = 7) -> list[str]:
    """Generate Amazon search queries from title, subtitle, and description."""
    queries: list[str] = []

    if project.title:
        queries.append(project.title[:60])

    # Extract significant words from title + subtitle
    raw = " ".join(filter(None, [project.title, project.subtitle]))
    words = [
        w.strip(".,!?;:\"'()[]") for w in raw.split()
        if len(w) > 4 and w.lower().strip(".,!?;:\"'()[]") not in _STOP_WORDS
    ]

    # Build 2-word keyphrases
    for i in range(0, len(words) - 1, 2):
        phrase = f"{words[i]} {words[i + 1]}"
        if phrase not in queries:
            queries.append(phrase)
        if len(queries) >= max_queries - 1:
            break

    # Add subtitle as a separate query if distinct
    if project.subtitle and project.subtitle not in queries:
        queries.append(project.subtitle[:60])

    # Fallback if too few queries
    if len(queries) < 3:
        queries.extend(["Sachbuch Praxis", "Ratgeber Praxis"])

    return queries[:max_queries]


def render_amazon_research_brief(project: BookProject) -> str:
    queries = _derive_search_queries(project)
    query_lines = "\n".join(f"- {q}" for q in queries)
    lines = [
        "# Amazon-Recherche",
        "",
        f"Projekt: `{project.project_id}`",
        f"Titel: {project.title or 'Unbekannt'}",
        f"Untertitel: {project.subtitle or 'Unbekannt'}",
        "",
        "## Ziel",
        "",
        "Nutze diese Recherche vor der Veroeffentlichung, um dein Buch mit echten Amazon-Ergebnissen zu vergleichen. Suche manuell auf Amazon und fuelle die Wettbewerbs-Tabelle aus.",
        "",
        "## Suchbegriffe fuer Amazon",
        "",
        query_lines,
        "",
        "## Was du eintragen sollst",
        "",
        "- Titel und Untertitel des Konkurrenzbuchs",
        "- Preis und Format",
        "- Anzahl Bewertungen und Sterne",
        "- Kategorie oder Rang, falls sichtbar",
        "- die ersten drei Zeilen der Beschreibung",
        "- ob das Cover als kleines Bild lesbar ist",
        "- welches Versprechen das Konkurrenzbuch macht",
        "- was dein Buch glaubwuerdig besser oder konkreter macht",
        "",
        "## Entscheidungsregel",
        "",
        "Wenn dein Buch klarer, konkreter, weniger hype-lastig und als kleines Cover konkurrenzfaehig ist, kannst du nach den verbleibenden QA-Fixes veroeffentlichen.",
    ]
    return "\n".join(lines)


def render_competitor_template_csv(project: BookProject | None = None) -> str:
    queries = _derive_search_queries(project) if project else ["Sachbuch Praxis", "Ratgeber Praxis"]
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "search_query",
        "competitor_title",
        "author",
        "price",
        "format",
        "review_count",
        "rating",
        "category_or_rank",
        "first_three_description_lines",
        "thumbnail_readable_yes_no",
        "promise",
        "our_advantage",
        "risk_for_our_book",
    ])
    for query in queries:
        writer.writerow([query, "", "", "", "", "", "", "", "", "", "", "", ""])
    return output.getvalue()
