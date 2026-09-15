from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "zcode"
SCRIPT_DIR = PLUGIN_DIR / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from zcode_turns import build_turns, connect_readonly  # noqa: E402


def _create_db(path: Path, *, session_id: str = "sess_test", parent_id: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE session (
            id text primary key, project_id text not null, workspace_id text, parent_id text,
            slug text not null, directory text not null, title text not null, version text not null,
            time_created integer not null, time_updated integer not null
        );
        CREATE TABLE message (
            id text primary key, session_id text not null, time_created integer not null,
            time_updated integer not null, data text not null, sequence integer
        );
        CREATE TABLE part (
            id text primary key, message_id text not null, session_id text not null,
            time_created integer not null, time_updated integer not null, data text not null, sequence integer
        );
        """
    )
    conn.execute(
        "INSERT INTO session VALUES (?, 'proj_test', NULL, ?, ?, '/tmp/project', 'Test', '0.16.5', 1, 1)",
        (session_id, parent_id, session_id),
    )
    conn.commit()
    return conn


def _add_message(
    conn: sqlite3.Connection,
    msg_id: str,
    role: str,
    time_created: int,
    parts: list[dict],
    *,
    session_id: str = "sess_test",
    parent_id: str | None = None,
    finish: str | None = None,
    origin: str = "real_user",
) -> None:
    data: dict = {"role": role, "time": {"created": time_created}}
    if role == "user":
        data["semantics"] = {"origin": origin, "kind": "user_prompt"}
    else:
        data["parentID"] = parent_id
        data["finish"] = finish
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?, ?, NULL)",
        (msg_id, session_id, time_created, time_created, json.dumps(data)),
    )
    for index, part in enumerate(parts):
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (f"{msg_id}_p{index}", msg_id, session_id, time_created + index, time_created + index, json.dumps(part)),
        )
    conn.commit()


def _populate_conversation(conn: sqlite3.Connection) -> None:
    _add_message(conn, "msg_u1", "user", 1000, [{"type": "text", "text": "How do ZCode hooks time out?"}])
    _add_message(
        conn,
        "msg_a1",
        "assistant",
        1001,
        [
            {"type": "step-start"},
            {"type": "reasoning", "text": "Thinking about the runner."},
            {"type": "text", "text": "Let me check the guide."},
            {"type": "tool", "tool": "Read", "state": {"status": "completed", "output": "SECRET TOOL OUTPUT"}},
        ],
        parent_id="msg_u1",
        finish="tool-calls",
    )
    _add_message(
        conn,
        "msg_a2",
        "assistant",
        1002,
        [{"type": "text", "text": "Command hooks use seconds; process hooks use milliseconds."}],
        parent_id="msg_u1",
        finish="stop",
    )
    _add_message(
        conn,
        "msg_reminder",
        "user",
        1003,
        [{"type": "text", "text": "The TodoWrite tool hasn't been used recently.", "synthetic": True}],
        origin="agent_runtime",
    )
    _add_message(conn, "msg_u2", "user", 1004, [{"type": "text", "text": "Thanks, and what about matchers?"}])
    _add_message(
        conn,
        "msg_a3",
        "assistant",
        1005,
        [{"type": "text", "text": "Matchers are case-sensitive regular expressions."}],
        parent_id="msg_u2",
        finish="stop",
    )


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _write_memsearch_stub(path: Path) -> None:
    _write_executable(
        path,
        """#!/usr/bin/env bash
echo "$*" >> "$MEMSEARCH_STUB_LOG"
case "$1" in
  config)
    if [ "$2" = "list" ]; then printf '%s\\n' "$MEMSEARCH_STUB_CONFIG"; fi
    ;;
  summarize)
    cat > "$MEMSEARCH_STUB_SUMMARIZE_INPUT"
    printf -- '- User asked how hooks time out\\n- ZCode explained the two timeout units\\n'
    ;;
