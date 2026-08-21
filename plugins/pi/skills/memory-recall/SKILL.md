---
name: memory-recall
description: Search and recall relevant memories from past sessions via MemSearch. Use when the user's question could benefit from historical context, past decisions, debugging notes, previous conversations, or project knowledge, especially questions such as "what did I decide about X", "why did we do Y", or "have I seen this before". Skip when the question is purely about current code state, ephemeral, or the user asks to ignore memory.
---

# Memory Recall

Search past project memories and return only context that is genuinely useful to the current request.

## Workflow

1. Call `memory_search` with a concrete query and `topK` between 3 and 5.
2. Discard irrelevant or generic matches.
3. Call `memory_get` for each promising chunk hash to recover the full markdown section.
4. If an expanded memory contains a Pi anchor such as:

   ```html
   <!-- session:ID turn:ID leaf:ID transcript:/path/to/session.jsonl -->
   ```

   and the exact exchange matters, call `memory_transcript` with its `transcriptPath`, `turnId`, and `leafId`.
5. Return a concise, curated summary organized by relevance. Include the memory date or source file for traceability.

If nothing relevant is found, say `No relevant memories found.` Do not pad the response with unrelated history.
