# memsearch — ZCode Plugin

Automatic persistent memory for [ZCode](https://z.ai/). Every conversation turn is summarized and indexed — your next session picks up where you left off.

## Prerequisites

- ZCode desktop app with plugin support
- Python 3.10+
- memsearch installed: `uv tool install "memsearch[onnx]"`

## Install

ZCode installs plugins from marketplaces. Add the memsearch repository as a marketplace and install `memsearch-zcode`:

1. **Settings → Plugin Management → Discover → `+`**, then add either the GitHub repository `zilliztech/memsearch` or a local checkout directory.
2. Install **memsearch-zcode** from the new marketplace.
3. Start a new ZCode session. The status line `[memsearch v...]` appears in the injected context once the hooks run.

ZCode copies the plugin into `~/.zcode/cli/plugins/cache/`; after pulling a newer checkout, update the plugin from **Settings → Plugin Management**.

## What happens automatically

| When | What |
|------|------|
| Session starts | Recent memory context is injected together with a `[memsearch v...]` status line |
| Each prompt | A `[memsearch] Recall available if needed` capability hint reminds ZCode that memory-recall is available |
| Each turn ends | A detached worker reads the finished turn from ZCode's SQLite store, summarizes it, and appends it to a daily `.md` file |

ZCode runs hooks inline and hands them a one-line transcript, so the Stop hook only records its payload and spawns `scripts/capture-turn.py`. The worker waits for ZCode to commit the turn to `~/.zcode/cli/db/db.sqlite`, groups the real user prompt with the assistant replies, and skips subagent sessions.

## Summarization

ZCode's bundled CLI only runs inside the desktop app's environment, so the plugin has no native summarizer. Route summaries through a memsearch-managed provider:

```toml
[llm.providers.openai]
type = "openai"
api_key = "env:OPENAI_API_KEY"

[plugins.zcode.summarize]
enabled = true
provider = "openai"
model = "gpt-5-mini"
```

With `provider` left empty, each turn is stored as a plain `User asked / ZCode` extract instead of an LLM summary. Maintenance tasks (`project_review`, `user_profile`, `memory_to_skill`) also require a memsearch-managed provider.

## Search past memories

Use the `memory-recall` skill (`memsearch-zcode:memory-recall`):

```
$memory-recall what did we decide about the deployment rollout?
```

The skill searches the project collection, expands relevant chunks, and can drill into the original ZCode conversation with `scripts/parse-transcript.py`, which reads ZCode's SQLite database by session and turn id.

## Memory storage

Memory lives in the centralized project store `~/.memsearch/projects/<collection>/memory/YYYY-MM-DD.md`, keyed by the same per-project collection name the other memsearch plugins derive, so ZCode shares memory with Claude Code, Codex, and Pi sessions on the same repository (including linked worktrees). Set `MEMSEARCH_DIR` to use one explicit store instead.

Each entry carries an anchor for progressive disclosure:

```markdown
### 14:30
<!-- session:sess_abc123 turn:msg_def456 db:~/.zcode/cli/db/db.sqlite -->
- User asked how the hook runner handles timeouts
- ZCode explained that command hooks use seconds and process hooks use milliseconds
```

## Plugin files

```
plugins/zcode/
├── .zcode-plugin/plugin.json     # Plugin manifest
├── hooks/
│   ├── hooks.json                # SessionStart / UserPromptSubmit / Stop
│   ├── common.sh                 # Shared setup (mirrors the Claude Code plugin)
│   ├── session-start.sh          # Status line + recent memory via additionalContext
│   ├── user-prompt-submit.sh     # Recall capability hint
│   └── stop.sh                   # Records payload, spawns the capture worker
├── scripts/
│   ├── capture-turn.py           # Detached worker: read turn → summarize → append → index
│   ├── zcode_turns.py            # SQLite turn builder shared by capture and L3
│   ├── parse-transcript.py       # L3 deep drill into ZCode's SQLite database
│   ├── derive-collection.sh      # Per-project collection name
│   └── maintenance-runner.py     # Project review / user profile / skill distillation
├── prompts/                      # Shared prompt templates (synced)
└── skills/
    ├── memory-recall/            # Hand-maintained for ZCode
    ├── memory-config/            # Synced from plugins/_shared
    └── memory-to-skill/          # Synced from plugins/_shared
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ZCODE_DB_PATH` | Override the ZCode database location (default `~/.zcode/cli/db/db.sqlite`) |
| `MEMSEARCH_ZCODE_DB_WAIT_S` | Seconds the worker waits for ZCode to commit the finished turn (default `10`) |
| `MEMSEARCH_DIR` | Use one explicit memory store instead of the per-project store |
