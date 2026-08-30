---
# No clawgate-task — $CLAUDE_CODE_SESSION_ID was unset; the board was not asked.
---
# Handoff: browser-bridge-architecture-trace — 2026-08-27

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Trace and document the browser-bridge extension's full architecture (three-actor command channel) and the devrc skill+flows pattern (SKILL.md → reference/ → flows/) as observed in the codebase. Research-only session, no code changes.

## State now
- **THE EFFORT IS CLOSED.** Feature merged, shipped, live-verified; the doc itself merged; the stranded branch deleted; the claim released.
- **devrc #1063** — `bd1572f3` — the toolbar icon copies `bw://<host>/<instance>/<tabId>`; the CLI expands it into `--instance`+`--tab` from either argument position. MERGED.
- **devrc #1103** — `d4a3a000` — `SKILL.md` says `context` is how you RESOLVE a ref. MERGED.
- **devrc #1106** — this doc. **MERGED 2026-08-30T19:55:26Z, squash `bf665bbe`.** Verified BY CONTENT (86 lines present on `origin/main`), never by ancestry — a squash merge leaves `merge-base --is-ancestor` false forever, and it reads as "not merged".
- 🔴 **Rank 1 EXECUTED 2026-08-30.** `origin/docs/browser-bridge-tab-ref-proposal` (`d923cc4`) **deleted**; confirmed gone (`git ls-remote` → 0 rows). Guarded on both preconditions re-checked *immediately before* the delete, not from the earlier survey: sha still `d923cc43`, and PRs-ever-on-that-branch still `0`. Rollback if ever needed: `git push origin d923cc4:refs/heads/docs/browser-bridge-tab-ref-proposal`. Claim `browser-bridge-architecture-trace-1` **RELEASED**.
- **devrc #1112** — FILED: `resume-state.sh`'s handoff freshness check is blind to a newer copy on an unmerged branch.
- **devrc #1114** — FILED: the browser-bridge spool-leak flake, root-caused.
- **devrc #1091** — still OPEN, untouched (`forcing: none`).
- ⚠ **`personal - other` is STILL `extension_stale: true`** (`b817ef1e88267a40` vs expected `66b98084daecd880`), re-measured 2026-08-30. Operator-only.

## Open investigations — live diagnosis state
(No unresolved investigations — this was a read-only research session.)

## Next steps (ranked)
1. **Reload the extension in the `personal - other` Brave profile.** OPERATOR-ONLY: no `reload` among the 18 `ALLOWED_OPS` (`server.py:179-181`); `SERVER_OPS` is `("release",)`. Needs a human in `brave://extensions` on that profile, or a full Brave restart. Until then that profile's icon click runs the old no-op code.
   forcing: none
2. devrc **#1114** — the spool leak. A late emit thread from `test_cmd_succeeds_when_spool_unwritable` lands in the NEXT test's spool, failing `test_instances_and_poll_still_do_not_emit`. It has now failed **three** unrelated PRs (two recorded at `test_server.py:270-281`, plus #1106). ⚠ Adjacent to live work on the same file (#1109 + a claim) — coordinate before editing `test_server.py`.
   forcing: none
3. devrc **#1112** — `resume-state.sh` freshness check.
   forcing: none
4. devrc **#1091** — the click-wiring guard. Scaffolding only; cannot affect what ships.
   forcing: none

🔴 **All `forcing: none` — the effort is genuinely finished.** A `/resume` drawing from this list should go find work elsewhere; the four items are recorded so they are not lost, not because anything is pushing them.

