"""Unit tests for modules.score_history."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from modules.discovery import BookProject
from modules.score_history import (
    MAX_HISTORY_ENTRIES,
    SCORE_HISTORY_VERSION,
    TOP_FIX_COUNT,
    append_score_history,
    load_score_history,
    render_score_history_markdown,
)
from tests.helpers import runtime_dir


def _project(project_id: str = "book") -> BookProject:
    return BookProject(
        project_id=project_id,
        root=runtime_dir("score_history"),
        title="Mein Sachbuch",
        author="Sven Steiner",
    )


def _qa(
    industrial_score: int = 78,
    decision: str = "GO_AFTER_FIXES",
    fixes: list[str] | None = None,
    gates: list[dict] | None = None,
) -> dict:
    return {
        "decision": decision,
        "industrial_score": industrial_score,
        "investor_grade": round(industrial_score / 10, 1),
        "gates": gates
        or [
            {"name": "asset_completeness", "status": "READY", "score": 90},
            {"name": "metadata_quality", "status": "REVIEW", "score": 70},
        ],
        "all_required_fixes": fixes or ["Cover schaerfen.", "Sample staerken.", "Keywords ergaenzen.", "Beschreibung ueberarbeiten."],
    }


def test_load_score_history_returns_fresh_for_missing_path():
    project = _project()
    path = project.root / "score_history.json"

    history = load_score_history(path, project.project_id)

    assert history["version"] == SCORE_HISTORY_VERSION
    assert history["project_id"] == project.project_id
    assert history["entries"] == []
    assert "created_at" in history


def test_load_score_history_recovers_from_invalid_json():
    project = _project()
    path = project.root / "score_history.json"
    path.write_text("not-json", encoding="utf-8")

    history = load_score_history(path, project.project_id)

    assert history["entries"] == []
    assert history["project_id"] == project.project_id


def test_load_score_history_ignores_wrong_project_id():
    project = _project("book_a")
    path = project.root / "score_history.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "project_id": "book_b",
            "created_at": "2026-05-01T00:00:00",
            "entries": [{"industrial_score": 99}],
        }),
        encoding="utf-8",
    )

    history = load_score_history(path, project.project_id)

    assert history["entries"] == []
    assert history["project_id"] == project.project_id


def test_append_first_entry_has_no_score_delta():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)

    updated = append_score_history(
        history,
        project,
        _qa(industrial_score=78),
        round_id="r1",
        mode="quick_qa",
        now=datetime(2026, 5, 11, 10, 30, 0),
    )

    assert len(updated["entries"]) == 1
    entry = updated["entries"][0]
    assert entry["industrial_score"] == 78
    assert entry["score_delta"] is None
    assert entry["round_id"] == "r1"
    assert entry["mode"] == "quick_qa"
    assert entry["timestamp"] == "2026-05-11T10:30:00"
    assert entry["investor_grade"] == 7.8
    assert entry["decision"] == "GO_AFTER_FIXES"
    assert len(entry["top_fixes"]) == TOP_FIX_COUNT
    assert entry["gates"][0]["name"] == "asset_completeness"


def test_append_is_immutable_does_not_mutate_input():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)
    snapshot = json.dumps(history, sort_keys=True, default=str)

    append_score_history(history, project, _qa(industrial_score=70))

    assert json.dumps(history, sort_keys=True, default=str) == snapshot


def test_append_second_entry_computes_score_delta_positive():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)
    history = append_score_history(history, project, _qa(industrial_score=70), round_id="r1")

    history = append_score_history(history, project, _qa(industrial_score=82), round_id="r2")

    assert len(history["entries"]) == 2
    assert history["entries"][1]["score_delta"] == 12


def test_append_score_delta_can_be_negative():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)
    history = append_score_history(history, project, _qa(industrial_score=85))

    history = append_score_history(history, project, _qa(industrial_score=80))

    assert history["entries"][-1]["score_delta"] == -5


def test_top_fixes_caps_at_three_and_dedupes():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)
    qa = _qa(fixes=["A", "A", "B", "C", "D", "E"])

    history = append_score_history(history, project, qa)

    assert history["entries"][0]["top_fixes"] == ["A", "B", "C"]


def test_history_caps_entries_to_max():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)

    for i in range(MAX_HISTORY_ENTRIES + 5):
        history = append_score_history(history, project, _qa(industrial_score=50 + (i % 30)))

    assert len(history["entries"]) == MAX_HISTORY_ENTRIES


def test_render_markdown_for_empty_history():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)

    rendered = render_score_history_markdown(project, history)

    assert "Score-Verlauf" in rendered
    assert "Noch keine" in rendered
    assert project.title in rendered


def test_render_markdown_shows_table_and_trend():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)
    history = append_score_history(
        history,
        project,
        _qa(industrial_score=70),
        round_id="r1",
        now=datetime(2026, 5, 10, 9, 0, 0),
    )
    history = append_score_history(
        history,
        project,
        _qa(industrial_score=82),
        round_id="r2",
        now=datetime(2026, 5, 11, 9, 0, 0),
    )

    rendered = render_score_history_markdown(project, history)

    assert "| Datum | Runde |" in rendered
    assert "70/100" in rendered
    assert "82/100" in rendered
    assert "+12" in rendered
    assert "Trend:" in rendered
    assert "Top-Fixes" in rendered


def test_qa_with_missing_fields_does_not_crash():
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)

    history = append_score_history(history, project, {})

    entry = history["entries"][0]
    assert entry["industrial_score"] == 0
    assert entry["decision"] == "HOLD"
    assert entry["gates"] == []
    assert entry["top_fixes"] == []
    assert entry["investor_grade"] is None


def test_load_round_trip_via_disk(tmp_path: Path):
    project = _project()
    history = load_score_history(project.root / "score_history.json", project.project_id)
    history = append_score_history(history, project, _qa(industrial_score=77), round_id="r1")

    persisted = tmp_path / "score_history.json"
    persisted.write_text(json.dumps(history, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    reloaded = load_score_history(persisted, project.project_id)

    assert len(reloaded["entries"]) == 1
    assert reloaded["entries"][0]["industrial_score"] == 77
    assert reloaded["entries"][0]["round_id"] == "r1"
