from __future__ import annotations

import json
import os
import re
from typing import Any

from modules.config import AppConfig, ConfigError
from modules.run_logger import RunLogger

class LLMClient:
    def __init__(self, config: AppConfig, logger: RunLogger):
        self.config = config
        self.logger = logger
        try:
            from dotenv import load_dotenv
            load_dotenv(config.project_root / ".env")
        except ImportError:
            pass
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    def require_api_key(self) -> None:
        if not self.api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY fehlt. Trage ihn in die .env-Datei ein:\n"
                "  ANTHROPIC_API_KEY=sk-ant-...\n"
                "Keine Quelldatei wurde veraendert."
            )

    def complete(self, system: str, user: str, model: str | None = None) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ConfigError("The anthropic package is not installed. Run: pip install -r requirements.txt") from exc

        selected_model = model or self.config.default_model
        client = anthropic.Anthropic(api_key=self.api_key)
        self.logger.log("model_call_started", model=selected_model)

        try:
            response = client.messages.create(
                model=selected_model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=self.config.temperature,
            )
            text = response.content[0].text if response.content else ""
            self.logger.log("model_call_completed", model=selected_model, chars=len(text))
            return text.strip()
        except Exception as first_error:
            self.logger.log("model_call_error", model=selected_model, error=str(first_error))
            fallback = self.config.fallback_model
            if fallback and fallback != selected_model:
                try:
                    response = client.messages.create(
                        model=fallback,
                        max_tokens=4096,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                        temperature=self.config.temperature,
                    )
                    text = response.content[0].text if response.content else ""
                    self.logger.log("model_call_completed", model=fallback, chars=len(text))
                    return text.strip()
                except Exception as fallback_error:
                    self.logger.log("model_call_error", model=fallback, error=str(fallback_error))
            raise ConfigError(
                f"Claude API call failed. Original error: {first_error}"
            ) from first_error

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        text = self.complete(system, user)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if match:
                return json.loads(match.group(0))
            raise ConfigError("Model did not return valid JSON.")
