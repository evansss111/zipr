"""
VERITY — The Falsifier.
Tries to break every proposed law. Rigorous but fair.
If a law survives, approves it with a confidence score.
"""

import logging

from zipr import ZiprMessage
from ..think import think
from ..config import MODEL_VERITY

log = logging.getLogger("council.verity")

SYSTEM = """\
You are Verity, the skeptic of an eternal council of time-archaeologists.
Your purpose is to break proposed laws. Be rigorous and specific.

Check for:
1. Counterexamples — name a specific historical case where the law fails
2. Confounds — an alternative explanation that explains the same pattern
3. Scope errors — the law is too vague, too broad, or smuggles in assumptions
4. Prediction failures — the law's prediction has already been tested and failed

Be fair: if the law is sound and specific, approve it. Do not reject for vagueness if \
the law is actually precise. Do not demand perfection — demand rigor.

If you can suggest a specific, minimal revision that rescues the law, do so.

Output ONLY valid JSON (no prose, no markdown):
{
  "verdict": "approved" | "rejected" | "revised",
  "confidence": <float 0.0-1.0, your confidence in the law if approved/revised>,
  "counterexamples": ["<specific counterexample 1>", "<specific counterexample 2>"],
  "verdict_reason": "<2-3 sentences explaining your verdict>",
  "revised_law": "<revised law string if verdict=revised, else null>"
}"""


def register_verity(bus) -> None:
    @bus.agent("verity")
    async def verity(msg: ZiprMessage) -> ZiprMessage:
        law        = msg.body.get("law", "")
        mechanism  = msg.body.get("mechanism", "")
        prediction = msg.body.get("prediction", "")
        evidence   = msg.body.get("evidence", [])
        confidence = msg.body.get("confidence", 0.5)
        topic      = msg.body.get("topic", "")

        evidence_text = "\n".join(f"- {e}" for e in (evidence if isinstance(evidence, list) else []))

        prompt = (
            f"Topic: {topic}\n\n"
            f"Proposed law: {law}\n\n"
            f"Mechanism: {mechanism}\n\n"
            f"Prediction: {prediction}\n\n"
            f"Evidence:\n{evidence_text}\n\n"
            f"Loom's confidence: {confidence}\n\n"
            "Evaluate this law."
        )

        result = await think(SYSTEM, prompt, model=MODEL_VERITY, max_tokens=1200)
        verdict = result.get("verdict", "rejected")
        conf    = float(result.get("confidence", 0))

        log.info(
            "[verity] verdict=%r confidence=%.2f for law: %s",
            verdict, conf, law[:60]
        )

        return msg.reply(
            "verity",
            {
                "verdict":         verdict,
                "confidence":      conf,
                "counterexamples": result.get("counterexamples", []),
                "verdict_reason":  result.get("verdict_reason", ""),
                "revised_law":     result.get("revised_law"),
            },
        )
