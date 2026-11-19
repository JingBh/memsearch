#!/usr/bin/env python3
"""Render Antigravity conversation turns from a transcript.jsonl file.

Usage:
  parse-transcript.py <transcript_path> [--limit N] [--turn TURN_ID] [--context N]

Options:
  --limit N        Max number of turns to return when no turn is targeted (default: 20)
  --turn TURN_ID   Return the target turn plus surrounding context turns (e.g. step_0)
  --context N      Number of turns before/after the target turn (default: 3)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from antigravity_turns import build_turns, find_turn_index, resolve_transcript_path


def _emit(text: str = "", *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(text)
    if not text.endswith("\n"):
        stream.write("\n")


def parse_transcript(transcript_path: str, limit: int, turn_id: str | None, context: int) -> None:
    resolved = resolve_transcript_path(transcript_path, prefer_full=True)
    if not resolved.is_file():
        _emit(f"Error: Antigravity transcript not found at {resolved}", err=True)
        sys.exit(1)

    turns = build_turns(str(resolved), prefer_full=True)
    if not turns:
        _emit(f"No turns found in {resolved}")
        return

    if turn_id:
        target_idx = find_turn_index(turns, turn_id)
        if target_idx < 0:
            _emit(f"Turn not found: {turn_id}", err=True)
            sys.exit(1)
        selected = turns[max(0, target_idx - context) : min(len(turns), target_idx + context + 1)]
    elif limit and limit > 0:
        selected = turns[-limit:]
    else:
        selected = turns

    for turn in selected:
        _emit(turn.render())
        _emit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse an Antigravity transcript.jsonl file")
    parser.add_argument("transcript_path", help="Path to transcript.jsonl or transcript_full.jsonl")
    parser.add_argument("--limit", type=int, default=20, help="Max turns to return")
    parser.add_argument("--turn", default=None, help="Target turn ID (e.g. step_0)")
    parser.add_argument("--context", type=int, default=3, help="Turns before/after target")
    args = parser.parse_args()

    parse_transcript(
        os.path.expanduser(args.transcript_path),
        limit=args.limit,
        turn_id=args.turn,
        context=args.context,
    )


if __name__ == "__main__":
    main()
