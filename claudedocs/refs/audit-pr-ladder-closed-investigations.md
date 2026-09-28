# refs: audit-pr-ladder — closed investigations

Sibling reference file for `claudedocs/handoff-audit-pr-ladder.md`, created 2026-09-28 when
that doc breached the per-document ceiling in `scripts/tests/test_handoff_doc_size.py` and its
eviction playbook's step 2 applied.

🔴 **DATED MATERIAL ONLY — investigations already RESOLVED or CLOSED, kept verbatim so a future
session can read what was already tried rather than repeat it.** Nothing here is an open thread.
This file is NOT indexed by `handoff_search` (it is not a handoff doc), which is exactly why an
open item must never be moved here. Every open question, gotcha and ruled-out theory stays in
the handoff doc.

---

### RESOLVED — the sibling ordering race, and `where=` was never going to close it
- **Was:** `test_an_absent_origin_header_is_not_the_same_as_an_empty_one` issued both `tabs`
  commands, then waited for two rows and unpacked them positionally, while its own docstring
  says order between them is the signal. `emit_cmd_event` runs off the critical path, after the
  HTTP response, so file order was the scheduler's.
- 🔴 **The reason it survived an order-safety pass: `_wait_ops`' docstring said the site's
  `where=_routed_to(inst)` "keeps the order". IT DOES NOT.** A per-row predicate cannot order two
  rows that satisfy it EQUALLY, and this test's two do — same op, same routing key. `where=`
  separates your rows from a NEIGHBOUR's; that is a different hazard with a different remedy.
  Both hazards are now named separately in the test's docstring and in `_wait_ops`'.
- **Fix (`#1109`):** wait for row one, then issue command two — `_wait_events`' own sanctioned
  "order pinned structurally" form. The single routed row returned before the second command
  EXISTS is the first command's, by observation rather than by argument. `pair[0] == absent`
  then asserts the append-only order still holds, so a future regression says so rather than
  surfacing as a bogus attribution failure.
- **CONTROL, run, because a passing test proves nothing about why it passes:** swapping the two
  commands (keeping the sequencing) turns it RED at `absent["session"]` with
  `KeyError: 'session'` — **while `pair[0] == absent` still PASSES**. So the red is the
  assertions being genuinely order-dependent, not the new guard firing: the mutation died for
  the right reason. File restored afterwards, and the checkable form of that claim is: the
  worktree file, the commit, and the built store source are byte-identical.
  ⚠ **This line used to cite `sha256 1b42b227…` as the proof.** That digest names an
  intermediate working-tree state reaching no commit, so no reader can reproduce it. A later
  audit found the retraction had been ADDED under Gotchas while this line still MADE the claim —
  the doc retracting something it also still asserted, ~140 lines apart. **An append-only
  section cannot be corrected by appending a correction to a different section.**
- **Ruled out:** routing as the fix (closes the foreign-row half only — measured on `#1074`);
  and a tighter deadline as a concern — the change **doubles** the budget, one 10 s wait becoming
  two, worst case 10 s → 20 s.
- **Still open:** the verdict of the sandbox tier. See "State now".

### RESOLVED — round 1 of the blind audit found three defects, all in prose I wrote
- **Method note that earned its keep:** the auditor was dispatched BLIND — the diff and the
  checklist, not my conclusions. All three findings are the failure mode the PR exists to close.
- 🔴 **(1) A FALSE HISTORICAL CITATION, introduced by the fix itself.** I wrote that `#1074`'s
  pair reversed "with `where=` already in place". `git show e9f8ce14` refutes it: the flaking
  site was a bare positional `_wait_events(spool_dir, len(ORIGIN_TOKENS))` and `#1074` **added**
  the `where=`. And `where=` did not fix its order either — that site also became
  `sorted(...) == sorted(ORIGIN_TOKENS)`. **The true version is stronger:** two halves, two
  remedies, and a site whose order IS the signal cannot take the sorting one.
- 🔴 **(2) THE GUARD CARRIED THE IDENTICAL RACE.** `test_a_neighbours_row_of_the_same_op_is_not_
  selected_as_one_of_ours` — the test whose whole job is to protect the site I fixed — issued both
  commands before waiting, unpacked `first, second` positionally, and still carried the comment
  `# THE FIX: where= keeps the pair THIS test caused, in order`, the exact sentence the PR
  retracts twice elsewhere. **The retraction had been applied everywhere except the one place
  that most needed it.** Also: my sentence "THE ONE SITE IN THIS FILE THAT UNPACKS A PAIR" was
  wrong on both halves — after my own change the test I named no longer unpacks a pair, and an
  AST walk finds exactly one tuple-unpack site, which is this one. Now sequenced; control re-run
  on it specifically (RED at `first["session"]`, `pair[0] == first` passing).