## Gotchas / decisions / dead-ends
- The `reference/` vs `flows/` distinction is defined in `CLAUDE.md:127-131`: reference holds facts you verify against; flows holds procedures you execute. A flows file does not auto-fire — something must name it (a SKILL.md table row, or a hook that names the path).
- browser-bridge has no `flows/` directory because its ops are direct (command → result), not procedural multi-step workflows. clawgate has flows because task authoring and pickup are multi-phase procedures enforced by hooks.
- The `sites/` sub-registry under `reference/` is a special case: `_index.json` maps host suffixes to filenames, matched on label boundaries (not substring), longest-wins. The server emits `site_notes` on matching result envelopes.
- The BUILD_MARKER (`build_id.js`) is a generated literal, not a runtime computation — the only signal that describes running code rather than load directory. Two profiles on one directory can report identical version/id while running different code (#324).
- 🔴 **"18 ops" is `ALLOWED_OPS` — the shared CLI↔server↔extension contract. There is a 19th op name the CLI can send that the extension never sees**: `SERVER_OPS = ("release",)` (`server.py:189`), handled entirely server-side. It carries a footgun the op table above does not show — ownership is keyed `(instance, session)`, so a `release` **without** `--instance` drops this session's owned tab on *every connected profile*, not just the one you meant.
- 🔴 **Deployment of this subsystem is SPLIT, and `readlink -f` is the only arbiter.** `SKILL.md` and the `browser` CLI are `mkOutOfStoreSymlink`s into the repo — an edit is LIVE immediately, no switch. `server.py` is a `/nix/store` copy — editing the repo does NOTHING until `home-manager switch` (+ `systemctl --user restart browser-bridge`). `ls -la` shows only the first hop and misleads. The trace above describes SOURCE; it says nothing about what is running.
- 🔴 **A green test suite is explicitly NOT verification here** — `scripts/browser-bridge/reference/security-ops.md` owns the gate and makes live-verify against real Brave the bar. An in-process fake cannot meet it, so an agent working only from tests CANNOT close a bridge change; the loop is merge → switch → live-verify.

- **CARRIED FORWARD from the 2026-08-27 `How to verify` section**, which this update replaced —
  it is a dated measurement, not status, and the replace would have dropped it: the doc's
  architecture claims were re-verified against the tree on 2026-08-27 (op parity in
  `server.py:179` vs `protocol.js:47`; `protocol.js` chrome-free in code; the `sites/` matcher at
  `server.py:1153-1193`; both `nix/home.nix` line ranges; zero `child_process`/`exec`/`spawn` in
  `browser_tool_impl.mjs`). The clawgate reference-file count was wrong (11 → 12) and was corrected
  then. ⚠ Those line numbers are as-of 2026-08-27 and this session moved `server.py` not at all but
  `protocol.js`/`service_worker.js` substantially — re-derive rather than trusting the offsets.
- 🔴 **This doc's own earlier implementation plan was WRONG IN TWO PLACES, both found by building it.** They are recorded because the unmerged branch `docs/browser-bridge-tab-ref-proposal` still states them:
  - **`navigator.clipboard.writeText()` is NOT reachable from an MV3 service worker** — there is no `document`. Injecting into the page instead needs that document FOCUSED (it is not, after a toolbar click) and is refused under a strict page CSP, so it would have failed precisely on GitHub. The copy goes through an **offscreen document** (`reasons: ["CLIPBOARD"]`, `document.execCommand("copy")`).
  - **"first 8 chars of the auto-id" DOES NOT ROUTE.** `server.py` `_resolve_target_locked` matches `target in (inst.key, inst.instance_id)` — EXACT. An abbreviated id fails `unknown_instance` on first use. A ref carries the full routing key: the label when set, else the whole UUID.
- 🔴 **The feature would have shipped INERT without `clipboardWrite`, and no test tier could see it.** `execCommand("copy")` is permitted without that permission only inside a short-lived handler for a user action; this copy runs after `config()`, a tab query, a NETWORK `/whoami` round trip and `createDocument()`, in a document that never had transient activation. `execCommand` reports refusal by RETURNING FALSE, not throwing — so the symptom would have been a ✗ badge and an untouched clipboard on every click, forever. `action_click.test.mjs` mocked the clipboard reply as `{ok:true}` and `offscreen.js` was imported by ZERO tests. An audit measured it: 8 mutations of the offscreen writer, **7 survived** the full 869-pytest/549-node suite.
- 🔴 **Six audit rounds; only ROUND 1 found a defect that would have reached the operator.** Rounds 2–5 each found a real defect *in a guard written to close the previous round*: a refusal that named a spelling (`bw://`) instead of the state (any `TAB`, then any `FRAME`); a SKILL.md ledger guard that survived its own negative control because it matched a backticked word appearing one sentence later; a check that passed on the exact mutant its comment named as closed; and a positive control resting on the very defect it controlled for (it proved a stub could see inheritance BY USING `BB_INSTANCE`, so fixing that leak would have broken the control). **Rounds 5 and 6 changed ZERO payload lines**, which is the stop condition that ended the ladder.
- 🔴 **The wiring guard missed the same mutant FOUR times, and the FIX SHAPE was the cause.** Each round REPLACED the previous check (text pin → anchored regex → line-depth scan → raw-source scan) rather than joining it, so each closed one hole and opened another; round 4's rewrite resurrected three mutants round 3 had killed. It now asserts THREE independent things and says a future round may ADD to them but must not swap one for another. It is the SOLE coverage of the wiring — no test anywhere calls `startBackground()`.
- 🔴 **The two tiers disagree, repeatedly, and only the sandbox gates the merge.** Two of this effort's own tests were green on the dev host and RED in `nix build .#checks…pytests`: one pair asserted `browser agent` had reached the wire (it does so only once its model-backend prerequisites are met, which come from the developer's environment); another wrote a stub with `#!/usr/bin/env bash`, which does not exist in the nix sandbox. The second is what `scripts/testlib/mockbin.py` exists to prevent — its own docstring records four prior sites and warns that "a rule re-derived at every call site regenerates the same bug at the next one".
- **A `bw://` ref is resolvable with `browser <ref> context`** — 459 bytes, one round trip, no script injection (so no strict-CSP trap) and no dependence on the tab being foregrounded. It echoes back the extension's own `tabId`, so a routing mismatch is visible rather than assumed.
- **Contention, not code**: `SQLite database … is busy` / `opening lock file "…/big-lock"` / `test_live_cotenants_does_not_count_this_process` counting a live `git` are all the concurrent-nix-build false-failure class now documented in `CLAUDE.md`. Proven here: the SAME tree, same derivation, failed under load and passed quiet.

