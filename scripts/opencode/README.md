# opencode configuration (home-manager managed)

Everything opencode reads from `~/.config/opencode/` is deployed from this
directory (plus a generated `AGENTS.md` and a generated `plugin/env.js`) by
`nix/home.nix`. Edit **here**, then `home-manager switch` (or
`scripts/ship.sh`) — the deployed files are read-only nix-store symlinks.

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
| `~/.config/opencode/plugin/env.js` | **generated** | from `nix/agent-handles.nix` — the same file that generates the zsh exports |
| `~/.config/opencode/plugin/guard.js` | `plugin/guard.js` | 🔴 the deterministic bash guard — see below |
| `~/.config/opencode/guard_core.py` | `../claude-hooks/guard_core.py` | the shared checking core, the same file Claude Code's `bash-guard.py` imports |
| `~/.config/opencode/agent/*.md` | `agent/` | the `nav`, `k8s` and `review` subagents |

## 🔴 Two layers, and only one of them is a safety control

**Layer 1 — the `permission.bash` globs in `opencode.jsonc`: FRICTION.** Broad
`ask` on mutation families so a human sees the command.

**Layer 2 — `plugin/guard.js` → `guard_core.py`: ENFORCEMENT.** It parses:
splits on `;`/`&&`/`||`/`|`/`&`, strips `VAR=…` prefixes and
`sudo`/`doas`/`env`/`timeout`/… wrappers, recurses into `bash -c '…'`, and
reasons about **argv**. It throws from `tool.execute.before`, which hard-blocks
the call.

### Why the split

A glob matches a command node's **full text**, so "this command wipes a node" is
not expressible — the set of spellings is unbounded. Two rounds of
pattern-patching each closed the spellings we thought of and left the ones we
did not. Measured at `c1e4c02` by replaying that ref's config through the
resolver model, **all resolving `allow` on the primary agent**:

```
talosctl -n 192.168.50.94 reset          <- a node wipe, no prompt
talosctl --nodes=192.168.50.94 reset
rm -f -r /          rm --recursive --force /
mke2fs /dev/sdc     mkswap /dev/sdd
```

because `"*talosctl reset*"` requires the tool and the verb to be **adjacent**,
and `"*rm -rf*"`/`"*mkfs*"` knew one flag order and one binary name. The deny
block had been given infix wildcards; the ask block had not.

The `review` agent had a second instance of the same class: its `git -C * diff*`
allow-list sat after its own `"*": deny`, and opencode's `*` is an unrestricted
`.*` crossing spaces — so **`git -C <path> stash push -m 'wip on the diff'`
executed and created a stash** (verified; the same command without the word
"diff" was denied). The word "diff" in a message was the whole bypass.

**So: do not add a new dangerous family to `opencode.jsonc` and consider it
handled.** A glob there buys a prompt for the spelling you thought of. If the
action is irreversible, add a check to `guard_core.py`'s `"opencode"` policy.

### Why `tool.execute.before` and not `permission.ask`

Measured on 1.18.4, this host, 2026-08-02:

- `permission.ask` **is** in the `Hooks` type and its `output.status` is typed
  `"ask" | "deny" | "allow"`, so an *ask* decision looks expressible. It is not:
  the hook **never fired** in any probe — not on the allow path, and not on the
  ask path either (a `*probe-beta*: ask` rule under `opencode run` printed
  `auto-rejecting` without the hook logging a line). A hook that does not run
  cannot upgrade an allow into an ask, which is the one thing a guard needs it
  for.
- `tool.execute.before` **did** fire on every bash call, and **throwing from it
  hard-blocks** — opencode surfaces the thrown message to the model as a tool
  error and the command never runs.

**DENY is expressible from a plugin; ASK is not.** Ask-grade families therefore
stay as globs. Globs are acceptable for friction and unacceptable as the only
thing guarding an irreversible action.

Also measured: **`opencode run` AUTO-REJECTS an `ask`** (it prints
`auto-rejecting`); only the interactive TUI turns one into a prompt, and
`opencode debug agent --tool` auto-**approves** it. So `ask` means "friction for
a human", never "a control on an unattended agent".

### What the deny globs are still for

A short, deliberately **non-exhaustive** mirror of the guard's families in their
naive spellings, so a `guard.js` that fails to load does not silently remove
every deny at once. Defence in depth, not the control — every one of those
patterns has known bypasses, which is exactly why layer 2 exists.

