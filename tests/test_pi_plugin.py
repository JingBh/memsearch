from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_pi_transcript_parser_selects_anchored_branch(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    entries = [
        {"type": "session", "version": 3, "id": "session-1", "cwd": str(tmp_path)},
        {"type": "message", "id": "u1", "parentId": None, "message": {"role": "user", "content": "Fix auth"}},
        {
            "type": "message",
            "id": "a1",
            "parentId": "u1",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {"type": "toolCall", "name": "bash", "arguments": {"command": "pytest -q"}},
                ],
            },
        },
        {
            "type": "message",
            "id": "t1",
            "parentId": "a1",
            "message": {
                "role": "toolResult",
                "toolName": "bash",
                "content": [{"type": "text", "text": "1 passed"}],
            },
        },
        {
            "type": "message",
            "id": "a2",
            "parentId": "t1",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Auth fixed."}]},
        },
        {
            "type": "message",
            "id": "alt",
            "parentId": "u1",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Wrong branch."}]},
        },
    ]
    transcript.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "plugins" / "pi" / "scripts" / "parse-transcript.py"

    result = subprocess.run(
        ["python3", str(script), str(transcript), "--turn", "u1", "--leaf", "a2", "--context", "0"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "[User]: Fix auth" in result.stdout
    assert '[Pi tool call bash]: {"command": "pytest -q"}' in result.stdout
    assert "[Tool bash]: 1 passed" in result.stdout
    assert "[Pi]: Auth fixed." in result.stdout
    assert "Wrong branch" not in result.stdout


def test_pi_capture_worker_writes_summarized_turn(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    scripts = plugin_dir / "scripts"
    prompts = plugin_dir / "prompts"
    memory_dir = tmp_path / "store" / "memory"
    scripts.mkdir(parents=True)
    prompts.mkdir()
    (prompts / "summarize.txt").write_text("Summarize {{AGENT_NAME}}", encoding="utf-8")
    (scripts / "maintenance-runner.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

    fake_memsearch = tmp_path / "memsearch"
    fake_memsearch.write_text(
        """#!/usr/bin/env python3
import sys
if sys.argv[1:4] == ['config', 'get', 'plugins.pi.summarize.enabled']:
    print('true')
elif sys.argv[1:4] == ['config', 'get', 'plugins.pi.summarize.provider']:
    print('test-provider')
elif sys.argv[1:4] == ['config', 'get', 'plugins.pi.summarize.model']:
    print('test-model')
elif sys.argv[1:4] == ['config', 'get', 'milvus.uri']:
    print('local.db')
elif sys.argv[1] == 'summarize':
    sys.stdin.read()
    print('- Captured by Pi.')
else:
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    fake_memsearch.chmod(0o755)

    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "projectDir": str(tmp_path),
                "memsearchDir": str(tmp_path / "store"),
                "memoryDir": str(memory_dir),
                "collectionName": "ms_test",
                "memsearchCommand": [str(fake_memsearch)],
                "pluginDir": str(plugin_dir),
                "sessionId": "session-1",
                "turnId": "user-1",
                "leafId": "assistant-1",
                "transcriptPath": str(tmp_path / "session.jsonl"),
                "transcript": "[User]: test\n[Pi]: done",
                "userText": "test",
                "assistantText": "done",
                "capturedAt": 1_800_000_000_000,
            }
        ),
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[1] / "plugins" / "pi" / "scripts" / "capture-turn.py"

    subprocess.run(["python3", str(script), str(payload)], check=True)

    journals = list(memory_dir.glob("*.md"))
    assert len(journals) == 1
    content = journals[0].read_text(encoding="utf-8")
    assert "<!-- session:session-1 turn:user-1 leaf:assistant-1 transcript:" in content
    assert "- Captured by Pi." in content
    assert not payload.exists()


def test_pi_resolve_store_uses_centralized_collection(tmp_path: Path) -> None:
    plugin_scripts = Path(__file__).resolve().parents[1] / "plugins" / "pi" / "scripts"
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}

    result = subprocess.run(
        ["bash", str(plugin_scripts / "resolve-store.sh"), str(tmp_path / "repo")],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    assert result.stdout.strip().startswith(f"{tmp_path}/.memsearch/projects/ms_repo_")
