from __future__ import annotations

import json
from typing import Any

from groq import Groq


MODEL = "openai/gpt-oss-120b"

SYSTEM = """You are LLM #3, the FINAL ARCHITECT for a generic review screenshot scraper.

You receive:
- discovery hypotheses
- adversarial critique
- deterministic selector measurements
- behavioral probes

Make the final decision using evidence.

HARD RULES:
1. The review selector MUST match at least 2 elements in selector_tests.
2. screenshot.method MUST be "element_screenshot". Never output "full_page".
3. identity_passed is true only when the identity probe shows usable unique IDs
   or another explicitly evidenced stable identity.
4. navigation_confirmed MUST come from probes.confirmed_navigation.confirmed.
5. If navigation is not confirmed, status MUST be "uncertain" or "failed".
   Never call an unverified navigation plan "probable".
6. If screenshot probing failed, status cannot be "verified".
7. Do not invent selectors or navigation controls.
8. The executor will follow this plan literally.

For a load-more site, the ideal evidence is:
before_count < after_count after clicking the candidate button.

Return ONLY JSON matching the requested structure."""


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {
            "type": "string",
            "enum": ["verified", "probable", "uncertain", "failed"]
        },
        "review": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selector": {"type": ["string", "null"]},
                "identity": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "strategy": {"type": "string"},
                        "attribute": {"type": ["string", "null"]}
                    },
                    "required": ["strategy", "attribute"]
                }
            },
            "required": ["selector", "identity"]
        },
        "navigation": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string"},
                "selector": {"type": ["string", "null"]},
                "verified": {"type": "boolean"}
            },
            "required": ["type", "selector", "verified"]
        },
        "screenshot": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["element_screenshot"]
                },
                "one_per_review": {"type": "boolean"}
            },
            "required": ["method", "one_per_review"]
        },
        "proof": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selector_match_count": {"type": "integer"},
                "navigation_confirmed": {"type": "boolean"},
                "screenshot_passed": {"type": "boolean"},
                "identity_passed": {"type": "boolean"},
                "notes": {"type": "array", "items": {"type": "string"}}
            },
            "required": [
                "selector_match_count",
                "navigation_confirmed",
                "screenshot_passed",
                "identity_passed",
                "notes"
            ]
        }
    },
    "required": ["status", "review", "navigation", "screenshot", "proof"]
}


def _evidence_gates(probes: dict[str, Any]) -> dict[str, Any]:
    selected = probes.get("selected_probe_selector")
    match_count = 0

    for item in probes.get("selector_tests", []):
        if item.get("selector") == selected:
            match_count = int(item.get("count", 0))
            break

    screenshot_passed = bool(
        probes.get("screenshot_test", {}).get("success")
    )

    identity = probes.get("identity_test", {})
    identity_passed = bool(identity.get("stable_id_candidates"))

    nav = probes.get("confirmed_navigation", {})
    navigation_confirmed = bool(nav.get("confirmed"))

    return {
        "selector": selected,
        "match_count": match_count,
        "screenshot_passed": screenshot_passed,
        "identity_passed": identity_passed,
        "navigation_confirmed": navigation_confirmed,
        "navigation": nav,
    }


def finalize(
    client: Groq,
    discovery: dict[str, Any],
    critique: dict[str, Any],
    probes: dict[str, Any],
) -> dict[str, Any]:

    gates = _evidence_gates(probes)

    evidence = {
        "discovery": discovery,
        "critique": critique,
        "probes": probes,
        "hard_gates": gates,
    }

    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        max_completion_tokens=1400,
        messages=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    evidence,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "final_plan",
                "strict": True,
                "schema": SCHEMA,
            },
        },
    )

    result = json.loads(resp.choices[0].message.content)

    # Deterministic post-validation. LLM #3 cannot override browser facts.
    result["screenshot"]["method"] = "element_screenshot"
    result["screenshot"]["one_per_review"] = True

    result["proof"]["selector_match_count"] = gates["match_count"]
    result["proof"]["navigation_confirmed"] = gates["navigation_confirmed"]
    result["proof"]["screenshot_passed"] = gates["screenshot_passed"]
    result["proof"]["identity_passed"] = gates["identity_passed"]

    if gates["selector"] and gates["match_count"] >= 2:
        result["review"]["selector"] = gates["selector"]

    if gates["navigation_confirmed"]:
        result["navigation"]["type"] = gates["navigation"]["type"]
        result["navigation"]["selector"] = gates["navigation"].get("selector")
        result["navigation"]["verified"] = True
    else:
        # Never let an LLM hallucinate verified navigation.
        result["navigation"] = {
            "type": "unknown",
            "selector": None,
            "verified": False,
        }

    # Hard safety gates for execution.
    if not gates["selector"] or gates["match_count"] < 2:
        result["status"] = "uncertain"

    if not gates["screenshot_passed"]:
        result["status"] = "uncertain"

    if not gates["identity_passed"]:
        result["status"] = "uncertain"

    if not gates["navigation_confirmed"]:
        result["status"] = "uncertain"

    return result
