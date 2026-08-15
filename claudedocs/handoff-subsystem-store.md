# Handoff: subsystem-store — 2026-08-13

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it — the
`devrc/subsystem-index` entry describes the very tooling below. 🔴 RECALL, NOT LIVE
OBSERVATION: every line is a pointer to VERIFY, never a current reading, and it may describe
a gotcha already fixed. `scope-absent`/`scope-empty` means nothing recorded yet: ordinary,
not an error, not a clean bill of health. Non-blocking — if it exits non-zero, print the
stderr line and carry on. **Two measured sessions skipped this when it was only reachable via
`/resume`; it is here because reading this doc is the one thing both of them did first.**

## Goal
A durable store for subsystem data + history with clean Claude Code integration.
**Built, deployed, verified at the consumer on both hosts.** The read half is cheap enough to
run every `/resume`, searchable, and survives a malformed entry. What remains is measurement,
not a build.

## State now

- **Branch:** `main` at `98ce099`, clean tracked tree. The base clone is SHARED — other
  sessions have moved it mid-work twice today. Re-check `git branch --show-current`
  immediately before any write; do the work in a worktree.
- 🔴 **HOSTS ARE CONVERGED — the split recorded below is CLOSED.** `ship.sh` ran clean and
  was verified AT THE CONSUMER, not at the deploy: `readlink -f $(command -v opencode)` on
  the workbench **and** the laptop both resolve to
  `/nix/store/rcrzfd71k96f1r55533lzc16p7ix3v22-opencode-1.18.16`. 432 (workbench) / 393
  (laptop) managed artifacts, 0 dangling, 0 absent. The dev-host gate that was red here
  (`assert '1.18.4' == '1.18.16'`) now passes.
- **Merged since the last handoff (3 mine):** `#496` the `--push` pre-check, rewritten onto
  `ls-remote` after audit · `#498` the index write ungated · `#502` the deployed-state doc
  lines updated for the convergence.
  (`#492`, `#497`, `#499`, `#500` merged today are **other sessions'**.)
- **The handoff skill's own step 4 no longer asks y/N** and the change is DEPLOYED (verified
  in `~/.claude/skills/handoff/SKILL.md`, not inferred from the merge). Step 5's push gate is
  kept, and the asymmetry is stated in the source so it does not get "harmonised".

## Open investigations — live diagnosis state

### The worktree-path bucket — MEASURED 2026-08-15, and the framing was wrong
🔴 **`--session` is THIN, not dead. "Structurally empty here because of the worktree rule" is
REFUTED.** Over 14 recent devrc sessions, using the repo's own `collect_session_paths`: 12 did
file work, **only 1 had an empty in-cwd set**, and **41 of 232 paths (17.7%) landed under cwd** —
independently matching the 13.9% over 3,913 paths recorded below, from a different sample. What
IS true is the threshold: only **5 of 12** reached the 2 paths a nomination needs, so the window
usually cannot nominate even when it is not empty. `--pr`/`--commit` stay primary for work that
LANDED; `--session` stays worth running.

🔴 **How this nearly went the other way.** Three agent reports of "0 under cwd, N outside" were
about to be written into the store as a structural fact about this host. They are the tail: 1 in
12. The scan takes two minutes and the entry it would have created would have outlived the
session that got it wrong — which is the entire premise of the store. **Do not promote an
anecdote to a structural claim without the scan.**

⚠ **The CLI cannot reproduce this.** `--session` refuses any transcript not written in the last
30 minutes ("this is not the session that is running now"), which is correct for a writer and
fatal for a retrospective. Measure through `collect_session_paths(..., max_age_seconds=inf)`,
which reads the path distribution without pretending to be those sessions.

- **Original symptom (the tail case, not the norm):** a session whose edits all land in a
  throwaway worktree gets `status=looked-at-nothing` from `--session`. Seen **4×** on 08-13;
  most recent: 0 paths under cwd, **23 outside**, all worktree paths (civitai scope).
- **Observed:** `#459` does **NOT** fix this. Its window reports paths lexically under
  `--repo`; a worktree at `/tmp/…/wt-x/src/foo.ts` is not. Measured bucket: **143 of 945**
  outside-cwd paths were temp worktrees, of which only **32 still existed on disk** — and 38
  such worktrees were deleted the same day. The correct source today is `--pr` / `--commit`,
  which is what all 4 sessions used.
