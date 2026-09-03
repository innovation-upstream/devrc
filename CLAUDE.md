# devrc — NixOS / home-manager dotfiles

Personal dev-environment config (zsh, tmux, neovim, i3, scripts) for the workbench + laptop NixOS hosts. Managed by **home-manager via a flake**.

## Shell environment (read before running commands)
- **Bash tool runs NON-interactive zsh** (`zsh -c`) → sources `.zshenv` only, NOT `.zshrc`/`initContent`. Shell tweaks Claude needs at runtime must go in home-manager `programs.zsh.envExtra` (→ `.zshenv`). `unsetopt nomatch` lives there so unmatched globs pass through literally instead of aborting with "no matches found".
- zsh reserves `status` — use `rc=`/`out=`, never `status=$(...)`.
- Use `git -C <path>` and absolute paths — never `cd <repo> && …` (triggers approval prompts and can run untrusted hooks).
- **Canonical env handles are pre-exported in `.zshenv`** (via `envExtra`) so you don't re-`cd`/`export` on every call — non-interactive `zsh -c` doesn't persist state. Use them directly (each is existence-guarded, absent on hosts without that checkout): repo roots `$DEVRC` `$HOMELAB` `$DATAPACKET` `$CIVITAI` (e.g. `git -C $DATAPACKET status`); kubeconfigs `$KC_HOMELAB` `$KC_WORKBENCH` `$KC_DPPROD` `$KC_NEBULA` (e.g. `KUBECONFIG=$KC_DPPROD kubectl get pods`). There is deliberately **no default `KUBECONFIG`** — pick a cluster per command so a bare `kubectl` can't hit prod.

## Applying changes
- **Deploy to BOTH hosts (after merge):** `scripts/ship.sh` — converges workbench + laptop to `origin/main` (fetch → `merge --ff-only` → `home-manager switch` → verify HEAD==origin/main) in one idempotent call. **It never stashes** (the stash is repo-GLOBAL and would reach into other worktrees — see RULES.md "Git Workflow"); a host it cannot fast-forward, or one with a conflicted/mid-merge tree, is **skipped and left exactly as found**, with the blocking files named. Use this instead of hand-running the per-host dance; `--no-laptop`/`--no-local` to scope. Covers home-manager only (not `sudo nixos-rebuild`). 🔴 **Its verdict is now a claim about the two hosts agreeing on ONE sha** (rc 19 when they don't — origin/main can move between the two legs' fetches, and every per-host check stays green while it does), and **a run that ships a change to `ship.sh` itself RE-EXECS the new copy** before the remote leg (rc 20 if it cannot), because the CONVERGE payload is expanded before the fast-forward. A one-host run says `cross-host agreement NOT COMPARED` rather than implying two.
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
  🔴 **rc 22 — a host's deployed `skillOverrides` disagree with `claude/skill-tiers.json`.**
  A host with NO overrides prints **NOT ADOPTED and sets no rc**: the tier mechanism
  shipped applied to zero hosts on purpose (nothing is being truncated today), and
  counting an unapplied host as drift would have made this arm red on every run from
  the day it landed. rc 22 is **adopted-then-drifted** only. It ranks just under rc 10
  because a BEHIND host carries a stale ledger — ship it first, then re-run
  `scripts/sync-skill-tiers.py`. The expectation is read through the ledger's own
  parser, never a second sed; a ledger it cannot read prints COULD NOT MEASURE and
  sets no rc, because an empty expectation would make every host look compliant.
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
- `claude/` — **global Claude Code config, managed declaratively**: `RULES.md` (+ `RULES-ARCHIVE.md`), `PRINCIPLES.md`, and **every skill under `claude/skills/<name>/SKILL.md`** (+ its `reference/`, which ships too). `nix/home.nix` symlinks these into `~/.claude/`, so both hosts stay in sync. **Edit them HERE + `home-manager switch`/`ship.sh` — NOT `~/.claude/*`** (read-only nix-store symlinks). 🔴 **`claude/commands/` NO LONGER EXISTS** — upstream merged custom commands into skills, so all 17 migrated to `claude/skills/` (a skill still gives you `/<name>`, and now also auto-fires on its description). **opencode commands are auto-generated** from these skills by `scripts/opencode/generate-commands.py` (nix derivation `opencodeCommands` in `nix/home.nix`), deployed to `~/.config/opencode/commands/`. This makes every skill show as `/<name>` in opencode's TUI autocomplete (source="command" with hints, instead of source="skill" with empty hints). Deliberate MUTABLE exceptions: the `browser` + `dl-router` skills (`mkOutOfStoreSymlink` onto `scripts/`, edits apply with no switch), `close-the-loop`'s `STATE.md`/`ARCHIVE.md` (sourced from `claudedocs/close-the-loop/` — the skill WRITES to them every run, so they must not be store symlinks), and `~/.claude/CLAUDE.md` (genuinely per-host, unreferenced by `home.nix`). New-host caveat: `home.file.force` does NOT clobber a pre-existing *foreign* `~/.claude/RULES.md` or `skills/*` — `rm` those once before the first switch. Also managed: `~/.claude/hooks/bash-guard.py` (from `scripts/claude-hooks/`; `dropStaleClaudeHooks` displaces a hand-placed regular file, `force` alone cannot).
- **`reference/` vs `flows/` inside a skill**: `reference/` holds durable FACTS you verify
  against; `flows/` (sibling dir, same `cp -R` deploy, no nix change needed) holds PROCEDURES you
  execute step by step. 🔴 A `flows/` file does **not** auto-fire the way a skill `description`
  does — something must NAME it, so give each one a router (a SKILL.md table row, and where it
  matters a hook that names the path in its message; see `clawgate/flows/task-authoring.md`).
