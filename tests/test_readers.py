"""Tests for the user-facing error wrapping in modules.readers.

The goal: when a customer downloads BookPublisher and runs it on a
broken / missing / wrong-format file, they must see a clear German
message with a concrete next step - not a Python traceback.
"""

from __future__ import annotations

import sys
import types
import zipfile
from pathlib import Path

import pytest

from modules.readers import (
    ManuscriptReadError,
    open_docx_paragraphs,
    read_any_text,
    read_docx_text,
    read_pdf_text,
    read_text_file,
)
from tests.helpers import runtime_dir


def _write_broken_docx(workspace: Path, name: str = "broken.docx") -> Path:
    """Create a 'docx' file that is not a valid ZIP package."""
    path = workspace / name
    path.write_bytes(b"this is definitely not a docx package")
    return path


def _write_bad_zip_docx(workspace: Path, name: str = "bad_zip.docx") -> Path:
    """Create a file with a zip-ish header but corrupted content."""
    path = workspace / name
    path.write_bytes(b"PK\x03\x04corrupted-content-not-a-real-docx")
    return path


def test_manuscript_read_error_has_user_friendly_german_message():
    workspace = runtime_dir("readers_msg")
    error = ManuscriptReadError(
        workspace / "missing.docx",
        reason="Test-Grund.",
        hint="Test-Hinweis.",
    )

    message = str(error)
    assert "Datei konnte nicht gelesen werden" in message
    assert "missing.docx" in message
    assert "Test-Grund." in message
    assert "Test-Hinweis." in message
    assert "Traceback" not in message
    assert "Exception" not in message


def test_manuscript_read_error_preserves_path_attribute():
    target = Path("does/not/exist.docx")
    error = ManuscriptReadError(target, reason="X", hint="Y")
    assert error.path == target
    assert error.reason == "X"
    assert error.hint == "Y"


def test_read_docx_text_raises_friendly_error_for_missing_file():
    workspace = runtime_dir("readers_missing")
    missing = workspace / "no_such_book.docx"

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_docx_text(missing)

    message = str(exc_info.value)
    assert "no_such_book.docx" in message
    assert "nicht gefunden" in message.lower()
    assert "buchordner" in message.lower()


def test_read_docx_text_raises_friendly_error_for_directory_input():
    workspace = runtime_dir("readers_dir")

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_docx_text(workspace)

    message = str(exc_info.value)
    assert "ordner" in message.lower()
    assert ".docx" in message


def test_read_docx_text_raises_friendly_error_for_corrupt_docx():
    workspace = runtime_dir("readers_broken")
    broken = _write_broken_docx(workspace)

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_docx_text(broken)

    message = str(exc_info.value)
    assert "broken.docx" in message
    assert "word" in message.lower() or "docx" in message.lower()
    assert "Traceback" not in message


def test_read_docx_text_raises_friendly_error_for_bad_zip():
    workspace = runtime_dir("readers_bad_zip")
    bad = _write_bad_zip_docx(workspace)

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_docx_text(bad)

    message = str(exc_info.value)
    assert "bad_zip.docx" in message
    assert ".docx" in message


def test_open_docx_paragraphs_uses_same_error_wrapping():
    workspace = runtime_dir("readers_open_docx")
    broken = _write_broken_docx(workspace, name="corrupted.docx")

    with pytest.raises(ManuscriptReadError) as exc_info:
        open_docx_paragraphs(broken)

    assert "corrupted.docx" in str(exc_info.value)


def test_read_any_text_routes_docx_through_friendly_error():
    workspace = runtime_dir("readers_any_docx")
    broken = _write_broken_docx(workspace, name="any_docx.docx")

    with pytest.raises(ManuscriptReadError):
        read_any_text(broken)


def test_read_text_file_raises_friendly_error_for_missing_file():
    workspace = runtime_dir("readers_text_missing")
    missing = workspace / "no_notes.md"

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_text_file(missing)

    assert "no_notes.md" in str(exc_info.value)
    assert "nicht gefunden" in str(exc_info.value).lower()


