"""
ZIPR Network Transport — TCP-based cross-process/cross-machine agent routing.

Architecture:
    ZiprServer  — central broker, routes messages between connected clients
    ZiprClient  — wraps a ZiprBus, connects it to a server

Wire protocol (line-oriented, UTF-8):
    Client → Server on connect:  HELLO agent1,agent2,...\\n
    Server → Client (ack):       OK\\n
    Either direction (message):  <ZIPR wire>\\n
    Either direction (error):    ERR <reason>\\n
    Either direction (close):    BYE\\n

Usage — Server process:
    server = ZiprServer()

    @server.agent("planner")
    async def planner(msg):
        resp = await server.bus.request(task("planner", "worker", {...}))
        ...

    asyncio.run(server.serve())

Usage — Client process:
    bus = ZiprBus()

    @bus.agent("worker")
    async def worker(msg):
        return msg.reply("worker", {"result": "done"})

    client = ZiprClient(bus)
    asyncio.run(client.run())
"""

import asyncio
import logging
from dataclasses import dataclass, field

from .core import ZiprMessage, parse, encode, ZiprParseError, _new_id
from .bus import ZiprBus

log = logging.getLogger("zipr.net")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7779

_HELLO   = "HELLO"
_OK      = "OK"
_BYE     = "BYE"
_ERR_PFX = "ERR"


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

@dataclass
class _ClientConn:
    """One connected client with its registered agent names."""
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    agents: set[str] = field(default_factory=set)
    id: str = field(default_factory=_new_id)

    async def send(self, line: str) -> None:
        try:
            self.writer.write((line + "\n").encode())
            await self.writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

    @property
    def peer(self) -> str:
        try:
            return "{}:{}".format(*self.writer.get_extra_info("peername"))
        except Exception:
            return self.id


