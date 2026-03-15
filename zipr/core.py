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
        return f"ZiprMessage({self.src}->{self.dst} [{self.type}] body={self.body} ctx={self.ctx})"

    def reply(self, src: str, body: dict[str, Any], msg_type: str = "r") -> "ZiprMessage":
        """Build a reply to this message, automatically setting re= context tag."""
        ctx: dict[str, str] = {}
        if "id" in self.ctx:
            ctx["re"] = self.ctx["id"]
        if "ctx" in self.ctx:
            ctx["ctx"] = self.ctx["ctx"]
        return ZiprMessage(src=src, dst=self.src, type=msg_type, body=body, ctx=ctx)

    def token_count(self) -> int:
        """Estimate token count of the encoded message (requires tiktoken)."""
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(encode(self)))
        except ImportError:
            # Rough heuristic: ~4 chars per token
            return len(encode(self)) // 4


# ---------------------------------------------------------------------------
# Value parser (handles nested, lists, primitives)
# ---------------------------------------------------------------------------

def _parse_value(raw: str) -> Any:
    raw = raw.strip()

    if raw == "~":
        return None
    if raw == "T":
        return True
    if raw == "F":
        return False

    # Quoted string — handle escape sequences
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        inner = raw[1:-1]
        return inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")

    # List: [a,b,c]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        if not inner.strip():
            return []
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
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    in_quote = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and in_quote and i + 1 < len(s):
            buf.append(c)
            buf.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
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
    result: dict[str, Any] = {}
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

class ZiprParseError(ValueError):
    pass


def parse(raw: str) -> ZiprMessage:
    """Parse a ZIPR message string into a ZiprMessage."""
    raw = raw.strip()

    m = re.match(r"^([^|]+?)(?:->|→)([^|]+)\|([a-z]+):(.*)$", raw, re.DOTALL)
    if not m:
        raise ZiprParseError(
            f"Invalid ZIPR message — expected 'src->dst|type:body[;tag=val...]'\n  Got: {raw!r}"
        )

    src = m.group(1).strip()
    dst = m.group(2).strip()
    msg_type = m.group(3).strip()
    rest = m.group(4)

    if not src:
        raise ZiprParseError("Missing src in ZIPR message")
    if not dst:
        raise ZiprParseError("Missing dst in ZIPR message")

    parts = _split_top(rest, ";")
    body_raw = parts[0] if parts else ""
    ctx_parts = parts[1:] if len(parts) > 1 else []

    body: dict[str, Any] = {}
    if body_raw and body_raw != "~":
        body = _parse_kv_block(body_raw)

    ctx: dict[str, str] = {}
    for tag in ctx_parts:
        if "=" in tag:
            k, _, v = tag.partition("=")
            ctx[k.strip()] = v.strip()

    return ZiprMessage(src=src, dst=dst, type=msg_type, body=body, ctx=ctx)


def try_parse(raw: str) -> tuple[ZiprMessage | None, str | None]:
    """Parse without raising. Returns (msg, None) on success or (None, error) on failure."""
    try:
        return parse(raw), None
    except ZiprParseError as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

_NEEDS_QUOTE = re.compile(r'[ ,=;|{}\[\]"\\]')


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
    if isinstance(v, float):
        # Trim unnecessary trailing zeros: 1.40 -> 1.4
        s = f"{v:.6g}"
        return s
    if isinstance(v, str):
        if _NEEDS_QUOTE.search(v) or v in ("T", "F", "~") or not v:
            escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            return f'"{escaped}"'
        return v
    return str(v)


def encode(msg: ZiprMessage) -> str:
    """Encode a ZiprMessage into a ZIPR wire string."""
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

_DEFAULT_PRI = 5


def _new_id() -> str:
    """4-char hex ID — short enough to save tokens, unique enough for a session."""
    return uuid.uuid4().hex[:4]


def _ts() -> str:
    return str(int(time.time()))


def _base_ctx(include_ts: bool = False, **extra: str) -> dict[str, str]:
    c: dict[str, str] = {"id": _new_id()}
    if include_ts:
        c["ts"] = _ts()
    c.update({k: str(v) for k, v in extra.items() if v is not None})
    return c


def query(src: str, dst: str, body: dict, *, ts: bool = False, **ctx) -> ZiprMessage:
    return ZiprMessage(src, dst, "q", body, _base_ctx(ts, **ctx))


def respond(src: str, dst: str, body: dict, *, re: str = "", ts: bool = False, **ctx) -> ZiprMessage:
    c = _base_ctx(ts, **ctx)
    if re:
        c["re"] = re
    return ZiprMessage(src, dst, "r", body, c)


def task(src: str, dst: str, body: dict, *, pri: int = _DEFAULT_PRI, ts: bool = False, **ctx) -> ZiprMessage:
    c = _base_ctx(ts, **ctx)
    if pri != _DEFAULT_PRI:
        c["pri"] = str(pri)
    return ZiprMessage(src, dst, "t", body, c)


def ack(src: str, dst: str, re: str, **body) -> ZiprMessage:
    return ZiprMessage(src, dst, "a", body, {"re": re})


def error(src: str, dst: str, code: int, msg: str, re: str = "") -> ZiprMessage:
    ctx: dict[str, str] = {}
    if re:
        ctx["re"] = re
    return ZiprMessage(src, dst, "e", {"code": code, "msg": msg}, ctx)


def broadcast(src: str, body: dict, *, ts: bool = False, **ctx) -> ZiprMessage:
    return ZiprMessage(src, "*", "b", body, _base_ctx(ts, **ctx))


def ping(src: str, dst: str) -> ZiprMessage:
    return ZiprMessage(src, dst, "p", {})


def state(src: str, dst: str, body: dict, *, ts: bool = True, **ctx) -> ZiprMessage:
    """State snapshots include ts by default since time matters for state."""
    return ZiprMessage(src, dst, "s", body, _base_ctx(ts, **ctx))


def capabilities(src: str, caps: list[str], **extra) -> ZiprMessage:
    return ZiprMessage(src, "*", "c", {"caps": caps, **extra}, {"id": _new_id()})


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------

TYPE_LABELS = {
    "q": "QUERY", "r": "RESP", "t": "TASK", "a": "ACK",
    "e": "ERR",   "s": "STATE", "b": "BCAST", "c": "CAPS",
    "p": "PING",  "x": "TERM",
}


def pprint(raw_or_msg, *, color: bool = False) -> str:
    if isinstance(raw_or_msg, str):
        msg = parse(raw_or_msg)
    else:
        msg = raw_or_msg

    label = TYPE_LABELS.get(msg.type, msg.type.upper())
    body_str = " ".join(f"{k}={_encode_value(v)}" for k, v in msg.body.items()) if msg.body else "(empty)"
    ctx_str = " " + " ".join(f"[{k}={v}]" for k, v in msg.ctx.items()) if msg.ctx else ""

    if color:
        CYAN, YELLOW, GREEN, RESET = "\033[96m", "\033[93m", "\033[92m", "\033[0m"
        return f"{CYAN}{msg.src}{RESET} -> {CYAN}{msg.dst}{RESET}  {YELLOW}{label}{RESET}  {GREEN}{body_str}{RESET}{ctx_str}"

    return f"{msg.src} -> {msg.dst}  {label}  {body_str}{ctx_str}"
