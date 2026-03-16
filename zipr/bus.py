"""
ZIPR Message Bus — async in-process pub/sub + request/reply for agent networks.

Usage:
    bus = ZiprBus()

    @bus.agent("worker")
    async def worker(msg: ZiprMessage) -> ZiprMessage | None:
        return msg.reply("worker", {"result": "done"})

    @bus.agent("planner")
    async def planner(msg: ZiprMessage) -> None:
        resp = await bus.request(task("planner", "worker", {"action": "search"}))
        print(pprint(resp))

    asyncio.run(bus.run("planner", {"goal": "find logs"}))
"""

import asyncio
import logging
import time
from collections.abc import Callable, Awaitable
from dataclasses import dataclass, field
from typing import Any

from .core import ZiprMessage, encode, pprint, _new_id

log = logging.getLogger("zipr.bus")

Handler    = Callable[[ZiprMessage], Awaitable[ZiprMessage | None]]
Middleware = Callable[[ZiprMessage, Callable], Awaitable[None]]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass
class AgentMetrics:
    received:  int   = 0
    sent:      int   = 0
    errors:    int   = 0
    total_ms:  float = 0.0  # cumulative handler latency

    @property
    def avg_latency_ms(self) -> float:
        return self.total_ms / self.received if self.received else 0.0

    def as_dict(self) -> dict:
        return {
            "received":       self.received,
            "sent":           self.sent,
            "errors":         self.errors,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
        }


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------

