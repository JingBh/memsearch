#!/usr/bin/env python3
"""DSH plugin summarizer: reuse memsearch's ``[llm.providers.*]`` config.

The memsearch CLI exposes ``memsearch summarize --plugin <name>`` for the four
built-in platforms, but ``plugins.dsh.*`` is not part of the core config
schema, so this plugin ships its own thin summarizer that imports the same
memsearch config + LLM plumbing directly:

    prompt + transcript  ->  resolve_config() -> compact.summarize_text()

Provider selection (most specific first):

1. ``--provider <name>``  — the DSH plugin config's ``summarizeProvider``;
   looked up in ``[llm.providers.<name>]``. Missing entry is a visible error.
2. ``cfg.llm.provider``   — when it names a configured provider or is a raw
   provider type (openai/anthropic/gemini).
3. ``cfg.compact.llm_provider`` (deprecated fallback) or ``openai``.

Failures exit non-zero with a message on stderr so the plugin can surface a
visible error instead of silently writing nothing.

Usage:
    summarize.py --agent-name "DeepSeek Harness" [--provider NAME] [--model M] \\
        [--project-dir DIR]
    transcript ... (stdin)  ->  bullet points (stdout)
"""

# ruff: noqa: T201  # CLI tool: stdout/stderr are the output mechanism
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# memsearch importability bootstrap (shared with plugins/_shared/scripts)
# ---------------------------------------------------------------------------


def ensure_memsearch_importable() -> None:
    """Make the memsearch Python API importable from any environment."""
    user_paths = [
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
        str(Path.home() / "bin"),
        "/usr/local/bin",
    ]
    existing_path = os.environ.get("PATH", "")
    path_parts = existing_path.split(os.pathsep) if existing_path else []
    for user_path in reversed(user_paths):
        if Path(user_path).is_dir() and user_path not in path_parts:
            path_parts.insert(0, user_path)
    os.environ["PATH"] = os.pathsep.join(path_parts)

    # Prefer the checkout's own source when running from a git worktree.
    for parent in Path(__file__).resolve().parents:
        src_dir = parent / "src"
        if (src_dir / "memsearch").is_dir():
            sys.path.insert(0, str(src_dir))
            break

    try:
        import memsearch  # noqa: F401

        return
    except ModuleNotFoundError:
        pass

    if os.environ.get("MEMSEARCH_DSH_UV_BOOTSTRAP") == "1":
        return

    memsearch_bin = _which("memsearch")
    if memsearch_bin:
        try:
            first_line = Path(memsearch_bin).read_text(encoding="utf-8", errors="ignore").splitlines()[0]
            if first_line.startswith("#!"):
                python_bin = first_line[2:].strip().split()[0]
                if python_bin:
                    os.execvpe(
                        python_bin,
                        [python_bin, str(Path(__file__).resolve()), *sys.argv[1:]],
                        {**os.environ, "MEMSEARCH_DSH_UV_BOOTSTRAP": "1"},
                    )
        except (OSError, UnicodeDecodeError):
            pass

    uv = _which("uv")
    if not uv:
        return

    env = {**os.environ, "MEMSEARCH_DSH_UV_BOOTSTRAP": "1"}
    os.execvpe(
        uv,
        [
            uv,
            "run",
            "--with",
            "memsearch[onnx]",
            "python",
            str(Path(__file__).resolve()),
            *sys.argv[1:],
        ],
        env,
    )


def _which(name: str) -> str | None:
    """Return the first PATH match for ``name`` (no shell involved)."""
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------


def _load_summarize_prompt(config, agent_name: str, plugin_dir: Path) -> str:
    """Load the summarize prompt: user override > plugin template > inline."""
    configured = getattr(config, "prompts", None)
    custom_path = getattr(configured, "summarize", "") if configured else ""
    if custom_path:
        candidate = Path(custom_path).expanduser()
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").replace("{{AGENT_NAME}}", agent_name)

    builtin = plugin_dir / "prompts" / "summarize.txt"
    if builtin.is_file():
        return builtin.read_text(encoding="utf-8").replace("{{AGENT_NAME}}", agent_name)

    return (
        "You are a third-person note-taker. You will receive a transcript of ONE conversation turn "
        f"between User and {agent_name}.\n\n"
        "Record what happened as factual third-person notes. Output 2-10 bullet points, each starting with '- '. "
        "Use 'User' for the user. First bullet: what User asked or wanted. Remaining bullets: what was done, "
        f"found, changed, configured, tested, explained, decided, or could not be completed by {agent_name}. "
        "Mandatory language rule: write every bullet in the same primary language as the [User] text. "
        "If User mixes languages, use the dominant user-facing language. "
        "Be specific when useful: mention important files read or edited, searches or research performed, "
        "refactors, commands or tests run, key findings, and concrete outcomes. Prefer the final user-visible "
        "outcome over low-level transcript mechanics. Do NOT answer User's question yourself. Output ONLY "
        "bullet points."
    )


