"""
ZIPR Schema Validation — define and enforce message contracts between agents.

Usage:
    from zipr.schema import Schema, field, validate, BUILTIN

    # Use a built-in schema
    errors = validate(msg, BUILTIN["task"])

    # Define your own
    search_task = Schema(
        msg_type="t",
        required=["action", "target"],
        optional=["path", "depth"],
        field_types={"depth": int},
        constraints={"action": lambda v: v in ("search", "grep", "list")},
    )
    errors = validate(msg, search_task)
    if errors:
        print("\\n".join(errors))

    # Decorator for bus handlers
    @bus.agent("worker")
    @search_task.enforces
    async def worker(msg):
        ...
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Callable

from .core import ZiprMessage


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

@dataclass
class Schema:
    """
    Contract for a ZIPR message body.

    Attributes:
        msg_type:     Expected message type code (e.g. "t", "q", "r").
                      None means any type is accepted.
        required:     Body keys that must be present.
        optional:     Body keys that may be present (others are flagged if strict=True).
        field_types:  {key: type} — body values will be checked against these.
        constraints:  {key: callable} — callable(value) must return True.
        strict:       If True, unknown body keys are flagged as errors.
    """
    msg_type: str | None = None
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    field_types: dict[str, type] = field(default_factory=dict)
    constraints: dict[str, Callable[[Any], bool]] = field(default_factory=dict)
    strict: bool = False

    def validate(self, msg: ZiprMessage) -> list[str]:
        """Return a list of validation error strings (empty = valid)."""
        return validate(msg, self)

    def is_valid(self, msg: ZiprMessage) -> bool:
        return len(self.validate(msg)) == 0

    def enforces(self, handler):
        """Decorator: raise SchemaError if the incoming message fails validation."""
        @functools.wraps(handler)
        async def wrapper(msg: ZiprMessage):
            errors = self.validate(msg)
            if errors:
                raise SchemaError(f"Schema validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
            return await handler(msg)
        return wrapper


class SchemaError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate(msg: ZiprMessage, schema: Schema) -> list[str]:
    """Validate a ZiprMessage against a Schema. Returns list of error strings."""
    errors: list[str] = []

    # Type check
    if schema.msg_type is not None and msg.type != schema.msg_type:
        errors.append(f"Expected type '{schema.msg_type}', got '{msg.type}'")

    body = msg.body

    # Required fields
    for key in schema.required:
        if key not in body:
            errors.append(f"Missing required field: '{key}'")

    # Unknown fields (strict mode)
    if schema.strict:
        allowed = set(schema.required) | set(schema.optional)
        for key in body:
            if key not in allowed:
                errors.append(f"Unknown field: '{key}' (strict mode)")

    # Type checks
    for key, expected_type in schema.field_types.items():
        if key in body:
            val = body[key]
            if not isinstance(val, expected_type):
                errors.append(
                    f"Field '{key}' expected {expected_type.__name__}, "
                    f"got {type(val).__name__} ({val!r})"
                )

    # Constraint checks
    for key, constraint in schema.constraints.items():
        if key in body:
            val = body[key]
            try:
                ok = constraint(val)
            except Exception as e:
                errors.append(f"Constraint check for '{key}' raised: {e}")
                continue
            if not ok:
                errors.append(f"Field '{key}' failed constraint check (got {val!r})")

    return errors


# ---------------------------------------------------------------------------
# Built-in schemas for standard ZIPR message types
# ---------------------------------------------------------------------------

BUILTIN: dict[str, Schema] = {
    "ping": Schema(msg_type="p"),

    "query": Schema(msg_type="q"),

    "task": Schema(
        msg_type="t",
        required=["action"],
        field_types={"action": str},
    ),

    "response": Schema(msg_type="r"),

    "ack": Schema(
        msg_type="a",
        optional=["status", "eta"],
    ),

    "error": Schema(
        msg_type="e",
        required=["code", "msg"],
        field_types={"code": int, "msg": str},
        constraints={"code": lambda v: 100 <= v <= 599},
    ),

    "state": Schema(msg_type="s"),

    "broadcast": Schema(msg_type="b"),

    "capabilities": Schema(
        msg_type="c",
        required=["caps"],
        field_types={"caps": list},
    ),
}


# ---------------------------------------------------------------------------
# Schema builder helpers
# ---------------------------------------------------------------------------

def task_schema(
    actions: list[str] | None = None,
    required_fields: list[str] | None = None,
    **kwargs,
) -> Schema:
    """Shorthand for building a task schema with an action allowlist."""
    constraints: dict[str, Callable] = {}
    if actions:
        constraints["action"] = lambda v: v in actions
    return Schema(
        msg_type="t",
        required=["action"] + (required_fields or []),
        constraints=constraints,
        **kwargs,
    )


def response_schema(required_fields: list[str], **kwargs) -> Schema:
    """Shorthand for a response schema that requires certain result fields."""
    return Schema(msg_type="r", required=required_fields, **kwargs)
