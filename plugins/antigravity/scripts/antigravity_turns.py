"""Parse Antigravity JSONL transcripts into turns.

Antigravity writes ``transcript.jsonl`` (compact) and ``transcript_full.jsonl``
(untruncated) under ``<app_data_dir>/brain/<conversationId>/.system_generated/logs/``.
Each line is a step object keyed by ``step_index``; a single step may be flushed
more than once as it streams, so the last line per ``step_index`` wins.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
MAX_TOOL_ARG_CHARS = 180
MAX_RENDER_CHARS = 12_000


def unwrap_user_request(content: str) -> str:
    """Extract the user's message from ``<USER_REQUEST>...</USER_REQUEST>``."""
    match = USER_REQUEST_RE.search(content)
    if match:
        return match.group(1).strip()
    return content.strip()


def _unquote_arg(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] in {'"', "[", "{"}:
            with contextlib.suppress(ValueError):
                parsed = json.loads(stripped)
                if isinstance(parsed, str):
                    return parsed
                return json.dumps(parsed, ensure_ascii=False)
        return value
    return json.dumps(value, ensure_ascii=False)


def _format_tool_call(call: dict[str, Any]) -> str:
    name = str(call.get("name") or "tool")
    args = call.get("args")
    if not isinstance(args, dict) or not args:
        return f"[tool] {name}"
    for key in ("CommandLine", "Path", "TargetFile", "Query", "query", "Url", "Action", "Prompt"):
        if key in args:
            summary = " ".join(_unquote_arg(args[key]).split())[:MAX_TOOL_ARG_CHARS]
            return f"[tool] {name}: {summary}"
    parts: list[str] = []
    for k, v in list(args.items())[:3]:
        val = " ".join(_unquote_arg(v).split())[:80]
        parts.append(f"{k}={val}")
    return f"[tool] {name}({', '.join(parts)})"


@dataclass
class AntigravityTurn:
    turn_id: str
    step_index: int
    created_at: str = ""
    user_parts: list[str] = field(default_factory=list)
    assistant_parts: list[str] = field(default_factory=list)
    tool_lines: list[str] = field(default_factory=list)
    complete: bool = False

    def user_text(self) -> str:
        return "\n".join(p for p in self.user_parts if p).strip()

    def assistant_text(self) -> str:
        return "\n\n".join(p for p in self.assistant_parts if p).strip()

    def render(self) -> str:
        sections: list[str] = []
        user = self.user_text()
        if user:
            sections.append(f"[User]: {user}")
        if self.tool_lines:
            sections.append("\n".join(self.tool_lines))
        assistant = self.assistant_text()
        if assistant:
            sections.append(f"[Antigravity]: {assistant}")
        rendered = "\n\n".join(sections)
        if len(rendered) > MAX_RENDER_CHARS:
            rendered = rendered[:MAX_RENDER_CHARS] + "\n... [truncated]"
        return rendered


def resolve_transcript_path(transcript_path: str, *, prefer_full: bool = False) -> Path:
    path = Path(os.path.expanduser(transcript_path))
    if prefer_full and path.name == "transcript.jsonl":
        full_path = path.with_name("transcript_full.jsonl")
        if full_path.is_file():
            return full_path
    return path


def load_steps(path: Path) -> list[dict[str, Any]]:
    """Load JSONL steps, keeping the latest record for each ``step_index``."""
    by_index: dict[int, dict[str, Any]] = {}
    fallback_idx = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            idx = obj.get("step_index")
            if isinstance(idx, int):
                by_index[idx] = obj
                fallback_idx = max(fallback_idx, idx + 1)
            else:
                by_index[fallback_idx] = obj
                fallback_idx += 1
    return [by_index[k] for k in sorted(by_index)]


def build_turns(transcript_path: str, *, prefer_full: bool = False) -> list[AntigravityTurn]:
    path = resolve_transcript_path(transcript_path, prefer_full=prefer_full)
    if not path.is_file():
        return []
    steps = load_steps(path)
    turns: list[AntigravityTurn] = []
    current: AntigravityTurn | None = None

    for step in steps:
        step_type = str(step.get("type") or "")
        source = str(step.get("source") or "")
        status = str(step.get("status") or "")
        step_index = int(step.get("step_index") or 0)

        if step_type == "USER_INPUT" and source in {"", "USER_EXPLICIT"}:
            text = unwrap_user_request(str(step.get("content") or ""))
            if not text:
                continue
            current = AntigravityTurn(
                turn_id=f"step_{step_index}",
                step_index=step_index,
                created_at=str(step.get("created_at") or ""),
                user_parts=[text],
            )
            turns.append(current)
            continue

        if current is None:
            continue

        if step_type == "PLANNER_RESPONSE":
            content = str(step.get("content") or "").strip()
            if content:
                current.assistant_parts.append(content)
            tool_calls = step.get("tool_calls")
            has_tools = isinstance(tool_calls, list) and len(tool_calls) > 0
            if has_tools:
                for call in tool_calls:
                    if isinstance(call, dict):
                        current.tool_lines.append(_format_tool_call(call))
            current.complete = status == "DONE" and not has_tools

    return turns


def find_turn_index(turns: list[AntigravityTurn], turn_id: str) -> int:
    for idx, turn in enumerate(turns):
        if turn.turn_id == turn_id or turn.turn_id.startswith(turn_id):
            return idx
    return -1


def find_summaries_db(transcript_path: str | None = None) -> Path | None:
    """Locate ``conversation_summaries.db`` for the Antigravity surface."""
    override = os.environ.get("ANTIGRAVITY_SUMMARIES_DB", "").strip()
    if override:
        p = Path(os.path.expanduser(override))
        return p if p.is_file() else None
    if transcript_path:
        parts = Path(os.path.expanduser(transcript_path)).parts
        if "brain" in parts:
            brain_idx = parts.index("brain")
            candidate = Path(*parts[:brain_idx]) / "conversation_summaries.db"
            if candidate.is_file():
                return candidate
    default_db = Path.home() / ".gemini" / "antigravity" / "conversation_summaries.db"
    return default_db if default_db.is_file() else None


def is_subagent(conversation_id: str, transcript_path: str | None = None) -> bool:
    """Return True if ``conversation_id`` has a parent conversation in Antigravity."""
    db_path = find_summaries_db(transcript_path)
    if db_path is None or not conversation_id:
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            row = conn.execute(
                "SELECT parent_conversation_id, nesting_depth FROM conversation_summaries WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    parent_id, nesting_depth = row
    return bool(parent_id) or (isinstance(nesting_depth, int) and nesting_depth > 0)
