from __future__ import annotations

import json
from typing import Any

from groq import Groq


# Keep the critic cheaper/faster, but use plain JSON mode rather than
# strict JSON Schema. The 20B model can be more brittle with complex
# nested/nullable structured-output schemas.
MODEL = "openai/gpt-oss-20b"


SYSTEM = """You are LLM #2, the ADVERSARIAL INVESTIGATOR for a review screenshot scraper.

Assume LLM #1 may be wrong. Try to break every important hypothesis using
the deterministic browser evidence.

Focus on:
- selector cardinality: one element vs a repeated collection
- whether the selector is the actual review boundary
- whether candidates are nested duplicates
- virtualized/recycled DOM
- stable identity across navigation
- whether navigation really can reveal more reviews
- whether a screenshot of the candidate would capture one review rather than a page/container
- simpler/more robust alternatives already present in the evidence

Do not invent selectors. You may recommend candidates already present in
the evidence that LLM #1 missed.

IMPORTANT OUTPUT RULE:
Return ONLY one valid JSON object.
Do not use markdown.
Do not use ``` fences.
Do not explain the answer outside JSON.

Use exactly this structure:

{
  "verdict": "promising|needs_probe|weak|failed",
  "review_rankings": [
    {
      "selector": "string",
      "decision": "accept|probe|reject",
      "reason": "string",
      "priority": 1
    }
  ],
  "navigation_rankings": [
    {
      "type": "load_more|pagination|internal_scroll|page_scroll|unknown",
      "selector": "string or null",
      "decision": "accept|probe|reject",
      "reason": "string"
    }
  ],
  "identity_strategy": "string or null",
  "risks": ["string"]
}

Return at most 6 review rankings, 6 navigation rankings and 8 risks.
priority must be an integer starting at 1.
If there is not enough evidence, use "probe" or "failed"; do not invent facts."""


def _clean_json_text(text: str) -> str:
    text = (text or "").strip()

    # Defensive cleanup for models that nevertheless emit fences.
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return text


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Make the critic output safe for the rest of the pipeline."""
    if not isinstance(data, dict):
        raise ValueError("Critic returned a JSON value that is not an object")

    verdicts = {"promising", "needs_probe", "weak", "failed"}
    decisions = {"accept", "probe", "reject"}

    verdict = data.get("verdict")
    if verdict not in verdicts:
        verdict = "needs_probe"

    reviews = []
    for item in data.get("review_rankings", []) or []:
        if not isinstance(item, dict):
            continue
        selector = item.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            continue
        decision = item.get("decision")
        if decision not in decisions:
            decision = "probe"
        try:
            priority = int(item.get("priority", len(reviews) + 1))
        except Exception:
            priority = len(reviews) + 1

        reviews.append({
            "selector": selector.strip(),
            "decision": decision,
            "reason": str(item.get("reason", ""))[:600],
            "priority": priority,
        })

    navigation = []
    valid_nav = {"load_more", "pagination", "internal_scroll", "page_scroll", "unknown"}
    for item in data.get("navigation_rankings", []) or []:
        if not isinstance(item, dict):
            continue
        typ = item.get("type")
        if typ not in valid_nav:
            continue
        selector = item.get("selector")
        if selector is not None and not isinstance(selector, str):
            selector = None
        decision = item.get("decision")
        if decision not in decisions:
            decision = "probe"
        navigation.append({
            "type": typ,
            "selector": selector,
            "decision": decision,
            "reason": str(item.get("reason", ""))[:600],
        })

    risks = [
        str(x)[:400]
        for x in (data.get("risks", []) or [])
        if isinstance(x, (str, int, float))
    ][:8]

    identity = data.get("identity_strategy")
    if identity is not None:
        identity = str(identity)[:300]

    return {
        "verdict": verdict,
        "review_rankings": reviews[:6],
        "navigation_rankings": navigation[:6],
        "identity_strategy": identity,
        "risks": risks,
    }


def criticize(client: Groq, observation: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
    # Keep this prompt deliberately compact. LLM #2 is supposed to attack
    # hypotheses, not reread the whole DOM.
    evidence = {
        "page": observation.get("page", {}),
        "review_candidates": observation.get("review_candidates", [])[:12],
        "action_candidates": observation.get("action_candidates", [])[:12],
        "scroll_candidates": observation.get("scroll_candidates", [])[:8],
        "repeated_groups": observation.get("repeated_groups", [])[:10],
        "discovery": discovery,
    }

    last_error = None

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                max_completion_tokens=1800,
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
                # Important: JSON mode instead of strict json_schema.
                # This avoids the 400 "Failed to validate JSON" issue seen
                # with the 20B critic.
                response_format={"type": "json_object"},
            )

            raw = resp.choices[0].message.content or ""
            raw = _clean_json_text(raw)

            if not raw:
                raise ValueError("Critic returned empty content")

            return _normalize(json.loads(raw))

        except Exception as exc:
            last_error = exc
            if attempt == 0:
                # Give the model one smaller retry instead of immediately
                # killing the whole 3-LLM pipeline.
                evidence = {
                    "page": observation.get("page", {}),
                    "review_candidates": observation.get("review_candidates", [])[:8],
                    "action_candidates": observation.get("action_candidates", [])[:8],
                    "repeated_groups": observation.get("repeated_groups", [])[:6],
                    "discovery": discovery,
                }

    raise RuntimeError(f"LLM #2 critic failed after retry: {last_error}")
