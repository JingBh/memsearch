# memsearch-dsh

MemSearch plugin for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (DSH).
It gives DSH persistent, cross-agent memory on the same `.memsearch/memory/`
markdown store used by the Claude Code, Codex, OpenClaw, and OpenCode plugins,
backed by a Milvus hybrid search index.

```
capture  ── session/event turn/end ──> summarize.py (reuses [llm.providers.*]) ──> memory/YYYY-MM-DD.md
inject   ── agent/pre-step step 1  ──> memsearch search ──> relevant chunks injected (zero cost otherwise)
recall   ── ctx.skills.register(memory-recall) ──> search → expand → transcript
```

## Prerequisites

- A working `memsearch` CLI. Either install it:

  ```bash
  uv tool install "memsearch[onnx]"
  ```

  or let the plugin fall back to `uvx --from 'memsearch[onnx]' memsearch`
  (auto-detected at load).
- A DSH profile (web / headless / tui) you want to attach memory to.
- Node >= 22.19 (DSH's requirement).

## Install

From the repo root, install the plugin into a profile. `dsh plugin` is a pnpm
forwarder: it links the directory into the profile, detects the `dsh.bundle`
declaration in `package.json`, and appends the package to the profile's bundle
layers. The `cordis.patch.yml` inside this directory then inserts the
`memsearch` row into the profile's plugin tree.

```bash
dsh plugin --profile web add /path/to/memsearch/plugins/dsh
```

> The path is resolved from the directory you run `dsh` in; an absolute path
> is safest. Replace `web` with your profile name (`headless`, `tui`, ...).
>
> Profiles are managed by the `@deepseek-ai/dsh-app-boot` profile store, and
> the patch layer list lives in the profile manifest. The same flow works for
> every shipped profile.

Restart DSH for the profile (or start a new session) so the plugin mounts.

### Manual patch insertion (no `dsh plugin`)

Append this row to the profile's `cordis.patch.yml` and make sure
`memsearch-dsh` is resolvable from the profile's `node_modules` (for example a
`link:` dependency):

```yaml
- insert:
    - id: memsearch
      name: 'memsearch-dsh'
```

### Verify it loaded

Start DSH and check the session log for the plugin mount, or confirm the
`memory-recall` skill is available through the `skill` tool. Captured turns
land in `<project>/.memsearch/memory/YYYY-MM-DD.md`.

## Configuration

The plugin is configured through the profile's `cordis.patch.yml` `config`
block (patch the `memsearch` row you inserted). All keys are optional.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `captureEnabled` | bool | `true` | Capture completed turns into memory. |
| `injectEnabled` | bool | `true` | Inject relevant memory before each turn's first step. |
| `summarizeEnabled` | bool | `true` | Summarize turns with a memsearch-managed LLM. |
| `summarizeProvider` | string | `''` | Name of an `[llm.providers.*]` entry used for summarization. |
| `summarizeModel` | string | `''` | Model override for summarization. |
| `memsearchDir` | string | `<project>/.memsearch` | Override the memory directory. |
| `collection` | string | derived | Override the Milvus collection name. |
| `milvusUri` | string | `''` | Point search/index at a dedicated Milvus (URI or path) instead of memsearch's own config. Useful to isolate a profile's memory or target a shared Milvus Server. |
| `agentName` | string | `DeepSeek Harness` | Display name in the summarize prompt. |

Example override layer (add this to the profile's own `cordis.patch.yml`):

```yaml
- id: memsearch
  config:
    summarizeProvider: deepseek
```

### Summarization provider resolution

Summarization reuses memsearch's `[llm.providers.*]` configuration — the same
entries the other platform plugins use — so DSH adds no separate LLM setup.
Provider selection (most specific first):

1. `summarizeProvider` — looked up in `[llm.providers.<name>]`; a missing
   entry fails loudly (visible error), never a silent empty write.
2. `llm.provider` when it names a configured provider or is a raw type.
3. `compact.llm_provider` (deprecated) or `openai` as a final default.

A failed summarization writes the raw turn as a fallback so memory is never
lost, and logs a visible warning through the DSH logger.

## How it works

- **Capture** — listens on `session/event` for `turn/end`, renders the turn
  (`[User]` / `[Assistant]` / `[Tool call]` lines), stages it durably in
  `<memsearchDir>/.dsh-capture.jsonl`, then asynchronously summarizes and
  appends it to `memory/YYYY-MM-DD.md` with the shared anchor format
  `<!-- session:<id> turn:<N> db:<path> -->`. Turns staged by a run that was
  interrupted mid-drain are replayed on the next plugin load.
- **Inject** — on `agent/pre-step` at step 1, runs a bounded memsearch search
  over the user's question. Only when relevant chunks exist does it inject
  them plus a `[memsearch] Memory available.` hint; otherwise the decision is
  returned unchanged (zero context cost).
- **Recall** — registers a `memory-recall` skill (invocable through DSH's
  native `skill` tool) that performs search → expand → transcript drill-down
  and returns a curated summary.

## Uninstall

```bash
dsh plugin --profile web rm memsearch-dsh
```

Removing the dependency drops the profile-layer entry; the memory markdown
files and the Milvus index are left untouched.

## Development

- The plugin is plain ESM with no build step — `dsh plugin add` links the
  checkout directly, so edits are live after a profile reload.
- Python helpers under `scripts/` are linted with the repo's `ruff` config and
  tested under `plugins/dsh/tests/`.
- Keep the memory-write format byte-compatible with the other platform
  plugins; see `plugins/opencode/scripts/capture-daemon.py` for the canonical
  writer.
