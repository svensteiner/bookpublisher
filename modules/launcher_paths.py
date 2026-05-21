"""Path-resolution helpers for the customer-facing launcher.

A complete beginner downloads ``BookPublisher.zip``, extracts it to the
desktop, and double-clicks the launcher. We want the GUI to come up
with the bundled ``beispielbuch/`` folder already pre-selected — so the
first click is "Pruefrunde starten", not "Ordner waehlen".

This module isolates the path-resolution logic (which directory should
be the initial input?) as pure functions so it can be unit-tested
without spinning up a Tk root or touching the user's real filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

# Sibling directory name where the customer's first-touch sample book
# lives. Kept as a module-level constant so the release packager, GUI,
# and tests all agree on the same name.
SAMPLE_BOOK_DIRNAME: str = "beispielbuch"


def _candidate_directories(*roots: Path) -> Iterable[Path]:
    """Yield ``<root>/beispielbuch`` for each root, in order, deduped.

    Roots are tried in the order given so callers can express priority
    (e.g. "first the directory next to the launcher, then the cwd").
    Duplicates from ``Path.resolve()`` collisions are filtered so we
    don't pay for the same stat twice.
    """

    seen: set[Path] = set()
    for root in roots:
        if root is None:
            continue
        try:
            resolved = root.resolve()
        except OSError:
            # ``resolve()`` can raise on Windows for paths with broken
            # links — skip rather than crash the launcher boot.
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        yield resolved / SAMPLE_BOOK_DIRNAME


def resolve_default_input_path(
    config_default: Path,
    *,
    app_dir: Path,
    cwd: Path,
) -> Path:
    """Pick the initial ``input_path`` for the GUI.

    Priority:
      1. ``<app_dir>/beispielbuch`` — the bundled sample, sitting next
         to the launcher inside the extracted ZIP.
      2. ``<cwd>/beispielbuch`` — when the user runs the source repo
         from a parent directory.
      3. ``config_default`` — last-resort fallback (the YAML default,
         which may point at the developer's own KDP endversion folder).

    The first existing directory wins. No filesystem writes; only
    ``Path.is_dir()`` is consulted so the function stays cheap and
    deterministic.
    """

    for candidate in _candidate_directories(app_dir, cwd):
        if candidate.is_dir():
            return candidate
    return config_default
