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


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from a string, even if surrounded by prose."""
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try to find a JSON block
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Last resort: return raw text wrapped in a dict
    log.warning("Could not parse JSON from response, wrapping raw text")
    return {"raw": text.strip()}


async def think(
    system: str,
    prompt: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 600,
) -> dict:
    """Call Claude and return parsed JSON dict. Runs in executor to avoid blocking."""
    loop = asyncio.get_running_loop()

    def _call() -> dict:
        client = _get_client()
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        log.debug("Raw LLM response: %s", text[:200])
        return _extract_json(text)

    return await loop.run_in_executor(None, _call)