- 🔴 **2026-08-30 — `resume-state.sh` reconciled the WRONG COPY of this very doc, and said so in words that read as an all-clear.** `handoff-read: working-tree copy (identical to origin/main)` is a claim about tree-vs-`origin/main`, and both were the **superseded 64-line** version while the authoritative 86-line one sat on unmerged PR #1106. Everything downstream was therefore about a rewritten document: the lone DRIFT line named `PR #180`, which the current doc does not mention at all. The digest DID flag `! gh answered for 1 of 2 referenced PR(s)`, which was the only visible tell. **Generalise: a freshness check against mainline cannot see a newer copy on a branch, and in a repo that forbids committing to `main` that is where every fresh handoff lives.** Re-derive the authoritative copy with `git ls-remote --heads origin` + the doc's open PR before trusting the digest's framing.
- 🔴 **Same trap one level down: `handoff_doc.py` takes its base from `--repo`'s WORKING TREE.** Run from `main` on 2026-08-30 it would have rebuilt this doc from the 64-line copy and discarded the 22 lines PR #1106 added, at exit 0. The fix used here was to `git checkout -b <topic> origin/docs/handoff-browser-bridge-tab-ref` FIRST so the tree carried the authoritative base, then merge into that. **Check `wc -l` of the base against the newest branch copy before confirming any handoff merge.**
- **The subsystem index's `browser-bridge` `OPEN:` bullet is STILL genuinely open** (re-checked 2026-08-30, not taken on trust): a cross-instance `browser sessions` op. `sessions` is absent from `SUBCOMMANDS` at `scripts/browser-bridge/browser:581`.

- 🔴 **2026-08-30 — the shared checkout was switched OUT FROM UNDER this session, mid-work.** Reflog: `14:33:29 checkout: moving from docs/handoff-bb-resume-0830 to main`, followed by two `merge origin/main: Fast-forward` — none of them this session's. The commit at `14:17:44` was safe **only because `git branch --show-current` was echoed immediately before it**; had the switch landed a few minutes earlier the handoff would have been committed to `main`, silently, in a repo whose `CLAUDE.md` forbids exactly that. **The pre-commit branch echo is not ceremony — it is the whole defence, and this is a live instance of the hazard, not a hypothetical.**
- 🔴 **#1106's first gate run went RED on a test no markdown diff can reach**, and the second passed with no change to the payload. The failure was `test_instances_and_poll_still_do_not_emit` — a neighbour's leaked spool row, now #1114. **The re-run did not fix anything; it re-rolled a race.** Do not read the eventual green as evidence the first red was meaningless — read #1114.
- 🔴 **`git diff --stat <ref> HEAD -- <path> && echo "identical"` ALWAYS prints "identical".** `git diff --stat` exits 0 whether or not there is a diff, so the `&&` fires unconditionally. Used here it asserted a branch's `scripts/` matched main while the stat block directly above showed **1,791 lines across 5 files**. Branch on `--quiet` (exit 1 = differs), or read the stat — never chain an `echo` off a plain `diff`.

## How to verify
```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser

# 1. running code is the shipped build (the ONLY signal that describes RUNNING code)
$BB --instance work ping        # buildMarker must be 66b98084daecd880
$BB whoami                      # work: stale=False

# 2. the CLI half, end to end — resolve any ref the icon produced
$BB bw://workbench/work/<tabId> context     # echoes back that exact tabId

# 3. the refusals (each must FAIL, and send nothing)
$BB bw://laptop/work/123 context            # minted on the other host
$BB bw://workbench/work context             # malformed
$BB --instance work bw://workbench/work/123 context   # ref + flag
```
Repo gates: `nix build .#checks.x86_64-linux.pytests` and `…nodetests` — 🔴 **one at a time**; a combined invocation produces false failures (nix-store contention), documented in `CLAUDE.md`.
