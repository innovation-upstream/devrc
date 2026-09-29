# Handoff: object-leak-guard — 2026-08-25 (rev 2)

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
Stop agent-created objects (ClickUp tasks, GitHub issues, PRs) accumulating unclosable.
The arc is named **`object-leak`**; the rule it produced is the **closing-condition rule**.
Full measurement record: `civitai/talos-infra:claudedocs/agent-object-leak-2026-08-23.md`.

## State now

**The guard is MERGED, DEPLOYED and BEHAVIOURALLY PROVEN LIVE.** devrc **#821** squash-merged
as **`12781a79`**, verified by content on `origin/main` (the branch head is never an ancestor
after a squash, so ancestry cannot settle this — four files compared blob-for-blob).

- **Live and behaviourally proven — the strongest evidence in this arc.** The gate fired
  inside a real session, through the harness, on a real `gh issue create`: `PreToolUse`
  denied it before `gh` ran, and nothing was created. The probe was aimed at a nonexistent
  repo on purpose, so a hook that had failed open would have errored on the missing repo
  rather than filing the object the gate exists to prevent.
- **Deployed:** `home-manager switch` → `rc=0`, generation **563** unchanged (a true no-op —
  a switch had already run post-merge). The `/nix/store` copy is byte-identical to
  `origin/main` (blob `7e875f1c`). Registered `PreToolUse` / matcher `Bash` with the pinned
  `/nix/store/…python3.12` interpreter, so it cannot fail open mid-switch (#658).
- **Also merged in this arc, all verified by content on their default branches:**
  talos-infra #1277 (stamp 3 CronJob producers), #1286 (Python `manual:<who>`), #1292
  (producer lookup fixes); devrc #768 (ClickUp stamping), #772 (the rule), #786 (single
  definition + guard), #795 (query.mjs regression), #803 (`manual:<who>` + task-hygiene flow);
  civitai #4333 (`CLAUDE.md` § Filing follow-up work).
- **Live but unproven:** the closing-condition *rule* reaches every session that loads it.
- **Deploy honesty:** talos-infra #1292 is Flux-reconciled and in the live ConfigMaps; its
  *behaviour* is unverified until the next producer run (capacity-sweep 08:00Z daily,
  reliability weekly Mon).

### What #821 grew between rev 1 of this doc and merge

Rev 1 said the head was `6e7aa7a3` with 383 passing and one open gap. **Both halves were
wrong**, and the way they were wrong is the most reusable thing in this document.

| ref | measured in a CLEAN checkout |
|---|---|
| `6e7aa7a3` — what was actually pushed | **380 passed, 3 FAILED** |
| the dirty tree rev 1 read | 383 passed, 0 failed |

`6e7aa7a3` had shipped the B2 heredoc-scoping **tests without their implementation**, so a
correct `gh issue create -t t --body-file - <<'EOF' … EOF` denied as unreadable. Three
rounds followed, each audited:

- **`2bd3d1e7`** — the create's own heredoc fell into no command span at all. One line.
- **`deee9f47`** — 🔴 **that fix REOPENED B2.** With two heredoc openers on one physical
  line, bash queues the bodies in operator order, so the span swallowed the *earlier*
  command's body while the create's own was skipped — exactly inverted. Seven shapes
  (`&&`, `;`, `| tee`, no body flag, `<<-`, CRLF, three chained) ALLOWed a bodyless issue,
  **with both CI legs green over it.** Fixed by attributing each heredoc to the segment
  containing its `<<` offset — `_Heredoc.op`, the field whose own comment already said it
  "is what attributes a heredoc to the COMMAND that opened it" and which `_shell_walk` had
  never read. `command_spans` → `command_segments`, returning reassembled text, because one
  `[lo, hi)` slice genuinely cannot express the answer when bodies interleave.
- **`d84835f6`** — two further confirmed bypasses. **A:** `URL="$(gh issue create …)"` was
  invisible while the same line without the two quote characters denied — the single most
  natural shape an agent writes. Fixed in the hook (`_shell_walk` **and** `_read_word`, the
  latter swallowing the whole assignment word so the walk never saw the opener);
  `guard_core.py` untouched. **B:** a duplicate `--body` bought a pass with ~40 characters
  of stock text that never reaches GitHub. Now one effective body per source.

