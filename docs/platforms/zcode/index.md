# ZCode Plugin

**Semantic memory for [ZCode](https://z.ai/).** A marketplace plugin that captures every finished turn from ZCode's SQLite session store and provides three-layer memory recall through the `memory-recall` skill.

---

## Why memsearch for ZCode?

ZCode's built-in memory is off by default and stays inside ZCode. memsearch gives ZCode the same markdown-first, Milvus-backed memory the other plugins use:

| Aspect | memsearch |
|--------|-----------|
| **Vector backend** | [Milvus](https://milvus.io/) -- hybrid search (dense + BM25 + RRF) |
| **Storage format** | Plain `.md` files (human-readable, git-friendly) |
| **Cross-platform** | Same memories accessible from Claude Code, Codex, Pi, DSH, OpenClaw, OpenCode |
| **Capture method** | Stop hook + detached worker reading ZCode's SQLite store |
| **Progressive disclosure** | Three-layer: search → expand → transcript |
| **Embedding model** | Pluggable: ONNX bge-m3 (default), OpenAI, Google, Voyage, Jina, Mistral, Ollama |

### The Cross-Platform Advantage

If you use ZCode alongside Claude Code or Codex on the same repository, memsearch gives you a **unified memory layer**: every plugin derives the same per-project collection name and writes the same markdown format, so a conversation in ZCode becomes searchable context in the other agents and vice versa.

---

## Key Features

- **Hook-based capture without blocking** -- ZCode runs hooks inline, so the Stop hook only records its payload and spawns a detached worker
- **Real transcripts from SQLite** -- the worker reads the finished turn from `~/.zcode/cli/db/db.sqlite`, not from the one-line transcript ZCode hands to hooks
- **Three-layer progressive recall** -- search, expand, and drill into original conversations ([details](memory-recall.md))
- **Cold-start context** -- recent memories injected at session start via `additionalContext`
- **Provider-routed summarization** -- ZCode's bundled CLI only runs inside the desktop app's environment, so summaries run through a memsearch-managed LLM provider
- **ONNX embedding by default** -- no API key required, runs locally on CPU

---

## When Is This Useful?

- **Multi-day projects.** ZCode sessions are long-lived but not searchable. memsearch captures every turn so you can pick up where you left off without re-explaining context.
- **Cross-platform workflows.** You switch between ZCode and Claude Code or Codex depending on the task. memsearch provides continuous memory across all of them.
- **Debugging trails.** When a bug resurfaces, memsearch can recall what was tried before -- which approaches failed, what worked, and why.

---

## Platform Notes

!!! note "macOS and Linux"
    The plugin hooks are bash scripts and the worker is Python 3. On Windows, run ZCode's hooks through a POSIX shell (Git Bash or WSL2).

## Pages

- [Installation](installation.md) -- marketplace install, configuration, verification
- [How It Works](how-it-works.md) -- hooks, capture worker, memory files, architecture
- [Memory Recall](memory-recall.md) -- the memory-recall skill and progressive disclosure
