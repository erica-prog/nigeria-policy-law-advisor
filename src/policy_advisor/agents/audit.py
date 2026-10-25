"""The audit trail of agent runs: tables `agent_runs` and `agent_tool_calls`
(migration 0008).

A run is opened before the first model call and closed with its outcome; a
tool call is recorded whether it ran, was refused by the policy, hit the
budget, or failed. Arguments are truncated before storage so a pasted
document in a question does not end up duplicated in the audit table; tool
results are measured, not stored.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from policy_advisor.db import connect

ARGUMENT_CHARS = 500


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    matter_id: str | None
    username: str | None
    question: str
    model: str
    mode: str
    status: str
    tool_call_count: int
    input_tokens: int | None
    output_tokens: int | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class ToolCallRecord:
    seq: int
    connector: str
    tool: str
    source_id: str | None
    arguments: dict[str, Any]
    status: str
    result_chars: int | None
    duration_ms: int | None
    error: str | None


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Strings cut at ARGUMENT_CHARS; everything else as is."""
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > ARGUMENT_CHARS:
            out[key] = value[:ARGUMENT_CHARS] + f"… [{len(value) - ARGUMENT_CHARS} more characters]"
        else:
            out[key] = value
    return out


def start_run(
    *,
    matter_id: str | None,
    username: str | None,
    question: str,
    model: str,
    mode: str = "research",
) -> str:
    run_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_runs (run_id, matter_id, username, question, model, mode) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (run_id, matter_id, username, question[:4000], model, mode),
        )
    return run_id


def record_call(
    run_id: str,
    seq: int,
    *,
    connector: str,
    tool: str,
    status: str,
    source_id: str | None = None,
    arguments: dict[str, Any] | None = None,
    result_chars: int | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_tool_calls (run_id, seq, connector, tool, source_id, arguments, status, "
            "result_chars, duration_ms, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id,
                seq,
                connector,
                tool,
                source_id,
                Jsonb(redact_arguments(arguments or {})),
                status,
                result_chars,
                duration_ms,
                (error or None) and str(error)[:1000],
            ),
        )


def finish_run(
    run_id: str,
    *,
    status: str,
    tool_call_count: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = %s, tool_call_count = %s, input_tokens = %s, "
            "output_tokens = %s, error = %s, finished_at = now() WHERE run_id = %s",
            (
                status,
                tool_call_count,
                input_tokens,
                output_tokens,
                error and str(error)[:1000],
                run_id,
            ),
        )


def get_run(run_id: str) -> RunRecord | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE run_id = %s", (run_id,)).fetchone()
    if row is None:
        return None
    return RunRecord(**{k: row[k] for k in RunRecord.__dataclass_fields__})


def list_calls(run_id: str) -> list[ToolCallRecord]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT seq, connector, tool, source_id, arguments, status, result_chars, duration_ms, error "
            "FROM agent_tool_calls WHERE run_id = %s ORDER BY seq",
            (run_id,),
        ).fetchall()
    return [
        ToolCallRecord(
            seq=r["seq"],
            connector=r["connector"],
            tool=r["tool"],
            source_id=r["source_id"],
            arguments=r["arguments"]
            if isinstance(r["arguments"], dict)
            else json.loads(r["arguments"]),
            status=r["status"],
            result_chars=r["result_chars"],
            duration_ms=r["duration_ms"],
            error=r["error"],
        )
        for r in rows
    ]