- 🔴 **Ruled out — my own motivating number was WRONG.** I claimed 112 cleanly-attributable
  cross-repo paths. Actual derivation: 185 loose → −97 same-repo-from-a-worktree → 88
  different top-level dir → −58 `devrc-*` **sibling worktree directories of devrc itself**
  (`devrc-fix443`, `devrc-clickup`, `devrc-clawgate-ext`, `devrc-cutguard`, `devrc-458fix`,
  `devrc-fix444`) → **30 genuinely cross-project** (24 `homelab→civit`, 6 `homelab→devrc`).
  Corroborated independently at 33. The bug was `repo_of()` treating every top-level dir under
  `~/workspace` as a separate project — a label claiming more than its predicate tested.
  **The 112 is RETRACTED; do not re-derive it.**
- **Leading hypothesis:** the practical win from `#459` is mostly the **97 same-repo-from-a-
  worktree** paths, not the 30 cross-project ones. Worktree→repo mapping is fragile by
  construction — nothing in a transcript maps a worktree back to its repo, and the worktree is
  usually already gone.
- **Next probe (verbatim):** before building anything, measure the yield —
  `python3 scripts/lib/subsystem_touch.py --repo <r> --session <uuid>` on a worktree-only
  session, and read `changed_paths_outside_cwd` against what `--pr` recovers for the same work.

### The doc hook FIRED on its first exposed continuation — n=1, and that 1 is contaminated
- **Symptom:** the index read is skipped by sessions started from a kickoff block.
- 🔴 **Every earlier zero was measured PRE-EXPOSURE — the fix had n=0, not n=2.** `#457` put
  the block in the doc at **20:36Z**. Reconstructing each session's actual `Read` *result* and
  grepping it for `Run this first`: `d5db63c4` read the doc 3× (17:54Z, 18:02Z, 19:34Z) — all
  before 20:36Z, hook absent from every one. `e98279bd`'s later read (22:27Z) was
  `offset:140, limit:95` and never saw the top of the file. **Exposure is a property of the
  READ, not of the session's start time — check the tool_result, not the clock.**
- **Observed (2026-08-13, session `4a7d5bf8`, the FIRST exposed continuation):** read the doc
  at turn 1 → ran `subsystem_recall.py --repo` as its **first Bash call**, before any other
  work. `Skill` calls **0** (so `/resume` never loaded — the prefix is still inert as prompt
  text). Attribution is clean: `subsystem_recall` appears in **zero** always-loaded surfaces
  (`~/.claude/{CLAUDE,RULES,PRINCIPLES}.md`, `devrc/CLAUDE.md`, `MEMORY.md`); the only surface
  naming it is the `/resume` skill body, which was never loaded. **The doc was the sole path.**
- 🔴 **Contaminated — do not read this as an adoption rate.** That session's kickoff said
  *"measure whether the doc hook actually fires"*, which primes the behaviour being measured.
  It establishes the mechanism is **capable** of firing unaided by `/resume`; it does **not**
  establish that a session with an ordinary kickoff will run it. **n=1 uncontaminated: 0.**
- **Ruled out:** that the `/resume` prefix alone suffices (`#446`'s claim, retracted in
  `#457`). A subagent receives the kickoff as prompt TEXT — no CLI slash-command parsing.
- 🔴 **Ruled out — counting MENTIONS is not the measurement.** A grep showed `recall=3
  touch=60` and read as success; parsing actual `tool_use` calls gave **0** recall executions.
  The 60 were the agent *editing* `subsystem_touch.py`.
- 🔴 **Parsing `tool_use` is NOT sufficient either — three false-positive modes survive it,**
  found only because the probe reported **5** for a session known to have run it **once**:
  (a) **substring containment** — `test_subsystem_recall.py` contains `subsystem_recall.py`,
  so every `pytest` run and every `git add` of the test file counted; (b) **heredoc bodies** —
  `python3 - <<'PY' … PY` reads its script from *stdin*, so the body is DATA, yet splitting the
  command on newlines turns each mentioning line into a fake invocation; (c) `python3 -m
  pytest <path>` / `python3 -c`. Require an **exact basename match** on a token that is the
  script argument, strip heredoc bodies first, and reject `-c`/`-m`/`-`.
- **Instrument validation that the numbers rest on** — three controls, all watched:
  negative (6 mentions, 0 executions) · false-positive (5 mentions in the shapes above, 0) ·
  positive (4 distinct invocation shapes, 4/4). Plus a live control: the measuring session's
  own ground truth of exactly 1 read back as exactly 1. **The pre-fix probe passed the
  positive control and would still have been wrong** — it had no false-positive control.
  The probe itself lives only in that session's scratchpad and is **gone**; the recipe above
  is the durable form. Land it under `scripts/lib/` if this gets measured a third time.
