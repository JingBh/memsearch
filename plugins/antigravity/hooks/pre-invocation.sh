#!/usr/bin/env bash
# PreInvocation hook: capability hint reminding Antigravity about memory-recall.
# Fires before every model call, so deduplicate per (conversationId, lastUserInput)
# to inject the hint once per user turn.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

PROMPT=$(_json_val "$INPUT" "lastUserInput" "")
if [ -z "$PROMPT" ] || [ "${#PROMPT}" -lt 10 ]; then
  echo '{}'
  exit 0
fi

if ! memsearch_available; then
  echo '{}'
  exit 0
fi

CONV_ID=$(_json_val "$INPUT" "conversationId" "")
if [ -n "$CONV_ID" ]; then
  HINT_DIR="$MEMSEARCH_DIR/.antigravity-hint"
  mkdir -p "$HINT_DIR" 2>/dev/null || true
  PROMPT_HASH=$(printf '%s' "$PROMPT" | shasum -a 256 2>/dev/null | cut -c1-16 || printf '%s' "${#PROMPT}")
  MARKER="$HINT_DIR/${CONV_ID}.last"
  PREV=""
  [ -f "$MARKER" ] && PREV=$(cat "$MARKER" 2>/dev/null || true)
  if [ "$PREV" = "$PROMPT_HASH" ]; then
    echo '{}'
    exit 0
  fi
  printf '%s' "$PROMPT_HASH" > "$MARKER" 2>/dev/null || true
fi

echo '{"injectSteps": [{"ephemeralMessage": "[memsearch] Recall available if needed"}]}'
