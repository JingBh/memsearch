from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "plugins" / "opencode" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

CAPTURE_DAEMON_PATH = SCRIPT_DIR / "capture-daemon.py"
CAPTURE_DAEMON_SPEC = importlib.util.spec_from_file_location(
    "capture_daemon",
    CAPTURE_DAEMON_PATH,
)
assert CAPTURE_DAEMON_SPEC is not None
assert CAPTURE_DAEMON_SPEC.loader is not None
capture_daemon = importlib.util.module_from_spec(CAPTURE_DAEMON_SPEC)
CAPTURE_DAEMON_SPEC.loader.exec_module(capture_daemon)

from opencode_turns import (  # noqa: E402
    OpenCodeMessage,
    OpenCodeTurn,
    TurnState,
    build_turns,
    get_turn_db_path,
    load_session_turn_rows,
    load_turn_state,
    open_turn_db,
    save_turn,
    save_turn_state,
)


def _write_utf8_memsearch_stub(path: Path) -> None:
    path.write_text(
        """\
import os
import sys


def write(stream, value):
    stream.buffer.write(value.encode("utf-8"))
    stream.buffer.flush()


args = sys.argv[1:]
mode = os.environ.get("MEMSEARCH_STUB_MODE", "success")

write(sys.stderr, "诊断丁: UTF-8 stderr\\n")
if mode == "config-nonzero" and args[:2] == ["config", "get"]:
    raise SystemExit(7)

if args[:2] == ["config", "get"]:
    values = {
        "plugins.opencode.summarize.model": "模型/摘要",
        "plugins.opencode.summarize.provider": "custom-provider",
        "plugins.opencode.summarize.enabled": "true",
        "prompts.summarize": os.environ["MEMSEARCH_STUB_PROMPT_PATH"],
    }
    write(sys.stdout, values[args[2]] + "\\n")
elif args and args[0] == "summarize":
    sys.stdin.buffer.read()
    if mode == "summarize-nonzero":
        raise SystemExit(7)
    write(sys.stdout, "- 摘要包含非 ASCII\\n")
else:
    raise SystemExit(2)
""",
        encoding="utf-8",
    )


def _utf8_stub_command(stub: Path) -> str:
    return f'"{sys.executable}" "{stub}"'


def _record_subprocess_results_with_cp1252_default(monkeypatch, subprocess_module):
    real_run = subprocess.run
    completed = []

    def run(*args, **kwargs):
        if kwargs.get("text") and "encoding" not in kwargs:
            kwargs["encoding"] = "cp1252"
            kwargs["errors"] = "strict"
        result = real_run(*args, **kwargs)
        completed.append(result)
        return result

    monkeypatch.setattr(subprocess_module, "run", run)
    return completed


def test_memsearch_adapter_decodes_utf8_when_locale_is_cp1252(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stub = tmp_path / "memsearch-stub.py"
    prompt_dir = tmp_path / "自定义提示"
    prompt_dir.mkdir()
    prompt = prompt_dir / "摘要提示.txt"
    prompt.write_text("为 {{AGENT_NAME}} 生成摘要", encoding="utf-8")
    _write_utf8_memsearch_stub(stub)

    monkeypatch.setenv("MEMSEARCH_STUB_PROMPT_PATH", str(prompt))
    command = _utf8_stub_command(stub)
    argv = [sys.executable, str(stub), "config", "get", "plugins.opencode.summarize.model"]

    with pytest.raises(UnicodeDecodeError):
        subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="cp1252",
            errors="strict",
            check=False,
        )

    completed = _record_subprocess_results_with_cp1252_default(monkeypatch, capture_daemon.subprocess)

    assert capture_daemon.get_plugin_summarize_model(command) == "模型/摘要"
    assert capture_daemon.get_plugin_summarize_provider(command) == "custom-provider"
    assert capture_daemon.get_plugin_summarize_enabled(command) is True
    assert capture_daemon._load_summarize_prompt("OpenCode", command) == "为 OpenCode 生成摘要"
    assert capture_daemon.summarize_with_llm("用户: 请总结", "", command) == "- 摘要包含非 ASCII"
    assert completed
    assert all(result.stderr == "诊断丁: UTF-8 stderr\n" for result in completed)


def test_memsearch_adapter_preserves_nonzero_fallbacks(tmp_path: Path, monkeypatch) -> None:
    stub = tmp_path / "memsearch-stub.py"
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("unused", encoding="utf-8")
    _write_utf8_memsearch_stub(stub)

    monkeypatch.setenv("MEMSEARCH_STUB_MODE", "config-nonzero")
    monkeypatch.setenv("MEMSEARCH_STUB_PROMPT_PATH", str(prompt))
    command = _utf8_stub_command(stub)

    assert capture_daemon.get_plugin_summarize_model(command) == ""
    assert capture_daemon.get_plugin_summarize_provider(command) == ""
    assert capture_daemon.get_plugin_summarize_enabled(command) is True

    monkeypatch.setenv("MEMSEARCH_STUB_MODE", "summarize-nonzero")
    assert capture_daemon.summarize_with_llm("turn", "", command) is None