- 🔴 **(3) THE NEW GUARD'S COMMENT OVER-CLAIMED — in the PR about over-claiming comments.** It
  said `pair[0] == absent` would report a lost sequencing. It cannot: re-fold the commands and
  `absent` becomes whatever landed first, which IS `pair[0]` by construction, so it stays green.
  Narrowed to the append-only-order invariant it really pins, and it now says outright that
  nothing there can detect the sequencing's removal.
- **Independently re-derived before fixing** — the `git show`, the AST walk, and the mutation
  were all re-run here rather than accepted from the agent.

### CLOSED, and the recommendation went stale mid-investigation — the memory-detail WIP
- **What it was:** `ship.sh` was authorised against a workbench tree holding another session's
  uncommitted `nix/graphical.nix`, `nix/pkgs/default.nix`, staged `scripts/memory-detail` and two
  untracked test files, deploying them to the workbench only.
- 🔴 **OWNER FOUND ONLY BY SEARCHING BOTH RUNTIMES** — opencode session
  `ses_fab8bd9e7ffe6En2UiziYXH9Md`, `run=d6cc95d5`, `directory=/home/zach/workspace/devrc` (the
  base clone, **no worktree**). **No Claude Code transcript contains an `Edit`/`Write` to those
  paths** — only mentions. Searching one runtime would have concluded nobody owned it, which is
  the identical finding this doc already recorded for `discord-embed-ext`.
- **Its agent-ledger record carries `pane_id: None`, `window_id: None`, `tmux_pid: None`** — a
  headless dispatch, never attached to a tmux pane, so `session-manager` could not find a window
  and there was no human to notify. The three live opencode windows all carry different session
  ids.
- 🔴 **RECOMMENDATION RETRACTED BEFORE IT WAS ACTED ON.** I recommended opening a PR for their
  work. Between recommending and re-checking, **the owner landed it themselves** —
  `0c0b8794 feat(bar): memory block left-click opens top RAM consumers view` on
  `feat/memory-detail-click`, pushed. Acting on the recommendation would have DUPLICATED their
  work, which is the shared-queue hazard `claim-work` exists for. The state moved under a
  recommendation that was correct when made.
- **Residue:** `nix/pkgs/default.nix` (`inxi`/`cpu-x`) is still uncommitted, so the workbench has
  two packages the laptop lacks.

### RESOLVED — #1342's controls are reachable, and there are EIGHT of them, not six
🔴 **This block was EDITED IN PLACE, not appended to.** `Open investigations` is an append-only
section under `handoff_doc.py`, and this doc already records that a correction appended to a
different section leaves the original still making its claim ~140 lines above. The heading and
the count below are corrections to text that was wrong; the original wording is quoted where it
is load-bearing rather than left standing as a live claim.

- **The count in the original entry was WRONG, and it is the shape this thread keeps finding.**
  It read "six control assertions … plus the four separators", which reconciles with nothing:
  the block is **5 `assert` statements** carrying **8 distinct control claims** (2 `shell_code`
  overshoot directions + 2 `last_command` overshoot directions + a 4-iteration loop over the
  separators `;`, `||`, `|`, `&&`). Re-derived by reading the block, not by re-quoting the
  handoff — the same rule this doc already carries three times over.
- **REACHABILITY, measured first, because it was the live risk.** The controls sit after an
  early `return` in `test_the_cached_build_fallback_is_emitted_with_its_guards` (line ~3085:
  the test bails when the brief fences no sandbox tier) **and** after a `len(blocks) == 1`
  assert. Either would have made all eight vacuous while the suite stayed green. Measured at
  `39c31521`: `run_main(["900"])` → rc 0, the precondition string IS present, and exactly
  **one** `nix log` fenced block is emitted. So the block executes.
- **OWN-REASON, measured per control.** Each of the eight was isolated by mutating the PARSER —
  never the assertion, which would only prove the assertion exists — so that exactly one
  control's claim breaks and it is the FIRST to fail. All eight: **KILLED, carrying their own
  message.** The four separator iterations are discriminated by the sep token their message
  names (`';'` / `'||'` / `'|'` / `'&&'`); `"reached by '|'"` is not a substring of
  `"reached by '||'"`, checked, which is what makes those two rows different measurements.