def test_read_text_file_returns_content_for_valid_file():
    workspace = runtime_dir("readers_text_ok")
    path = workspace / "notes.md"
    path.write_text("# Headline\n\nContent.", encoding="utf-8")

    assert read_text_file(path) == "# Headline\n\nContent."


def test_read_pdf_text_raises_friendly_error_for_missing_file():
    workspace = runtime_dir("readers_pdf_missing")
    missing = workspace / "no_book.pdf"

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_pdf_text(missing)

    assert "no_book.pdf" in str(exc_info.value)
    assert "pdf" in str(exc_info.value).lower()


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self, _mode: str) -> str:
        return self._text


class _FakeDocument:
    """Mimics the subset of a PyMuPDF document the reader touches."""

    def __init__(self, pages: list[str]) -> None:
        self._pages = [_FakePage(text) for text in pages]
        self.closed = False

    def __iter__(self):
        return iter(self._pages)

    def close(self) -> None:
        self.closed = True


def _install_fake_fitz(monkeypatch: pytest.MonkeyPatch, *, opener) -> dict:
    """Inject a fake `fitz` module so reader tests need no real PDF binary.

    Returns a mutable state dict the caller can inspect after the call
    (e.g. to assert the document was closed)."""
    state: dict = {"opened": None}
    module = types.ModuleType("fitz")

    def _open(_path):  # noqa: ANN001 - mirrors fitz.open signature
        doc = opener(_path)
        state["opened"] = doc
        return doc

    module.open = _open  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", module)
    return state


def test_read_pdf_text_raises_friendly_error_for_directory_input(monkeypatch):
    workspace = runtime_dir("readers_pdf_dir")
    _install_fake_fitz(monkeypatch, opener=lambda _p: _FakeDocument(["irrelevant"]))

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_pdf_text(workspace)

    message = str(exc_info.value).lower()
    assert "ordner" in message
    assert ".pdf" in message
    assert "Traceback" not in str(exc_info.value)


def test_read_pdf_text_returns_joined_pages_and_closes_document(monkeypatch):
    workspace = runtime_dir("readers_pdf_ok")
    pdf = workspace / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")
    state = _install_fake_fitz(
        monkeypatch,
        opener=lambda _p: _FakeDocument(["Seite eins", "Seite zwei"]),
    )

    result = read_pdf_text(pdf)

    assert result == "Seite eins\nSeite zwei"
    assert state["opened"].closed is True


def test_read_pdf_text_respects_max_pages_and_closes(monkeypatch):
    workspace = runtime_dir("readers_pdf_maxpages")
    pdf = workspace / "long.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")
    state = _install_fake_fitz(
        monkeypatch,
        opener=lambda _p: _FakeDocument([f"S{i}" for i in range(10)]),
    )

    result = read_pdf_text(pdf, max_pages=3)

    assert result == "S0\nS1\nS2"
    assert state["opened"].closed is True


def test_read_pdf_text_wraps_open_failure_as_friendly_error(monkeypatch):
    workspace = runtime_dir("readers_pdf_open_fail")
    pdf = workspace / "corrupt.pdf"
    pdf.write_bytes(b"not a real pdf")

    def _boom(_path):
        raise RuntimeError("fitz internal failure")

    _install_fake_fitz(monkeypatch, opener=_boom)

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_pdf_text(pdf)

    message = str(exc_info.value)
    assert "corrupt.pdf" in message
    assert "beschaedigt" in message.lower()
    assert "Traceback" not in message


def test_read_pdf_text_wraps_page_read_failure_and_closes(monkeypatch):
    workspace = runtime_dir("readers_pdf_page_fail")
    pdf = workspace / "page_fail.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")

    class _FailingPage:
        def get_text(self, _mode: str) -> str:
            raise RuntimeError("page decode error")

    class _FailingDocument(_FakeDocument):
        def __init__(self) -> None:
            super().__init__([])
            self._pages = [_FailingPage()]

    state = _install_fake_fitz(monkeypatch, opener=lambda _p: _FailingDocument())

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_pdf_text(pdf)

    message = str(exc_info.value)
    assert "page_fail.pdf" in message
    assert "seiten" in message.lower()
    assert state["opened"].closed is True
    assert "Traceback" not in message