- 🔴 **The skill LISTING is a third always-on cost, and it fails SILENTLY.** Every skill's name + `description` (+ `when_to_use`) loads on **every session** under a budget of **1% of the context window** (per-entry cap 1,536 chars); on overflow Claude Code DROPS descriptions starting with the skills you invoke least — stripping the very trigger keywords that make a skill auto-fire, with no error. So a description is **routing surface, not documentation**: key use case first, then the literal phrases Zach says, then disambiguation from a sibling skill. Narrative goes in the body (0 until invoked). 🔴 **Measured 2026-08-23: the listing does NOT fit a 200k context and cannot be made to — it fits 1M.** The TOTAL is now ratcheted by `scripts/tests/test_skill_descriptions.py`, which owns the constants, the measurement, the break-even argument and the eviction playbook — **read them there, never restate them.** Any addition needs an eviction in the SAME commit; closing the remaining gap means retiring or merging skills, or disabling the cloudflare plugin (a quarter of the total, and not devrc's to edit). ⚠ `claude doctor` (the CLI) reports no listing cost; the interactive `/doctor` reportedly does — unverified.
  🔴 **The cost is now per-skill opt-in, and ADDING A SKILL MEANS TIERING IT.** `claude/skill-tiers.json` assigns every skill tier A (full description) or tier B (`name-only`, ~12 chars, still `/name`-invocable, no routing prose). `scripts/tests/test_skill_tiers.py` pins it **two-way** — a skill with no entry, or an entry naming no skill, fails the suite — and owns the tier-A ratchet, the real charging formula and the playbook: **read them there, never restate them.** Tier by whether the skill must **AUTO-FIRE from a symptom Zach describes**, never by an invocation counter (`dl-router` runs as a live systemd service and `adoption-scan` has 20,494 tool events; Claude Code's counter says 0 for both). 🔴 **The ledger is applied to NO host by default** — `~/.claude/settings.json` is per-host and unmanaged, so `scripts/sync-skill-tiers.py` (dry-run unless `--apply`) is an operator act, and `drift-check.sh` reports an unapplied host as **NOT ADOPTED, not drift**. Full argument: `claudedocs/proposal-skill-listing-tiers.md`.
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
| `scripts/check-clickup-addressed/` | `check-clickup-addressed` | did the work on a ClickUp ticket actually happen — reads session transcripts for completion signals (migrated out of datapacket-talos 2026-08-22) |
| `nix/i3/`, `nix/graphical.nix`, `scripts/bar-status-poll` | `bar` | i3 + i3status-rust bar, count blocks, dunst toasts |
| `scripts/opencode/` | `opencode` | dispatch a task to the headless opencode agent (`opencode-dispatch`), + its config/agents/guard plugin |

Repo-level facts that are NOT in any skill — they live here on purpose:
- 🔴 **A NEW file must be `git add`ed or the flake silently omits it from the deploy.** Applies to every managed path: a new skill, a new `reference/*.md` inside one, an extension file, a hook or a test. The switch succeeds and the file simply is not there.
- **Graphical/agent-facing layer is home-manager, never `/etc/nixos`** (migrated PR #74; the old `i3config.nix`/`i3blocks.nix`/`i3blocks-scripts` are RETIRED). Cutover gotcha: finish with `sudo systemctl restart display-manager`, NOT `i3-msg restart`.
- 🔴 **`scripts/agent-ops` — the "mission control" TUI — is RETIRED, and so are all three of its launchers** (`$mod+i`, tmux `prefix+A`, the ▦ bar button's click). Every panel has an owner: the clawgate queue + live runs → `session-manager`, open PRs + cluster health + **local systemd health** → `standup`, momentum → `/initiative-scan`, the mail/clawgate counts → bar pills. The one part worth keeping, its `/proc` Claude-session detector, is now `scripts/lib/claude_sessions.py` — shared, and deployed beside the ▦ bar pill (`i3status-claude-runs`), which remains as an indicator. ⚠ fuzzyclaw (`~/.tmux/tasks/*.json`) is UNTRUSTED as a data source.
- 🔴 **Zach works ENTIRELY via agents → modernization targets this agent-facing layer, NOT interactive-CLI ricing.**
- **Several docs have enforced byte ceilings, each gated by its own test that OWNS the constants and prints an eviction playbook on failure** — `claude/RULES.md` (the only always-on one) plus the skill bodies `browser`, `prune-skill`, `session-manager` and `handoff`. 🔴 **Read the numbers in the tests, never restate them, and do not restate the LIST either** — `git grep -l MIN_HEADROOM_BYTES scripts/` answers it. This bullet has twice carried a count that was wrong within a day. Any addition needs an eviction in the SAME commit; raising a ceiling needs the commit message to say which instruction would not fit.
- **Run the gate with `scripts/gate.sh`** (`--tier pytest|node|both`, `--set hermetic|all`). It sends the full output to a LOG FILE and prints only a bounded summary, so there is no reason to pipe it — and **its exit status is authoritative**. It also cross-checks that status against the runners' own `RESULT:` line and exits **90 = could-not-vouch** when they disagree, when a run printed no verdict, or when `panic: test timed out` appears. 90 is not "the tests failed"; it means read the log.
- 🔴 **BUILD THE TWO `nix` CHECK DERIVATIONS ONE AT A TIME — a combined invocation produces FALSE FAILURES.** `nix build .#checks.x86_64-linux.pytests .#checks.x86_64-linux.nodetests` builds both concurrently, and the tests that shell out to nested `nix` then contend on the store. MEASURED 2026-08-30 on one tree: the combined call reported **2 failures** — `SQLite database … is busy` evaluating `nix/home.nix`, and `OperationalError('database is locked')` in dl-router — while the SAME tree, same derivations, run **sequentially**, reported **0**. Load-dependent, so earlier combined runs were green and looked fine. **A combined GREEN is trustworthy** (a contended run fails loudly, it does not fake a pass); **a combined RED is not**, until re-checked one at a time. This cost a near-miss report of "PR #1029 broke the gate", against a diff that touched one test file and could not reach either failure. ⚠ Same run also reproduced the documented `| tail` trap: `nix build … | tail` printed `NIXBUILD_RC=0` for a build that had just failed 45 tests — read the runners' own `RESULT:` lines, never the piped exit code.
- **To run a SUBSET, use the flake devShell — it already carries the gate toolchain:** `nix develop ~/workspace/devrc -c python3 -m pytest <paths> -q` (cwd-independent with absolute paths; MEASURED from the repo root and from `/tmp`, pytest 9.1.1). `gate.sh` has no per-file filter and `run-tests.sh`'s positional is a repo ROOT, not a test selector — but that is a gap in those two entry points, **not** in the repo: the toolchain is there, by another door. 🔴 **`.envrc` is `use opencode`, so a loaded direnv does NOT put pytest on PATH** — and the worktree recipe in `claude/RULES.md` says to copy `.envrc`, which propagates that env into every worktree. A bare `python3 -m pytest` failing with `No module named pytest` therefore means you are in the opencode shell, never that the suite is unrunnable. This bullet exists because three true observations — no `gate.sh` filter, no `run-tests.sh` selector, direnv has no pytest — were read as "no subset mode exists", and an ad-hoc `nix-shell -p` was built instead of opening the door that was already there.
- **The runners' verdict line carries their exit code** (`RESULT: FAIL (exit=1)`), emitted from one writer behind an EXIT trap, so it survives a pipe and a killed run still says so. Historically the status was destroyed by `… | tail; echo "rc=$?"` — four agents reported `exit 0` over `RESULT: FAIL` on 2026-08-11 — which is why counting `PASSED`/`FAILED` lines used to be mandatory. Still a fine cross-check; no longer the only thing you can trust.
- 🔴 **NOTHING BLOCKS A MERGE TODAY — `main` is protected in NAME ONLY. You are the gate.** <!-- merge-gate: other -->
  MEASURED 2026-09-02: `required_status_checks` is **absent from the protection object
  entirely** and `enforce_admins: false`, while `GET /branches/main` still reports
  `protected: true`. A PR merges with both Tekton checks red, or with none posted at all.
  🔴 **DELIBERATE AND CURRENT — not drift, and not yours to "restore".** The operator turned
  it off because the gate was slowing work down; it stays off until the Tekton capacity
  issue is addressed, which a different session owns. 🔴 **That decision is now DECLARED IN
  CODE, and the declaration is load-bearing.** `bp_declared_off_reason()` in
  `scripts/drift-check.sh` names this repo with the reason above, so rc 24 fires on
  DISAGREEMENT rather than on the bare state: declared-off/live-off is printed plainly and
  sets no code (it exited 24 on every 6-hourly fire before that — 5 DND-bypassing toasts in
  3 days), while an UNDECLARED unprotected `main` is still the alarm, unchanged. 🔴 **When
  protection is restored, DELETE that arm in the same change** — a declaration left saying
  "off" over a live gate DISARMS rc 24, which is `drift-check.sh` rc 25 and toasts until it
  is removed. The declaration is deliberately not env-overridable and has no ledger path: a
  run must not be able to excuse itself.
  ⚠ **Tekton still RUNS** — both checks post on a PR head, they just do not gate. That is
  exactly why the marker stays `other`: it records that something runs at merge time, never
  that it blocks. There is still no `.github/workflows`.
  🔴 **So run BOTH tiers yourself before merging** — `scripts/gate.sh --tier both` AND
  `nix build .#checks.x86_64-linux.{pytests,nodetests}` one at a time, on the MERGED tree —
  and name the tier and the base sha in the claim. ⚠ 2026-08-23 measured the OPPOSITE state
  (`contexts` = both checks, `enforce_admins: true`), and earlier that same day `contexts`
  held nodetests ALONE, which collects `*.test.mjs` only — so a Python-only PR could not
  fail it and read `UNSTABLE` with pytests red. **Check the LIST, never that the key
  exists**, and re-measure rather than trusting this paragraph's age:
  `gh api /repos/innovation-upstream/devrc/branches/main/protection --jq .required_status_checks`
  🔴 **WHEN IT IS RESTORED, `enforce_admins: true` leaves no admin override** — if Tekton is
  down or wedged, NOTHING merges.
  The escape hatch, deliberately written down because you will want it under pressure —
  🔴 **and it does NOT round-trip. Read all four steps before you run step 2.** MEASURED
  over three uses on 2026-08-29/30: `DELETE` opens the window and **`PATCH` cannot close
  it**, so two restores failed — one of them inside an EXIT trap that fired exactly as
  designed and still left `main` open, because the untested command was *inside* the
  safety net. `PATCH …/required_status_checks` **404s `Required status checks not
  enabled`**: it updates checks that exist, it cannot recreate a deleted sub-resource.
  Closing the window needs a full `PUT` of the WHOLE protection object.
  ```bash
  R=innovation-upstream/devrc; S=<scratchpad>          # 1. CAPTURE FIRST — without this
  gh api /repos/$R/branches/main/protection --jq '{    #    you cannot restore at all.
    required_status_checks:{strict:.required_status_checks.strict,
      checks:[.required_status_checks.checks[]|{context,app_id}]},
    enforce_admins:.enforce_admins.enabled,
    required_pull_request_reviews, restrictions,
    required_linear_history:.required_linear_history.enabled,
    allow_force_pushes:.allow_force_pushes.enabled,
    allow_deletions:.allow_deletions.enabled,
    block_creations:.block_creations.enabled,
    required_conversation_resolution:.required_conversation_resolution.enabled,
    lock_branch:.lock_branch.enabled,
    allow_fork_syncing:.allow_fork_syncing.enabled}' > $S/restore.json
  gh api -X DELETE /repos/$R/branches/main/protection/required_status_checks   # 2. OPEN
  gh api -X PUT /repos/$R/branches/main/protection --input $S/restore.json     # 3. CLOSE
  gh api /repos/$R/branches/main/protection > $S/after.json                    # 4. READ BACK
  ```
  🔴 **Step 4 is not optional, and step 1 is what makes it possible.** A PARTIAL `PUT`
  succeeds and silently drops every key you omitted — `enforce_admins`, force-push and
  deletion settings included — so `PUT` returning 200 is a claim about the REQUEST, never
  about the protection. Diff `after` against the step-1 capture **key by key** and report
  which keys matched; `restore: OK` printed by your own trap is not evidence. All 11 keys
  are load-bearing: `required_status_checks`, `enforce_admins`,
  `required_pull_request_reviews` and `restrictions` are *required* by the endpoint (the
  last two are legitimately `null` here), and the `app_id` pinning inside `checks` is what
  makes the restored context bind to Tekton rather than to any app that can post the name.
  ⚠ **Not measured, so do not reach for it under pressure:** whether `PUT` with
  `required_status_checks: null` opens the window symmetrically was never tried — the
  `DELETE`/`PUT` asymmetry above is what has actually been run.
  🔴 **The backstop is a DETECTOR, not a restore:** `drift-check.sh` rc 24 reports an
  unprotected `main`, on a timer that repeats every `OnUnitActiveSec=6h` — so detection
  lags by up to a full interval, and only where the deadman is actually wired in
  (`serverMode && enableDriftDeadman`; the timer runs on the workbench). It catches a
  botched restore after the fact; it does not undo one, and it is not a substitute for
  step 4. ⚠ **And it is only armed for a repo whose declared expectation is ON** — while
  the arm above declares this repo's gate off, a botched restore lands in the same cell as
  the intended state and passes silently. That is the price of the declaration, and it is
  why deleting the arm is part of restoring protection rather than a follow-up.
  ⚠ **`strict` is FALSE on purpose.** `strict: true` would force every PR to be up to date
  with `main` before merging — correct in principle, and unworkable here: `main` moved 11+
  times in one session and each move would re-queue a ~20-minute gate for every open PR.
  **So a green check is a claim about the PR's BRANCH, not about the tree the merge
  creates.** Gating the merged tree is still yours to do by hand.
  🔴 **A brand-new check is not an instrument until it has passed ONCE.** `devrc-ci` was
  red on its first **5 of 5** runs — every one on the same test
  (`test_timeout_reaps_the_whole_process_group`), in a file none of those changes touched:
  the reap check asked `os.kill(pid, 0)`, which succeeds on a **zombie**, and a CI step
  container's PID 1 does not reap. Fixed in #722; first green run was `devrc-ci-hkgtf`.
  Read a red check's step log before believing its verdict — the SUMMARY counts nearly
  matched a local run there and pointed at a different test entirely.
  🔴 This line has now been WRONG IN BOTH DIRECTIONS TWICE. It read `CI gates both suites`,
  then `NO AUTOMATED GATE IS RUNNING`, then (2026-08-23) `A MERGE IS BLOCKED BY BOTH
  TIERS` — which stayed after protection was turned off and was false again by 2026-09-02,
  found only because a session re-measured before merging. That is the exact kind of claim
  nobody re-checks: an agent reads it, believes the merge is protected (or that no check
  exists), and skips the run either way. **Re-measure; do not trust this paragraph.**
  🔴 **`error` is not `failure`.** A check posted as `error` with `COULD NOT RUN: <leg>`
  means the gate stopped before that leg reported — a broken gate, not a bad change. Do not
  debug your diff against it.
  🔴 **A run that hits `timeouts.tasks` posts NOTHING** — the `finally` report task never
  runs, so the PR's checks stay `pending` forever: measured on `devrc-ci-nnt6f` and
  `devrc-ci-9p6mf`, `childReferences` `[notify, gate]` only, still `pending` hours later,
  and no re-run clears it; only a fresh push does. ⚠ **This is currently survivable and was
  not** — while the checks were REQUIRED a PR in that state was unsatisfiable and stuck.
  With protection off it merely means you have no CI signal, so the local two-tier run is
  the only evidence there is. It becomes blocking again the moment protection is restored.
  🔴 **But a gate SHIPS IN THIS REPO, and whether it is INSTALLED is not a fact this file can
  state** — `githooks/` (`install.sh`, `pre-push`, `tests-on-push.sh`) is a real blocking
  pre-push test gate, and `scripts/run-tests.sh` treats it as a first-class consumer.
  `git config --get core.hooksPath` is what answers that, **never** `ls .git/hooks` — githooks
  installs by pointing `core.hooksPath` elsewhere, so `.git/hooks` stays sample-only whether or
  not the gate is live. 🔴 **The value is VOLATILE, not merely per-clone — re-measure at the
  moment you act, never earlier in the session.** This line read "uninstalled" until
  2026-08-21, when a push from a worktree hung ~2 min and came back with the branch REWRITTEN:
  the commit gone, the index wrecked, and `autocommit: N change(s) in the some-scope
  analyze-service index` fixture commits on the branch (reproduced twice; task #322). At that
  moment `core.hooksPath` was set **repo-LOCALLY** to `<repo>/githooks`. Hours later, same
  session, it was unset everywhere with no action by anyone here — and `install.sh` sets the
  key `--global`, so the `--local` value came from something else. **That something else is
  now IDENTIFIED** (2026-08-28): the `civit-datapacket-talos` session
  `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` set it at `2026-08-21T22:16:14Z` while verifying the
  pre-push gate, and unset it itself at `2026-08-23T21:32:51Z` — so "no action by anyone here"
  was true of *this* repo's sessions and false of the box. It explains the `githooks/`
  sighting ONLY; the 08-20 `.git/hooks` ones remain unattributed. **Neither "installed" nor
  "uninstalled" is safe to carry in prose**, and the attribution does not make it stable —
  re-measure anyway. 🔴 **A pre-push gate that runs the suite IN the
  worktree can corrupt the branch it is pushing** — see #322 before using `--no-verify` to get
  past it, and re-check the branch afterwards. ⚠ **Check for a REPO-LOCAL `core.hooksPath` before installing**: `install.sh` sets the
  key `--global`, and a local one wins — observed pointing at `.git/hooks` (08-20, workbench
  `devrc` + `homelab-talos`; laptop none) and at `githooks/` (08-21). It correlates with
  agent-worktree creation, is NOT a devrc setting, and `git config --local --get
  core.hooksPath` per clone is the only answer.
  **Until then: run the gate yourself before you merge, and say which command you ran.**
  Both of these assert collected-test FLOORS and parse structured output rather than reading
  an exit code, because `node --test <dir>` silently yields a bogus `# tests 1` and a pytest
  suite can collect 0 with a zero exit. Gate on the MERGED tree, not the PR branch.
  🔴 **BUT THEY ARE TWO DIFFERENT TIERS, NOT TWO SPELLINGS OF ONE — this line used to join
  them with "or", and that word cost a required check.** `scripts/gate.sh` runs
  `scripts/run-tests.sh` + `scripts/run-node-tests.sh` **on the dev host** (see its
  `PYTEST_RUNNER`/`NODE_RUNNER`); it does **not** invoke `nix build` at all.
  `nix build .#checks.x86_64-linux.{pytests,nodetests}` builds from a `cp -r ${./.}` **store
  copy with NO `.git`**, and that is the tier **Tekton runs and the merge is gated on**.
  Measured 2026-08-23 on #773: four consecutive `GATE: RESULT=PASS` runs, then
  `tekton/devrc-pytests` red — the sandbox tier had never been run. The dev-host tier is also
  structurally blind to anything keyed on the repo being a git checkout: GUARD 10's
  `NOGIT_REPO_LOCAL` is EMPTY in the sandbox, so its whole repo-local class evaluates
  differently there. **Run BOTH before you claim a merge is safe, and name the tier and the
  base sha in the claim** — "the gate passed" is true of one run, one tier, one base, and
  reads as a property of the change.
  🔴 **The `<!-- merge-gate: … -->` marker above is LOAD-BEARING, not decoration.**
  `scripts/tests/test_ci_claim_matches_reality.py` parses it and fails when it disagrees with
  the repo. **Reword this prose freely — but keep a sentence on the marker's line and do not
  add a second marker anywhere** (the test requires exactly one, and that its line still says
  something to a human: an HTML comment renders as nothing, so a lone marker would leave you
  reading no warning at all). Change it only when something that runs AT MERGE TIME appears
  or disappears: a GitHub Actions workflow triggering on **`pull_request`,
  `pull_request_target` or `merge_group`**, or a non-Actions equivalent. A `push`, tag,
  `schedule` or `workflow_dispatch` workflow runs after or outside the merge and gates
  nothing. Values: `none`, `github-actions`, or **`other`** for anything this test cannot
  see (Tekton — which is why it reads `other` today — or an installed `githooks/` pre-push
  gate); `other` exists so the test can never force you to write a false `none`.
  🔴 **The marker records that something RUNS, never that it BLOCKS.** Those are different
  facts, and the marker cannot tell them apart: it read `other` while nothing blocked
  (2026-08-22), while one tier blocked, and now that both do (2026-08-23) — the value never
  moved, because what changed each time was branch protection, which this test cannot see.
  So `other` never licenses "the merge is protected". Whether a run blocks is branch
  protection, which — along with Tekton and git hooks — is named above but NOT
  machine-checked from here. Re-verify by hand rather than trusting this paragraph's age:
  `gh api /repos/innovation-upstream/devrc/branches/main/protection --jq .required_status_checks`
- 🔴 **There is no hand-written test TOTAL any more.** `run-tests.sh` carries a **per-target** floor table (`TARGET_FLOORS`, pinned two-way against the target list) and derives the global floor as their sum. The old single `MIN_TESTS` literal was base-dependent and took **eleven values across eight PRs in one day**; `rerere` replayed a resolution from a different merge and silently wrote a four-way total onto a two-way tree. A floor is now a function of the current measurement — `m - min(50, max(1, m/20))` — and the gate PRINTS the exact replacement number when one drifts. Resolve a conflict here by re-running the gate and copying what it says, never by arithmetic on the two sides.
- 🔴 **This repo is PUBLIC.** Never commit a real media-library path, directory name, filename, route log, or a real third-party hostname used as an example. **Nor CAPTURED TEXT — anyone's message bodies, prompts, transcripts or chat content, or a model's summaries of them — however it arrives** (an eval capture, a fixture, a debug dump). A test needs the SHAPE; regenerate it synthetic. Gated for JSON/JSONL/JSONC by `scripts/tests/test_no_captured_text.py` and for `.html`/`.txt` by `scripts/tests/test_no_captured_markup.py`; each owns its ledgers, thresholds, pinned allowlist and blind spots — read them there, never restate them. 🔴 All four content gates (these two plus the IP and hostname ones) read `git ls-files` and are **blind to git history** — see `SECRETS.md` → "Dead credentials in reachable history" before treating a green run as "the repo is clean".

## Conventions
- Git: see **Git discipline** above (and `claude/RULES.md` → "Git Workflow" for the portable rules).
- Land work on `main` via PR, then `scripts/ship.sh` — never `git pull` + switch per host by hand.
