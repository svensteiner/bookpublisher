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


def render_amazon_research_brief(project: BookProject) -> str:
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
        "- KI Agenten Unternehmen",
        "- KI Automatisierung Praxis",
        "- kuenstliche Intelligenz Unternehmen",
        "- Automatisierung ohne Mitarbeiter",
        "- KI fuer Selbststaendige",
        "- Zukunft der Arbeit KI",
        "- KI Produktivitaet Manager",
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


def render_competitor_template_csv() -> str:
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
    for query in [
        "KI Agenten Unternehmen",
        "KI Automatisierung Praxis",
        "kuenstliche Intelligenz Unternehmen",
        "Automatisierung ohne Mitarbeiter",
        "KI fuer Selbststaendige",
        "Zukunft der Arbeit KI",
        "KI Produktivitaet Manager",
    ]:
        writer.writerow([query, "", "", "", "", "", "", "", "", "", "", "", ""])
    return output.getvalue()
