# ZIPR — Zero-overhead Interagent Protocol

A **token-efficient message format** for AI agent networks.

When AI agents talk to each other through prompts, every word costs money.
JSON — the current default — is designed for humans and parsers, not for LLMs.
ZIPR strips inter-agent messages down to their minimum token footprint.

## Benchmark

```
Task delegation     ZIPR: 38 tok  JSON: 52 tok   -27%
Query with context  ZIPR: 33 tok  JSON: 43 tok   -23%
Response with list  ZIPR: 43 tok  JSON: 60 tok   -28%
Error message       ZIPR: 26 tok  JSON: 40 tok   -35%
Capability bcast    ZIPR: 36 tok  JSON: 49 tok   -27%
State snapshot      ZIPR: 41 tok  JSON: 48 tok   -15%
─────────────────────────────────────────────────────
Average             ZIPR: 217 tok JSON: 292 tok  -26%
```

At 10,000 runs/day with 1,000 inter-agent messages each: **~$375/day saved**.

## Format

```
src->dst|type:key=val,key2=val2;tag=val;tag2=val2
```

| Part | Example | Meaning |
|------|---------|---------|
| `src->dst` | `planner->worker` | sender and receiver agent IDs |
| `\|type:` | `\|t:` | message type (see below) |
| `body` | `action=search,target=logs` | comma-separated key=value payload |
| `;tags` | `;id=abc;ctx=job1` | optional context tags |

**Message types:** `q` query · `r` response · `t` task · `a` ack · `e` error · `s` state · `b` broadcast · `c` caps · `p` ping · `x` terminate

**Value types:** plain string · `"quoted string"` · `42` number · `0.9` float · `T`/`F` bool · `~` null · `[a,b,c]` list · `{k=v}` nested dict · `#id` reference

## Install

```bash
pip install zipr                    # core only
pip install zipr[demo]              # + Anthropic SDK for agent demo
pip install zipr[bench]             # + tiktoken for benchmarks
```

## Usage

```python
from zipr import task, respond, parse, encode, pprint

# Build messages
msg = task("planner", "worker", {"action": "search", "target": "logs"}, ctx="job1")
wire = encode(msg)
# "planner->worker|t:action=search,target=logs;id=3a9f1c;ts=1741996800;pri=5;ctx=job1"

# Parse incoming
msg = parse(wire)
print(pprint(msg))
# planner -> worker  TASK  action=search target=logs [id=3a9f1c] [ctx=job1]

# Use in an LLM prompt
system_prompt = f"""
You are a worker agent. Receive tasks in ZIPR format and reply in ZIPR.
Reply format: worker->planner|r:result=<value>;re=<task_id>
"""
user_message = encode(msg)   # pass the ZIPR wire directly
```

## Multi-agent demo

```bash
export ANTHROPIC_API_KEY=sk-...
python demo/agents.py "What are the main risks of AI agents?"
```

Two Claude agents (Planner + Worker) communicate entirely over ZIPR, then synthesize a final answer.

## Files

```
zipr/           core library — parse, encode, message builders
spec.md         full language specification
benchmark.py    token cost comparison vs JSON
demo/agents.py  live two-agent demo using Claude API
```

## Why not JSON?

JSON keys are long, quotes are expensive, brackets add overhead, and the schema is fixed.
ZIPR uses 1-char type codes, drops quotes where possible, and packs metadata into semicolon-delimited tags rather than nested objects.
Agents trained on structured text handle it reliably without any fine-tuning.
