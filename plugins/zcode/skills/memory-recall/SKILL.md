---
name: memory-recall
description: "Search and recall relevant memories from past sessions via memsearch. Use when the user's question could benefit from historical context, past decisions, debugging notes, previous conversations, or project knowledge -- especially questions like 'what did I decide about X', 'why did we do Y', or 'have I seen this before'. Also use when you see `[memsearch] Recall available if needed` capability hints injected via SessionStart or UserPromptSubmit. Typical flow: search for 3-5 chunks, expand the most relevant, optionally deep-drill into original transcripts via the anchor format. Skip when the question is purely about current code state (use Read/Grep), ephemeral (today's task only), or the user has explicitly asked to ignore memory."
---

You are performing memory retrieval for memsearch. Search past memories and return the most relevant context to the current conversation.

## Plugin Root

The plugin's helper scripts live in ZCode's plugin cache. Resolve the root once:
```
PLUGIN_ROOT="${ZCODE_PLUGIN_ROOT:-$(python3 -c 'import json,os;print(next(p["installPath"] for p in json.load(open(os.path.expanduser("~/.zcode/cli/plugins/installed_plugins.json")))["plugins"] if p["name"]=="memsearch-zcode"))')}"
```

## Project Collection

Determine the collection name by running:
```
bash -c 'if [ -n "${MEMSEARCH_DIR:-}" ]; then bash "$PLUGIN_ROOT/scripts/derive-collection.sh" "$MEMSEARCH_DIR"; else root=$(git rev-parse --show-toplevel 2>/dev/null || true); if [ -n "$root" ]; then bash "$PLUGIN_ROOT/scripts/derive-collection.sh" "$root"; else bash "$PLUGIN_ROOT/scripts/derive-collection.sh"; fi; fi'
```

Memory markdown for this project lives in `${MEMSEARCH_DIR:-$HOME/.memsearch/projects/<collection name>}/memory/`.

## Steps

1. **Search**: Run `memsearch search "<query>" --top-k 5 --json-output --default-collection <collection name from above>` to find relevant chunks.
   - If `memsearch` is not found, try `uvx memsearch` instead.
   - Choose a search query that captures the core intent of the user's question.

2. **Evaluate**: Look at the search results. Skip chunks that are clearly irrelevant or too generic.

3. **Expand**: For each relevant result, run `memsearch expand <chunk_hash> --default-collection <collection name from above>` to get the full markdown section with surrounding context.
   - **Fallback** (if expand fails with a lock error because another process holds Milvus Lite): read the source file directly. The search results include `source` (file path) and `start_line`/`end_line`.

4. **Deep drill (optional)**: If an expanded chunk contains transcript anchors (HTML comments with session info), and the original conversation seems critical:
   - If the anchor contains `turn:`, run `python3 "$PLUGIN_ROOT/scripts/parse-transcript.py" <session_id> --turn <turn_id> --context 3` to retrieve the original conversation around that turn from ZCode's SQLite database.
   - If the anchor only contains `db:` / `session:` with no turn cursor, run `python3 "$PLUGIN_ROOT/scripts/parse-transcript.py" <session_id> --limit 10` to retrieve the most recent turns.
   - If the anchor format is unfamiliar (e.g. `transcript:` or `rollout:` instead of `db:`), the memory was written by another agent's plugin: run `memsearch transcript <path>` on the referenced file, or read it directly to locate the conversation by the identifiers in the anchor.

5. **Return results**: Output a curated summary of the most relevant memories. Be concise — only include information that is genuinely useful for the user's current question.

## When unsure what to search

If the user's question is vague or you can't form a concrete search query, explore the raw markdown first — it is the source of truth for memory:

- `MDIR="${MEMSEARCH_DIR:-$HOME/.memsearch/projects/<collection name>}"; ls -t "$MDIR/memory/" | head -10` — recent daily logs
- `MDIR="${MEMSEARCH_DIR:-$HOME/.memsearch/projects/<collection name>}"; grep -h "^## " "$MDIR/memory/"*.md | sort -u | tail -40` — session headings across all days
- `MDIR="${MEMSEARCH_DIR:-$HOME/.memsearch/projects/<collection name>}"; cat "$MDIR/memory/<YYYY-MM-DD>.md"` — read a specific day

Once a concrete topic jumps out, go back to `memsearch search` with a specific query.

## Output Format

Organize by relevance. For each memory include:
- The key information (decisions, patterns, solutions, context)
- Source reference (file name, date) for traceability

If nothing relevant is found, simply say "No relevant memories found."
