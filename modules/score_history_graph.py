"""Optional PNG rendering of the QA score history.

Reads the same ``score_history.json`` payload that ``modules.score_history``
emits and produces a graph-ready PNG of the industrial score plus any
optional per-round metrics (arc, positioning, balance, readability).

Design constraints (see BACKLOG.md "Score-Verlauf Graph als PNG"):

* Off by default — matplotlib is not in the install set, importing it
  unconditionally would bloat the Windows EXE for users who never look
  at the chart.
* Pure-Python core (dataset extraction) so unit tests can exercise the
  shape without bringing in matplotlib on CI.
* Renderer is injected as a callable, so tests run with a fake renderer
  and the production code uses the matplotlib-backed default.
* No silent failures: when matplotlib is missing or rendering raises,
  the caller gets a ``ChartRenderResult`` with ``success=False`` and a
  human-readable reason it can log or surface in beginner_summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


CHART_TITLE_FALLBACK = "Score-Verlauf"
CHART_INDUSTRIAL_LABEL = "Industrial-Score"
CHART_OPTIONAL_METRICS: tuple[tuple[str, str], ...] = (
    ("arc_score", "Arc"),
    ("positioning_score", "Positionierung"),
    ("balance_score", "Balance"),
    ("readability_score", "Lesbarkeit"),
)
CHART_MIN_POINTS = 2


@dataclass(frozen=True)
class ChartSeries:
    """One named line on the chart (industrial or one of the optionals).

    ``values`` carries one entry per round; ``None`` means "no value for
    this round" so the renderer can break the line instead of plotting a
    misleading zero.
    """

    label: str
    values: tuple[int | None, ...]


@dataclass(frozen=True)
class ChartDataset:
    """Frozen, render-agnostic snapshot of the score history."""

    project_title: str
    timestamps: tuple[str, ...]
    industrial: ChartSeries
    optional: tuple[ChartSeries, ...]


@dataclass(frozen=True)
class ChartRenderResult:
    success: bool
    output_path: Path | None
    message: str


Renderer = Callable[[ChartDataset, Path], None]


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_chart_dataset(
    history: dict[str, Any] | None,
    *,
    project_title: str | None = None,
) -> ChartDataset | None:
    """Return a frozen ``ChartDataset`` or ``None`` when there is nothing to draw.

    Returns ``None`` for histories with fewer than ``CHART_MIN_POINTS``
    entries — a single point has no trend, and a chart with one dot would
    only confuse the author. Optional series (arc/positioning/balance/
    readability) are only included when at least one entry carries a
    real value; that keeps the legend honest for projects where some
    metrics were never measured.
    """

    if not history:
        return None
    entries = list(history.get("entries") or [])
    if len(entries) < CHART_MIN_POINTS:
        return None

    timestamps: list[str] = []
    industrial_values: list[int] = []
    optional_buckets: dict[str, list[int | None]] = {
        key: [] for key, _label in CHART_OPTIONAL_METRICS
    }

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        timestamps.append(str(entry.get("timestamp") or ""))
        industrial_values.append(_coerce_int(entry.get("industrial_score")))
        for key, _label in CHART_OPTIONAL_METRICS:
            optional_buckets[key].append(_coerce_optional_int(entry.get(key)))

    if len(industrial_values) < CHART_MIN_POINTS:
        return None

    optional_series: list[ChartSeries] = []
    for key, label in CHART_OPTIONAL_METRICS:
        values = optional_buckets[key]
        if any(value is not None for value in values):
            optional_series.append(
                ChartSeries(label=label, values=tuple(values))
            )

    return ChartDataset(
        project_title=(project_title or "").strip() or CHART_TITLE_FALLBACK,
        timestamps=tuple(timestamps),
        industrial=ChartSeries(
            label=CHART_INDUSTRIAL_LABEL,
            values=tuple(industrial_values),
        ),
        optional=tuple(optional_series),
    )


def _matplotlib_renderer(dataset: ChartDataset, output_path: Path) -> None:
    """Default renderer — imports matplotlib lazily.

    Raises ``ImportError`` when matplotlib is not installed so the
    caller's exception handler can surface a useful message. Uses the
    headless ``Agg`` backend so the function works without a display
    (CI, Windows-EXE, server runs).
    """

    import matplotlib  # type: ignore[import-not-found]

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt  # type: ignore[import-not-found]

    x_indices = list(range(len(dataset.timestamps)))
    fig, ax = plt.subplots(figsize=(8.0, 4.5))
    ax.plot(
        x_indices,
        list(dataset.industrial.values),
        marker="o",
        linewidth=2.0,
        label=dataset.industrial.label,
    )
    for series in dataset.optional:
        ax.plot(
            x_indices,
            [v if v is not None else float("nan") for v in series.values],
            marker="o",
            linewidth=1.2,
            label=series.label,
        )
    ax.set_title(f"Score-Verlauf — {dataset.project_title}")
    ax.set_xlabel("Runde")
    ax.set_ylabel("Score (0–100)")
    ax.set_ylim(0, 100)
    ax.set_xticks(x_indices)
    ax.set_xticklabels(
        [_short_timestamp(ts) for ts in dataset.timestamps],
        rotation=30,
        ha="right",
        fontsize=8,
    )
    ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.6)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def _short_timestamp(timestamp: str) -> str:
    """Render an ISO timestamp as ``YYYY-MM-DD`` for compact tick labels."""

    if not timestamp:
        return ""
    return timestamp.split("T", 1)[0]


def render_history_chart_png(
    dataset: ChartDataset | None,
    output_path: Path,
    *,
    renderer: Renderer | None = None,
) -> ChartRenderResult:
    """Render ``dataset`` to ``output_path`` using ``renderer``.

    ``renderer`` is injected for testability — the default uses
    matplotlib via a lazy import. When the renderer raises (most
    commonly ``ImportError`` because matplotlib is not installed),
    this function turns the exception into a structured
    ``ChartRenderResult`` so the pipeline keeps running and the
    failure is loggable instead of crashing the QA pass.
    """

    if dataset is None:
        return ChartRenderResult(
            success=False,
            output_path=None,
            message="Keine Score-History-Daten verfuegbar (mindestens 2 Runden noetig).",
        )

    active_renderer = renderer or _matplotlib_renderer
    try:
        active_renderer(dataset, output_path)
    except ImportError as exc:
        return ChartRenderResult(
            success=False,
            output_path=None,
            message=(
                "matplotlib ist nicht installiert — Score-Verlauf-PNG uebersprungen "
                f"({exc}). Installation: pip install matplotlib"
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return ChartRenderResult(
            success=False,
            output_path=None,
            message=f"Score-Verlauf-PNG konnte nicht erzeugt werden: {exc}",
        )

    return ChartRenderResult(
        success=True,
        output_path=output_path,
        message=f"Score-Verlauf-Grafik geschrieben: {output_path.name}",
    )
