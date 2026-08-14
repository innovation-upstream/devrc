# opencode-Specific Addendum

Everything above this line is the shared, tool-agnostic ruleset (`PRINCIPLES.md` +
`RULES.md`). This section is appended ONLY to the opencode build of the global
instruction file (`~/.config/opencode/AGENTS.md`) and covers the ways opencode
differs from Claude Code.

🔴 **Why this file is CONCATENATED rather than `@`-imported.** opencode does NOT
expand `@`-imports inside `AGENTS.md`/`CLAUDE.md` — measured on v1.18.4, NOT re-derived
since (it needs a live model call), with an
agent that had every tool denied, so no file read was possible: an imported
passphrase came back `NONE`, the same content inline came back verbatim. Since
`~/.claude/CLAUDE.md` is ~1.5 KB consisting almost entirely of `@PRINCIPLES.md`
and `@RULES.md` import lines, opencode reading it would receive **none** of the
32 KB of actual rules. A project `AGENTS.md` also SUPPRESSES `CLAUDE.md`
(first match wins), so this generated file is what opencode actually sees.
It is assembled by `home-manager` at switch time from the same
`claude/PRINCIPLES.md` and `claude/RULES.md` that Claude Code reads, so the two
tools cannot drift. **Do not replace this concatenation with imports.**

## Use the native tools, not the shell

opencode ships first-class `read` / `glob` / `grep` tools. Use them. Shelling
out to `cd`, `ls`, `cat`, `find`, `head`, `tail`, `wc -l` for file navigation is
slower, costs more tokens, loses structured output, and on this host trips the
bash guard.

| Want to… | Use | NOT |
|---|---|---|
| read a file | `read` (with offset/limit) | `cat` / `head` / `tail` |
| find files by name | `glob` | `find` / `ls -R` |
| search file contents | `grep` | `rg` / `grep` via bash |
| see what is in a dir | `glob` on `<dir>/*` | `ls` |

There is **no `list` tool** on opencode 1.18.16 — verified against the resolved
tool map, which contains exactly `bash, edit, glob, grep, invalid, question,
read, skill, task, todowrite, webfetch, write`. Use `glob` to enumerate a
directory.

`cd` is never needed — every tool takes an absolute path, and the bash tool's
working directory does not persist the way you expect. **Pass absolute paths.**

Reserve the bash tool for things that genuinely execute: `git`, `kubectl`,
`flux`, `nix`, build/test commands.

## Kubeconfig handles are EXPORTED — use them verbatim

The `shell.env` plugin (`~/.config/opencode/plugin/env.js`) injects these into
every bash tool invocation. opencode's bash tool does **not** run zsh startup
files, which is why these have to come from a plugin at all — and why, before
the plugin existed, `KUBECONFIG=` got hand-retyped 169 times across 3 different
spellings.

| Handle | Value |
|---|---|
| `$HOMELAB` | the `homelab-talos` repo root |
| `$KC_HOMELAB` | homelab (Talos) kubeconfig |
| `$KC_WORKBENCH` | workbench (NixOS k3s) kubeconfig |
| `$KC_PROD` | production (Hetzner k0s) kubeconfig |

🔴 **Use the handle verbatim.** Never construct, interpolate, or relative-path a
kubeconfig — not `./homelab-kubeconfig`, not `$HOME/workspace/.../kubeconfig`,
not `$HOMELAB/homelab-kubeconfig`. A constructed path is the failure mode where
a command silently runs against the WRONG CLUSTER, or against no cluster at all
while looking like it worked.

```bash
KUBECONFIG=$KC_HOMELAB kubectl get pods -A      # correct
KUBECONFIG=./homelab-kubeconfig kubectl get pods # WRONG — depends on cwd
```

## Delegate — don't do it inline

Two subagents exist and are cheaper than doing the work in the primary context:

- **`nav`** — bulk file navigation and search. It has `read`/`glob`/`grep` and
  **no shell at all** (and no `skill`/`task`, so it carries none of the skill
  catalogue), runs on the cheap model at temperature 0, and returns absolute
  paths plus minimal excerpts. Send it anything shaped like "where is X",
  "which files do Y", "find all callers of Z". Do not run a sprawling
  multi-step search yourself.
- **`k8s`** — cluster work across homelab / workbench / production. It knows the
  three clusters, the handles, and the read-before-mutate discipline.

- **`review`** — adversarial reviewer. It reads the diff and history and hunts
  for vacuous tests, unreachable guards, stale comments and uncommitted-file
  dependencies. It has `read`/`glob`/`grep` plus a read-only shell (`git
  diff`/`log`/`show`/`status`, in both the bare and `git -C <path> …` forms, and
  `rg`) — so it cannot run the test suite, and will tell you so. Use it before
  committing, pushing or merging.

Three, and deliberately no more: every additional subagent permanently enlarges
the primary agent's `task` tool description on **every** request.

## 🔴 Committing to trunk in `homelab-talos` IS deploying

`~/workspace/homelab-talos` is reconciled from `trunk` by Flux. A commit that
lands on `trunk` goes to a LIVE cluster — there is no staging step and no review
gate. So:

- Verify **before** the commit, never after.
- For anything risky use the safe sequence:
  `flux suspend` → commit → `flux reconcile` → verify → `flux resume`.
- "HelmRelease reconciled" / "pod Running" / "rollout complete" is **not**
  verification. Hit the real endpoint or the real click path.
- `kubectl rollout restart` is NOT reverted by Flux (it only bumps a
  pod-template annotation), so it is a safe way to restart a Flux-managed
  workload.

This is the one repo where the standard feature-branch/PR default does not
apply — because its own `CLAUDE.md` says so. That written statement in the
target repo is what makes the exception apply; it is not self-declarable, and it
does not generalise to any other repo.