- 🔴 **BOTH HARNESS CONTROLS RUN, and the second is the one that makes the first readable.**
  Positive: `shell_code` stubbed to `return ""` is KILLED (the batch's known-caught mutant, so
  a stale `.pyc` scoring SURVIVED would show). Negative: mutating `shell_code`'s
  backslash-inside-double-quotes branch — which no fixture and no line of the emitted block
  reaches — **SURVIVED**, proving the harness can report SURVIVED at all. Run under
  `PYTHONDONTWRITEBYTECODE=1` with `-p no:cacheprovider`, each mutation asserted to have landed
  on disk before the run, and the file restored from a `cp -a` copy (never `git checkout --`,
  per this doc's own incident).
- 🔴 **A KILLER SET CANNOT SEE THESE, AND THAT IS A SEAM, NOT A DETAIL.**
  `mutants-audit-dispatch.py` expects each row to name the TESTS that must kill it; all eight
  controls live inside ONE test, so eight rows would report the same single name and read as
  coverage while measuring one. They landed as a second table, `TESTLIB_ROWS`, which mutates
  `scripts/tests/test_audit_dispatch.py` (not `audit-dispatch.py`) and matches the failing
  assertion's own MESSAGE, failing a row when ANOTHER row's message appears.
- **And the ledger that grades the fix matrix could not see them either** —
  `_known_mutant_ids()` read `mod.ROWS` alone, so a future matrix row citing `T5` would have
  been rejected as "a mutant the harness does not carry". Widened to both tables; it is a
  membership set, so widening cannot turn a passing row red, and deleting `TESTLIB_ROWS` now
  breaks the suite at import rather than silently.
- **Ruled out:** that the mutants could be scored without executing — the negative control
  above is what rules it out, not the `PYTHONDONTWRITEBYTECODE=1` flag on its own.

### (historical) UNVERIFIED at merge: are #1342's new control assertions reachable?
- **Symptom + exact repro:** #1342 added control assertions pinning both overshoot
  directions of the new `shell_code()` / `last_command()` parsers, plus the four separators the
  scanner must recognise. **Nobody checked they are REACHABLE and fail for their OWN reason.**
  The round-2 delta audit of #1342 was stopped by the operator after clearing items 1–3 and
  before reaching this one. 🔴 **CLOSED — see the RESOLVED block directly above.** The original
  wording said "six"; there are eight.
- **Observed (with values):** items 5 and 6 WERE closed by hand against `origin/main`:
  FIX_MATRIX = **106 rows** read from the file, `MIN_FIX_MATRIX_ROWS = 101`, and the repo
  formula `106 − min(50, max(1, 106//20)) = 101` agrees. All four rows present (`r18/F1`,
  `r18/F3` corrected; `r19/A1`, `r19/A2` new).
- **Ruled out:** that this blocks the merge — the ladder's own attribution gate says otherwise:
  the fix round preceding the merge changed **zero payload lines** (`scripts/audit-dispatch.py`
  untouched; `74cb7409..ee201067` = `test_audit_dispatch.py` 249/32, `mutants-audit-dispatch.py`
  55/0; rc 0, silent stderr), so one further round would have fired the gate. via: measurement
- **Leading hypothesis:** the assertions are fine — they were written alongside measured
  attacks — but "a control that passes vacuously is worse than none", so this is genuinely
  open, not dismissed.
- **Next probe:** mutate each of the six control assertions individually, under
  `PYTHONDONTWRITEBYTECODE=1`, and confirm each fails with its OWN message rather than a
  neighbour's; keep a known-caught mutant as positive control and report the pair.

### RESOLVED — `main` red from #1439's test stubs
Superseded: the "`main` is red: #1439's test stubs wrote their own shebang" block above is
CLOSED. `#1461` → `30a1eb8b`; `test_runtime_shebangs.py` green on `main` (9 passed), and the
guard's own stale-pin accounting passes with it. No ALLOWLIST entry was added — the offenders
were removed, not pinned. Its "Next probe" is spent; do not re-run it.

### RESOLVED — the `#1495` ladder ran to a clean stop; the guard took four versions

- **Observed (with values):** rounds 0 → 1 → 2 → 3. Round 3 returned **no code defect**. Each guard version was defeated by a *different* mechanism, every one found by mutation rather than reading:
  - **v1 proximity** ("a retraction within 20 lines") — SURVIVED an in-place re-assertion, because the retraction note the same commit added satisfied the window. No window size fixes it.
  - **v2 per-file COUNT** — SURVIVED the same mutant for an unrelated reason: the edit replaces the quoting line with an assertion built from the same tokens, so the count is identical whether lines or matches are counted. **A count cannot tell quotation from assertion.**
  - **v3 normalised TEXT pin** — closed that, but narrowed `FIGURE` to the literal `min`, re-opening the class the guard exists for: the SPELLED-OUT unit ("minutes"/"mins" rather than "min") became invisible. ⚠ The literal figure is deliberately NOT quoted in this doc — see the gotcha below.
  - **v4** — `min(?:ute)?s?`, and the comment-lead strip's `*` branch requires a following space (it was eating one star of a markdown `**BOLD**` run).
- **Ruled out:** *"the count ledger was the remedy"* — measured, M3 survived it. This was recorded as the lesson in the `devrc/tests` cairn entry and stood FALSE until a post-merge sweep; corrected at revision `64a2bba6`. `via: measurement`
- **Ruled out:** *"the sandbox tier is fine because a no-`.git` replica passes"* — the replica is a proxy; the authoritative answer came from `tekton/devrc-pytests` at `760c8769`. `via: measurement`
- **Stop grounds (two, independent):** round 3 clean, AND the attribution gate fired — `d6a5aa42..2d89291a` and `2d89291a..1c3b59b2` both touched the test file only, leaving `scripts/scoped-tests.sh` untouched, i.e. two consecutive zero-payload rounds.

### RESOLVED — rank 1: round 0 WORKS; its dispatch trigger is the defect
- **Question:** the retirement condition says run round 0 on 3-5 PRs and `DELETE this section if it ran and changed nothing`.
- **Answer: `ran: 6 · changed the outcome: 3` — it stays.** Evidence, measured:
  - **Round 0 changed an outcome.** `#1518` closed unmerged `2026-09-12T01:39:29Z`, its closing comment crediting *"round 0 and round 1"* for the counterexample that falsified its premise. `gh pr view 1518` → `CLOSED`, `mergedAt: null`; the step it proposed deleting is still wired (`git show origin/main:claude/skills/resume/SKILL.md | grep -c handoff_search.py` → 1).
  - **None of MY three could act.** `#1523` merged 27 min before dispatch, `#1510` +6 min after, `#1518` closed 5 min before trial 4 returned. Runtimes 459 / 920 / 776 s.
- **Ruled out — "delete it, it ran and changed nothing":** the condition requires *ran AND changed nothing*; it changed things every time it landed before the decision. `via: measurement`
- **Ruled out — "the auditor is too slow":** see the runtimes against the merge offsets; no speedup reaches either. `via: measurement`
- **Ruled out — "trial 4 independently confirmed `#1518`'s premise":** it re-implemented the same method from its description and inherited all three of its flaws. `via: measurement`
- **Next probe — build the trigger, stop measuring.** A PR younger than 15 minutes, audited immediately, is the only missing cell:
  ```bash
  gh pr list --repo innovation-upstream/devrc --state open --json number,createdAt \
    --jq '[.[]|select((now - (.createdAt|fromdateiso8601)) < 900)|.number]'
  ```

### RESOLVED — rank 14's sweep ran; the open question moved to how a rule is DATED
- **Resolved:** the full-corpus run completed at 2026-09-12T20:33Z, `EXIT=0`, over **5,993
  transcript files** (both tiers), 5,855 surviving the prefilter. **Controls PASSED and were
  read before any row: positive 584, negative 0.** Verdicts: `FIRED=14 · UNFIRED=3 ·
  UNRELIABLE=32`. Raw output `…/scratchpad/r14/full.{txt,json}`; re-run with
  `python3 scripts/audit-rule-firing-sweep.py --samples 1`.
- **Ruled out — the run died when I deleted the script from the base clone mid-run.** It did
  not: the process had already loaded and compiled the source, and `rchar` was still climbing
  past 4.4 GB after the delete. via: measurement
- **Ruled out — "sweep not running", which my own detector reported once.** A broken detector,
  not a dead process: the loop matched the `zsh -c` wrappers `pgrep -f` returns for my own
  shell (the documented trap) so `ls -d /proc/...` took several PIDs and failed.
  `pgrep -af … | grep -v 'zsh -c'` showed the python process throughout. via: command
- **Ruled out — UNRELIABLE means my regexes are bad.** For 20 of the 32 the pre-origin count
  is 1–8, which is a rule whose practice predates its current sentence rather than a loose
  pattern; only ~12 (≥10 hits) are genuinely over-broad. The dominant cause is the DATING.
  via: measurement
- **What is still open, and it is a different question from the one rank 14 asked:** how to
  date a rule so the window survives the rule being reworded. **Next probe:** add a per-rule
  `origin_hint` to the ledger (or date at the earliest commit touching the containing `## `
  section) for the 20 low-pre-origin rules, re-run, and check the withheld count drops
  without loosening the control. 🔴 Do not raise a pre-origin threshold to make them pass —
  that number was measured nowhere.
