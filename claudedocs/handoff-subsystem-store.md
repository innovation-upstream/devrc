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

- **Branch:** `main` at `d01cf23`, clean tracked tree (4 untracked: `.envrc`, `.opencode/`,
  `claudedocs/proposed-rules-cut/`, `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`).
- **Merged this session (10):** `#436` step-4 probe-first · `#418` prior revision of this doc ·
  `#440` the **digest** · `#441` reopening-gate correction · `#442` `--search` + 100-cap
  pagination · `#446` kickoff `/resume` prefix · `#448` decline-on-content-not-cost ·
  `#449` malformed entry no longer kills a scope · `#457` index read moved into the doc ·
  `#459` the `session-absolute` window.
- **Deploy:** `ship.sh rc=0` — both hosts converged + verified, **440 (workbench) / 402
  (laptop) managed artifacts, 0 dangling**. Verified at the consumer, not inferred.
- **Store:** 34 entries · 5 scopes · **all 5 now have a README** (was 1 of 5) · 0 remotes.
- **Worktrees:** 61 → 13. The 2026-08-13 sweep is DONE: 22 dropped, 1 kept (`#355`), 1
  salvaged (→ `#447`). Full 23-row evidence table is in git history at `d1cc0ba`; the 8
  detached heads are preserved as `preserved/*` tags **on origin**
  (`git ls-remote --tags origin 'preserved/*'`).

### Numbers that were the point
```
34 entries — civitai 2 · civitai-app-starters 1 · datapacket-talos 27 · devrc 2 · homelab-talos 2
by created_by: analyze-service 1 · handoff 12 · unstamped 21
```
Anchor before any second writer existed: **21 entries · 1 scope · 21 unstamped.**

**`/resume` step 4: 7,871 → ~1,212 tokens (84.6% less) AND strictly more complete** — lists
every entry instead of hiding 13 of 25.

## Open investigations — live diagnosis state

### Is the worktree-path bucket worth closing? — UNMEASURED, do not build on my numbers
- **Symptom:** a session whose edits all land in a throwaway worktree gets
  `status=looked-at-nothing` from `--session`. Seen **4×** today; most recent: 0 paths under
  cwd, **23 outside**, all worktree paths (civitai scope).
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

## Next steps (ranked)
1. **Get an ORGANIC doc-hook sample.** Two exposed continuations, two fired — including a
   **dispatched agent**, the exact case `#446` failed (details above). Both are contaminated,
   in different ways, and the remaining contamination is now structural: **this doc describes
   the experiment, and `Read` returns the whole file**, so any agent sent here can see it may
   be the subject. A staged probe cannot fix that. Just watch the next few real continuations
   and parse their transcripts; do not stage another one and count it as clean.
2. **Decide the worktree bucket** — measure the yield first (probe above). I was wrong about
   exactly this class of number today.
3. **Watch the census — the ACTIVITY lines, not the total.** `python3
   scripts/lib/subsystem_touch.py --census`. Coverage is 21 → 34 across 5 scopes, 12
   handoff-written + 1 analyze-service-written. 🔴 **The total cannot detect a stall and never
   could**: every count is a count of CREATION events, `created_by` is stamped once and never
   updated, so a week of pure appends moves nothing. Measured 2026-08-13: the store read 34
   across two readings 40 minutes apart while the git history showed **7 new entries and 9
   appends that same day** across 5 scopes. Read `newest write` and `touched in the last
   24h/7d` instead — mtime-derived, deliberately not the store's git log, because commits are
   batched by an hourly timer and a git reading lags real writes by up to 60 minutes.
   **The number actually worth watching is the `analyze-service` share**: 12 of 13 stamped
   entries are `handoff`, so the store is effectively single-writer and the second writer has
   an n=1 track record over the whole instrumented period.
4. Investigate the `empty` cwd bucket (155 of 290 path-carrying sessions, `depth=4`); decide
   the opencode stringified-path question (`str(["a.py"])` is accepted by `to_repo_relative`;
   zero occurrences in the emitted corpus — the path exists, has never fired).
5. **The floors line in `scripts/run-tests.sh`** has conflicted on ~9 consecutive PRs. Worth
   measuring separately.

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
