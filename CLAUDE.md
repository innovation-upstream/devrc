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
- 🔴 **Merged ≠ deployed — `git pull` changes NOTHING that nix manages.** Every `home.file` target (`~/.claude/{RULES.md,skills/}`, `~/.config/browser-bridge/server.py`, `~/.local/share/browser-bridge-ext/`, the hooks) only changes on a **`home-manager switch`**. That git-immunity is deliberate — a concurrent session's `git checkout` cannot swap deployed code out mid-verification — and is exactly what makes it easy to trip on. The full sequence is **merge → pull → `switch` → restart the consumer** (`systemctl --user restart <svc>`, or a FULL Brave restart for an extension); skip the last two and you will verify the OLD artifact. Whether a given path is live-or-stale is answered by `readlink -f` only (RULES.md → "Shell & Tooling Gotchas"), never by diffing it against the repo.
- **Quick syntax check** before switching: `nix-instantiate --parse <file>.nix >/dev/null`.
- **NEVER `sudo nixos-rebuild` from Claude** — can't sudo non-interactively. Stage a system-level change instead: write an executable `nix/system/apply-<change>.sh` that makes the `/etc/nixos` edit and runs the rebuild, hand the user the exact path to run under `sudo`, and say plainly that it is staged-not-applied. `nix/system/` already holds nine of these (`apply-nebula-443.sh`, `apply-perf-tuning-2026-07-30.sh`, …) — copy their shape. home-manager (user-level) is fine.

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
  greens, and it prints its own rc legend on failure. Recurred 2026-08-09 (three un-pushed
  commits, rescued as #366) — because **nothing runs `ship.sh` on a schedule**, so the only
  detector was a human shipping something unrelated. `scripts/drift-check.sh` is the passive
  deadman for exactly this: READ-ONLY (fetch + report, never fixes), same rc vocabulary as
  `ship.sh`, run it any time by hand or via the `drift-check` systemd-user timer.
- 🔴 **Git parity is not host parity — `drift-check.sh` reports both.** A host can be
  byte-identical to `origin/main` while nothing home-manager deploys actually resolves: on
  2026-08-11 every skill on the laptop was a dangling symlink into a GC'd `/nix/store` path
  (46 of 139 managed links) with a perfectly clean checkout. So the checker also reports
  **dangling managed symlinks** (rc 14) and **host divergence** in `settings.json`
  top-level key names + `enabledPlugins` (rc 15). It always prints links EXAMINED beside
  links dangling — a bare "0 dangling" from a scan that walked nothing is the failure, not
  the all-clear — and the cross-host comparison says NOT COMPARED unless it got facts from
  both machines. 🔴 **`settings.json` is per-host and unmanaged by design**, so the key-set
  half carries an **explicitly enumerated** allowlist — `theme`, `voice`, `effortLevel`,
  each with its reason in the source — printed as `IGNORED`, not counted as drift. It is an
  enumeration and not a pattern: an unknown key is drift by default, and the list is not
  env-overridable. `permissions` is deliberately NOT on it — a host without a `permissions`
  block prompts for what the other allows, which is a real gap. Close it with
  `scripts/sync-claude-permissions.py` (idempotent, additive, **curated** — never copy the
  other host's block wholesale; #380 exists because that file accretes junk).
  🔴 **Git parity is not SOURCE parity either — rc 17.** Some `nix/pkgs/**`
  derivations build from a **local working tree of another repo** (`${workspace}/…`:
  today `homelab-talos` and `tmux-fuzzyclaw`), and **nothing converges those** —
  `ship.sh` is scoped to `~/workspace/devrc`. On 2026-08-14 the laptop's
  homelab-talos was 24 commits behind, so it shipped a `clawgatectl` with no
  `task status`, labelled `0.7.95` by devrc's version literal; the command printed
  help and **exited 0**, and drift-check was green throughout. Both halves are now
  closed: `clawgatectl.nix` **reads its version out of the Go source it compiles**
  (never a literal — an unparseable source means no binary, not a wrong label), and
  drift-check reports each package's source currency per host. The covered set is
  **derived from `nix/pkgs/` at scan time and pinned two-way by a test**, so a third
  such package is covered automatically. `absent` / `fetch failed` / `detached` are
  reported as **UNMEASURED**, never folded into a clean count.
  🔴 **UNMEASURED is not forever — rc 18.** Setting no code was right per run and
  wrong forever: a scope that can never be evaluated escalated NEVER, so the run
  read as clean while rc 17 was structurally unable to fire for it. Measured
  2026-08-18: `tmux-fuzzyclaw` on a local branch with **no upstream**,
  `unmeasured=1`, rc 0 — concealing a genuinely divergent build between the two
  hosts. It now carries the rc 13 ladder — reported every run, escalated only
  after N **consecutive** runs, **per (host, scope)**, reset the moment it
  measures. The reasons are **not** one hazard: `no upstream` / an unparseable
  count are structural (`DRIFT_UNMEASURED_ESCALATE`, default 4 ≈ 24h), a failed
  `fetch` is plausibly transient so it gets a longer ladder
  (`DRIFT_UNMEASURED_FETCH_ESCALATE`, default 12 ≈ 3 days), and **`repo ABSENT`
  never escalates at any count** — a host without the checkout is a state
  `clawgatectl.nix` deliberately supports. The block prints
  `hosts-reporting=/scopes=/unmeasured=/escalated=` and **withholds that summary
  entirely** when either count is zero: a ladder over no scopes is not a clean
  ladder.
  🔴 **The unit is the package's own `srcDir` SUBTREE, not the repo** — escalating
  on the repo made rc 17 a permanently-red gate. Measured: the workbench sat 18
  commits behind `origin/trunk` with **0** of them touching `containers/clawgate`,
  and over 14 days that repo took 98 commits of which only 32 could reach any
  built artefact. So the verdict is a pathspec-limited count against the branch's
  **own** upstream (never a hardcoded `main`/`trunk`), the repo-wide numbers are
  printed beside it as information, and the cross-host comparison diffs **subtree
  tree OIDs** rather than repo HEADs.
  🔴 **rc 16 is NOT drift** — it is the fuzzyclaw phase-2 gate reporting that zero rows
  still take their age from fuzzyclaw alone, i.e. the readers can now be deleted; the
  final line says `ACTIONABLE (not drift)` and it is the least severe code, so it can
  only ever be the verdict on an otherwise-clean run — which is also printed, since
  "no drift" and "a cleanup is possible" are independent claims. It is a **success** to
  systemd (`SuccessExitStatus = 16`): it stays set until the cleanup happens, so failing
  the unit on it would fire the DND-defeating failure toast 4×/day forever. Every way
  that gate can fail to measure prints `COULD NOT MEASURE` with a reason and sets **no**
  rc — a `0` there is never a pass, because the answer it hands over is a deletion. That
  last claim is **enforced, not asserted**: `test_drift_check.py::test_the_phase2_reason_
  token_ledger_is_pinned_to_the_fields_read` pins the emitted reason-token set against
  the report fields `lib/drift_phase2.py` reads, so consulting a new field without giving
  its absence a token fails the suite. It exists because the prose version was false for
  three days — `summary.age_sources` was read with no presence check, and a report
  missing it printed a `READY` byte-identical to a real one.
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
- `claude/` — **global Claude Code config, managed declaratively**: `RULES.md` (+ `RULES-ARCHIVE.md`), `PRINCIPLES.md`, and **every skill under `claude/skills/<name>/SKILL.md`** (+ its `reference/`, which ships too). `nix/home.nix` symlinks these into `~/.claude/`, so both hosts stay in sync. **Edit them HERE + `home-manager switch`/`ship.sh` — NOT `~/.claude/*`** (read-only nix-store symlinks). 🔴 **`claude/commands/` NO LONGER EXISTS** — upstream merged custom commands into skills, so all 17 migrated to `claude/skills/` (a skill still gives you `/<name>`, and now also auto-fires on its description). Deliberate MUTABLE exceptions: the `browser` + `dl-router` skills (`mkOutOfStoreSymlink` onto `scripts/`, edits apply with no switch), `close-the-loop`'s `STATE.md`/`ARCHIVE.md` (sourced from `claudedocs/close-the-loop/` — the skill WRITES to them every run, so they must not be store symlinks), and `~/.claude/CLAUDE.md` (genuinely per-host, unreferenced by `home.nix`). New-host caveat: `home.file.force` does NOT clobber a pre-existing *foreign* `~/.claude/RULES.md` or `skills/*` — `rm` those once before the first switch. Also managed: `~/.claude/hooks/bash-guard.py` (from `scripts/claude-hooks/`; `dropStaleClaudeHooks` displaces a hand-placed regular file, `force` alone cannot).
- 🔴 **The skill LISTING is a third always-on cost, and it fails SILENTLY.** Every skill's name + `description` (+ `when_to_use`) loads on **every session** under a budget of **1% of the context window** (per-entry cap 1,536 chars); on overflow Claude Code DROPS descriptions starting with the skills you invoke least — stripping the very trigger keywords that make a skill auto-fire, with no error. So a description is **routing surface, not documentation**: key use case first, then the literal phrases Zach says, then disambiguation from a sibling skill. Narrative goes in the body (0 until invoked). `/doctor` estimates the listing cost.
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
- 🔴 **A NEW file must be `git add`ed or the flake silently omits it from the deploy.** Applies to every managed path: a new skill, a new `reference/*.md` inside one, an extension file, a hook or a test. The switch succeeds and the file simply is not there.
- **Graphical/agent-facing layer is home-manager, never `/etc/nixos`** (migrated PR #74; the old `i3config.nix`/`i3blocks.nix`/`i3blocks-scripts` are RETIRED). Cutover gotcha: finish with `sudo systemctl restart display-manager`, NOT `i3-msg restart`.
- 🔴 **`scripts/agent-ops` — the "mission control" TUI — is RETIRED, and so are all three of its launchers** (`$mod+i`, tmux `prefix+A`, the ▦ bar button's click). Every panel has an owner: blocked-on-me + live runs → `session-manager`, open PRs + cluster health + **local systemd health** → `standup`, momentum → `/initiative-scan`, the mail/clawgate counts → bar pills. The one part worth keeping, its `/proc` Claude-session detector, is now `scripts/lib/claude_sessions.py` — shared, and deployed beside the ▦ bar pill (`i3status-claude-runs`), which remains as an indicator. ⚠ fuzzyclaw (`~/.tmux/tasks/*.json`) is UNTRUSTED as a data source.
- 🔴 **Zach works ENTIRELY via agents → modernization targets this agent-facing layer, NOT interactive-CLI ricing.**
- **Two always-on docs have enforced byte ceilings**, because both load on every session: `scripts/browser-bridge/SKILL.md` (gated by `scripts/browser-bridge/tests/test_skill_size.py`) and `claude/RULES.md` (gated by `scripts/tests/test_rules_size.py`). Each test OWNS its constants and prints an eviction playbook on failure — **read the numbers there, never restate them.** Any addition needs an eviction in the SAME commit.
- **Run the gate with `scripts/gate.sh`** (`--tier pytest|node|both`, `--set hermetic|all`). It sends the full output to a LOG FILE and prints only a bounded summary, so there is no reason to pipe it — and **its exit status is authoritative**. It also cross-checks that status against the runners' own `RESULT:` line and exits **90 = could-not-vouch** when they disagree, when a run printed no verdict, or when `panic: test timed out` appears. 90 is not "the tests failed"; it means read the log.
- **The runners' verdict line carries their exit code** (`RESULT: FAIL (exit=1)`), emitted from one writer behind an EXIT trap, so it survives a pipe and a killed run still says so. Historically the status was destroyed by `… | tail; echo "rc=$?"` — four agents reported `exit 0` over `RESULT: FAIL` on 2026-08-11 — which is why counting `PASSED`/`FAILED` lines used to be mandatory. Still a fine cross-check; no longer the only thing you can trust.
- **CI gates both suites**: `nix build .#checks.x86_64-linux.pytests` and `.#checks.x86_64-linux.nodetests`. Both assert collected-test FLOORS and parse structured output rather than reading an exit code — `node --test <dir>` silently yields a bogus `# tests 1`, and a pytest suite can collect 0 with a zero exit.
- 🔴 **There is no hand-written test TOTAL any more.** `run-tests.sh` carries a **per-target** floor table (`TARGET_FLOORS`, pinned two-way against the target list) and derives the global floor as their sum. The old single `MIN_TESTS` literal was base-dependent and took **eleven values across eight PRs in one day**; `rerere` replayed a resolution from a different merge and silently wrote a four-way total onto a two-way tree. A floor is now a function of the current measurement — `m - min(50, max(1, m/20))` — and the gate PRINTS the exact replacement number when one drifts. Resolve a conflict here by re-running the gate and copying what it says, never by arithmetic on the two sides.
- 🔴 **This repo is PUBLIC.** Never commit a real media-library path, directory name, filename, route log, or a real third-party hostname used as an example. **Nor CAPTURED TEXT — anyone's message bodies, prompts, transcripts or chat content, or a model's summaries of them — however it arrives** (an eval capture, a fixture, a debug dump). A test needs the SHAPE; regenerate it synthetic. Gated for JSON/JSONL/JSONC by `scripts/tests/test_no_captured_text.py` and for `.html`/`.txt` by `scripts/tests/test_no_captured_markup.py`; each owns its ledgers, thresholds, pinned allowlist and blind spots — read them there, never restate them. 🔴 All four content gates (these two plus the IP and hostname ones) read `git ls-files` and are **blind to git history** — see `SECRETS.md` → "Dead credentials in reachable history" before treating a green run as "the repo is clean".

## Conventions
- Git: see **Git discipline** above (and `claude/RULES.md` → "Git Workflow" for the portable rules).
- Land work on `main` via PR, then `scripts/ship.sh` — never `git pull` + switch per host by hand.
