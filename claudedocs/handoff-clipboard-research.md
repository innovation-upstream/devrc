---
# No clawgate task — session had no CLAUDE_CODE_SESSION_ID
---
# Handoff: clipboard-research — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Research modern best practices for clipboard and terminal clipboard interaction on Linux/NixOS/i3wm. Determine what's current, what's optimal, and what (if anything) needs changing.

## State now

- **Rank 1 is DONE, and this is the first claim in this effort verified under the
  real systemd unit rather than by hand.** `drift-check.sh` gained **rc 24**: an
  unprotected-`main` detector. Merged as **#1065 → `ebbe5eaa`**, both hosts
  converged and compared at **`809486fa`**.
- **Live proof**, from `journalctl --user -u drift-check` after `systemctl --user
  start drift-check` on the workbench (the only host the timer runs on):
  ```
  [protect] innovation-upstream/devrc main: 2 required status check(s) — tekton/devrc-pytests,tekton/devrc-nodetests
  [protect]   enforce_admins=true — the checks bind admins too.
  drift-check: no drift on the host(s) CHECKED: workbench (local), laptop (remote)
  ```
  `gh` resolves on the unit's own PATH on **both** hosts (checked by running
  `command -v gh` under the unit's `Environment` PATH, not by reading home.nix).
- **Two side PRs shipped from this thread:** **#1069** (re-pin skill-tier
  measurements — `main` was RED for every PR until it landed) and the
  `TARGET_FLOORS` merge-conflict resolution.
- **Also fixed while verifying:** the laptop's `homelab-talos` was 31 commits
  behind with **1 touching `containers/clawgate`** — the subtree `clawgatectl`
  is compiled from. Fast-forwarded + re-switched; `drift-check` now reports both
  hosts' built sources CURRENT. ⚠ `clawgatectl --version` read **0.8.18 before
  and after** — the version string was NOT the signal; the deadman was.
### Earlier — the 2026-08-29 record (how the clipboard fix shipped INERT)
Kept because its content is durable, not status: the inert-fix story, the
E484 chain and the verified-on-the-real-path claim. It is no longer under a
`State now` heading, so a future status replace cannot silently delete it.

- **Research session (00:37–00:53):** report completed, no code changes. This doc
  landed on `handoff/clipboard-research` → PR #1014 (it could not be pushed to
  `main`: protected branch, 2 required checks).
- **Resume session:** both open decisions resolved, and a bug the research missed
  was found, fixed and gated.
  - `unnamedplus` — **declined**, no change.
  - `"+y` dead with no `DISPLAY` — fixed in **#1027**, which then turned out to
    be INERT in production until **#1043**. See the block below before trusting
    anything in this section.