### Two policies, one implementation

`guard_core.py` exposes named policy sets:

| Policy | Checks | Used by |
|---|---|---|
| `claude-code` | the original six, **frozen** | `~/.claude/hooks/bash-guard.py` |
| `opencode` | those six **plus** `talosctl reset`, `mkfs`, `dd` to a block device, `rm -r` of `/`/`$HOME`/cwd/system dirs, `git stash`, `git clean -f`, and `git reset --hard` through a `-C` hop | `plugin/guard.js` |

`bash-guard.py` fires on **every** Bash call in **every** Claude Code session on
both hosts, so adding a check to `claude-code` changes the operator's primary
tool. It is deliberately unchanged: a before/after decision matrix over a
2,097-command corpus differs on **0** rows.

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

Size matters here: the generated file measures **38,363 B (37.5 KB) ≈ 8.9k
tokens**, which is fine. A 331 KB `AGENTS.md` causes a permanent compaction loop.
`scripts/tests/test_opencode_config.py` enforces a 100 KB ceiling.

## Why `plugin/env.js` is generated too

The repo-root and kubeconfig handles (`$HOMELAB`, `$KC_HOMELAB`, …) are defined
**once**, in `nix/agent-handles.nix`, and that one definition generates both:

* the `[[ -d … ]] && export …` lines in `programs/zsh` (what Claude Code sees), and
* the `shell.env` hook in `plugin/env.js` (what opencode sees).

Before this, `scripts/opencode/env.js` was a checked-in file that hardcoded
`/home/zach/workspace/homelab-talos`, duplicated the zsh block, applied **no
existence guard**, and defined a `KC_PROD` that zsh did not have. Add a handle in
`agent-handles.nix` — never in one consumer.

## 🔴 Gotchas that are already baked in — do not "tidy" them away

- **Permission ordering is the INVERSE of Claude Code: LAST match wins**, over a
  single FLAT array. `"*": "allow"` must be the FIRST key of the `bash` block,
  then every `ask`, then every `deny` — deny-last is what lets a narrow deny
  (`*talosctl reset*`) beat a broad ask (`*talosctl*`).
  A key-order assertion alone is **not** enough: `"*"` (0x2A) sorts to the front,
  so an alphabetical "tidy-up" keeps the wildcard looking correct while
  reordering everything else, and a broad ask can overtake the deny it should
  lose to. The suite therefore pins the **effective resolved outcome** for a
  matrix of dangerous commands on every agent.
- 🔴 **An agent-level `bash: {"*": allow}` NULLIFIES the entire global block.**
  Agent rules are appended AFTER the global ones, so an agent wildcard wins over
  all of them. `k8s` shipped with one: only its own 4 rules survived, leaving
  `git stash`, `git reset --hard`, `git add -A`, `rm -rf ~…`, `sops -d`,
  `nixos-rebuild` and `home-manager switch` as plain **allow** on that agent. An
  agent block may only ever TIGHTEN — the global wildcard already keeps `bash`
  enabled, so re-stating it buys nothing and costs everything.
- 🔴 **Every dangerous pattern must be leading-`*`.** opencode matches a command
  NODE's full text against a glob compiled to `.*` with dotAll, so a bare
  `"git stash*"` misses `FOO=1 git stash`, `sudo -n git stash` and
  `git -C /tmp stash` — all three measured as ALLOWED. That is acute here because
  the house style mandates exactly those spellings (`KUBECONFIG=$KC_HOMELAB
  kubectl …`, `git -C <path> …`). `*` crosses spaces, `/` and `-`, so
  `*git*stash*` catches all of them. Pipelines and `&&` chains are already
  checked per-command, so only the prefix/wrapper case needed closing.
- 🔴 **The global `permission` block also applies to the hidden
  `title`/`summary`/`compaction` agents**, whose stock tool set is EMPTY.
  Listing tools there re-enabled `bash`, `write`, `edit`, `task` and `skill` on
  all three — including `compaction`, which runs automatically on every context
  overflow, on the cheap model, on a path nobody watches. Each therefore carries
  an explicit `"permission": {"*": "deny"}`.
- **Do not add `"read": "allow"`.** opencode ships a built-in `.env` guard
  (`*.env` → ask); a blanket allow is appended after it and silently defeats it
  on every agent. The default already allows every non-`.env` read, so the
  correct configuration is to say nothing.
