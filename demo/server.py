"""
ZIPR Network Demo — SERVER process

Runs a ZIPR server with a local "planner" agent.
The planner receives goals, delegates work to a remote "worker" agent
(running in a separate process via demo/client.py), and prints results.

Run in two terminals:
    Terminal 1:  python demo/server.py
    Terminal 2:  python demo/client.py "What are the benefits of async IO?"
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zipr import task, ZiprMessage, encode, pprint
from zipr.net import ZiprServer

server = ZiprServer(host="127.0.0.1", port=7779, log_messages=False)


@server.agent("planner")
async def planner(msg: ZiprMessage) -> None:
    goal = msg.body.get("goal", msg.body)
    print(f"\n[planner] goal received: {goal!r}")

    # Break goal into two subtasks and send to remote worker
    subtasks = [
        task("planner", "worker", {"action": "research", "topic": str(goal), "aspect": "overview"}),
        task("planner", "worker", {"action": "research", "topic": str(goal), "aspect": "examples"}),
    ]

    print(f"[planner] sending {len(subtasks)} task(s) to worker...")
    results = []
    for t in subtasks:
        wire = encode(t)
        print(f"  >> {wire}")
        resp = await server.bus.request(t, timeout=20.0)
        wire_back = encode(resp)
        print(f"  << {wire_back}")
        results.append(resp.body.get("result", ""))

    print()
    print("=" * 60)
    print("[planner] FINAL ANSWER:")
    for i, r in enumerate(results, 1):
        print(f"  [{i}] {r}")
    print("=" * 60)

    # Print metrics
    print()
    server.bus.print_metrics()


async def main() -> None:
    print("[ZIPR demo] Starting server — waiting for client to connect...")
    print("            Run:  python demo/client.py \"your question here\"")
    print()
    await server.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[server] stopped.")