def test_memsearch_adapter_preserves_timeout_fallbacks(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(capture_daemon.subprocess, "run", timeout)

    assert capture_daemon.get_plugin_summarize_model("memsearch") == ""
    assert capture_daemon.get_plugin_summarize_provider("memsearch") == ""
    assert capture_daemon.get_plugin_summarize_enabled("memsearch") is True
    assert capture_daemon.summarize_with_llm("turn", "", "memsearch") is None


def test_opencode_config_resolution_uses_jsonc_and_env_dir_precedence(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    xdg_config = tmp_path / "xdg-config"
    project = tmp_path / "repo"
    config_dir = tmp_path / "custom-opencode"
    home.mkdir()
    project.mkdir()
    (xdg_config / "opencode").mkdir(parents=True)
    config_dir.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    monkeypatch.delenv("OPENCODE_DISABLE_PROJECT_CONFIG", raising=False)
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config_dir))

    (xdg_config / "opencode" / "opencode.json").write_text(
        '{"small_model": "global-json", "username": "global"}',
        encoding="utf-8",
    )
    (xdg_config / "opencode" / "opencode.jsonc").write_text(
        '{\n  // jsonc should override json in the same directory\n  "small_model": "global-jsonc",\n}\n',
        encoding="utf-8",
    )
    (project / "opencode.json").write_text(
        '{"small_model": "project-json", "username": "project-json"}',
        encoding="utf-8",
    )
    (project / "opencode.jsonc").write_text(
        '{\n  "small_model": "project-jsonc",\n  "provider": {"openai": {"apiKey": "{file:token.txt}"}},\n}\n',
        encoding="utf-8",
    )
    (config_dir / "opencode.jsonc").write_text(
        '{"small_model": "config-dir", "plugin": ["memsearch-opencode"]}',
        encoding="utf-8",
    )

    cfg = capture_daemon.load_opencode_config(project)

    assert capture_daemon.get_small_model(project) == "config-dir"
    assert cfg["small_model"] == "config-dir"
    assert cfg["username"] == "project-json"
    assert cfg["provider"]["openai"]["apiKey"] == f"{{file:{project / 'token.txt'}}}"


def test_opencode_config_resolution_uses_opencode_config_file(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    xdg_config = tmp_path / "xdg-config"
    project = tmp_path / "repo"
    custom_config = tmp_path / "custom-opencode.jsonc"
    home.mkdir()
    project.mkdir()
    (xdg_config / "opencode").mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("OPENCODE_CONFIG", str(custom_config))
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    monkeypatch.delenv("OPENCODE_DISABLE_PROJECT_CONFIG", raising=False)

    (xdg_config / "opencode" / "opencode.jsonc").write_text(
        '{"small_model": "global-jsonc"}',
        encoding="utf-8",
    )
    custom_config.write_text(
        '{\n  // exact env config overrides global config\n  "small_model": "env-config",\n}\n',
        encoding="utf-8",
    )

    assert capture_daemon.get_small_model(project) == "env-config"


def test_opencode_isolated_config_sanitizes_plugins(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    project = tmp_path / "repo"
    config_dir = tmp_path / "custom-opencode"
    home.mkdir()
    project.mkdir()
    config_dir.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    monkeypatch.delenv("OPENCODE_DISABLE_PROJECT_CONFIG", raising=False)
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config_dir))

    (project / "opencode.jsonc").write_text(
        '{"model": "project/model", "plugin": ["project-plugin"]}',
        encoding="utf-8",
    )
    (config_dir / "opencode.jsonc").write_text(
        '{"small_model": "dir/model", "plugin": ["dir-plugin"]}',
        encoding="utf-8",
    )

    isolated_root = Path(capture_daemon.ensure_isolated_config(project))
    isolated_config = json.loads((isolated_root / "opencode" / "opencode.json").read_text(encoding="utf-8"))

    assert isolated_root == home / ".codex" / "tmp" / "opencode-memsearch-summarize"
    assert isolated_config["model"] == "project/model"
    assert isolated_config["small_model"] == "dir/model"
    assert "plugin" not in isolated_config
    assert not (isolated_root / "opencode" / "opencode.jsonc").exists()


