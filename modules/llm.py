from __future__ import annotations

import json
import os
import re
from typing import Any

from modules.config import AppConfig, ConfigError
from modules.run_logger import RunLogger

MAX_TOKENS = 4096


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

    def _call_model(self, model: str, system: str, user: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ConfigError(
                "The anthropic package is not installed. Run: pip install -r requirements.txt"
            ) from exc

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=self.config.temperature,
        )
        text = response.content[0].text if response.content else ""
        return text.strip()

    def complete(self, system: str, user: str, model: str | None = None) -> str:
        primary_model = model or self.config.default_model
        fallback_model = self.config.fallback_model

        self.logger.log("model_call_started", model=primary_model)
        try:
            text = self._call_model(primary_model, system, user)
            self.logger.log("model_call_completed", model=primary_model, chars=len(text))
            return text
        except Exception as primary_error:
            self.logger.log(
                "model_call_error",
                model=primary_model,
                error=str(primary_error),
            )

            if not fallback_model or fallback_model == primary_model:
                raise ConfigError(
                    f"Claude API call failed and no fallback model is configured. "
                    f"Original error: {primary_error}"
                ) from primary_error

            self.logger.log(
                "model_fallback_started",
                primary_model=primary_model,
                fallback_model=fallback_model,
            )
            try:
                text = self._call_model(fallback_model, system, user)
                self.logger.log(
                    "model_fallback_completed",
                    fallback_model=fallback_model,
                    chars=len(text),
                )
                return text
            except Exception as fallback_error:
                self.logger.log(
                    "model_call_error",
                    model=fallback_model,
                    error=str(fallback_error),
                )
                raise ConfigError(
                    "Claude API call failed for both the primary model "
                    f"({primary_model}) and the fallback model ({fallback_model}). "
                    f"Original error: {primary_error}. "
                    f"Fallback error: {fallback_error}."
                ) from fallback_error

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        text = self.complete(system, user)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if match:
                return json.loads(match.group(0))
            raise ConfigError("Model did not return valid JSON.")
