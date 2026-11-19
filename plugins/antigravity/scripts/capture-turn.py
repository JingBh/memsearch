#!/usr/bin/env python3
"""Capture one finished Antigravity turn into the MemSearch journal.

Spawned detached by hooks/stop.sh with the Stop hook payload. Reads the finished
turn from Antigravity's transcript.jsonl, summarizes it, appends it to the daily
journal, re-indexes, and wakes maintenance.

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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from antigravity_turns import AntigravityTurn, build_turns, is_subagent

PLUGIN_DIR = Path(__file__).resolve().parent.parent
AGENT_NAME = "Antigravity"
TRANSCRIPT_WAIT_SECONDS = float(os.environ.get("MEMSEARCH_ANTIGRAVITY_TRANSCRIPT_WAIT_S", "5"))
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


def wait_for_turn(transcript_path: str, deadline: float) -> AntigravityTurn | None:
    """Poll until the latest turn has a completed assistant response."""
    while True:
        turns = build_turns(transcript_path)
        turn = turns[-1] if turns else None
        if turn is not None and turn.complete and turn.assistant_parts:
            return turn
        if time.monotonic() >= deadline:
            return turn
        time.sleep(0.5)


def plain_extract(turn: AntigravityTurn) -> str:
    """Bullet extract used when no LLM summarizer is configured."""
    user_text = " ".join(turn.user_text().split())[:500]
    assistant_text = " ".join(turn.assistant_text().split())[:1200]
    lines: list[str] = []
    if user_text:
        lines.append(f"- User asked: {user_text}")
    if assistant_text:
        lines.append(f"- {AGENT_NAME}: {assistant_text}")
    return "\n".join(lines)


def summarize(transcript: str, snapshot: dict[str, Any], command: list[str], cwd: Path, env: dict[str, str]) -> str:
    """Summarize through a memsearch-managed provider."""
    provider = str(config_value(snapshot, "plugins.antigravity.summarize.provider"))
    if not provider or provider == "native":
        return ""
    result = run_command(
        [*command, "summarize", "--plugin", "antigravity", "--agent-name", AGENT_NAME],
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


def anchor_path(path_str: str) -> str:
    home = str(Path.home())
    if path_str.startswith(home + os.sep):
        return "~" + path_str[len(home) :]
    return path_str


def append_capture(
    memory_dir: Path,
    session_id: str,
    turn_id: str,
    transcript_path: str,
    summary: str,
    captured_at: datetime,
) -> Path:
    """Append the summary under a lazily written session heading."""
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
        f"\n### {now}\n<!-- session:{session_id} turn:{turn_id} transcript:{anchor_path(transcript_path)} -->\n"
        f"{summary.rstrip()}\n"
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
    """Index the journal; skip when another index is still running."""
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
            "antigravity",
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
    conversation_id = str(payload.get("conversationId") or "")
    transcript_path = str(payload.get("transcriptPath") or "")
    if not conversation_id or not transcript_path:
        return
    if is_subagent(conversation_id, transcript_path):
        return

    project_dir = Path(args.project_dir)
    memsearch_dir = Path(args.memsearch_dir)
    memory_dir = memsearch_dir / "memory"
    command = list(args.memsearch_cmd)
    env = {**os.environ, "MEMSEARCH_DIR": str(memsearch_dir), "MEMSEARCH_NO_WATCH": "1", "MEMSEARCH_DISABLE": "1"}
    memsearch_dir.mkdir(parents=True, exist_ok=True)

    turn = wait_for_turn(transcript_path, time.monotonic() + TRANSCRIPT_WAIT_SECONDS)
    if turn is None:
        return
    transcript = turn.render()
    if not transcript or not (turn.assistant_parts or turn.tool_lines):
        return

    lock_path = memsearch_dir / ".antigravity-capture.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if capture_exists(memory_dir, conversation_id, turn.turn_id):
            return
        snapshot = load_config_snapshot(command, args.collection, project_dir, env)
        if str(config_value(snapshot, "plugins.antigravity.summarize.enabled", True)).lower() == "false":
            return
        summary = summarize(transcript, snapshot, command, project_dir, env) or plain_extract(turn)
        if not summary:
            return
        append_capture(memory_dir, conversation_id, turn.turn_id, transcript_path, summary, datetime.now())

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
    parser = argparse.ArgumentParser(description="Capture one finished Antigravity turn into the MemSearch journal.")
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
