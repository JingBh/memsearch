#!/usr/bin/env bash
# Stop hook: hand the finished turn to a detached capture worker.
#
# ZCode runs every hook inline (`async` has no effect) and its transcript_path is
# a one-line stand-in for the real transcript, so this hook does no parsing or
# summarization itself. It stores the hook payload and spawns
# scripts/capture-turn.py, which reads the finished turn from ZCode's SQLite
# store, summarizes it, appends it to the daily journal, and re-indexes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Stop hooks that requested continuation re-fire with stop_hook_active; the
# original invocation already captured this turn.
STOP_HOOK_ACTIVE=$(_json_val "$INPUT" "stop_hook_active" "false")
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  echo '{}'
  exit 0
fi

SESSION_ID=$(_json_val "$INPUT" "session_id" "")
if [ -z "$SESSION_ID" ] || ! memsearch_available; then
  echo '{}'
  exit 0
fi

PAYLOAD_DIR="$MEMSEARCH_DIR/.zcode-capture"
mkdir -p "$PAYLOAD_DIR"
PAYLOAD_FILE="$PAYLOAD_DIR/${SESSION_ID}-$(date +%s)-$$.json"
printf '%s' "$INPUT" > "$PAYLOAD_FILE"

# Both fds must be redirected and the worker detached, otherwise the hook runner
# keeps the session waiting on the pipe until the worker exits.
launch_prefix="nohup"
command -v setsid &>/dev/null && launch_prefix="setsid"
(
  cd "$_PROJECT_DIR"
  MEMSEARCH_DIR="$MEMSEARCH_DIR" MEMSEARCH_NO_WATCH=1 MEMSEARCH_DISABLE=1 \
    exec $launch_prefix python3 "$SCRIPT_DIR/../scripts/capture-turn.py" "$PAYLOAD_FILE" \
      --project-dir "$_PROJECT_DIR" \
      --memsearch-dir "$MEMSEARCH_DIR" \
      --collection "$COLLECTION_NAME" \
      -- "${MEMSEARCH_CMD[@]}"
) </dev/null >/dev/null 2>&1 &

echo '{}'
