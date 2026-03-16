"""
ZIPR Built-in Middleware

Usage:
    from zipr.middleware import logger, retry, rate_limit, require_auth

    bus.use(logger())
    bus.use(retry(max_attempts=3, delay=0.5))
    bus.use(rate_limit(per_second=10))
    bus.use(require_auth(allowed_srcs={"planner", "monitor"}))
"""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Awaitable

from .core import ZiprMessage

log = logging.getLogger("zipr.middleware")

Middleware = Callable[[ZiprMessage, Callable], Awaitable[None]]


def logger(level: int = logging.DEBUG) -> Middleware:
    """Log every message that passes through the bus."""
    async def _middleware(msg: ZiprMessage, next: Callable) -> None:
        log.log(level, "MSG  %s->%s [%s] body=%s", msg.src, msg.dst, msg.type, msg.body)
        t0 = time.perf_counter()
        await next()
        elapsed = (time.perf_counter() - t0) * 1000
        log.log(level, "DONE %s->%s in %.1fms", msg.src, msg.dst, elapsed)
    return _middleware


def retry(max_attempts: int = 3, delay: float = 0.25, backoff: float = 2.0) -> Middleware:
    """
    Retry failed handlers up to max_attempts times with exponential backoff.
    Only retries on Exception — does not retry on TimeoutError or CancelledError.
    """
    async def _middleware(msg: ZiprMessage, next: Callable) -> None:
        wait = delay
        for attempt in range(1, max_attempts + 1):
            try:
                await next()
                return
            except (asyncio.TimeoutError, asyncio.CancelledError):
                raise
            except Exception as exc:
                if attempt == max_attempts:
                    raise
                log.warning(
                    "Retry %d/%d for %s->%s [%s]: %s",
                    attempt, max_attempts, msg.src, msg.dst, msg.type, exc
                )
                await asyncio.sleep(wait)
                wait *= backoff
    return _middleware


def rate_limit(per_second: float, *, burst: int | None = None) -> Middleware:
    """
    Token-bucket rate limiter per destination agent.
    Drops messages that exceed the rate limit (logs a warning).

    per_second: max messages per second per dst agent
    burst:      max burst size (defaults to per_second * 2)
    """
    max_tokens = burst or max(1, int(per_second * 2))
    tokens: dict[str, float] = defaultdict(lambda: float(max_tokens))
    last:   dict[str, float] = defaultdict(time.monotonic)

    async def _middleware(msg: ZiprMessage, next: Callable) -> None:
        dst = msg.dst
        now = time.monotonic()
        elapsed = now - last[dst]
        last[dst] = now
        tokens[dst] = min(max_tokens, tokens[dst] + elapsed * per_second)

        if tokens[dst] < 1:
            log.warning("Rate limit exceeded for dst=%r — dropping message", dst)
            return  # drop the message

        tokens[dst] -= 1
        await next()

    return _middleware


def require_auth(allowed_srcs: set[str]) -> Middleware:
    """
    Block messages whose src is not in the allowed set.
    Useful for preventing unauthorized agents from calling privileged handlers.
    """
    async def _middleware(msg: ZiprMessage, next: Callable) -> None:
        if msg.src not in allowed_srcs and msg.src not in ("__bus__", "__client__"):
            log.warning("Auth denied: src=%r not in allowed set", msg.src)
            raise PermissionError(f"Agent '{msg.src}' is not authorized to send to '{msg.dst}'")
        await next()
    return _middleware


def filter_types(*allowed_types: str) -> Middleware:
    """Only pass messages whose type is in allowed_types."""
    async def _middleware(msg: ZiprMessage, next: Callable) -> None:
        if msg.type in allowed_types:
            await next()
        else:
            log.debug("Filtered message type=%r (allowed: %s)", msg.type, allowed_types)
    return _middleware


def ttl_check() -> Middleware:
    """Drop messages whose ttl= context tag has expired (value is seconds from ts=)."""
    async def _middleware(msg: ZiprMessage, next: Callable) -> None:
        ttl = msg.ctx.get("ttl")
        ts  = msg.ctx.get("ts")
        if ttl and ts:
            try:
                expires_at = float(ts) + float(ttl)
                if time.time() > expires_at:
                    log.warning("Dropping expired message from %s (ttl=%s)", msg.src, ttl)
                    return
            except (ValueError, TypeError):
                pass
        await next()
    return _middleware
