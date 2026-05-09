from modules.agent_core import AgentMemory, SkillRegistry
from modules.discovery import BookProject
from tests.helpers import runtime_dir


def test_skill_registry_loads_yaml_skills():
    workspace = runtime_dir("skills")
    skills_dir = workspace / "skills"
    skills_dir.mkdir()
    (skills_dir / "kindle.yaml").write_text(
        """
name: kindle
purpose: Check Kindle production.
checks:
  - toc
heuristics:
  - sample matters
""",
        encoding="utf-8",
    )

    registry = SkillRegistry(skills_dir)

    assert registry.names() == ["kindle"]
    assert "sample matters" in registry.prompt_context()


def test_agent_memory_records_project_and_qa():
    workspace = runtime_dir("memory")
    memory = AgentMemory(workspace / "artifacts" / "agent_memory.json")
    project = BookProject(
        project_id="book",
        root=workspace,
        title="Title",
        author="Author",
        amazon_description="Description",
    )

    memory.remember_project(project)
    memory.remember_qa(project, {
        "decision": "GO_AFTER_FIXES",
        "industrial_score": 91,
        "investor_grade": 9.1,
        "all_required_fixes": ["Check Kindle sample."],
    })
    memory.save()

    reloaded = AgentMemory(workspace / "artifacts" / "agent_memory.json")
    snapshot = reloaded.snapshot("book")

    assert snapshot["project_memory"]["open_risks"] == ["Check Kindle sample."]
    assert snapshot["project_memory"]["rounds"][-1]["industrial_score"] == 91
