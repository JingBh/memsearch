# Installation

## Prerequisites

- ZCode desktop app with plugin support
- Python 3.10+
- memsearch installed: `uv tool install "memsearch[onnx]"`
- `bash` and `python3` on `PATH` for the hook scripts

## Install from the marketplace

ZCode installs plugins from marketplaces. The memsearch repository is a marketplace that lists `memsearch-zcode`.

1. Open **Settings → Plugin Management → Discover** and click **`+`**.
2. Add the GitHub repository `zilliztech/memsearch`, or a local checkout directory for development.
3. Install **memsearch-zcode** from the new marketplace.
4. Start a new ZCode session.

ZCode copies the plugin into `~/.zcode/cli/plugins/cache/<marketplace>/memsearch-zcode/<version>/` and enables its hooks automatically. Skills are namespaced as `memsearch-zcode:memory-recall`, `memsearch-zcode:memory-config`, and `memsearch-zcode:memory-to-skill`.

## Configure summarization

ZCode's bundled CLI only runs inside the desktop app's environment, so the plugin has no native summarizer. Define a memsearch-managed provider and route the ZCode plugin through it:

```bash
memsearch config set llm.providers.openai.type openai
memsearch config set llm.providers.openai.api_key env:OPENAI_API_KEY
memsearch config set plugins.zcode.summarize.provider openai
memsearch config set plugins.zcode.summarize.model gpt-5-mini
```

Without a provider, each turn is stored as a plain `User asked / ZCode` extract instead of an LLM summary. The same providers drive the optional maintenance tasks (`plugins.zcode.project_review`, `plugins.zcode.user_profile`, `plugins.zcode.memory_to_skill`).

ZCode launches hooks from the desktop app, so they do not inherit your shell exports. If `config.toml` uses `env:VAR` references for API keys, put those variables in `~/.memsearch/.env` (see [Configuration](../../home/configuration.md#secrets-via-env-references)); memsearch reads it whenever a referenced variable is missing from the environment.

The plugin defaults to ONNX embedding (no API key). Other configuration uses the standard memsearch config system:

```bash
memsearch config set embedding.provider onnx
memsearch config set milvus.uri http://localhost:19530  # optional: remote Milvus
```

## Verify the plugin is working

1. Start a new ZCode session in a project and chat for a couple of turns. The injected context starts with a `[memsearch v...]` status line.
2. Find the project's collection and confirm memory files appear:

```bash
COLLECTION=$(bash ~/.zcode/cli/plugins/cache/*/memsearch-zcode/*/scripts/derive-collection.sh "$(git rev-parse --show-toplevel)")
ls ~/.memsearch/projects/$COLLECTION/memory/
```

3. Inspect today's memory file:

```bash
cat ~/.memsearch/projects/$COLLECTION/memory/$(date +%Y-%m-%d).md
```

4. Ask a recall question in ZCode, for example:

```text
We discussed the hook timeout units before, what did we conclude?
```

If capture is working, entries appear a few seconds after each turn ends (the worker waits for ZCode to commit the turn), and ZCode invokes `memsearch-zcode:memory-recall` when history is relevant.

## Updating

After pulling a newer checkout or when the marketplace publishes a new version, update the plugin from **Settings → Plugin Management**. ZCode keeps the cached copy until you do.

## Uninstall

Uninstall **memsearch-zcode** from **Settings → Plugin Management**. Uninstalling the plugin does not delete memory files under `~/.memsearch/projects/`.
