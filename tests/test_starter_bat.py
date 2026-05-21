"""Smoke tests for the customer-facing ``BookPublisher starten.bat``.

The launcher is the very first thing a non-technical customer touches
after extracting ``BookPublisher.zip``. These tests pin the contract:

* It tries the EXE first (so the customer never needs Python).
* It tries Python second (so the developer source repo still works).
* Both failure paths produce a German user-facing message — never a raw
  ``python is not recognized`` shell error or a Python traceback.

We can't ``exec`` the .bat on a non-Windows CI runner, so the contract
is enforced by inspecting the script text. The checks are intentionally
loose (substring matches) so harmless wording tweaks don't break CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest


LAUNCHER_PATH: Path = (
    Path(__file__).resolve().parents[1] / "BookPublisher starten.bat"
)


@pytest.fixture(scope="module")
def launcher_text() -> str:
    assert LAUNCHER_PATH.exists(), f"Launcher missing: {LAUNCHER_PATH}"
    # Windows .bat files are read with the default locale on real
    # systems. The customer bundle stays ASCII-safe so utf-8 decode
    # succeeds regardless of the file's actual encoding.
    return LAUNCHER_PATH.read_text(encoding="utf-8", errors="replace")


def test_launcher_prefers_exe_before_python(launcher_text: str) -> None:
    exe_index = launcher_text.find("BookPublisher.exe")
    python_index = launcher_text.find("python")
    assert exe_index >= 0, "Launcher must mention BookPublisher.exe"
    assert python_index >= 0, "Launcher must mention python as fallback"
    assert exe_index < python_index, (
        "EXE branch must come before python branch — otherwise a system "
        "with both installed would launch the slower source-repo path."
    )


def test_launcher_has_german_message_when_python_missing(launcher_text: str) -> None:
    assert "Python ist auf diesem Computer nicht installiert" in launcher_text


def test_launcher_explains_both_recovery_options(launcher_text: str) -> None:
    """The 'no Python' message must offer BOTH the EXE and the install
    paths so the beginner can pick — one without the other strands the
    user."""

    assert "BookPublisher.exe" in launcher_text
    assert "python.org" in launcher_text


def test_launcher_has_german_message_when_bundle_incomplete(
    launcher_text: str,
) -> None:
    assert "ZIP" in launcher_text or "zip" in launcher_text
    assert "entpackt" in launcher_text.lower()


def test_launcher_changes_to_script_directory(launcher_text: str) -> None:
    """Without ``cd /d "%~dp0"`` the launcher would read config.yaml from
    the user's current shell directory — broken when started via the
    Desktop shortcut."""

    assert 'cd /d "%~dp0"' in launcher_text


def test_launcher_starts_exe_relative_to_script_dir(launcher_text: str) -> None:
    """A bare ``BookPublisher.exe`` would resolve against the shell's
    working directory, not the launcher's folder — fragile when started
    from a Desktop shortcut with a different ``Start in`` value."""

    assert '"%~dp0BookPublisher.exe"' in launcher_text


def test_launcher_does_not_use_dangerous_flags(launcher_text: str) -> None:
    """No ``--no-verify``-style escape hatches, no ``rm -rf``-equivalent
    in batch (``rd /s /q`` against absolute paths). The launcher only
    starts processes, never deletes anything."""

    lower = launcher_text.lower()
    assert "rd /s /q" not in lower
    assert "del /q" not in lower
    assert "format " not in lower


def test_launcher_pauses_on_error_so_user_can_read_message(
    launcher_text: str,
) -> None:
    """Without ``pause`` the cmd window flashes and disappears, hiding
    the German error message."""

    assert "pause" in launcher_text.lower()


def test_launcher_has_distinct_exit_codes_per_failure_mode(
    launcher_text: str,
) -> None:
    """Distinct exit codes let a power-user (or our own smoke test)
    distinguish 'no Python' from 'bundle incomplete'. The numbers
    themselves are arbitrary — only the distinctness matters."""

    assert "exit /b 0" in launcher_text  # success
    # At least two distinct non-zero codes.
    nonzero_codes = {
        token
        for line in launcher_text.splitlines()
        for token in line.split()
        if token.startswith("/b")
    }
    # exit /b 0, 1, 2, 3 means we'll see "/b" four times — the codes
    # come right after. Extract the numbers explicitly.
    import re

    codes = set(re.findall(r"exit\s+/b\s+(\d+)", launcher_text))
    # 0 plus at least two distinct failures = the contract we care about.
    assert "0" in codes
    assert len(codes - {"0"}) >= 2, f"need >= 2 distinct error codes, got {codes}"