Suite **251 → 474**. Full repo gate on the rebased tree: `RESULT: PASS`, 16410 passed / 0
failed, both tiers, matching CI exactly.

## Open investigations — live diagnosis state

**The crash-path investigation in rev 1 is CLOSED, and it was already closed when rev 1 was
written.** The amend at `6e7aa7a3` had fixed it; that commit's *message* still listed it
under "STILL OPEN", and rev 1 copied the message forward. Confirmed properly: an `if False:`
mutant on the crash branch is **killed** by `test_the_crash_path_denies_a_create_shape`
alone, failing with that test's own assertion (`expected a deny, got an allow`), and its two
negative controls hold — an unrelated `gh pr checks` still ALLOWs through a poisoned hook,
so the fallback is scoped rather than blanket.

### Three clauses in the shipped hook are NOT distinguished by any test
- **Observed:** `substitution_scopes`' blank-body skip SURVIVED its mutant and was deleted
  (`command_segments` already drops blank segments). `_shell_walk`'s unclosed-opener tail
  and `creating_invocations`' dedupe also SURVIVED.
- **Decision:** both survivors are KEPT for their fail-closed direction and say so in their
  own comments. **Do not cite them as covered** — that is the point of the labelling.
- **Next probe if anyone wants them pinned:** find an input where the tail and the dedupe
  change a verdict. Nobody has yet.

### Known-uncovered routes, pinned as gaps rather than fixed
`{ gh issue create …; }` and a function body `f(){ …; }; f` both ALLOW, because
`guard_core.commands` yields `argv[0] == '{'`. Both are in the docstring's NOT-COVERED
enumeration and pinned by `test_a_brace_group_is_a_KNOWN_UNCOVERED_ROUTE`, so the day the
behaviour changes, someone is told. Deliberate: the fix lives in the shared
`guard_core.py`, whose blast radius is every other hook.

## Next steps (ranked)
1. **Verify the #1292 producer fix behaviourally** (`talos-infra`): after the next
   reliability-sweep run, `agent/reliability-sweep-rightsize` should appear on issue **#176**
   for the first time — one query settles it. *Closing condition: that label present on #176.*
2. **Watch the guard's ALLOW direction in real use for a few days.** A permanently-red gate
   trains everyone to click through, and this one fires `PreToolUse` on every Bash call.
   The evidence is good (36 realistic calls in-suite, 29 driven against the deployed copy,
   0 false denials) but none of it is a week of real sessions. *Closing condition: no false
   deny reported by 2026-09-01, or one reported and fixed.*
3. The 30-day re-measure is clawgate task **#352** (2026-09-23). Do not run early; its
   comments carry the baseline and a discriminator to run first.
4. **Housekeeping:** `/tmp/wt-821fix`, `/tmp/wt-821base`, `/tmp/wt-821push` and the local
   branch `zach/gh-issue-closing-condition-guard` are leftovers from the pre-merge sessions.
   The remote branch was deleted at merge. *Closing condition: `git worktree list` shows no
   `821` entries.*

## Gotchas / decisions / dead-ends
- 🔴 **A GREEN SUITE AND A CLEAN AUDIT MEASURED ON A DIRTY TREE ARE CLAIMS ABOUT THE TREE,
  NOT THE COMMIT.** Rev 1's "383 passed / 0 failed" and its "everything else is CLOSED" were
  both read off `/tmp/wt-821fix` with an uncommitted one-line fix in it. At the commit the
  suite was RED and the PR had shipped tests without their implementation. `git status`
  before quoting any number, and re-measure in a detached worktree of the ref you mean.
- 🔴 **AN AMEND REWRITES THE TREE, NOT THE PROSE DESCRIBING IT.** `6e7aa7a3`'s message lists
  the crash-path test under "STILL OPEN, deliberately not attempted here"; the amend had
  fixed it. A whole next-step was spent re-deriving a closed item. Read the tree, not the
  message, when a commit says what it did *not* do.
- 🔴 **A FIX FOR A REAL BUG CAN OPEN A WORSE ONE, AND CI WILL NOT SAY SO.** `2bd3d1e7` was
  correct about the symptom, one line, matched a comment that had been wrong for weeks — and
  reopened B2 in seven shapes. Both tiers went green over it. The delta re-audit is the only
  thing that caught it. **Treat a one-line fix in a parser as a full change, not a nit.**
