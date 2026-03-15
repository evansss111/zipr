"""
ZIPR — Zero-overhead Interagent Protocol
Parser, encoder, and message builder for agent-to-agent communication.
"""

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ZiprMessage:
    src: str
    dst: str
    type: str
    body: dict[str, Any]
    ctx: dict[str, str] = field(default_factory=dict)

    def __repr__(self):
        return f"ZiprMessage({self.src}→{self.dst} [{self.type}] body={self.body} ctx={self.ctx})"


# ---------------------------------------------------------------------------
# Value parser (handles nested, lists, primitives)
# ---------------------------------------------------------------------------

def _parse_value(raw: str) -> Any:
    raw = raw.strip()

    # Null
    if raw == "~":
        return None

    # Boolean
    if raw == "T":
        return True
    if raw == "F":
        return False

    # Quoted string
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]

    # List: [a,b,c]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        return [_parse_value(v) for v in _split_top(inner, ",")]

    # Nested dict: {a=1,b=2}
    if raw.startswith("{") and raw.endswith("}"):
        inner = raw[1:-1]
        return _parse_kv_block(inner)

    # Reference #id
    if raw.startswith("#"):
        return {"$ref": raw[1:]}

    # Number
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass

    return raw  # plain string


def _split_top(s: str, sep: str) -> list[str]:
    """Split on sep but not inside brackets/braces/quotes."""
    parts, depth, buf, in_quote = [], 0, [], False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '"' and not in_quote:
            in_quote = True
            buf.append(c)
        elif c == '"' and in_quote:
            in_quote = False
            buf.append(c)
        elif in_quote:
            buf.append(c)
        elif c in "([{":
            depth += 1
            buf.append(c)
        elif c in ")]}":
            depth -= 1
            buf.append(c)
        elif c == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    if buf:
        parts.append("".join(buf))
    return [p for p in parts if p]


def _parse_kv_block(s: str) -> dict[str, Any]:
    """Parse a=v,b=w style block into a dict."""
    result = {}
    for pair in _split_top(s, ","):
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k.strip()] = _parse_value(v)
        else:
            result[pair.strip()] = True
    return result


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse(raw: str) -> ZiprMessage:
    """Parse a ZIPR message string into a ZiprMessage."""
    raw = raw.strip()

    # Split header from body+context: SRC→DST|TYPE:REST
    m = re.match(r"^([^|]+?)(?:->|→)([^|]+)\|([a-z]+):(.*)$", raw, re.DOTALL)
    if not m:
        raise ValueError(f"Invalid ZIPR message: {raw!r}")

    src, dst, msg_type, rest = m.group(1), m.group(2), m.group(3), m.group(4)

    # Split rest into body and context tags by splitting on ; at top level
    parts = _split_top(rest, ";")
    body_raw = parts[0] if parts else ""
    ctx_parts = parts[1:] if len(parts) > 1 else []

    # Parse body
    body: dict[str, Any] = {}
    if body_raw and body_raw != "~":
        body = _parse_kv_block(body_raw)

    # Parse context tags
    ctx: dict[str, str] = {}
    for tag in ctx_parts:
        if "=" in tag:
            k, _, v = tag.partition("=")
            ctx[k.strip()] = v.strip()

    return ZiprMessage(src=src, dst=dst, type=msg_type, body=body, ctx=ctx)


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

def _encode_value(v: Any) -> str:
    if v is None:
        return "~"
    if v is True:
        return "T"
    if v is False:
        return "F"
    if isinstance(v, list):
        return "[" + ",".join(_encode_value(i) for i in v) + "]"
    if isinstance(v, dict):
        if "$ref" in v:
            return "#" + v["$ref"]
        return "{" + ",".join(f"{k}={_encode_value(val)}" for k, val in v.items()) + "}"
    if isinstance(v, str) and (" " in v or "," in v or "=" in v):
        return f'"{v}"'
    return str(v)


