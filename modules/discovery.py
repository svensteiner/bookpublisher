from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from modules.artifacts import safe_slug
from modules.readers import read_any_text, read_text_file


@dataclass
class BookProject:
    project_id: str
    root: Path
    manuscript: Path | None = None
    cover: Path | None = None
    title: str | None = None
    subtitle: str | None = None
    author: str | None = None
    amazon_description: str | None = None
    metadata_files: list[Path] = field(default_factory=list)
    notes_files: list[Path] = field(default_factory=list)
    pdf_files: list[Path] = field(default_factory=list)
    cover_files: list[Path] = field(default_factory=list)
    zip_files: list[Path] = field(default_factory=list)
    missing_assets: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, Path):
                payload[key] = str(value)
            elif isinstance(value, list):
                payload[key] = [str(item) if isinstance(item, Path) else item for item in value]
        return payload


def _is_skipped(path: Path, skip_dirs: set[str]) -> bool:
    return any(part.lower() in skip_dirs for part in path.parts)


def collect_files(root: Path, skip_dirs: set[str]) -> list[Path]:
    if not root.exists():
        return []
    files = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part.lower() in skip_dirs for part in relative_parts[:-1]):
            continue
        files.append(path)
    return files


def _is_supplemental_text(path: Path) -> bool:
    if path.suffix.lower() not in {".md", ".txt"}:
        return False
    name = path.name.lower()
    if any(marker in name for marker in ("backup", "vor_final", "vor_second", "endversion.md")):
        return False
    return any(
        marker in name
        for marker in (
            "amazon",
            "beschreibung",
            "metadata",
            "metadaten",
            "notes",
            "notizen",
            "titel",
            "untertitel",
            "voice",
            "readme",
        )
    )


def collect_supplemental_text_files(root: Path, supplemental_dirs: set[str]) -> list[Path]:
    if not root.exists() or not supplemental_dirs:
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        relative_parts = [part.lower() for part in path.relative_to(root).parts[:-1]]
        if not any(part in supplemental_dirs for part in relative_parts):
            continue
        if _is_supplemental_text(path):
            files.append(path)
    return files


def _direct_book_files(root: Path, supported_exts: set[str]) -> list[Path]:
    return [
        path for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in supported_exts
    ]


def detect_project_roots(input_path: Path, skip_dirs: set[str], supported_exts: set[str]) -> list[Path]:
    roots: list[Path] = []
    if not input_path.exists():
        return roots

    if _direct_book_files(input_path, supported_exts):
        roots.append(input_path)

    for child in input_path.iterdir():
        if not child.is_dir() or child.name.lower() in skip_dirs:
            continue
        if any(p.suffix.lower() in supported_exts for p in collect_files(child, skip_dirs)):
            roots.append(child)

    if not roots and any(p.suffix.lower() in supported_exts for p in collect_files(input_path, skip_dirs)):
        roots.append(input_path)

    return roots


def _choose_manuscript(files: Iterable[Path]) -> Path | None:
    candidates = [p for p in files if p.suffix.lower() == ".docx" and not p.name.startswith("~$")]
    if not candidates:
        return None
    def score(path: Path) -> tuple[int, int]:
        name = path.name.lower()
        points = 0
        if "korrigiert" in name:
            points += 100
        if "endversion" in name:
            points += 50
        if "final" in name:
            points += 30
        return points, path.stat().st_size
    return sorted(candidates, key=score, reverse=True)[0]


