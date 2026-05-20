"""Unit tests for modules.score_history_graph (PNG renderer).

The tests exercise the pure-Python dataset extraction directly and use a
captured fake renderer for the render layer — that keeps CI free of a
matplotlib install while still pinning the public contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from modules.config import AppConfig, load_config
from modules.score_history_graph import (
    CHART_OPTIONAL_METRICS,
    CHART_TITLE_FALLBACK,
    ChartDataset,
    ChartSeries,
    build_chart_dataset,
    render_history_chart_png,
)


def _history(entries: list[dict]) -> dict:
    return {
        "version": 1,
        "project_id": "book",
        "created_at": "2026-05-20T10:00:00",
        "entries": entries,
    }


def _entry(
    *,
    timestamp: str = "2026-05-20T10:00:00",
    industrial_score: int = 70,
    arc_score: int | None = None,
    positioning_score: int | None = None,
    balance_score: int | None = None,
    readability_score: int | None = None,
) -> dict:
    return {
        "timestamp": timestamp,
        "industrial_score": industrial_score,
        "arc_score": arc_score,
        "positioning_score": positioning_score,
        "balance_score": balance_score,
        "readability_score": readability_score,
    }


# ---------------------------------------------------------------- build_chart_dataset


def test_build_chart_dataset_returns_none_for_empty_history():
    assert build_chart_dataset(None) is None
    assert build_chart_dataset({}) is None
    assert build_chart_dataset(_history([])) is None


def test_build_chart_dataset_returns_none_for_single_entry():
    history = _history([_entry(industrial_score=70)])
    assert build_chart_dataset(history) is None


def test_build_chart_dataset_collects_industrial_values_in_order():
    history = _history([
        _entry(timestamp="2026-05-18T09:00:00", industrial_score=60),
        _entry(timestamp="2026-05-19T09:00:00", industrial_score=70),
        _entry(timestamp="2026-05-20T09:00:00", industrial_score=85),
    ])

    dataset = build_chart_dataset(history)

    assert dataset is not None
    assert dataset.industrial.label == "Industrial-Score"
    assert dataset.industrial.values == (60, 70, 85)
    assert dataset.timestamps == (
        "2026-05-18T09:00:00",
        "2026-05-19T09:00:00",
        "2026-05-20T09:00:00",
    )


def test_build_chart_dataset_uses_project_title_when_provided():
    history = _history([_entry(), _entry(timestamp="2026-05-21T09:00:00")])

    dataset = build_chart_dataset(history, project_title="Mein Sachbuch")
    assert dataset is not None
    assert dataset.project_title == "Mein Sachbuch"


def test_build_chart_dataset_falls_back_to_default_title_for_empty_title():
    history = _history([_entry(), _entry(timestamp="2026-05-21T09:00:00")])

    dataset = build_chart_dataset(history, project_title="   ")
    assert dataset is not None
    assert dataset.project_title == CHART_TITLE_FALLBACK


def test_build_chart_dataset_omits_optional_series_when_never_measured():
    history = _history([
        _entry(industrial_score=70),
        _entry(timestamp="2026-05-21T09:00:00", industrial_score=75),
    ])

    dataset = build_chart_dataset(history)

    assert dataset is not None
    assert dataset.optional == ()


def test_build_chart_dataset_includes_optional_series_when_any_entry_has_data():
    history = _history([
        _entry(industrial_score=70, arc_score=None, readability_score=55),
        _entry(timestamp="2026-05-21T09:00:00", industrial_score=78, arc_score=80, readability_score=60),
    ])

    dataset = build_chart_dataset(history)

    assert dataset is not None
    labels = {series.label for series in dataset.optional}
    assert "Arc" in labels
    assert "Lesbarkeit" in labels
    # Positioning was never measured — must not appear.
    assert "Positionierung" not in labels


def test_build_chart_dataset_preserves_none_for_missing_optional_rounds():
    history = _history([
        _entry(industrial_score=70, arc_score=None),
        _entry(timestamp="2026-05-21T09:00:00", industrial_score=78, arc_score=80),
        _entry(timestamp="2026-05-22T09:00:00", industrial_score=82, arc_score=None),
    ])

    dataset = build_chart_dataset(history)

    assert dataset is not None
    arc_series = next(s for s in dataset.optional if s.label == "Arc")
    # First and last rounds didn't carry an arc score — must remain None.
    assert arc_series.values == (None, 80, None)


def test_build_chart_dataset_coerces_non_integer_industrial_to_zero():
    """Defensive: a corrupted entry with a string score must not crash the chart."""
    history = _history([
        _entry(industrial_score=60),
        {
            "timestamp": "2026-05-21T09:00:00",
            "industrial_score": "not-a-number",
        },
    ])

    dataset = build_chart_dataset(history)

    assert dataset is not None
    assert dataset.industrial.values == (60, 0)


def test_build_chart_dataset_skips_non_dict_entries():
    history = _history([_entry(industrial_score=60), "garbage", _entry(industrial_score=70)])  # type: ignore[list-item]

    dataset = build_chart_dataset(history)

    assert dataset is not None
    # The "garbage" string is skipped; we end up with 2 real entries.
    assert dataset.industrial.values == (60, 70)


def test_build_chart_dataset_is_frozen():
    history = _history([_entry(), _entry(timestamp="2026-05-21T09:00:00")])

    dataset = build_chart_dataset(history)

    assert dataset is not None
    with pytest.raises(Exception):
        dataset.timestamps = ("changed",)  # type: ignore[misc]


def test_chart_optional_metrics_uses_canonical_score_history_field_names():
    """Drift-guard: if score_history.py adds a new optional metric, this list
    must be updated together — otherwise the chart silently drops it."""
    keys = {key for key, _label in CHART_OPTIONAL_METRICS}
    assert keys == {"arc_score", "positioning_score", "balance_score", "readability_score"}


# ---------------------------------------------------------------- render_history_chart_png


def test_render_returns_failure_when_dataset_is_none(tmp_path: Path):
    result = render_history_chart_png(None, tmp_path / "chart.png")

    assert result.success is False
    assert result.output_path is None
    assert "noetig" in result.message.lower() or "noch" in result.message.lower()


def test_render_invokes_renderer_with_dataset_and_path(tmp_path: Path):
    history = _history([
        _entry(industrial_score=60),
        _entry(timestamp="2026-05-21T09:00:00", industrial_score=75),
    ])
    dataset = build_chart_dataset(history)
    captured: dict = {}

    def fake_renderer(ds: ChartDataset, path: Path) -> None:
        captured["dataset"] = ds
        captured["path"] = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")

    target = tmp_path / "score_history.png"
    result = render_history_chart_png(dataset, target, renderer=fake_renderer)

    assert result.success is True
    assert result.output_path == target
    assert captured["dataset"] is dataset
    assert captured["path"] == target
    assert target.exists()


def test_render_surfaces_import_error_as_failure(tmp_path: Path):
    """matplotlib missing → renderer raises ImportError → we return a clean failure."""
    history = _history([
        _entry(industrial_score=60),
        _entry(timestamp="2026-05-21T09:00:00", industrial_score=75),
    ])
    dataset = build_chart_dataset(history)

    def broken_renderer(ds: ChartDataset, path: Path) -> None:
        raise ImportError("No module named 'matplotlib'")

    result = render_history_chart_png(
        dataset, tmp_path / "score_history.png", renderer=broken_renderer
    )

    assert result.success is False
    assert result.output_path is None
    assert "matplotlib" in result.message.lower()
    assert "pip install matplotlib" in result.message


def test_render_surfaces_generic_error_as_failure(tmp_path: Path):
    """Any other render error must not crash the pipeline."""
    history = _history([
        _entry(industrial_score=60),
        _entry(timestamp="2026-05-21T09:00:00", industrial_score=75),
    ])
    dataset = build_chart_dataset(history)

    def broken_renderer(ds: ChartDataset, path: Path) -> None:
        raise RuntimeError("disk full")

    result = render_history_chart_png(
        dataset, tmp_path / "score_history.png", renderer=broken_renderer
    )

    assert result.success is False
    assert result.output_path is None
    assert "disk full" in result.message


# ---------------------------------------------------------------- AppConfig integration


def test_app_config_default_disables_score_history_graph():
    config = AppConfig(
        project_root=Path("."),
        default_input_path=Path("."),
        default_model="claude-sonnet-4-6",
        fallback_model="claude-haiku-4-5-20251001",
    )
    assert config.score_history_graph_enabled is False


def test_load_config_reads_score_history_graph_enabled(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
default_model: claude-sonnet-4-6
fallback_model: claude-haiku-4-5-20251001
score_history_graph_enabled: true
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.score_history_graph_enabled is True


def test_load_config_defaults_score_history_graph_off_when_key_missing(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
default_model: claude-sonnet-4-6
fallback_model: claude-haiku-4-5-20251001
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.score_history_graph_enabled is False


def test_load_config_coerces_truthy_yaml_values(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
default_model: claude-sonnet-4-6
fallback_model: claude-haiku-4-5-20251001
score_history_graph_enabled: "yes"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)
    assert config.score_history_graph_enabled is True
