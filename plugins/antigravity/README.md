# memsearch — Antigravity Plugin

Automatic persistent memory for Google Antigravity (App, CLI, and IDE). Every conversation turn is summarized and indexed — your next session picks up where you left off.

## Prerequisites

- Google Antigravity (App, CLI, or IDE)
- Python 3.10+
- `memsearch` installed (`uv tool install "memsearch[onnx]"` or from checkout)
- API keys stored in `~/.memsearch/.env` if using `env:VAR_NAME` references in `~/.memsearch/config.toml`

## Install

Link `plugins/antigravity` into Antigravity's global plugins directory:

```bash
ln -s "$PWD/plugins/antigravity" ~/.gemini/config/plugins/memsearch
```

## What happens automatically

| When | What |
|------|------|
| `SessionStart` | Recent memory context and `[memsearch v...]` status line are injected via `injectSteps.systemMessage` |
| `PreInvocation` | Once per user prompt (≥ 10 chars), `[memsearch] Recall available if needed` is injected as an `ephemeralMessage` |
| `Stop` | A detached worker reads the finished turn from `transcript.jsonl`, summarizes it, appends it to the daily journal, and re-indexes |

Subagent conversations (rows with a non-empty `parent_conversation_id` in `conversation_summaries.db`) are skipped automatically.
