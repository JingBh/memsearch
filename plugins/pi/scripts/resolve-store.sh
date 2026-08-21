#!/usr/bin/env bash
# Print the centralized MemSearch state directory for the current project.

set -euo pipefail

if [ -n "${MEMSEARCH_DIR:-}" ]; then
  python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$MEMSEARCH_DIR"
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
COLLECTION=$(bash "$SCRIPT_DIR/derive-collection.sh" "$PROJECT_DIR")
printf '%s/.memsearch/projects/%s\n' "$HOME" "$COLLECTION"
