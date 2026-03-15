"""
ZIPR CLI — python -m zipr <command> [args]

Commands:
    parse   <wire>          Parse and pretty-print a ZIPR message
    encode  <json>          Encode a JSON object as ZIPR
    bench                   Run the token benchmark
    demo                    Run the demo agent conversation
    compress <wire>         Compress a message and show stats
    validate <wire> <type>  Validate a message against a built-in schema

Examples:
    python -m zipr parse "scout->base|q:loc=enemy;ctx=m1"
    python -m zipr encode '{"src":"a","dst":"b","type":"q","body":{"x":1}}'
    python -m zipr bench
    python -m zipr compress "planner->worker|t:action=search,target=logs"
    python -m zipr validate "worker->planner|e:code=200,msg=ok" error
"""

import sys
import json
import os

# Ensure the package root is importable when run directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zipr.core import parse, encode, pprint, ZiprMessage, ZiprParseError, try_parse


def cmd_parse(args: list[str]) -> int:
    if not args:
        print("Usage: python -m zipr parse <wire-message>", file=sys.stderr)
        return 1
    wire = " ".join(args)
    msg, err = try_parse(wire)
    if err:
        print(f"Parse error: {err}", file=sys.stderr)
        return 1
    print(pprint(msg, color=sys.stdout.isatty()))
    print()
    print(f"  src:  {msg.src}")
    print(f"  dst:  {msg.dst}")
    print(f"  type: {msg.type}")
    if msg.body:
        print(f"  body:")
        for k, v in msg.body.items():
            print(f"    {k} = {v!r}")
    if msg.ctx:
        print(f"  ctx:")
        for k, v in msg.ctx.items():
            print(f"    {k} = {v!r}")
    return 0


def cmd_encode(args: list[str]) -> int:
    if not args:
        print("Usage: python -m zipr encode '<json>'", file=sys.stderr)
        print("  JSON must have keys: src, dst, type, body (dict), ctx (dict, optional)")
        return 1
    try:
        data = json.loads(" ".join(args))
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1

    missing = [k for k in ("src", "dst", "type", "body") if k not in data]
    if missing:
        print(f"Missing required JSON keys: {missing}", file=sys.stderr)
        return 1

    msg = ZiprMessage(
        src=data["src"],
        dst=data["dst"],
        type=data["type"],
        body=data["body"],
        ctx=data.get("ctx", {}),
    )
    print(encode(msg))
    return 0


def cmd_bench(_args: list[str]) -> int:
    bench_path = os.path.join(os.path.dirname(__file__), "..", "benchmark.py")
    if not os.path.exists(bench_path):
        print("benchmark.py not found", file=sys.stderr)
        return 1
    import runpy
    runpy.run_path(bench_path, run_name="__main__")
    return 0


def cmd_compress(args: list[str]) -> int:
    if not args:
        print("Usage: python -m zipr compress <wire-message>", file=sys.stderr)
        return 1
    from zipr.compress import compress_str, stats, ratio
    wire = " ".join(args)
    msg, err = try_parse(wire)
    if err:
        print(f"Parse error: {err}", file=sys.stderr)
        return 1
    s = stats(msg)
    compressed = compress_str(msg)
    print(f"Original  ({s['original_chars']} chars): {wire}")
    print(f"Compressed ({len(compressed)} chars): {compressed}")
    print(f"Ratio: {s['ratio']} ({s['savings_pct']}% smaller)")
    return 0


def cmd_validate(args: list[str]) -> int:
    if len(args) < 2:
        print("Usage: python -m zipr validate <wire-message> <schema-type>", file=sys.stderr)
        print("  Schema types: task, query, response, error, ack, state, broadcast, capabilities, ping")
        return 1
    from zipr.schema import BUILTIN, validate
    wire, schema_name = args[0], args[1]
    msg, err = try_parse(wire)
    if err:
        print(f"Parse error: {err}", file=sys.stderr)
        return 1
    schema = BUILTIN.get(schema_name)
    if schema is None:
        print(f"Unknown schema: {schema_name!r}. Available: {list(BUILTIN)}", file=sys.stderr)
        return 1
    errors = validate(msg, schema)
    if errors:
        print(f"INVALID ({len(errors)} error(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("VALID")
    return 0


def cmd_demo(_args: list[str]) -> int:
    from zipr.core import ping, task, respond, encode, pprint, ZiprMessage

    print("=== ZIPR Demo Conversation ===\n")
    messages = [
        ping("ui", "planner"),
        ZiprMessage("planner", "ui", "p", {"status": "ready"}),
        task("ui", "planner", {"goal": "find all TODO comments in repo"}, ctx="session1"),
        task("planner", "scout", {"action": "grep", "pattern": "TODO", "scope": "/src"}, ctx="session1"),
        respond("scout", "planner", {"matches": 47, "files": ["main.py", "utils.py"]}, ctx="session1"),
        respond("planner", "ui", {"summary": "47 TODOs in 2 files", "files": ["main.py", "utils.py"]}, ctx="session1"),
    ]
    for msg in messages:
        raw = encode(msg)
        print(f"RAW : {raw}")
        print(f"NICE: {pprint(msg, color=sys.stdout.isatty())}")
        print()
    return 0


COMMANDS = {
    "parse": cmd_parse,
    "encode": cmd_encode,
    "bench": cmd_bench,
    "compress": cmd_compress,
    "validate": cmd_validate,
    "demo": cmd_demo,
}


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = args[0]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd!r}", file=sys.stderr)
        print(f"Available: {list(COMMANDS)}", file=sys.stderr)
        return 1

    return COMMANDS[cmd](args[1:])


if __name__ == "__main__":
    sys.exit(main())
