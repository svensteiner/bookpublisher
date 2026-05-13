"""Tests for the error-dialog dispatch in gui.py.

We deliberately do not instantiate ``PublisherGui`` (that requires a Tk
display). Instead we exercise the pure dispatch helper and call the
``_handle_error_event`` method via ``__func__`` with a small stub that
records each side-effect — that way the GUI's error-path is locked in
without a graphical environment, which matches our CI-friendly test
contract (no filesystem, no real Tk).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

import pytest

from modules.config import ConfigError
from modules.readers import ManuscriptReadError


def _manuscript_error(
    *,
    path: str = "C:/buecher/buch.docx",
    reason: str = "Die Datei wurde nicht gefunden.",
    hint: str = "Bitte die DOCX in den Buchordner kopieren.",
) -> ManuscriptReadError:
    return ManuscriptReadError(path, reason, hint)


def _load_gui_module():
    """Import the gui module fresh so monkeypatching ``messagebox`` is safe."""
    import gui  # type: ignore[import-not-found]

    return importlib.reload(gui)


# ─── error_dialog_payload ────────────────────────────────────────────────


def test_error_dialog_payload_routes_manuscript_read_error():
    gui = _load_gui_module()

    exc = _manuscript_error(path="C:/buecher/buch.docx", reason="Datei fehlt.")

    title, message = gui.error_dialog_payload(exc)

    assert title == gui.DIALOG_TITLE_MANUSCRIPT
    assert "buch.docx" in message
    # readable German message reaches the user, not raw type names / traceback
    assert "Grund" in message
    assert "Traceback" not in message


def test_error_dialog_payload_routes_config_error():
    gui = _load_gui_module()

    exc = ConfigError("ANTHROPIC_API_KEY fehlt in .env")

    title, message = gui.error_dialog_payload(exc)

    assert title == gui.DIALOG_TITLE_CONFIG
    assert "ANTHROPIC_API_KEY" in message


def test_error_dialog_payload_falls_back_for_unknown_exception():
    gui = _load_gui_module()

    exc = ValueError("etwas Unerwartetes passierte")

    title, message = gui.error_dialog_payload(exc)

    assert title == gui.DIALOG_TITLE_GENERIC
    assert message == "etwas Unerwartetes passierte"


def test_error_dialog_payload_uses_str_of_exception_verbatim():
    """The customer sees the German message, not a Python repr."""
    gui = _load_gui_module()

    exc = _manuscript_error(reason="Datei beschädigt.", hint="Bitte erneut speichern.")
    expected = str(exc)

    _, rendered = gui.error_dialog_payload(exc)

    assert rendered == expected
    assert "Datei beschädigt." in rendered
    assert "Bitte erneut speichern." in rendered


# ─── _handle_error_event via stub ───────────────────────────────────────


@dataclass
class _StubVar:
    value: str = ""

    def set(self, value: str) -> None:
        self.value = value


@dataclass
class _GuiStub:
    """Mimic the minimal surface ``_handle_error_event`` calls on ``self``."""

    status: _StubVar = field(default_factory=_StubVar)
    report_text: str = ""
    busy_calls: list[bool] = field(default_factory=list)

    def _set_busy(self, busy: bool) -> None:
        self.busy_calls.append(busy)

    def _set_report_text(self, text: str) -> None:
        self.report_text = text


@pytest.fixture
def showerror_recorder(monkeypatch):
    """Patch ``messagebox.showerror`` to capture calls instead of opening Tk."""
    gui_mod = _load_gui_module()
    calls: list[tuple[str, str]] = []

    def _fake_showerror(title: str, message: str, **_: Any) -> None:
        calls.append((title, message))

    monkeypatch.setattr(gui_mod.messagebox, "showerror", _fake_showerror)
    return gui_mod, calls


def _invoke_handle_error(gui_mod, stub: _GuiStub, payload: object) -> None:
    """Call ``PublisherGui._handle_error_event`` against our stub.

    In Python 3 the attribute access ``Cls.method`` returns the raw
    function — no descriptor binding — so calling it with the stub as
    the first argument simulates a bound method without a real Tk root.
    """
    gui_mod.PublisherGui._handle_error_event(stub, payload)


def test_handle_error_event_sets_status_and_clears_busy(showerror_recorder):
    gui_mod, _ = showerror_recorder
    stub = _GuiStub()

    _invoke_handle_error(gui_mod, stub, _manuscript_error())

    assert stub.status.value == "Fehler."
    assert stub.busy_calls == [False]


def test_handle_error_event_dispatches_manuscript_error_dialog(showerror_recorder):
    gui_mod, calls = showerror_recorder
    stub = _GuiStub()

    exc = _manuscript_error(reason="DOCX fehlt")
    _invoke_handle_error(gui_mod, stub, exc)

    assert len(calls) == 1
    title, message = calls[0]
    assert title == gui_mod.DIALOG_TITLE_MANUSCRIPT
    assert message == str(exc)
    # the same message lands in the report pane so the author can re-read it
    assert stub.report_text == str(exc)


def test_handle_error_event_dispatches_config_error_dialog(showerror_recorder):
    gui_mod, calls = showerror_recorder
    stub = _GuiStub()

    _invoke_handle_error(gui_mod, stub, ConfigError("key fehlt"))

    title, _ = calls[0]
    assert title == gui_mod.DIALOG_TITLE_CONFIG


def test_handle_error_event_uses_generic_title_for_unknown_exception(showerror_recorder):
    gui_mod, calls = showerror_recorder
    stub = _GuiStub()

    _invoke_handle_error(gui_mod, stub, RuntimeError("boom"))

    title, message = calls[0]
    assert title == gui_mod.DIALOG_TITLE_GENERIC
    assert message == "boom"


def test_handle_error_event_handles_non_exception_payload(showerror_recorder):
    """Defensive: a stringly-typed payload from the worker still renders."""
    gui_mod, calls = showerror_recorder
    stub = _GuiStub()

    _invoke_handle_error(gui_mod, stub, "round failed: unexpected state")

    title, message = calls[0]
    assert title == gui_mod.DIALOG_TITLE_GENERIC
    assert message == "round failed: unexpected state"
    assert stub.report_text == "round failed: unexpected state"


def test_handle_error_event_always_writes_message_to_report_pane(showerror_recorder):
    """The report pane mirrors the dialog text — author can re-read after close."""
    gui_mod, _ = showerror_recorder
    stub = _GuiStub()

    _invoke_handle_error(
        gui_mod,
        stub,
        _manuscript_error(hint="Bitte DOCX in den Buchordner kopieren."),
    )

    assert "DOCX" in stub.report_text


def test_handle_error_event_calls_showerror_exactly_once(showerror_recorder):
    """No double-dialogs even if multiple branches happen to overlap."""
    gui_mod, calls = showerror_recorder
    stub = _GuiStub()

    _invoke_handle_error(gui_mod, stub, ConfigError("missing"))
    _invoke_handle_error(gui_mod, stub, _manuscript_error(reason="missing"))

    assert len(calls) == 2
    assert calls[0][0] == gui_mod.DIALOG_TITLE_CONFIG
    assert calls[1][0] == gui_mod.DIALOG_TITLE_MANUSCRIPT
