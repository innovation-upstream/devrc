# Handoff: subsystem-store — 2026-08-16 (phase 1 shipped)

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
A durable store for subsystem data + history with clean Claude Code integration, **reachable
from anywhere**. The local half is built, deployed and verified at the consumer on both hosts:
the read half is cheap enough to run every `/resume`, searchable, and survives a malformed
entry. The server half is at **phase 1 of 4** — see "State now".

⚠ The previous revision ended *"what remains is measurement, not a build"*. That stopped being
true on 2026-08-16: phases 1.5–4 are builds, and 1.5 is the one that faces the internet.

## State now — 🔴 THE STORE NOW HAS A SERVER. Phase 1 shipped 2026-08-16.

- **devrc `main` at `6bc6518`; homelab-infra `trunk` at `4f6ced02`.** Both base clones re-synced,
  both hosts at the same devrc commit. Only untracked leftover on the workbench is one
  `nix/system/*.LOCAL-preserved` file.
- 🔴 **A pod serves the store on homelab, ns `subsystem-store`, cluster-internal.**
  ClusterIP on **8102** (not the proposal's original 8110 — taken). Flux-managed since #326
  (`kustomize.toolkit.fluxcd.io/name: subsystem-store`). **No ingress, no public exposure,
  no DNS, no Authelia rule** — that is phase 1.5 and it has NOT been done.
- **The design doc is `claudedocs/proposal-subsystem-store-homelab.md`** and it is current:
  its header says what is built vs still proposed, and its port matches the deployment.

### Shipped this session (9 PRs)

| PR | repo | what |
|---|---|---|
| `#507` | devrc | two stranded rule lessons rescued (mutation-isolation; Loki partial results) — shipped + verified at the consumer on both hosts |
| `#508` | devrc | `.envrc` + `.opencode/` were unignored in a PUBLIC repo |
| `#509` | devrc | the migration proposal |
| `#510` | devrc | the laptop's logitech pair — applied on that host 2026-08-13, never committed |
| `#512` | devrc | **phase 1: the HTTP API over the existing reader** + 1,282 lines of tests |
| `#516` | devrc | the proposal header still said "nothing built" after phase 1 shipped |
| `#326` | homelab-infra | **phase 1 manifests** — ns, Deployment, Service, PVC, SOPS secret |
| `#327` | homelab-infra | the checker's rc 1 meant two things — a crash could spell a verdict |

`#503`/`#504`/`#505` (previous session) remain shipped + verified at the consumer on both hosts.

### 🔴 What phase 1 is NOT
Read this before trusting anything above as coverage:
- **Nothing has been tested off-mesh.** Phase 1 creates no route, so the control that matters
  for exposure — a request from outside your network — has never run.
- **The `(B-required)` hardening is not built**: no rate-limit, no lockout, no split
  read/write tokens. **Token rotation has never been exercised once**, which the proposal
  names as a pre-cutover requirement.
- **88 of the 89 API tests are invariant guards, not regressions** — `server.py` did not
  exist at base, so "red at base" is a collection error and proves nothing. The evidence
  that they bite is the 22-mutant sweep, not the red-at-base matrix.
- **`seed.sh --push` has no hermetic test** (needs a cluster); it was exercised live 3×.
- **`--mode=full` / `--page` over HTTP** were never byte-compared against the pod; only the
  default digest was.

### `#505` in one paragraph
An entry proposed a one-line remedy at **15:00:18** on 2026-07-24; the remedy landed at **15:02:21** — 2m03s later — and the entry served it as outstanding for **22 days**. `/handoff` step 4 runs MID-session, so the writer is gone by the time the work finishes. Openness is now a typed prefix, not prose, because a prose detector is walkable by rewording. Six populations, one precedence source (`JournalBullet.openness_population`); `--validate` reports four of them and its VERDICT is deliberately unchanged (an entry with unfinished business is well-formed; failing it would be a permanently-red gate). Index rows gain `🔴 N OPEN`, conditional so the common case is byte-identical.

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

### 🔴 A leaked credential in the store has been OPEN for 33 days and nothing was tracking it
- **Symptom:** the new `--validate` advisory surfaced the store's ONE pre-existing hand-written `OPEN:` bullet. It is a credential rotation + SOPS re-encrypt, dated **2026-07-14**, in the `datapacket-talos` scope. 🔴 **Deliberately not named here — this repo is PUBLIC and the entry is client-confidential.** Read it with `subsystem_recall.py --scope datapacket-talos --search "rotate"`.
- **Observed:** `subsystem_touch.py --scope datapacket-talos --validate` → `28 of 28 parse, 0 malformed`, then `🔴 1 declared OPEN:` naming the file, plus `⚠ 2 unmarked` (the forgejo remedy and an unpinned image tag).
- **Ruled out:** that the marker convention was invented by `#505` — a past session had ALREADY hand-written this bullet as `- OPEN: …` with no tooling asking for it. The schema formalises a shape the corpus invented on its own.
- **Next probe (verbatim):** rotate the credential, re-encrypt the SOPS file, then rewrite that bullet as `- RESOLVED <sha>: …` in the SAME edit so the badge clears. **Operator's call whether an agent touches the credential.**

### The doc hook — still n=0 uncontaminated, and THIS session cannot be the sample
- **Observed:** this session ran `subsystem_recall.py` at `/resume` step 4, i.e. because the SKILL told it to. `Skill` calls were non-zero (the `resume` skill loaded), so attribution to the doc is structurally impossible here.
- **Unchanged:** parse the next few ORGANIC continuations; do not stage a probe.

### `#329` is the on-switch and is deliberately unmerged
- **State:** OPEN, base `trunk`, exactly 2 files (IngressRoute + kustomization line) after I rebased it
  off the squashed parent. Merging it **is** deploying, and it is what makes a client-confidential
  store internet-reachable.
- **Blocking, and unmeasured:** the production node's host firewall on `:8102`. Both nebula gateways
  forward `CF-Connecting-IP` **verbatim and deliberately** (`clusters/{homelab,production}/apps/nebula/
  gateway/gateway-nginx-config.yaml` — "MUST SURVIVE THIS HOP … deliberately NOT re-set here"), so
  anything reaching a gateway's `:8102` while bypassing Cloudflare arrives as the trusted peer
  `10.244.0.123` and its header **is read**. Production's is `listen 0.0.0.0:8102` on a hostNetwork
  DaemonSet.
- **Ruled out:** that homelab Traefik would be denied by the NetworkPolicy. It is never in the path —
  the IngressRoute lives in the **production** cluster and targets Endpoints `10.0.0.2:8102`, so
  traffic arrives via the homelab nebula gateway (hostNetwork, same node, allowed).
- **Next probe (verbatim):** audit the production node's firewall for `:8102` reachability from off-mesh,
  then run the off-mesh probe from a production-cluster pod asserting `store.zacx.dev` resolves to a
  **Cloudflare** address (a hairpin or cluster DNS would otherwise hand you a confident false pass).

### Keep-alive audit-log misattribution — real, reported, NOT fixed
- **Symptom:** on a keep-alive connection whose **second** request line is malformed, `self.headers`
  and `self.path` still hold the **first** request's values, so the audit line reads:
  `ip=203.0.113.7 peer=trusted … path=/api/v1/recall/sc auth=fail result=401` — the *previous*
  request's path and `CF-Connecting-IP`.
- **Observed:** measured on one connection during `#520`'s mutation sweep, while explaining why a
  `send_error`-reset mutant was equivalent.
- **Not a bypass:** the request is still refused. It is log **fidelity** — on the log this design calls
  "the only thing that can answer it" if a leak is ever suspected.
- **Why unfixed:** predates the branch; closing it means changing `_raw_path`'s base behaviour.
- **Next probe:** decide whether it earns its own PR. Recorded in the source and in `#520`'s body.

### `--exclude` over-reports the under-cwd counter
- **Symptom:** `subsystem_touch.py` exclusion filters `paths` but **not** the `under_cwd` counter, so the
  printed note over-reports after an exclusion (measured: counter stays 3 while `paths` drops to 2).
- **Next probe:** one-line fix plus a test that pins counter and path-set together.

## Next steps (ranked)

1. **QUEUED — a skill to audit recent index ENTRIES for fidelity.** Read recent entries *and their
   source raw sessions*, then judge whether the entry faithfully captured what the session learned.
   Nothing does this today: `--validate` checks **well-formedness** (parses, `OPEN:` grammar), never
   **fidelity**. The manual prototype exists — the 2026-08-17 audit of session `e317505f` — and it
   found real gaps, so the skill is a systematisation of a proven procedure, not a new idea.
   **Carry forward:** parse `tool_use` structurally (never grep a transcript — a grep once reported
   `recall=3 touch=60` against a true count of **0**); strip heredoc bodies; exact basename match;
   reject `-c`/`-m`. Establish the entry's **before** state via `git -C ~/.claude/analyze-service-index/
   <scope> log -- <entry>.md` or you credit the session for pre-existing bullets. Transcripts are
   ~2 MB — parse, never `Read` raw.
2. **QUEUED — what is stored vs what is not: does a generic JOURNAL belong?** The `e317505f` audit found
   durable items that landed **nowhere** because they fit no surface: two checker-design lessons (a
   threshold derived from the data it guards goes silent under the very failure it guards; a
   discriminator is only trustworthy when its cut sits in an empty gap) lived only in a PR body, and two
   in-session retractions lived only in assistant prose. The store is **subsystem-scoped**
   (`<scope>/<service>.md`), so a lesson about *how to build a checker* maps to no subsystem; RULES.md is
   byte-capped and gated; MEMORY.md is byte-capped; skills are domain ops. **The gap is real: cross-cutting
   engineering lessons that are not yet rules and are not subsystem facts.**
   🔴 **Design constraint learned the hard way today: a fourth surface that nothing routes to is invisible,
   which is exactly the defect `#1081` just fixed.** Any journal must be named in the always-on docs and
   reachable by `--search`, or it will repeat the 63%-never-opened outcome.
3. **Decide `#329`** — after the host-firewall audit above. Merging is deploying and is irreversible in
   perception even if reversible in fact.
4. **Build and push image `0.2.0`**, then verify the trusted-proxy env is live at the consumer and
   exercise token rotation end-to-end against the pod.
5. **The credential rotation** — `github-pat-civitai`, still the only declared-`OPEN:` item in the store,
   now **34 days**. Untouched **by operator decision**, not because it was checked and found fine.

## Gotchas / decisions / dead-ends

### New 2026-08-16 (phase 1)
- 🔴 **A CRASH can spell a VERDICT when an exit code means two things.** Python exits 1 on any
  uncaught exception; the phase-1 checker used rc 1 for "an exposure object is present". Run
  without PyYAML, `ModuleNotFoundError` exited 1 and the negative control printed
  `✓ Middleware → rc=1` **while examining nothing**. Fixed by `#327` (rc 3 = the checker could
  not run). Widest reading: **any process whose failure code collides with one of its verdict
  codes**. Found only because a run happened to lack a dependency — so ask what your rc=1
  *else* means.
- 🔴 **`gh pr view --json mergeable` is NOT the check status, and `CLEAN` before checks
  register is byte-identical to `CLEAN` after they pass.** An agent polled inside that window
  and reported a PR clean whose gitleaks leg then failed. **Read `gh pr checks` too**, and
  quote it. Corollary: `CLEAN` on a repo with **no** CI configured (devrc has no
  `.github/workflows`) is a much weaker claim than `CLEAN` on one that ran a pipeline — say
  which you mean.
- 🔴 **A scanner run with the wrong flags is an instrument error wearing a zero's clothes.**
  `gitleaks detect` without `--no-git --config --baseline-path` scans git *history*, so it
  structurally cannot see a finding in a working-tree file, and returned a confident
  "0 findings in my new files". **Copy the pipeline's flags verbatim**, then positive-control
  it: reintroduce the offending string and watch the count move.
- 🔴 **A fix's own explanatory comment can reintroduce the hazard it documents.** The first
  draft of the gitleaks fix *quoted the offending URL to explain it*, turning 1 finding into
  2. Caught only by re-scanning; reasoning said "a comment is inert."
- 🔴 **Widening an allowlist to clear a red gate is the failure mode, not the fix** —
  `.gitleaks.toml` says so in its own header. The fix was to the fixture. Then prove the
  fixture still fails for its ORIGINAL reason (mutation: drop `Middleware` from
  `FORBIDDEN_KINDS`, watch it go red), or you have traded a red gate for a vacuous one.
- **`kubectl diff -k` shows a SOPS secret as differing even when it matches** — the manifest
  holds ciphertext, the cluster holds plaintext. Decrypt with the age key and compare hashes
  before concluding Flux would swap it. `sops -d --extract` needs `SOPS_AGE_KEY_FILE`, and
  `.secrets/age.key` is gitignored so it does **not** come with a worktree — point it at the
  base clone's copy.
- ⚠ **`yq -r` on a missing path returns the literal string `null`, not empty** — an
  `[ -z "$x" ]` guard does not catch it, and the resulting sha256
  (`74234e98afe7498f…`) reads as a genuine mismatch. Check for `"null"` explicitly.
- **Adopting hand-applied objects into Flux was a no-op here, and that was verified, not
  assumed**: same pod, same `startTime`, `restarts=0`; Deployment generation moved 4→5 from
  Flux adding its own labels, which is metadata and does not roll the pod template.


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

- 🔴 **A `grep` you wrote the pattern for is not a leak check.** `grep -iE 'oauth2-proxy|CSRF|…'` over the diff returned nothing and was reported clean. It was not: what survived was a service name WITHOUT the prefix the pattern required, plus two fragments of ordinary English no hand-written pattern would contain. The zero was a fact about the pattern. Replaced by `scripts/tests/test_store_content_not_copied.py`, which DERIVES 8-word phrases from the store — and immediately found **two more copies** that neither the grep nor a full adversarial audit had caught.
- 🔴 **A denylist beats an allowlist for "which files can hold prose".** That check first scanned 632 of 839 tracked files (an extension allowlist missing every `.mjs`/`.js`/`.html` and 48 extensionless scripts) while documented as covering "any tracked file".
- 🔴 **`{7,40}` inside a `*` regex loop is a ReDoS.** Introduced mid-review; measured on a SENTENCE-CASED bullet quoting long shas with no trailing colon: 64 hex 0.028 s, three 40-char shas **no return in 30 s**, hanging `/handoff` and `--validate` with no output. Fix is a non-splittable atom, `(?![0-9a-fA-F])`. The all-caps form is unaffected — which is why its regression test must be sentence-cased.
- 🔴 **A timing guard must be shown to REACH the code it times.** That ReDoS test was vacuous twice: first the payload was all-caps and short-circuited the branch; then it called `parse_journal_bullets` without reading `near_miss_marker`, which is a LAZY property. Both passed with the fix deleted.
- 🔴 **An evaluation matrix in a scratchpad is an opinion.** Three rounds changed one regex, each justified by a private matrix; a differently-constructed one inverted the verdict. Now `scripts/tests/fixtures/near_miss_shapes.json`, parametrized, with a `matrix_problems()` guard that is itself tested against degenerate matrices.
- 🔴 **A fixture can be blind to the class its own change introduces.** The `prose` arm shipped with the all-caps branch contained ZERO all-caps shapes, so 39 green assertions said nothing about `OPENSSL_CONF` / `OPEN_MAX` firing a red advisory.
- 🔴 **A mutation result with no BASELINE is a fact about the harness.** One probe reported KILLED for both variants because it ran bare `python3` with no pytest installed.
- **Two claims of mine measured false and were corrected in place:** "`re.I` fires on ordinary prose" (0 FPs over 196 live bullets — the examples were constructed), and a phrase-distribution row that reproduced under no tree-and-filter combination.
- **Splice a comment with anchored `Edit`, not index arithmetic** — one such splice deleted a whole regex definition and turned 129 tests red.
- **A new test file must be `git add`ed or the flake silently omits it** — the gate passed green without ever running it; caught only because `skipped=` did not move.

### The structural finding (2026-08-17) — and the two technicalities that were NOT the cause
- 🔴 **The store was reachable through exactly ONE door.** Measured over the 40 most recent
  `datapacket-talos` sessions: `/handoff` ran in **12/40**; an index window ran **11** times when
  `/handoff` ran and **2** when it did not. **20 of the 32 sessions that landed a commit or PR (63%)
  never opened the store.** Cause: that repo's 92 KB always-on `CLAUDE.md` named two homes for durable
  knowledge and contained **zero** occurrences of `analyze-service-index`/`subsystem_touch`/
  `subsystem_recall`. Fixed by `#1081`.
- 🔴 **The subtler half: a window that runs can answer about the WRONG REPO.** Scope follows `--repo`,
  and that project dir is a dispatch hub — **7 of 13** window-running sessions left at least one PR's
  repo unscoped. Fixed by `#527`.
- ⚠ **Both technicalities investigated first were BENIGN, proven with controls** — do not re-derive them.
  The 17-vs-7 path gap was 7 writes + 10 **reads** (inputs). `--exclude claudedocs` was a **complete
  no-op** here, with a positive control showing the flag *is* live. The real cause was one level up and
  invisible to the tooling itself.
- **`--session` nominated an entry in 0 of 12 runs** across those 40 sessions. Not a bug to fix in the
  window: worktree-isolated subagents are the standing default and are two of its three blind spots.
  `#522` makes it say so with the run's own numbers.

### Verification traps hit THIS session (each produced a confident wrong answer)
- 🔴 **`nix build` returned a CACHED result — a 0-byte log with exit 0.** Hit twice. That is not a pass;
  read the real output with `nix log <drv>`, and a valid store output is itself evidence since the
  derivation fails on test failure.
- 🔴 **A trailing `echo` destroyed a push's exit status** — my `git push … | tail; echo "pushed"` printed
  `pushed` while the push had **failed**. Reading the content is what caught it.
- 🔴 **`yq -r` returns the literal string `null` on a missing path**, which `[ -z ]` does not catch; the
  resulting `sha256("null")` = `74234e98afe7498f…` read as a genuine token **MISMATCH**. It was an
  instrument error.
- 🔴 **A suite run without its dependency looked like a code failure** — `ModuleNotFoundError: yaml` gave
  `pass=4 fail=11`, and one assertion (`rc=1`) passed *vacuously* because a Python crash also exits 1.
  That accident is what exposed homelab `#327`.
- 🔴 **A pre-push gate refused with `gate 9 could not RUN … pyyaml missing`** — the documented exit-3
  environment case, **not** a violation. Supplying the dependency is the fix; `--no-verify` would have
  bypassed that repo's PRIMARY gate (branch protection is unavailable there).
- **`kubectl diff -k` shows a SOPS secret as differing even when it matches** (manifest holds ciphertext,
  cluster holds plaintext). Decrypt and compare hashes; `.secrets/age.key` is gitignored and does **not**
  come with a worktree — use the base clone's copy.
- 🔴 **A squash merge makes a stacked child's diff show its parent's files again.** After merging a
  stacked parent, `#329` listed **9** files whose content was byte-identical to `trunk`. Rebasing onto
  `trunk` reduced it to the **2** it actually changes. Merge a stacked parent **without**
  `--delete-branch`, or GitHub auto-closes the child and refuses to reopen it.

### On the audits
- **Two blind audits and two blind delta re-audits ran.** Every round found something the previous round's
  fix had introduced — including a mutation sweep returning **24/24 green with two criticals in the file**,
  and a `-k` selector that made a live mutant look SURVIVED (it dies to 8 tests when the whole file runs).
- 🔴 **An auditor's reasoning can be wrong while its finding is right.** One claimed `homelab-infra` had no
  subsystem-store app; `homelab-talos` *is* the local clone of `ZacxDev/homelab-infra`. The substantive
  half — that the referenced NetworkPolicy was unmerged — was correct. **Acting on it as written would
  have broken a correct path.**
- 🔴 **A finding can be true and its framing exculpatory.** A re-audit called a red test "not
  delta-introduced". It was green on `trunk` and red on the branch — i.e. **that PR's regression**,
  merely present since its first commit.

## How to verify

```bash
D=/home/zach/workspace/devrc
# the store's read half (READ-ONLY, no network, no subprocess)
python3 $D/scripts/lib/subsystem_recall.py --repo $D
python3 $D/scripts/lib/subsystem_recall.py --ref subsystem-store-api

# the LIVE service — cluster-internal only; there is no public route
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get pod,networkpolicy
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store port-forward svc/subsystem-store-api 18102:8102 &
TOK=$(KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get secret subsystem-store-token -o jsonpath='{.data.token}' | base64 -d)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOK" http://127.0.0.1:18102/api/v1/recall/devrc   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18102/api/v1/recall/devrc                                   # 401
getent hosts store.zacx.dev || echo "not public — correct"

# the gate — read CONTENT, never a piped exit code; a cached drv prints a 0-byte log
nix build .#checks.x86_64-linux.pytests --no-link --print-build-logs > /tmp/gate.log 2>&1
grep -aE 'TOTAL +collected|RESULT: (PASS|FAIL)' /tmp/gate.log     # last: 11566 collected, failed=0
```
## State now — phase 1 AND 1.5-hardening shipped; the store is a live service, still PRIVATE

- **devrc `main` at `edd0ba3`; homelab-infra `trunk` at `9f89db49`; civitai/talos-infra `trunk` carries `126f412b6`.**
- 🔴 **Live:** pod `subsystem-store-api-7fbb89cb5b-5f7m7` Running on `talos-jkj-deb`, ns `subsystem-store`,
  ClusterIP **8102**, Flux-managed, plus `subsystem-store-default-deny-ingress`. **No ingress, no DNS —
  `store.zacx.dev` does not resolve.** Verified 200 authed / 401 unauthed.
- 🔴 **The deployment is PRIMED WITH AN INERT CONFIG.** It rolled once at **2026-08-17T23:26:46Z**
  (generation 6) picking up `SUBSYSTEM_STORE_TRUSTED_PROXIES=10.244.0.123/32`. The image is still
  `0.1.0`, which **ignores** that variable — the code that reads it is `#520`, merged to devrc `main`
  with **no image built**. The moment a new image ships that value becomes load-bearing, and a wrong
  one is `EXIT_CONFIG` (78) → CrashLoop on `Recreate`+`replicas:1`.
  🔴 **The next image MUST be `0.2.0`** — re-pushing `0.1.0` is exactly the mutable-tag clobber
  `deployment.yaml:56-57` forbids, and it is an easy mistake *because* this file's tag did not move.

### Shipped (16 PRs, 3 repos — the rest in those repos' merge lists belong to other sessions)
| theme | PRs |
|---|---|
| stranded work rescued | devrc `#507` `#508` `#509` `#510` |
| the store as a service | devrc `#512` `#516` `#517` `#518` `#520` · homelab `#326` `#327` `#328` `#330` |
| the structural routing fix | devrc `#522` `#527` · **talos-infra `#1081`** |

Plus a store entry written by hand: **`devrc/subsystem-store-api.md`** — validated, committed, 0 remotes.

### 🔴 What is NOT done
- **Nothing has been tested off-mesh.** No route exists; the control that matters for exposure has never run.
- **Token rotation has never been exercised** against the live pod (proven hermetically only).
- **No image built or pushed.** `0.2.0` is owed as its own step.
- **The production node's host firewall on `:8102` is UNAUDITED** — it decides whether the
  CF-bypass residual is mesh-only or internet-reachable. **Prerequisite for `#329`.**
