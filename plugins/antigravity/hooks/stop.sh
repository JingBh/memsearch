#!/usr/bin/env bash
# Stop hook: hand the finished turn to a detached capture worker.
#
# Antigravity runs hooks synchronously, so this hook writes the payload to disk
# and spawns scripts/capture-turn.py detached, then immediately returns {}.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CONV_ID=$(_json_val "$INPUT" "conversationId" "")
if [ -z "$CONV_ID" ] || ! memsearch_available; then
  echo '{}'
  exit 0
fi

REASON=$(_json_val "$INPUT" "terminationReason" "")
if [ "$REASON" = "ERROR" ] || [ "$REASON" = "USER_CANCELED" ]; then
  echo '{}'
  exit 0
fi

PAYLOAD_DIR="$MEMSEARCH_DIR/.antigravity-capture"
mkdir -p "$PAYLOAD_DIR"
PAYLOAD_FILE="$PAYLOAD_DIR/${CONV_ID}-$(date +%s)-$$.json"
printf '%s' "$INPUT" > "$PAYLOAD_FILE"

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
