import pytest

from modules.config import AppConfig, ConfigError
from modules.llm import LLMClient
from modules.run_logger import RunLogger
from tests.helpers import runtime_dir


def test_missing_openai_key_handling(monkeypatch):
    workspace = runtime_dir("llm")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = AppConfig(
        project_root=workspace,
        default_input_path=workspace,
        default_model="gpt-4.1",
        fallback_model="gpt-4.1-mini",
    )
    client = LLMClient(config, RunLogger(workspace / "logs"))

    with pytest.raises(ConfigError):
        client.require_api_key()