def _resolve_llm_settings(config, provider_arg: str, model_arg: str) -> tuple[str, str | None, str | None, str | None]:
    """Resolve (provider_type, model, base_url, api_key) from memsearch config.

    Mirrors the plugin summarize resolution in ``cli.py`` while adding the
    compact-style fallback for when no ``[llm.providers.*]`` entry is named.
    """
    llm = getattr(config, "llm", None)
    compact = getattr(config, "compact", None)

    def _named_provider(name: str) -> tuple[str, str | None, str | None, str | None]:
        providers = getattr(llm, "providers", {}) if llm else {}
        provider_cfg = providers.get(name)
        if provider_cfg is None:
            raise ValueError(f"Unknown LLM provider {name!r}. Configure [llm.providers.{name}] in memsearch config.")
        provider_type = provider_cfg.type or name
        model = model_arg or provider_cfg.model or (getattr(llm, "model", "") or "")
        base_url = provider_cfg.base_url or getattr(llm, "base_url", "") or None
        api_key = provider_cfg.api_key or getattr(llm, "api_key", "") or None
        return provider_type, model or None, base_url, api_key

    if provider_arg:
        return _named_provider(provider_arg)

    top_provider = getattr(llm, "provider", "") if llm else ""
    if top_provider and top_provider in (getattr(llm, "providers", {}) or {}):
        return _named_provider(top_provider)

    if not top_provider:
        top_provider = getattr(compact, "llm_provider", "") if compact else ""
    provider_type = top_provider or "openai"
    model = model_arg or getattr(llm, "model", "") or getattr(compact, "llm_model", "")
    base_url = getattr(llm, "base_url", "") or getattr(compact, "base_url", "") or None
    api_key = getattr(llm, "api_key", "") or getattr(compact, "api_key", "") or None
    return provider_type, model or None, base_url, api_key


async def _summarize(
    prompt: str, llm_provider: str, model: str | None, base_url: str | None, api_key: str | None
) -> str:
    from memsearch.compact import summarize_text

    return await summarize_text(
        prompt,
        llm_provider=llm_provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a DSH turn with memsearch-managed LLM.")
    parser.add_argument("--agent-name", default="DeepSeek Harness", help="Agent display name.")
    parser.add_argument("--provider", default="", help="Named [llm.providers.*] entry to use.")
    parser.add_argument("--model", default="", help="Override the LLM model.")
    parser.add_argument("--project-dir", default="", help="Project directory (config resolution anchor).")
    parser.add_argument("--plugin-dir", default="", help="Plugin directory (prompt template location).")
    args = parser.parse_args()

    transcript = sys.stdin.read()
    if not transcript.strip():
        return 0

    plugin_dir = Path(args.plugin_dir).resolve() if args.plugin_dir else Path(__file__).resolve().parent.parent

    if args.project_dir:
        os.chdir(args.project_dir)

    ensure_memsearch_importable()

    try:
        from memsearch.config import resolve_config

        config = resolve_config()
    except Exception as error:  # surface any config failure visibly
        print(f"Error: failed to load memsearch config: {error}", file=sys.stderr)
        return 1

    try:
        system_prompt = _load_summarize_prompt(config, args.agent_name, plugin_dir)
        provider_type, model, base_url, api_key = _resolve_llm_settings(config, args.provider, args.model)
        prompt = f"{system_prompt}\n\nTranscript:\n{transcript}"
        summary = asyncio.run(_summarize(prompt, provider_type, model, base_url, api_key))
    except Exception as error:  # report provider errors instead of hiding them
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if summary and summary.strip():
        print(summary.strip())
        return 0
    print("Error: summarizer returned no output", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
