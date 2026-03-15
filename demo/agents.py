"""
Demo: Two Claude agents communicating via ZIPR.

Architecture:
    User -> Planner agent (Claude)
         -> breaks goal into subtasks, sends ZIPR task messages to Worker
    Worker agent (Claude)
         -> receives ZIPR tasks, executes them (web search / reasoning), replies in ZIPR
    Planner -> synthesizes results, returns final answer to user

Run:
    ANTHROPIC_API_KEY=sk-... python demo/agents.py "What are the main risks of AGI?"
"""

import sys
import os
import json
import re as _re

import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from zipr import parse, encode, task, respond, error, ZiprMessage

client = anthropic.Anthropic()
MODEL = "claude-haiku-4-5-20251001"   # fast + cheap for demo

# ---------------------------------------------------------------------------
# System prompts — agents are told to speak ZIPR
# ---------------------------------------------------------------------------

PLANNER_SYSTEM = """You are Planner, an orchestrating AI agent.
You communicate with a Worker agent using ZIPR — a compact token-efficient protocol.

ZIPR format:  src->dst|type:key=val,key2=val2;ctx_tag=val

Message types you use:
  t = task   (assign work to worker)
  r = result (return final answer to the user)

Rules:
1. Break the user's goal into 1-3 subtasks.
2. Send each subtask to worker as a ZIPR task message, one per line.
3. After worker replies, synthesize the results and output a final plain-text answer.
4. ZIPR messages must be on their own line, no surrounding text.

Example output (planning phase):
planner->worker|t:action=research,topic="AGI risks",depth=brief;id=t1;ctx=main
planner->worker|t:action=research,topic="AGI timelines",depth=brief;id=t2;ctx=main

After worker replies with ZIPR `r` messages, output:
FINAL: <your synthesized answer here>
"""

WORKER_SYSTEM = """You are Worker, a capable AI agent that executes tasks.
You receive tasks in ZIPR format and reply in ZIPR format.

ZIPR format:  src->dst|type:key=val,key2=val2;re=<original_task_id>

Message types you use:
  r = response  (result of a task)
  e = error     (if you cannot complete a task)

Rules:
1. Parse the incoming ZIPR task.
2. Execute the task (research, reasoning, summarization, etc.).
3. Reply with a single ZIPR `r` message containing your result.
4. Keep values compact — use short phrases, not full paragraphs.
5. Always include ;re=<task_id> referencing the task you're responding to.

Example:
Input:  planner->worker|t:action=research,topic="AGI risks";id=t1
Output: worker->planner|r:summary="alignment,misuse,concentration of power",confidence=0.9;re=t1
"""

# ---------------------------------------------------------------------------
# Agent wrappers
# ---------------------------------------------------------------------------

def run_planner(goal: str) -> tuple[list[ZiprMessage], str]:
    """Planner decides what to ask the worker. Returns task messages + raw output."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=PLANNER_SYSTEM,
        messages=[{"role": "user", "content": f"Goal: {goal}"}],
    )
    raw = resp.content[0].text.strip()

    # Extract ZIPR task lines
    task_msgs = []
    for line in raw.splitlines():
        line = line.strip()
        if _re.match(r"^\w[\w\-\.]*->\w[\w\-\.]*\|[a-z]:", line):
            try:
                msg = parse(line)
                if msg.type == "t":
                    task_msgs.append(msg)
            except Exception:
                pass

    return task_msgs, raw


def run_worker(task_msg: ZiprMessage) -> ZiprMessage:
    """Worker executes one task and returns a ZIPR response."""
    wire = encode(task_msg)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=WORKER_SYSTEM,
        messages=[{"role": "user", "content": wire}],
    )
    raw = resp.content[0].text.strip()

    # Find the ZIPR response line
    for line in raw.splitlines():
        line = line.strip()
        if _re.match(r"^\w[\w\-\.]*->\w[\w\-\.]*\|[a-z]:", line):
            try:
                return parse(line)
            except Exception:
                pass

    # Fallback: wrap raw text in an error
    return error("worker", "planner", 500, raw[:120], re=task_msg.ctx.get("id", ""))


def run_planner_synthesize(goal: str, task_results: list[ZiprMessage]) -> str:
    """Planner synthesizes worker results into a final answer."""
    result_block = "\n".join(encode(r) for r in task_results)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=PLANNER_SYSTEM,
        messages=[
            {"role": "user", "content": f"Goal: {goal}"},
            {"role": "assistant", "content": "(planning done, tasks sent)"},
            {"role": "user", "content": f"Worker results:\n{result_block}\n\nNow output: FINAL: <answer>"},
        ],
    )
    raw = resp.content[0].text.strip()
    for line in raw.splitlines():
        if line.startswith("FINAL:"):
            return line[len("FINAL:"):].strip()
    return raw  # fallback


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(goal: str):
    print(f"\nGoal: {goal}\n")
    print("=" * 60)

    # Step 1: Planner decomposes goal
    print("[Planner] Decomposing goal...")
    tasks, planner_raw = run_planner(goal)

    if not tasks:
        print("[Planner] Could not extract tasks. Raw output:")
        print(planner_raw)
        return

    print(f"[Planner] Issued {len(tasks)} task(s):\n")
    for t in tasks:
        print(f"  ZIPR -> {encode(t)}")

    print()

    # Step 2: Worker executes each task
    results = []
    for t in tasks:
        task_id = t.ctx.get("id", "?")
        print(f"[Worker] Executing task {task_id}: {t.body.get('action','?')} ...")
        result = run_worker(t)
        results.append(result)
        print(f"  ZIPR <- {encode(result)}")

    print()

    # Step 3: Planner synthesizes
    print("[Planner] Synthesizing results...")
    final = run_planner_synthesize(goal, results)

    print()
    print("=" * 60)
    print("ANSWER:")
    print(final)
    print("=" * 60)

    # Token summary
    zipr_chars = sum(len(encode(t)) for t in tasks) + sum(len(encode(r)) for r in results)
    json_equiv = sum(
        len(json.dumps({"from": t.src, "to": t.dst, "type": t.type, "body": t.body, "context": t.ctx}))
        for t in tasks + results
    )
    print(f"\nInter-agent messages: {len(tasks)} tasks + {len(results)} responses")
    print(f"ZIPR wire size:  {zipr_chars} chars")
    print(f"JSON equivalent: ~{json_equiv} chars")
    print(f"Savings: ~{json_equiv - zipr_chars} chars ({(json_equiv - zipr_chars)/json_equiv*100:.0f}%)")


if __name__ == "__main__":
    goal = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What are the biggest risks of AI agents?"
    run(goal)
