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

- **Rank 2 is MERGED and CLOSED OUT.** `#1128` → **`c06a56a1`**, verified by
  CONTENT on `origin/main` (a squash makes ancestry read FALSE — it does here
  too, and that is expected, not a problem): guard file present, one full `PUT`
  restore, one read-back step, `<!-- merge-gate: other -->` marker still exactly
  1, and exactly 1 `## State now` in this doc.
- **What it changed.** The break-glass note handed over
  `gh api -X DELETE …/branches/main/protection/required_status_checks` and said
  nothing about closing the window. It now carries **capture → open → full `PUT`
  → read back**. The capture command was run live against the real endpoint and
  emits all **11** keys the `PUT` requires.
- **Rank 1 (rc 24) remains DONE** — #1065 → `ebbe5eaa`, verified live under the
  real systemd unit. Unchanged by this session.
- **Guard verification:** red at `53f523ed` with the real finding (`carries no
  -X PUT`), green at HEAD; mutation sweep **7/7 killed**, control green, under
  `PYTHONDONTWRITEBYTECODE=1`. Gated on the **MERGED tree** (`83a24dcf` =
  `origin/main` + the PR), not just the branch: `19486 collected, 0 failed` vs
  `19459` on the branch alone — the count moving is what proves the merged tree
  was actually the thing gated.
- **Claim `clipboard-research-2` RELEASED.** All three worktrees this session
  created (`devrc-breakglass`, `devrc-baseglass`,
  `devrc-integ-breakglass-2d8f77d9`) removed; `integ/breakglass-merged-2d8f77d9`
  deleted. Base clone fast-forwarded to the merge.
- 🔴 **NOT SHIPPED, deliberately, and this is the one thing to re-check before
  anyone runs `ship.sh`.** Another session has `scripts/memory-detail` **staged**
  (`A `) in the shared workbench checkout with `nix/graphical.nix` modified to
  reference it from a bar click handler. A `home-manager switch` today would
  build and deploy **that session's half-finished feature** to both hosts.
  Nothing in rank 2 needs a deploy — the project `CLAUDE.md` is read straight
  from the working tree and is already live on the workbench via the
  fast-forward; a test file deploys nothing. But #1056/#1084/#1101 merged in the
  same window DO touch deployable paths, so a ship IS wanted — once that WIP
  lands or is unstaged. **Re-measure before shipping; do not trust this line.**

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

🔴 **Numbering stays STABLE** — rank is half a `claim-work` slug's identity.
Rank 2 has moved into the closed block below; its number is retired, NOT reused.

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
- ~~**Rank 2 — correct `CLAUDE.md`'s break-glass note**~~ — **MERGED 2026-08-30**,
  #1128 → `c06a56a1`, verified by content. Guard:
  `scripts/tests/test_break_glass_note.py`.

1. **Give the rc-24 arm an UNMEASURED ladder** (devrc). It has THREE
   could-not-measure states, and a lapsed/expired `gh` token leaves it blind
   **forever** while the deadman reads clean — verbatim the rc-18 lesson this
   repo already records ("a scope that can never be evaluated escalated NEVER").
   The `enforce_admins` half additionally needs repo-**admin**, the credential
   most likely to lapse. Files: `scripts/drift-check.sh` (reuse the
   `u_streak_bump`/`_streak_file_bump` machinery), `scripts/tests/test_drift_check.py`.
   forcing: none
3. **Run `/audit-pr 1043`** (devrc) — the one review the clipboard effort never
   got, and it touches `nix/programs/`, which every `home-manager switch`
   depends on. Merged, shipped and verified on the real path, so this is
   confirmation rather than a gate. Files: `nix/programs/`, `.config/nvim/`.
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
6. **OFFERED, NOT BUILT — `scripts/break-glass-merge.sh`** (devrc). The
   deterministic version of the recipe rank 2 wrote into prose: capture, open,
   merge, full `PUT`, read back, and **refuse to exit 0 unless the read-back
   diff matches the capture key-by-key**. Deliberately not built: shipping an
   *untested* command into a break-glass path is precisely the failure rank 2
   corrects, and it cannot be tested end-to-end without opening the window on
   `main`. **Needs an operator decision on what would make it trustworthy**
   before it is worth writing. Files: `scripts/break-glass-merge.sh` (new).
   forcing: none
