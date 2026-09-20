# Handoff: tmux-reply-menu-delivery — 2026-09-20

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` exits **0**
naming **#603**, but 603 is the transcript-archive card of the *tmux-webapp* arc
(`claudedocs/handoff-tmux-webapp.md`). This session worked both arcs, so the
session-scoped resolve is right that 603 was worked and wrong that it is *this* doc's
task. Do not add it; there is no clawgate task for the reply-agent work.

## Goal
Make a reply tapped on a clawgate attention card deliver **the option the operator
chose**, into the pane that is actually showing that ask — and never press a key in a
dialog that is not an `AskUserQuestion`.

- **closing-condition:** `check` — tap a non-first option on a clawgate card against a
  **real blocking `AskUserQuestion`** and observe Claude Code record *that* option
  (`● User answered Claude's questions:` / `⎿ · <q> → <chosen label>`), with the card
  reporting `delivered`. **Not yet met** — see Open investigations.

## State now
- Branch: devrc `main` at `62d83a08`, clean but for two untracked `claudedocs/scope-chief-*.md` (they are in PR **#1783**, open).
- **MERGED + DEPLOYED this session:**
  - `c8cf7b15e` (**#1784**) — the option-1 fix: read the pane, press the digit of the row whose label the reply names.
  - `62d83a08` (**#1795**) — the invariant: a short pane clipped every top-of-modal signal, so a live ask read `text` (typed into) and a live chain read `menu` (half-answered). Now keyed on the **footer**, which does not clip.
- **Deploy verified on BOTH hosts, chain not assumed:** `ship.sh` converged both to `62d83a08` (workbench already there; laptop fast-forwarded `52571758 → 62d83a08`; 619/578 managed artifacts resolve, 0 dangling, 0 stale). The unit runs the script **directly from the working tree** (`ExecStart=… python3 %h/workspace/devrc/scripts/tmux-reply-agent`), so a `home-manager switch` does not reload it — **the restart is the deploy**. Per host: leaf cgroup = `tmux-reply-agent.service`, script sha == `origin/main`'s (`ac8e88ae960b18c5`), process start postdates the file write, old PID gone.
  - workbench `2388761 → 1420991`, laptop `2222420 → 2227340`, both `active`/`running`, `NRestarts=0`.
- **Audit ladder on #1795: 5 rounds**, ending `no high-severity findings`. Payload trajectory **171 → 218 → 107 → 22 → 0** (the last verified by `ast`: 753 statements / 45 docstrings / **708 executable, identical both sides**).
- CI at the merged head: `nodetests` 1536/1536, `gotests` 386/386, `cairn-client-runs` pass; **`pytests` red on one pre-existing test** (below).

## Open investigations — live diagnosis state

### The original symptom has never been reproduced-then-confirmed-gone against a live ask
as-of: 2026-09-20
- **Symptom + exact repro:** before the fix, tapping *any* option on a clawgate attention card delivered **option 1** while the card reported `Delivered`. Repro then: raise a real `AskUserQuestion`, tap a non-first option on the card, read what Claude Code records.
- **Observed (with values):** the defect was reproduced *in process* — `tmux send-keys -t %176 -l -- 'SQLite'` then `send-keys Enter` → `⎿ · Which database should the queue use? → Postgres`. The **fix** is verified at classification level only: a paired 57-pane sweep classified by both revisions gave `text 53 → 53`, **drivable menu 0 → 1**, refused `4 → 3`, every after-verdict structurally checked; 19 of 22 new test ids red at base and all green at HEAD; 27-mutant and 19-mutant sweeps with sentinels and controls.
- **Ruled out:** that the classifier's `0` drivable menus meant no menus were up — it meant `MENU_ROW_GAP = 4` could not span a real render's described options (measured inter-row spans 7/4/5/2, 5/3/3/4/2, 6/5/3/2 on three live modals). `via: measurement`
- **Ruled out:** that the ordinary text-prompt path was also broken — pinned across four pane states (shell prompt, Claude text prompt, transcript, blank), green at base and HEAD. `via: command`
- **Leading hypothesis:** the fix is correct; what is missing is the end-to-end observation. Nobody has tapped an option on a card and watched the right answer land.
- **Next probe:** raise a two-option ask in a scratch tmux session, find its pane in clawgate, tap the **second** option on the card, then
  `tmux capture-pane -p -t <pane> | grep -A2 'User answered'` — the recorded label must be the one tapped.

### `main` is red on a test nothing in this arc can reach
as-of: 2026-09-20
- **Symptom + exact repro:** `gh pr checks <any devrc PR>` → `tekton/devrc-pytests fail`, `FAILING: test_engine_is_the_version_every_measurement_is_keyed_to`.
- **Observed (with values):** at `91f1622d`: `collected=24040 passed=24035 skipped=4` ⇒ exactly **1** failure, named. Lives in `scripts/tests/test_opencode_engine.py`. `main` carries a byte-identical failure at `c46bb9d4`, `a2b1893a` and later heads; it **passes on the dev host**.
- **Ruled out:** attribution to #1795 or #1784 — neither range touches any `opencode` path, and three independent audit rounds re-derived that. `via: measurement`
- **Leading hypothesis:** an engine/version pin in `test_opencode_engine.py` went stale against the installed opencode; sandbox-tier-only.
- **Next probe:** `nix build .#checks.x86_64-linux.pytests 2>&1 | grep -A20 test_engine_is_the_version` — read the assertion's expected-vs-actual, then decide whether the pin or the environment moved.

## Next steps (ranked)
1. **Close the closing condition — tap a non-first option against a real blocking ask and read the recorded label.** Repo `devrc`, no code change; the probe is in Open investigations. Until this runs, the arc's central claim is verified by tests and classification sweeps but not by the symptom.
   forcing: gate — the verification-honesty gate in `claude/RULES.md`: the exact failing path has not been exercised since the fix.
2. **Cut a clawgate release so #855's scroll fix reaches the pod.** `ZacxDev/homelab-infra` `edefd679f` is on `trunk`; the image pin in `clusters/workbench/apps/clawgate/deployment.yaml` has NOT moved, so `/tmux` on the phone still runs the old code. Load the `clawgate` skill's `deploy.md` first. ⚠ That release also ships #855's behaviour change across **seven** panels.
   forcing: user — the operator reported the mobile scroll symptom and cannot confirm the fix until it is on the pod; the audit was explicit that the downward direction is covered "by argument yes, by measurement no."
3. **Unbreak `main`'s red `test_engine_is_the_version_every_measurement_is_keyed_to`.** Repo `devrc`, `scripts/tests/test_opencode_engine.py`. Diagnosis block above.
   forcing: gate — a permanently-red required-looking check trains click-through, and `main-green-check` reproduces it every 4h.
4. **The laptop carries uncommitted work that is IN its deployed artifact.** `ship.sh` reported `🔴 DIRTY AND IN THE ARTIFACT — scripts/opencode/opencode.jsonc`, and **6 dirty paths (5 tracked)** on `zach@10.42.0.100`. Its generation is `origin/main` PLUS them.
   forcing: gate — `ship.sh` prints it on every run, and unsaved work in a working tree is one routine `checkout` from silent deletion.
5. **#1794 — `session-manager` reports NOT-WAITING for a window blocked on a multiSelect ask.** `_MENU_SELECTED_RE` needs the digit immediately after the cursor, so `❯ [ ] 1. …` matches nothing; measured `{'probable': False, 'signals': []}`.
   forcing: none
6. **#857 — the panel scroll-anchor ledger reads a SAFE verdict off a body it never substitutes.** `ZacxDev/homelab-infra`; a ninth panel of that shape passes the whole suite green. Both controls already demonstrated in the issue.
   forcing: none

## Defects (batched)
- Round 5's three 🟢 non-blocking nits on the merged code, all `scaffolding`: the `sys.modules` comment at `test_tmux_reply_agent.py:5949-5951` calls a pre-seeded entry "an import-file-mismatch waiting to happen" and measured it is not, in either invocation shape; the seam guard pins that each probe pattern is still **listed**, not still **compared** (neuter the alarm's comparison to `if False` and the guard stays green); and a pure reorder reds correctly but the custom message prints two empty lists, leaving pytest's own diff to name the index.
- `MENU_CHECKBOX` omits the bundle's ASCII-fallback tick `√` (U+221A) and includes `✓` (U+2713), which the bundle never emits — **unreachable**, because the same `M4s()` switch that selects `√` also makes `pointer` `>` instead of `❯`, so nothing classifies as a menu at all. Fails safe.
- The distinct-footer ledger floor is walkable by a **swap** (drop a measured footer, add a novel one with a correct verdict) — inherent to a `>=` ratchet, present before this arc, and the header already calls it one.

## Gotchas / decisions / dead-ends
- 🔴 **The unit runs the script from the WORKING TREE, so `git pull` + `home-manager switch` does NOT deploy it — only `systemctl --user restart tmux-reply-agent` does.** `ExecStart=… python3 %h/workspace/devrc/scripts/tmux-reply-agent`. Verify the consumer, not the deploy: leaf of `/proc/<pid>/cgroup` must read `tmux-reply-agent.service` (the FIRST `.service` in that path is `user@1000.service` — reading `head -1` names the user manager, not the unit), and the file's mtime must PRECEDE the process start.
- 🔴 **`AskUserQuestion` renders a MENU, not a text prompt.** Typed characters are discarded and `Enter` takes the **highlighted** row. That is why the original code — `send-keys -l -- '<label>'` then `send-keys Enter` — delivered option 1 every time. The index is now safe *because it is read off the pane*, not assumed.
- 🔴 **Three prescriptions in this arc were measured WRONG, each caught only by leaving the fixtures.** (a) An audit reported three panes as "quoted text, zero numbered rows"; they were **live modals** the detector could not see. (b) That audit's positive control was a **synthetic** menu with tightly-packed rows, so it passed while the detector was blind to every real render. (c) A remedy named `Enter to select · Tab/Arrow keys to navigate · Esc to cancel` "the modal's footer" — it is the **CHAIN's** footer; a single ask renders `↑/↓ to navigate`, so requiring it would have **refused every single-question modal and left the feature inert**.
- 🔴 **Every pre-existing menu fixture in `test_tmux_reply_agent.py` once carried an INVENTED footer** (`↑↓ to select · enter to confirm`) that Claude Code never renders. Treat any fixture in that file as suspect until its shape is seen in a real pane.
- **The footer set is NOT closed, and the code is safe anyway — for a different reason than the enumeration.** `Enter to select` is assembled from `<chord>`/`<action>` components, so a grep for `chord:"enter",action:"select"` sees only the literal-pair path: **≥23 sites in 2.1.232 can spell the anchor, not 16** (six via `lo`'s `fallback:"Enter"`, one via the picker's defaulted action). All of them resolve `FOOTER_UNKNOWN`. **Safety rests on the GRAMMAR.** A 627-component reconstruction at four widths admitted only the two ask renderers themselves.
- **`scripts/devhost-tests/test_claude_footer_sites.py` will red on most Claude Code bumps, by design.** Measured across six bundles (1.0.128 / 2.0.75 / 2.0.76 / 2.1.4 / 2.1.9 / 2.1.232): every probe is 0 on all five older ones. What keying on counts buys is the **re-derivation instruction**, not immunity. ⚠ `gate.sh` and `flake.nix` default to `--set hermetic` so it is opt-in — **but `githooks/tests-on-push.sh` runs `--set all`**, so an installed pre-push gate would block every push until the ledger is re-derived. `core.hooksPath` measured unset local+global on 2026-09-20; that value is volatile.
- 🔴 **NO PROBE MAY BE KEYED ON A MINIFIER-GENERATED IDENTIFIER** (`lo`, `qJl`, `hfw` are build artifacts). De-minified forms give identical counts on every bundle.
- **A guard on WORDS is walkable by prose ABOUT the guard.** The seam guard substring-matched the alarm file's text; the round that documented it added the same literal to that file, taking it 1 → 2 occurrences — and three rots then survived, including **deleting the probe that finds the AskUserQuestion renderers**. Fixed by importing the module and comparing `tuple(probe[0] for …)`. Rot matrix: 3 SURVIVED at `285e25b8`, **0 of 6 at `91f1622d`**.
- **The kept cost, stated because it is real:** where the ask is gone and a *different* menu is up, a correct delivery is reported `failed` (an audit row, never a second keypress — nothing on that path retries). Deliberate; the alternative asserts a measurement made once by a unit that presses digits into live panes.
- **Refusals that go inert rather than wrong, measured:** a chain footer quoted in a pane; an ask footer with no parseable block; and **any pane narrower than ~30 columns**, where `Enter to select` wraps → `FOOTER_UNKNOWN` → every ask refused. FOOTER_SINGLE holds at 104/60/48/40; all live widths ≥79.
- **Height was the dimension nothing was measured at twice**, and it hid both round-3 blockers. Resizing real windows (not truncating captures) gave: 40–16 correct; **15/14 a chain reads `menu`**; **13–10 both read `text`**. The TUI re-renders to fit and scrolls its own option list, marking the edge `↓` where `MENU_OPTION_RE` expects whitespace — a fourth clipping route no round had named.
- **`AskUserQuestion` cannot be answered by a hook.** `PermissionRequest` is allow/deny/defer only; the Agent SDK's `canUseTool` is the sole programmatic path and requires hosting the query loop, which clawgate cannot. Verified against current docs AND asserted independently by `internal/api/server.go:1169`. Do not re-open it.
- **The TUI already has a multi-question chain with submit-at-end** (tab strip, `☐`→`☒`, left-arrow back-nav, `Review your answers` → `Submit answers`). Free text on question 1 does **not** return the call, so a chain needs N answers + submit.
- **multiSelect is a MODE of ask renderer 1**, not a separate renderer — it emits no footer of its own, so `menu_footer` returns `FOOTER_SINGLE` for a live multiSelect and `menu_checkbox_block` is the whole guard. **The footer grammar cannot distinguish a driveable ask from a multiSelect.**
- **Per-question notes have no delivery path** — the field exists in the tool schema, but with no hook path the TUI is the only writer of the answer payload and it offers no note affordance. Dropped from scope deliberately.
- ⚠ **A payload count from a `#`-and-blank filter is WRONG for Python**: docstrings are not `#`-prefixed, so a docstring-only commit reported 34 "payload" lines. The honest measure is `ast` — count statements and subtract docstring `Expr` nodes, or hash a docstring-blinded unparse.
- ⚠ **zsh ate `$sha:scripts/...`** as a history modifier (`:s`) → `bad substitution`. Brace it: `${sha}:path`.

## How to verify
```bash
# the merge, BY CONTENT (a squash is never an ancestor)
git -C /home/zach/workspace/devrc fetch origin -q
git -C /home/zach/workspace/devrc grep -c 'def menu_footer' origin/main -- scripts/tmux-reply-agent   # 3

# the deploy — the CONSUMER, not the deploy
for h in local laptop; do :; done   # workbench below; laptop via ssh zach@10.42.0.100
P=$(systemctl --user show tmux-reply-agent -p MainPID --value)
awk -F/ '{print $NF}' /proc/$P/cgroup            # tmux-reply-agent.service  (NOT head -1)
sha256sum /home/zach/workspace/devrc/scripts/tmux-reply-agent | cut -c1-16   # ac8e88ae960b18c5
python3 -c "import os;print(os.stat('/proc/'+'$P').st_mtime > os.stat('/home/zach/workspace/devrc/scripts/tmux-reply-agent').st_mtime)"  # True

# the suite
nix develop /home/zach/workspace/devrc -c python3 -m pytest \
  scripts/tests/test_tmux_reply_agent.py scripts/devhost-tests/test_claude_footer_sites.py \
  -q -p no:cacheprovider          # 201 passed

# the closing condition — NOT YET RUN
# raise a 2-option ask in a scratch tmux session, tap the SECOND option on its clawgate card, then:
#   tmux capture-pane -p -t <pane> | grep -A2 'User answered'
# the recorded label must be the one tapped.
```
