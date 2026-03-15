"""
ZIPR Message Bus — async in-process pub/sub + request/reply for agent networks.

Usage:
    bus = ZiprBus()

    @bus.agent("worker")
    async def worker(msg: ZiprMessage) -> ZiprMessage | None:
        result = do_work(msg.body)
        return msg.reply("worker", {"result": result})

    @bus.agent("planner")
    async def planner(msg: ZiprMessage) -> None:
        t = task("planner", "worker", {"action": "search"})
        resp = await bus.request(t, timeout=10.0)
        print(pprint(resp))

    asyncio.run(bus.run("planner", {"goal": "find logs"}))
"""

import asyncio
import logging
from collections.abc import Callable, Awaitable
from typing import Any

from .core import ZiprMessage, encode, pprint, _new_id

log = logging.getLogger("zipr.bus")

Handler = Callable[[ZiprMessage], Awaitable[ZiprMessage | None]]


class ZiprBus:
    """
    In-process async message bus for ZIPR agents.

    Agents register with @bus.agent(name). Messages are routed by dst field.
    Wildcard ("*") messages are delivered to all registered agents.

    Handlers run as background tasks so they can await bus.request() without
    deadlocking the dispatch loop.
    """

    def __init__(self, *, log_messages: bool = False) -> None:
        self._handlers: dict[str, Handler] = {}
        self._pending: dict[str, asyncio.Future[ZiprMessage]] = {}
        self._log = log_messages
        self._queue: asyncio.Queue[ZiprMessage] = asyncio.Queue()
        self._history: list[ZiprMessage] = []
        self._active: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def agent(self, name: str) -> Callable[[Handler], Handler]:
        """Decorator to register an async agent handler."""
        def decorator(fn: Handler) -> Handler:
            if name in self._handlers:
                raise ValueError(f"Agent '{name}' is already registered on this bus")
            self._handlers[name] = fn
            return fn
        return decorator

    def register(self, name: str, handler: Handler) -> None:
        """Register a handler programmatically."""
        self._handlers[name] = handler

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, msg: ZiprMessage) -> None:
        """Fire-and-forget: put a message on the bus."""
        self._history.append(msg)
        if self._log:
            log.info("BUS >> %s", encode(msg))
        await self._queue.put(msg)

    async def request(self, msg: ZiprMessage, *, timeout: float = 10.0) -> ZiprMessage:
        """
        Send a message and await a reply.
        The reply is matched by re= tag pointing to this message's id.
        """
        if "id" not in msg.ctx:
            msg.ctx["id"] = _new_id()

        msg_id = msg.ctx["id"]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ZiprMessage] = loop.create_future()
        self._pending[msg_id] = future

        await self.publish(msg)

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"No reply to message id={msg_id} within {timeout}s")
        finally:
            self._pending.pop(msg_id, None)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _run_handler(self, name: str, handler: Handler, msg: ZiprMessage) -> None:
        """Run a handler as a background task, publishing its return value if any."""
        try:
            result = await handler(msg)
            if result is not None:
                await self.publish(result)
        except Exception as exc:
            log.exception("Agent %r raised while handling %s", name, encode(msg))
            msg_id = msg.ctx.get("id")
            if msg_id:
                from .core import error as make_error
                await self.publish(make_error(name, msg.src, 500, str(exc)[:120], re=msg_id))

    async def _dispatch(self, msg: ZiprMessage) -> None:
        """Route a single message: resolve pending futures or spawn handler tasks."""
        # Reply resolution — don't spawn a handler, just resolve the future
        re_id = msg.ctx.get("re")
        if re_id and re_id in self._pending:
            fut = self._pending.pop(re_id)
            if not fut.done():
                fut.set_result(msg)
            return

        # Route to handler(s)
        if msg.dst == "*":
            targets = list(self._handlers.keys())
        elif msg.dst in self._handlers:
            targets = [msg.dst]
        else:
            if msg.src != "__bus__" or self._log:
                log.warning("ZIPR bus: no handler for dst=%r", msg.dst)
            return

        for name in targets:
            # Spawn as a task so handlers can await bus.request() without deadlock
            t = asyncio.create_task(self._run_handler(name, self._handlers[name], msg))
            self._active.add(t)
            t.add_done_callback(self._active.discard)

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Drain the queue continuously."""
        while True:
            msg = await self._queue.get()
            await self._dispatch(msg)
            self._queue.task_done()

    async def run(self, start_agent: str, body: dict[str, Any], *,
                  timeout: float = 30.0, idle_for: float = 0.2) -> list[ZiprMessage]:
        """
        Kick off the bus by sending an initial message to start_agent,
        then run until the queue and all handler tasks are idle.

        Returns the full message history.
        """
        start_msg = ZiprMessage(
            src="__bus__", dst=start_agent, type="t",
            body=body, ctx={"id": _new_id()}
        )

        loop_task = asyncio.create_task(self._loop())

        await self.publish(start_msg)

        # Wait until idle: queue empty and no active handler tasks, for idle_for seconds
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(idle_for)
            if self._queue.empty() and not self._active:
                # Stay idle for one more cycle to catch any last replies
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

        # Wait for any remaining handler tasks
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
