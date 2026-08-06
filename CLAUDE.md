# devrc — NixOS / home-manager dotfiles

Personal dev-environment config (zsh, tmux, neovim, i3, scripts) for the workbench + laptop NixOS hosts. Managed by **home-manager via a flake**.

## Shell environment (read before running commands)
- **Bash tool runs NON-interactive zsh** (`zsh -c`) → sources `.zshenv` only, NOT `.zshrc`/`initContent`. Shell tweaks Claude needs at runtime must go in home-manager `programs.zsh.envExtra` (→ `.zshenv`). `unsetopt nomatch` lives there so unmatched globs pass through literally instead of aborting with "no matches found".
- zsh reserves `status` — use `rc=`/`out=`, never `status=$(...)`.
- Use `git -C <path>` and absolute paths — never `cd <repo> && …` (triggers approval prompts and can run untrusted hooks).
- **Canonical env handles are pre-exported in `.zshenv`** (via `envExtra`) so you don't re-`cd`/`export` on every call — non-interactive `zsh -c` doesn't persist state. Use them directly (each is existence-guarded, absent on hosts without that checkout): repo roots `$DEVRC` `$HOMELAB` `$DATAPACKET` `$CIVITAI` (e.g. `git -C $DATAPACKET status`); kubeconfigs `$KC_HOMELAB` `$KC_WORKBENCH` `$KC_DPPROD` `$KC_NEBULA` (e.g. `KUBECONFIG=$KC_DPPROD kubectl get pods`). There is deliberately **no default `KUBECONFIG`** — pick a cluster per command so a bare `kubectl` can't hit prod.

## Applying changes
- **Deploy to BOTH hosts (after merge):** `scripts/ship.sh` — converges workbench + laptop to `origin/main` (fetch → `merge --ff-only` → `home-manager switch` → verify HEAD==origin/main) in one idempotent call. **It never stashes** (the stash is repo-GLOBAL and would reach into other worktrees — see RULES.md "Git Workflow"); a host it cannot fast-forward, or one with a conflicted/mid-merge tree, is **skipped and left exactly as found**, with the blocking files named. Use this instead of hand-running the per-host dance; `--no-laptop`/`--no-local` to scope. Covers home-manager only (not `sudo nixos-rebuild`).
- **Apply config (single host):** `home-manager switch --flake ~/workspace/devrc --impure` (allowlisted). This is how you validate a Nix edit end-to-end.
- 🔴 **Merged ≠ deployed — `git pull` changes NOTHING that nix manages.** Every `home.file` target (`~/.claude/{RULES.md,commands/,skills/}`, `~/.config/browser-bridge/server.py`, `~/.local/share/browser-bridge-ext/`, the hooks) only changes on a **`home-manager switch`**. That git-immunity is deliberate — a concurrent session's `git checkout` cannot swap deployed code out mid-verification — and is exactly what makes it easy to trip on. The full sequence is **merge → pull → `switch` → restart the consumer** (`systemctl --user restart <svc>`, or a FULL Brave restart for an extension); skip the last two and you will verify the OLD artifact. Whether a given path is live-or-stale is answered by `readlink -f` only (RULES.md → "Shell & Tooling Gotchas"), never by diffing it against the repo.
- **Quick syntax check** before switching: `nix-instantiate --parse <file>.nix >/dev/null`.
- **NEVER `sudo nixos-rebuild` from Claude** — can't sudo non-interactively. System-level changes must be staged as an apply script for the user to run (see the `laptop` skill's `stage-system` pattern). home-manager (user-level) is fine.

## Git discipline
Portable rules (`git add -A`, `reset --hard`, `stash`, worktree isolation, feature-branches,
base-clone re-sync, stranded docs) are in **`claude/RULES.md` → "Git Workflow"** — read them
there. Only what's specific to this repo, where a working tree is also a **deploy target**:

- 🔴 **Never commit to `main` in EITHER host checkout** (`~/workspace/devrc`, workbench *or*
  laptop). `ship.sh` converges with `merge --ff-only`, so a diverged host is **skipped and
  left as found** — it then silently stops receiving every future change while still looking
  healthy. 2026-08-06: two un-pushed commits on the workbench blocked it for hours, and the
  regrowth timer would have fired on 08-11 running the very bug the undelivered commit fixed.
  **Read every per-host line of `ship.sh`, not the final verdict** — one skip hides among
  greens, and it prints its own rc legend on failure.
- **Recovering a diverged host** — preserve, verify, *then* move the pointer:
  `git branch <topic> HEAD && git push -u origin <topic>` on that host → confirm the shas are
  on origin **from a different host** → `git reset --keep origin/main` (`--keep` refuses
  rather than destroys; never `--hard`). Open a PR for `<topic>`: rescued commits have never
  been gated against the tree they now land in.
- **A failed switch is usually a pre-existing FOREIGN file, not a nix error.** `home.file`
  won't clobber a real file it doesn't manage and `force = true` does not override that. Tell:
  read-only, 1969 mtime (an old store copy). Look at it, copy it aside, remove, re-switch.

