#!/usr/bin/env python3
"""Capture one finished ZCode turn into the MemSearch journal.

Spawned detached by hooks/stop.sh with the Stop hook payload. The hook's
transcript_path only carries the last assistant message, so the worker reads
the finished turn from ZCode's SQLite store instead, summarizes it, appends it
to the daily journal, re-indexes, and wakes maintenance.

Usage:
  capture-turn.py <payload.json> --project-dir DIR --memsearch-dir DIR \\
      --collection NAME -- <memsearch command...>
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from zcode_turns import ZCodeTurn, build_turns, connect_readonly, get_db_path, session_info

PLUGIN_DIR = Path(__file__).resolve().parent.parent
AGENT_NAME = "ZCode"
# ZCode may commit the final assistant message slightly after the Stop hook
# fires; poll the database for this long before capturing whatever is there.
DB_WAIT_SECONDS = float(os.environ.get("MEMSEARCH_ZCODE_DB_WAIT_S", "10"))
SUMMARY_UNAVAILABLE = (
    "- Memory summary unavailable: {reason}; transcript content was omitted. "
    "Use the transcript anchor for progressive disclosure."
)


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
    timeout: int = 30,
) -> str:
    """Run a command and return its stdout, or '' when it fails for any reason."""
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def load_config_snapshot(command: list[str], collection: str, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    """Read the resolved memsearch config once instead of one CLI start per key."""
    output = run_command(
        [*command, "config", "list", "--resolved", "--json-output", "--default-collection", collection],
        cwd=cwd,
        env=env,
        timeout=30,
    )
    try:
        data = json.loads(output)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def config_value(snapshot: dict[str, Any], dotted_key: str, default: Any = "") -> Any:
    value: Any = snapshot
    for key in dotted_key.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return default if value is None else value


def wait_for_turn(db_path: str, session_id: str, deadline: float) -> tuple[ZCodeTurn | None, bool]:
    """Poll until the session's last turn is complete. Returns (turn, is_subagent)."""
    while True:
        conn = connect_readonly(db_path)
        try:
            info = session_info(conn, session_id)
            if info is not None and info["parent_id"]:
                return None, True
            turns = build_turns(conn, session_id)
        finally:
            conn.close()
        turn = turns[-1] if turns else None
        if turn is not None and turn.complete and turn.assistant_message_count > 0:
            return turn, False
        if time.monotonic() >= deadline:
            return turn, False
        time.sleep(1)


def fallback_transcript(payload: dict[str, Any]) -> str:
    """Without a database turn, the Stop payload still carries the final assistant text."""
    text = str(payload.get("last_assistant_message") or "").strip()
    return f"[{AGENT_NAME}]: {text}" if text else ""


def plain_extract(turn: ZCodeTurn | None, payload: dict[str, Any]) -> str:
    """Bullet extract used when no LLM summarizer is configured."""
    user_text = " ".join(turn.user_text().split())[:500] if turn else ""
    assistant_text = turn.assistant_text() if turn else str(payload.get("last_assistant_message") or "")
    assistant_text = " ".join(assistant_text.split())[:1200]
    lines = []
    if user_text:
        lines.append(f"- User asked: {user_text}")
    if assistant_text:
        lines.append(f"- {AGENT_NAME}: {assistant_text}")
    return "\n".join(lines)


def summarize(transcript: str, snapshot: dict[str, Any], command: list[str], cwd: Path, env: dict[str, str]) -> str:
    """Summarize through a memsearch-managed provider; ZCode has no headless CLI for a native path."""
    provider = str(config_value(snapshot, "plugins.zcode.summarize.provider"))
    if not provider or provider == "native":
        return ""
    result = run_command(
        [*command, "summarize", "--plugin", "zcode", "--agent-name", AGENT_NAME],
        cwd=cwd,
        env=env,
        input_text=transcript,
        timeout=120,
    )
    if result:
        return result
    return SUMMARY_UNAVAILABLE.format(reason="summarizer failed or returned no usable output")


def capture_exists(memory_dir: Path, session_id: str, turn_id: str) -> bool:
    marker = f"<!-- session:{session_id} turn:{turn_id} "
    for journal in memory_dir.glob("*.md"):
        content = ""
        with contextlib.suppress(OSError):
            content = journal.read_text(encoding="utf-8")
        if marker in content:
            return True
    return False


