"""
Persistent storage for the council.

canon.json   — the growing body of codified laws
sessions/    — one .jsonl per session, each line is a ZIPR wire string
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import CANON_FILE, SESSIONS_DIR

REJECTED_FILE = CANON_FILE.parent / "rejected.json"

log = logging.getLogger("council.storage")

# Live listener hooks — registered by the dashboard server
_wire_listeners:     list = []
_law_listeners:      list = []
_rejected_listeners: list = []

def add_wire_listener(fn)     -> None: _wire_listeners.append(fn)
def add_law_listener(fn)      -> None: _law_listeners.append(fn)
def add_rejected_listener(fn) -> None: _rejected_listeners.append(fn)


# ---------------------------------------------------------------------------
# Canon
# ---------------------------------------------------------------------------

def load_canon() -> list[dict]:
    if not CANON_FILE.exists():
        return []
    try:
        return json.loads(CANON_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load canon: %s", e)
        return []


def save_law(law: dict) -> None:
    """Append a new law to canon.json."""
    canon = load_canon()
    law_id = f"L{len(canon) + 1:04d}"
    law["id"] = law_id
    law["codified_at"] = datetime.now(timezone.utc).isoformat()
    canon.append(law)
    CANON_FILE.parent.mkdir(parents=True, exist_ok=True)
    CANON_FILE.write_text(
        json.dumps(canon, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Law %s codified: %s", law_id, law.get("law", "")[:80])
    for fn in _law_listeners:
        try:
            fn(dict(law))
        except Exception:
            pass


def law_count() -> int:
    return len(load_canon())


def load_rejected() -> list[dict]:
    if not REJECTED_FILE.exists():
        return []
    try:
        return json.loads(REJECTED_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_rejected(law: dict, reason: str, counterexamples: list, stage: str) -> None:
    """Record a law that was rejected by Verity or fell below the confidence threshold."""
    rejected = load_rejected()
    entry = {
        **law,
        "rejected_id":      f"R{len(rejected) + 1:04d}",
        "rejected_at":      datetime.now(timezone.utc).isoformat(),
        "verdict_reason":   reason,
        "counterexamples":  counterexamples,
        "stage":            stage,   # "verity" | "threshold" | "revision"
    }
    rejected.append(entry)
    REJECTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    REJECTED_FILE.write_text(json.dumps(rejected, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Law rejected (%s): %s", stage, law.get("law", "")[:80])
    for fn in _rejected_listeners:
        try:
            fn(dict(entry))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Session logs
# ---------------------------------------------------------------------------

def session_log_path(session_id: str) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"{session_id}.jsonl"


def log_wire(session_id: str, wire: str) -> None:
    """Append a ZIPR wire string to this session's log file."""
    path = session_log_path(session_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(wire + "\n")
    for fn in _wire_listeners:
        try:
            fn(wire)
        except Exception:
            pass


def save_session_summary(session_id: str, topic: str, n_laws: int, wires: list[str]) -> None:
    """Write a human-readable summary at the top of the session log."""
    path = session_log_path(session_id)
    summary = {
        "session": session_id,
        "topic": topic,
        "laws_codified": n_laws,
        "message_count": len(wires),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    # Prepend summary as first line (overwrite)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        json.dumps(summary) + "\n" + existing,
        encoding="utf-8",
    )
