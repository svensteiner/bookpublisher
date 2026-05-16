from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.cover import analyze_cover
from modules.discovery import BookProject
from modules.readers import read_any_text, read_text_file


@dataclass
class Gate:
    name: str
    status: str
    score: int
    findings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "score": self.score,
            "findings": self.findings,
            "fixes": self.fixes,
        }


from modules.scoring import (
    SCORE_BADGE_FIX,
    SCORE_BADGE_READY,
    SCORE_BADGE_REVIEW,
    SCORE_READY as SCORE_READY_THRESHOLD,
    SCORE_REVIEW as SCORE_REVIEW_THRESHOLD,
    score_badge,
    status_for as _status,
)

# Beginner-friendly German labels per gate (technical key → display label).
GATE_DISPLAY_LABELS: dict[str, str] = {
    "asset_completeness": "Dateien vollständig",
    "metadata_and_storefront": "Amazon-Metadaten",
    "kindle_ebook_readiness": "Kindle-Lesbarkeit",
    "production_package": "Produktionspaket",
    "amazon_sellability": "Amazon-Verkaufbarkeit",
}


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÄÖÜäöüß-]+\b", text, flags=re.UNICODE))


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]


def _read_notes(project: BookProject) -> str:
    parts: list[str] = []
    for path in project.metadata_files + project.notes_files:
        if path.exists() and path.suffix.lower() in {".md", ".txt"}:
            try:
                parts.append(read_text_file(path))
            except OSError:
                continue
    return "\n\n".join(parts)


def _proof_pdfs(project: BookProject) -> list[Path]:
    if not project.root.exists():
        return []
    candidates = [path for path in project.root.rglob("*.pdf") if path.is_file()]
    return sorted(candidates, key=lambda path: str(path).lower())


def _extract_keywords(text: str) -> list[str]:
    patterns = [
        r"##\s*(?:Keywords|Suchbegriffe|Amazon Keywords|KDP Keywords)\s*(.*?)(?:\n##\s+|\Z)",
        r"(?:Keywords|Suchbegriffe)\s*:\s*(.+)",
    ]
    found: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if not match:
            continue
        block = match.group(1)
        for item in re.split(r"[\n,;]+", block):
            cleaned = item.strip(" -*\t")
            if 2 <= len(cleaned) <= 80:
                found.append(cleaned)
    return list(dict.fromkeys(found))[:12]


def analyze_docx_structure(path: Path | None) -> dict[str, Any]:
    if not path:
        return {"available": False}
    from modules.readers import open_docx_paragraphs

    doc = open_docx_paragraphs(path)
    paragraphs: list[dict[str, Any]] = []
    headings: list[str] = []
    body_word_counts: list[int] = []
    non_empty_index = 0
    toc_detected = False

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        non_empty_index += 1
        style = para.style.name if para.style else ""
        words = _word_count(text)
        is_heading = "heading" in style.lower() or "überschrift" in style.lower()
        if is_heading:
            headings.append(text)
        else:
            body_word_counts.append(words)
        if non_empty_index <= 30 and re.search(r"\b(inhaltsverzeichnis|table of contents|toc)\b", text, flags=re.I):
            toc_detected = True
        paragraphs.append({"text": text, "style": style, "words": words, "is_heading": is_heading})

    text = "\n".join(item["text"] for item in paragraphs)
    total_words = _word_count(text)
    long_paragraphs = [item for item in paragraphs if not item["is_heading"] and item["words"] > 120]
    very_long_paragraphs = [item for item in paragraphs if not item["is_heading"] and item["words"] > 180]
    sample_words = max(1, round(total_words * 0.10))
    sample_text_parts: list[str] = []
    current_words = 0
    for item in paragraphs:
        sample_text_parts.append(item["text"])
        current_words += item["words"]
        if current_words >= sample_words:
            break
    sample_text = "\n".join(sample_text_parts)

    style_counts = Counter(item["style"] for item in paragraphs)
    return {
        "available": True,
        "path": str(path),
        "word_count": total_words,
        "paragraph_count": len(paragraphs),
        "body_paragraph_count": len(body_word_counts),
        "heading_count": len(headings),
        "first_headings": headings[:20],
        "table_count": len(doc.tables),
        "inline_shape_count": len(doc.inline_shapes),
        "toc_detected": toc_detected,
        "average_body_paragraph_words": round(sum(body_word_counts) / len(body_word_counts), 1) if body_word_counts else 0,
        "long_paragraph_count": len(long_paragraphs),
        "very_long_paragraph_count": len(very_long_paragraphs),
        "long_paragraph_ratio": round(len(long_paragraphs) / max(1, len(body_word_counts)), 3),
        "sample_word_target": sample_words,
        "sample_actual_words": current_words,
        "sample_sentence_count": len(_sentences(sample_text)),
        "sample_heading_count": sum(1 for item in paragraphs[: max(1, len(sample_text_parts))] if item["is_heading"]),
        "styles": dict(style_counts.most_common(20)),
    }


def _asset_gate(project: BookProject) -> Gate:
    findings: list[str] = []
    fixes: list[str] = []
    score = 100
    blocking = False

    required = {
        "manuscript_docx": project.manuscript,
        "cover_image": project.cover,
        "title": project.title,
        "subtitle": project.subtitle,
        "author": project.author,
        "amazon_description": project.amazon_description,
    }
    for name, value in required.items():
        if value:
            findings.append(f"{name}: present")
        else:
            findings.append(f"{name}: missing")
            fixes.append(f"Add production-ready {name}.")
            score -= 18
            if name in {"manuscript_docx", "cover_image", "title", "amazon_description"}:
                blocking = True

    return Gate("asset_completeness", _status(max(0, score), blocking=blocking), max(0, score), findings, fixes)


def _metadata_gate(project: BookProject, notes_text: str) -> Gate:
    findings: list[str] = []
    fixes: list[str] = []
    score = 100

    title_len = len(project.title or "")
    subtitle_len = len(project.subtitle or "")
    description = project.amazon_description or ""
    description_words = _word_count(description)
    keywords = _extract_keywords(notes_text)

    if 12 <= title_len <= 80:
        findings.append(f"title length: {title_len} chars")
    else:
        score -= 12
        fixes.append("Tune title length for Amazon scan speed and thumbnail comprehension.")

    if 20 <= subtitle_len <= 200:
        findings.append(f"subtitle length: {subtitle_len} chars")
    else:
        score -= 10
        fixes.append("Use the subtitle to state reader, method, and practical payoff.")

    if 120 <= description_words <= 650:
        findings.append(f"Amazon description: {description_words} words")
    else:
        score -= 16
        fixes.append("Bring Amazon description into a strong 120-650 word sales range.")

    if description.count("- ") >= 4 or description.count("\n-") >= 4:
        findings.append("description has scannable bullet structure")
    else:
        score -= 8
        fixes.append("Add buyer-friendly bullets to the Amazon description.")

    if len(keywords) >= 7:
        findings.append(f"keyword candidates found: {len(keywords)}")
    else:
        score -= 12
        fixes.append("Document at least 7 Amazon keyword candidates.")

    category_markers = re.findall(r"\b(kategorie|category|bisac|thema)\b", notes_text, flags=re.I)
    if category_markers:
        findings.append("category notes found")
    else:
        score -= 10
        fixes.append("Document primary and secondary KDP category hypotheses.")

    return Gate("metadata_and_storefront", _status(max(0, score)), max(0, score), findings, fixes)


