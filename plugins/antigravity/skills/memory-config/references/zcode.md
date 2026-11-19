# ZCode platform reference

## Plugin version & update

The ZCode plugin version is `plugins/zcode/.zcode-plugin/plugin.json`
(`version`), listed as `memsearch-zcode` in the marketplace. Compare the
installed copy with the source checkout:

```bash
python3 -c 'import json,os;print([p for p in json.load(open(os.path.expanduser("~/.zcode/cli/plugins/installed_plugins.json")))["plugins"] if p["name"]=="memsearch-zcode"])'
git -C <memsearch-repo> describe --tags --always --dirty
```

ZCode copies the plugin into `~/.zcode/cli/plugins/cache/` at install time, so
after updating the marketplace source, update the plugin from
**Settings → Plugin Management** to pick up new hooks, scripts, or skills.

ZCode transcript recall reads from ZCode's SQLite database
(`~/.zcode/cli/db/db.sqlite`, override with `ZCODE_DB_PATH`), while captured
MemSearch memory lives as markdown under the project store.

Docs: https://zilliztech.github.io/memsearch/platforms/zcode/installation/

## Plugin keys

```toml
[plugins.zcode.summarize]
enabled = true
provider = ""      # empty/native = plain user/assistant extract (no native summarizer)
model = ""

[plugins.zcode.project_review]
enabled = false
provider = "native"
model = ""
min_interval_hours = 24
input_dir = ".memsearch/memory"
output_file = ".memsearch/PROJECT.md"

[plugins.zcode.user_profile]
enabled = false
provider = "native"
model = ""
min_interval_hours = 24
input_dir = ".memsearch/memory"
output_file = ".memsearch/USER.md"

[plugins.zcode.memory_to_skill]
enabled = false
min_occurrences = 3   # how many times a workflow must recur before it is distilled
paths = []            # where installed skills are copied; empty = ask the user
```

## Native model defaults

- ZCode's bundled CLI only runs inside the desktop app's environment, so there
  is no native LLM summarizer. With
  `provider = ""` or `native`, capture stores a plain "User asked / ZCode"
  extract of the turn instead of an LLM summary.
- Maintenance tasks (`project_review`, `user_profile`, `memory_to_skill`)
  require a memsearch-managed provider: define `[llm.providers.<name>]` and
  set `plugins.zcode.<task>.provider` to that name. `native` fails with
  "Unsupported native maintenance platform".

## Restart guidance

ZCode loads plugin hooks and skills when a session starts. Start a new ZCode
session after updating the plugin. TOML changes apply on the next
capture/recall/index/maintenance invocation without restarting.