class ZiprBus:
    """
    In-process async message bus for ZIPR agents.

    Features:
    - @bus.agent(name) registers an async handler
    - bus.request(msg) sends and awaits a reply
    - bus.use(middleware) adds middleware to the dispatch chain
    - bus.metrics() returns per-agent stats
    - bus.on_unroutable(fn) hooks undeliverable messages (used by ZiprClient)
    """

    def __init__(self, *, log_messages: bool = False) -> None:
        self._handlers:    dict[str, Handler]      = {}
        self._pending:     dict[str, asyncio.Future[ZiprMessage]] = {}
        self._middleware:  list[Middleware]         = []
        self._metrics:     dict[str, AgentMetrics] = {}
        self._active:      set[asyncio.Task]        = set()
        self._queue:       asyncio.Queue[ZiprMessage] = asyncio.Queue()
        self._history:     list[ZiprMessage]        = []
        self._log          = log_messages
        self._on_unroutable: Callable | None        = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def agent(self, name: str) -> Callable[[Handler], Handler]:
        """Decorator to register an async agent handler."""
        def decorator(fn: Handler) -> Handler:
            if name in self._handlers:
                raise ValueError(f"Agent '{name}' is already registered")
            self._handlers[name] = fn
            self._metrics[name] = AgentMetrics()
            return fn
        return decorator

    def register(self, name: str, handler: Handler) -> None:
        """Register a handler programmatically."""
        self._handlers[name] = handler
        self._metrics.setdefault(name, AgentMetrics())

    def use(self, middleware: Middleware) -> None:
        """Add middleware to the dispatch chain (runs before every handler)."""
        self._middleware.append(middleware)

    def on_unroutable(self, fn: Callable) -> None:
        """
        Set a callback for messages whose dst has no local handler.
        Used by ZiprClient to forward messages to the network.
        """
        self._on_unroutable = fn

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, msg: ZiprMessage) -> None:
        """Fire-and-forget: enqueue a message for delivery."""
        self._history.append(msg)
        if self._log:
            log.info("BUS >> %s", encode(msg))
        await self._queue.put(msg)

    async def request(self, msg: ZiprMessage, *, timeout: float = 10.0) -> ZiprMessage:
        """Send and await a reply matched by re= pointing to msg's id."""
        if "id" not in msg.ctx:
            msg.ctx["id"] = _new_id()

        msg_id = msg.ctx["id"]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ZiprMessage] = loop.create_future()
        self._pending[msg_id] = future

        # Track outgoing on sender agent
        if msg.src in self._metrics:
            self._metrics[msg.src].sent += 1

        await self.publish(msg)

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No reply to id={msg_id} within {timeout}s")
        finally:
            self._pending.pop(msg_id, None)

    # ------------------------------------------------------------------
    # Middleware chain
    # ------------------------------------------------------------------

    async def _apply_middleware(self, msg: ZiprMessage, final: Callable) -> None:
        """Wrap `final` in the middleware chain and invoke it."""
        chain = list(self._middleware)

        async def build(idx: int) -> None:
            if idx < len(chain):
                await chain[idx](msg, lambda m=msg: build(idx + 1))
            else:
                await final(msg)

        await build(0)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _run_handler(self, name: str, handler: Handler, msg: ZiprMessage) -> None:
        m = self._metrics.get(name)
        if m:
            m.received += 1

        t0 = time.perf_counter()
        try:
            async def invoke(msg: ZiprMessage) -> None:
                result = await handler(msg)
                if result is not None:
                    if m:
                        m.sent += 1
                    await self.publish(result)

            await self._apply_middleware(msg, invoke)

        except Exception as exc:
            if m:
                m.errors += 1
            log.exception("Agent %r raised on %s", name, encode(msg))
            msg_id = msg.ctx.get("id")
            if msg_id:
                from .core import error as make_error
                await self.publish(make_error(name, msg.src, 500, str(exc)[:120], re=msg_id))
        finally:
            if m:
                m.total_ms += (time.perf_counter() - t0) * 1000

    async def _dispatch(self, msg: ZiprMessage) -> None:
        # Resolve pending request futures
        re_id = msg.ctx.get("re")
        if re_id and re_id in self._pending:
            fut = self._pending.pop(re_id)
            if not fut.done():
                fut.set_result(msg)
            return

        # Route
        if msg.dst == "*":
            targets = list(self._handlers.keys())
        elif msg.dst in self._handlers:
            targets = [msg.dst]
        else:
            if self._on_unroutable:
                await self._on_unroutable(msg)
            elif msg.src != "__bus__" and msg.src != "__client__":
                log.warning("No handler for dst=%r", msg.dst)
            return

        for name in targets:
            t = asyncio.create_task(
                self._run_handler(name, self._handlers[name], msg)
            )
            self._active.add(t)
            t.add_done_callback(self._active.discard)

    # ------------------------------------------------------------------
    # Run loops
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Run one iteration of the dispatch loop until the queue drains."""
        while True:
            msg = await self._queue.get()
            await self._dispatch(msg)
            self._queue.task_done()

    async def _loop_forever(self) -> None:
        """Run the dispatch loop indefinitely (used by ZiprClient)."""
        await self._loop()

    async def run(
        self,
        start_agent: str,
        body: dict[str, Any],
        *,
        timeout: float = 30.0,
        idle_for: float = 0.2,
    ) -> list[ZiprMessage]:
        """
        Start the bus with an initial message to start_agent and run until idle.
        Returns the full message history.
        """
        start_msg = ZiprMessage(
            src="__bus__", dst=start_agent, type="t",
            body=body, ctx={"id": _new_id()}
        )

        loop_task = asyncio.create_task(self._loop())
        await self.publish(start_msg)

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(idle_for)
            if self._queue.empty() and not self._active:
                await asyncio.sleep(idle_for)
                if self._queue.empty() and not self._active:
                    break
        else:
            log.warning("ZiprBus.run() timed out after %.1fs", timeout)

        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        if self._active:
            await asyncio.gather(*list(self._active), return_exceptions=True)

        return list(self._history)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def history(self) -> list[ZiprMessage]:
        return list(self._history)

    def print_history(self, *, color: bool = False) -> None:
        for msg in self._history:
            print(pprint(msg, color=color))

    def agents(self) -> list[str]:
        return list(self._handlers.keys())

    def metrics(self) -> dict[str, dict]:
        return {name: m.as_dict() for name, m in self._metrics.items()}

    def print_metrics(self) -> None:
        print(f"{'Agent':<20} {'Rcvd':>6} {'Sent':>6} {'Err':>5} {'Avg ms':>8}")
        print("-" * 48)
        for name, m in self._metrics.items():
            print(f"{name:<20} {m.received:>6} {m.sent:>6} {m.errors:>5} {m.avg_latency_ms:>8.1f}")
