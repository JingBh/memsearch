#!/usr/bin/env python3
"""Render turns from a Pi v3 JSONL session tree."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Turn:
    turn_id: str
    lines: list[str] = field(default_factory=list)

    def render(self, index: int) -> str:
        return f'=== Turn {index} ({self.turn_id}) ===\n' + '\n\n'.join(self.lines)


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ''
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'text' and isinstance(block.get('text'), str):
            text = block['text'].strip()
            if text:
                parts.append(text)
    return '\n'.join(parts)


def render_assistant_content(content: Any) -> list[str]:
    if not isinstance(content, list):
        text = text_content(content)
        return [f'[Pi]: {text}'] if text else []

    lines: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get('type')
        if block_type == 'text' and isinstance(block.get('text'), str):
            text = block['text'].strip()
            if text:
                lines.append(f'[Pi]: {text}')
        elif block_type == 'toolCall':
            name = block.get('name', 'unknown')
            arguments = json.dumps(block.get('arguments', {}), ensure_ascii=False, sort_keys=True)
            lines.append(f'[Pi tool call {name}]: {arguments}')
    return lines


def render_tool_result(message: dict[str, Any]) -> str:
    text = text_content(message.get('content'))
    if len(text) > 4000:
        text = f'{text[:4000]}\n...[tool result truncated]'
    return f'[Tool {message.get("toolName", "unknown")}]: {text}' if text else ''


def load_entries(path: Path) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for raw_line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        entry_id = entry.get('id')
        if isinstance(entry_id, str):
            entries[entry_id] = entry
    return entries


def choose_leaf(entries: dict[str, dict[str, Any]], leaf_id: str) -> str:
    if leaf_id:
        matches = [entry_id for entry_id in entries if entry_id == leaf_id or entry_id.startswith(leaf_id)]
        if matches:
            return matches[0]
        raise ValueError(f'leaf entry not found: {leaf_id}')

    parent_ids = {entry.get('parentId') for entry in entries.values() if entry.get('parentId')}
    leaves = [entry for entry in entries.values() if entry['id'] not in parent_ids]
    if not leaves:
        raise ValueError('session has no leaf entry')
    return max(leaves, key=lambda entry: str(entry.get('timestamp', '')))['id']


def build_branch(entries: dict[str, dict[str, Any]], leaf_id: str) -> list[dict[str, Any]]:
    branch: list[dict[str, Any]] = []
    current_id: str | None = leaf_id
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        entry = entries.get(current_id)
        if entry is None:
            break
        branch.append(entry)
        parent_id = entry.get('parentId')
        current_id = parent_id if isinstance(parent_id, str) else None
    branch.reverse()
    return branch


def build_turns(branch: list[dict[str, Any]]) -> list[Turn]:
    turns: list[Turn] = []
    current: Turn | None = None

    for entry in branch:
        if entry.get('type') != 'message' or not isinstance(entry.get('message'), dict):
            continue
        message = entry['message']
        role = message.get('role')
        if role == 'user':
            text = text_content(message.get('content'))
            if not text:
                continue
            current = Turn(turn_id=entry['id'], lines=[f'[User]: {text}'])
            turns.append(current)
        elif role == 'assistant' and current is not None:
            current.lines.extend(render_assistant_content(message.get('content')))
        elif role == 'toolResult' and current is not None:
            rendered = render_tool_result(message)
            if rendered:
                current.lines.append(rendered)
    return turns


def find_turn(turns: list[Turn], turn_id: str) -> int:
    for index, turn in enumerate(turns):
        if turn.turn_id == turn_id or turn.turn_id.startswith(turn_id) or turn_id.startswith(turn.turn_id):
            return index
    return -1


def main() -> int:
    parser = argparse.ArgumentParser(description='Render a Pi JSONL transcript branch.')
    parser.add_argument('transcript_path')
    parser.add_argument('--turn', default='')
    parser.add_argument('--leaf', default='')
    parser.add_argument('--context', type=int, default=3)
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()

    path = Path(args.transcript_path).expanduser()
    if not path.is_file():
        parser.error(f'transcript not found: {path}')

    entries = load_entries(path)
    try:
        leaf_id = choose_leaf(entries, args.leaf)
        turns = build_turns(build_branch(entries, leaf_id))
    except ValueError as error:
        parser.error(str(error))

    if args.turn:
        index = find_turn(turns, args.turn)
        if index < 0:
            parser.error(f'turn not found on selected branch: {args.turn}')
        start = max(0, index - max(0, args.context))
        end = min(len(turns), index + max(0, args.context) + 1)
        selected = turns[start:end]
        first_index = start + 1
    else:
        selected = turns[-max(1, args.limit):]
        first_index = len(turns) - len(selected) + 1

    output = '\n\n'.join(
        turn.render(first_index + offset)
        for offset, turn in enumerate(selected)
    )
    if output:
        sys.stdout.write(f'{output}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
