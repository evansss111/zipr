"""
CODEX — The Scribe.
Receives laws that survived Verity's scrutiny.
Refines their wording to be maximally precise and falsifiable, then writes them to the canon.
Broadcasts the codified law to all agents.
"""

import logging

from zipr import ZiprMessage, broadcast
from ..think import think
from ..storage import save_law, law_count
from ..config import MODEL_CODEX

log = logging.getLogger("council.codex")

SYSTEM = """\
You are Codex, the archivist of an eternal council of time-archaeologists.
You receive laws that have survived rigorous scrutiny. Your task:

1. Refine the wording to be maximally clear, specific, and falsifiable
2. Strip hedging language that makes the law vague or untestable
3. Assign meaningful tags (domain areas this law applies to)
4. Preserve the original insight — do not soften or change the core claim

The codified law will be permanent. Choose words carefully.

Output ONLY valid JSON (no prose, no markdown):
{
  "law": "<precisely worded law, 1-2 sentences>",
  "mechanism": "<the causal mechanism, 1-2 sentences>",
  "prediction": "<one specific, testable prediction>",
  "confidence": <float 0.0-1.0>,
  "tags": ["<tag1>", "<tag2>", "<tag3>"]
}"""


def register_codex(bus, session_id: str) -> None:
    @bus.agent("codex")
    async def codex(msg: ZiprMessage) -> None:
        raw_law    = msg.body.get("law", "")
        mechanism  = msg.body.get("mechanism", "")
        prediction = msg.body.get("prediction", "")
        evidence   = msg.body.get("evidence", [])
        confidence = float(msg.body.get("confidence", 0.5))
        topic      = msg.body.get("topic", "")
        analogues  = msg.body.get("analogues", [])
        signals    = msg.body.get("signals", [])

        evidence_text = "\n".join(f"- {e}" for e in (evidence if isinstance(evidence, list) else []))

        prompt = (
            f"Law to codify:\n{raw_law}\n\n"
            f"Mechanism: {mechanism}\n\n"
            f"Prediction: {prediction}\n\n"
            f"Evidence:\n{evidence_text}\n\n"
            f"Confidence: {confidence}\n\n"
            "Codify this law into its permanent form."
        )

        result = await think(SYSTEM, prompt, model=MODEL_CODEX, max_tokens=500)

        law_record = {
            "law":        result.get("law", raw_law),
            "mechanism":  result.get("mechanism", mechanism),
            "prediction": result.get("prediction", prediction),
            "confidence": float(result.get("confidence", confidence)),
            "tags":       result.get("tags", [topic]),
            "evidence":   evidence if isinstance(evidence, list) else [],
            "analogues":  [a.get("structural_echo", "") for a in (analogues if isinstance(analogues, list) else [])],
            "signals":    [s.get("signal", "") for s in (signals if isinstance(signals, list) else [])],
            "topic":      topic,
            "session":    session_id,
        }

        save_law(law_record)
        n = law_count()
        law_id = f"L{n:04d}"

        log.info("[codex] codified %s: %s", law_id, law_record["law"][:80])
        print(f"\n{'='*60}")
        print(f"[CODEX] {law_id} codified:")
        print(f"  LAW: {law_record['law']}")
        print(f"  MECHANISM: {law_record['mechanism']}")
        print(f"  PREDICTION: {law_record['prediction']}")
        print(f"  CONFIDENCE: {law_record['confidence']:.2f}")
        print(f"  TAGS: {law_record['tags']}")
        print(f"{'='*60}\n")

        # Broadcast to all agents
        await bus.publish(broadcast(
            "codex",
            {
                "event":      "law_codified",
                "id":         law_id,
                "law":        law_record["law"],
                "confidence": law_record["confidence"],
                "tags":       law_record["tags"],
                "session":    session_id,
            },
        ))
