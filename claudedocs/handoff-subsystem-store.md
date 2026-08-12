# Handoff: subsystem-store — 2026-08-12

## Goal
A durable store for subsystem data + history with clean Claude Code integration.
The store, its schema, its versioning and now a second writer all exist and are
deployed. What is unproven is whether entries actually **accrue** outside infra
recon — that is a measurement, deliberately left to run.

## State now

- **Branch/PR:** `main`, clean tracked tree. Everything below is merged and deployed to both hosts.
- **DONE this session** (squash shas on `main`):
  - `#361` — the recon index is a **per-scope git repo**, hourly autocommit, no remote, under `ProtectSystem=strict` / `ProtectHome=tmpfs` / `PrivateTmp` / `PrivateNetwork` / `InaccessiblePaths=/dev/shm`.
  - `#362` — schema repairs: optional kind-qualified filenames, **ambiguity errors rather than shadows**, `sensitivity:` with a fail-safe default, `repo:`→`scope:` with non-repo scopes, and the **liveness convention** (persist the derivation method, never the reading).
  - `#375` — the decision record for the rejected design.
  - `#378` — `scripts/lib/subsystem_resolver.py`, the path→subsystem resolver.
  - `#398` — both summarisers emit `changed_paths`; opencode's extractor fixed (it read tool inputs from the part's top level; they live under `state.input`, camelCase).
  - `#408` — six fatal shapes guarded in the claude tailer, and `run()` now **propagates failure** (it returned 0 unconditionally, silently disabling the unit's only alerting channel).
  - `#415` (`3fd97fd`) — **the second writer**: `scripts/lib/subsystem_touch.py` + `/handoff` step 4.
  - Closed as superseded: `#364` (by #366), `#410` (by #407).
- **Deploy status:** both hosts converged and switched. Verified live, not assumed —
  the deployed handoff skill carries all three of its new clauses, the autocommit
  timer fired 10 min before this doc was written, and the claude tailer fired 2 min before.
- **Backfill:** 585 historical opencode sessions re-emitted (362 files, 94 commits,
  37,311 lines that previously read as zero). Message streams verified unchanged.
- **Store right now:** 21 entries, **1 scope**, 3 commits, clean, **0 remotes**.
- **Census baseline (the falsifiability anchor):** `21 entries · 1 scope · 21 unstamped`.

## Open investigations — live diagnosis state

### 155 of 290 path-carrying sessions have `cwd` basename literally `empty`
- **Symptom + exact repro:** grouping session-summary rows by the last component of the `cwd` **column** returns `empty` for 155 of 290 sessions that carry ≥1 changed path.
- **Observed (with values):** `is_literal_empty=0` for all rows (so `cwd` is not the string `empty`); the bucket is `depth=4`, i.e. `/a/b/empty`. Basename query returns exactly `empty  4  155`. `countIf(repo='')` is **0** for both sources, so it is not an empty string.
- **Ruled out:** `cwd` being absent or empty (measured 0 for both sources). Reading `cwd` from the *payload* — that was **my error**: opencode's payload has no `cwd` field at all; it is a top-level **column**.
- **Leading hypothesis:** an opencode default/placeholder working directory. Unconfirmed.
- **Next probe:** `SELECT DISTINCT cwd FROM activity.events WHERE kind='session-summary' AND splitByChar('/', trimBoth(cwd))[-1]='empty' LIMIT 5` — then look at what that directory actually is on disk.
- **Why it matters:** those sessions can never resolve to a subsystem, so they are 53% of the corpus permanently excluded. It does not change any verdict already reached.

### The decision record's reopening gate asks the wrong question
- **Symptom:** `claudedocs/decision-subsystem-store-rejected-2026-08-11.md` says revisit at "≥5 entries outside the current single scope, or ≥5 non-infra entries".
- **Observed:** the P1 measurement showed the binding constraint is **coverage**: of 290 path-carrying sessions, **12** are in the one indexed scope, and 7 of those resolved — a **58% hit rate inside covered scope**. The resolver works; the index covers ~4% of the repos worked in.
- **Leading hypothesis:** the gate should read "does the index cover the repos where work happens" — currently 1 of ~12.
- **Next probe:** none needed; this is an edit to that document.

### opencode can emit a stringified list as a repo-relative path
- **Symptom:** `str(inp.get("filePath") or "")` — `str(["a.py"])` is `"['a.py']"`, which `to_repo_relative` **accepts** and emits. Manufactured data in the one module whose contract is that nothing is invented.
- **Observed:** searched the emitted corpus for that shape — **zero occurrences**. The path exists; it has never fired.
- **Ruled out:** that #408's claude-side fix covers it — it does not; #408 deliberately *validates and skips* on the claude side rather than coercing, precisely because coercion manufactures data.
- **Next probe:** decide whether to validate-and-skip on the opencode side to match, or leave a documented known-limitation.

## Next steps (ranked)

1. **Correct the reopening gate** in `decision-subsystem-store-rejected-2026-08-11.md` to the coverage question. One edit; it is the durable record and currently misleads.
2. **Let the writer run and watch the census.** `python3 scripts/lib/subsystem_touch.py --census`. Baseline is `21 / 1 scope / 21 unstamped`. If `created_by: handoff` entries do not appear across several repos within a few weeks, the need is narrower than it looks and the writer should be reconsidered — same discipline that killed the original design.
3. **Investigate the `empty` cwd bucket** (probe above).
4. **Decide the opencode stringified-path question** (validate-and-skip vs documented limitation).
5. **Worktree debt** — 17+ agent worktrees under `.claude/worktrees/`, several from this session's agents.

## Gotchas / decisions / dead-ends

- 🔴 **A large subsystem store was proposed and REJECTED on measurement** — no `type:`-driven sections, no dependency graph, no multi-verb CRUD. After eight weeks: 20 entries, **0 non-infra**, **1 scope**, **0 dependency edges**. A hand-authored `process` and `org` entry showed `type:` selected nothing. Do not rebuild it; read the decision record first.
- 🔴 **But "no demand" was partly circular reasoning, and that was my error.** The only writer was an infra-recon command pointed at two cluster repos, so of course only infra entries in one scope existed. "Nothing non-infra exists" is not evidence of no demand when nothing can create one. `#415` closes exactly that gap. The arguments that *do* survive are narrower: the `type:` taxonomy and graph were unjustified, and an opt-in multi-verb CRUD would not have stuck.
- **Adoption, measured across 17 commands:** six have never been invoked once. The lesson is **not** "opt-in fails" — it is that **opt-in survives when it rides a ritual already performed, and dies when it *is* the ritual**. `/analyze-service` stuck (29 invocations) because its index write is a free side-effect of recon. `#415` rides `/handoff` for the same reason.
- **Paths come from git, bounded to the branch, and the bound is printed on every output path.** Telemetry's `changed_paths` is the honest per-*session* source but does not exist yet when `/handoff` fires. The core takes paths as an argument so telemetry can feed it later.
- **`--show-toplevel` was rejected deliberately:** it would make **every agent worktree its own scope**, sharding the store into hundreds of unresolvable one-entry scopes. With dozens of concurrent worktrees here that is the normal case.
- **Working-tree residue, not branch age, is the dominant over-reporting source** — on a live run, 6 of 6 paths were other sessions' stale untracked files. `--exclude` is wired into the skill's command line; it is manageable, not solved.
- **A top-ranked `scripts` or `claudedocs` nomination is honest, not a bug** — it genuinely covers several paths in one subtree. The confirm gate is what rejects it.
- 🔴 **`~/.claude/analyze-service-index/` must never gain a remote** and no line of it may reach a public repo. `devrc` is PUBLIC. Each scope's `README.md` is authoritative over any command file.
- **Instrument validation kept being the actual finding.** Four separate mutation harnesses in this session produced wrong verdicts before being corrected — one reused a stale `.pyc`, one ran against uncommitted work whose `git checkout --` silently reverted the change under test, one mis-scored six kills by regex-matching a traceback body, one was environment-dependent on the pytest launch directory. A green sweep is a claim about the harness until proven otherwise.
- **`FETCH_HEAD` is repo-global** and is clobbered by concurrent sessions in this shared checkout — it produced one false verification here. Resolve to an explicit remote-tracking ref and assert the sha.
- **`git diff --stat <branch>..origin/main` renders the branch's own edits with inverted sign.** I misread that as "main collided with my files" and told an agent so; it was wrong. `git log <base>..origin/main -- <path>` is the right question.

## How to verify

```bash
# the writer, end to end (read-only; never writes)
python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo /home/zach/workspace/devrc

# the falsifiability counter
python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --census

# the store is versioned, contained, and has NO remote
S=~/.claude/analyze-service-index/datapacket-talos
git -C $S rev-list --count HEAD; git -C $S remote -v | wc -l   # commits; MUST be 0
systemctl --user list-timers 'analyze-service-index-commit*' --no-pager
systemctl --user show analyze-service-index-commit.service \
  -p ProtectSystem -p ProtectHome -p PrivateNetwork -p InaccessiblePaths --no-pager

# the authoritative gate (the local tier cannot reach the floor on this host)
nix build .#checks.x86_64-linux.pytests --no-link --print-build-logs 2>&1 | grep -E 'TOTAL collected|RESULT'
```
