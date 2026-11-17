"""Thin, provider-agnostic LLM client with JSON parsing and rate-limit backoff."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from .config import MAX_OUTPUT_TOKENS, TEMPERATURE, Provider


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Wraps the OpenAI SDK pointed at any OpenAI-compatible endpoint."""

    def __init__(self, provider: Provider, api_key: str, model: str | None = None,
                 on_wait: Callable[[float], None] | None = None):
        from openai import OpenAI  # imported lazily so core logic is testable without it

        if not api_key:
            raise LLMError(f"No API key for {provider.name}. Add {provider.key_env} to .env "
                           "or paste it in the sidebar.")
        self.provider = provider
        self.model = model or provider.default_model
        self.on_wait = on_wait
        self._client = OpenAI(base_url=provider.base_url, api_key=api_key, max_retries=0)
        self._json_mode_ok = True

    # ---- core call ---------------------------------------------------------
    def chat(self, system: str, user: str, json_mode: bool = False,
             max_tokens: int = MAX_OUTPUT_TOKENS, retries: int = 5) -> str:
        import openai

        kwargs: dict[str, Any] = dict(
            model=self.model,
            temperature=TEMPERATURE,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        if json_mode and self._json_mode_ok:
            kwargs["response_format"] = {"type": "json_object"}

        delay = 4.0
        for attempt in range(retries + 1):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or ""
            except openai.RateLimitError as e:
                if attempt == retries:
                    raise LLMError("Rate limit reached on the free tier. Wait a minute or "
                                   "lower the batch size in the sidebar.") from e
                wait = _retry_after(e) or delay
                if self.on_wait:
                    self.on_wait(wait)
                time.sleep(wait)
                delay = min(delay * 2, 60)
            except openai.BadRequestError as e:
                # Some models/endpoints reject response_format — retry once without it.
                if "response_format" in kwargs:
                    kwargs.pop("response_format")
                    self._json_mode_ok = False
                    continue
                raise LLMError(f"Request rejected: {e}") from e
            except openai.AuthenticationError as e:
                raise LLMError("API key was rejected — check it and try again.") from e
            except openai.APIConnectionError as e:
                raise LLMError(f"Could not reach {self.provider.name}. "
                               "Check your internet (or that Ollama is running).") from e
        raise LLMError("Unreachable")

    def chat_json(self, system: str, user: str, **kw) -> dict:
        """Ask for JSON; if the reply doesn't parse, ask once more to repair it."""
        raw = self.chat(system, user, json_mode=True, **kw)
        try:
            return parse_json(raw)
        except ValueError:
            fixed = self.chat(
                "You repair malformed JSON. Return only valid JSON, nothing else.",
                raw, json_mode=True, **kw)
            return parse_json(fixed)


def parse_json(text: str) -> dict:
    """Parse JSON from a model reply, tolerating code fences and leading prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("No JSON object found in model output")
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON from model: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("Expected a JSON object")
    return obj


def _retry_after(err: Exception) -> float | None:
    try:
        val = err.response.headers.get("retry-after")  # type: ignore[attr-defined]
        return float(val) + 0.5 if val else None
    except Exception:
        return None
