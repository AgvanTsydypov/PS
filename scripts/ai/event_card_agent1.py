"""
Agent 1 ("The Quant") event card generator.

Builds strict card payloads from event metadata with Gemini JSON mode,
then enforces hard output constraints deterministically.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel

from .gemini_client import GeminiJsonClient


FOUNDATIONAL_TAG_PRIORITY = [
    "Politics",
    "Crypto",
    "Sports",
    "Pop Culture",
    "Economics",
    "Tech",
    "Finance",
    "Weather",
    "Science",
    "World",
]

NOISE_TAGS = {
    "recurring",
    "multi strikes",
    "crypto prices",
    "prices",
    "price",
    "daily",
    "weekly",
}


class Agent1CardResponse(BaseModel):
    card_title: str
    card_lore: str
    primary_tag: str
    secondary_tag: Optional[str] = None


class Agent1QuantCardGenerator:
    """Generates event cards under strict output constraints."""

    def __init__(
        self,
        model: Optional[str] = None,
        prompt_version: str = "v1",
    ) -> None:
        self.prompt_version = prompt_version
        if model and model.strip():
            self.client = GeminiJsonClient(model=model.strip())
        else:
            # Fall back to GeminiJsonClient default model.
            self.client = GeminiJsonClient()
        self.model = self.client.model

    @staticmethod
    def _normalize_tags(tags: Any) -> list[str]:
        if not isinstance(tags, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            if tag is None:
                continue
            label = str(tag).strip()
            if not label:
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(label)
        return normalized

    @staticmethod
    def _is_recurring(series: Any) -> bool:
        values: list[str] = []
        if isinstance(series, str):
            values.append(series)
        elif isinstance(series, dict):
            for key in ("title", "recurrence", "series_type", "subtitle", "slug"):
                if series.get(key):
                    values.append(str(series.get(key)))
        elif isinstance(series, list):
            for item in series:
                if isinstance(item, str):
                    values.append(item)
                elif isinstance(item, dict):
                    for key in ("title", "recurrence", "series_type", "subtitle", "slug"):
                        if item.get(key):
                            values.append(str(item.get(key)))
        merged = " ".join(values).lower()
        return "daily" in merged or "weekly" in merged

    @staticmethod
    def _clean_title(title: str, fallback_title: str) -> str:
        value = re.sub(r"\s+", " ", (title or "").strip())
        value = value.replace("?", "").replace("...", "")
        words = value.split()
        if len(words) > 7:
            value = " ".join(words[:7]).strip()
        if not value:
            base_words = re.sub(r"\s+", " ", fallback_title.strip()).split()
            value = " ".join(base_words[:7]) if base_words else "Oracle Signal Card"
        return value

    @staticmethod
    def _clean_lore(lore: str, recurring: bool) -> str:
        text = re.sub(r"\s+", " ", (lore or "").strip())
        if not text:
            if recurring:
                text = (
                    "Recurring consensus matrix tracks daily/weekly repricing. "
                    "Probability shifts follow liquidity and resistance levels. "
                    "Oracle resolution updates each cycle close."
                )
            else:
                text = (
                    "Single-resolution contract with finite oracle settlement. "
                    "Probability and liquidity profile define terminal pricing. "
                    "Consensus converges at resolution timestamp."
                )

        sentences = re.split(r"(?<=[.!?])\s+", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) > 3:
            sentences = sentences[:3]
        compact = " ".join(sentences)
        words = compact.split()
        if len(words) > 49:
            compact = " ".join(words[:49]).rstrip(" ,;:")
            if compact and compact[-1] not in ".!?":
                compact += "."
        return compact

    @staticmethod
    def _choose_primary_tag(tags: list[str]) -> str:
        if not tags:
            raise ValueError("Cannot select primary_tag from empty tags")
        by_lower = {t.lower(): t for t in tags}
        for candidate in FOUNDATIONAL_TAG_PRIORITY:
            found = by_lower.get(candidate.lower())
            if found:
                return found
        for tag in tags:
            if tag.lower() not in NOISE_TAGS:
                return tag
        return tags[0]

    @staticmethod
    def _choose_secondary_tag(tags: list[str], primary_tag: str) -> Optional[str]:
        if len(tags) <= 1:
            return None
        candidates = [tag for tag in tags if tag.lower() != primary_tag.lower()]
        if not candidates:
            return None
        non_noise = [tag for tag in candidates if tag.lower() not in NOISE_TAGS]
        if non_noise:
            # Prefer the most specific label by length.
            return sorted(non_noise, key=lambda x: len(x), reverse=True)[0]
        return candidates[0]

    def _enforce_constraints(
        self,
        raw: Agent1CardResponse,
        event_title: str,
        tags: list[str],
        recurring: bool,
    ) -> Agent1CardResponse:
        primary = raw.primary_tag if raw.primary_tag in tags else self._choose_primary_tag(tags)

        if len(tags) <= 1:
            secondary = None
        elif raw.secondary_tag and raw.secondary_tag in tags and raw.secondary_tag != primary:
            secondary = raw.secondary_tag
        else:
            secondary = self._choose_secondary_tag(tags, primary)

        return Agent1CardResponse(
            card_title=self._clean_title(raw.card_title, fallback_title=event_title),
            card_lore=self._clean_lore(raw.card_lore, recurring=recurring),
            primary_tag=primary,
            secondary_tag=secondary,
        )

    def generate(self, payload: dict[str, Any]) -> Agent1CardResponse:
        event_title = str(payload.get("title") or "").strip()
        event_description = str(payload.get("description") or "").strip()
        tags = self._normalize_tags(payload.get("tags"))
        if not tags:
            raise ValueError("Event payload must include at least one tag")

        recurring = self._is_recurring(payload.get("series"))
        recurring_rule = (
            "Series recurrence is daily/weekly. Frame lore as recurring consensus matrix "
            "or ongoing volatility tracker."
            if recurring
            else "No recurring series context. Treat this as singular oracle resolution."
        )

        prompt = (
            "Generate a strict JSON object for a trader card.\n"
            "Input event payload:\n"
            f"- title: {event_title}\n"
            f"- description: {event_description}\n"
            f"- series: {payload.get('series')}\n"
            f"- tags: {tags}\n\n"
            "Hard rules:\n"
            "- card_title: max 7 words, no question mark, no trailing ellipsis.\n"
            "- card_lore: max 3 sentences, under 50 words, cold analytical quant-terminal tone.\n"
            "- primary_tag: must be exactly one string from provided tags array.\n"
            "- secondary_tag: must be distinct tag from provided tags array; null only if one tag exists.\n"
            f"- recurrence rule: {recurring_rule}\n"
        )

        system_instruction = (
            "You are Agent 1 The Quant, a Web3 prediction-market terminal. "
            "Return only structured JSON and follow constraints exactly."
        )

        raw = self.client.generate_json(
            prompt=prompt,
            response_schema=Agent1CardResponse,
            system_instruction=system_instruction,
            temperature=0.2,
        )
        if isinstance(raw, Agent1CardResponse):
            result = raw
        elif isinstance(raw, dict):
            result = Agent1CardResponse.model_validate(raw)
        else:
            result = Agent1CardResponse.model_validate(raw.model_dump())

        return self._enforce_constraints(
            raw=result,
            event_title=event_title,
            tags=tags,
            recurring=recurring,
        )
