"""
One council session — creates a fresh ZiprBus, registers all 5 agents,
kicks off Herald, and runs until the chain completes or times out.
"""

import asyncio
import logging
from datetime import datetime, timezone

from zipr import ZiprBus, task, encode
from .agents import (
    register_herald,
    register_kronos,
    register_loom,
    register_verity,
    register_codex,
)
from .storage import law_count, save_session_summary, log_wire
from .config import SESSION_TIMEOUT

log = logging.getLogger("council.session")


async def run_session(topic: str, session_id: str) -> dict:
    """
    Run one full council session on a given topic.
    Returns a summary dict: {session_id, topic, laws_before, laws_after, messages}.
    """
    laws_before = law_count()
    bus = ZiprBus(log_messages=False)

    # Register all five agents
    register_herald(bus, session_id)
    register_kronos(bus)
    register_loom(bus)
    register_verity(bus)
    register_codex(bus, session_id)

    # Intercept all messages for the session log
    original_publish = bus.publish

    async def logged_publish(msg):
        wire = encode(msg)
        log_wire(session_id, wire)
        log.debug("ZIPR %s", wire)
        await original_publish(msg)

    bus.publish = logged_publish

    log.info("[session %s] starting — topic: %s", session_id, topic)
    print(f"\n[council] session {session_id}")
    print(f"[council] topic: {topic}")
    print()

    # Fire off herald with the topic
    try:
        history = await bus.run(
            "herald",
            {"topic": topic, "session_id": session_id},
            timeout=SESSION_TIMEOUT,
            idle_for=0.5,
        )
    except Exception as e:
        log.exception("[session %s] crashed: %s", session_id, e)
        history = bus.history

    laws_after = law_count()
    n_new = laws_after - laws_before

    wires = [encode(m) for m in history]
    save_session_summary(session_id, topic, n_new, wires)

    log.info(
        "[session %s] complete — %d message(s), %d new law(s)",
        session_id, len(history), n_new
    )

    bus.print_metrics()

    return {
        "session_id": session_id,
        "topic":      topic,
        "laws_before": laws_before,
        "laws_after":  laws_after,
        "new_laws":    n_new,
        "messages":    len(history),
    }


def make_session_id() -> str:
    return datetime.now(timezone.utc).strftime("s%Y%m%d_%H%M%S")
