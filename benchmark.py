"""
Benchmark: ZIPR vs JSON token cost for typical agent-to-agent messages.

Shows real token savings using tiktoken (cl100k_base, same tokenizer as GPT-4/Claude).
"""

import json
import tiktoken

from zipr import parse, encode, task, respond, query, error, broadcast

enc = tiktoken.get_encoding("cl100k_base")

def tokens(s: str) -> int:
    return len(enc.encode(s))


# ---------------------------------------------------------------------------
# Representative agent messages — same semantic content, two formats
# ---------------------------------------------------------------------------

SCENARIOS = []

def scenario(name: str, zipr_msg, json_equiv: dict):
    zipr_wire = encode(zipr_msg)
    json_wire = json.dumps(json_equiv, separators=(",", ":"))
    SCENARIOS.append((name, zipr_wire, json_wire))


# 1. Simple task delegation
scenario(
    "Task delegation",
    task("planner", "worker", {"action": "search", "target": "config files", "path": "/etc"}, ctx="job1"),
    {
        "from": "planner", "to": "worker", "type": "task",
        "body": {"action": "search", "target": "config files", "path": "/etc"},
        "context": {"id": "abc123", "priority": 5, "ctx": "job1", "ts": 1741996800}
    }
)

# 2. Query with context
scenario(
    "Query with context",
    query("scout", "base", {"loc": "enemy", "zone": "north"}, ctx="mission1"),
    {
        "from": "scout", "to": "base", "type": "query",
        "body": {"loc": "enemy", "zone": "north"},
        "context": {"id": "q001", "ctx": "mission1", "ts": 1741996800}
    }
)

# 3. Rich response with list
scenario(
    "Response with list payload",
    respond("worker", "planner", {
        "status": "done",
        "found": ["main.py", "utils.py", "config.py"],
        "count": 3,
        "elapsed": 1.4
    }),
    {
        "from": "worker", "to": "planner", "type": "response",
        "body": {
            "status": "done",
            "found": ["main.py", "utils.py", "config.py"],
            "count": 3,
            "elapsed": 1.4
        },
        "context": {"id": "r001", "re": "t001", "ts": 1741996800}
    }
)

# 4. Error
scenario(
    "Error message",
    error("worker", "planner", 404, "target not found", re="t001"),
    {
        "from": "worker", "to": "planner", "type": "error",
        "body": {"code": 404, "message": "target not found"},
        "context": {"re": "t001", "ts": 1741996800}
    }
)

# 5. Broadcast capability advertisement
scenario(
    "Capability broadcast",
    broadcast("agent7", {"caps": ["search", "summarize", "translate"], "lang": ["en", "fr", "de"]}),
    {
        "from": "agent7", "to": "*", "type": "broadcast",
        "body": {"capabilities": ["search", "summarize", "translate"], "languages": ["en", "fr", "de"]},
        "context": {"id": "b001", "ts": 1741996800}
    }
)

# 6. Nested state update
scenario(
    "State snapshot",
    respond("monitor", "log", {"cpu": 0.72, "mem": 0.41, "tasks": ["t01", "t02"], "queue": 3}),
    {
        "from": "monitor", "to": "log", "type": "state",
        "body": {"cpu": 0.72, "mem": 0.41, "tasks": ["t01", "t02"], "queue": 3},
        "context": {"ts": 1741996800}
    }
)


# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------

print("=" * 72)
print(f"{'ZIPR vs JSON Token Benchmark':^72}")
print(f"{'tokenizer: cl100k_base (same as GPT-4 / Claude)':^72}")
print("=" * 72)
print()

total_zipr = 0
total_json = 0

for name, zipr_wire, json_wire in SCENARIOS:
    zt = tokens(zipr_wire)
    jt = tokens(json_wire)
    saved = jt - zt
    pct = (saved / jt) * 100
    total_zipr += zt
    total_json += jt

    print(f"  {name}")
    print(f"    ZIPR  ({zt:>3} tok): {zipr_wire}")
    print(f"    JSON  ({jt:>3} tok): {json_wire}")
    print(f"    Saved: {saved} tokens  ({pct:.0f}% reduction)")
    print()

print("-" * 72)
print(f"  TOTAL across {len(SCENARIOS)} messages")
print(f"    ZIPR: {total_zipr} tokens")
print(f"    JSON: {total_json} tokens")
saved_total = total_json - total_zipr
pct_total = (saved_total / total_json) * 100
print(f"    Saved: {saved_total} tokens  ({pct_total:.0f}% reduction)")
print()
print("  Real-world projection (1,000 inter-agent messages / run):")
per_msg_saving = saved_total / len(SCENARIOS)
saved_1k = int(per_msg_saving * 1000)
# claude-3.5-sonnet input pricing ~$3/M tokens
cost_saving = (saved_1k / 1_000_000) * 3.0
print(f"    ~{saved_1k:,} tokens saved")
print(f"    ~${cost_saving:.4f} saved at $3/M input tokens")
print(f"    At 10,000 runs/day: ~${cost_saving * 10_000:.2f}/day saved")
print("=" * 72)