- **There is no `list` tool and no `websearch` tool** on 1.18.4. The resolved set
  is exactly {bash, edit, glob, grep, invalid, question, read, skill, task,
  todowrite, webfetch, write}. Naming a nonexistent tool is a silent no-op that
  reads like configuration.
- **The plugin glob is `{plugin,plugins}/*.{ts,js}` — non-recursive, `.ts`/`.js`
  only.** A `.mjs` will not load, and a plugin in a subdirectory will not load.
- **There is no `env` config key**, so the `shell.env` plugin hook is the only
  supported seam. 🔴 But the claim that "opencode's bash tool does not source zsh
  startup files" is **FALSE on this host** (measured 2026-08-02, v1.18.4): the
  tool shell IS zsh (`ZSH_VERSION` reports 5.9 inside a bash tool call) and it
  does source `.zshenv` — with the plugin absent and `VITEST_MAX_WORKERS`
  explicitly unset in the parent, the tool still reported `4`. The original
  negative control was real but **misattributed**: the kubeconfigs are gitignored
  and absent from the checkout, so zsh's existence guard correctly declines to
  export `KC_HOMELAB`, while the old UNGUARDED `env.js` exported it regardless —
  i.e. the plugin looked load-bearing because it was pointing a handle at a file
  that does not exist. Keep the plugin as belt-and-braces for a non-zsh `$SHELL`,
  but it now existence-guards exactly like zsh.
- **`small_model` drives title generation only** — not compaction. The cheap
  model is applied to compaction by pinning the hidden `title`/`summary`/
  `compaction` agents in the `agent` block.
- **`plan`'s built-in `task: {general: deny}` is easy to undo by accident.** A
  global `"task": "allow"` is appended after it and flips it, which hands `plan`
  a shell by delegation (the `general` subagent has one). It is restated at agent
  level.
- **Deprecated on 1.18.4, deliberately absent:** agent-level `tools`, `mode` at
  config level, `layout`, `autoshare`, `reference` (now `references`),
  `maxSteps` (now `steps`). `theme`/`keybinds`/`tui` live in a separate
  `~/.config/opencode/tui.json`.

### Known limitation: in-session "always allow"

Session-scoped "always" approvals are concatenated **after** the config ruleset,
so an in-session *always allow* outranks a config `deny` for matching commands.
The auto-generated always-pattern is arity-derived, so approving
`git -C /tmp stash` once would install `git -C *` — considerably broader than it
looks. Prefer a one-off approval over "always" for anything under `git`.

## Why only three subagents

Every available subagent permanently enlarges the primary agent's `task` tool
description on **every** request. Measured schema cost on the same
project/prompt/model: no tools **680** tokens; `+read/grep/glob`
**1,406**; `+skill` alone **4,410** (the 16-skill catalogue is ~3,730
tokens/request); the built-in `build` agent **11,130**. Adding a fourth
general-purpose agent is not free — justify it against that number.

## Tests

```bash
python -m pytest scripts/tests/test_opencode_config.py -q          # config + agents + browser-agent
python -m pytest scripts/claude-hooks/tests/test_guard_core.py -q  # the shared guard core
python3 scripts/claude-hooks/tests/test_bash_guard.py              # the Claude Code adapter, end-to-end
```

`test_guard_core.py` runs every new rule across an outer product of nine
prefixes (`VAR=`, `sudo`, `sudo -n`, `doas`, `env`, `timeout`, `nohup`, …), each
git global-option hop, and each of the five command separators — because the
glob-era suite pinned one spelling per pattern, always the one the pattern was
written around, which is exactly why it was blind to `talosctl -n <ip> reset`.
That matrix immediately caught a real bug in the first draft of the parser
(`sudo -n <cmd>` peeled wrong, because `-n` had been put in a shared value-flag
set for `nice -n 5`).

`test_opencode_config.py` asserts the **layered** verdict (`layered_verdict`)
for "must not run", and the glob-only model (`effective_bash_action`) only for
claims about the config file's own structure and for `ask`, which the guard
cannot express.

Part of the hermetic set run by `scripts/run-tests.sh` and the flake check.
Two tests shell out to `nix-instantiate --eval` to pin the generated handle
values; they FAIL rather than skip if nix is absent, because a silent skip there
is how a wrong kubeconfig path ships.
