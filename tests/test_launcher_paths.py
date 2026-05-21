"""Tests for the customer-launcher path resolver.

These tests cover the rule that the GUI must come up with the bundled
``beispielbuch/`` folder pre-selected when the customer double-clicks
the launcher inside the extracted ZIP — even when the YAML
``default_input_path`` points at the developer's own KDP folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.launcher_paths import SAMPLE_BOOK_DIRNAME, resolve_default_input_path


def test_returns_app_dir_sample_when_present(tmp_path: Path):
    app_dir = tmp_path / "extracted"
    cwd = tmp_path / "somewhere_else"
    config_default = tmp_path / "developer_kdp_folder"
    (app_dir / SAMPLE_BOOK_DIRNAME).mkdir(parents=True)
    cwd.mkdir()
    config_default.mkdir()

    result = resolve_default_input_path(config_default, app_dir=app_dir, cwd=cwd)

    assert result == (app_dir / SAMPLE_BOOK_DIRNAME).resolve()


def test_falls_back_to_cwd_sample_when_app_dir_missing(tmp_path: Path):
    app_dir = tmp_path / "extracted"
    cwd = tmp_path / "user_cwd"
    config_default = tmp_path / "developer_kdp_folder"
    app_dir.mkdir()
    (cwd / SAMPLE_BOOK_DIRNAME).mkdir(parents=True)
    config_default.mkdir()

    result = resolve_default_input_path(config_default, app_dir=app_dir, cwd=cwd)

    assert result == (cwd / SAMPLE_BOOK_DIRNAME).resolve()


def test_falls_back_to_config_default_when_no_sample_anywhere(tmp_path: Path):
    app_dir = tmp_path / "extracted"
    cwd = tmp_path / "user_cwd"
    config_default = tmp_path / "developer_kdp_folder"
    app_dir.mkdir()
    cwd.mkdir()
    config_default.mkdir()

    result = resolve_default_input_path(config_default, app_dir=app_dir, cwd=cwd)

    assert result == config_default


def test_prefers_app_dir_sample_over_cwd_sample(tmp_path: Path):
    """The launcher-adjacent sample always wins — that's the canonical
    customer-bundle layout. CWD-adjacent only matters when the user runs
    the source repo from a parent shell."""

    app_dir = tmp_path / "extracted"
    cwd = tmp_path / "user_cwd"
    config_default = tmp_path / "developer_kdp_folder"
    (app_dir / SAMPLE_BOOK_DIRNAME).mkdir(parents=True)
    (cwd / SAMPLE_BOOK_DIRNAME).mkdir(parents=True)
    config_default.mkdir()

    result = resolve_default_input_path(config_default, app_dir=app_dir, cwd=cwd)

    assert result == (app_dir / SAMPLE_BOOK_DIRNAME).resolve()


def test_ignores_sample_when_path_points_at_a_file_not_a_dir(tmp_path: Path):
    app_dir = tmp_path / "extracted"
    cwd = tmp_path / "user_cwd"
    config_default = tmp_path / "developer_kdp_folder"
    app_dir.mkdir()
    (app_dir / SAMPLE_BOOK_DIRNAME).write_text("not a dir", encoding="utf-8")
    cwd.mkdir()
    config_default.mkdir()

    result = resolve_default_input_path(config_default, app_dir=app_dir, cwd=cwd)

    # The file-shaped sample is not usable, so we fall through.
    assert result == config_default


def test_does_not_create_anything_when_resolving(tmp_path: Path):
    """Pure path resolution — no side effects on disk."""

    app_dir = tmp_path / "extracted"
    cwd = tmp_path / "user_cwd"
    config_default = tmp_path / "developer_kdp_folder"
    app_dir.mkdir()
    cwd.mkdir()
    config_default.mkdir()

    snapshot_before = {p.name for p in tmp_path.iterdir()}
    resolve_default_input_path(config_default, app_dir=app_dir, cwd=cwd)
    snapshot_after = {p.name for p in tmp_path.iterdir()}

    assert snapshot_before == snapshot_after


def test_same_dir_for_app_and_cwd_is_handled(tmp_path: Path):
    """When app_dir == cwd we must still resolve cleanly (no infinite loop,
    no duplicate stat) — the dedup inside ``_candidate_directories``
    protects this."""

    shared = tmp_path / "shared"
    config_default = tmp_path / "fallback"
    (shared / SAMPLE_BOOK_DIRNAME).mkdir(parents=True)
    config_default.mkdir()

    result = resolve_default_input_path(config_default, app_dir=shared, cwd=shared)

    assert result == (shared / SAMPLE_BOOK_DIRNAME).resolve()


def test_returns_config_default_unmodified_when_already_a_pathlib_path(
    tmp_path: Path,
):
    """The fallback path must be returned as-is — no resolve(), no
    string-roundtrip. That keeps trailing slashes and exotic configs
    (UNC paths, env-var-expanded paths) byte-identical to what the
    config layer produced."""

    app_dir = tmp_path / "extracted"
    cwd = tmp_path / "user_cwd"
    config_default = Path(r"C:\Users\dev\KDP\endversion")
    app_dir.mkdir()
    cwd.mkdir()

    result = resolve_default_input_path(config_default, app_dir=app_dir, cwd=cwd)

    assert result == config_default
    assert result is config_default
