"""
Async Claude API wrapper for council agents.
Each agent calls think() with its system prompt and a structured user prompt,
and gets back a parsed dict (JSON output enforced by prompt design).
"""

import asyncio
import json
import logging
import os
import re

import anthropic

log = logging.getLogger("council.think")

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic(api_key=key)
    return _client


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a string, even if surrounded by prose."""
    text = text.strip()

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    # Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Find the outermost { ... } block (handles prose before/after)
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, c in enumerate(text[start:], start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        pass
                    break

    return None


async def think(
    system: str,
    prompt: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 600,
) -> dict:
    """
    Call Claude and return parsed JSON dict.
    Uses a prefilled assistant turn ('{') to force JSON output.
    Runs in executor to avoid blocking the event loop.
    """
    loop = asyncio.get_running_loop()

    def _call() -> dict:
        client = _get_client()
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[
                {"role": "user",      "content": prompt},
                {"role": "assistant", "content": "{"},   # force JSON open
            ],
        )
        # The response continues from '{', so prepend it back
        text = "{" + resp.content[0].text.strip()
        log.debug("Raw response: %.200s", text)

        result = _extract_json(text)
        if result is None:
            log.warning("JSON parse failed, raw: %.300s", text)
            return {"raw": text}
        return result

    return await loop.run_in_executor(None, _call)
