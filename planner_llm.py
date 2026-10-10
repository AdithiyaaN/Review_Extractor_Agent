from __future__ import annotations

import json
from typing import Any

from groq import Groq


MODEL = "openai/gpt-oss-120b"


SYSTEM = """You are LLM #1, the DISCOVERY ENGINE for a generic review screenshot scraper.

Your job is high-recall reverse engineering. Do NOT select one final answer.
Generate ranked hypotheses for:
1. individual review selectors
2. review identity strategies
3. review screenshot boundaries
4. navigation mechanisms
5. load-more / pagination controls
6. possible lazy-loading behavior

Use ONLY the supplied evidence. Never invent selectors.

Important:
- A unique element ID is usually NOT a collection selector if it matches 1 element.
- Prefer selectors matching repeated review elements.
- Consider virtualization/recycled DOM nodes.
- Separate individual-review selectors from review-container selectors.
- Return multiple candidates when evidence supports them.

Return JSON only."""


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "review_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "selector": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                    "identity_candidates": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["selector", "reason", "confidence", "identity_candidates"]
            },
            "maxItems": 8
        },
        "navigation_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string", "enum": ["load_more", "pagination", "internal_scroll", "page_scroll", "unknown"]},
                    "selector": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"}
                },
                "required": ["type", "selector", "reason", "confidence"]
            },
            "maxItems": 8
        },
        "risk_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 10}
    },
    "required": ["review_candidates", "navigation_candidates", "risk_flags"]
}


def _compact(obs: dict[str, Any]) -> dict[str, Any]:
    return {
        "page": obs["page"],
        "review_candidates": obs["review_candidates"][:18],
        "action_candidates": obs["action_candidates"][:18],
        "scroll_candidates": obs["scroll_candidates"][:10],
        "repeated_groups": obs["repeated_groups"][:12],
    }


def discover(client: Groq, observation: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(_compact(observation), separators=(",", ":"), ensure_ascii=False)
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        max_completion_tokens=1800,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": payload},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "discovery", "strict": True, "schema": SCHEMA}},
    )
    return json.loads(resp.choices[0].message.content)
