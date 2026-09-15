#!/usr/bin/env python3
"""Shared helpers for reading ZCode conversation turns.

ZCode stores sessions in an OpenCode-compatible SQLite database
(``~/.zcode/cli/db/db.sqlite``): ``session``, ``message``, and ``part`` tables
whose ``data`` columns hold JSON. That database is the source of truth for
transcript content; this module only ever reads it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from urllib.request import pathname2url

DEFAULT_DB_PATH = os.path.join("~", ".zcode", "cli", "db", "db.sqlite")
AGENT_LABEL = "[ZCode]"


def _tail_truncate(text: str, max_chars: int) -> str:
    """Keep the end of long text, where final conclusions usually appear."""
    if len(text) <= max_chars:
        return text
    return "...(truncated to tail)\n" + text[-max_chars:]


@dataclass
class ZCodeMessage:
    """A single meaningful ZCode message with rendered text."""

    id: str
    role: str
    parent_id: str | None
    time_created: int
    finish: str | None
    text: str


@dataclass
class ZCodeTurn:
    """A user turn plus its assistant follow-up messages."""

    session_id: str
    turn_id: str
    turn_index: int
    start_time: int
    end_time: int
    first_message_id: str
    last_message_id: str
    message_count: int
    assistant_message_count: int
    complete: bool
    messages: list[ZCodeMessage] = field(default_factory=list)

    def render(self, max_chars: int = 3000) -> str:
        """Render the turn into readable transcript text."""
        lines: list[str] = [f"=== Turn {self.turn_index} ({self.turn_id}) ==="]
        for message in self.messages:
            label = "[User]" if message.role == "user" else AGENT_LABEL
            lines.append(f"{label}: {_tail_truncate(message.text.strip(), max_chars)}")
            lines.append("")
        return "\n".join(lines).strip()

    def user_text(self) -> str:
        return next((m.text for m in self.messages if m.role == "user"), "")

    def assistant_text(self) -> str:
        return next((m.text for m in reversed(self.messages) if m.role == "assistant"), "")


def get_db_path() -> str:
    """Return the ZCode SQLite database path; ZCODE_DB_PATH overrides the default."""
    return os.path.expanduser(os.environ.get("ZCODE_DB_PATH") or DEFAULT_DB_PATH)


def connect_readonly(db_path: str) -> sqlite3.Connection:
    """Open the database read-only so reads never block or alter ZCode's writes."""
    uri = f"file:{pathname2url(os.path.abspath(db_path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def session_info(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    """Return the session row (id, parent_id, directory, title) or None."""
    return conn.execute(
        "SELECT id, parent_id, directory, title FROM session WHERE id = ?",
        (session_id,),
    ).fetchone()


def load_messages(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    """Load ZCode messages for a session in chronological order."""
    return conn.execute(
        "SELECT id, data, time_created FROM message WHERE session_id = ? ORDER BY time_created ASC, id ASC",
        (session_id,),
    ).fetchall()


def extract_message_text(conn: sqlite3.Connection, message_id: str) -> str:
    """Render a message's text parts; reasoning, tool, and synthetic parts are skipped."""
    parts = conn.execute(
        "SELECT id, data, time_created FROM part WHERE message_id = ? ORDER BY time_created ASC, id ASC",
        (message_id,),
    ).fetchall()

    text_parts: list[str] = []
    for part in parts:
        try:
            part_data = json.loads(part["data"])
        except Exception:
            continue
        if part_data.get("type") != "text" or part_data.get("synthetic"):
            continue
        text = str(part_data.get("text") or "").strip()
        if text:
            text_parts.append(text)
    return "\n".join(text_parts).strip()


def _is_real_user(msg_data: dict) -> bool:
    """ZCode tags runtime-injected user messages (todo reminders, hook context) by origin."""
    semantics = msg_data.get("semantics")
    if not isinstance(semantics, dict):
        return True
    origin = semantics.get("origin")
    return origin is None or origin == "real_user"


def build_turns(conn: sqlite3.Connection, session_id: str) -> list[ZCodeTurn]:
    """Group ZCode messages into turns: one real user message plus its assistant replies."""
    turns: list[ZCodeTurn] = []
    current: ZCodeTurn | None = None
    current_message_ids: set[str] = set()

    for row in load_messages(conn, session_id):
        try:
            msg_data = json.loads(row["data"])
        except Exception:
            continue

        role = msg_data.get("role", "unknown")
        parent_id = msg_data.get("parentID")
        time_created = int(row["time_created"])
        finish = msg_data.get("finish")
        message_text = extract_message_text(conn, row["id"])

        if role == "user":
            if not message_text or not _is_real_user(msg_data):
                continue
            message = ZCodeMessage(row["id"], role, parent_id, time_created, finish, message_text)
            if current is not None:
                current.complete = _is_complete(current)
                turns.append(current)
            current = ZCodeTurn(
                session_id=session_id,
                turn_id=message.id,
                turn_index=len(turns) + 1,
                start_time=message.time_created,
                end_time=message.time_created,
                first_message_id=message.id,
                last_message_id=message.id,
                message_count=1,
                assistant_message_count=0,
                complete=False,
                messages=[message],
            )
            current_message_ids = {message.id}
            continue

        if role == "assistant" and current is not None:
            if parent_id and parent_id not in current_message_ids:
                continue
            current_message_ids.add(row["id"])
            if not message_text:
                continue
            current.messages.append(ZCodeMessage(row["id"], role, parent_id, time_created, finish, message_text))
            current.end_time = time_created
            current.last_message_id = row["id"]
            current.message_count += 1
            current.assistant_message_count += 1

    if current is not None:
        current.complete = _is_complete(current)
        turns.append(current)

    return turns


def _is_complete(turn: ZCodeTurn) -> bool:
    """A turn is finished once its last assistant text stopped for a reason other than tool calls."""
    finishes = [m.finish for m in turn.messages if m.role == "assistant"]
    if not finishes:
        return False
    return finishes[-1] not in (None, "tool-calls")


def find_turn_index(turns: list[ZCodeTurn], target_turn_id: str) -> int:
    """Find a turn index by exact or prefix match."""
    if not target_turn_id:
        return -1
    for index, turn in enumerate(turns):
        if turn.turn_id == target_turn_id:
            return index
        if turn.turn_id.startswith(target_turn_id) or target_turn_id.startswith(turn.turn_id[:8]):
            return index
    return -1
