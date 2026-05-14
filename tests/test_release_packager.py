"""Tests for the release-ZIP packager (CI-fähig, isoliert in tmp_path)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from modules.release_packager import (
    DEFAULT_BEISPIELBUCH_DIRNAME,
    DEFAULT_LAUNCHER_FILENAME,
    EXCLUDED_PATTERNS,
    ReleaseManifest,
    build_release_zip,
    main,
)


def _make_fake_project(root: Path, *, with_exe: bool = False) -> dict[str, Path]:
    """Build a minimal project tree mirroring the real layout."""
    release = root / "release"
    sample = release / DEFAULT_BEISPIELBUCH_DIRNAME
    sample.mkdir(parents=True)
    (sample / "LIES_MICH.txt").write_text("read me", encoding="utf-8")
    (sample / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (sample / "manuscript.docx").write_bytes(b"PK\x03\x04fake-docx")
    (sample / "metadata.md").write_text("# Titel\n", encoding="utf-8")

    launcher = root / DEFAULT_LAUNCHER_FILENAME
    launcher.write_text("@echo off\npython gui.py\n", encoding="utf-8")

    exe_path: Path | None = None
    if with_exe:
        exe_dir = root / "dist" / "BookPublisher"
        exe_dir.mkdir(parents=True)
        exe_path = exe_dir / "BookPublisher.exe"
        exe_path.write_bytes(b"MZ\x90\x00fake-exe")

    return {
        "release": release,
        "sample": sample,
        "launcher": launcher,
        "exe": exe_path or Path("/missing/exe"),
    }


def _zip_contents(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as archive:
        return set(archive.namelist())


def test_build_release_zip_includes_beispielbuch_when_present(tmp_path):
    _make_fake_project(tmp_path)
    output = tmp_path / "out" / "BookPublisher.zip"

    manifest = build_release_zip(tmp_path, output)

    assert output.exists()
    names = _zip_contents(output)
    assert "beispielbuch/LIES_MICH.txt" in names
    assert "beispielbuch/cover.jpg" in names
    assert "beispielbuch/manuscript.docx" in names
    assert "beispielbuch/metadata.md" in names
    # Manifest matches archive.
    for name in [
        "beispielbuch/LIES_MICH.txt",
        "beispielbuch/cover.jpg",
        "beispielbuch/manuscript.docx",
        "beispielbuch/metadata.md",
    ]:
        assert name in manifest.included_files


def test_build_release_zip_includes_exe_when_present(tmp_path):
    paths = _make_fake_project(tmp_path, with_exe=True)
    output = tmp_path / "BookPublisher.zip"

    manifest = build_release_zip(tmp_path, output, exe_path=paths["exe"])

    names = _zip_contents(output)
    assert "BookPublisher.exe" in names
    assert "BookPublisher.exe" in manifest.included_files
    # No warning about missing EXE.
    assert not any("EXE fehlt" in w for w in manifest.warnings)
    assert not any("Kein EXE-Pfad" in w for w in manifest.warnings)


def test_build_release_zip_records_warning_when_exe_missing(tmp_path):
    _make_fake_project(tmp_path)  # no exe built
    output = tmp_path / "BookPublisher.zip"

    manifest = build_release_zip(tmp_path, output)

    names = _zip_contents(output)
    assert "BookPublisher.exe" not in names
    assert any("Kein EXE-Pfad" in w for w in manifest.warnings)


def test_build_release_zip_records_warning_when_exe_path_does_not_exist(tmp_path):
    _make_fake_project(tmp_path)
    missing = tmp_path / "dist" / "BookPublisher" / "BookPublisher.exe"
    output = tmp_path / "BookPublisher.zip"

    manifest = build_release_zip(tmp_path, output, exe_path=missing)

    names = _zip_contents(output)
    assert "BookPublisher.exe" not in names
    assert any("EXE fehlt" in w for w in manifest.warnings)


def test_build_release_zip_includes_starter_bat(tmp_path):
    _make_fake_project(tmp_path)
    output = tmp_path / "BookPublisher.zip"

    build_release_zip(tmp_path, output)

    names = _zip_contents(output)
    assert DEFAULT_LAUNCHER_FILENAME in names


def test_build_release_zip_warns_when_launcher_missing(tmp_path):
    paths = _make_fake_project(tmp_path)
    paths["launcher"].unlink()
    output = tmp_path / "BookPublisher.zip"

    manifest = build_release_zip(tmp_path, output)
    assert any("Launcher fehlt" in w for w in manifest.warnings)
    assert DEFAULT_LAUNCHER_FILENAME not in _zip_contents(output)


def test_build_release_zip_writes_top_level_lies_mich(tmp_path):
    _make_fake_project(tmp_path)
    output = tmp_path / "BookPublisher.zip"

    build_release_zip(tmp_path, output)

    with zipfile.ZipFile(output) as archive:
        text = archive.read("LIES_MICH.txt").decode("utf-8")
    # The top-level LIES_MICH must reference the customer flow, not be empty.
    assert "Doppelklick" in text
    assert "beispielbuch" in text
    assert "Pruefrunde" in text


def test_build_release_zip_creates_output_parent_directory(tmp_path):
    _make_fake_project(tmp_path)
    output = tmp_path / "deeply" / "nested" / "dist" / "BookPublisher.zip"
    assert not output.parent.exists()

    build_release_zip(tmp_path, output)

    assert output.exists()


def test_build_release_zip_overwrites_existing_output(tmp_path):
    _make_fake_project(tmp_path)
    output = tmp_path / "BookPublisher.zip"
    output.write_bytes(b"stale content from a previous build")

    build_release_zip(tmp_path, output)

    # Real ZIP file now — zipfile can parse it.
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist()


def test_build_release_zip_excludes_pycache(tmp_path):
    paths = _make_fake_project(tmp_path)
    cache_dir = paths["sample"] / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "stale.pyc").write_bytes(b"\x00\x00")
    (paths["sample"] / "trash.pyc").write_bytes(b"\x00\x00")

    output = tmp_path / "BookPublisher.zip"
    build_release_zip(tmp_path, output)

    names = _zip_contents(output)
    assert not any("__pycache__" in n for n in names)
    assert not any(n.endswith(".pyc") for n in names)


def test_build_release_zip_total_bytes_matches_entries(tmp_path):
    paths = _make_fake_project(tmp_path, with_exe=True)
    output = tmp_path / "BookPublisher.zip"

    manifest = build_release_zip(tmp_path, output, exe_path=paths["exe"])

    # total_bytes is the sum of *uncompressed* sizes — must be positive
    # and at least the cover-jpeg size.
    assert manifest.total_bytes > 0
    cover_size = (paths["sample"] / "cover.jpg").stat().st_size
    assert manifest.total_bytes >= cover_size


def test_release_manifest_is_frozen(tmp_path):
    _make_fake_project(tmp_path)
    output = tmp_path / "BookPublisher.zip"
    manifest = build_release_zip(tmp_path, output)

    assert isinstance(manifest, ReleaseManifest)
    try:
        manifest.total_bytes = 999  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ReleaseManifest should be frozen")


def test_build_release_zip_warns_when_sample_dir_missing(tmp_path):
    # Project tree without release/beispielbuch.
    (tmp_path / DEFAULT_LAUNCHER_FILENAME).write_text("@echo off\n", encoding="utf-8")
    (tmp_path / "release").mkdir()

    output = tmp_path / "BookPublisher.zip"
    manifest = build_release_zip(tmp_path, output)

    assert any("beispielbuch-Ordner fehlt" in w for w in manifest.warnings)
    # ZIP still gets built — launcher + top-level readme remain useful.
    names = _zip_contents(output)
    assert DEFAULT_LAUNCHER_FILENAME in names
    assert "LIES_MICH.txt" in names


def test_build_release_zip_deterministic_order(tmp_path):
    """Two builds against the same tree produce the same archive entry order."""
    _make_fake_project(tmp_path)
    out1 = tmp_path / "first.zip"
    out2 = tmp_path / "second.zip"

    m1 = build_release_zip(tmp_path, out1)
    m2 = build_release_zip(tmp_path, out2)

    assert m1.included_files == m2.included_files


def test_excluded_patterns_cover_typical_developer_artifacts():
    # Drift-protection: enforce the contract that the standard junk
    # files are excluded. Future maintainers can add patterns without
    # breaking the test, but accidental removal is caught.
    assert "__pycache__" in EXCLUDED_PATTERNS
    assert ".pyc" in EXCLUDED_PATTERNS
    assert ".DS_Store" in EXCLUDED_PATTERNS
    assert ".git" in EXCLUDED_PATTERNS


def test_custom_top_readme_text_is_written(tmp_path):
    _make_fake_project(tmp_path)
    output = tmp_path / "BookPublisher.zip"

    build_release_zip(tmp_path, output, top_readme_text="custom readme block")

    with zipfile.ZipFile(output) as archive:
        text = archive.read("LIES_MICH.txt").decode("utf-8")
    assert text == "custom readme block"


def test_main_cli_builds_zip_at_specified_output(tmp_path, capsys, monkeypatch):
    """End-to-end smoke: argv-style invocation produces a valid ZIP."""
    project = tmp_path / "project"
    project.mkdir()
    _make_fake_project(project)

    output = tmp_path / "build" / "BookPublisher.zip"
    monkeypatch.setattr(
        "modules.release_packager.Path",
        Path,  # use the real Path for argparse coercion
    )

    rc = main(
        [
            "--output",
            str(output),
            "--release-dir",
            str(project / "release"),
        ]
    )
    captured = capsys.readouterr()

    assert rc == 0
    assert output.exists()
    assert "Release-ZIP erstellt" in captured.out


def test_main_cli_returns_zero_even_with_warnings(tmp_path, capsys):
    """Missing EXE prints a warning but the CLI still exits zero."""
    project = tmp_path / "project"
    project.mkdir()
    _make_fake_project(project)

    output = tmp_path / "BookPublisher.zip"
    rc = main(
        [
            "--output",
            str(output),
            "--release-dir",
            str(project / "release"),
        ]
    )
    captured = capsys.readouterr()

    assert rc == 0
    # Warning about missing EXE was surfaced.
    assert "Warnungen" in captured.out