class ZiprServer:
    """
    Central ZIPR broker that routes messages between connected ZiprClient instances.

    The server itself can also host local agents via server.bus.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        log_messages: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.bus = ZiprBus(log_messages=log_messages)
        self._clients: list[_ClientConn] = []
        self._log = log_messages
        self._server: asyncio.Server | None = None

        # Hook local bus: forward unroutable messages to network
        self.bus.on_unroutable(self._forward_or_drop)

    # ------------------------------------------------------------------
    # Local agent registration (shortcut for agents hosted on the server)
    # ------------------------------------------------------------------

    def agent(self, name: str):
        return self.bus.agent(name)

    # ------------------------------------------------------------------
    # Connection handler
    # ------------------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = _ClientConn(reader=reader, writer=writer)
        log.info("New connection from %s [id=%s]", conn.peer, conn.id)

        try:
            # Handshake
            line = (await reader.readline()).decode().strip()
            if not line.startswith(_HELLO):
                await conn.send(f"{_ERR_PFX} expected HELLO")
                writer.close()
                return

            agents = [a.strip() for a in line[len(_HELLO):].strip().split(",") if a.strip()]
            conn.agents = set(agents)
            self._clients.append(conn)
            await conn.send(_OK)

            log.info("Client %s registered agents: %s", conn.peer, agents)

            # Message loop
            async for raw_line in reader:
                line = raw_line.decode().strip()
                if not line or line == _BYE:
                    break
                await self._route_from_client(conn, line)

        except asyncio.IncompleteReadError:
            pass
        except Exception:
            log.exception("Error handling client %s", conn.peer)
        finally:
            self._clients.remove(conn)
            writer.close()
            log.info("Client %s disconnected (agents: %s)", conn.peer, conn.agents)

    async def _route_from_client(self, sender: _ClientConn, wire: str) -> None:
        """Route a message received from a client."""
        try:
            msg = parse(wire)
        except ZiprParseError as e:
            await sender.send(f"{_ERR_PFX} parse:{e}")
            return

        if self._log:
            log.info("ROUTE %s -> %s [%s]", msg.src, msg.dst, msg.type)

        # Broadcast: send to all other clients + local bus
        if msg.dst == "*":
            for c in self._clients:
                if c is not sender:
                    await c.send(wire)
            await self.bus.publish(msg)
            return

        # Check local bus agents first
        if msg.dst in self.bus.agents():
            await self.bus.publish(msg)
            return

        # Route to remote client
        target = self._find_client(msg.dst)
        if target:
            await target.send(wire)
        else:
            await sender.send(f"{_ERR_PFX} unknown dst={msg.dst}")
            log.warning("No route for dst=%r", msg.dst)

    def _find_client(self, agent: str) -> _ClientConn | None:
        for c in self._clients:
            if agent in c.agents:
                return c
        return None

    async def _forward_or_drop(self, msg: ZiprMessage) -> None:
        """Called by local bus when a message has no local handler — forward to network."""
        target = self._find_client(msg.dst)
        if target:
            await target.send(encode(msg))
        else:
            log.warning("Server: no route for dst=%r (no local handler, no client)", msg.dst)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def serve(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Start the server and run until stop_event is set (or forever)."""
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        addr = self._server.sockets[0].getsockname()
        log.info("ZIPR server listening on %s:%s", *addr)
        print(f"[ZIPR server] listening on {addr[0]}:{addr[1]}")

        # Run the local bus dispatch loop alongside the TCP server
        bus_task = asyncio.create_task(self.bus._loop_forever())
        try:
            async with self._server:
                if stop_event:
                    await stop_event.wait()
                else:
                    await asyncio.Event().wait()  # run forever
        finally:
            bus_task.cancel()
            try:
                await bus_task
            except asyncio.CancelledError:
                pass

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def connected_agents(self) -> list[str]:
        return [a for c in self._clients for a in c.agents]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ZiprClient:
    """
    Connects a ZiprBus to a ZiprServer for cross-process/cross-machine messaging.

    Local agents are registered on the bus as normal.
    Messages to remote agents are forwarded to the server automatically.
    Messages from the server are delivered to local agents.
    """

    def __init__(
        self,
        bus: ZiprBus,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        reconnect: bool = True,
        reconnect_delay: float = 2.0,
    ) -> None:
        self.bus = bus
        self.host = host
        self.port = port
        self.reconnect = reconnect
        self.reconnect_delay = reconnect_delay
        self._writer: asyncio.StreamWriter | None = None
        self._connected = asyncio.Event()

        # Hook the bus: unroutable messages go to server
        self.bus.on_unroutable(self._forward_to_server)

    async def _forward_to_server(self, msg: ZiprMessage) -> None:
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write((encode(msg) + "\n").encode())
                await self._writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                log.warning("Lost connection while forwarding message")
        else:
            log.warning("Client: not connected, dropping message to dst=%r", msg.dst)

    async def _receive_loop(self, reader: asyncio.StreamReader) -> None:
        """Receive messages from the server and deliver them to the local bus."""
        async for raw in reader:
            line = raw.decode().strip()
            if not line or line == _BYE:
                break
            if line.startswith(_ERR_PFX):
                log.warning("Server error: %s", line)
                continue
            try:
                msg = parse(line)
                await self.bus.publish(msg)
            except ZiprParseError:
                log.warning("Received unparseable line: %r", line)

    async def connect(self) -> None:
        """Connect to the server, register local agents, start routing."""
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                self._writer = writer
                agent_names = ",".join(self.bus.agents())
                writer.write(f"{_HELLO} {agent_names}\n".encode())
                await writer.drain()

                ack = (await reader.readline()).decode().strip()
                if ack != _OK:
                    raise ConnectionError(f"Server rejected handshake: {ack}")

                self._connected.set()
                log.info("Connected to ZIPR server %s:%s (agents: %s)", self.host, self.port, agent_names)
                print(f"[ZIPR client] connected to {self.host}:{self.port}, agents: [{agent_names}]")

                await self._receive_loop(reader)

            except (ConnectionRefusedError, OSError) as e:
                self._connected.clear()
                if not self.reconnect:
                    raise
                log.warning("Connection failed (%s), retrying in %.1fs…", e, self.reconnect_delay)
                print(f"[ZIPR client] connection failed, retrying in {self.reconnect_delay}s…")
                await asyncio.sleep(self.reconnect_delay)
                continue

            self._connected.clear()
            log.info("Disconnected from server")
            if not self.reconnect:
                break
            await asyncio.sleep(self.reconnect_delay)

    async def wait_connected(self, timeout: float = 10.0) -> None:
        """Wait until successfully connected to the server."""
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    async def run(self, *, start_agent: str | None = None, body: dict | None = None) -> None:
        """
        Connect and run the bus. Optionally kick off a start_agent.
        Runs until the connection is closed.
        """
        bus_task = asyncio.create_task(self.bus._loop_forever())
        connect_task = asyncio.create_task(self.connect())

        if start_agent:
            await self.wait_connected()
            await self.bus.publish(
                ZiprMessage("__client__", start_agent, "t", body or {}, {"id": _new_id()})
            )

        await asyncio.gather(bus_task, connect_task, return_exceptions=True)