7. **Remove the socket-timeout dependency in `test_subsystem_store_api.py`**
   (devrc). `TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_
   DISCARDED[record0-…]` failed `tekton/devrc-pytests` on #1128 with a socket
   read `TimeoutError` — not an assertion; the test never reached the property
   it asserts. It stands up a real HTTP server and the client timed out under
   `popen-gw2` with ~14 concurrent PipelineRuns on the cluster. **Attribution
   was measured, not assumed:** same tree + same derivation passed locally
   (`19459, 0 failed`), merged tree passed (`19486, 0 failed`), and the class
   passes 3/3 locally in ~6s. It was cleared by a re-trigger, which is the
   WEAKER remedy and is recorded as such — the durable fix is to remove the
   timing dependency (bound the wait explicitly, or assert against an in-process
   client rather than a socket). Files:
   `scripts/tests/test_subsystem_store_api.py`.
   **CLOSING CONDITION:** that test no longer reads from a socket with an
   implicit timeout, and `tekton/devrc-pytests` passes on the PR that changes it.
   forcing: gate — it failed a REQUIRED check and cost a merge cycle on #1128.
8. **`/handoff`'s kickoff template emits a path that does NOT resolve** (devrc).
   The template is `<repo>/claudedocs/handoff-<topic>.md`, which yields
   `devrc/claudedocs/…`; `resume-state.sh` matched no such file, **fell back to
   the newest of 90 handoff docs** and reconciled a DIFFERENT initiative — PR
   states, DRIFT lines and all — with only the `!! GAPS` banner as the tell.
   Measured this session. This is a defect in the emitted template, not in one
   session's typing, so it recurs for every kickoff. Files:
   `claude/skills/handoff/SKILL.md` (step 3 template).
   **CLOSING CONDITION:** a kickoff emitted by the skill, pasted verbatim,
   produces a `resume-state.sh` run whose `handoff:` line names the intended doc
   and whose DRIFT block carries no `requested handoff … NO SUCH FILE` gap.
   forcing: incident — it silently reconciled the wrong initiative on this run.

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

🔴 **RELOCATED 2026-08-30 (second attempt), and the first attempt is the lesson.**
This block was moved out of `## State now — updated 2026-08-29` into a `###`
subsection — but left INSIDE `## State now`, which REPLACES. The very next update
was about to delete it, and `handoff_doc.py` said so. **Nesting durable content
under a REPLACE heading does not protect it; only the SECTION's bucket decides.**
It now sits under `## Gotchas`, which appends.


