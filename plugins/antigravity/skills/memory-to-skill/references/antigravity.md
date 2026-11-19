# Antigravity platform reference (memory-to-skill)

## Config key prefix

`plugins.antigravity.memory_to_skill.*`

## Install paths

Each agent reads skills from its own directory: Antigravity `.agents/skills/`
(project) and `~/.gemini/config/skills/` (global); Claude Code `.claude/skills/`;
Codex and OpenCode `.agents/skills/`; ZCode `.zcode/skills/` and `.agents/skills/`.

Recommended targets:

- `.agents/skills` — **project-local (recommended)**: shared across Antigravity,
  Codex, ZCode, and OpenCode.
- `~/.gemini/config/skills` — global for Antigravity across all workspaces
  (App, CLI, and IDE).
- a custom path, or several (one skill can be installed to multiple dirs).

Install to multiple paths to cover several agents.
