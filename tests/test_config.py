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
