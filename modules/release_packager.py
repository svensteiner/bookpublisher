"""Build a single distributable BookPublisher.zip for the homepage.

The customer downloads ONE file from the homepage. Unzip → ready to
test their book. This module assembles that ZIP from the in-tree
release directory + a freshly built EXE (if available) + the launcher
batch file + a short top-level LIES_MICH explaining the bundle.

Pure-Python, no third-party dependencies. CI-fähig: every helper takes
explicit path arguments so tests can run against an isolated fixture
tree built in pytest's ``tmp_path``.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Directories/files inside the source tree the packager pulls from.
DEFAULT_RELEASE_DIRNAME: str = "release"
DEFAULT_BEISPIELBUCH_DIRNAME: str = "beispielbuch"
DEFAULT_LAUNCHER_FILENAME: str = "BookPublisher starten.bat"
DEFAULT_EXE_RELPATH: str = "dist/BookPublisher/BookPublisher.exe"
DEFAULT_OUTPUT_RELPATH: str = "dist/BookPublisher.zip"

# Top-level LIES_MICH.txt written into the ZIP root. Kept inline (not
# a separate source file) because it documents the *release bundle*,
# not the source tree — its contents track this module, not the repo.
_TOP_README_TEXT: str = (
    "BookPublisher - Schnellstart\n"
    "============================\n"
    "\n"
    "Du hast gerade BookPublisher.zip ausgepackt. So testest du den\n"
    "Agenten in 30 Sekunden:\n"
    "\n"
    "1. Doppelklick auf BookPublisher.exe (wenn vorhanden) ODER auf\n"
    "   'BookPublisher starten.bat' (startet die Python-Version).\n"
    "2. Klicke 'Ordner waehlen' und waehle den Ordner 'beispielbuch'\n"
    "   im selben Verzeichnis.\n"
    "3. Klicke 'Pruefrunde starten' - der Schnellmodus laeuft ohne\n"
    "   API-Schluessel.\n"
    "4. Lies 'beginner_summary.md' - dort steht die Ampel und die\n"
    "   naechsten konkreten Schritte.\n"
    "\n"
    "Wenn du den Agenten gegen dein eigenes Buch laufen lassen willst,\n"
    "kopierst du dein Buch in einen neuen Ordner und waehlst diesen\n"
    "statt 'beispielbuch'.\n"
    "\n"
    "Voller Funktionsumfang (LLM-Review, Cover-Check, Launch-Plan)\n"
    "benoetigt einen Anthropic-API-Key. Details: README.md\n"
)

# Filename patterns we never want in a release ZIP — keeps the\n# distribution lean and prevents leaking developer artifacts.
EXCLUDED_PATTERNS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".pyc",
        ".pyo",
        ".DS_Store",
        "Thumbs.db",
        ".git",
        ".pytest_cache",
    }
)


@dataclass(frozen=True)
class ReleaseManifest:
    """What ended up in the ZIP — surfaced to CLI users and tests."""

    output_path: Path
    included_files: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    total_bytes: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "included_files": list(self.included_files),
            "warnings": list(self.warnings),
            "total_bytes": self.total_bytes,
        }


def _is_excluded(path: Path) -> bool:
    """Skip developer/build artifacts that don't belong in the release."""
    for part in path.parts:
        if part in EXCLUDED_PATTERNS:
            return True
        for pattern in EXCLUDED_PATTERNS:
            if pattern.startswith(".") and part.endswith(pattern):
                return True
    return False


def _iter_directory_files(root: Path) -> list[Path]:
    """List every file under ``root`` (recursive), filtered + sorted.

    Sorted output gives byte-identical ZIPs across runs — important so
    tests and release-checksums don't drift between invocations.
    """
    if not root.exists() or not root.is_dir():
        return []
    files: list[Path] = []
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        if _is_excluded(entry.relative_to(root)):
            continue
        files.append(entry)
    return sorted(files)


def _add_file(
    archive: zipfile.ZipFile,
    source: Path,
    arcname: str,
    *,
    accumulator: list[tuple[str, int]],
) -> None:
    """Write a single file into the ZIP and track its size."""
    archive.write(source, arcname=arcname)
    accumulator.append((arcname, source.stat().st_size))


