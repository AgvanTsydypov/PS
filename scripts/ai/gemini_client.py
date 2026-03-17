"""
Lightweight Gemini client with JSON mode and schema enforcement.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from typing import Any, Type

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

load_dotenv()


class GeminiJsonClient:
    """Minimal wrapper around Google GenAI SDK for structured outputs."""
    _global_limiter_lock = threading.Lock()
    _model_locks: dict[str, threading.Lock] = {}
    _model_timestamps: dict[str, deque[float]] = defaultdict(deque)

    def __init__(
        self,
        # model: str = "gemini-2.5-flash",
        model: str = "gemini-3.1-flash-lite-preview",
        api_key_env_var: str = "GEMINI_API_KEY",
        requests_per_minute: int = 15,
    ) -> None:
        api_key = os.getenv(api_key_env_var, "").strip()
        if not api_key:
            raise ValueError(f"{api_key_env_var} is not set")
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be > 0")

        self.model = model
        self.requests_per_minute = requests_per_minute
        self.client = genai.Client(api_key=api_key)

        with self._global_limiter_lock:
            if self.model not in self._model_locks:
                self._model_locks[self.model] = threading.Lock()

    def _wait_for_rate_limit(self) -> None:
        """
        Shared per-model RPM limiter.
        Blocks when request rate exceeds configured limit.
        """
        model_lock = self._model_locks[self.model]
        timestamps = self._model_timestamps[self.model]
        window_seconds = 60.0
        while True:
            with model_lock:
                now = time.monotonic()
                while timestamps and (now - timestamps[0]) >= window_seconds:
                    timestamps.popleft()
                if len(timestamps) < self.requests_per_minute:
                    timestamps.append(now)
                    return

                wait_seconds = window_seconds - (now - timestamps[0]) + 0.01
            time.sleep(max(wait_seconds, 0.01))

    def generate_json(
        self,
        prompt: str,
        response_schema: dict[str, Any] | Type[BaseModel],
        *,
        temperature: float = 0.1,
        max_output_tokens: int = 2048,
        system_instruction: str | None = None,
    ) -> dict[str, Any] | BaseModel:
        """
        Generate a structured JSON response validated against schema.

        Args:
            prompt: User prompt sent to Gemini.
            response_schema: JSON Schema dict or Pydantic model class.
            temperature: Sampling temperature.
            max_output_tokens: Maximum model output tokens.
            system_instruction: Optional system instruction text.
        """
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")

        self._wait_for_rate_limit()

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt.strip(),
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
                response_schema=response_schema,
                system_instruction=system_instruction.strip() if system_instruction else None,
            ),
        )

        if response.parsed is not None:
            parsed = response.parsed
            if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
                return response_schema.model_validate(parsed)
            if isinstance(parsed, dict):
                return parsed

        raw_json = (response.text or "").strip()
        if not raw_json:
            raise ValueError("Gemini returned an empty response")

        if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
            try:
                return response_schema.model_validate_json(raw_json)
            except ValidationError as exc:
                raise ValueError(f"Response does not match schema: {exc}") from exc

        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON response: {exc}") from exc

        return payload