def _choose_cover(files: Iterable[Path]) -> Path | None:
    candidates = [p for p in files if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
    if not candidates:
        return None
    def score(path: Path) -> tuple[int, int]:
        name = path.name.lower()
        points = 0
        if "cover_kdp_final" in name:
            points += 100
        if "cover" in name:
            points += 50
        if "preview" in name:
            points -= 100
        return points, path.stat().st_size
    return sorted(candidates, key=score, reverse=True)[0]


def _extract_description(text: str) -> str | None:
    match = re.search(r"##\s+Amazon Beschreibung\s*(.*?)(?:\n##\s+|\Z)", text, flags=re.S | re.I)
    if match:
        return match.group(1).strip()
    return None


def _clean_author(line: str) -> str:
    value = re.sub(r"^(von|autor|by)\s*:?\s*", "", line, flags=re.I).strip()
    value = re.sub(r"copyright\s*[©(c)]*\s*\d{4}", "", value, flags=re.I).strip(" .")
    value = re.sub(r"alle rechte vorbehalten\.?", "", value, flags=re.I).strip(" .")
    # Extract name: optional academic title + capitalized word sequence
    match = re.search(
        r"((?:(?:Mag|Dr|Prof|Dipl\.[-\w]*|M\.?\s?Sc|B\.?\s?Sc)\.\s+)?[A-ZÄÖÜ][a-zäöüß]+"
        r"(?:\s+[A-ZÄÖÜ][a-zäöüß]+)+)",
        value,
    )
    if match:
        return match.group(1).strip()
    return value


def _extract_metadata_from_text(text: str) -> dict[str, Any]:
    lines = [line.strip("#* \t") for line in text.splitlines() if line.strip()]
    title = lines[0] if lines else None
    subtitle = None
    author = None

    for line in lines[1:10]:
        lower = line.lower()
        if not subtitle and len(line) > 8 and not lower.startswith(("von ", "autor", "copyright", "ki-hinweis")):
            subtitle = line
        if "von " in lower or lower.startswith("autor") or re.search(r"\b(mag|dr|prof|by)\b", lower):
            author = _clean_author(line)

    title_match = re.search(r"##\s+KDP Titel\s*\n+\s*\**(.+?)\**\s*(?:\n|$)", text, flags=re.I)
    subtitle_match = re.search(r"##\s+KDP Untertitel\s*\n+\s*\**(.+?)\**\s*(?:\n|$)", text, flags=re.I)
    author_match = re.search(r"##\s+KDP Autor\s*\n+\s*\**(.+?)\**\s*(?:\n|$)", text, flags=re.I)
    description_match = re.search(r"##\s+Amazon Beschreibung", text, flags=re.I)
    if title_match:
        title = title_match.group(1).strip("* ")
    if subtitle_match:
        subtitle = subtitle_match.group(1).strip("* ")
    if author_match:
        author = _clean_author(author_match.group(1))

    return {
        "title": title,
        "subtitle": subtitle,
        "author": author,
        "amazon_description": _extract_description(text),
        "_explicit": {
            "title": bool(title_match),
            "subtitle": bool(subtitle_match),
            "author": bool(author_match),
            "amazon_description": bool(description_match),
        },
    }


def discover_books(
    input_path: Path,
    skip_dirs: set[str],
    supported_files: dict[str, list[str]],
    supplemental_text_directories: set[str] | None = None,
) -> list[BookProject]:
    supported_exts = {ext for exts in supported_files.values() for ext in exts}
    roots = detect_project_roots(input_path, skip_dirs, supported_exts)
    projects: list[BookProject] = []

    for root in roots:
        files = collect_files(root, skip_dirs)
        manuscript = _choose_manuscript(files)
        cover = _choose_cover(files)
        production_text_files = [p for p in files if p.suffix.lower() in {".md", ".txt"}]
        supplemental_text_files = collect_supplemental_text_files(root, supplemental_text_directories or set())
        text_files = sorted(set(production_text_files + supplemental_text_files), key=lambda p: str(p).lower())
        pdf_files = [p for p in files if p.suffix.lower() == ".pdf"]
        zip_files = [p for p in files if p.suffix.lower() == ".zip"]
        cover_files = [p for p in files if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]

        project_id = safe_slug(root.name if root != input_path else (manuscript.stem if manuscript else root.name))
        metadata: dict[str, str | None] = {
            "title": None,
            "subtitle": None,
            "author": None,
            "amazon_description": None,
        }
        explicit_set: set[str] = set()

        def _merge(extracted: dict[str, Any]) -> None:
            explicit_flags = extracted.get("_explicit", {}) or {}
            for key in ("title", "subtitle", "author", "amazon_description"):
                value = extracted.get(key)
                if not value:
                    continue
                is_explicit = bool(explicit_flags.get(key))
                if not metadata.get(key):
                    metadata[key] = value
                    if is_explicit:
                        explicit_set.add(key)
                elif is_explicit and key not in explicit_set:
                    metadata[key] = value
                    explicit_set.add(key)

        for path in text_files:
            try:
                text = read_text_file(path)
            except OSError:
                continue
            _merge(_extract_metadata_from_text(text))

        if manuscript:
            try:
                _merge(_extract_metadata_from_text(read_any_text(manuscript)[:5000]))
            except Exception:
                pass

        project = BookProject(
            project_id=project_id,
            root=root,
            manuscript=manuscript,
            cover=cover,
            title=metadata["title"],
            subtitle=metadata["subtitle"],
            author=metadata["author"],
            amazon_description=metadata["amazon_description"],
            metadata_files=[p for p in text_files if "metadata" in p.name.lower() or "beschreibung" in p.name.lower()],
            notes_files=[p for p in text_files],
            pdf_files=pdf_files,
            cover_files=cover_files,
            zip_files=zip_files,
        )
        if not project.manuscript:
            project.missing_assets.append("manuscript_docx")
        if not project.cover:
            project.missing_assets.append("cover_image")
        if not project.amazon_description:
            project.missing_assets.append("amazon_description")
        projects.append(project)

    return projects


def render_discovery_markdown(projects: list[BookProject], input_path: Path) -> str:
    lines = [
        "# Discovery Report",
        "",
        f"Input path: `{input_path}`",
        f"Detected projects: **{len(projects)}**",
        "",
    ]
    if not projects:
        lines.append("No book projects were detected.")
        return "\n".join(lines)

    for project in projects:
        lines.extend([
            f"## {project.project_id}",
            "",
            f"- Root: `{project.root}`",
            f"- Manuscript: `{project.manuscript}`" if project.manuscript else "- Manuscript: MISSING",
            f"- Cover: `{project.cover}`" if project.cover else "- Cover: MISSING",
            f"- Title: {project.title or 'Unknown'}",
            f"- Subtitle: {project.subtitle or 'Unknown'}",
            f"- Author: {project.author or 'Unknown'}",
            f"- Metadata files: {len(project.metadata_files)}",
            f"- Notes/text files: {len(project.notes_files)}",
            f"- PDFs: {len(project.pdf_files)}",
            f"- ZIP exports: {len(project.zip_files)}",
            f"- Missing assets: {', '.join(project.missing_assets) if project.missing_assets else 'None'}",
            "",
        ])
    return "\n".join(lines)