def test_opencode_daemon_wake_maintenance_disables_hooks(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["stdin"] = kwargs["stdin"]
        captured["stdout"] = kwargs["stdout"]
        captured["stderr"] = kwargs["stderr"]
        captured["start_new_session"] = kwargs["start_new_session"]

        class Process:
            pass

        return Process()

    monkeypatch.setattr(capture_daemon.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("MEMSEARCH_NO_WATCH", "0")
    monkeypatch.delenv("MEMSEARCH_DISABLE", raising=False)

    capture_daemon.wake_maintenance(str(project))

    assert captured["cmd"][:4] == ["python3", str(SCRIPT_DIR / "maintenance-runner.py"), "--platform", "opencode"]
    assert captured["cmd"][captured["cmd"].index("--project-dir") + 1] == str(project)
    assert captured["cmd"][captured["cmd"].index("--memsearch-dir") + 1] == str(project / ".memsearch")
    assert captured["env"]["MEMSEARCH_NO_WATCH"] == "1"
    assert captured["env"]["MEMSEARCH_DISABLE"] == "1"
    assert captured["stdin"] == capture_daemon.subprocess.DEVNULL
    assert captured["stdout"] == capture_daemon.subprocess.DEVNULL
    assert captured["stderr"] == capture_daemon.subprocess.DEVNULL
    assert captured["start_new_session"] is True


def test_opencode_plugin_capture_daemon_uses_spawned_child_lifecycle() -> None:
    source = (SCRIPT_DIR.parent / "index.ts").read_text(encoding="utf-8")

    assert 'exec(\n    `python3 "${daemonScript}"' not in source
    assert "spawn(" in source
    assert '"--parent-pid"' in source
    assert "String(process.pid)" in source
    assert "writeFileSync(pidFile, String(child.pid)" in source


def test_opencode_capture_daemon_parent_process_liveness(monkeypatch) -> None:
    def alive(pid: int, signal_number: int) -> None:
        assert pid == 123
        assert signal_number == 0

    monkeypatch.setattr(capture_daemon.os, "kill", alive)
    assert capture_daemon.process_is_alive(123) is True

    def missing(pid: int, signal_number: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(capture_daemon.os, "kill", missing)
    assert capture_daemon.process_is_alive(123) is False

    def inaccessible(pid: int, signal_number: int) -> None:
        raise PermissionError

    monkeypatch.setattr(capture_daemon.os, "kill", inaccessible)
    assert capture_daemon.process_is_alive(123) is True


def _make_opencode_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _insert_message(
    conn: sqlite3.Connection,
    message_id: str,
    session_id: str,
    time_created: int,
    role: str,
    parent_id: str | None = None,
    finish: str = "stop",
    text: str = "",
    tool_output: str | None = None,
    tool_error: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "role": role,
        "time": {"created": time_created},
        "finish": finish,
    }
    if parent_id:
        payload["parentID"] = parent_id
    conn.execute(
        """
        INSERT INTO message (id, session_id, time_created, time_updated, data)
        VALUES (?, ?, ?, ?, ?)
        """,
        (message_id, session_id, time_created, time_created, json.dumps(payload)),
    )

    part_id = f"{message_id}-p1"
    if text:
        conn.execute(
            """
            INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                part_id,
                message_id,
                session_id,
                time_created,
                time_created,
                json.dumps({"type": "text", "text": text}),
            ),
        )

    if tool_output is not None:
        conn.execute(
            """
            INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"{message_id}-tool",
                message_id,
                session_id,
                time_created,
                time_created,
                json.dumps(
                    {
                        "type": "tool",
                        "tool": "Bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "ls"},
                            "output": tool_output,
                        },
                    }
                ),
            ),
        )

    if tool_error is not None:
        conn.execute(
            """
            INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"{message_id}-tool-error",
                message_id,
                session_id,
                time_created,
                time_created,
                json.dumps(
                    {
                        "type": "tool",
                        "tool": "Bash",
                        "state": {
                            "status": "error",
                            "error": tool_error,
                        },
                    }
                ),
            ),
        )


def test_build_turns_groups_multi_message_assistant_turns(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_test"

    _insert_message(conn, "u1", session_id, 100, "user", text="Plan the change")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="tool-calls", text="Checking")
    _insert_message(
        conn,
        "a2",
        session_id,
        120,
        "assistant",
        parent_id="u1",
        finish="stop",
        text="Done",
        tool_output="output.txt",
    )
    _insert_message(conn, "u2", session_id, 130, "user", text="Thanks")
    _insert_message(conn, "a3", session_id, 140, "assistant", parent_id="u2", finish="stop", text="You're welcome")
    conn.commit()

    turns = build_turns(conn, session_id)

    assert len(turns) == 2
    assert turns[0].turn_id == "u1"
    assert turns[0].assistant_message_count == 2
    assert turns[0].complete is True
    assert "Plan the change" in turns[0].render()
    assert "[Tool:" not in turns[0].render()
    assert turns[1].turn_id == "u2"
    assert turns[1].complete is True

    conn.close()


def test_build_turns_omits_successful_tool_output_content(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_tool_output"

    _insert_message(conn, "u1", session_id, 100, "user", text="Check the journal")
    _insert_message(
        conn,
        "a1",
        session_id,
        110,
        "assistant",
        parent_id="u1",
        finish="stop",
        text="Checking",
        tool_output="stale fact from old journal: memsearch 0.4.4",
    )
    conn.commit()

    rendered = build_turns(conn, session_id)[0].render()

    assert "[Tool:" not in rendered
    assert "output_chars" not in rendered
    assert "stale fact" not in rendered
    assert "0.4.4" not in rendered

    conn.close()


def test_build_turns_omits_tool_error_content(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_tool_error"

    _insert_message(conn, "u1", session_id, 100, "user", text="Debug the failure")
    _insert_message(
        conn,
        "a1",
        session_id,
        110,
        "assistant",
        parent_id="u1",
        finish="stop",
        text="Debugging",
        tool_error="prefix secret failure payload final error marker",
    )
    conn.commit()

    rendered = build_turns(conn, session_id)[0].render()

    assert "[Tool:" not in rendered
    assert "error_chars" not in rendered
    assert "final error marker" not in rendered
    assert "prefix secret" not in rendered

    conn.close()


def test_opencode_turn_render_keeps_tail_of_long_messages() -> None:
    turn = OpenCodeTurn(
        session_id="ses_tail",
        turn_id="u1",
        turn_index=1,
        start_time=100,
        end_time=110,
        first_message_id="u1",
        last_message_id="a1",
        message_count=2,
        assistant_message_count=1,
        complete=True,
        messages=[
            OpenCodeMessage("u1", "user", None, 100, "stop", "Question"),
            OpenCodeMessage("a1", "assistant", "u1", 110, "stop", "prefix " + ("x" * 200) + " final marker"),
        ],
    )

    rendered = turn.render(max_chars=40)

    assert "...(truncated to tail)" in rendered
    assert "final marker" in rendered
    assert "prefix " not in rendered


def test_build_turns_keeps_assistant_descendants_in_same_turn(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_descendants"

    _insert_message(conn, "u1", session_id, 100, "user", text="Investigate the failure")
    _insert_message(
        conn,
        "a1",
        session_id,
        110,
        "assistant",
        parent_id="u1",
        finish="tool-calls",
        text="Running checks",
    )
    _insert_message(
        conn,
        "a2",
        session_id,
        120,
        "assistant",
        parent_id="a1",
        finish="stop",
        text="Found the root cause",
    )
    _insert_message(conn, "u2", session_id, 130, "user", text="Apply the fix")
    conn.commit()

    turns = build_turns(conn, session_id)

    assert len(turns) == 2
    assert turns[0].turn_id == "u1"
    assert turns[0].assistant_message_count == 2
    assert turns[0].complete is True
    assert [message.id for message in turns[0].messages] == ["u1", "a1", "a2"]

    conn.close()


def test_build_turns_keeps_descendants_through_textless_assistant_nodes(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_textless_descendants"

    _insert_message(conn, "u1", session_id, 100, "user", text="Investigate the failure")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="tool-calls")
    _insert_message(
        conn,
        "a2",
        session_id,
        120,
        "assistant",
        parent_id="a1",
        finish="stop",
        text="Found the root cause",
    )
    _insert_message(conn, "u2", session_id, 130, "user", text="Apply the fix")
    conn.commit()

    turns = build_turns(conn, session_id)

    assert len(turns) == 2
    assert turns[0].turn_id == "u1"
    assert turns[0].assistant_message_count == 1
    assert turns[0].complete is True
    assert [message.id for message in turns[0].messages] == ["u1", "a2"]
    assert "Found the root cause" in turns[0].render()

    conn.close()


def test_turn_sidecar_persists_state_and_turns(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    turn_db = open_turn_db(str(project_dir))
    turn = OpenCodeTurn(
        session_id="ses_1",
        turn_id="u1",
        turn_index=1,
        start_time=100,
        end_time=150,
        first_message_id="u1",
        last_message_id="a1",
        message_count=2,
        assistant_message_count=1,
        complete=True,
        messages=[],
    )

    save_turn(turn_db, turn)
    save_turn_state(
        turn_db,
        TurnState(
            session_id="ses_1",
            last_completed_time=150,
            last_completed_message_id="a1",
            last_completed_turn_id="u1",
        ),
    )

    rows = load_session_turn_rows(turn_db, "ses_1")
    state = load_turn_state(turn_db, "ses_1")

    assert get_turn_db_path(str(project_dir)).endswith(".memsearch/opencode-turns.db")
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "u1"
    assert state.last_completed_turn_id == "u1"
    assert state.last_completed_message_id == "a1"

    turn_db.close()


def test_capture_session_turns_keeps_monotonic_turn_index_across_batches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_capture"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"

    monkeypatch.setattr(capture_daemon, "summarize_with_llm", lambda *args, **kwargs: None)
    current_time_ms = {"value": 0}
    monkeypatch.setattr(capture_daemon.time, "time", lambda: current_time_ms["value"] / 1000)

    _insert_message(conn, "u1", session_id, 100, "user", text="Question one")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="stop", text="Answer one")
    conn.commit()

    turn_db = open_turn_db(str(project_dir))
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
    )

    assert load_session_turn_rows(turn_db, session_id) == []

    _insert_message(conn, "u2", session_id, 200, "user", text="Question two")
    _insert_message(conn, "a2", session_id, 210, "assistant", parent_id="u2", finish="stop", text="Answer two")
    conn.commit()

    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
    )

    rows = load_session_turn_rows(turn_db, session_id)
    assert [(row["turn_id"], row["turn_index"]) for row in rows] == [("u1", 1)]

    _insert_message(conn, "u3", session_id, 300, "user", text="Question three")
    conn.commit()

    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
    )

    rows = load_session_turn_rows(turn_db, session_id)

    assert [(row["turn_id"], row["turn_index"]) for row in rows] == [("u1", 1), ("u2", 2)]

    turn_db.close()
    conn.close()


def _prepare_summary_capture(
    tmp_path: Path,
    session_id: str,
    marker: str,
    assistant_text: str = "Answer",
) -> tuple[sqlite3.Connection, sqlite3.Connection, Path, Path, Path]:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"

    _insert_message(conn, "u1", session_id, 100, "user", text=marker)
    _insert_message(
        conn,
        "a1",
        session_id,
        110,
        "assistant",
        parent_id="u1",
        finish="stop",
        text=assistant_text,
    )
    _insert_message(conn, "u2", session_id, 200, "user", text="Close the first turn")
    conn.commit()
    return conn, open_turn_db(str(project_dir)), db_path, project_dir, memory_dir


def _run_summary_capture(
    conn: sqlite3.Connection,
    turn_db: sqlite3.Connection,
    db_path: Path,
    memory_dir: Path,
    session_id: str,
) -> str:
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
    )
    return next(memory_dir.glob("*.md")).read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["missing", "failure", "nonzero", "empty", "unusable", "timeout"])
def test_capture_session_turns_omits_transcript_when_native_summary_fails(
    tmp_path: Path,
    monkeypatch,
    mode: str,
) -> None:
    session_id = "ses_summary_failure"
    marker = "S651_SYNTHETIC_PRIVATE_TURN_MARKER"
    conn, turn_db, db_path, _, memory_dir = _prepare_summary_capture(tmp_path, session_id, marker)
    isolated_root = tmp_path / "isolated"

    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_enabled", lambda *args: True)
    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_provider", lambda *args: "")
    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_model", lambda *args: "")
    monkeypatch.setattr(capture_daemon, "_load_summarize_prompt", lambda *args: "Summarize safely.")
    monkeypatch.setattr(capture_daemon, "ensure_isolated_config", lambda *args: str(isolated_root))

    def fail_native(cmd, **kwargs):
        if mode == "missing":
            raise FileNotFoundError("opencode")
        if mode == "failure":
            raise RuntimeError("synthetic native failure")
        if mode == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if mode == "nonzero":
            return subprocess.CompletedProcess(cmd, 23, stdout=f"- {marker}\n", stderr="synthetic failure")
        if mode == "unusable":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{marker}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(capture_daemon.subprocess, "run", fail_native)

    content = _run_summary_capture(conn, turn_db, db_path, memory_dir, session_id)
    assert marker not in content
    assert "Memory summary unavailable" in content
    assert "transcript content was omitted" in content
    assert f"<!-- session:{session_id} turn:u1 db:{db_path} -->" in content
    assert load_turn_state(turn_db, session_id).last_completed_turn_id == "u1"

    turn_db.close()
    conn.close()


def test_capture_session_turns_omits_transcript_when_prompt_loading_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_id = "ses_prompt_exception"
    marker = "S651_SYNTHETIC_PROMPT_EXCEPTION_MARKER"
    conn, turn_db, db_path, _, memory_dir = _prepare_summary_capture(
        tmp_path,
        session_id,
        marker,
        "Synthetic answer",
    )

    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_enabled", lambda *args: True)

    def raise_prompt_decode_error(*args):
        raise UnicodeError("synthetic unreadable prompt")

    monkeypatch.setattr(capture_daemon, "_load_summarize_prompt", raise_prompt_decode_error)

    content = _run_summary_capture(conn, turn_db, db_path, memory_dir, session_id)
    assert marker not in content
    assert "Synthetic answer" not in content
    assert capture_daemon._SUMMARY_UNAVAILABLE in content
    assert f"<!-- session:{session_id} turn:u1 db:{db_path} -->" in content
    assert load_turn_state(turn_db, session_id).last_completed_turn_id == "u1"

    turn_db.close()
    conn.close()


@pytest.mark.parametrize("mode", ["exception", "nonzero", "empty", "timeout"])
def test_capture_session_turns_omits_transcript_when_managed_summary_fails(
    tmp_path: Path,
    monkeypatch,
    mode: str,
) -> None:
    session_id = "ses_managed_summary_failure"
    marker = "S651_SYNTHETIC_MANAGED_PRIVATE_MARKER"
    conn, turn_db, db_path, _, memory_dir = _prepare_summary_capture(tmp_path, session_id, marker)

    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_enabled", lambda *args: True)
    monkeypatch.setattr(capture_daemon, "_load_summarize_prompt", lambda *args: "Summarize safely.")
    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_provider", lambda *args: "openai")

    def fail_managed(cmd, **kwargs):
        if mode == "exception":
            raise RuntimeError("synthetic managed failure")
        if mode == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if mode == "nonzero":
            return subprocess.CompletedProcess(cmd, 23, stdout=f"- {marker}\n", stderr="synthetic failure")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(capture_daemon.subprocess, "run", fail_managed)

    content = _run_summary_capture(conn, turn_db, db_path, memory_dir, session_id)
    assert marker not in content
    assert capture_daemon._SUMMARY_UNAVAILABLE in content
    assert f"<!-- session:{session_id} turn:u1 db:{db_path} -->" in content
    assert load_turn_state(turn_db, session_id).last_completed_turn_id == "u1"

    turn_db.close()
    conn.close()


def test_capture_session_turns_accepts_managed_provider_plain_text(tmp_path: Path, monkeypatch) -> None:
    session_id = "ses_managed_plain_text"
    marker = "S651_SYNTHETIC_MANAGED_PLAIN_TEXT_MARKER"
    plain_summary = "Managed provider plain-text summary."
    conn, turn_db, db_path, _, memory_dir = _prepare_summary_capture(tmp_path, session_id, marker)

    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_enabled", lambda *args: True)
    monkeypatch.setattr(capture_daemon, "_load_summarize_prompt", lambda *args: "Summarize safely.")
    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_provider", lambda *args: "openai")
    monkeypatch.setattr(
        capture_daemon.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout=f"{plain_summary}\n", stderr=""),
    )

    content = _run_summary_capture(conn, turn_db, db_path, memory_dir, session_id)
    assert plain_summary in content
    assert marker not in content
    assert "Memory summary unavailable" not in content
    assert f"<!-- session:{session_id} turn:u1 db:{db_path} -->" in content
    assert load_turn_state(turn_db, session_id).last_completed_turn_id == "u1"

    turn_db.close()
    conn.close()


def test_capture_session_turns_keeps_normal_native_summary_behavior(tmp_path: Path, monkeypatch) -> None:
    session_id = "ses_summary_success"
    marker = "S651_SYNTHETIC_NORMAL_USER_MARKER"
    conn, turn_db, db_path, _, memory_dir = _prepare_summary_capture(tmp_path, session_id, marker)
    isolated_root = tmp_path / "isolated"

    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_enabled", lambda *args: True)
    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_provider", lambda *args: "")
    monkeypatch.setattr(capture_daemon, "get_plugin_summarize_model", lambda *args: "")
    monkeypatch.setattr(capture_daemon, "_load_summarize_prompt", lambda *args: "Summarize safely.")
    monkeypatch.setattr(capture_daemon, "ensure_isolated_config", lambda *args: str(isolated_root))

    captured_env = {}

    def summarize_native(cmd, **kwargs):
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout="- Safe synthetic summary.\n", stderr="")

    monkeypatch.setattr(capture_daemon.subprocess, "run", summarize_native)

    content = _run_summary_capture(conn, turn_db, db_path, memory_dir, session_id)
    assert "- Safe synthetic summary." in content
    assert marker not in content
    assert "Memory summary unavailable" not in content
    assert f"<!-- session:{session_id} turn:u1 db:{db_path} -->" in content
    assert captured_env["XDG_DATA_HOME"] == str(isolated_root / "data")
    assert captured_env["MEMSEARCH_NO_WATCH"] == "1"
    assert load_turn_state(turn_db, session_id).last_completed_turn_id == "u1"

    turn_db.close()
    conn.close()


def test_capture_session_turns_is_idempotent_after_partial_state_save_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_crash"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"

    _insert_message(conn, "u1", session_id, 100, "user", text="Question")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="stop", text="Answer")
    _insert_message(conn, "u2", session_id, 200, "user", text="Follow-up")
    conn.commit()

    monkeypatch.setattr(capture_daemon, "summarize_with_llm", lambda *args, **kwargs: None)

    original_save_turn_state = capture_daemon.save_turn_state
    failed_once = {"value": False}

    def flaky_save_turn_state(*args, **kwargs):
        if not failed_once["value"]:
            failed_once["value"] = True
            raise RuntimeError("simulated crash before cursor persisted")
        return original_save_turn_state(*args, **kwargs)

    turn_db = open_turn_db(str(project_dir))
    monkeypatch.setattr(capture_daemon, "save_turn_state", flaky_save_turn_state)

    with suppress(RuntimeError):
        capture_daemon.capture_session_turns(
            conn,
            turn_db,
            str(memory_dir),
            session_id,
            "",
            "memsearch",
            str(db_path),
        )

    monkeypatch.setattr(capture_daemon, "save_turn_state", original_save_turn_state)
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
    )

    files = sorted(memory_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert content.count(f"<!-- session:{session_id} turn:u1 db:{db_path} -->") == 1

    state = load_turn_state(turn_db, session_id)
    assert state.last_completed_turn_id == "u1"

    turn_db.close()
    conn.close()


def test_capture_session_turns_waits_for_tail_turn_stability_before_capture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_tail_wait"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"
    tail_cache: dict[str, object] = {}

    monkeypatch.setattr(capture_daemon, "summarize_with_llm", lambda *args, **kwargs: None)
    current_time_ms = {"value": 0}
    monkeypatch.setattr(capture_daemon.time, "time", lambda: current_time_ms["value"] / 1000)

    _insert_message(conn, "u1", session_id, 100, "user", text="Question")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="stop", text="Answer")
    conn.commit()

    turn_db = open_turn_db(str(project_dir))
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )
    assert load_session_turn_rows(turn_db, session_id) == []

    current_time_ms["value"] = 299_999
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )
    assert load_session_turn_rows(turn_db, session_id) == []

    current_time_ms["value"] = 300_000
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )

    rows = load_session_turn_rows(turn_db, session_id)
    assert [(row["turn_id"], row["turn_index"]) for row in rows] == [("u1", 1)]

    turn_db.close()
    conn.close()


def test_capture_session_turns_resets_tail_stability_when_content_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_tail_reset"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"
    tail_cache: dict[str, object] = {}

    monkeypatch.setattr(capture_daemon, "summarize_with_llm", lambda *args, **kwargs: None)
    current_time_ms = {"value": 0}
    monkeypatch.setattr(capture_daemon.time, "time", lambda: current_time_ms["value"] / 1000)

    _insert_message(conn, "u1", session_id, 100, "user", text="Question")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="stop", text="Draft answer")
    conn.commit()

    turn_db = open_turn_db(str(project_dir))
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )
    assert load_session_turn_rows(turn_db, session_id) == []

    _insert_message(conn, "a2", session_id, 120, "assistant", parent_id="a1", finish="stop", text="Final answer")
    conn.commit()

    current_time_ms["value"] = 250_000
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )
    assert load_session_turn_rows(turn_db, session_id) == []

    current_time_ms["value"] = 549_999
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )
    assert load_session_turn_rows(turn_db, session_id) == []

    current_time_ms["value"] = 550_000
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )

    rows = load_session_turn_rows(turn_db, session_id)
    assert [(row["turn_id"], row["turn_index"]) for row in rows] == [("u1", 1)]

    turn_db.close()
    conn.close()


def test_capture_session_turns_closes_prior_turn_as_soon_as_next_user_arrives(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_next_user"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"
    tail_cache: dict[str, object] = {}

    monkeypatch.setattr(capture_daemon, "summarize_with_llm", lambda *args, **kwargs: None)
    current_time_ms = {"value": 0}
    monkeypatch.setattr(capture_daemon.time, "time", lambda: current_time_ms["value"] / 1000)

    _insert_message(conn, "u1", session_id, 100, "user", text="Question one")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="stop", text="Answer one")
    conn.commit()

    turn_db = open_turn_db(str(project_dir))
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )
    assert load_session_turn_rows(turn_db, session_id) == []

    _insert_message(conn, "u2", session_id, 200, "user", text="Question two")
    conn.commit()

    current_time_ms["value"] = 1_000
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
        tail_cache,
    )

    rows = load_session_turn_rows(turn_db, session_id)
    assert [(row["turn_id"], row["turn_index"]) for row in rows] == [("u1", 1)]

    turn_db.close()
    conn.close()


def test_capture_session_turns_uses_legacy_last_msg_time_before_sidecar_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_upgrade"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"
    memory_dir.mkdir(parents=True)
    legacy_state_file = project_dir / ".memsearch" / ".last_msg_time"
    legacy_state_file.write_text("150", encoding="utf-8")

    today = capture_daemon.datetime.now().strftime("%Y-%m-%d")
    legacy_memory = memory_dir / f"{today}.md"
    legacy_memory.write_text(
        f"# {today}\n\n"
        "## Session 00:00\n\n"
        "### 00:00\n"
        f"<!-- session:{session_id} db:{db_path} -->\n"
        "- legacy summary for turn u1\n\n",
        encoding="utf-8",
    )

    _insert_message(conn, "u1", session_id, 100, "user", text="Question one")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="stop", text="Answer one")
    _insert_message(conn, "u2", session_id, 200, "user", text="Question two")
    _insert_message(conn, "a2", session_id, 210, "assistant", parent_id="u2", finish="stop", text="Answer two")
    _insert_message(conn, "u3", session_id, 300, "user", text="Question three")
    conn.commit()

    monkeypatch.setattr(capture_daemon, "summarize_with_llm", lambda *args, **kwargs: None)

    turn_db = open_turn_db(str(project_dir))
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
    )

    rows = load_session_turn_rows(turn_db, session_id)
    assert [row["turn_id"] for row in rows] == ["u2"]

    content = legacy_memory.read_text(encoding="utf-8")
    assert f"<!-- session:{session_id} db:{db_path} -->" in content
    assert f"<!-- session:{session_id} turn:u1 db:{db_path} -->" not in content
    assert f"<!-- session:{session_id} turn:u2 db:{db_path} -->" in content
    assert "Question two" not in content
    assert "Memory summary unavailable" in content

    state = load_turn_state(turn_db, session_id)
    assert state.last_completed_turn_id == "u2"
    assert state.last_completed_time == 210

    turn_db.close()
    conn.close()


def test_capture_session_turns_does_not_advance_sidecar_when_markdown_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_write_fail"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"

    _insert_message(conn, "u1", session_id, 100, "user", text="Question")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="stop", text="Answer")
    conn.commit()

    monkeypatch.setattr(capture_daemon, "summarize_with_llm", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        capture_daemon,
        "write_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    turn_db = open_turn_db(str(project_dir))

    with suppress(OSError):
        capture_daemon.capture_session_turns(
            conn,
            turn_db,
            str(memory_dir),
            session_id,
            "",
            "memsearch",
            str(db_path),
        )

    state = load_turn_state(turn_db, session_id)
    rows = load_session_turn_rows(turn_db, session_id)

    assert state.last_completed_turn_id == ""
    assert state.last_completed_message_id == ""
    assert state.last_completed_time == 0
    assert rows == []

    turn_db.close()
    conn.close()


def test_capture_session_turns_skips_summarizer_when_anchor_already_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    summarize_calls = {"count": 0}

    def tracking_summarize(*args, **kwargs):
        summarize_calls["count"] += 1
        return "- summarized"

    monkeypatch.setattr(capture_daemon, "summarize_with_llm", tracking_summarize)

    db_path = tmp_path / "opencode.db"
    conn = _make_opencode_db(db_path)
    session_id = "ses_rebuild"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = project_dir / ".memsearch" / "memory"
    memory_dir.mkdir(parents=True)

    _insert_message(conn, "u1", session_id, 100, "user", text="Question")
    _insert_message(conn, "a1", session_id, 110, "assistant", parent_id="u1", finish="stop", text="Answer")
    _insert_message(conn, "u2", session_id, 200, "user", text="Follow-up")
    conn.commit()

    turn_db = open_turn_db(str(project_dir))
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
    )

    assert summarize_calls["count"] == 1
    content = next(memory_dir.glob("*.md")).read_text(encoding="utf-8")
    assert f"<!-- session:{session_id} turn:u1 db:{db_path} -->" in content

    turn_db.close()

    turn_db = open_turn_db(str(project_dir))
    capture_daemon.capture_session_turns(
        conn,
        turn_db,
        str(memory_dir),
        session_id,
        "",
        "memsearch",
        str(db_path),
    )

    assert summarize_calls["count"] == 1

    content = next(memory_dir.glob("*.md")).read_text(encoding="utf-8")
    assert content.count(f"<!-- session:{session_id} turn:u1 db:{db_path} -->") == 1

    turn_db.close()
    conn.close()