- **Observed (2026-08-13, a DISPATCHED agent, ordinary kickoff naming only the doc and
  next-step 3):** tool call 1 = `Read` the doc, tool call 2 = **execute
  `subsystem_recall.py --repo`**, before any task work. `Skill` calls **0**. So the kickoff
  reaching a subagent as plain prompt text — the thing that made `#446` inert — does **not**
  stop the read happening, because the instruction now travels in the doc the agent reads
  anyway. Counted from the agent's own transcript, not its self-report: it *claimed* it ran
  the index read first, and the claim happened to be true, but the claim is not the evidence.
- 🔴 **The remaining contamination is STRUCTURAL and a staged probe cannot remove it.** This
  doc describes the experiment, and `Read` returns the whole file — so any agent dispatched
  here can see it may be the subject. Two exposed continuations, two fired, neither clean:
  the first was told by its prompt, the second could read about itself. **Do not stage a
  third and call it uncontaminated.** Measure organically instead: parse the next few real
  continuations. The instrument recipe is above; it is three controls and twenty lines.

### The two hosts run different opencode, and five doc lines are false while they do
- **Symptom:** `ship.sh` rc=7 — workbench SKIPPED, laptop converged. The dev-host
  `scripts/gate.sh --tier pytest` is RED on the workbench and will stay red until it converges:
  `test_engine_is_the_version_every_measurement_is_keyed_to` compares the binary against
  `PINNED_VERSION` and gets `assert '1.18.4' == '1.18.16'`.
- **Observed (values):** `readlink -f $(command -v opencode)` → workbench
  `/nix/store/64n428…-opencode-1.18.4`, laptop `/nix/store/rcrzfd71…-opencode-1.18.16`;
  `flake.lock` pins 1.18.16. `ship.sh` named the three blocking files verbatim
  (`scripts/run-tests.sh`, `scripts/tests/test_agent_ledger.py`,
  `scripts/tests/test_session_manager.py`) and left the host **exactly as found** — the
  protective outcome, by design.
