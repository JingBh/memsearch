# Antigravity platform reference

## Plugin version & update

The Antigravity plugin manifest is `plugins/antigravity/plugin.json` (`version`).
It is loaded directly from `~/.gemini/config/plugins/memsearch` (typically
symlinked to the checkout's `plugins/antigravity/` directory) or registered via
`~/.gemini/config/plugins.json`.

Antigravity transcripts live under `<app_data_dir>/brain/<conversationId>/.system_generated/logs/transcript.jsonl`
(and sibling `transcript_full.jsonl`), while captured MemSearch memory lives as
markdown under the central project store.

## Plugin keys

```toml
[plugins.antigravity.summarize]
enabled = true
provider = ""      # empty/native = plain user/assistant extract (no native summarizer)
model = ""

[plugins.antigravity.project_review]
enabled = false
provider = "native"
model = ""
min_interval_hours = 24
input_dir = ".memsearch/memory"
output_file = ".memsearch/PROJECT.md"

[plugins.antigravity.user_profile]
enabled = false
provider = "native"
model = ""
min_interval_hours = 24
input_dir = ".memsearch/memory"
output_file = ".memsearch/USER.md"

[plugins.antigravity.memory_to_skill]
enabled = false
min_occurrences = 3   # how many times a workflow must recur before it is distilled
paths = []            # where installed skills are copied; empty = ask the user
```

## Native model defaults

- Antigravity runs hooks inside its own desktop/CLI/IDE host, so there is no
  standalone native summarizer path. With `provider = ""` or `native`, capture
  stores a plain "User asked / Antigravity" extract of the turn instead of an
  LLM summary.
- Maintenance tasks (`project_review`, `user_profile`, `memory_to_skill`)
  require a memsearch-managed provider: define `[llm.providers.<name>]` and set
  `plugins.antigravity.<task>.provider` to that name.
- API keys referenced with `env:VAR_NAME` are resolved from `~/.memsearch/.env`
  automatically when unset in the host environment.

## Restart guidance

Antigravity rereads plugin skills and hooks on the next turn/session. TOML
changes apply on the next capture/recall/index/maintenance invocation without
restarting.
