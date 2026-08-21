#!/usr/bin/env python3
"""Capture one settled Pi turn into the centralized MemSearch journal."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
    timeout: int = 30,
) -> str:
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=str(cwd),
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def memsearch_value(command: list[str], key: str, cwd: Path, env: dict[str, str]) -> str:
    return run_command([*command, 'config', 'get', key], cwd=cwd, env=env, timeout=5)


def load_prompt(plugin_dir: Path) -> str:
    prompt_file = plugin_dir / 'prompts' / 'summarize.txt'
    if prompt_file.is_file():
        return prompt_file.read_text(encoding='utf-8').replace('{{AGENT_NAME}}', 'Pi')
    return (
        'You are a third-person note-taker. Summarize one conversation turn as 2-10 factual '
        'bullet points. Use the same primary language as the user. Do not answer the user. '
        'Output only bullet points.'
    )


def summarize(payload: dict[str, Any], env: dict[str, str]) -> str:
    command = [str(part) for part in payload['memsearchCommand']]
    cwd = Path(payload['projectDir'])
    if memsearch_value(command, 'plugins.pi.summarize.enabled', cwd, env).lower() == 'false':
        return ''

    transcript = str(payload['transcript'])[:8000]
    provider = memsearch_value(command, 'plugins.pi.summarize.provider', cwd, env)
    model = memsearch_value(command, 'plugins.pi.summarize.model', cwd, env)

    if provider and provider != 'native':
        result = run_command(
            [*command, 'summarize', '--plugin', 'pi', '--agent-name', 'Pi'],
            cwd=cwd,
            env=env,
            input_text=transcript,
        )
        if result:
            return result
    else:
        prompt = f'{load_prompt(Path(payload["pluginDir"]))}\n\nTranscript:\n{transcript}'
        pi_command = [
            'pi',
            '--no-session',
            '--no-extensions',
            '--no-skills',
            '--no-prompt-templates',
            '--no-themes',
            '--no-context-files',
            '--no-tools',
            '--thinking',
            'low',
        ]
        if model:
            pi_command.extend(['--model', model])
        pi_command.extend(['--print', prompt])
        result = run_command(pi_command, cwd=cwd, env=env)
        if result:
            return result

    user_text = ' '.join(str(payload['userText']).split())[:500]
    assistant_text = ' '.join(str(payload['assistantText']).split())[:1200]
    return f'- User asked: {user_text}\n- Pi: {assistant_text}'


def capture_exists(memory_dir: Path, session_id: str, turn_id: str) -> bool:
    marker = f'<!-- session:{session_id} turn:{turn_id} '
    for journal in memory_dir.glob('*.md'):
        content = ''
        with contextlib.suppress(OSError):
            content = journal.read_text(encoding='utf-8')
        if marker in content:
            return True
    return False


def append_capture(payload: dict[str, Any], summary: str) -> Path:
    memory_dir = Path(payload['memoryDir'])
    memory_dir.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.fromtimestamp(int(payload['capturedAt']) / 1000)
    date = captured_at.strftime('%Y-%m-%d')
    time = captured_at.strftime('%H:%M')
    journal = memory_dir / f'{date}.md'
    existing = journal.read_text(encoding='utf-8') if journal.is_file() else ''

    session_id = str(payload['sessionId'])
    if not existing:
        existing = f'# {date}\n'
    if f'<!-- session:{session_id} ' not in existing:
        existing = f'{existing.rstrip()}\n\n## Session {time}\n'

    anchor_parts = [
        f'session:{session_id}',
        f'turn:{payload["turnId"]}',
        f'leaf:{payload["leafId"]}',
    ]
    transcript_path = str(payload.get('transcriptPath') or '')
    if transcript_path:
        anchor_parts.append(f'transcript:{transcript_path}')
    entry = f'\n### {time}\n<!-- {" ".join(anchor_parts)} -->\n{summary.rstrip()}\n'
    journal.write_text(f'{existing.rstrip()}\n{entry}', encoding='utf-8')
    return journal


def run_maintenance(payload: dict[str, Any], env: dict[str, str]) -> None:
    runner = Path(payload['pluginDir']) / 'scripts' / 'maintenance-runner.py'
    run_command(
        [
            'python3',
            str(runner),
            '--platform',
            'pi',
            '--project-dir',
            str(payload['projectDir']),
            '--memsearch-dir',
            str(payload['memsearchDir']),
        ],
        cwd=Path(payload['projectDir']),
        env=env,
        timeout=180,
    )


def process(payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding='utf-8'))
    memsearch_dir = Path(payload['memsearchDir'])
    memory_dir = Path(payload['memoryDir'])
    memsearch_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        'MEMSEARCH_DIR': str(memsearch_dir),
        'MEMSEARCH_DISABLE': '1',
        'MEMSEARCH_NO_WATCH': '1',
        'PI_SKIP_VERSION_CHECK': '1',
    }

    lock_path = memsearch_dir / '.pi-capture.lock'
    with lock_path.open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        session_id = str(payload['sessionId'])
        turn_id = str(payload['turnId'])
        if capture_exists(memory_dir, session_id, turn_id):
            return
        summary = summarize(payload, env)
        if not summary:
            return
        append_capture(payload, summary)

        command = [str(part) for part in payload['memsearchCommand']]
        uri = memsearch_value(command, 'milvus.uri', Path(payload['projectDir']), env)
        if uri.startswith(('http', 'tcp')):
            run_command(
                [
                    *command,
                    'index',
                    str(memory_dir),
                    '--collection',
                    str(payload['collectionName']),
                ],
                cwd=Path(payload['projectDir']),
                env=env,
                timeout=120,
            )
        run_maintenance(payload, env)


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write('usage: capture-turn.py <payload.json>\n')
        return 2
    payload_path = Path(sys.argv[1])
    try:
        process(payload_path)
    finally:
        payload_path.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
