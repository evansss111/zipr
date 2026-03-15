"""
ZIPR Compression — zlib + base64 encoding for large or binary-transport messages.

A compressed ZIPR message is prefixed with "Z!" to distinguish it from plain wire format.

Usage:
    from zipr.compress import compress_str, decompress_str, ratio

    wire = encode(msg)
    small = compress_str(msg)        # "Z!eJy..." (base64 of zlib)
    back  = decompress_str(small)    # original ZiprMessage

    print(ratio(msg))                # e.g. 0.61 (39% smaller after compression)

When to use:
    - Large list or nested payloads (>200 chars)
    - Binary transport channels (WebSocket, TCP frames)
    - Storing message logs compactly
    - Compressing entire conversation histories
"""

import zlib
import base64
from typing import Union

from .core import ZiprMessage, encode, parse

_PREFIX = "Z!"
_ENCODING = "utf-8"


# ---------------------------------------------------------------------------
# Single message
# ---------------------------------------------------------------------------

def compress_bytes(msg: Union[ZiprMessage, str]) -> bytes:
    """Compress a ZiprMessage (or wire string) to raw zlib bytes."""
    wire = encode(msg) if isinstance(msg, ZiprMessage) else msg
    return zlib.compress(wire.encode(_ENCODING), level=9)


def decompress_bytes(data: bytes) -> ZiprMessage:
    """Decompress raw zlib bytes back to a ZiprMessage."""
    wire = zlib.decompress(data).decode(_ENCODING)
    return parse(wire)


def compress_str(msg: Union[ZiprMessage, str]) -> str:
    """Compress and base64-encode a message for text transport. Prefixed with 'Z!'."""
    raw = compress_bytes(msg)
    return _PREFIX + base64.b64encode(raw).decode("ascii")


def decompress_str(s: str) -> ZiprMessage:
    """Decompress a 'Z!'-prefixed base64 string back to a ZiprMessage."""
    if not s.startswith(_PREFIX):
        # Treat as plain wire format
        return parse(s)
    raw = base64.b64decode(s[len(_PREFIX):])
    return decompress_bytes(raw)


def is_compressed(s: str) -> bool:
    """Return True if the string is a ZIPR compressed message."""
    return s.startswith(_PREFIX)


def ratio(msg: Union[ZiprMessage, str]) -> float:
    """
    Compression ratio: compressed_size / original_size.
    A value < 1.0 means compression saved space (e.g. 0.6 = 40% smaller).
    """
    wire = encode(msg) if isinstance(msg, ZiprMessage) else msg
    original = len(wire.encode(_ENCODING))
    compressed = len(compress_bytes(wire))
    return compressed / original if original else 1.0


# ---------------------------------------------------------------------------
# Batch compression (conversation history)
# ---------------------------------------------------------------------------

def compress_batch(messages: list[ZiprMessage]) -> str:
    """
    Compress a list of messages together as a single blob.
    Messages are joined with newlines before compression (better ratio than individual).
    Returns a 'Z!'-prefixed base64 string.
    """
    joined = "\n".join(encode(m) for m in messages)
    compressed = zlib.compress(joined.encode(_ENCODING), level=9)
    return _PREFIX + base64.b64encode(compressed).decode("ascii")


def decompress_batch(s: str) -> list[ZiprMessage]:
    """Decompress a batch-compressed string back to a list of ZiprMessages."""
    if not s.startswith(_PREFIX):
        raise ValueError("Not a compressed ZIPR batch (missing 'Z!' prefix)")
    raw = base64.b64decode(s[len(_PREFIX):])
    joined = zlib.decompress(raw).decode(_ENCODING)
    return [parse(line) for line in joined.splitlines() if line.strip()]


def batch_ratio(messages: list[ZiprMessage]) -> float:
    """Compression ratio for a batch of messages."""
    joined = "\n".join(encode(m) for m in messages)
    original = len(joined.encode(_ENCODING))
    compressed = len(zlib.compress(joined.encode(_ENCODING), level=9))
    return compressed / original if original else 1.0


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def stats(msg: Union[ZiprMessage, str]) -> dict:
    """Return compression statistics for a message."""
    wire = encode(msg) if isinstance(msg, ZiprMessage) else msg
    original = len(wire.encode(_ENCODING))
    compressed_raw = len(compress_bytes(wire))
    compressed_b64 = len(compress_str(wire).encode("ascii"))
    return {
        "original_chars": len(wire),
        "original_bytes": original,
        "compressed_bytes": compressed_raw,
        "base64_chars": compressed_b64,
        "ratio": round(compressed_raw / original, 3) if original else 1.0,
        "savings_pct": round((1 - compressed_raw / original) * 100, 1) if original else 0.0,
    }
