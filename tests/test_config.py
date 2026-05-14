from modules.config import load_config
from tests.helpers import runtime_dir


def test_config_loading():
    workspace = runtime_dir("config")
    config = workspace / "config.yaml"
    config.write_text(
        """
default_input_path: "C:\\\\Books"
default_model: gpt-4.1
fallback_model: gpt-4.1-mini
skip_directories: [artifacts]
supported_files:
  manuscripts: [.docx]
supplemental_text_directories: [nicht_hochladen]
skills_directory: skills
memory_path: artifacts/agent_memory.json
""",
        encoding="utf-8",
    )

    loaded = load_config(config)

    assert loaded.default_model == "gpt-4.1"
    assert loaded.fallback_model == "gpt-4.1-mini"
    assert "artifacts" in loaded.skip_directories
    assert "nicht_hochladen" in loaded.supplemental_text_directories
    assert str(loaded.skills_directory) == "skills"
    assert str(loaded.memory_path).replace("\\", "/") == "artifacts/agent_memory.json"


def _minimal_config(workspace_name: str, extra: str = "") -> object:
    workspace = runtime_dir(workspace_name)
    config = workspace / "config.yaml"
    config.write_text(
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n"
        + extra,
        encoding="utf-8",
    )
    return load_config(config)


def test_positioning_limit_defaults_to_one():
    loaded = _minimal_config("config_positioning_default")
    assert loaded.beginner_summary_positioning_limit == 1


def test_positioning_limit_reads_yaml_value():
    loaded = _minimal_config(
        "config_positioning_yaml",
        extra="beginner_summary_positioning_limit: 3\n",
    )
    assert loaded.beginner_summary_positioning_limit == 3


def test_positioning_limit_clamps_above_three():
    """Values above 3 must clamp to the TOP_POSITIONING_MAX_LIMIT so the
    summary never gets crowded with low-strength signals."""
    loaded = _minimal_config(
        "config_positioning_high",
        extra="beginner_summary_positioning_limit: 12\n",
    )
    assert loaded.beginner_summary_positioning_limit == 3


def test_positioning_limit_clamps_below_one():
    """Values <1 must clamp to 1 so the strongest angle is always shown
    when a real positioning signal exists."""
    loaded = _minimal_config(
        "config_positioning_low",
        extra="beginner_summary_positioning_limit: 0\n",
    )
    assert loaded.beginner_summary_positioning_limit == 1
