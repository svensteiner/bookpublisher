from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

from modules.config import AppConfig, ConfigError
from modules.run_logger import RunLogger

MAX_TOKENS = 4096


class LLMClient:
    # Sleep hook for exponential backoff between retries. Tests override
    # this attribute (on the instance) with a no-op so they don't actually
    # wait on the wall clock.
    _sleep: Callable[[float], None] = staticmethod(time.sleep)

    def __init__(self, config: AppConfig, logger: RunLogger):
        self.config = config
        self.logger = logger
        try:
            from dotenv import load_dotenv
            load_dotenv(config.project_root / ".env")
        except ImportError:
            pass
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        # Fallback-usage tracking — surfaced to beginner_summary so the
        # author can tell when a run leaned on the cheaper fallback model
        # (lower review depth) rather than the primary model.
        self._primary_calls: int = 0
        self._fallback_calls: int = 0
        self._last_primary_model: str | None = None
        self._last_fallback_model: str | None = None

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

    def _call_with_retries(
        self, model: str, system: str, user: str, *, label: str
    ) -> str:
        """Call ``_call_model`` with exponential backoff retries.

        Per ``AppConfig.llm_retry_attempts`` total attempts (>=1). Between
        attempts, sleeps ``backoff * 2**(attempt-1)`` seconds where
        ``backoff = AppConfig.llm_retry_backoff_seconds``. ``ConfigError``
        is treated as a hard error (e.g., anthropic package not installed)
        and is NOT retried — it propagates immediately.

        ``label`` is logged alongside each error/retry so beginner_summary
        traces can distinguish primary-model retries from fallback-model
        retries.
        """

        attempts = max(1, int(self.config.llm_retry_attempts))
        base_delay = max(0.0, float(self.config.llm_retry_backoff_seconds))
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                return self._call_model(model, system, user)
            except ConfigError:
                # Hard config error (e.g., anthropic missing) — no point retrying.
                raise
            except Exception as exc:
                last_error = exc
                self.logger.log(
                    "model_call_error",
                    model=model,
                    error=str(exc),
                    attempt=attempt,
                    max_attempts=attempts,
                    label=label,
                )
                if attempt >= attempts:
                    break
                wait_seconds = base_delay * (2 ** (attempt - 1))
                self.logger.log(
                    "model_call_retry",
                    model=model,
                    next_attempt=attempt + 1,
                    wait_seconds=wait_seconds,
                    label=label,
                )
                if wait_seconds > 0:
                    self._sleep(wait_seconds)

        assert last_error is not None
        raise last_error

    def complete(self, system: str, user: str, model: str | None = None) -> str:
        primary_model = model or self.config.default_model
        fallback_model = self.config.fallback_model
        self._last_primary_model = primary_model

        self.logger.log("model_call_started", model=primary_model)
        try:
            text = self._call_with_retries(primary_model, system, user, label="primary")
            self.logger.log("model_call_completed", model=primary_model, chars=len(text))
            self._primary_calls += 1
            return text
        except ConfigError:
            # Hard error (anthropic package missing, etc.) — propagate as-is.
            raise
        except Exception as primary_error:
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
                text = self._call_with_retries(
                    fallback_model, system, user, label="fallback"
                )
                self.logger.log(
                    "model_fallback_completed",
                    fallback_model=fallback_model,
                    chars=len(text),
                )
                self._fallback_calls += 1
                self._last_fallback_model = fallback_model
                return text
            except Exception as fallback_error:
                raise ConfigError(
                    "Claude API call failed for both the primary model "
                    f"({primary_model}) and the fallback model ({fallback_model}). "
                    f"Original error: {primary_error}. "
                    f"Fallback error: {fallback_error}."
                ) from fallback_error

    def fallback_summary(self) -> dict[str, Any] | None:
        """Return a compact summary of fallback-model usage in this run.

        Returns ``None`` when no model calls have happened yet OR no
        fallback was ever triggered — that's the silent-success case
        the beginner_summary should not surface.

        When at least one fallback call succeeded, returns a dict with
        the primary/fallback model names and the call counts so the
        author understands which model produced the review depth.
        """

        if self._fallback_calls <= 0:
            return None
        total_calls = self._primary_calls + self._fallback_calls
        return {
            "fallback_used": True,
            "primary_model": self._last_primary_model,
            "fallback_model": self._last_fallback_model,
            "primary_calls": self._primary_calls,
            "fallback_calls": self._fallback_calls,
            "total_calls": total_calls,
        }

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        text = self.complete(system, user)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if match:
                return json.loads(match.group(0))
            raise ConfigError("Model did not return valid JSON.")
