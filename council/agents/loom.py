"""
LOOM — The Weaver.
Receives signals from Herald and history from Kronos.
Proposes a candidate fundamental law, then shepherds it through Verity's scrutiny.
"""

import logging

from zipr import ZiprMessage, query, task
from ..think import think
from ..config import MODEL_LOOM, APPROVAL_THRESHOLD
from ..storage import save_rejected, load_canon

log = logging.getLogger("council.loom")

WEAVE_SYSTEM = """\
You are Loom, the pattern synthesizer of an eternal council of time-archaeologists.
You receive signals from the present and structural echoes from history, and you weave \
them into a candidate fundamental law — a rule that seems to govern how waves of human \
research and technological progress unfold.

Requirements for a valid law:
1. Specific — not "things change" but a precise mechanism with timing or conditions
2. Falsifiable — it makes a prediction that could be tested or refuted
3. Supported by at least 2 distinct historical instances from different eras
4. Reveals a hidden mechanism, not just an obvious trend

Output ONLY valid JSON (no prose, no markdown):
{
  "law": "<the law, stated precisely in 1-2 sentences>",
  "mechanism": "<the underlying causal mechanism in 1-2 sentences>",
  "prediction": "<a specific, falsifiable prediction this law makes about current or near-future events>",
  "evidence": ["<historical instance 1>", "<historical instance 2>", "<modern signal>"],
  "confidence": <float 0.0-1.0>
}"""

REVISE_SYSTEM = """\
You are Loom, the pattern synthesizer. Verity has challenged your proposed law.
Revise it to address the counterexamples and objections raised, while preserving the core insight.
If the challenge is fatal and no revision is possible, output confidence 0.0.

Output ONLY valid JSON (no prose, no markdown):
{
  "law": "<revised law>",
  "mechanism": "<revised mechanism>",
  "prediction": "<revised prediction>",
  "evidence": ["<evidence 1>", "<evidence 2>"],
  "confidence": <float 0.0-1.0>,
  "revision_note": "<what was changed and why>"
}"""


def register_loom(bus) -> None:
    @bus.agent("loom")
    async def loom(msg: ZiprMessage) -> None:
        topic    = msg.body.get("topic", "")
        signals  = msg.body.get("signals", [])
        summary  = msg.body.get("summary", "")
        session  = msg.body.get("session", "")

        log.info("[loom] weaving thread for topic=%r", topic)

        # 1. Ask Kronos for historical analogues
        kronos_q = query(
            "loom", "kronos",
            {"topic": topic, "signals": signals, "summary": summary},
        )
        try:
            kronos_resp = await bus.request(kronos_q, timeout=60.0)
        except TimeoutError:
            log.warning("[loom] kronos timed out, proceeding without history")
            kronos_resp = None

        analogues    = kronos_resp.body.get("analogues", [])    if kronos_resp else []
        deep_pattern = kronos_resp.body.get("deep_pattern", "") if kronos_resp else ""

        # 2. Weave a candidate law
        signal_text = "\n".join(
            f"- {s.get('title','')}: {s.get('signal','')}"
            for s in (signals if isinstance(signals, list) else [])
        ) if signals else summary

        analogue_text = "\n".join(
            f"- [{a.get('era','')}] {a.get('domain','')}: {a.get('pattern','')} → {a.get('structural_echo','')}"
            for a in (analogues if isinstance(analogues, list) else [])
        ) if analogues else ""

        # Build canon context — do not duplicate existing laws
        canon = load_canon()
        canon_context = ""
        if canon:
            recent = canon[-20:]
            canon_lines = "\n".join(
                f"{i+1}. {entry.get('law', '')}"
                for i, entry in enumerate(recent)
            )
            canon_context = (
                "Laws already codified — do not duplicate these, but you may build upon or extend them:\n"
                f"{canon_lines}\n\n"
            )

        weave_prompt = (
            f"Topic: {topic}\n\n"
            f"Modern signals:\n{signal_text}\n\n"
            f"Historical analogues:\n{analogue_text or 'none found'}\n\n"
            f"Deep pattern from Kronos: {deep_pattern}\n\n"
            f"{canon_context}"
            "Propose a fundamental law."
        )

        law_data = await think(WEAVE_SYSTEM, weave_prompt, model=MODEL_LOOM, max_tokens=600)
        log.info("[loom] proposed law (confidence=%.2f): %s", law_data.get("confidence", 0), law_data.get("law", "")[:80])

        # 3. Send to Verity for scrutiny
        verity_t = task(
            "loom", "verity",
            {
                "law":         law_data.get("law", ""),
                "mechanism":   law_data.get("mechanism", ""),
                "prediction":  law_data.get("prediction", ""),
                "evidence":    law_data.get("evidence", []),
                "confidence":  law_data.get("confidence", 0.5),
                "topic":       topic,
                "session":     session,
            },
        )
        try:
            verdict_msg = await bus.request(verity_t, timeout=90.0)
        except TimeoutError:
            log.warning("[loom] verity timed out — skipping law")
            return

        verdict    = verdict_msg.body.get("verdict", "rejected")
        confidence = float(verdict_msg.body.get("confidence", 0))
        revised    = verdict_msg.body.get("revised_law")

        log.info("[loom] verity verdict=%r confidence=%.2f", verdict, confidence)

        # 4. Handle revision
        if verdict == "revised" and revised:
            revise_prompt = (
                f"Original law: {law_data.get('law','')}\n"
                f"Verity's counterexamples: {verdict_msg.body.get('counterexamples', [])}\n"
                f"Verity's reason: {verdict_msg.body.get('verdict_reason','')}\n"
                f"Verity's suggested revision: {revised}\n\n"
                "Revise the law."
            )
            law_data = await think(REVISE_SYSTEM, revise_prompt, model=MODEL_LOOM, max_tokens=500)
            confidence = float(law_data.get("confidence", 0))
            log.info("[loom] revised law (confidence=%.2f): %s", confidence, law_data.get("law", "")[:80])

        # 5. If confidence meets threshold, send to Codex; otherwise record rejection
        law_record = {
            "law":        law_data.get("law", ""),
            "mechanism":  law_data.get("mechanism", ""),
            "prediction": law_data.get("prediction", ""),
            "evidence":   law_data.get("evidence", []),
            "confidence": confidence,
            "topic":      topic,
            "session":    session,
            "analogues":  analogues,
            "signals":    signals,
        }

        if confidence >= APPROVAL_THRESHOLD:
            await bus.publish(task("loom", "codex", law_record))
        else:
            stage = "verity" if verdict == "rejected" else "threshold"
            save_rejected(
                law_record,
                reason=verdict_msg.body.get("verdict_reason", ""),
                counterexamples=verdict_msg.body.get("counterexamples", []),
                stage=stage,
            )
            log.info("[loom] law rejected (%s, confidence=%.2f)", stage, confidence)