def _kindle_gate(profile: dict[str, Any]) -> Gate:
    findings: list[str] = []
    fixes: list[str] = []
    score = 100
    blocking = False

    if not profile.get("available"):
        return Gate("kindle_ebook_readiness", "FIX", 0, ["manuscript not available"], ["Add a DOCX manuscript."])

    words = int(profile.get("word_count") or 0)
    headings = int(profile.get("heading_count") or 0)
    avg_para = float(profile.get("average_body_paragraph_words") or 0)
    long_ratio = float(profile.get("long_paragraph_ratio") or 0)

    findings.append(f"word count: {words}")
    findings.append(f"heading count: {headings}")
    findings.append(f"average body paragraph words: {avg_para}")
    findings.append(f"long paragraph ratio: {long_ratio}")

    if words < 12000:
        score -= 18
        fixes.append("Confirm the ebook has enough perceived value for paid nonfiction.")
    if headings < 8:
        score -= 18
        fixes.append("Add/verify a clear heading hierarchy for Kindle navigation.")
    if avg_para > 75:
        score -= 12
        fixes.append("Shorten average paragraph length for mobile Kindle reading.")
    if long_ratio > 0.12:
        score -= 12
        fixes.append("Break long paragraphs before Kindle upload.")
    if profile.get("table_count", 0) > 0:
        score -= 5
        fixes.append("Preview every table on phone-sized Kindle layouts.")
    if not profile.get("toc_detected"):
        score -= 8
        fixes.append("Verify the DOCX heading styles generate a clickable Kindle TOC.")
    if profile.get("sample_sentence_count", 0) < 20:
        score -= 8
        fixes.append("Strengthen the first 10 percent sample with clearer promise and proof.")

    if words == 0:
        blocking = True
    return Gate("kindle_ebook_readiness", _status(max(0, score), blocking=blocking), max(0, score), findings, fixes)


def _production_gate(project: BookProject, profile: dict[str, Any]) -> Gate:
    findings: list[str] = []
    fixes: list[str] = []
    score = 100
    blocking = False

    if project.cover:
        cover = analyze_cover(project.cover)
        findings.append(f"cover size: {cover['width']} x {cover['height']} px")
        findings.append(f"cover ratio: {cover['aspect_ratio_height_width']}")
        if cover["width"] < 1500 or cover["height"] < 2500:
            score -= 20
            fixes.append("Export ebook cover at production size before KDP upload.")
        if not 1.45 <= cover["aspect_ratio_height_width"] <= 1.65:
            score -= 20
            fixes.append("Adjust ebook cover ratio near 1.6 height/width.")
        if cover["low_thumbnail_contrast_risk"]:
            score -= 12
            fixes.append("Increase thumbnail contrast for Amazon search results.")
        if cover["very_light_cover"]:
            score -= 8
            fixes.append("Check edge visibility on Amazon white backgrounds.")
    else:
        score -= 35
        blocking = True
        fixes.append("Add a production-ready cover image.")

    if profile.get("inline_shape_count", 0) > 0:
        findings.append(f"inline images in manuscript: {profile['inline_shape_count']}")
        fixes.append("Preview every inline image in Kindle Previewer.")
        score -= 5
    else:
        findings.append("inline images in manuscript: 0")

    proof_pdfs = project.pdf_files or _proof_pdfs(project)
    if proof_pdfs:
        findings.append(f"PDF/proof exports found: {len(proof_pdfs)}")
    else:
        score -= 5
        fixes.append("Keep a rendered customer PDF/proof outside upload-critical files for final visual QA.")

    return Gate("production_package", _status(max(0, score), blocking=blocking), max(0, score), findings, fixes)


def _sellability_gate(project: BookProject, profile: dict[str, Any], notes_text: str) -> Gate:
    findings: list[str] = []
    fixes: list[str] = []
    score = 100
    combined = "\n".join([project.title or "", project.subtitle or "", project.amazon_description or "", notes_text])

    markers = {
        # Targets a named reader type — works for any nonfiction category
        "specific_reader": r"\b(selbstst[aä]ndige|gr[uü]nder|manager|ceo|cfo|cto|berater|wissensarbeiter|unternehmer|f[uü]hrungskraft|freiberufler|coach|einsteiger|fortgeschrittene|professional|leser|angestellte|studenten)\b",
        # Claims a differentiated, non-generic angle
        "differentiated_angle": r"\b(kein hype|keine theorie|echte praxis|n[üu]chtern|ehrlich|anders als|ohne umwege|direkt|konkret|nicht wie|feldnotiz|aus der praxis|gegen den strom)\b",
        # Contains any specific number+unit or concrete proof signal — works for any book
        "proof_or_specificity": r"(?:\d+\s*(?:euro|€|\$|stunden|tage|wochen|monate|%|prozent|schritte|tipps|methoden|beispiele|strategien|fehler|projekte|seiten|jahre|kunden|nutzer|minuten)|(?:fallstudie|case study|selbst getestet|live-projekt|aus eigener erfahrung))",
        # Promises actionable content — generic nonfiction buying signal
        "practical_payoff": r"\b(anleitung|schritt-f[uü]r-schritt|methode|system|framework|checkliste|vorlage|werkzeug|praktisch|umsetzbar|sofort|heute noch|direkt anwendbar|leitfaden|aufgaben|kontrollpunkte)\b",
    }
    for name, pattern in markers.items():
        if re.search(pattern, combined, flags=re.I):
            findings.append(f"{name}: present")
        else:
            score -= 12
            fixes.append(f"Make {name.replace('_', ' ')} explicit on the product page.")

    if profile.get("sample_heading_count", 0) >= 2:
        findings.append("first 10 percent has structural signposts")
    else:
        score -= 10
        fixes.append("Make the first Kindle sample structurally obvious within the first 10 percent.")

    if profile.get("sample_sentence_count", 0) >= 35:
        findings.append("first 10 percent has enough reading surface")
    else:
        score -= 8
        fixes.append("Ensure the first 10 percent contains enough concrete promise, proof, and payoff.")

    return Gate("amazon_sellability", _status(max(0, score)), max(0, score), findings, fixes)


def build_industrial_qa(project: BookProject, agent_context: dict[str, Any] | None = None) -> dict[str, Any]:
    notes_text = _read_notes(project)
    profile = analyze_docx_structure(project.manuscript)
    gates = [
        _asset_gate(project),
        _metadata_gate(project, notes_text),
        _kindle_gate(profile),
        _production_gate(project, profile),
        _sellability_gate(project, profile, notes_text),
    ]
    blocking = [gate for gate in gates if gate.status == "FIX"]
    review = [gate for gate in gates if gate.status == "REVIEW" or gate.fixes]
    average_score = round(sum(gate.score for gate in gates) / len(gates)) if gates else 0
    decision = "GO" if not blocking and not review and average_score >= 85 else "GO_AFTER_FIXES" if not blocking else "HOLD"
    investor_grade = round(average_score / 10, 1)

    return {
        "project_id": project.project_id,
        "decision": decision,
        "industrial_score": average_score,
        "investor_grade": investor_grade,
        "gates": [gate.to_json() for gate in gates],
        "docx_profile": profile,
        "keywords_found": _extract_keywords(notes_text),
        "all_required_fixes": [fix for gate in gates for fix in gate.fixes],
        "agent_context": agent_context or {},
    }


