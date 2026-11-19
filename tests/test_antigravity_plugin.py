"""Tests for the Antigravity plugin (transcript parser, hooks, and capture worker)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "antigravity"
sys.path.insert(0, str(PLUGIN_DIR / "scripts"))

import antigravity_turns  # noqa: E402


def _write_transcript(path: Path, steps: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")


def _write_summaries_db(db_path: Path, rows: list[tuple[str, str, int]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE conversation_summaries ("
        "conversation_id TEXT PRIMARY KEY, "
        "parent_conversation_id TEXT NOT NULL DEFAULT '', "
        "nesting_depth INTEGER NOT NULL DEFAULT 0)"
    )
    conn.executemany(
        "INSERT INTO conversation_summaries (conversation_id, parent_conversation_id, nesting_depth) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_build_turns_deduplicates_steps_and_unwraps_user_request(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(
        transcript,
        [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-11-19T09:42:24Z",
                "content": "<USER_REQUEST>\npartial\n</USER_REQUEST>",
            },
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-11-19T09:43:30Z",
                "content": "<USER_REQUEST>\nFix the login timeout bug\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nfoo\n</ADDITIONAL_METADATA>",
            },
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "thinking": "internal thoughts",
                "tool_calls": [{"name": "read_file", "args": {"Path": '"/repo/auth.py"'}}],
            },
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "GENERIC",
                "status": "DONE",
                "content": "file contents",
            },
            {
                "step_index": 3,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "Increased the session timeout from 5s to 30s in auth.py.",
            },
        ],
    )

    turns = antigravity_turns.build_turns(str(transcript))
    assert len(turns) == 1
    turn = turns[0]
    assert turn.turn_id == "step_0"
    assert turn.user_text() == "Fix the login timeout bug"
    assert turn.assistant_text() == "Increased the session timeout from 5s to 30s in auth.py."
    assert turn.tool_lines == ["[tool] read_file: /repo/auth.py"]
    assert turn.complete is True
    rendered = turn.render()
    assert "internal thoughts" not in rendered
    assert "[User]: Fix the login timeout bug" in rendered
    assert "[Antigravity]: Increased the session timeout" in rendered


def test_is_subagent_checks_conversation_summaries_db(tmp_path: Path) -> None:
    app_data = tmp_path / "antigravity"
    db_path = app_data / "conversation_summaries.db"
    _write_summaries_db(
        db_path,
        [
            ("conv-root", "", 0),
            ("conv-child", "conv-root", 1),
        ],
    )
    transcript = app_data / "brain" / "conv-child" / ".system_generated" / "logs" / "transcript.jsonl"
    assert antigravity_turns.is_subagent("conv-root", str(transcript)) is False
    assert antigravity_turns.is_subagent("conv-child", str(transcript)) is True


def test_hooks_json_valid_and_at_plugin_root() -> None:
    hooks_data = json.loads((PLUGIN_DIR / "hooks.json").read_text(encoding="utf-8"))
    plugin_data = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert plugin_data["name"] == "memsearch"
    assert "memsearch" in hooks_data
    for event in ("SessionStart", "PreInvocation", "Stop"):
        assert isinstance(hooks_data["memsearch"][event], list)
        assert len(hooks_data["memsearch"][event]) == 1


def test_pre_invocation_hook_deduplicates_same_prompt(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ms = fake_bin / "memsearch"
    fake_ms.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_ms.chmod(0o755)

    workspace = tmp_path / "repo"
    workspace.mkdir()
    memsearch_dir = tmp_path / "ms"

    payload = json.dumps(
        {
            "conversationId": "conv-123",
            "workspacePaths": [str(workspace)],
            "lastUserInput": "What did we decide about the auth middleware?",
        }
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "MEMSEARCH_DIR": str(memsearch_dir),
    }

    first = subprocess.run(
        ["bash", str(PLUGIN_DIR / "hooks" / "pre-invocation.sh")],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    first_json = json.loads(first.stdout)
    assert first_json == {"injectSteps": [{"ephemeralMessage": "[memsearch] Recall available if needed"}]}

    second = subprocess.run(
        ["bash", str(PLUGIN_DIR / "hooks" / "pre-invocation.sh")],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert json.loads(second.stdout) == {}