def encode(msg: ZiprMessage) -> str:
    """Encode a ZiprMessage into a ZIPR string."""
    if msg.body:
        body = ",".join(f"{k}={_encode_value(v)}" for k, v in msg.body.items())
    else:
        body = "~"

    ctx_str = ""
    if msg.ctx:
        ctx_str = ";" + ";".join(f"{k}={v}" for k, v in msg.ctx.items())

    return f"{msg.src}->{msg.dst}|{msg.type}:{body}{ctx_str}"


# ---------------------------------------------------------------------------
# Message builder helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return uuid.uuid4().hex[:6]

def _ts() -> str:
    return str(int(time.time()))


def query(src: str, dst: str, body: dict, **ctx) -> ZiprMessage:
    return ZiprMessage(src, dst, "q", body, {"id": _new_id(), "ts": _ts(), **ctx})

def respond(src: str, dst: str, body: dict, re: str = "", **ctx) -> ZiprMessage:
    c = {"id": _new_id(), "ts": _ts(), **ctx}
    if re:
        c["re"] = re
    return ZiprMessage(src, dst, "r", body, c)

def task(src: str, dst: str, body: dict, pri: int = 5, **ctx) -> ZiprMessage:
    return ZiprMessage(src, dst, "t", body, {"id": _new_id(), "ts": _ts(), "pri": str(pri), **ctx})

def ack(src: str, dst: str, re: str, **body) -> ZiprMessage:
    return ZiprMessage(src, dst, "a", body, {"re": re, "ts": _ts()})

def error(src: str, dst: str, code: int, msg: str, re: str = "") -> ZiprMessage:
    ctx = {"ts": _ts()}
    if re:
        ctx["re"] = re
    return ZiprMessage(src, dst, "e", {"code": code, "msg": msg}, ctx)

def broadcast(src: str, body: dict, **ctx) -> ZiprMessage:
    return ZiprMessage(src, "*", "b", body, {"id": _new_id(), "ts": _ts(), **ctx})

def ping(src: str, dst: str) -> ZiprMessage:
    return ZiprMessage(src, dst, "p", {})

def capabilities(src: str, caps: list[str], **extra) -> ZiprMessage:
    return ZiprMessage(src, "*", "c", {"caps": caps, **extra}, {"id": _new_id()})


# ---------------------------------------------------------------------------
# Conversation pretty-printer
# ---------------------------------------------------------------------------

TYPE_LABELS = {
    "q": "QUERY", "r": "RESP", "t": "TASK", "a": "ACK",
    "e": "ERR",  "s": "STATE", "b": "BCAST", "c": "CAPS",
    "p": "PING", "x": "TERM",
}

def pprint(raw_or_msg) -> str:
    if isinstance(raw_or_msg, str):
        msg = parse(raw_or_msg)
    else:
        msg = raw_or_msg
    label = TYPE_LABELS.get(msg.type, msg.type.upper())
    ctx_str = " " + " ".join(f"[{k}={v}]" for k, v in msg.ctx.items()) if msg.ctx else ""
    body_str = " ".join(f"{k}={_encode_value(v)}" for k, v in msg.body.items()) if msg.body else "(empty)"
    return f"{msg.src} -> {msg.dst}  {label}  {body_str}{ctx_str}"


# ---------------------------------------------------------------------------
# CLI / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Parse and pretty-print a message from the command line
        raw = sys.argv[1]
        msg = parse(raw)
        print(pprint(msg))
        print()
        print("Re-encoded:", encode(msg))
    else:
        # Run a demo conversation
        print("=== ZIPR Demo Conversation ===\n")

        messages = [
            ping("ui", "planner"),
            ZiprMessage("planner", "ui", "p", {"status": "ready"}),
            task("ui", "planner", {"goal": "find all TODO comments in repo"}, ctx="session1"),
            task("planner", "scout", {"action": "grep", "pattern": "TODO", "scope": "/src"}, ctx="session1"),
            respond("scout", "planner", {"matches": 47, "files": ["main.py", "utils.py"]}, ctx="session1"),
            respond("planner", "ui", {"summary": "47 TODOs in 2 files", "files": ["main.py", "utils.py"]}, ctx="session1"),
        ]

        for msg in messages:
            raw = encode(msg)
            print("RAW :", raw)
            print("NICE:", pprint(raw))
            print()