def _gate_lookup(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {gate["name"]: gate for gate in report.get("gates", [])}


def _traffic_light(decision: str) -> tuple[str, str]:
    if decision == "GO":
        return "GRUEN", "Du kannst nach einer normalen Sichtpruefung hochladen."
    if decision == "GO_AFTER_FIXES":
        return "GELB", "Das Buch ist stark, aber vor dem Upload bleiben einzelne Kontrollpunkte."
    return "ROT", "Nicht hochladen. Es gibt blockierende Punkte."


def _affected_file_for_fix(fix: str) -> str:
    lower = fix.lower()
    if "cover" in lower or "thumbnail" in lower or "background" in lower:
        return "Cover-Datei"
    if "description" in lower or "keyword" in lower or "category" in lower or "title" in lower or "subtitle" in lower:
        return "Amazon_Beschreibung_und_Metadaten.md"
    if "docx" in lower or "kindle" in lower or "paragraph" in lower or "toc" in lower or "sample" in lower or "ebook" in lower:
        return "DOCX-Manuskript"
    if "pdf" in lower or "proof" in lower:
        return "Proof-PDF"
    return "Buchordner"


def _plain_fix(fix: str) -> str:
    lower = fix.lower()
    if "enough perceived value" in lower:
        return "Pruefe, ob das Buch fuer den geplanten Preis genug wahrgenommenen Wert bietet. Bei rund 9.000 Woertern sollte der Nutzen sehr klar sein."
    if "edge visibility" in lower:
        return "Pruefe, ob das Cover auf weissem Amazon-Hintergrund noch klar abgegrenzt ist."
    if "kindle previewer" in lower:
        return "Oeffne die Datei in der Kindle-Vorschau und pruefe Handy-, Tablet- und Kindle-Ansicht."
    if "clickable kindle toc" in lower or "toc" in lower:
        return "Pruefe, ob das Inhaltsverzeichnis in Kindle klickbar ist."
    if "amazon description" in lower:
        return "Passe die Amazon-Beschreibung an, damit Nutzen und Zielgruppe sofort klar sind."
    if "keyword" in lower:
        return "Ergaenze mindestens sieben passende Amazon-Suchbegriffe."
    if "category" in lower:
        return "Lege passende Amazon-Kategorien fest."
    if "paragraph" in lower:
        return "Kuerze zu lange Absaetze, damit das Buch am Handy leichter lesbar ist."
    if "cover" in lower:
        return "Pruefe oder ersetze die Cover-Datei."
    return fix


def _render_gate_overview(report: dict[str, Any]) -> list[str]:
    """Render the unified Gate-Übersicht block for beginner_summary."""

    gates = report.get("gates") or []
    if not gates:
        return []
    lines: list[str] = ["## Gate-Übersicht", "", "Skala: 🟢 ≥85 · 🟡 65–84 · 🔴 <65", ""]
    for gate in gates:
        name = gate.get("name") or ""
        score = int(gate.get("score") or 0)
        blocking = (gate.get("status") == "FIX") and score >= SCORE_REVIEW_THRESHOLD
        badge, status = score_badge(score, blocking=blocking)
        # Respect an explicit FIX status that ignores the score (blocking gates).
        if gate.get("status") == "FIX" and status != "FIX":
            badge, status = SCORE_BADGE_FIX, "FIX"
        label = GATE_DISPLAY_LABELS.get(name, name.replace("_", " ").title())
        lines.append(f"- {badge} **{label}** — {score}/100 ({status})")
    lines.append("")
    return lines


def _render_gate_overview_table(report: dict[str, Any]) -> list[str]:
    """Render a compact table view of all gates for ``industrial_qa_report.md``.

    Power-user companion to :func:`_render_gate_overview`: shows the same
    badge / score / status data, but in a tabular layout (Gate | Badge |
    Score | Status) so reviewers can scan the gate verdict at a glance
    before drilling into per-gate findings. Returns ``[]`` when there
    are no gates — keeps the report clean for first runs.
    """

    gates = report.get("gates") or []
    if not gates:
        return []
    lines: list[str] = [
        "## Gate-Übersicht",
        "",
        "Skala: 🟢 ≥85 · 🟡 65–84 · 🔴 <65",
        "",
        "| Gate | Badge | Score | Status |",
        "|---|---|---|---|",
    ]
    for gate in gates:
        name = gate.get("name") or ""
        score = int(gate.get("score") or 0)
        blocking = (gate.get("status") == "FIX") and score >= SCORE_REVIEW_THRESHOLD
        badge, status = score_badge(score, blocking=blocking)
        if gate.get("status") == "FIX" and status != "FIX":
            badge, status = SCORE_BADGE_FIX, "FIX"
        label = GATE_DISPLAY_LABELS.get(name, name.replace("_", " ").title())
        lines.append(f"| {label} | {badge} | {score}/100 | {status} |")
    lines.append("")
    return lines


def _render_weakest_chapters(weakest_chapters: list[dict[str, Any]] | None) -> list[str]:
    """Render the 'Schwächste Kapitel' block from a list of weakest-chapter dicts.

    Each dict is expected to carry ``index``, ``title``, ``overall`` and
    ``fix`` (matching ``ChapterScore.to_json``). Returns an empty list when
    no data is provided — the section is then omitted entirely.
    """

    if not weakest_chapters:
        return []
    lines: list[str] = ["## Schwächste Kapitel", ""]
    for chap in weakest_chapters:
        title = str(chap.get("title") or f"Kapitel {chap.get('index', '?')}")[:80]
        index = chap.get("index", "?")
        score = int(chap.get("overall") or 0)
        badge, _ = score_badge(score)
        fix = str(chap.get("fix") or "").strip() or "Kein Fix-Vorschlag verfügbar."
        lines.append(f"- {badge} **Kapitel {index} — {title}** ({score}/100)")
        lines.append(f"  Fix: {fix}")
    lines.append("")
    return lines


# Beginner-friendly German labels per rewrite field (technical key → display).
REWRITE_FIELD_LABELS: dict[str, str] = {
    "title": "Titel",
    "subtitle": "Untertitel",
    "description_lead": "Beschreibungs-Einstieg",
}


def _render_top_chapter_balance(
    top_balance: dict[str, Any] | None,
) -> list[str]:
    """Render the 'Kapitel-Balance' block from the most extreme outlier.

    Surfaces the single most surprising word-count outlier — either the
    longest chapter (split candidate) or the shortest (merge candidate).
    Skips the section entirely when no outlier exists or the payload
    carries no actionable fix; a balanced book should not be nagged with
    cosmetic structural notes.

    The dict is expected to carry ``kind`` (``"oversized"`` or
    ``"undersized"``), ``index``, ``title``, ``word_count``, ``median``,
    ``ratio`` and ``fix``.
    """

    if not top_balance:
        return []
    fix = str(top_balance.get("fix") or "").strip()
    if not fix:
        return []
    kind = str(top_balance.get("kind") or "").strip()
    index = top_balance.get("index", "?")
    title = str(top_balance.get("title") or "").strip() or f"Kapitel {index}"
    word_count = int(top_balance.get("word_count") or 0)
    median = int(top_balance.get("median") or 0)
    ratio = float(top_balance.get("ratio") or 0.0)
    if kind == "oversized":
        kind_label = "🔴 Split-Kandidat — Kapitel ist zu lang"
    elif kind == "undersized":
        kind_label = "🟡 Merge-Kandidat — Kapitel ist zu kurz"
    else:
        kind_label = "Strukturelles Risiko"
    ratio_str = f"{ratio:.1f}×".replace(".0×", "×")
    lines = [
        "## Kapitel-Balance",
        "",
        (
            "Der extremste Längen-Ausreißer im Manuskript — strukturelle "
            "Schieflagen verraten oft, wo der Leser abbricht."
        ),
        "",
        f"- {kind_label}",
        f"- **Kapitel {index} — {title}** ({word_count} Wörter, Median {median})",
        f"- Verhältnis zum Median: **{ratio_str}**",
        "",
        "**Fix:**",
        "",
        f"> {fix}",
        "",
        "Vollständige Balance-Tabelle siehe `chapter_review.md`.",
        "",
    ]
    return lines


def _render_top_arc(top_arc: dict[str, Any] | None) -> list[str]:
    """Render the 'Kapitel-Reihung' block from a top-arc payload.

    Surfaces the single biggest structural lever from ``chapter_arc.json``:
    either a reorder fix from an inversion or a missing-phase fix. Skips
    the section entirely when the manuscript follows the canonical
    Problem → Lösung → Beweis → Transformation arc cleanly — no point
    nagging the author when the structure is already sound.

    The dict is expected to carry ``arc_score``, ``status``, ``top_fix``,
    ``inversion_count`` and ``missing_count``. Returns an empty list when
    ``top_arc`` is ``None``/empty or carries no usable ``top_fix`` (no
    actionable lever to surface).
    """

    if not top_arc:
        return []
    top_fix = str(top_arc.get("top_fix") or "").strip()
    if not top_fix:
        return []
    arc_score = int(top_arc.get("arc_score") or 0)
    inversion_count = int(top_arc.get("inversion_count") or 0)
    missing_count = int(top_arc.get("missing_count") or 0)
    badge, _ = score_badge(arc_score)
    lines = [
        "## Kapitel-Reihung",
        "",
        (
            "Folgt dein Buch dem klassischen Sachbuch-Bogen "
            "(Problem → Lösung → Beweis → Transformation)?"
        ),
        "",
        f"- {badge} Arc-Score: **{arc_score}/100**",
    ]
    if inversion_count:
        lines.append(
            f"- 🔁 Reihenfolge-Konflikte: **{inversion_count}**"
        )
    if missing_count:
        lines.append(
            f"- ⚠️ Fehlende Phasen: **{missing_count}**"
        )
    lines.extend([
        "",
        "**Größter Hebel:**",
        "",
        f"> {top_fix}",
        "",
        "Vollständige Phasen-Tabelle und alle Fixes siehe `chapter_arc.md`.",
        "",
    ])
    return lines


def _render_top_rewrite(top_rewrite: dict[str, Any] | None) -> list[str]:
    """Render the 'Top-Rewrite-Pick' block from a top-rewrite payload.

    The dict is expected to carry ``field``, ``text``, ``keyword_score``,
    ``char_count`` and ``motivation`` (the strongest single rewrite option
    selected by the pipeline). Returns an empty list when no data is
    provided so the section is omitted entirely — keeping the summary
    clean when the existing metadata has no diagnostic findings.
    """

    if not top_rewrite:
        return []
    field_key = str(top_rewrite.get("field") or "").strip()
    field_label = REWRITE_FIELD_LABELS.get(field_key, field_key or "Feld")
    text = str(top_rewrite.get("text") or "").strip()
    if not text:
        return []
    score = int(top_rewrite.get("keyword_score") or 0)
    char_count = int(top_rewrite.get("char_count") or len(text))
    motivation = str(top_rewrite.get("motivation") or "").strip()
    badge, _ = score_badge(score)
    lines = [
        "## Top-Rewrite-Pick",
        "",
        (
            "Stärkster Copy-Vorschlag mit dem höchsten Keyword-Score — "
            f"kannst du direkt ins KDP-Backend kopieren ({field_label})."
        ),
        "",
        f"**{field_label}:**",
        "",
        f"> {text}",
        "",
        f"- {badge} Keyword-Score: **{score}/100**",
        f"- Zeichen: **{char_count}**",
    ]
    if motivation:
        lines.append(f"- Warum: {motivation}")
    lines.extend([
        "",
        "Weitere Varianten siehe `rewrite_suggestions.md`.",
        "",
    ])
    return lines


def _render_round_delta_highlight(
    highlight: dict[str, Any] | None,
) -> list[str]:
    """Render the 'Runden-Fortschritt' block from a round-delta highlight.

    Surfaces the most motivating signal from the previous round: did the
    author resolve the fixes we flagged last time, did the score move,
    did the decision change? Skips the section entirely when there is no
    previous round to compare against — round 1 has nothing to celebrate.

    The dict is expected to carry ``resolved_count``, ``persistent_count``,
    ``new_count``, ``score_delta`` (int | None), ``decision_changed``,
    ``previous_decision``, ``current_decision``, ``top_resolved`` (list of
    fix strings, already capped) and ``top_persistent`` (list of fix
    strings, already capped). All counts default to 0 when missing so
    partial payloads do not crash the renderer.
    """

    if not highlight:
        return []
    resolved = int(highlight.get("resolved_count") or 0)
    persistent = int(highlight.get("persistent_count") or 0)
    new_count = int(highlight.get("new_count") or 0)
    score_delta = highlight.get("score_delta")
    decision_changed = bool(highlight.get("decision_changed"))
    previous_decision = str(highlight.get("previous_decision") or "").strip()
    current_decision = str(highlight.get("current_decision") or "").strip()
    top_resolved = [str(item).strip() for item in (highlight.get("top_resolved") or []) if str(item).strip()]
    top_persistent = [str(item).strip() for item in (highlight.get("top_persistent") or []) if str(item).strip()]

    if isinstance(score_delta, (int, float)):
        if score_delta > 0:
            score_badge_emoji = SCORE_BADGE_READY
            score_text = f"Score: **+{int(score_delta)} Punkte** seit der Vorrunde"
        elif score_delta < 0:
            score_badge_emoji = SCORE_BADGE_FIX
            score_text = f"Score: **{int(score_delta)} Punkte** seit der Vorrunde"
        else:
            score_badge_emoji = SCORE_BADGE_REVIEW
            score_text = "Score: **±0 Punkte** seit der Vorrunde"
    else:
        score_badge_emoji = SCORE_BADGE_REVIEW
        score_text = "Score: kein Vergleich möglich"

    lines: list[str] = [
        "## Runden-Fortschritt",
        "",
        "Was hat sich seit der letzten Prüfrunde geändert?",
        "",
        f"- {SCORE_BADGE_READY} **{resolved} Fix(es) umgesetzt**",
        f"- {SCORE_BADGE_REVIEW} {persistent} Fix(es) weiterhin offen",
        f"- {SCORE_BADGE_FIX} {new_count} neue(r) Fix(es)",
        f"- {score_badge_emoji} {score_text}",
    ]
    if decision_changed and previous_decision and current_decision:
        lines.append(f"- 🔁 Entscheidung: {previous_decision} → **{current_decision}**")
    lines.append("")

    if top_resolved:
        lines.append("**Erledigt seit der Vorrunde:**")
        lines.extend(f"- ✅ {item}" for item in top_resolved)
        lines.append("")
    if top_persistent:
        lines.append("**Weiterhin offen — jetzt anpacken:**")
        lines.extend(f"- ⚠️ {item}" for item in top_persistent)
        lines.append("")
    lines.extend([
        "Details siehe `round_delta.md`.",
        "",
    ])
    return lines


TREND_LABELS: dict[str, str] = {
    "rising": "steigt",
    "falling": "sinkt",
    "stable": "stabil",
}


def _render_score_history_highlight(
    highlight: dict[str, Any] | None,
) -> list[str]:
    """Render the 'Score-Verlauf' block from a score-history highlight.

    Surfaces the score trajectory across the last few rounds so the author
    sees momentum directly in beginner_summary — no need to open
    ``score_history.md`` separately. Skips the section entirely when there
    are fewer than two data points (no trend with only round 1).

    The dict is expected to carry ``series`` (list of ``{timestamp, score,
    delta}``), ``first_score``, ``latest_score``, ``delta_total`` and
    ``trend`` (``rising`` / ``falling`` / ``stable``). All counts default
    to safe values so partial payloads do not crash the renderer.
    """

    if not highlight:
        return []
    series = highlight.get("series") or []
    if len(series) < 2:
        return []
    delta_total = highlight.get("delta_total")
    trend_key = str(highlight.get("trend") or "stable")
    trend_label = TREND_LABELS.get(trend_key, "stabil")

    if isinstance(delta_total, (int, float)):
        delta_int = int(delta_total)
        if delta_int > 0:
            trend_badge = SCORE_BADGE_READY
            delta_text = f"**+{delta_int} Punkte**"
        elif delta_int < 0:
            trend_badge = SCORE_BADGE_FIX
            delta_text = f"**{delta_int} Punkte**"
        else:
            trend_badge = SCORE_BADGE_REVIEW
            delta_text = "**±0 Punkte**"
    else:
        trend_badge = SCORE_BADGE_REVIEW
        delta_text = "**Verlauf unklar**"

    lines: list[str] = [
        "## Score-Verlauf",
        "",
        "So hat sich dein Industrial-Score über die letzten Prüfrunden bewegt:",
        "",
    ]
    for entry in series:
        try:
            score = int(entry.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        badge, _ = score_badge(score)
        timestamp = str(entry.get("timestamp") or "").strip() or "n/a"
        delta = entry.get("delta")
        if isinstance(delta, (int, float)):
            delta_int = int(delta)
            if delta_int > 0:
                delta_suffix = f" (+{delta_int})"
            elif delta_int < 0:
                delta_suffix = f" ({delta_int})"
            else:
                delta_suffix = " (±0)"
        else:
            delta_suffix = ""
        lines.append(f"- {badge} **{score}/100** — {timestamp}{delta_suffix}")
    window_len = len(series)
    lines.extend([
        "",
        f"Trend: {trend_badge} {delta_text} über {window_len} Runden — {trend_label}.",
        "",
        "Details siehe `score_history.md`.",
        "",
    ])
    return lines


_KDP_KEYWORD_COUNT_WORDS: dict[int, str] = {
    1: "stärkste",
    2: "zwei stärksten",
    3: "drei stärksten",
    4: "vier stärksten",
    5: "fünf stärksten",
    6: "sechs stärksten",
    7: "sieben stärksten",
}


def _render_top_kdp_keywords(
    top_keywords: list[dict[str, Any]] | None,
) -> list[str]:
    """Render the 'KDP-Keywords (Top-N)' block from the top-keyword payload.

    Each dict is expected to carry ``text``, ``char_count``, ``source``
    and ``rationale`` (matching ``KDPKeyword.to_json``). Returns an empty
    list when no keywords are provided so the section is omitted entirely
    — the full kdp_keywords.md remains the source of truth for all 7
    slots and the spielregeln-Block. Heading and intro adapt to the
    configured ``beginner_summary_kdp_keyword_limit`` so authors who pick
    5 or 7 slots see the correct count in the section header.
    """

    if not top_keywords:
        return []
    visible = [kw for kw in top_keywords if str(kw.get("text") or "").strip()]
    if not visible:
        return []
    count = len(visible)
    intro_word = _KDP_KEYWORD_COUNT_WORDS.get(count, f"{count} stärksten")
    intro_descriptor = "Slot" if count == 1 else "Slots"
    lines: list[str] = [
        f"## KDP-Keywords (Top-{count})",
        "",
        (
            f"Die {intro_word} {intro_descriptor} aus der 7er-Liste — sofort ins KDP-Backend "
            "übernehmbar (Buchdetails > Schlüsselwörter)."
        ),
        "",
    ]
    for idx, keyword in enumerate(visible, start=1):
        text = str(keyword.get("text") or "").strip()
        char_count = int(keyword.get("char_count") or len(text))
        rationale = str(keyword.get("rationale") or "").strip()
        lines.append(f"{idx}. `{text}`  *(Zeichen: {char_count}/50)*")
        if rationale:
            lines.append(f"   Warum: {rationale}")
    lines.extend([
        "",
        "Alle 7 Slots siehe `kdp_keywords.md`.",
        "",
    ])
    return lines


def _render_sample_section_line(section: dict[str, Any]) -> list[str]:
    """Format one sample-scan section into two markdown lines.

    Returns a `[bullet, fix_line]` pair that matches the existing
    single-section rendering so single- and multi-section blocks stay
    visually consistent.
    """

    label = str(section.get("label") or "").strip()
    index = section.get("index", "?")
    score = int(section.get("overall") or 0)
    risk = str(section.get("risk") or "").strip()
    fix = str(section.get("fix") or "").strip() or "Kein Fix-Vorschlag verfügbar."
    badge, _ = score_badge(score)
    headline = label or f"Abschnitt {index}"
    risk_suffix = f" — {risk}" if risk else ""
    return [
        f"- {badge} **Abschnitt {index} — {headline}** ({score}/100){risk_suffix}",
        f"  Fix: {fix}",
    ]


def _render_weakest_samples(
    weakest_samples: list[dict[str, Any]] | None,
) -> list[str]:
    """Render the weakest-sample block for one or many sections.

    Each dict is expected to carry ``index``, ``label``, ``overall``,
    ``risk`` and ``fix`` (matching ``SampleSectionScore.to_json``). When
    the list is empty (or ``None``) the section is omitted entirely so
    the summary stays clean for manuscripts with no drop-off risk.

    When the list contains exactly one section, the legacy singular
    heading ``## Schwächster Sample-Abschnitt`` is rendered to preserve
    backward-compat with prior summaries. When two or more sections are
    surfaced, a plural heading + intro signal that the Kindle-Sample has
    a cluster issue rather than a single weak passage.
    """

    if not weakest_samples:
        return []
    count = len(weakest_samples)
    if count == 1:
        heading = "## Schwächster Sample-Abschnitt"
        intro = "Hier bricht der Kindle-Leser am ehesten ab. Fixe diesen Abschnitt zuerst."
    else:
        heading = "## Schwächste Sample-Abschnitte"
        intro = (
            f"Die {count} riskantesten Stellen im Kindle-Sample. "
            "Mehrere FIX-Flags hier deuten auf ein Cluster-Problem hin — "
            "fixe sie in dieser Reihenfolge."
        )
    lines: list[str] = [heading, "", intro, ""]
    for section in weakest_samples:
        lines.extend(_render_sample_section_line(section))
    lines.append("")
    return lines


def _render_weakest_sample(weakest_sample: dict[str, Any] | None) -> list[str]:
    """Backwards-compatible single-section renderer.

    Delegates to ``_render_weakest_samples`` with a one-element list so
    existing callers that pass a single dict keep working unchanged.
    """

    if not weakest_sample:
        return []
    return _render_weakest_samples([weakest_sample])


def _render_top_persona(
    top_persona: dict[str, Any] | None,
) -> list[str]:
    """Render the 'Top-Persona' block from a top-persona payload.

    Surfaces the single most likely buyer (Persona #1 of the persona
    report) directly in beginner_summary so the author knows who they
    must address in the first three description lines — without
    opening ``buyer_personas.md`` separately.

    The dict is expected to carry ``label``, ``age_range``, ``job``,
    ``problem``, ``buying_motive``, ``anchor_quote``, ``niche_label``
    and ``niche_confidence``. Returns an empty list when no payload is
    provided or when both ``problem`` and ``buying_motive`` are empty
    — keeping the summary clean if the report has no usable content.
    """

    if not top_persona:
        return []
    label = str(top_persona.get("label") or "").strip()
    problem = str(top_persona.get("problem") or "").strip()
    motive = str(top_persona.get("buying_motive") or "").strip()
    if not problem and not motive:
        return []
    age_range = str(top_persona.get("age_range") or "").strip()
    job = str(top_persona.get("job") or "").strip()
    quote = str(top_persona.get("anchor_quote") or "").strip()
    headline = label or "Persona 1"

    lines: list[str] = [
        "## Top-Persona",
        "",
        (
            "Schreibe Titel, Untertitel und die ersten drei Beschreibungs-Zeilen "
            "so, dass sich diese Persona sofort wiedererkennt."
        ),
        "",
        f"**{headline}**",
        "",
    ]
    if age_range:
        lines.append(f"- **Alter:** {age_range}")
    if job:
        lines.append(f"- **Job / Rolle:** {job}")
    if problem:
        lines.append(f"- **Problem:** {problem}")
    if motive:
        lines.append(f"- **Kaufmotiv:** {motive}")
    if quote:
        lines.append(f"- **Mögliche Suchanfrage:** _{quote}_")
    lines.extend([
        "",
        "Alle 3 Personas siehe `buyer_personas.md`.",
        "",
    ])
    return lines


def _render_amazon_html_preview(
    amazon_html_preview: dict[str, Any] | None,
) -> list[str]:
    """Render the plain-text Kindle-shopper preview of the Amazon HTML.

    Shows ``headline + lead + up to 2 bullets`` as the reader sees
    them above "Mehr lesen" — without HTML tags, normalized whitespace.
    Returns ``[]`` when the payload is empty or carries no real content
    so the section is omitted from the summary.

    The preview text gets clipped to a soft cap so the beginner_summary
    stays scannable; the author still has the full ``amazon_description.html``
    artifact for the complete listing.
    """

    if not amazon_html_preview:
        return []
    headline = str(amazon_html_preview.get("headline") or "").strip()
    lead = str(amazon_html_preview.get("lead") or "").strip()
    bullets = list(amazon_html_preview.get("bullets") or ())
    bullets = [str(item).strip() for item in bullets if str(item).strip()]
    if not headline and not lead and not bullets:
        return []

    lines: list[str] = [
        "## Amazon-Beschreibung (Vorschau)",
        "",
        (
            "So liest der Kindle-Shopper die ersten Zeilen — ohne HTML-Tags,"
            " genau wie im Amazon-Listing über dem \"Mehr lesen\"-Link."
        ),
        "",
    ]
    if headline:
        lines.append(f"> **{headline}**")
    if lead:
        if headline:
            lines.append(">")
        lines.append(f"> {lead}")
    if bullets:
        if headline or lead:
            lines.append(">")
        for bullet in bullets:
            lines.append(f"> - {bullet}")

    char_count = int(amazon_html_preview.get("char_count") or 0)
    keyword_score = int(amazon_html_preview.get("keyword_score") or 0)
    if char_count > 0 or keyword_score > 0:
        meta_parts: list[str] = []
        if char_count > 0:
            meta_parts.append(f"Gesamt-Zeichen: {char_count}")
        if keyword_score > 0:
            meta_parts.append(f"Keyword-Score: {keyword_score}")
        lines.append("")
        lines.append(" · ".join(meta_parts))
    lines.extend([
        "",
        "Volle HTML-Fassung siehe `amazon_description.html`.",
        "",
    ])
    return lines


def _render_llm_fallback_notice(
    llm_fallback: dict[str, Any] | None,
) -> list[str]:
    """Render a ⚠️ notice when a run leaned on the fallback model.

    The author needs to know when the primary model failed and the
    cheaper / smaller fallback produced the review depth — that way a
    surprisingly mild verdict is contextualized rather than mistaken
    for a free pass. Returns ``[]`` when no fallback was used (the
    silent-success case) so the summary stays clean.
    """

    if not llm_fallback:
        return []
    if not bool(llm_fallback.get("fallback_used")):
        return []
    primary = str(llm_fallback.get("primary_model") or "").strip()
    fallback = str(llm_fallback.get("fallback_model") or "").strip()
    fallback_calls = int(llm_fallback.get("fallback_calls") or 0)
    total_calls = int(llm_fallback.get("total_calls") or 0)
    if fallback_calls <= 0:
        return []

    lines: list[str] = [
        "## ⚠️ Modell-Fallback aktiv",
        "",
        (
            "Das primäre Bewertungs-Modell war für mindestens einen Aufruf"
            " in dieser Runde nicht erreichbar. Der Agent hat automatisch"
            " auf das Fallback-Modell umgestellt — die Tiefe der Review"
            " ist dadurch niedriger als bei einer normalen Runde."
        ),
        "",
    ]
    if primary and fallback:
        lines.append(f"- **Primär:** `{primary}` (fehlgeschlagen)")
        lines.append(f"- **Fallback:** `{fallback}` (eingesprungen)")
    elif fallback:
        lines.append(f"- **Fallback:** `{fallback}` (eingesprungen)")
    if total_calls > 0:
        lines.append(
            f"- **Aufrufe in dieser Runde:** {fallback_calls} via Fallback,"
            f" {total_calls} insgesamt."
        )
    lines.extend([
        "",
        (
            "Empfehlung: Starte vor der nächsten KDP-Veröffentlichung eine"
            " weitere Runde, wenn das primäre Modell wieder erreichbar ist."
        ),
        "",
    ])
    return lines


def _render_persona_match_highlight(
    persona_match: dict[str, Any] | None,
) -> list[str]:
    """Render the persona vs. description match-score highlight.

    Surfaces the aggregate persona-match score (computed in
    ``modules.persona_match``) directly in beginner_summary so the
    author sees at a glance whether their Amazon description targets
    the personas the agent identified. Returns an empty list when no
    payload is provided, when there are no personas with measurable
    tokens, or when the description is missing — surfacing a zero
    score with no context would be misleading.
    """

    if not persona_match:
        return []
    description_present = bool(persona_match.get("description_present"))
    total_personas = int(persona_match.get("total_personas") or 0)
    if total_personas <= 0:
        return []
    overall_score = int(persona_match.get("overall_score") or 0)
    status = str(persona_match.get("status") or "").strip()
    badge, _ = score_badge(overall_score)

    lines: list[str] = [
        "## Persona-Match",
        "",
    ]
    if not description_present:
        lines.extend([
            "Keine Amazon-Beschreibung in den Metadaten — Match nicht messbar.",
            "Trage die Beschreibung in `metadata.md` ein, dann erscheint hier ein Score.",
            "",
        ])
        return lines

    status_text = f" ({status})" if status else ""
    lines.append(
        f"{badge} **{overall_score}/100**{status_text}"
        " — wie stark die ersten drei Beschreibungs-Zeilen die Persona-Anker treffen."
    )

    weakest_label = str(persona_match.get("weakest_label") or "").strip()
    weakest_score = persona_match.get("weakest_score")
    weakest_missing = persona_match.get("weakest_missing") or []
    if weakest_label and isinstance(weakest_score, int):
        missing_text = ", ".join(str(token) for token in list(weakest_missing)[:3])
        if missing_text:
            lines.append(
                f"- Schwächste Persona: **{weakest_label}** ({weakest_score}/100)"
                f" — fehlen z.B.: {missing_text}"
            )
        else:
            lines.append(
                f"- Schwächste Persona: **{weakest_label}** ({weakest_score}/100)"
            )
    lines.extend([
        "",
        "Detail-Tabelle siehe `buyer_personas.md` (Match-Score-Sektion).",
        "",
    ])
    return lines


def _render_top_positioning(
    top_positioning: dict[str, Any] | None,
) -> list[str]:
    """Render the 'Positionierung' block from a top-positioning payload.

    Surfaces the strongest differentiation angle plus the one-sentence
    pitch directly in beginner_summary so the author sees what makes
    the book unique without opening ``competitive_positioning.md``.

    The dict is expected to carry ``angle_claim``, ``angle_evidence``,
    ``angle_strength``, ``pitch``, ``niche_label``, ``niche_confidence``
    and ``audience``. Returns an empty list when no payload is provided
    or when the pitch text is empty — keeping the summary clean when
    the metadata gives no positioning signal.
    """

    if not top_positioning:
        return []
    pitch = str(top_positioning.get("pitch") or "").strip()
    claim = str(top_positioning.get("angle_claim") or "").strip()
    if not pitch and not claim:
        return []
    strength = int(top_positioning.get("angle_strength") or 0)
    evidence = str(top_positioning.get("angle_evidence") or "").strip()
    niche_label = str(top_positioning.get("niche_label") or "").strip()
    niche_confidence = int(top_positioning.get("niche_confidence") or 0)
    audience = str(top_positioning.get("audience") or "").strip()
    badge, _ = score_badge(strength)

    lines: list[str] = [
        "## Positionierung",
        "",
        (
            "Das ist dein Differenzierungs-Hebel gegenüber den typischen "
            "Wettbewerbern in der Nische — kannst du in die Amazon-Beschreibung "
            "übernehmen."
        ),
        "",
    ]
    if niche_label:
        if niche_confidence:
            lines.append(f"- **Nische:** {niche_label} (Konfidenz: {niche_confidence}/100)")
        else:
            lines.append(f"- **Nische:** {niche_label}")
    if audience:
        lines.append(f"- **Zielgruppe:** {audience}")
    if claim:
        lines.append(f"- {badge} **Stärkster Angle:** {claim} (Stärke: {strength}/100)")
        if evidence:
            lines.append(f"  - Beleg: {evidence}")
    additional = top_positioning.get("additional_angles") or []
    for extra in additional:
        if not isinstance(extra, dict):
            continue
        extra_claim = str(extra.get("angle_claim") or "").strip()
        if not extra_claim:
            continue
        extra_strength = int(extra.get("angle_strength") or 0)
        extra_evidence = str(extra.get("angle_evidence") or "").strip()
        extra_badge, _ = score_badge(extra_strength)
        lines.append(
            f"- {extra_badge} **Weiterer Angle:** {extra_claim} "
            f"(Stärke: {extra_strength}/100)"
        )
        if extra_evidence:
            lines.append(f"  - Beleg: {extra_evidence}")
    if pitch:
        lines.extend([
            "",
            "**Positionierungs-Satz:**",
            "",
            f"> {pitch}",
        ])
    lines.extend([
        "",
        "Wettbewerber-Archetypen und Kollisions-Risiken siehe `competitive_positioning.md`.",
        "",
    ])
    return lines


def _render_top_collision_risk(
    top_collision_risk: dict[str, Any] | None,
) -> list[str]:
    """Render the top positioning collision-risk warning block.

    Surfaces the single highest-priority collision risk from
    ``competitive_positioning.json`` directly in beginner_summary so the
    author sees what would make the book hard to distinguish from
    generic competitors — placed right under the positioning pitch so
    the warning lands next to the claim the author would otherwise
    paste into the Amazon description.

    The dict is expected to carry ``risk``, ``niche_label``,
    ``niche_confidence`` and ``total_risks``. Returns an empty list when
    no payload is provided or the risk text is whitespace-only — keeping
    the summary clean when the metadata gives no collision signal.
    """

    if not top_collision_risk:
        return []
    risk = str(top_collision_risk.get("risk") or "").strip()
    if not risk:
        return []
    niche_label = str(top_collision_risk.get("niche_label") or "").strip()
    total_risks = int(top_collision_risk.get("total_risks") or 0)
    lines: list[str] = [
        "## ⚠️ Kollisions-Risiko",
        "",
        (
            "Das größte Risiko, dass dein Buch in der Nische untergeht — "
            "fixe das, bevor du den Pitch oben ins KDP-Backend kopierst."
        ),
        "",
        f"- {SCORE_BADGE_FIX} {risk}",
    ]
    if niche_label:
        lines.append(f"- **Nische:** {niche_label}")
    if total_risks > 1:
        remaining = total_risks - 1
        plural = "weitere Risiken" if remaining != 1 else "weiteres Risiko"
        lines.append(f"- Außerdem {remaining} {plural} im vollen Report.")
    lines.extend([
        "",
        "Alle Kollisions-Risiken siehe `competitive_positioning.md`.",
        "",
    ])
    return lines


def render_beginner_summary(
    project: BookProject,
    report: dict[str, Any],
    weakest_chapters: list[dict[str, Any]] | None = None,
    weakest_sample: dict[str, Any] | None = None,
    weakest_samples: list[dict[str, Any]] | None = None,
    top_rewrite: dict[str, Any] | None = None,
    round_delta_highlight: dict[str, Any] | None = None,
    score_history_highlight: dict[str, Any] | None = None,
    top_kdp_keywords: list[dict[str, Any]] | None = None,
    top_positioning: dict[str, Any] | None = None,
    top_collision_risk: dict[str, Any] | None = None,
    top_persona: dict[str, Any] | None = None,
    top_arc: dict[str, Any] | None = None,
    top_chapter_balance: dict[str, Any] | None = None,
    persona_match: dict[str, Any] | None = None,
    llm_fallback: dict[str, Any] | None = None,
    amazon_html_preview: dict[str, Any] | None = None,
) -> str:
    decision = str(report.get("decision", "HOLD"))
    light, plain_decision = _traffic_light(decision)
    gates = _gate_lookup(report)
    profile = report.get("docx_profile", {})
    fixes = report.get("all_required_fixes", [])

    industrial_score = report.get("industrial_score")
    overall_badge = (
        score_badge(int(industrial_score))[0]
        if isinstance(industrial_score, (int, float))
        else ""
    )

    lines = [
        "# Einfache Buch-Pruefung",
        "",
        f"Buch: **{project.title or project.project_id}**",
        f"Ampel: **{light}**",
        f"Ergebnis: {plain_decision}",
        f"Score: {overall_badge} **{industrial_score if industrial_score is not None else 'n/a'}/100**",
        "",
    ]
    lines.extend(_render_gate_overview(report))
    lines.extend([
        "## Was ist schon gut?",
        "",
    ])

    positives: list[str] = []
    if gates.get("asset_completeness", {}).get("status") == "READY":
        positives.append("Alle wichtigen Dateien sind da: Manuskript, Cover, Titel, Autor und Amazon-Beschreibung.")
    if gates.get("metadata_and_storefront", {}).get("status") == "READY":
        positives.append("Die Amazon-Seite ist grundsaetzlich vorbereitet: Beschreibung, Keywords und Kategorien sind vorhanden.")
    if gates.get("amazon_sellability", {}).get("status") == "READY":
        positives.append("Das Buch hat eine klare Zielgruppe, konkrete Beweise und eine verkaufbare Positionierung.")
    if gates.get("production_package", {}).get("score", 0) >= 85:
        positives.append("Cover-Format und Produktionspaket sehen technisch gut aus.")
    if profile.get("heading_count", 0) >= 8:
        positives.append("Das Manuskript hat genug Ueberschriften fuer Kindle-Navigation und Lesbarkeit.")

    lines.extend(f"- {item}" for item in positives or ["Die Grundstruktur wurde erkannt und kann geprueft werden."])

    lines.extend(["", "## Was musst du jetzt tun?", ""])
    if fixes:
        for idx, fix in enumerate(fixes, start=1):
            lines.extend([
                f"{idx}. {_plain_fix(fix)}",
                f"   Betroffene Datei: **{_affected_file_for_fix(fix)}**",
            ])
    else:
        lines.append("Nichts Blockierendes. Mache nur noch eine menschliche Endkontrolle.")

    lines.append("")
    lines.extend(_render_llm_fallback_notice(llm_fallback))
    lines.extend(_render_round_delta_highlight(round_delta_highlight))
    lines.extend(_render_score_history_highlight(score_history_highlight))
    lines.extend(_render_weakest_chapters(weakest_chapters))
    lines.extend(_render_top_chapter_balance(top_chapter_balance))
    # Prefer the list parameter when callers pass it; fall back to the
    # legacy single-dict form so older call sites and tests keep working.
    if weakest_samples is not None:
        lines.extend(_render_weakest_samples(weakest_samples))
    else:
        lines.extend(_render_weakest_sample(weakest_sample))
    lines.extend(_render_top_arc(top_arc))
    lines.extend(_render_top_persona(top_persona))
    lines.extend(_render_persona_match_highlight(persona_match))
    lines.extend(_render_top_positioning(top_positioning))
    lines.extend(_render_top_collision_risk(top_collision_risk))
    lines.extend(_render_top_rewrite(top_rewrite))
    lines.extend(_render_top_kdp_keywords(top_kdp_keywords))
    lines.extend(_render_amazon_html_preview(amazon_html_preview))

    lines.extend([
        "## Was bedeutet das praktisch?",
        "",
    ])
    if decision == "GO":
        lines.append("Du kannst den KDP-Upload vorbereiten. Pruefe trotzdem einmal die Kindle-Vorschau und die Amazon-Produktseite.")
    elif decision == "GO_AFTER_FIXES":
        lines.append("Du bist nah an der Veroeffentlichung. Arbeite die Punkte oben ab, speichere die Dateien, und starte danach die naechste Pruefrunde.")
    else:
        lines.append("Bitte zuerst die fehlenden oder blockierenden Dateien korrigieren. Danach eine neue Pruefrunde starten.")

    lines.extend([
        "",
        "## Erkannte Dateien",
        "",
        f"- Manuskript: `{project.manuscript or 'FEHLT'}`",
        f"- Cover: `{project.cover or 'FEHLT'}`",
        f"- Metadaten-Dateien: {len(project.metadata_files)}",
        "",
        "## Naechster Klick",
        "",
        "Nach deinen Anpassungen wieder **Pruefrunde starten** klicken.",
    ])
    return "\n".join(lines)


def render_industrial_qa_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Industrial Publisher QA",
        "",
        f"Project: `{report['project_id']}`",
        f"Decision: **{report['decision']}**",
        f"Industrial score: **{report['industrial_score']}/100**",
        f"Investor grade: **{report['investor_grade']}/10**",
        "",
    ]
    agent_context = report.get("agent_context") or {}
    skills = agent_context.get("skills") or []
    memory = agent_context.get("memory") or {}
    if skills or memory:
        lines.extend(["## Agent System", ""])
        if skills:
            lines.append("Loaded skills:")
            lines.extend(f"- {skill.get('name', 'unknown')}: {skill.get('purpose', 'n/a')}" for skill in skills)
            lines.append("")
        project_memory = memory.get("project_memory") or {}
        if project_memory.get("rounds"):
            latest = project_memory["rounds"][-1]
            lines.extend([
                "Memory:",
                f"- Previous decision: {latest.get('decision', 'n/a')}",
                f"- Previous industrial score: {latest.get('industrial_score', 'n/a')}",
                "",
            ])
    lines.extend(_render_gate_overview_table(report))
    lines.extend([
        "## Gates",
        "",
    ])
    for gate in report["gates"]:
        score = int(gate.get("score") or 0)
        explicit_fix = gate.get("status") == "FIX" and score >= SCORE_REVIEW_THRESHOLD
        badge, _ = score_badge(score, blocking=explicit_fix)
        lines.extend([
            f"### {badge} {gate['name']} - {gate['status']} ({gate['score']}/100)",
            "",
            "Findings:",
            *(f"- {item}" for item in gate["findings"]),
            "",
        ])
        if gate["fixes"]:
            lines.extend(["Required fixes:", *(f"- {item}" for item in gate["fixes"]), ""])

    profile = report["docx_profile"]
    if profile.get("available"):
        lines.extend([
            "## Kindle Profile",
            "",
            f"- Word count: {profile['word_count']}",
            f"- Paragraphs: {profile['paragraph_count']}",
            f"- Headings: {profile['heading_count']}",
            f"- Tables: {profile['table_count']}",
            f"- Inline images: {profile['inline_shape_count']}",
            f"- Average body paragraph words: {profile['average_body_paragraph_words']}",
            f"- Long paragraph ratio: {profile['long_paragraph_ratio']}",
            f"- TOC detected in manuscript text: {profile['toc_detected']}",
            "",
        ])
    if report["all_required_fixes"]:
        lines.extend([
            "## Release-Critical Fixes",
            "",
            *(f"{idx}. {item}" for idx, item in enumerate(report["all_required_fixes"], start=1)),
            "",
        ])
    lines.extend([
        "## Machine JSON",
        "",
        "```json",
        json.dumps(
            {
                "decision": report["decision"],
                "industrial_score": report["industrial_score"],
                "investor_grade": report["investor_grade"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
    ])
    return "\n".join(lines)