- 🔴 **MEASURE A TOOL'S SEMANTICS, DO NOT ASSUME THEM.** The audit asserted `curl -d` was
  last-wins like `gh --body`. It is not — curl **merges** repeated data options (`&` for the
  `-d` family, plain concatenation for `--json`), and `gh api` **hard-errors** on a repeated
  `body` field rather than taking either. Applying one rule everywhere would have created
  false denials in two directions.
- 🔴 **FIVE consecutive wrong conclusions in the pre-merge session, all from probe hygiene,
  none from the code.** Ad-hoc probes wrote fixtures to FIXED paths in shared `/tmp`
  (`/tmp/plan.md`, `/tmp/good-body-821.md`), so a leftover file was read by `--body-file`
  resolution and answered the guard's question. **Every fixture goes in a per-run
  `mktemp -d`, and verify the fixture encodes the case its label claims.** A sixth instance
  landed this session: a probe built a body with Python `%r`, which emits a literal `\n` —
  exactly the over-acceptance the guard denies — so a CORRECT deny read as a false positive.
- 🔴 **A MUTATION SWEEP IS ONLY A CLAIM ABOUT THE FIXTURES YOU IMAGINED.** Three of round
  three's guards SURVIVED on the first pass because a fixture split a JSON payload *inside a
  string literal*, so an inserted `&` landed harmlessly in a value and both joins parsed.
  Split at a structural position, and carry both a positive control that must die and a
  comment-only control that must survive — the second is what proves the sweep can report
  SURVIVED at all.
- **A subagent that finishes without emitting a report is not a stuck subagent.** The #821
  fix agent notified three times with `"Waiting."` and was stopped as looping; it had in fact
  completed every fix. Check the tree before believing the silence.
- **Subagents DO inherit `~/.claude/RULES.md`.** An earlier claim that they do not was a
  false zero: **transcripts do not record the system prompt**, so grepping them for rule text
  can never find it. Do not repeat that measurement.
- **The failure is salience, not delivery.** Blind test, private solo repos so the
  outward-facing rule could not confound: unbriefed subagent filed **6 issues / 0 closing
  conditions**; the same rule pasted into the brief gave **10/10**. Both had the rule.
- Semantic compliance is weaker than form compliance: in the briefed arm all 10 had the
  heading, most wrote the *remedy* under it rather than an end-state. The hook says so in its
  own deny message — passing it is a floor, not a verdict.
- **Duplication was never the problem** (~2% GitHub, 2.6% ClickUp, zero exact duplicate
  titles). A dedup-first design was drafted and cut on the measurement.
- `bash-guard.py` matches raw command text and cannot tell quoting from executing — it
  blocked a `grep` whose *pattern* contained a blind-stage command. Write such strings to a
  file with the Write tool.
- Not done deliberately: no stamping wrapper for session-filed `gh issue create` (the guard
  supersedes the need); MetaAgent is not ours to tune (no producer in any repo we control);
  CI node capacity is tracked as talos-infra **#1205**.

## How to verify

The guard is merged and live, so verify the **deployed** copy, not a checkout — and invoke it
through `~/.claude/hooks/`, never the resolved `/nix/store` path, or `guard_core` will not
import and you will measure the crash path instead of the gate.

```bash
HOOK=~/.claude/hooks/gh-issue-closing-condition-guard.py
DEVRC=~/workspace/devrc
# 1. the deployed copy IS what merged (blob compare, not ancestry — squash merges break that)
git -C "$DEVRC" hash-object "$(readlink -f $HOOK)"
git -C "$DEVRC" rev-parse origin/main:scripts/claude-hooks/gh-issue-closing-condition-guard.py
# 2. the suite, at the ref you mean, in a clean worktree
git -C "$DEVRC" worktree add --detach /tmp/wt-ghccg origin/main
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  /tmp/wt-ghccg/scripts/claude-hooks/tests/test_gh_issue_closing_condition_guard.py -q -p no:cacheprovider
# expect: 474 passed. Then: git -C "$DEVRC" worktree remove --force /tmp/wt-ghccg
```

**3. The one that actually proves it is live** — run a `gh issue create` with no closing
condition *from a session*, aimed at a repo that does not exist, and watch the harness refuse
it. The nonexistent target is the safety: if the hook were inert, `gh` errors on the missing
repo instead of filing the object. A subprocess battery cannot prove this half — it simulates
the payload rather than exercising the harness.