def build_release_zip(
    project_root: Path,
    output_path: Path,
    *,
    release_dir: Path | None = None,
    exe_path: Path | None = None,
    launcher_path: Path | None = None,
    top_readme_text: str = _TOP_README_TEXT,
) -> ReleaseManifest:
    """Assemble a release ZIP from the in-tree release artifacts.

    Pieces are optional in the sense that a missing EXE adds a warning
    rather than crashing — local dev builds without PyInstaller still
    produce a usable ZIP for source-distribution testing.
    """

    release_root = release_dir or (project_root / DEFAULT_RELEASE_DIRNAME)
    beispielbuch = release_root / DEFAULT_BEISPIELBUCH_DIRNAME
    launcher = launcher_path or (project_root / DEFAULT_LAUNCHER_FILENAME)
    exe = exe_path  # explicit None means "skip"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Overwrite any prior bundle — release builds are idempotent.
    if output_path.exists():
        output_path.unlink()

    warnings: list[str] = []
    entries: list[tuple[str, int]] = []

    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        # 1. Sample book — mandatory for the customer's first-touch demo.
        if not beispielbuch.exists():
            warnings.append(
                f"beispielbuch-Ordner fehlt unter {beispielbuch} — "
                "Kunde haette keine Test-Datei nach dem Entpacken."
            )
        else:
            for source in _iter_directory_files(beispielbuch):
                rel = source.relative_to(beispielbuch).as_posix()
                arcname = f"{DEFAULT_BEISPIELBUCH_DIRNAME}/{rel}"
                _add_file(archive, source, arcname, accumulator=entries)

        # 2. Launcher batch file — works without the EXE.
        if not launcher.exists():
            warnings.append(
                f"Launcher fehlt unter {launcher} — Kunden ohne EXE muessen Python manuell starten."
            )
        else:
            _add_file(
                archive,
                launcher,
                DEFAULT_LAUNCHER_FILENAME,
                accumulator=entries,
            )

        # 3. PyInstaller EXE — optional but ideal.
        if exe is None:
            warnings.append(
                "Kein EXE-Pfad uebergeben — ZIP enthaelt nur die Python-Launcher-Variante."
            )
        elif not exe.exists():
            warnings.append(
                f"EXE fehlt unter {exe} — bitte vorher 'scripts/build_windows_app.bat' ausfuehren."
            )
        else:
            _add_file(archive, exe, exe.name, accumulator=entries)

        # 4. Top-level LIES_MICH.txt — written from inline string so the
        #    bundle README always tracks the packager version, not a
        #    drifting source file.
        archive.writestr("LIES_MICH.txt", top_readme_text)
        entries.append(("LIES_MICH.txt", len(top_readme_text.encode("utf-8"))))

    total_bytes = sum(size for _, size in entries)
    included = tuple(name for name, _ in entries)
    return ReleaseManifest(
        output_path=output_path,
        included_files=included,
        warnings=tuple(warnings),
        total_bytes=total_bytes,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build BookPublisher.zip for distribution."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output ZIP path (default: <project>/{DEFAULT_OUTPUT_RELPATH})",
    )
    parser.add_argument(
        "--exe",
        type=Path,
        default=None,
        help=f"Optional path to BookPublisher.exe (default: <project>/{DEFAULT_EXE_RELPATH} if present)",
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=None,
        help="Override the release/ source directory (for testing).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    output_path = args.output or (project_root / DEFAULT_OUTPUT_RELPATH)
    release_dir = args.release_dir or (project_root / DEFAULT_RELEASE_DIRNAME)

    exe_candidate = args.exe
    if exe_candidate is None:
        default_exe = project_root / DEFAULT_EXE_RELPATH
        if default_exe.exists():
            exe_candidate = default_exe

    manifest = build_release_zip(
        project_root,
        output_path,
        release_dir=release_dir,
        exe_path=exe_candidate,
    )
    print(f"Release-ZIP erstellt: {manifest.output_path}")
    print(f"Enthaltene Dateien: {len(manifest.included_files)}")
    print(f"Gesamt-Groesse (unkomprimiert): {manifest.total_bytes} Bytes")
    if manifest.warnings:
        print("Warnungen:")
        for warning in manifest.warnings:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