def test_read_pdf_text_does_not_swallow_manuscript_read_error(monkeypatch):
    """A ManuscriptReadError raised mid-read must propagate unchanged,
    not be re-wrapped by the broad except block."""
    workspace = runtime_dir("readers_pdf_passthrough")
    pdf = workspace / "passthrough.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")

    sentinel_hint = "EINDEUTIGER-SENTINEL-HINWEIS"

    class _RaisingPage:
        def get_text(self, _mode: str) -> str:
            raise ManuscriptReadError(pdf, reason="Innerer Fehler.", hint=sentinel_hint)

    class _RaisingDocument(_FakeDocument):
        def __init__(self) -> None:
            super().__init__([])
            self._pages = [_RaisingPage()]

    state = _install_fake_fitz(monkeypatch, opener=lambda _p: _RaisingDocument())

    with pytest.raises(ManuscriptReadError) as exc_info:
        read_pdf_text(pdf)

    assert exc_info.value.hint == sentinel_hint
    assert state["opened"].closed is True


def test_read_pdf_text_missing_file_check_runs_before_open(monkeypatch):
    """The not-exists branch must fire even with a working fitz, so the
    customer never reaches a confusing fitz error for a missing path."""
    workspace = runtime_dir("readers_pdf_missing_order")
    missing = workspace / "ghost.pdf"
    state = _install_fake_fitz(monkeypatch, opener=lambda _p: _FakeDocument(["x"]))

    with pytest.raises(ManuscriptReadError):
        read_pdf_text(missing)

    assert state["opened"] is None  # fitz.open never reached


def test_chapters_module_propagates_friendly_error():
    """A broken DOCX should not raise PackageNotFoundError through the
    chapter extractor - the customer must see the wrapped error."""
    from modules.chapters import extract_docx_chapters

    workspace = runtime_dir("readers_chapters")
    broken = _write_broken_docx(workspace, name="chapter_broken.docx")

    with pytest.raises(ManuscriptReadError) as exc_info:
        extract_docx_chapters(broken)

    assert "chapter_broken.docx" in str(exc_info.value)


def test_industrial_qa_propagates_friendly_error_for_corrupt_docx():
    """analyze_docx_structure must surface ManuscriptReadError so the
    pipeline can stop with a clear message instead of a traceback."""
    from modules.industrial import analyze_docx_structure

    workspace = runtime_dir("readers_industrial")
    broken = _write_broken_docx(workspace, name="industrial_broken.docx")

    with pytest.raises(ManuscriptReadError) as exc_info:
        analyze_docx_structure(broken)

    assert "industrial_broken.docx" in str(exc_info.value)


def test_industrial_qa_returns_unavailable_for_no_manuscript():
    """No manuscript at all (None) is a normal state, not an error."""
    from modules.industrial import analyze_docx_structure

    result = analyze_docx_structure(None)
    assert result == {"available": False}


def test_sample_scan_propagates_friendly_error_for_corrupt_docx():
    """Sample-scan must use the same friendly-error pipeline."""
    from modules.discovery import BookProject
    from modules.sample_scan import build_sample_scan_report

    workspace = runtime_dir("readers_sample_scan")
    broken = _write_broken_docx(workspace, name="sample_broken.docx")
    project = BookProject(
        project_id="broken_sample",
        root=workspace,
        manuscript=broken,
    )

    with pytest.raises(ManuscriptReadError) as exc_info:
        build_sample_scan_report(project)

    assert "sample_broken.docx" in str(exc_info.value)


def test_friendly_error_includes_actionable_hint():
    """Every error path must end with a concrete next step the customer can take."""
    workspace = runtime_dir("readers_hints")

    cases: list[tuple[Path, str]] = [
        (workspace / "missing.docx", "buchordner"),
        (workspace, "ordner"),
    ]
    broken = _write_broken_docx(workspace, name="corrupted_hint.docx")
    cases.append((broken, "word"))

    for path, expected_hint_keyword in cases:
        with pytest.raises(ManuscriptReadError) as exc_info:
            read_docx_text(path)
        message = str(exc_info.value).lower()
        assert "so loest du das" in message
        assert expected_hint_keyword in message
