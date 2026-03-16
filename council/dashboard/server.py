"""
The Eternal Council — Web Dashboard Server

Serves the live dashboard and runs council sessions in the background.

Usage:
    python council/dashboard/server.py
    # or
    uvicorn council.dashboard.server:app --reload

Open: http://localhost:8000
"""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from itertools import cycle
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from council.config import SEEDS, SESSION_INTERVAL, SESSIONS_DIR
from council.storage import load_canon, load_rejected, law_count, session_log_path, add_wire_listener, add_law_listener, add_rejected_listener
from council.session import run_session, make_session_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("council.dashboard")

# ---------------------------------------------------------------------------
# SSE broadcast infrastructure
# ---------------------------------------------------------------------------

_subscribers: set[asyncio.Queue] = set()
_server_start = time.time()
_session_count = 0
_current_session: dict | None = None


def _push(event: str, data: dict) -> None:
    """Push an SSE event to all connected clients."""
    line = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    dead = set()
    for q in _subscribers:
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            dead.add(q)
    _subscribers.difference_update(dead)


def _on_wire(wire: str) -> None:
    """Called by council.storage.log_wire for every ZIPR message."""
    _push("zipr", {"wire": wire, "ts": int(time.time())})


def _on_law(law: dict) -> None:
    _push("law", law)

def _on_rejected(law: dict) -> None:
    _push("rejected", law)

add_wire_listener(_on_wire)
add_law_listener(_on_law)
add_rejected_listener(_on_rejected)


# ---------------------------------------------------------------------------
# Council run loop (background task)
# ---------------------------------------------------------------------------

async def _council_loop() -> None:
    global _session_count, _current_session

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY not set — council is in read-only mode")
        return

    seed_cycle = cycle(SEEDS)
    while True:
        _session_count += 1
        topic = next(seed_cycle)
        session_id = make_session_id()
        _current_session = {"id": session_id, "topic": topic, "num": _session_count, "started": time.time()}

        _push("session", {"type": "start", "session_id": session_id, "topic": topic, "num": _session_count})
        log.info("Session %d starting: %s", _session_count, topic)

        try:
            summary = await run_session(topic, session_id)
            _push("session", {"type": "end", **summary})
        except Exception as e:
            log.exception("Session crashed: %s", e)
            _push("session", {"type": "error", "session_id": session_id, "error": str(e)})

        _current_session = None
        _push("stats", _stats())

        log.info("Sleeping %ds before next session…", SESSION_INTERVAL)
        await asyncio.sleep(SESSION_INTERVAL)


def _stats() -> dict:
    uptime = int(time.time() - _server_start)
    return {
        "law_count":      law_count(),
        "session_count":  _session_count,
        "uptime_seconds": uptime,
        "current_session": _current_session,
    }


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_council_loop())
    yield


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/api/laws")
async def get_laws():
    laws = load_canon()
    laws.reverse()   # newest first
    return JSONResponse(laws)


@app.get("/api/laws/{law_id}")
async def get_law(law_id: str):
    for law in load_canon():
        if law.get("id") == law_id:
            return JSONResponse(law)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/stats")
async def get_stats():
    return JSONResponse(_stats())


@app.get("/api/rejected")
async def get_rejected():
    laws = load_rejected()
    laws.reverse()
    return JSONResponse(laws)


@app.get("/api/sessions")
async def list_sessions():
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.jsonl"), reverse=True)[:50]:
        try:
            first = f.read_text(encoding="utf-8").splitlines()[0]
            meta = json.loads(first)
            sessions.append(meta)
        except Exception:
            sessions.append({"session": f.stem})
    return JSONResponse(sessions)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    path = session_log_path(session_id)
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    lines = path.read_text(encoding="utf-8").splitlines()
    # First line is summary JSON, rest are ZIPR wires
    summary = {}
    wires = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if i == 0:
            try:
                summary = json.loads(line)
            except Exception:
                wires.append(line)
        else:
            wires.append(line)
    return JSONResponse({"summary": summary, "wires": wires})


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

@app.get("/api/stream")
async def stream(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)

    async def generate():
        # Send current stats immediately on connect
        yield f"event: stats\ndata: {json.dumps(_stats())}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield chunk
                except asyncio.TimeoutError:
                    yield "event: heartbeat\ndata: {}\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  THE ETERNAL COUNCIL — Dashboard")
    print("  http://localhost:8000")
    print("=" * 60)
    uvicorn.run("council.dashboard.server:app", host="0.0.0.0", port=8000, reload=False, log_level="warning")