- 🔴 **THE CLIPBOARD FIX (#1027) SHIPPED INERT. #1043 is what made it work.**
  Everything above this line was written before that was known; the paragraph
  that used to sit here claimed the change "goes live on `git pull` alone,
  no `home-manager switch`" and cited `$DEVRC_DIR` as the mechanism. Both
  halves were wrong, and it is quoted rather than deleted because the way it
  was wrong is the reusable part.

  Measured over real ssh to the laptop, against the deployed copy, AFTER #1027
  had merged and shipped:

  ```
  Error in /home/zach/.config/nvim/init.lua:
  E484: Can't open file /.config/nvim/config/native.vim
  clipboard: No provider. Try ":checkhealth" or ":h clipboard".
  ```

  `$DEVRC_DIR` was set in exactly ONE place — a systemd user service's
  `Environment=` block in `nix/graphical.nix` — so it existed only inside a
  graphical session. `init.vim` sourced every other config file through it, so
  off-session the first `source` raised E484 and **aborted the entire nvim
  config**: no options, no leader mappings, no lua half, no plugin config.
  neovim had been running unconfigured over ssh, on a bare TTY, in units and in
  cron — invisible because the only place anyone reads a config error is the
  terminal in front of them, which is the one place the variable was set.

  🔴 **Why no test caught it, which is the lesson worth keeping:** #1027's
  red/green harness **set `$DEVRC_DIR` itself**, manufacturing the one
  precondition that does not hold in production. **A fixture that supplies an
  environment cannot observe that environment being absent.** The fix was
  correct, merged, green, mutation-tested — and did nothing where it mattered.

- **Fixed in #1043**: nix substitutes the repo path into `init.vim` at BUILD
  time, `init.lua` self-locates via `debug.getinfo`, and `lazygit.lua` — found
  by the new guard, not by hand — stopped pointing at
  `nil/.config/lazygit/config.yml`. Guards: a hermetic relationship test that
  NO file under `.config/nvim` reads `$DEVRC_DIR` at runtime (comments
  stripped, mutation-tested), plus a dev-host red/green counting E484s from the
  real chain.

- 🔴 **Deploy: a `home-manager switch` IS required** (the corrected claim).
  `init.vim` is `builtins.readFile`'d into the store, so the substitution
  happens at build time. Files it sources — `native.lua`, `native.vim` — are
  still read from the `~/workspace/devrc` working tree at runtime, so edits to
  THOSE remain live on `git pull`. The two are different questions and the old
  paragraph collapsed them into one.

- ✅ **VERIFIED on the real path, 2026-08-29**, both hosts at `638959b4`:
  `ssh` → `nvim` → `"+yy` on the laptop went from `E484=9, No provider,
  OSC52=0` to `E484=0, No provider=0, OSC52=1`, payload decoding to the exact
  yanked line. Workbench resolves the same substituted store `init.vim`. This
  is the first claim in this effort verified on the path that actually failed
  rather than a reconstruction of it.

## Research findings — clipboard/terminal clipboard best practices (2025-2026)

### tmux clipboard — ALREADY OPTIMAL, no changes
- `set -s set-clipboard on` makes copy-mode emit OSC 52 to the outer terminal
- `set -ga terminal-features '*:clipboard'` advertises capability regardless of TERM
- Works across SSH and nested tmux — no external tools needed
- tmux-yank plugin is superseded (pops to `xsel`/`xclip` locally; OSC 52 is better)

### Alacritty OSC 52 — ALREADY OPTIMAL, no changes
- `osc52 = "OnlyCopy"` is the correct security posture (prevents remote paste injection)
- All modern terminals (Kitty, WezTerm, Ghostty) support OSC 52 fully

### X11 clipboard tools — ALREADY OPTIMAL, no changes
- `xclip` installed and sufficient for X11 scripting
- `xsel` is an alternative with slightly different CLI but no advantage
- If migrating to Wayland: switch to `wl-copy`/`wl-paste` (2.4k stars, gold standard)

### Clipboard managers — NOT NEEDED
- `cliphist` (1.5k stars) is the modern gold standard but Wayland-only
- On X11/i3, tmux OSC 52 + xclip covers the use cases
- `clipcat` (X11) or `CopyQ` available if history is wanted, but optional

### Neovim clipboard — ONE REAL BUG (found on resume), ONE DECLINED IMPROVEMENT

🔴 **The original claim on this line was wrong, and the correction is the whole
value of this section.** It read "`"+y` / `"+p` work without config", full stop.
That is true **only while `DISPLAY` is set**. Measured 2026-08-29 on nvim 0.12.5
at two independent points — `--headless` with the real config, and a pty with
`-u NONE` — with no `DISPLAY` (any ssh session, any bare TTY):

```
clipboard: No provider. Try ":checkhealth" or ":h clipboard".
```

Neovim ≥0.10 ships an OSC 52 provider but does **not** auto-enable it here, so
`"+y` was dead off-display and took `:Absc` (`lua/config/native.lua`, which does
`setreg('+')`) down with it. The research missed it because every probe ran on
the local X11 session — **one measurement point, and the failure lives at the
other one.**

- **FIXED** — `.config/nvim/lua/config/native.lua` now installs an OSC 52
  provider when `DISPLAY` is absent. Red→green on the exact failing path.
- 🔴 **Paste is served from a local cache, deliberately.**
  `vim.ui.clipboard.osc52.paste` queries the terminal and waits 1s + 9s before
  giving up, and alacritty is configured `osc52 = "OnlyCopy"` so it never
  answers. Wiring it straight through — **which is what `:h clipboard`'s own
  example does** — hangs 10s on every `"+p`. The mutant that does exactly that
  took the suite from 2.7s to 12.7s.
- Guarded on `DISPLAY` being absent: with a display, neovim's xclip
  autodetection already works *and* supports a real paste, so it is left alone.

**`set clipboard=unnamedplus` — DECLINED 2026-08-29.** The doc framed the cost as
"vim muscle memory", which understates it: `unnamedplus` routes **every** delete
and change (`d`, `c`, `x`, `s`) through the `+` register, so `dd` clobbers the
system clipboard and the copy-from-browser → edit → paste workflow breaks. Zach
works via agents rather than long interactive nvim sessions, so the payoff is
small and the cost is not. `"+y`/`"+p` stay explicit.

### Espanso + clipboard — ALREADY WORKING, no changes
- `{{clip}}` variable reads X CLIPBOARD via espanso's own `espanso-clipboard` module
- No conflicts with tmux OSC 52 path
- Gotcha: `{{clip}}` reads whatever is in CLIPBOARD at expansion time — potential race with concurrent writes

## Open investigations — live diagnosis state

None open. ⚠ **But note what this section said before, and why it was wrong:**
*"None. Research is complete. The findings above are conclusive."* It was written
after a survey that measured only the local X11 session, and it read as a clean
bill of health for a setup with a dead clipboard register over ssh. **A research
doc that names no open question is making a claim, not reporting an absence** —
the honest version names the dimension it did not vary. Here that dimension was
`DISPLAY`.

### RESOLVED 2026-08-30 — `main`'s branch protection has a detector now
The open item below ("`main`'s branch protection keeps ending up OFF, and nothing
detects it") is **closed by rc 24**. Keeping the original block above it, because
the *attribution* half was never settled and remains unsettled: occurrence 2 was
not attributed to anyone, and GitHub's protection-change events are org-audit-log
only. What changed is that a recurrence is now **detected within 6 hours** instead
of by a human happening to look.

- **What rc 24 actually asserts:** `required_status_checks.contexts` is non-empty
  on `main` AND `enforce_admins` is true AND — when classic protection is absent —
  at least one **ruleset** with a `required_status_checks` rule is `active` with
  **zero `bypass_actors`**.
- 🔴 **What it CANNOT assert, and this is deliberate:** the DRIFT branch has
  never run against real GitHub. Proving it means deleting `main`'s protection,
  which is the hazard itself. It is covered by stubs + 23 mutants only.
- **Measured API facts the arm depends on** (re-derive rather than trust):
  - `/branches/main` does **NOT** populate `.protection.enforce_admins` for this
    repo — `// false` there yields **false** while `/branches/main/protection`
    yields **true**. Keying on the wrong endpoint fires rc 24 on a healthy repo.
  - A ruleset-gated branch reads `protected=true, contexts=[]` (measured on
    `astral-sh/uv`), which the classic read alone calls wide open.
  - `/rules/branches/main` exposes `parameters` but **not** `bypass_actors`; the
    ruleset DETAIL endpoint carries both and is readable **without** repo-admin.
  - jq emits `2 ,111` when a selected rule has a **null** `ruleset_id` — an empty
    field is a LOST id, not a separator.

## Next steps (ranked)

**Closed by this effort — kept so a resume does not re-open them:**
- ~~adopt `set clipboard=unnamedplus`~~ — **DECLINED 2026-08-29**: it routes every
  `d`/`c`/`x`/`s` through the `+` register, so `dd` clobbers the system clipboard.
- ~~install a clipboard manager for history~~ — **investigated 2026-08-29, nothing
  installed.** The evidence and the **RETRACTED** greenclip security argument are
  under Gotchas; the retraction stands — anyone who can read
  `~/.cache/greenclip.history` can already read `~/.ssh/id_*`.
- ~~migrate to Wayland~~ — not applicable: measured `XDG_SESSION_TYPE=x11`.
- ~~**Add an unprotected-`main` arm to `scripts/drift-check.sh`**~~ — **SHIPPED
  2026-08-30** as rc 24, #1065 → `ebbe5eaa`, verified live under the real unit.

1. **Give the rc-24 arm an UNMEASURED ladder** (devrc). It now has THREE
   could-not-measure states, and a lapsed/expired `gh` token leaves it blind
   **forever** while the deadman reads clean — verbatim the rc-18 lesson this same
   file already records ("a scope that can never be evaluated escalated NEVER").
   The `enforce_admins` half additionally needs repo-**admin**, which is the
   credential most likely to lapse. Files: `scripts/drift-check.sh` (reuse the
   `u_streak_bump`/`_streak_file_bump` machinery), `scripts/tests/test_drift_check.py`.
   forcing: none
2. **Correct `devrc/CLAUDE.md`'s break-glass note** (devrc). It hands over
   `gh api -X DELETE …/required_status_checks` verbatim and says **nothing about
   restoring** — and the obvious `PATCH` back **silently fails**, which is why the
   2026-08-29 break-glass left `main` unprotected despite an EXIT-trap restore that
   ran. It must carry the full `PUT` payload and say the restore has to be READ
   BACK. Files: `CLAUDE.md`.
   forcing: incident — the 2026-08-29 double unprotection; occurrence 1's restore
   trap executed and still left main open, occurrence 2 left a direct push
   (`837d3fde`) on main that required checks would have rejected.
3. **Run `/audit-pr 1043`** (devrc) — the one review the clipboard effort never
   got, and it touches `nix/programs/`, which every `home-manager switch` depends
   on. Merged, shipped and verified on the real path, so this is confirmation
   rather than a gate. Files: `nix/programs/`, `.config/nvim/`.
   forcing: none
4. **Consider recording the transferable lesson in `claude/RULES.md`**: *a fixture
   that supplies an environment cannot observe that environment being absent.*
   Gated — `RULES.md` has an enforced ceiling (`scripts/tests/test_rules_size.py`)
   needing an eviction in the SAME commit, so this is an operator call.
   forcing: none
5. **The gh read-only guard rejects `--paginate`/`-H`** (devrc). It fails
   **CLOSED**, so the cost is a test edit, not safety — but `--paginate` is a
   plausible near-term need on `/rules/branches/main`. Files:
   `scripts/tests/test_drift_check.py`.
   forcing: none

## Gotchas / decisions / dead-ends
- OSC 52 supersedes tmux-yank for this setup — no reason to install the plugin
- Wayland clipboard is a different ecosystem — `wl-clipboard` is the equivalent of `xclip`
- Espanso's clipboard access is separate from tmux's OSC 52 but reads the same X CLIPBOARD selection
- No clipboard manager installed — see the section above for the evidence, and
  note the retracted security argument so it is not re-derived
- 🔴 `greenclip` has NO top-level nixpkgs attribute; `nix ... nixpkgs#greenclip`
  fails. It is `haskellPackages.greenclip`. A version check that falls back to
  that attribute will report 4.3.1 and read as if the top-level one existed

### Clipboard managers — investigated on resume, still NOT installed
🔴 **RELOCATED 2026-08-30, verbatim, and the relocation is the point.** This block
sat under `## Next steps (ranked)` — a REPLACE heading — so every future status
update silently deleted it, including the **retraction** below. `handoff_doc.py`
flagged it as a durable line about to be dropped. Retractions must outlive the
status that happened to surround them, so it now lives under an APPEND heading.

### Clipboard managers — investigated on resume, still NOT installed

The original section concluded "NOT NEEDED — tmux OSC 52 + xclip covers the use
cases". That reasoning was about **transport** and did not address
**persistence**, which is a real and measured gap:

```
set CLIPBOARD from a process   ->  [OWNERSHIP-PROBE]
kill the owning process        ->  Error: target STRING not available
```

X11 selections are owned by the source client, and no manager is running to
hold them (verified: none of clipcat/copyq/greenclip/clipmenu/diodon/parcellite
is installed or running). Close the app you copied from and the clipboard is
empty. So the original conclusion was right by luck, not by argument.

**Nothing installed anyway, and the reason is NOT security.** An earlier draft
of this section argued against `greenclip` because its only exclusion mechanism
is `blacklisted_applications` (per-application, verified against the real
binary's generated config), so a vault paste out of Brave cannot be excluded
without excluding Brave itself, leaving up to 50 plaintext secrets in
`~/.cache/greenclip.history`. 🔴 **That argument is RETRACTED as overweighted**:
anyone who can read that file can already read `~/.ssh/id_*`, the age
identities and the `$KC_*` kubeconfigs in the same home directory, and
`~/.cache` is conventionally excluded from backups. The marginal exposure is
small. The per-application fact is true; the conclusion drawn from it was not.

The actual reason is **no measured need**. The probe above demonstrates the
MECHANISM; nothing establishes the FREQUENCY, and no instance of actually
losing a clipboard was observed. Against that: Zach works entirely via agents
and the standing direction is to modernize the agent-facing layer rather than
interactive-CLI ricing — a history picker on `$mod+Shift+v` is the latter — and
`MEMORY.md`'s "do we need it before hardening" entry exists because a 145 KB
webhook listener that had never run once was shipped and later retired.

Contrast with the neovim fix above, which shipped precisely because it was
**not** speculative: `"+y` was reproducibly dead on every ssh session.

**If it is ever wanted, the pick is `greenclip`** (4.3.1 in the pinned nixpkgs,
attribute `haskellPackages.greenclip` — NOT top-level), because it reuses the
rofi already bound to `$mod+d` and solves persistence AND history for the same
machinery, which strictly dominates a persistence-only daemon. `$mod+Shift+v`
is free. The trigger to revisit is an OBSERVED lost clipboard, not this note.

- 🔴 **SIX audit rounds, and FOUR of them found the FIX ROUND's own defect.** The
  ladder is the record: R2 counted rule declarations → R3 fixed it but read only
  `.[0]` → R4 fixed that but let the loop run ZERO times and announce "nothing
  gates main" → R5 fixed that only for `examined==0`, leaving a partially-lost id
  list → R6 fixed that. Separately R3 opened a `command` hole R2 had CLOSED, and
  R4 re-opened it one flag over (`-p`, which EXECUTES). **Budget for several
  rounds; the count is set by findings, never by a number.**
- 🔴 **A guard that re-implements the thing it guards is testing itself.** Round
  6's headline was TWO SURVIVING MUTANTS in guards written to catch exactly what
  the mutant did — and one survived TWICE, because the "fix" recomputed the
  derivation LOCALLY inside the test. The remedy is one definition with two
  consumers (`_derive_gh_calls`), which is the same one-rule-one-place rule the
  repo already applies to predicates.
- 🔴 **A stubbed binary means its `--jq` NEVER RUNS.** Every behavioural test drove
  `gh` through a stub, so the four jq filters were exercised by NOTHING — a mutant
  removing a filter survived the entire suite. `jq` is in both tiers' toolchains;
  run the real filters against fixture payloads, with a negative control proving
  the harness can tell a gating rule from an empty one.
- 🔴 **Widening a guard flagged the file it guards — three times.** `command -v`,
  `DRIFT_GH=gh`, and `[ -z "${DRIFT_GH+set}" ]` each read as an invocation after a
  widening. A guard that reds against its own subject is one the next person
  loosens.
- 🔴 **`TARGET_FLOORS` conflict: neither side's number described the merged tree.**
  `main` pinned 10269, the branch pinned 10233; the merged tree collects more than
  either. Resolved with a NON-NUMERIC placeholder first so a stale number could
  not survive by accident (`--check-floors` rejects it loudly), then measured.
  ⚠ And the resulting comment initially claimed a provenance the number did not
  have — "the gate's own printed replacement" — when the gate had printed nothing
  because the check PASSED. On this line, a false recipe in the comment is the
  failure mode.
- **`ship.sh` SUPERSEDED itself mid-run** (its own fast-forward replaced the script
  executing it) and re-exec'd the new copy before the remote leg. Working as
  designed; everything the old copy printed was recomputed.
- ⚠ **A version string is not a deploy check.** The laptop's `clawgatectl` read
  `0.8.18` before and after a genuine source advance. `drift-check`'s BUILT SOURCE
  line is the instrument; the version was silent.
- **Squash merges:** verify by CONTENT. `merge-base --is-ancestor <head> main`
  returns **false after every squash, forever**, and reads as "not merged".

## How to verify
- 🔴 **The rc-24 arm, under the real unit** — not by reading the file:
  ```
  systemctl --user start drift-check
  journalctl --user -u drift-check --since '5 minutes ago' | grep '\[protect\]'
  ```
  Healthy reads `2 required status check(s) — tekton/devrc-pytests,tekton/devrc-nodetests`
  then `enforce_admins=true`. A file that merely CONTAINS the arm proves nothing:
  it needs `pkgs.gh` on the unit's PATH, which arrives only with a switch.
- **That gh resolves where the unit will look** (the failure that reads as
  COULD NOT MEASURE forever from a unit that looks correct):
  ```
  P=$(systemctl --user show drift-check -p Environment --value | tr ' ' '\n' | grep ^PATH= | cut -d= -f2-)
  env PATH="$P" sh -c 'command -v gh'
  ```
- **Both hosts agree:** `scripts/ship.sh` must end `2 hosts compared, both at <sha>` —
  a one-host run says `cross-host agreement NOT COMPARED`, which is a different claim.
- Neovim off-display, tmux/espanso clipboard: unchanged, see the section above.
