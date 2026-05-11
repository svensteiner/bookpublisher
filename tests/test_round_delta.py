from modules.agent_core import AgentMemory
from modules.discovery import BookProject
from modules.round_delta import compute_round_delta, render_round_delta_markdown
from tests.helpers import runtime_dir


def _project(project_id: str = "book") -> BookProject:
    return BookProject(
        project_id=project_id,
        root=runtime_dir("round_delta"),
        title="Mein Sachbuch",
        author="Sven Steiner",
    )


def test_compute_round_delta_first_round_has_no_previous():
    rounds = [
        {
            "round_id": "r1",
            "decision": "GO_AFTER_FIXES",
            "industrial_score": 80,
            "investor_grade": 8.0,
            "required_fixes": ["Cover schärfen.", "Sample stärken."],
        }
    ]

    delta = compute_round_delta("book", rounds)

    assert delta is not None
    assert delta.has_previous is False
    assert delta.previous_round is None
    assert delta.current_round["round_id"] == "r1"
    assert delta.score_delta is None
    assert delta.resolved_fixes == ()
    assert delta.persistent_fixes == ()
    assert set(delta.new_fixes) == {"Cover schärfen.", "Sample stärken."}


def test_compute_round_delta_classifies_resolved_persistent_new():
    rounds = [
        {
            "round_id": "r1",
            "decision": "GO_AFTER_FIXES",
            "industrial_score": 70,
            "investor_grade": 7.0,
            "required_fixes": ["Cover schärfen.", "Sample stärken.", "Description kürzen."],
        },
        {
            "round_id": "r2",
            "decision": "GO",
            "industrial_score": 85,
            "investor_grade": 8.5,
            "required_fixes": ["Sample stärken.", "Keywords ausfüllen."],
        },
    ]

    delta = compute_round_delta("book", rounds)

    assert delta is not None
    assert delta.has_previous is True
    assert delta.score_delta == 15
    assert delta.investor_grade_delta == 1.5
    assert delta.decision_changed is True
    assert set(delta.resolved_fixes) == {"Cover schärfen.", "Description kürzen."}
    assert delta.persistent_fixes == ("Sample stärken.",)
    assert delta.new_fixes == ("Keywords ausfüllen.",)


def test_compute_round_delta_returns_none_for_empty_rounds():
    assert compute_round_delta("book", []) is None


def test_compute_round_delta_explicit_round_ids():
    rounds = [
        {"round_id": "a", "industrial_score": 50, "required_fixes": ["x"]},
        {"round_id": "b", "industrial_score": 60, "required_fixes": ["y"]},
        {"round_id": "c", "industrial_score": 70, "required_fixes": ["z"]},
    ]

    delta = compute_round_delta("book", rounds, current_round_id="c", previous_round_id="a")

    assert delta is not None
    assert delta.current_round["round_id"] == "c"
    assert delta.previous_round is not None
    assert delta.previous_round["round_id"] == "a"
    assert delta.score_delta == 20
    assert delta.resolved_fixes == ("x",)
    assert delta.new_fixes == ("z",)


def test_render_round_delta_markdown_includes_sections():
    rounds = [
        {
            "round_id": "r1",
            "ts": "2026-05-10T12:00:00",
            "decision": "FIX",
            "industrial_score": 60,
            "investor_grade": 6.0,
            "required_fixes": ["Alt-Fix erledigt.", "Bleibt offen."],
        },
        {
            "round_id": "r2",
            "ts": "2026-05-11T12:00:00",
            "decision": "GO_AFTER_FIXES",
            "industrial_score": 75,
            "investor_grade": 7.5,
            "required_fixes": ["Bleibt offen.", "Neu aufgetaucht."],
        },
    ]
    delta = compute_round_delta("book", rounds)
    assert delta is not None

    markdown = render_round_delta_markdown(_project(), delta)

    assert "Runden-Delta" in markdown
    assert "Alt-Fix erledigt." in markdown
    assert "Bleibt offen." in markdown
    assert "Neu aufgetaucht." in markdown
    assert "60 → 75 (+15)" in markdown
    assert "geändert" in markdown


def test_render_round_delta_markdown_handles_missing_previous():
    rounds = [
        {
            "round_id": "r1",
            "decision": "FIX",
            "industrial_score": 55,
            "required_fixes": ["Eins.", "Zwei."],
        }
    ]
    delta = compute_round_delta("book", rounds)
    assert delta is not None

    markdown = render_round_delta_markdown(_project(), delta)

    assert "erste aufgezeichnete Runde" in markdown
    assert "Industrial-Score: 55" in markdown


def test_agent_memory_compare_rounds_after_two_qa_runs():
    workspace = runtime_dir("delta_memory")
    memory_path = workspace / "artifacts" / "agent_memory.json"
    memory = AgentMemory(memory_path)
    project = BookProject(
        project_id="book",
        root=workspace,
        title="Title",
        author="Author",
    )
    memory.remember_project(project)
    memory.remember_qa(project, {
        "decision": "FIX",
        "industrial_score": 60,
        "investor_grade": 6.0,
        "all_required_fixes": ["Cover schärfen.", "Description kürzen."],
    }, round_id="r1")
    memory.remember_qa(project, {
        "decision": "GO_AFTER_FIXES",
        "industrial_score": 78,
        "investor_grade": 7.8,
        "all_required_fixes": ["Description kürzen.", "Keywords befüllen."],
    }, round_id="r2")

    delta = memory.compare_rounds("book")

    assert delta is not None
    assert delta.has_previous is True
    assert delta.score_delta == 18
    assert delta.resolved_fixes == ("Cover schärfen.",)
    assert delta.persistent_fixes == ("Description kürzen.",)
    assert delta.new_fixes == ("Keywords befüllen.",)


def test_agent_memory_compare_rounds_with_no_history_returns_none():
    workspace = runtime_dir("delta_empty")
    memory = AgentMemory(workspace / "artifacts" / "agent_memory.json")
    assert memory.compare_rounds("unknown_project") is None
