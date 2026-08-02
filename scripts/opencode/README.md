# opencode configuration (home-manager managed)

Everything opencode reads from `~/.config/opencode/` is deployed from this
directory (plus a generated `AGENTS.md`) by `nix/home.nix`. Edit **here**, then
`home-manager switch` (or `scripts/ship.sh`) — the deployed files are read-only
nix-store symlinks.

## 🔴 One-time manual step before the first switch

`~/.config/opencode/opencode.jsonc` already exists on this host as an
**unmanaged regular file**. home-manager will not clobber a hand-placed regular
file at a managed path — `force = true` is *not* sufficient (measured on
2026-07-30 for `~/.claude/hooks/bash-guard.py`: two consecutive switches
returned rc=0, printed "Creating home file links", and silently left the file
unmanaged).

The `opencodeDropStaleConfig` activation step in `nix/home.nix` handles this
automatically: before `checkLinkTargets` runs it moves any non-symlink
`opencode.jsonc` aside to `opencode.jsonc.pre-devrc-<timestamp>.bak` and
removes the original. **No manual step is required.**

If you would rather do it by hand first, or if the activation step is ever
removed, the exact command is:

```bash
mv ~/.config/opencode/opencode.jsonc ~/.config/opencode/opencode.jsonc.bak
```

The pre-existing file on this host is a 50-byte `$schema`-only stub, so nothing
of value is at stake — the backup exists purely so the switch can never be the
thing that destroys a config someone had edited.

## What lands where

| Deployed path | Source | Notes |
|---|---|---|
| `~/.config/opencode/AGENTS.md` | **generated** | `claude/PRINCIPLES.md` + `claude/RULES.md` + `claude/opencode-addendum.md`, concatenated at switch time |
| `~/.config/opencode/opencode.jsonc` | `opencode.jsonc` | model, permissions, compaction, agent pinning |
| `~/.config/opencode/plugin/env.js` | `env.js` | `shell.env` hook injecting the kubeconfig handles |
| `~/.config/opencode/agent/*.md` | `agent/` | the `nav`, `k8s` and `review` subagents |

## Why `AGENTS.md` is generated rather than symlinked

opencode does **not** expand `@`-imports inside `AGENTS.md`/`CLAUDE.md`
(measured on v1.18.4 with an all-tools-denied agent, so no file read was
possible: an imported passphrase returned `NONE`, the same content inline
returned verbatim). `~/.claude/CLAUDE.md` is ~1.5 KB consisting almost entirely
of `@PRINCIPLES.md` / `@RULES.md` import lines — so if opencode read it, it
would receive **none** of the 32 KB of actual rules.

Generating the file by concatenation at switch time means it can never drift
from the sources Claude Code reads. A project `AGENTS.md` also *suppresses*
`CLAUDE.md` entirely (first match wins), which is why this is the file that
matters.

Size matters here: the generated file measures **38,033 B (37.1 KB) ≈ 8.8k
tokens**, which is fine. A 331 KB `AGENTS.md` causes a permanent compaction loop.
`scripts/tests/test_opencode_config.py` enforces a 100 KB ceiling.

## 🔴 Gotchas that are already baked in — do not "tidy" them away

- **Permission ordering is the INVERSE of Claude Code: LAST match wins.** So
  `"*": "allow"` must be the FIRST key of the `bash` block and every deny/ask
  must come after it. Sorting the keys alphabetically silently disables every
  deny. The test suite pins `"*"` as the first key for exactly this reason.
- **The plugin glob is `{plugin,plugins}/*.{ts,js}` — non-recursive, `.ts`/`.js`
  only.** A `.mjs` will not load, and a plugin in a subdirectory will not load.
- **There is no `env` config key.** Injecting environment into the bash tool
  requires the `shell.env` plugin hook; opencode's bash tool does not source zsh
  startup files.
- **`small_model` drives title generation only** — not compaction. The cheap
  model is applied to compaction by pinning the hidden `title`/`summary`/
  `compaction` agents in the `agent` block.
- **Deprecated on 1.18.4, deliberately absent:** agent-level `tools`, `mode` at
  config level, `layout`, `autoshare`, `reference` (now `references`),
  `maxSteps` (now `steps`). `theme`/`keybinds`/`tui` live in a separate
  `~/.config/opencode/tui.json`.

## Why only three subagents

Every available subagent permanently enlarges the primary agent's `task` tool
description on **every** request. Measured schema cost on the same
project/prompt/model: no tools **680** tokens; `+read/grep/glob/list`
**1,406**; `+skill` alone **4,410** (the 16-skill catalogue is ~3,730
tokens/request); the built-in `build` agent **11,130**. Adding a fourth
general-purpose agent is not free — justify it against that number.

## Tests

```bash
python -m pytest scripts/tests/test_opencode_config.py -q
```

Part of the hermetic set run by `scripts/run-tests.sh` and the flake check.