esac
exit 0
""",
    )


def _stub_config(provider: str) -> str:
    return json.dumps(
        {
            "embedding": {"provider": "onnx", "model": "bge-m3", "api_key": ""},
            "milvus": {"uri": "~/.memsearch/milvus.db", "collection": "ms_test"},
            "plugins": {"zcode": {"summarize": {"enabled": True, "provider": provider, "model": ""}}},
        }
    )


def _plugin_copy(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    shutil.copytree(PLUGIN_DIR, plugin)
    (plugin / "scripts" / "maintenance-runner.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    return plugin


def _run_worker(tmp_path: Path, payload: dict, *, provider: str = "api-gateway", db: Path | None = None) -> Path:
    plugin = _plugin_copy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    _write_memsearch_stub(fake_bin / "memsearch")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    memsearch_dir = tmp_path / "store"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "ZCODE_DB_PATH": str(db or tmp_path / "missing.sqlite"),
        "MEMSEARCH_ZCODE_DB_WAIT_S": "0",
        "MEMSEARCH_STUB_LOG": str(tmp_path / "stub.log"),
        "MEMSEARCH_STUB_CONFIG": _stub_config(provider),
        "MEMSEARCH_STUB_SUMMARIZE_INPUT": str(tmp_path / "summarize-input.txt"),
    }
    subprocess.run(
        [
            "python3",
            str(plugin / "scripts" / "capture-turn.py"),
            str(payload_path),
            "--project-dir",
            str(tmp_path),
            "--memsearch-dir",
            str(memsearch_dir),
            "--collection",
            "ms_test",
            "--",
            "memsearch",
        ],
        check=True,
        env=env,
        timeout=60,
    )
    assert not payload_path.exists(), "worker must delete its payload file"
    return memsearch_dir / "memory"


def _journals(memory_dir: Path) -> str:
    return (
        "\n".join(p.read_text(encoding="utf-8") for p in sorted(memory_dir.glob("*.md"))) if memory_dir.is_dir() else ""
    )


def test_zcode_turns_group_real_user_turns_and_skip_runtime_noise(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    conn = _create_db(db)
    _populate_conversation(conn)
    conn.close()

    conn = connect_readonly(str(db))
    try:
        turns = build_turns(conn, "sess_test")
    finally:
        conn.close()

    assert [turn.turn_id for turn in turns] == ["msg_u1", "msg_u2"]
    assert all(turn.complete for turn in turns)
    rendered = turns[0].render()
    assert "[User]: How do ZCode hooks time out?" in rendered
    assert "[ZCode]: Let me check the guide." in rendered
    assert "[ZCode]: Command hooks use seconds" in rendered
    assert "SECRET TOOL OUTPUT" not in rendered
    assert "Thinking about the runner" not in rendered
    assert "TodoWrite" not in rendered
    assert turns[0].assistant_message_count == 2


def test_zcode_turn_without_final_stop_is_incomplete(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    conn = _create_db(db)
    _add_message(conn, "msg_u1", "user", 1000, [{"type": "text", "text": "Run the tests"}])
    _add_message(
        conn,
        "msg_a1",
        "assistant",
        1001,
        [{"type": "text", "text": "Running."}],
        parent_id="msg_u1",
        finish="tool-calls",
    )
    conn.close()

    conn = connect_readonly(str(db))
    try:
        turns = build_turns(conn, "sess_test")
    finally:
        conn.close()

    assert len(turns) == 1
    assert turns[0].complete is False


def test_zcode_parse_transcript_renders_target_turn_with_context(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    conn = _create_db(db)
    _populate_conversation(conn)
    conn.close()

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT_DIR / "parse-transcript.py"),
            "sess_test",
            "--turn",
            "msg_u2",
            "--context",
            "0",
            "--db",
            str(db),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "=== Turn 2 (msg_u2) ===" in result.stdout
    assert "[User]: Thanks, and what about matchers?" in result.stdout
    assert "How do ZCode hooks time out" not in result.stdout


def test_zcode_capture_worker_writes_summarized_turn(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    conn = _create_db(db)
    _populate_conversation(conn)
    conn.close()

    memory_dir = _run_worker(tmp_path, {"session_id": "sess_test", "turnId": "turn_ignored"}, db=db)

    journal = _journals(memory_dir)
    assert "## Session " in journal
    assert "<!-- session:sess_test turn:msg_u2 db:" in journal
    assert "- User asked how hooks time out" in journal
    summarize_input = (tmp_path / "summarize-input.txt").read_text(encoding="utf-8")
    assert "[User]: Thanks, and what about matchers?" in summarize_input
    assert "[ZCode]: Matchers are case-sensitive" in summarize_input
    log = (tmp_path / "stub.log").read_text(encoding="utf-8")
    assert "summarize --plugin zcode --agent-name ZCode" in log
    assert f"index {memory_dir} --default-collection ms_test" in log


def test_zcode_capture_worker_uses_plain_extract_without_provider(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    conn = _create_db(db)
    _populate_conversation(conn)
    conn.close()

    memory_dir = _run_worker(tmp_path, {"session_id": "sess_test"}, provider="", db=db)

    journal = _journals(memory_dir)
    assert "- User asked: Thanks, and what about matchers?" in journal
    assert "- ZCode: Matchers are case-sensitive regular expressions." in journal
    assert "summarize" not in (tmp_path / "stub.log").read_text(encoding="utf-8")


def test_zcode_capture_worker_skips_subagent_sessions(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    conn = _create_db(db, session_id="sess_child", parent_id="sess_parent")
    _add_message(conn, "msg_u1", "user", 1000, [{"type": "text", "text": "Subagent task"}], session_id="sess_child")
    _add_message(
        conn,
        "msg_a1",
        "assistant",
        1001,
        [{"type": "text", "text": "Done."}],
        session_id="sess_child",
        parent_id="msg_u1",
        finish="stop",
    )
    conn.close()

    memory_dir = _run_worker(tmp_path, {"session_id": "sess_child"}, db=db)

    assert _journals(memory_dir) == ""
    assert not (tmp_path / "stub.log").exists()


def test_zcode_capture_worker_dedupes_existing_anchor(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    conn = _create_db(db)
    _populate_conversation(conn)
    conn.close()
    memory_dir = tmp_path / "store" / "memory"
    memory_dir.mkdir(parents=True)
    existing = "# 2026-09-15\n\n## Session 10:00\n\n### 10:00\n<!-- session:sess_test turn:msg_u2 db:~/x.sqlite -->\n- Already captured\n"
    (memory_dir / "2026-09-15.md").write_text(existing, encoding="utf-8")

    _run_worker(tmp_path, {"session_id": "sess_test"}, db=db)

    assert (memory_dir / "2026-09-15.md").read_text(encoding="utf-8") == existing
    assert not (tmp_path / "stub.log").exists()


def test_zcode_capture_worker_falls_back_to_payload_without_database(tmp_path: Path) -> None:
    payload = {
        "session_id": "sess_orphan",
        "turnId": "turn_42",
        "last_assistant_message": "The final answer from the payload.",
    }

    memory_dir = _run_worker(tmp_path, payload, provider="")

    journal = _journals(memory_dir)
    assert "<!-- session:sess_orphan turn:turn_42 db:" in journal
    assert "- ZCode: The final answer from the payload." in journal


def _hook_env(tmp_path: Path, *, config_json: str = "") -> dict[str, str]:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    project = tmp_path / "project"
    home.mkdir(exist_ok=True)
    fake_bin.mkdir(exist_ok=True)
    project.mkdir(exist_ok=True)
    (home / ".memsearch").mkdir(exist_ok=True)
    (home / ".memsearch" / "config.toml").write_text("", encoding="utf-8")
    _write_executable(
        fake_bin / "memsearch",
        f"""#!/usr/bin/env bash
