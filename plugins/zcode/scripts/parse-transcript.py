#!/usr/bin/env python3
"""Render ZCode conversation turns for a session from ZCode's SQLite store.

Usage:
  parse-transcript.py <session_id> [--limit N] [--turn TURN_ID] [--context N] [--db PATH]

Options:
  --limit N        Max number of turns to return when no turn is targeted (default: 20)
  --turn TURN_ID   Return the target turn plus surrounding context turns
  --context N      Number of turns before/after the target turn (default: 3)
  --db PATH        ZCode database (default: $ZCODE_DB_PATH or ~/.zcode/cli/db/db.sqlite)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zcode_turns import build_turns, connect_readonly, find_turn_index, get_db_path


def _emit(text: str = "", *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(text)
    if not text.endswith("\n"):
        stream.write("\n")


def parse_session(db_path: str, session_id: str, limit: int, turn_id: str | None, context: int) -> None:
    if not os.path.exists(db_path):
        _emit(f"Error: ZCode database not found at {db_path}", err=True)
        sys.exit(1)

    conn = connect_readonly(db_path)
    try:
        turns = build_turns(conn, session_id)
    finally:
        conn.close()

    if not turns:
        _emit(f"No messages found for session {session_id}")
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
    parser = argparse.ArgumentParser(description="Parse a ZCode session transcript")
    parser.add_argument("session_id", help="Session ID to parse")
    parser.add_argument("--limit", type=int, default=20, help="Max turns to return")
    parser.add_argument("--turn", default=None, help="Target turn ID (prefix match)")
    parser.add_argument("--context", type=int, default=3, help="Turns before/after target")
    parser.add_argument("--db", default=None, help="ZCode SQLite database path")
    args = parser.parse_args()

    parse_session(
        os.path.expanduser(args.db) if args.db else get_db_path(),
        args.session_id,
        limit=args.limit,
        turn_id=args.turn,
        context=args.context,
    )


if __name__ == "__main__":
    main()
