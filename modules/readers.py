from __future__ import annotations

import zipfile
from pathlib import Path


def read_docx_text(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("python-docx is required to read .docx manuscripts") from exc

    doc = Document(path)
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                parts.append(row_text)
    return "\n".join(parts)


def read_pdf_text(path: Path, max_pages: int = 25) -> str:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required to read .pdf files") from exc

    doc = fitz.open(path)
    texts = []
    for idx, page in enumerate(doc):
        if idx >= max_pages:
            break
        texts.append(page.get_text("text"))
    return "\n".join(texts)


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def describe_zip(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        return {"path": str(path), "entries": names[:200], "entry_count": len(names)}
    except zipfile.BadZipFile:
        return {"path": str(path), "error": "bad_zip_file"}


def read_docx_chapters(path: Path) -> list:
    """Re-export of modules.chapters.extract_docx_chapters for convenience."""

    from modules.chapters import extract_docx_chapters

    return extract_docx_chapters(path)


def read_any_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        return read_docx_text(path)
    if ext == ".pdf":
        return read_pdf_text(path)
    if ext in {".md", ".txt"}:
        return read_text_file(path)
    return ""
