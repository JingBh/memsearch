# MemSearch Pi Extension

Persistent semantic memory for [Pi](https://pi.dev), backed by the shared MemSearch CLI and centralized per-project storage.

## Features

- Captures each settled Pi user turn into a daily Markdown journal.
- Uses the project Git root to share memory across worktrees.
- Stores state under `~/.memsearch/projects/<collection>/` unless `MEMSEARCH_DIR` is explicitly set.
- Injects recent memory context at session start.
- Registers `memory_search`, `memory_get`, and `memory_transcript` tools.
- Loads the `memory-recall`, `memory-config`, and `memory-to-skill` skills.
- Runs project review, user profile, and procedural-memory maintenance through `plugins.pi.*` configuration.

## Install from source

Link the whole plugin directory as one Pi extension:

```bash
mkdir -p ~/.pi/agent/extensions
ln -s /path/to/memsearch/plugins/pi ~/.pi/agent/extensions/memsearch
```

Pi discovers `index.ts`; the extension contributes its bundled skills through `resources_discover`.

Install the MemSearch CLI separately, for example with `uv tool install 'memsearch[onnx]'`. The extension never installs dependencies automatically.

## Configuration

```toml
[plugins.pi.summarize]
enabled = true
provider = "native"
model = ""

[plugins.pi.project_review]
enabled = false
provider = "native"
model = ""
min_interval_hours = 24
input_dir = ".memsearch/memory"
output_file = ".memsearch/PROJECT.md"

[plugins.pi.user_profile]
enabled = false
provider = "native"
model = ""
min_interval_hours = 24
input_dir = ".memsearch/memory"
output_file = ".memsearch/USER.md"
```

Paths beginning with `.memsearch/` resolve inside the centralized project store, not inside the checkout. Existing legacy `PROJECT.md` and `USER.md` files are moved there on the next maintenance run when no centralized copy exists.

Run `/reload` after updating the extension or its skills.
