"""
Agent 2 ("The Colorist") tag color generator.

Assigns visually distinct and legible hex colors to primary tags.
"""

from __future__ import annotations

import colorsys
import json
import re
from typing import Any, Optional

from pydantic import BaseModel

from .claude_client import ClaudeJsonClient


HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_hex_color(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not HEX_COLOR_RE.fullmatch(candidate):
        return None
    return f"#{candidate[1:].upper()}"


def hex_to_hsl(hex_color: str) -> tuple[float, float, float]:
    normalized = normalize_hex_color(hex_color)
    if not normalized:
        raise ValueError(f"Invalid hex color: {hex_color}")
    raw = normalized[1:]
    red = int(raw[0:2], 16) / 255.0
    green = int(raw[2:4], 16) / 255.0
    blue = int(raw[4:6], 16) / 255.0
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    return (hue * 360.0, saturation * 100.0, lightness * 100.0)


def hue_distance_degrees(left_hue: float, right_hue: float) -> float:
    direct = abs(left_hue - right_hue) % 360.0
    return min(direct, 360.0 - direct)


def hsl_to_hex(hue_deg: float, saturation_pct: float, lightness_pct: float) -> str:
    hue = (hue_deg % 360.0) / 360.0
    saturation = max(0.0, min(100.0, saturation_pct)) / 100.0
    lightness = max(0.0, min(100.0, lightness_pct)) / 100.0
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def is_dark_mode_legible(hex_color: str) -> bool:
    hue, saturation, lightness = hex_to_hsl(hex_color)
    if saturation < 45.0:
        return False
    if not (42.0 <= lightness <= 72.0):
        return False
    # Avoid blinding near-neon tones with extreme saturation+lightness.
    if saturation > 94.0 and lightness > 66.0:
        return False
    return True


def _theme_bucket(tag: str) -> str:
    text = (tag or "").lower().strip()
    if any(key in text for key in ("finance", "macro", "econom", "stocks", "rates", "yield")):
        return "finance"
    if any(key in text for key in ("politic", "election", "war", "conflict", "middle east", "geopolit")):
        return "politics"
    if any(
        key in text
        for key in ("tech", "ai", "crypto", "bitcoin", "btc", "ethereum", "eth", "space", "science", "web3")
    ):
        return "tech"
    if any(key in text for key in ("weather", "climate", "environment", "earth", "energy")):
        return "environment"
    if any(key in text for key in ("sport",)):
        return "sports"
    if any(key in text for key in ("pop", "culture", "entertainment", "music", "film")):
        return "culture"
    return "general"


def _min_hue_distance_for_pair(left_tag: str, right_tag: str) -> float:
    # Thematically similar tags: tighter hue rotation. Different themes: wider.
    return 5.0 if _theme_bucket(left_tag) == _theme_bucket(right_tag) else 20.0


def is_hue_contrast_valid(
    candidate_hex: str,
    candidate_tag: str,
    existing_palette: list[dict[str, str]],
) -> bool:
    if not existing_palette:
        return True
    candidate_hue, _, _ = hex_to_hsl(candidate_hex)
    for existing in existing_palette:
        existing_hex = normalize_hex_color(existing.get("hex_color"))
        existing_tag = str(existing.get("tag_label") or "").strip()
        if not existing_hex:
            continue
        existing_hue, _, _ = hex_to_hsl(existing_hex)
        min_hue_distance = _min_hue_distance_for_pair(candidate_tag, existing_tag)
        if hue_distance_degrees(candidate_hue, existing_hue) < min_hue_distance:
            return False
    return True


class Agent2ColorResponse(BaseModel):
    hex_color: str


class Agent2ColoristGenerator:
    """Generates a distinct hex color for a primary tag."""

    def __init__(
        self,
        model: Optional[str] = None,
        prompt_version: str = "v1",
    ) -> None:
        self.prompt_version = prompt_version
        if model and model.strip():
            self.client = ClaudeJsonClient(model=model.strip())
        else:
            self.client = ClaudeJsonClient()
        self.model = self.client.model

    @staticmethod
    def _normalize_palette(raw_palette: Any) -> list[dict[str, str]]:
        if not isinstance(raw_palette, list):
            return []
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for value in raw_palette:
            tag_label = ""
            normalized_hex: Optional[str] = None
            if isinstance(value, str):
                normalized_hex = normalize_hex_color(value)
            elif isinstance(value, dict):
                tag_label = str(value.get("tag_label") or value.get("label") or "").strip()
                normalized_hex = normalize_hex_color(value.get("hex_color"))
            if not normalized_hex:
                continue
            key = f"{tag_label.lower()}|{normalized_hex}"
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"tag_label": tag_label, "hex_color": normalized_hex})
        return normalized

    @staticmethod
    def _theme_hues_and_style(tag: str) -> tuple[list[float], float, float]:
        text = tag.lower().strip()
        if any(key in text for key in ("finance", "macro", "econom", "stocks", "rates", "yield")):
            return ([142.0, 48.0, 62.0], 68.0, 56.0)
        if any(key in text for key in ("politic", "election", "war", "conflict", "middle east", "geopolit")):
            return ([14.0, 28.0, 350.0], 76.0, 56.0)
        if any(key in text for key in ("tech", "ai", "crypto", "space", "science", "web3")):
            return ([192.0, 274.0, 210.0], 82.0, 60.0)
        if any(key in text for key in ("weather", "climate", "environment", "earth", "energy")):
            return ([166.0, 102.0, 186.0], 65.0, 55.0)
        if any(key in text for key in ("sport",)):
            return ([212.0, 24.0, 330.0], 74.0, 57.0)
        if any(key in text for key in ("pop", "culture", "entertainment", "music", "film")):
            return ([318.0, 42.0, 286.0], 75.0, 60.0)
        return ([206.0, 280.0, 36.0, 160.0], 72.0, 58.0)

    @staticmethod
    def _pick_fallback_hex(tag: str, existing_palette: list[dict[str, str]]) -> str:
        base_hues, saturation, lightness = Agent2ColoristGenerator._theme_hues_and_style(tag)
        hue_offsets = [0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180]
        sat_offsets = [0.0, -6.0, 6.0, -10.0, 10.0]
        light_offsets = [0.0, -6.0, 6.0, -10.0, 10.0]

        for base_hue in base_hues:
            for hue_offset in hue_offsets:
                hue = (base_hue + hue_offset) % 360.0
                for sat_offset in sat_offsets:
                    for light_offset in light_offsets:
                        candidate = hsl_to_hex(hue, saturation + sat_offset, lightness + light_offset)
                        if not is_dark_mode_legible(candidate):
                            continue
                        if not is_hue_contrast_valid(candidate, tag, existing_palette):
                            continue
                        return candidate

        # Absolute last-resort deterministic search.
        for hue in range(0, 360, 5):
            candidate = hsl_to_hex(float(hue), 74.0, 58.0)
            if is_dark_mode_legible(candidate) and is_hue_contrast_valid(candidate, tag, existing_palette):
                return candidate
        return "#5AA9FF"

    def _enforce_constraints(self, candidate_hex: Any, tag: str, existing_palette: list[dict[str, str]]) -> str:
        normalized = normalize_hex_color(candidate_hex)
        if normalized and is_dark_mode_legible(normalized):
            if not existing_palette or is_hue_contrast_valid(normalized, tag, existing_palette):
                return normalized
        return self._pick_fallback_hex(tag=tag, existing_palette=existing_palette)

    def generate(self, payload: dict[str, Any]) -> Agent2ColorResponse:
        tag = str(payload.get("new_primary_tag") or "").strip()
        if not tag:
            raise ValueError("Payload must include non-empty new_primary_tag")
        existing_palette = self._normalize_palette(payload.get("existing_palette"))

        rules = (
            "If existing_palette is empty, pick the best thematic color immediately.\n"
            "If existing_palette is non-empty, use hue rotation thresholds:\n"
            "- at least 5 degrees away for thematically similar tags\n"
            "- at least 20 degrees away for thematically different tags\n"
            "Theme mapping: finance/macro -> green/gold; geopolitics/conflict -> red/orange; "
            "tech/ai/space -> cyan/purple; environment -> green/teal.\n"
            "Dark mode legibility: color must pop on dark background; avoid pure blinding neon and muddy low-saturation tones.\n"
            "Return only JSON with key hex_color."
        )
        prompt_payload = {"new_primary_tag": tag, "existing_palette": existing_palette}
        prompt = (
            "Assign a mathematically distinct hex color for a primary tag.\n"
            f"Input payload: {json.dumps(prompt_payload, ensure_ascii=True)}\n"
            f"Hard rules:\n{rules}\n"
        )
        system_instruction = (
            "You are Agent 2 The Colorist, a futuristic trading-terminal design protocol. "
            "Respond with structured JSON only."
        )
        raw = self.client.generate_json(
            prompt=prompt,
            response_schema=Agent2ColorResponse,
            system_instruction=system_instruction,
            temperature=0.1,
        )
        if isinstance(raw, Agent2ColorResponse):
            response = raw
        elif isinstance(raw, dict):
            response = Agent2ColorResponse.model_validate(raw)
        else:
            response = Agent2ColorResponse.model_validate(raw.model_dump())

        enforced_hex = self._enforce_constraints(
            candidate_hex=response.hex_color,
            tag=tag,
            existing_palette=existing_palette,
        )
        return Agent2ColorResponse(hex_color=enforced_hex)