- 🔴 **`ship.sh` rc 19 — EVERY PER-HOST LINE READ ✅ VERIFIED AND THE FLEET WAS
  STILL WRONG.** Measured 2026-08-30: the workbench landed `db790e08` and the
  laptop `e9437342`, each reporting `✅ VERIFIED — on branch main at origin/main
  (clean tree) + switched`. `origin/main` moved BETWEEN the two legs' fetches
  (#1046 merged mid-run), so both hosts converged correctly to different commits.
  This is the whole reason `ship.sh` compares the two landed shas: a per-host
  verdict cannot see it, and reading the per-host lines — which is otherwise the
  right instinct — would have called it clean.
  **Diagnose before re-running**: `git merge-base --is-ancestor <older> <newer>`.
  True ⇒ benign, main simply advanced; re-run `ship.sh` and it converges. False
  ⇒ genuine divergence, do not re-run blind. A one-host run reports
  `cross-host agreement NOT COMPARED`, which is a different claim from agreement.
- 🔴 **A DATED STATUS HEADING CANNOT BE REPLACED, and the doc had one.**
  `handoff_doc.py` buckets by EXACT heading, so `## State now — updated
  <date>` never matches the next session's `## State now — updated <other date>`:
  the delta is bucketed NEW and **appends a second status section** while the
  stale one stays at the top. Measured here — the doc briefly opened with
  2026-08-29 status and carried the current state 300 lines below. Normalised to
  the template's bare `## State now`; the 2026-08-29 content was kept under
  `### Earlier — the 2026-08-29 record`, a non-status heading a replace cannot
  touch. Same failure mode as a dated topic slug, one level down.
- 🔴 **The write gate caught a RETRACTION about to be deleted.** The greenclip
  security retraction sat under `## Next steps` — a REPLACE heading — so every
  future status update would have silently removed it. `handoff_doc.py` flagged
  it as a durable line being dropped; it now lives under `## Gotchas`, which
  appends. **Where a durable claim SITS decides whether it survives**, and a
  retraction is the class that must.

### The 2026-08-30 break-glass correction (rank 2)

- 🔴 **`PATCH` does not "silently fail" — it 404s, and the distinction changes
  the fix.** The prior handoff recorded the symptom as silent. Primary evidence
  from two sessions says otherwise: `PATCH …/protection/required_status_checks`
  returns **`Required status checks not enabled`** once the sub-resource is
  deleted. It *updates checks that exist*; it cannot recreate a deleted
  sub-resource. What made it look silent is the idiom around it — a restore
  inside an EXIT trap written `>/dev/null 2>&1`, which discards the very message
  that names the cause.
- 🔴 **The bigger hazard is the one the summary omitted: a PARTIAL `PUT`
  returns 200 and silently drops every key it does not carry** — `enforce_admins`,
  force-push and deletion settings included. So "the PUT returned 200" is a claim
  about the REQUEST, never about the protection, and the read-back is not
  optional. All **11** keys are load-bearing;
  `required_status_checks`/`enforce_admins`/`required_pull_request_reviews`/`restrictions`
  are *required* by the endpoint (the last two are legitimately `null` here), and
  the `app_id` pinning inside `checks` is what binds the restored context to
  Tekton rather than to any app that can post the same name.
- ⚠ **NOT MEASURED and deliberately labelled so in `CLAUDE.md`:** whether
  `PUT` with `required_status_checks: null` opens the window symmetrically. The
  `DELETE`/`PUT` asymmetry is what has actually been run. Recording an untested
  alternative as an option is how the original trap got its untested command.
- 🔴 **A guard's own positive control caught the guard being vacuous — keep the
  control.** `test_break_glass_note.py` first parsed `gh api` paths with
  `/repos/[^\s'"]+`, which swallows the closing **backtick**. `CLAUDE.md` writes
  the DELETE inline in backticks, so the parser saw **no** DELETE in a document
  that plainly contained one, and the round-trip assertion passed **VACUOUSLY**
  on exactly the note it exists to reject. The separate
  `test_the_note_still_offers_the_escape_hatch` control is what failed and
  exposed it. **A guard over MARKDOWN must be tested against the markdown
  rendering, not just the bare command.**
- 🔴 **Two mutants SURVIVED the first sweep; both were fixture gaps, not logic
  bugs.** `put-accepts-patch` — nothing fed a `PATCH` on the protection
  **object**, so widening the verb test to `("PUT","PATCH")` went unnoticed and
  the guard would have accepted a note recommending an unmeasured restore verb.
  `checks-anchor-dropped` — `…/required_status_checks/contexts` is a real and
  different endpoint, misclassified as the sub-resource once the `\Z` anchors
  went. **The sweep only ever tests the mutations you imagined**; the fix was to
  vary the axes, not to add more mutants of the same shape.
- **Why the recipe was not re-verified live:** confirming it would mean `PUT`ing
  protection back over itself on `main`. The step is already measured by a real
  run (2026-08-30, read-back diffed key-by-key, "FAITHFUL — every key matches"),
  so a second confirmation buys little against a write to the protection surface
  that has already gone wrong three times this week. The capture half *was* run
  live, because it is a read.

### The kickoff block's own path format is a live trap

- 🔴 **A `devrc/claudedocs/…` prefix in a kickoff does NOT resolve, and the
  failure is quiet in the direction that matters.** `resume-state.sh` matched no
  such file, **fell back to the newest of 90** handoff docs
  (`handoff-browser-bridge-architecture-trace.md`) and reconciled a *different
  initiative* — DRIFT lines, PR states and all. The only tell was the `!! GAPS`
  banner naming the file it could not find. Re-running with the repo-relative or
  absolute path reconciled correctly. **Read the gap banner before the DRIFT
  block**; a clean-looking digest under a fallback is a digest about other work.
  `/handoff` emits the prefixed form, so this will recur — pass the path as it
  exists on disk.

### Close-out of rank 2 (2026-08-30)

- 🔴 **A FAILED SETUP STEP DOES NOT STOP THE STEPS THAT ASSUME IT WORKED.**
  Building the merged-tree integration branch, `git worktree add
  /home/zach/workspace/devrc-integ` **failed loudly** — that path already existed
  as ANOTHER session's worktree on `integ/963-965` — and every chained `git -C`
  command after it ran anyway, **merging this branch into their integration
  branch**. Caught one command later; restored from the reflog with
  `reset --keep ea9811ed` (never `--hard`), working tree clean, no branch
  contains the stray merge. **Two lessons, and the second is the reusable one:**
  name worktrees per-session (`devrc-<topic>-<session-prefix>`), and **guard the
  path first** (`test -e "$W" && exit 1`) rather than relying on `worktree add`
  to stop the sequence — its failure is loud and its successors are silent.
- 🔴 **The merged-tree gate is not ceremony — `strict: false` means a green
  check is a claim about the PR's BRANCH.** #1128 was 3 commits behind `main`
  with **zero file overlap**, which reads as obviously safe and is exactly the
  case the repo's own rule refuses ("disjoint files are not merge safety"). Both
  sides added test files; the merged tree collected **19486** against the
  branch's **19459**. The moving count is the evidence the right tree was
  gated — a merged-tree run that reports the branch's own number gated nothing.
- ⚠ **A red REQUIRED check is not automatically your diff, and not automatically
  a flake either.** The discriminator that settled it here was the FAILURE KIND:
  a socket `TimeoutError` means the test never reached its assertions, so it
  asserts nothing about the code. Read the step log for the exception, not the
  summary line. See ranked item 7 — re-running cleared it and is the weaker
  remedy.
- 🔴 **`ship.sh` was NOT run, and "merged" does not imply "deployed".** Another
  session's `scripts/memory-detail` is staged in the shared checkout with
  `nix/graphical.nix` referencing it, so a switch would deploy their unfinished
  feature to both hosts. Rank 2 needs no deploy (repo-root prose + a test), but
  the laptop is now behind on #1056/#1084/#1101, which do not. **Re-measure the
  staged state before shipping.**

## How to verify

- 🔴 **The rc-24 arm, under the real unit** — not by reading the file. Carried
  forward from the rank-1 close-out; still the only honest check for that arm:
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
  a one-host run says `cross-host agreement NOT COMPARED`, a different claim.
- **The break-glass guard, red at base and green at HEAD** — the claim is the
  MATRIX, not either half:
  ```bash
  git -C ~/workspace/devrc worktree add --detach /tmp/bg-base 53f523ed
  cp <branch>/scripts/tests/test_break_glass_note.py /tmp/bg-base/scripts/tests/
  PYTHONDONTWRITEBYTECODE=1 nix develop ~/workspace/devrc -c \
    python3 -m pytest /tmp/bg-base/scripts/tests/test_break_glass_note.py -q
  # expect: 1 failed (test_the_break_glass_note_round_trips, "carries no -X PUT")
  PYTHONDONTWRITEBYTECODE=1 nix develop ~/workspace/devrc -c \
    python3 -m pytest ~/workspace/devrc/scripts/tests/test_break_glass_note.py -q
  # expect: all passed
  ```
- **The vacuity control is the one that matters** — if
  `test_the_note_still_offers_the_escape_hatch` fails, the round-trip assertion
  is passing on a document with no DELETE in it and proves nothing.
- **The `<!-- merge-gate: other -->` marker must survive the edit** — it sits
  three lines above the changed region and is parsed by
  `scripts/tests/test_ci_claim_matches_reality.py`, which must stay green.
- **Both tiers, one at a time** (the dev-host tier is NOT the tier Tekton gates
  on): `nix develop <repo> --command bash <repo>/scripts/gate.sh --tier both`,
  then `nix build .#checks.x86_64-linux.pytests` and
  `.#checks.x86_64-linux.nodetests` **separately**.
