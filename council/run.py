"""
The Eternal Council — 24/7 run loop.

Cycles through seed topics, runs one council session per interval,
then auto-commits new laws and session logs to git.

Usage:
    python -m council.run                     # use default interval from config
    COUNCIL_INTERVAL=300 python -m council.run  # every 5 minutes

Environment:
    ANTHROPIC_API_KEY  — required
    COUNCIL_INTERVAL   — seconds between sessions (default: 3600)
    COUNCIL_ONCE       — if set to "1", run one session and exit
"""

import asyncio
import logging
import os
import subprocess
import sys
from itertools import cycle

# Make sure the project root is on the path when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from council.config import SEEDS, SESSION_INTERVAL
from council.session import run_session, make_session_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("council.run")


def _git_commit(session_id: str, n_laws: int, topic: str) -> None:
    """Commit new laws and session log to git, push to origin."""
    try:
        root = os.path.join(os.path.dirname(__file__), "..")
        msg = f"[council] {session_id}: {n_laws} law(s) — {topic[:60]}"

        subprocess.run(["git", "add", "council/laws/"], cwd=root, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=root, capture_output=True
        )
        if result.returncode == 0:
            log.info("Nothing new to commit for session %s", session_id)
            return

        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=root, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push"],
            cwd=root, check=False, capture_output=True  # don't crash if push fails
        )
        log.info("Committed and pushed: %s", msg)
        print(f"[git] committed: {msg}")
    except subprocess.CalledProcessError as e:
        log.warning("Git commit failed: %s", e)


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    once = os.environ.get("COUNCIL_ONCE") == "1"
    seed_cycle = cycle(SEEDS)

    print("=" * 60)
    print("  THE ETERNAL COUNCIL OF TIME-ARCHAEOLOGISTS")
    print("  Discovering the hidden laws of human progress")
    print(f"  Session interval: {SESSION_INTERVAL}s | Seeds: {len(SEEDS)}")
    print("=" * 60)
    print()

    session_num = 0
    while True:
        session_num += 1
        topic = next(seed_cycle)
        session_id = make_session_id()

        log.info("=== Session %d starting: %s ===", session_num, topic)

        summary = await run_session(topic, session_id)
        n_laws = summary["new_laws"]

        if n_laws > 0:
            _git_commit(session_id, n_laws, topic)
        else:
            log.info("No laws codified this session — skipping commit")

        if once:
            log.info("COUNCIL_ONCE=1 — exiting after one session")
            break

        log.info(
            "Session %d complete. Sleeping %ds before next session…",
            session_num, SESSION_INTERVAL
        )
        print(f"\n[council] sleeping {SESSION_INTERVAL}s before next session…\n")
        await asyncio.sleep(SESSION_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[council] stopped by user.")