def anchor_db_path(db_path: str) -> str:
    home = str(Path.home())
    if db_path.startswith(home + os.sep):
        return "~" + db_path[len(home) :]
    return db_path


def append_capture(
    memory_dir: Path,
    session_id: str,
    turn_id: str,
    db_path: str,
    summary: str,
    captured_at: datetime,
) -> Path:
    """Append the summary under a lazily written session heading, like the other plugins."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    date = captured_at.strftime("%Y-%m-%d")
    now = captured_at.strftime("%H:%M")
    journal = memory_dir / f"{date}.md"
    existing = journal.read_text(encoding="utf-8") if journal.is_file() else ""
    if not existing:
        existing = f"# {date}\n"
    if f"session:{session_id}" not in existing:
        existing = f"{existing.rstrip()}\n\n## Session {now}\n"
    entry = (
        f"\n### {now}\n<!-- session:{session_id} turn:{turn_id} db:{anchor_db_path(db_path)} -->\n{summary.rstrip()}\n"
    )
    journal.write_text(f"{existing.rstrip()}\n{entry}", encoding="utf-8")
    return journal


def run_index(
    command: list[str],
    memory_dir: Path,
    memsearch_dir: Path,
    collection: str,
    description: str,
    cwd: Path,
    env: dict[str, str],
) -> None:
    """Index the journal; skip when another index (session start or a previous turn) is still running."""
    lock_path = memsearch_dir / ".index.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        index_args = [*command, "index", str(memory_dir), "--default-collection", collection]
        if description:
            index_args += ["--description", description]
        run_command(index_args, cwd=cwd, env=env, timeout=600)


def run_maintenance(project_dir: Path, memsearch_dir: Path, env: dict[str, str]) -> None:
    run_command(
        [
            "python3",
            str(PLUGIN_DIR / "scripts" / "maintenance-runner.py"),
            "--platform",
            "zcode",
            "--project-dir",
            str(project_dir),
            "--memsearch-dir",
            str(memsearch_dir),
        ],
        cwd=project_dir,
        env=env,
        timeout=180,
    )


def process(args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return

    project_dir = Path(args.project_dir)
    memsearch_dir = Path(args.memsearch_dir)
    memory_dir = memsearch_dir / "memory"
    command = list(args.memsearch_cmd)
    env = {**os.environ, "MEMSEARCH_DIR": str(memsearch_dir), "MEMSEARCH_NO_WATCH": "1", "MEMSEARCH_DISABLE": "1"}
    memsearch_dir.mkdir(parents=True, exist_ok=True)

    db_path = get_db_path()
    turn: ZCodeTurn | None = None
    if os.path.exists(db_path):
        try:
            turn, is_subagent = wait_for_turn(db_path, session_id, time.monotonic() + DB_WAIT_SECONDS)
        except sqlite3.Error:
            turn, is_subagent = None, False
        if is_subagent:
            return

    turn_id = turn.turn_id if turn else str(payload.get("turnId") or "")
    transcript = turn.render() if turn else fallback_transcript(payload)
    if not turn_id or not transcript:
        return

    lock_path = memsearch_dir / ".zcode-capture.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if capture_exists(memory_dir, session_id, turn_id):
            return
        snapshot = load_config_snapshot(command, args.collection, project_dir, env)
        if str(config_value(snapshot, "plugins.zcode.summarize.enabled", True)).lower() == "false":
            return
        summary = summarize(transcript, snapshot, command, project_dir, env) or plain_extract(turn, payload)
        if not summary:
            return
        append_capture(memory_dir, session_id, turn_id, db_path, summary, datetime.now())

        provider = config_value(snapshot, "embedding.provider", "onnx")
        model = config_value(snapshot, "embedding.model", "") or "default"
        run_index(
            command,
            memory_dir,
            memsearch_dir,
            args.collection,
            f"{project_dir.name} | {provider}/{model}",
            project_dir,
            env,
        )
        run_maintenance(project_dir, memsearch_dir, env)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture one finished ZCode turn into the MemSearch journal.")
    parser.add_argument("payload", help="Stop hook payload JSON written by hooks/stop.sh")
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--memsearch-dir", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("memsearch_cmd", nargs="+", help="memsearch command, given after --")
    args = parser.parse_intermixed_args()

    try:
        process(args)
    finally:
        Path(args.payload).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
