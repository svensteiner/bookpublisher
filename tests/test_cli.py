"""Tests for modules.cli — exit codes and CLI argument parsing.

Power-users bind the CLI into shell scripts and CI pipelines and rely on
stable exit codes. These tests lock the contract: every documented code
must be returned for its documented condition. The README under
``## Run > Exit codes`` is the human-readable mirror of these tests.
"""

from __future__ import annotations

from pathlib import Path

from modules.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_GENERIC_ERROR,
    EXIT_MANUSCRIPT_ERROR,
    EXIT_SUCCESS,
    build_parser,
    main,
)
from modules.config import ConfigError
from modules.readers import ManuscriptReadError


def test_exit_code_constants_are_stable():
    """Locking the contract: codes 0/1/2/3 must not silently drift."""
    assert EXIT_SUCCESS == 0
    assert EXIT_GENERIC_ERROR == 1
    assert EXIT_CONFIG_ERROR == 2
    assert EXIT_MANUSCRIPT_ERROR == 3


def test_exit_codes_are_pairwise_unique():
    codes = {EXIT_SUCCESS, EXIT_GENERIC_ERROR, EXIT_CONFIG_ERROR, EXIT_MANUSCRIPT_ERROR}
    assert len(codes) == 4


def test_main_returns_config_error_code_when_config_raises(monkeypatch, tmp_path):
    """ConfigError → exit code 2."""

    def fake_load_config(_path):
        raise ConfigError("missing API key")

    monkeypatch.setattr("modules.cli.load_config", fake_load_config)
    result = main(["qa", "--input-path", str(tmp_path)])
    assert result == EXIT_CONFIG_ERROR


def test_main_returns_manuscript_error_code_when_pipeline_raises_manuscript(
    monkeypatch, tmp_path
):
    """ManuscriptReadError → exit code 3."""
    from modules.config import AppConfig

    config = AppConfig(
        project_root=tmp_path,
        default_input_path=tmp_path,
        default_model="claude-sonnet-4-6",
        fallback_model="claude-haiku-4-5",
    )

    monkeypatch.setattr("modules.cli.load_config", lambda _p: config)

    class _FakePipeline:
        def __init__(self, *args, **kwargs):
            pass

        def discover(self, _path):
            raise ManuscriptReadError(
                path=Path("ghost.docx"),
                reason="zip corrupt",
                hint="Datei erneut speichern.",
            )

    monkeypatch.setattr("modules.cli.PublisherPipeline", _FakePipeline)
    result = main(["scan", "--input-path", str(tmp_path)])
    assert result == EXIT_MANUSCRIPT_ERROR


def test_main_returns_generic_error_for_unexpected_exception(monkeypatch, tmp_path):
    """Any other Exception → exit code 1 (generic)."""
    from modules.config import AppConfig

    config = AppConfig(
        project_root=tmp_path,
        default_input_path=tmp_path,
        default_model="claude-sonnet-4-6",
        fallback_model="claude-haiku-4-5",
    )
    monkeypatch.setattr("modules.cli.load_config", lambda _p: config)

    class _BoomPipeline:
        def __init__(self, *args, **kwargs):
            pass

        def discover(self, _path):
            raise RuntimeError("unexpected boom")

    monkeypatch.setattr("modules.cli.PublisherPipeline", _BoomPipeline)
    result = main(["scan", "--input-path", str(tmp_path)])
    assert result == EXIT_GENERIC_ERROR


def test_main_returns_success_when_scan_completes(monkeypatch, tmp_path):
    """Happy path → exit code 0."""
    from modules.config import AppConfig

    config = AppConfig(
        project_root=tmp_path,
        default_input_path=tmp_path,
        default_model="claude-sonnet-4-6",
        fallback_model="claude-haiku-4-5",
    )
    monkeypatch.setattr("modules.cli.load_config", lambda _p: config)

    class _OkPipeline:
        def __init__(self, *args, **kwargs):
            pass

        def discover(self, _path):
            return []

    monkeypatch.setattr("modules.cli.PublisherPipeline", _OkPipeline)
    result = main(["scan", "--input-path", str(tmp_path)])
    assert result == EXIT_SUCCESS


def test_parser_accepts_all_documented_commands():
    parser = build_parser()
    for command in ["scan", "qa", "round", "review", "cover", "launch", "all"]:
        args = parser.parse_args([command])
        assert args.command == command


def test_readme_documents_every_exit_code():
    """README must list every exit code the CLI returns — drift check."""

    readme = Path(__file__).resolve().parent.parent / "README.md"
    content = readme.read_text(encoding="utf-8")

    # The constant names must appear in the README so the contract is
    # documented and discoverable.
    assert "EXIT_SUCCESS" in content
    assert "EXIT_GENERIC_ERROR" in content
    assert "EXIT_CONFIG_ERROR" in content
    assert "EXIT_MANUSCRIPT_ERROR" in content

    # Every numeric code must show up at least once in the README
    # (inside the documented table).
    for code in (EXIT_SUCCESS, EXIT_GENERIC_ERROR, EXIT_CONFIG_ERROR, EXIT_MANUSCRIPT_ERROR):
        assert f"| {code} |" in content, f"Exit code {code} missing from README table"