## Server / headless mode
- `~/.server-mode` marker toggles graphical bits: `headless-mode` (disables dunst/espanso) vs `graphical-mode` (re-enables), both run a home-manager switch. A host may be in server mode — check for the marker before assuming a GUI.

## Layout
- `nix/` — home-manager modules (`programs/zsh`, tmux, nvim, i3, …). `flake.nix` at root.
- `scripts/` — utility scripts (prefer extending these over re-typing inline bash / heredocs).
- `claude/` — **global Claude Code config, managed declaratively**: `RULES.md` (+ `RULES-ARCHIVE.md`), `PRINCIPLES.md`, all slash-commands under `claude/commands/`, all skills under `claude/skills/`. `nix/home.nix` symlinks these into `~/.claude/`, so both hosts stay in sync. **Edit them HERE + `home-manager switch`/`ship.sh` — NOT `~/.claude/*`** (read-only nix-store symlinks). Two deliberate MUTABLE exceptions: the `browser` skill (`mkOutOfStoreSymlink` onto `scripts/browser-bridge/`, so edits apply with no switch) and `~/.claude/CLAUDE.md` (genuinely per-host, unreferenced by `home.nix`). New-host caveat: `home.file.force` does NOT clobber a pre-existing *foreign* `~/.claude/RULES.md` or `commands/*.md` — `rm` those once before the first switch. Also managed: `~/.claude/hooks/bash-guard.py` (from `scripts/claude-hooks/`; `dropStaleClaudeHooks` displaces a hand-placed regular file, `force` alone cannot).
- `.zshrc`, `.tmux.conf` etc. are read by the nix modules — read with offset/limit, they're large.

### Subsystems — operate each via its SKILL, not from here
A skill body costs ZERO context until its trigger fires; a paraphrase here costs every session.
So these are pointers. **Load the skill before touching the subsystem.** The full pre-split prose
is preserved verbatim in `docs/LAYOUT.md` (not auto-loaded, and stale by design).

| path | skill | what it is |
|---|---|---|
| `scripts/browser-bridge/` | `browser` | drive Zach's REAL logged-in Brave (loopback server + MV3 extension) |
| `scripts/dl-router/` | `dl-router` | route browser downloads into a private media library by PAGE CONTEXT |
| `scripts/collector/`, `scripts/validation/`, `scripts/session-analysis/` | `activity` | personal activity telemetry → ClickHouse → Grafana + the insights/session-analysis reports |
| `scripts/initiatives/` | `initiatives` | durable cross-repo initiative ledger + viewer + router + assistant |
| `scripts/mail-actions/` | `mailbox` | email-automation layer over the self-hosted inbox (**separate from activity telemetry**) |
| `scripts/repo-cos/` | `repo-cos` | weekly repo "chief-of-staff" proposal digest + reply-driven exclusions |
| `nix/i3/`, `nix/graphical.nix`, `scripts/bar-status-poll` | `bar` | i3 + i3status-rust bar, count blocks, dunst toasts |

Repo-level facts that are NOT in any skill — they live here on purpose:
- 🔴 **A NEW file must be `git add`ed or the flake silently omits it from the deploy.** Applies to every managed path: a new command, skill, extension file, hook or test. The switch succeeds and the file simply is not there.
- **Graphical/agent-facing layer is home-manager, never `/etc/nixos`** (migrated PR #74; the old `i3config.nix`/`i3blocks.nix`/`i3blocks-scripts` are RETIRED). Cutover gotcha: finish with `sudo systemctl restart display-manager`, NOT `i3-msg restart`.
- **`scripts/agent-ops` — the read-only "mission control" dashboard** (`$mod+i` / tmux `prefix+A` / the ▦ bar button): blocked-on-me, live Claude-in-tmux runs (task from pane title + scratch codename), open PRs (`gh pr list`), momentum (`initiative-scan --json`), health. ⚠ fuzzyclaw (`~/.tmux/tasks/*.json`) is UNTRUSTED as a data source.
- 🔴 **Zach works ENTIRELY via agents → modernization targets this agent-facing layer, NOT interactive-CLI ricing.**
- **Two always-on docs have enforced byte ceilings**, because both load on every session: `scripts/browser-bridge/SKILL.md` (gated by `scripts/browser-bridge/tests/test_skill_size.py`) and `claude/RULES.md` (gated by `scripts/tests/test_rules_size.py`). Each test OWNS its constants and prints an eviction playbook on failure — **read the numbers there, never restate them.** Any addition needs an eviction in the SAME commit.
- **CI gates both suites**: `nix build .#checks.x86_64-linux.pytests` and `.#checks.x86_64-linux.nodetests`. Both assert collected-test FLOORS and parse structured output rather than reading an exit code — `node --test <dir>` silently yields a bogus `# tests 1`, and a pytest suite can collect 0 with a zero exit.
- 🔴 **This repo is PUBLIC.** Never commit a real media-library path, directory name, filename, route log, or a real third-party hostname used as an example.

## Conventions
- Git: see **Git discipline** above (and `claude/RULES.md` → "Git Workflow" for the portable rules).
- Land work on `main` via PR, then `scripts/ship.sh` — never `git pull` + switch per host by hand.
