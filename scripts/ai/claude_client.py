"""
Lightweight Claude client with JSON output validation.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import Any, Type

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()


class ClaudeJsonClient:
    """Minimal wrapper around Anthropic Messages API for structured outputs."""

    _global_limiter_lock = threading.Lock()
    _model_locks: dict[str, threading.Lock] = {}
    _model_timestamps: dict[str, deque[float]] = defaultdict(deque)

    def __init__(
        self,
        model: str = "claude-opus-4-6",
        api_key_env_var: str = "ANTHROPIC_API_KEY",
        requests_per_minute: int = 15,
        timeout_seconds: int = 60,
    ) -> None:
        api_key = os.getenv(api_key_env_var, "").strip()
        if not api_key:
            raise ValueError(f"{api_key_env_var} is not set")
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be > 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self.model = model
        self.api_key = api_key
        self.requests_per_minute = requests_per_minute
        self.timeout_seconds = timeout_seconds

        with self._global_limiter_lock:
            if self.model not in self._model_locks:
                self._model_locks[self.model] = threading.Lock()

    def _wait_for_rate_limit(self) -> None:
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

    @staticmethod
    def _extract_text_from_response(payload: dict[str, Any]) -> str:
        content = payload.get("content")
        if not isinstance(content, list):
            raise ValueError("Claude response missing content blocks")

        chunks: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    chunks.append(text)

        if not chunks:
            raise ValueError("Claude returned empty text content")
        return "\n".join(chunks).strip()

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        value = (text or "").strip()
        if not value:
            return value
        fence_match = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", value, flags=re.DOTALL | re.IGNORECASE)
        if fence_match:
            return fence_match.group(1).strip()
        return value

    @staticmethod
    def _extract_first_json_object(text: str) -> str:
        value = (text or "").strip()
        if not value:
            return value
        start = value.find("{")
        if start < 0:
            return value
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(value)):
            ch = value[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
                continue
            if ch == "}":
                depth -= 1
                if depth == 0:
                    return value[start : idx + 1].strip()
        return value

    @staticmethod
    def _normalize_json_text(text: str) -> str:
        stripped = ClaudeJsonClient._strip_code_fences(text)
        return ClaudeJsonClient._extract_first_json_object(stripped)

    def _call_messages_api(
        self,
        *,
        user_prompt: str,
        system_text: str,
        temperature: float,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        req_body = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "system": system_text,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=req_body,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise ValueError(f"Claude API error {response.status_code}: {response.text}")
        return response.json()

    def generate_json(
        self,
        prompt: str,
        response_schema: dict[str, Any] | Type[BaseModel],
        *,
        temperature: float = 0.1,
        max_output_tokens: int = 2048,
        system_instruction: str | None = None,
    ) -> dict[str, Any] | BaseModel:
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")

        self._wait_for_rate_limit()

        schema_hint = ""
        if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
            schema_hint = json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
        elif isinstance(response_schema, dict):
            schema_hint = json.dumps(response_schema, ensure_ascii=False)

        user_prompt = (
            f"{prompt.strip()}\n\n"
            "Return ONLY valid JSON. No markdown, no explanations.\n"
            "JSON schema to follow:\n"
            f"{schema_hint}"
        )
        system_text = (system_instruction or "").strip()
        if not system_text:
            system_text = "Return strictly valid JSON that matches the provided schema."
        attempts = (
            (user_prompt, system_text),
            (
                user_prompt
                + "\n\nCRITICAL: Return a raw JSON object only. Do not wrap in markdown fences.",
                system_text
                + " Output must be a single raw JSON object string without markdown code fences.",
            ),
        )
        last_error: Exception | None = None
        for attempt_user_prompt, attempt_system_text in attempts:
            payload = self._call_messages_api(
                user_prompt=attempt_user_prompt,
                system_text=attempt_system_text,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            raw_text = self._extract_text_from_response(payload)
            normalized_json = self._normalize_json_text(raw_text)

            if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
                try:
                    return response_schema.model_validate_json(normalized_json)
                except ValidationError as exc:
                    last_error = exc
                    continue
            else:
                try:
                    return json.loads(normalized_json)
                except json.JSONDecodeError as exc:
                    last_error = exc
                    continue

        if isinstance(last_error, ValidationError):
            raise ValueError(f"Response does not match schema: {last_error}") from last_error
        if isinstance(last_error, json.JSONDecodeError):
            raise ValueError(f"Invalid JSON response: {last_error}") from last_error
        raise ValueError("Claude returned a non-JSON response")