- **Ruled out:** that this is a nix error. The laptop's first switch failed the same way
  (`converge exited 9`) on **pre-existing FOREIGN files** — `~/.config/opencode/agent/review.md`
  and `plugin/guard.js`, read-only with 1969 mtimes, stale pre-#469 store copies. Preserved to
  `~/foreign-opencode-preserved-2026-08-13`, diffed (they differed only by #469's edits), removed,
  re-switched clean.
- **Blocked on, not broken:** the blocker has since cleared — that session committed and merged —
  so `ship.sh` would now SUCCEED at `git checkout main` and switch a live session off
  `feat/agent-activity-ledger`. That is why it was not re-run. **Decide before running it.**
- 🔴 **Consequence to fix the moment it converges:** five DEPLOYED-STATE lines in
  `scripts/browser-bridge/{README.md,reference/agent.md}` say "Both hosts run 1.18.4". True today,
  false the instant the workbench switches, and the `HISTORICAL_VERSION_CLAIMS` ledger **exempts
  them**, so nothing will catch it. One-word fix to 1.18.16 once both hosts agree.
- **Next probe (verbatim):** `scripts/ship.sh` then, on the workbench,
  `readlink -f $(command -v opencode) && opencode --version` — a deploy reporting success is a
  claim about the DEPLOY, not the consumer.

### The doc hook fires; the uncontaminated sample is still n=0
- **Observed:** two exposed continuations, two fired. A CLI session read the doc at turn 1 and ran
  `subsystem_recall.py` as its first Bash call; a **dispatched agent** with an ordinary kickoff did
  the same at tool calls 1→2, with **0 `Skill` calls** — the case `#446` failed.
- 🔴 **Both contaminated, differently, and the residue is STRUCTURAL:** the first session's prompt
  named the measurement; the second could read about the experiment because this doc describes it
  and `Read` returns the whole file. A staged probe cannot fix that. **Do not stage a third and
  call it clean** — parse the next few organic continuations instead.
- **Instrument (reusable, and the reason to trust the numbers):** parse `tool_use` calls, never
  string mentions — and three false-positive modes survive naive parsing: `test_<name>.py` contains
  `<name>.py`; heredoc bodies (`python3 - <<'PY'`) are DATA yet split into fake invocations;
  `-m pytest`/`-c`. Require an exact basename match, strip heredocs, reject `-c`/`-m`/`-`.

### RESOLVED 2026-08-15 — the opencode host split (kept for the trail, do not re-open)
Both hosts are on 1.18.16, verified at the consumer (values above). The five DEPLOYED-STATE
doc lines went false exactly as predicted, and the version ledger **caught it**: updating
them orphaned all five exemptions and the suite went red. 🔴 The prediction in the previous
revision — "the ledger exempts them, so nothing will catch it" — was WRONG, and usefully so:
the exemptions themselves are asserted, so the shrink direction fires. The category earned
its separate name: *a historical record never stops being true; a deployed-state claim stops
the day you ship.*

### One test failed ONCE and I could not reproduce it — three mechanisms eliminated
- **Symptom:** during the `#496` merge resolution, the combined run of
  `test_handoff_doc.py + test_subsystem_touch.py` failed at
  `test_the_LENGTH_bound_is_not_vacuous_git_WOULD_have_expanded_it`,
  `scripts/tests/test_subsystem_touch.py:5841`: `assert expanded == [sha]`, where
  `expanded = _run_git(repo, "rev-parse", f"--disambiguate={sha[:3]}").split()`.
- **Ruled out — prefix collision** (my first theory, and the plausible one): a 3-hex prefix
  matching more than one object. **0 of 550** trials, measured at TWO repo shapes — 0/250 on a
  minimal repo, then 0/300 on the REAL fixture (`_init_repo` + `_commit`, ~9 reachable
  objects) after the first probe used the wrong shape.
- **Ruled out — order dependence:** `pytest-randomly` is NOT installed (checked
  `importlib.util.find_spec`), so collection order is deterministic; the same order passed
  on re-run.
- **Ruled out — a swallowed git failure:** `_run_git` asserts `proc.returncode == 0`
  (`test_subsystem_touch.py:184`), so a transient git failure surfaces there, not at 5841.
- **Ruled out — merge-induced:** passes in isolation, passes in the same combined pair, and
  40/40 in a loop on the merged tree. The authoritative nix gate is green.
- **Leading hypothesis:** none that survives. Say so rather than picking one.
- **Next probe (verbatim):** if it recurs, capture the FULL failure block before re-running —
  `nix-shell -p python3Packages.pytest python3Packages.pyyaml --run "python3 -m pytest
  scripts/tests/test_subsystem_touch.py scripts/tests/test_handoff_doc.py -q -rs" >
  /tmp/f.log 2>&1` — and read `expanded`'s actual value. Every elimination above assumed the
  assertion compared `[sha]` against a LONGER list; a shorter or different one points
  elsewhere and none of this work applies.

## Next steps (ranked)
1. **Nothing is queued.** Every item raised this session is landed and verified. The two
   standing offers, both declined or deferred by the operator, are below.
2. **The `__pycache__` false-green is still unwritten** (operator said skip, 2026-08-15).
   `cp -a` preserves `__pycache__` and a SAME-LENGTH source mutation does not change file
   size, so the `.pyc` staleness check (mtime+size) misses it and pytest imports the CACHED
   module — a mutation battery then reports a mutant as SURVIVED when it never ran. Bit this
   session twice. It belongs in `claude/RULES.md` and needs an eviction in the same commit.
3. **Get an ORGANIC doc-hook sample** — still `n=0` uncontaminated. Do not stage a third
   probe; parse the next few real continuations.
4. **Watch the `analyze-service` share of the census**, not the total: 13 of 14 stamped
   entries are `handoff`, so the store is effectively single-writer.

## Gotchas / decisions / dead-ends

- 🔴 **Never read an exit status through a pipe.** `cmd | tail; echo $?` gives tail's status.
  This reported `ship.sh` as green when it was **rc=12** — and that ignored rc=12 was a real
  broken managed artifact which sat for 15h and later **failed a switch outright (rc=9)**.
- 🔴 **`nix build` returns a CACHED result silently** — 0-byte log, exit 0, `grep FAILED`
  matches nothing. `nix log <drv>` is the real output for a cached drv. `--rebuild` forces a
  re-run but **errors on a drv never built** *and* on one whose previous build failed. A valid
  cached output is itself proof the suite passed (`flake.nix:268` fails the derivation).
- 🔴 **A pytest failure does NOT print `FAILED` here** — the runner uses `-q -rs`. Look for
  `=== FAILURES ===`, the summary line, and `FAIL  <dir>  (… failed=N …)`. Runner lines are
  prefixed `devrc-pytests>`, so **never anchor a grep at `^`**.
- 🔴 **`rev-list origin/main..HEAD` is NOT a merged-ness test.** A squash merge never makes the
  head an ancestor — it flagged **all 52** worktree branches as unmerged work. Classify by PR
  state, or by `git diff origin/main <head>` being empty. This misled me **3×** today.
- 🔴 **Measure "did this branch land" by branch-ADDED lines present in main
  (`merge-base..head`), never `git diff main head`** — the latter is dominated by what *main*
  added since the fork and reported 100–500 changed files for branches that touched 3.
  Validate the instrument first: positive control (a known-merged head → 100%), negative
  control (a never-merged head → 2%). **A low score is not proof of lost work** — rewording,
  restructuring and retired paths all score low; read every head under ~95%.
- 🔴 **A LOCAL tag is not a backup, and removing a DETACHED worktree makes its commits
  GC-able.** Tag before a destructive sweep, **push the tags, then read them back from the
  remote** — `git ls-remote --tags origin` is the check, not `git tag -l`.
- 🔴 **Re-check the branch IMMEDIATELY BEFORE a write, and gate on it** — printing it in the
  same command is not checking it. I ran `checkout --` + `merge --ff-only` on another
  session's branch that way; `--ff-only` refusing rather than destroying is what saved it.
- 🔴 **A failed `home-manager switch` is usually a pre-existing FOREIGN path.** A **dangling**
  symlink still blocks `mkdir` ("File exists"). Inspect → record its target → remove → re-switch.
- 🔴 **Front matter is parsed LINE BY LINE.** An `aliases: [...]` wrapped over two physical
  lines reads as an unterminated bare string and **used to kill the entire scope**. `#449`
  made the reader degrade; **always run `--validate <path>` in the same turn as a write**.
- **Mutation found what reading did not.** 3 of the 4 highest-value findings on `#459` came
  from mutants while the gate stayed green: a surviving `total > cap` → `>=`, a truncation
  note whose **outright deletion survived the whole suite**, and an unkillable clause.
- **A green gate certifies nothing broke; it never says a guard is reachable or a boundary
  covered.** `#442` shipped 3 pagination defects past 3,679 green tests because the largest
  real scope (26) is far below the 100 cap — the feature's own boundary was unreachable from
  real data.
- **`browser-bridge failed=1` was NOT load** — 1.45× wall time, not the ~15× the rule needs.
  The decisive argument is structural: no import or exec edge from the changed modules.
- 🔴 **The store must never gain a remote.** `devrc` is PUBLIC. The policy file governing a
  scope is whichever the probe names on its `policy:` line — do not go looking for another,
  and never create a scope README yourself.

- 🔴 **A trailing `echo` destroys the exit status exactly like a pipe.** `cmd > log 2>&1; echo
  "RC=$?"` makes the COMPOUND return the echo's 0 — the harness reported `ship.sh` as **exit code
  0** when the log said `SHIP_RC=7`. Reading the log content is what caught it. Same lesson as
  `| tail`, new shape.
- 🔴 **`cp -a` PRESERVES mtime; plain `cp`, `git clone` and `git checkout -- <path>` RESET it;
  `git add`/`git commit` preserve it.** Measured 2026-08-14. A docstring asserted the opposite and
  was shipped.
- 🔴 **`--session` refuses any transcript older than 30 minutes**, so the CLI structurally cannot
  measure historical sessions. Go under it: `collect_session_paths(repo, session=…,
  max_age_seconds=math.inf)` reads the path distribution without pretending to be that session.
- 🔴 **`\b1\.18\.4` never matches `v1.18.4`** — `v` and `1` are both word characters, so there is
  no boundary. A version-consistency guard passed green while a `v`-prefixed claim was stale.
- **A grep is an instrument: give it a positive control.** A case-wrong pattern (`raw` vs `RAW`)
  reported content missing from `origin/main` that was present; a typo'd test path made pytest say
  "no tests ran", which is an instrument error, not a zero.
- **Audit rounds, honestly:** across `#481` and `#485` every round but the last found a real defect
  in the PRECEDING fix — a feature that would have shipped INERT (both call sites deletable green),
  a guard pinned by a header string that stayed green while its arguments were corrupted, a
  suppression gate that was DEAD CODE, and a caveat that was simply false. **Five consecutive rounds
  contained a false claim inside the sentence doing the correcting.** Budget for it.
- 🔴 **The recurring error was one thing: stating a claim beyond what was measured.** Never caught
  by re-reading; always caught by running something. The `#494` measurement REFUTED the
  recommendation that motivated it — three agent reports of "0 paths under cwd" were one turn from
  becoming a structural claim in the store, and the real rate was **1 in 12**.

- 🔴 **`git fetch` is NOT a safe read in a shared checkout, and `FETCH_HEAD` is NOT a private
  scratch ref.** Measured: `fetch` writes `refs/remotes/<remote>/<branch>` in the COMMON
  gitdir (shared by every worktree) plus objects and reflogs, and two concurrent
  `git fetch --quiet origin main` produced `cannot lock ref` in **30 of 30** trials. Worse,
  reading `HEAD..FETCH_HEAD` in a SECOND process is racy — another session's fetch in between
  made a pushability check return a confident `0` on a checkout that was genuinely behind.
  **Use `git ls-remote` when you only need to know what the remote has**: zero local writes,
  12/12 concurrent runs clean.
- 🔴 **A ledger that RESTATES cannot catch what it was written for.** A test asserting "every
  status the module emits is documented in the skill" iterated a hand-written literal, so the
  status added by the very PR that needed it walked straight past. Deriving the list from the
  module (`re.findall(r'status=([a-z-]+)', src)`) caught a SECOND undocumented status
  immediately.
- 🔴 **Two guards reaching one outcome cannot be told apart by any test.** A `cat-file -e`
  check and `merge-base --is-ancestor` both refused on an unknown sha, so deleting either
  stayed green — the dead-predicate shape. Keep one, or pin the DIAGNOSTIC that differs
  (which is what made the second one worth keeping elsewhere: the message, not the verdict).
- 🔴 **A test can be satisfied through the wrong branch.** An ahead-and-behind fixture never
  reached the ancestry comparison because its repo had never fetched the remote commit, so
  the unknown-tip path decided and flattening `merge-base` to `False` stayed green. Ask which
  branch your fixture actually takes.
- **Reading a diff/grep is an instrument too.** A case-wrong pattern (`raw` vs `RAW`) reported
  content missing from `origin/main` that was present; a typo'd test path made pytest say "no
  tests ran"; a `-k` selector that matched the wrong 4 tests made a mutant look survived.
  Give every zero a positive control.

## How to verify

```bash
D=/home/zach/workspace/devrc
# read half — digest, index, search, pagination (READ-ONLY, no network, no subprocess)
python3 $D/scripts/lib/subsystem_recall.py --repo $D
python3 $D/scripts/lib/subsystem_recall.py --scope datapacket-talos --list
python3 $D/scripts/lib/subsystem_recall.py --scope datapacket-talos --search "nginx ratelimit"
python3 $D/scripts/lib/subsystem_recall.py --scope datapacket-talos --search "zzz kryptonite"  # must print NO MATCH + closest candidate

# write windows — run separately, NEVER merge the path sets
python3 $D/scripts/lib/subsystem_touch.py --repo $D --session <scratchpad-uuid>
python3 $D/scripts/lib/subsystem_touch.py --repo $D --pr <n>[,...]
python3 $D/scripts/lib/subsystem_touch.py --repo $D --commit <sha>[,...]

# after ANY entry write, in the SAME turn
python3 $D/scripts/lib/subsystem_touch.py --validate <path-just-written>

python3 $D/scripts/lib/subsystem_touch.py --census      # anchor was 21 / 1 scope / 21 unstamped
# 🔴 read the ACTIVITY lines, not the total: every count is a CREATION event, so a week
# of pure appends moves none of them and a worked store reads identical to a dead one.

# every scope versioned, contained, NO remote
for s in $(ls -d ~/.claude/analyze-service-index/*/ | xargs -n1 basename); do
  T=~/.claude/analyze-service-index/$s
  echo "$s: $(git -C $T rev-list --count HEAD) commits, $(git -C $T remote -v | wc -l) remotes"
done

# gate — read the CONTENT, never a piped exit code
nix build .#checks.x86_64-linux.pytests --no-link --print-build-logs > /tmp/gate.log 2>&1; echo "rc=$?"
grep -aE 'TOTAL +collected|RESULT: (PASS|FAIL)' /tmp/gate.log

scripts/drift-check.sh      # read-only deploy + host-parity deadman
```
