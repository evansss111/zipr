"""
KRONOS — The Chronicler.
Thinks in centuries. Finds structural echoes — the same pattern recurring across eras.
Does not search the web; relies on Claude's historical knowledge.
"""

import logging

from zipr import ZiprMessage
from ..think import think
from ..config import MODEL_KRONOS
from ..storage import load_canon

log = logging.getLogger("council.kronos")

SYSTEM = """\
You are Kronos, the deep memory of an eternal council of time-archaeologists.
You think in centuries. Your role is to find structural echoes — not vague similarities, \
but the same dynamic playing out in a different era with the same mechanism.

Given a modern research signal and its topic, identify 2 precise historical analogues where:
- The same acceleration pattern occurred
- The same kind of resistance or paradigm collapse followed
- The same underlying mechanism drove both events

Historical domains to draw from: printing press era, industrial revolution, germ theory, \
quantum mechanics revolution, computer science emergence, genomics, the internet transition.

Output ONLY valid JSON (no prose, no markdown):
{
  "analogues": [
    {
      "era": "<decade or century>",
      "domain": "<field>",
      "pattern": "<what happened, specifically>",
      "resolution": "<how it resolved — what collapsed, what won>",
      "structural_echo": "<one sentence: exactly why this mirrors the modern signal>"
    }
  ],
  "deep_pattern": "<2-3 sentences: the meta-level pattern these analogues share with the modern signal>"
}"""


def register_kronos(bus) -> None:
    @bus.agent("kronos")
    async def kronos(msg: ZiprMessage) -> ZiprMessage:
        topic   = msg.body.get("topic", "")
        signals = msg.body.get("signals", [])
        summary = msg.body.get("summary", "")

        signal_text = ""
        if signals:
            signal_text = "\n".join(
                f"- {s.get('title','')}: {s.get('signal','')}"
                for s in (signals if isinstance(signals, list) else [])
            )

        # Build proven analogues context from the last 10 codified laws
        canon = load_canon()
        proven_text = ""
        if canon:
            recent_laws = canon[-10:]
            proven = []
            for entry in recent_laws:
                for a in (entry.get("analogues") or []):
                    era    = a.get("era", "")
                    domain = a.get("domain", "")
                    pattern = a.get("pattern", "")
                    if era and domain and pattern:
                        proven.append(f"[{era} · {domain}] {pattern}")
            if proven:
                proven_text = (
                    "\n\nThese historical analogues have previously led to codified laws "
                    "— consider them as proven fruitful territory:\n"
                    + "\n".join(f"- {p}" for p in proven)
                )

        prompt = (
            f"Modern topic: {topic}\n\n"
            f"Current signals:\n{signal_text or summary}\n\n"
            f"Overall summary: {summary}\n\n"
            "Find historical structural echoes."
            f"{proven_text}"
        )

        result = await think(SYSTEM, prompt, model=MODEL_KRONOS, max_tokens=700)
        log.info("[kronos] found %d analogues for topic=%r", len(result.get("analogues", [])), topic)

        return msg.reply(
            "kronos",
            {
                "analogues":    result.get("analogues", []),
                "deep_pattern": result.get("deep_pattern", ""),
            },
        )
