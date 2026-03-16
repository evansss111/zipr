"""
ZIPR Network Demo — CLIENT process

Runs a ZIPR client with a local "worker" agent that executes tasks
sent by the remote "planner" agent on the server.

Run in two terminals:
    Terminal 1:  python demo/server.py
    Terminal 2:  python demo/client.py "What are the benefits of async IO?"

The worker uses the Claude API if ANTHROPIC_API_KEY is set,
otherwise returns a simulated response.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zipr import ZiprBus, ZiprMessage, encode
from zipr.net import ZiprClient
from zipr.middleware import logger, retry
import logging

logging.basicConfig(level=logging.WARNING)

bus = ZiprBus()


def _llm_research(topic: str, aspect: str) -> str:
    """Research a topic using Claude if available, else return a stub."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return f"[stub] {aspect} of '{topic}': (set ANTHROPIC_API_KEY for real answers)"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"Give a brief {aspect} of: {topic}. Be concise — 2-3 sentences max."
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"[error] {e}"


@bus.agent("worker")
async def worker(msg: ZiprMessage) -> ZiprMessage:
    action = msg.body.get("action", "unknown")
    topic  = msg.body.get("topic", "")
    aspect = msg.body.get("aspect", "overview")

    wire = encode(msg)
    print(f"[worker] received: {wire}")

    if action == "research":
        # Run in executor so we don't block the event loop during HTTP call
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _llm_research, topic, aspect)
    else:
        result = f"unknown action: {action}"

    reply = msg.reply("worker", {"result": result, "action": action, "aspect": aspect})
    wire_reply = encode(reply)
    print(f"[worker] replying: {wire_reply}")
    return reply


async def main() -> None:
    goal = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What are the benefits of async IO?"

    client = ZiprClient(bus, host="127.0.0.1", port=7779)

    print(f"[ZIPR demo] Connecting to server...")
    print(f"[ZIPR demo] Goal: {goal!r}")
    print()

    # Connect, then trigger the planner on the server side with our goal
    connect_task = asyncio.create_task(client.connect())
    bus_task = asyncio.create_task(bus._loop_forever())

    await client.wait_connected(timeout=10.0)

    # Send the goal to the planner (which lives on the server)
    from zipr import task
    goal_msg = task("client", "planner", {"goal": goal})
    await client._forward_to_server(goal_msg)
    print(f"[client] sent goal to planner: {encode(goal_msg)}")
    print()

    # Wait for the work to complete (worker will receive and reply to tasks)
    await asyncio.sleep(30)

    connect_task.cancel()
    bus_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[client] stopped.")
