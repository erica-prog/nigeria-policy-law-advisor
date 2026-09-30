"""Per-matter conversation log for the chat-first client (revision 2).

One JSON sidecar per matter, `conversation.json`, next to the chunks and
meta files the ingestion layer already keeps, so the storage PR can move all
three together. It is only ever read or written after `get_matter` has
verified ownership. The shared read-only demo matter has no log: several
users can talk to it and their conversations must not mix, so the client
keeps that transcript in memory only.

Also home of `derive_title`, the rule that turns the first message of a
conversation into the human title shown in "Your cases" (the user never
sees a matter id)."""

import json
import re
import threading
import uuid
from datetime import UTC, datetime

from pydantic import TypeAdapter

from policy_advisor.api.schemas import MATTER_TITLE_MAX, ChatReply, UserMessage
from policy_advisor.ingestion.matter_store import matter_meta_path

TITLE_TARGET = 60
_WS = re.compile(r"\s+")
_LOG_LOCK = threading.Lock()
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


def conversation_path(matter_id: str):
    return matter_meta_path(matter_id).parent / "conversation.json"


def load_messages(matter_id: str) -> list[UserMessage | ChatReply]:
    path = conversation_path(matter_id)
    if not path.exists():
        return []
    with _LOG_LOCK:
        raw = json.loads(path.read_text(encoding="utf-8"))
    return _MESSAGES.validate_python(raw.get("messages", []))


def append_messages(matter_id: str, *messages: UserMessage | ChatReply) -> None:
    path = conversation_path(matter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK:
        existing: list = []
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8")).get("messages", [])
        existing.extend(m.model_dump(mode="json") for m in messages)
        path.write_text(json.dumps({"messages": existing}), encoding="utf-8")


def has_analysis(messages: list[UserMessage | ChatReply]) -> bool:
    return any(isinstance(m, ChatReply) and m.mode == "analysis" for m in messages)


def first_user_message(messages: list[UserMessage | ChatReply]) -> UserMessage | None:
    for message in messages:
        if isinstance(message, UserMessage):
            return message
    return None
