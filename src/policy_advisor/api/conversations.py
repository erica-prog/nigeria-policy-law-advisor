"""Per-matter conversation log for the chat-first client (revision 2).

One row per message in the `conversations` table (migration 0005), in order.
It is only ever read or written after `get_matter` has verified ownership. The shared read-only demo matter has no log: several
users can talk to it and their conversations must not mix, so the client
keeps that transcript in memory only.

Also home of `derive_title`, the rule that turns the first message of a
conversation into the human title shown in "Your cases" (the user never
sees a matter id)."""

import re
import uuid
from datetime import UTC, datetime

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from policy_advisor.api.schemas import MATTER_TITLE_MAX, ChatReply, UserMessage
from policy_advisor.db import connect, transaction

TITLE_TARGET = 60
_WS = re.compile(r"\s+")
_MESSAGES = TypeAdapter(list[UserMessage | ChatReply])


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_message_id() -> str:
    return uuid.uuid4().hex


def derive_title(message: str, limit: int = TITLE_TARGET) -> str:
    """First ~60 characters of the message, cut at a word boundary, one line.
    Falls back to "New case" for whitespace-only input."""
    text = _WS.sub(" ", message).strip().strip("\"'“”‘’")
    if not text:
        return "New case"
    if len(text) <= limit:
        return text[:MATTER_TITLE_MAX]
    cut = text[: limit + 1]
    space = cut.rfind(" ")
    head = cut[:space] if space >= limit // 2 else text[:limit]
    return head.rstrip(" ,;:.-") + "…"


def load_messages(matter_id: str) -> list[UserMessage | ChatReply]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT message FROM conversations WHERE matter_id = %s ORDER BY id", (matter_id,)
        ).fetchall()
    return _MESSAGES.validate_python([row["message"] for row in rows])


def append_messages(matter_id: str, *messages: UserMessage | ChatReply) -> None:
    """One transaction per exchange, so a user message never lands without
    its reply (or the reverse) if the process dies between the two."""
    if not messages:
        return
    with connect() as conn, transaction(conn):
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO conversations (matter_id, message) VALUES (%s, %s)",
                [(matter_id, Jsonb(m.model_dump(mode="json"))) for m in messages],
            )


def has_analysis(messages: list[UserMessage | ChatReply]) -> bool:
    return any(isinstance(m, ChatReply) and m.mode == "analysis" for m in messages)


def first_user_message(messages: list[UserMessage | ChatReply]) -> UserMessage | None:
    for message in messages:
        if isinstance(message, UserMessage):
            return message
    return None