if [ "$1" = "config" ] && [ "$2" = "list" ]; then
  echo '{config_json}'
fi
exit 0
""",
    )
    return {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "ZCODE_PROJECT_DIR": str(project),
        "MEMSEARCH_DIR": str(tmp_path / ".memsearch"),
        "MEMSEARCH_NO_WATCH": "1",
    }


def test_zcode_stop_hook_spawns_worker_and_returns_immediately(tmp_path: Path) -> None:
    plugin = _plugin_copy(tmp_path)
    marker = tmp_path / "worker-args.json"
    _write_executable(
        plugin / "scripts" / "capture-turn.py",
        f"""#!/usr/bin/env python3
import json, sys
json.dump(sys.argv[1:], open({str(marker)!r}, "w"))
""",
    )
    env = _hook_env(tmp_path)
    payload = {"session_id": "sess_hook", "stop_hook_active": False, "last_assistant_message": "done"}

    started = time.monotonic()
    result = subprocess.run(
        ["bash", str(plugin / "hooks" / "stop.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    assert result.stdout.strip() == "{}"
    assert time.monotonic() - started < 10
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.1)
    args = json.loads(marker.read_text(encoding="utf-8"))
    payload_file = Path(args[0])
    assert payload_file.parent == tmp_path / ".memsearch" / ".zcode-capture"
    assert json.loads(payload_file.read_text(encoding="utf-8")) == payload
    assert args[args.index("--project-dir") + 1] == str(tmp_path / "project")
    assert args[args.index("--memsearch-dir") + 1] == str(tmp_path / ".memsearch")
    assert args[args.index("--collection") + 1].startswith("ms_")
    assert args[args.index("--") + 1 :] == ["memsearch"]


def test_zcode_stop_hook_skips_when_stop_hook_active(tmp_path: Path) -> None:
    env = _hook_env(tmp_path)

    result = subprocess.run(
        ["bash", str(PLUGIN_DIR / "hooks" / "stop.sh")],
        input=json.dumps({"session_id": "sess_hook", "stop_hook_active": True}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    assert result.stdout.strip() == "{}"
    assert not (tmp_path / ".memsearch" / ".zcode-capture").exists()


def test_zcode_session_start_injects_status_and_recent_memory(tmp_path: Path) -> None:
    memory = tmp_path / ".memsearch" / "memory"
    memory.mkdir(parents=True)
    (memory / "2026-09-14.md").write_text(
        "# 2026-09-14\n\n## Session 10:00\n\n### 10:00\n- User configured ZCode hooks.\n",
        encoding="utf-8",
    )
    env = _hook_env(
        tmp_path,
        config_json='{"embedding":{"provider":"onnx","model":"bge-m3","api_key":""},"milvus":{"uri":"http://localhost:19530","collection":"ms_test"}}',
    )

    result = subprocess.run(
        ["bash", str(PLUGIN_DIR / "hooks" / "session-start.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert "systemMessage" not in payload
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("[memsearch")
    assert "embedding: onnx/bge-m3" in context
    assert "\n\n# Recent Memory\n\n## 2026-09-14.md\n" in context
    assert "- User configured ZCode hooks." in context
    assert "\\n" not in context


def test_zcode_user_prompt_submit_returns_hint_as_additional_context(tmp_path: Path) -> None:
    env = _hook_env(tmp_path)
    script = PLUGIN_DIR / "hooks" / "user-prompt-submit.sh"

    hinted = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"prompt": "What did we decide about the rollout last week?"}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    short = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"prompt": "hi"}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    assert json.loads(hinted.stdout) == {"additionalContext": "[memsearch] Recall available if needed"}
    assert json.loads(short.stdout) == {}
