# Handoff: subsystem-store — 2026-08-12

## Goal
A durable store for subsystem data + history with clean Claude Code integration.
**Built, deployed, and now measurably working.** What remains is a short list of stale
claims and one open measurement.

## State now

- **Branch:** `main`, clean tracked tree (1 behind origin at time of writing).
- **`main` is green.** An earlier revision of this doc called it RED; that was wrong — see the resolved note below.
- **DONE this session** (squash shas on `main`):
  - `#361` per-scope git store + contained hourly autocommit · `#362` schema repairs · `#375` decision record · `#378` resolver · `#398` `changed_paths` + the opencode extractor fix · `#408` failure propagation in the claude tailer
  - `#415` **the `/handoff` writer** · `#421` `--session` · `#424` `--pr` · `#432` `--commit`
  - `#426` **the read half** (`subsystem_recall.py` + `/resume` wiring) · `#429` show existing bullets before an append, + the new-scope versioning clause
  - Closed as superseded: `#364` (by #366), `#410` (by #407)
- **IN FLIGHT:** `#418` — an older revision of this doc, still OPEN. This supersedes it; push to the same branch (`docs/handoff-subsystem-store`), do not open a second PR.
- **Deploy:** both hosts converged and switched throughout. The `#432` skill half needs a `ship.sh` to reach either host (`scripts/` is read from the repo and is already current).

### The measurement that was the whole point
Falsifiability anchor, recorded before any writer existed: **21 entries · 1 scope · 21 unstamped.**

```
27 entries
  by scope:      civitai 1 · civitai-app-starters 1 · datapacket-talos 24 · devrc 1
  by created_by: handoff 6 · unstamped 21
```

**Six entries created by the `/handoff` writer across four scopes**, three of which did not
exist before. Entries are accruing outside infra recon, which is exactly what the rejected
design's evidence said would never happen — because the only writer at the time was an
infra-recon command. That reasoning was circular and this is the refutation.

## Open investigations — live diagnosis state

### RESOLVED — the `st_blocks` failure was a flake, and my diagnosis of it was wrong
Kept because the *mistake* is the durable part, not the bug.

- **What I saw:** `test_st_blocks_CANNOT_see_a_fallocated_partial` failing `assert 16896 == 16904` on two consecutive sandbox runs of pristine `main`, while the same suite passed `991/991` run directly on the host.
- **What I concluded, wrongly:** a structural sandbox-vs-host split — "green where it is observed, red where it gates" — and I wrote `main is RED` into this doc's state section.
- **What it actually was:** a **flake**. Another session measured 1 red in 5 runs, with the assertion's **operands reversed between reports** — the tell I never looked for. `#435` (`73e15f8`) landed the real fix: *"st_blocks equality pinned allocator luck, not the code under test."* The defect was real; the diagnosis was not.
- **The lesson:** two runs plus one contrasting environment is not enough to call a structural split. `claude/RULES.md` says distinguish a flake by wall time and by *whose* time moved, **not by one re-run** — I effectively did two, found a story that fit, and stopped. A cheap third and fourth run would have refuted it.

### The reopening gate in the decision record asks the wrong question
- **Symptom:** `claudedocs/decision-subsystem-store-rejected-2026-08-11.md` says revisit at "≥5 entries outside the current single scope, or ≥5 non-infra entries".
- **Observed:** both conditions are now **met** — 3 entries outside the original scope, 6 non-infra by writer. And the gate was the wrong question anyway: the binding constraint measured was **coverage** (of 290 path-carrying sessions, 12 were in the one indexed scope; 7 of those resolved — a **58% hit rate inside covered scope**).
- **Leading hypothesis:** it should read "does the index cover the repos where work happens" — was 1 of ~12, now 4 of ~12.
- **Next probe:** none. One-paragraph edit.

### 155 of 290 path-carrying sessions have `cwd` basename literally `empty`
- **Observed:** `cwd = 'empty'` false for all (not the literal string); bucket is `depth=4`, i.e. `/a/b/empty`; `countIf(repo='')` is **0** for both sources.
- **Ruled out:** `cwd` absent/empty. Reading `cwd` from the *payload* — my error; opencode's payload has no `cwd`, it is a top-level **column**.
- **Next probe:** `SELECT DISTINCT cwd FROM activity.events WHERE kind='session-summary' AND splitByChar('/', trimBoth(cwd))[-1]='empty' LIMIT 5`, then inspect that directory on disk.

### opencode can emit a stringified list as a repo-relative path
- **Observed:** `str(["a.py"])` is `"['a.py']"`, which `to_repo_relative` accepts and emits. Searched the emitted corpus: **zero occurrences** — the path exists, has never fired.
- **Ruled out:** that `#408`'s claude-side fix covers it. It does not; `#408` validates-and-skips rather than coercing, precisely because coercion manufactures data.
- **Next probe:** decide validate-and-skip on the opencode side, or a documented known-limitation.

## Next steps (ranked)

1. **Correct the reopening gate** in the decision record — both its conditions are now met and it is still the wrong question.
2. **Merge `#418`** after pushing this revision to its branch.
3. **Watch the census.** `python3 scripts/lib/subsystem_touch.py --census`. It has moved 21→27 across 4 scopes with 6 handoff-written entries. If that stalls, the writer is not sticking.
4. Investigate the `empty` cwd bucket; decide the opencode stringified-path question.
5. **The floors line in `scripts/run-tests.sh` has conflicted on ~9 consecutive PRs.** Per-target floors (`#397`) removed the base-dependent global total but made every test-adding PR touch the table. Worth measuring separately.
6. **Worktree debt** — 20+ agent worktrees under `.claude/worktrees/`.

## Gotchas / decisions / dead-ends

- 🔴 **A large subsystem store was proposed and REJECTED on measurement** (no `type:` taxonomy, no dependency graph, no multi-verb CRUD). Read `decision-subsystem-store-rejected-2026-08-11.md` before re-proposing. **But its "no demand" half was CIRCULAR** — the only writer was infra-scoped, so only infra entries could exist. The census above is the refutation.
- **Four path sources, blind in different directions — never merge their outputs.** git window: misses work merged during the session. `--session`: misses **subagent** work (`isSidechain` excluded, 196 of 733 file-tool calls) and `Bash`-written files, and is blind in worktree-mandated repos (25 paths outside cwd, 0 inside). `--pr`: sees subagents, but is the **branch union**, and misses direct pushes — **144 of 200** recent `datapacket-talos` trunk commits carry no `(#N)`. `--commit`: the primitive the others reduce to. The flags are argparse-exclusive on purpose.
- **`Edit` vs `Write` on an entry, MEASURED:** `Write` silently loses a concurrent append; `Edit` is **bounded**, so the other bullet survives. 🔴 It does **not** reliably fail loudly — an earlier version of this rationale said it did and was wrong. The real safeguard is **re-read and re-apply to current bytes**; never treat "no error" as evidence you were alone.
- **A brand-new scope is `git init`ed, identity-seeded and committed by the store's own hourly timer** — measured twice. Do not create the repo yourself; an entry there is unversioned for up to an hour, which is the normal window.
- **`gh pr view --json files` silently truncates at 100** while `changedFiles` reports the truth. Guarded cap-agnostically. My own verification missed it by checking a 4-file PR.
- **`git diff-tree` exits 0 and prints nothing** for a merge, a root commit, a blob and a tree — git's own silent zeros, each closed before the diff runs.
- 🔴 **The store must never gain a remote**; no line of it may reach a public repo. `devrc` is PUBLIC. Each scope's `README.md` is authoritative.
- **Instrument validation kept being the actual finding.** Six mutation harnesses this session produced wrong verdicts before correction — a stale `.pyc`; a sweep whose `git checkout --` reverted the change under test; a parser regex-matching a traceback body; one environment-dependent on the pytest launch directory; one that overwrote the repo's `conftest.py`; one whose anchor matched twice, silently retiring its own guard.
- **Floor deltas must be attributed to the branch, not the target's movement.** Three near-misses: `+118` measured `+68`; `+68` was really `+64`; `+124` was really `+109`.
- **`FETCH_HEAD` is repo-global** and clobbered by concurrent sessions — it produced one false verification here.
- **`git diff --stat <branch>..origin/main` renders the branch's own edits with inverted sign.** Use `git log <base>..origin/main -- <path>`.
- **zsh eats `:s`/`:h`/`:p` after an unbraced `$VAR` in a git ref.** Brace it.
- **`git -C $VAR` defeats the bash-guard's repo detection** — it judges the caller's directory instead. Pass an absolute path.

## How to verify

```bash
D=/home/zach/workspace/devrc
# all four windows (read-only; the tool never writes)
python3 $D/scripts/lib/subsystem_touch.py --repo $D --session <scratchpad-uuid>
python3 $D/scripts/lib/subsystem_touch.py --repo $D --pr <n>[,...]
python3 $D/scripts/lib/subsystem_touch.py --repo $D --commit <sha>[,...]
# the read half
python3 $D/scripts/lib/subsystem_recall.py --repo $D
# the falsifiability counter — anchor was 21 / 1 scope / 21 unstamped
python3 $D/scripts/lib/subsystem_touch.py --census

# every scope versioned, contained, NO remote
for s in $(ls -d ~/.claude/analyze-service-index/*/ | xargs -n1 basename); do
  T=~/.claude/analyze-service-index/$s
  echo "$s: $(git -C $T rev-list --count HEAD) commits, $(git -C $T remote -v | wc -l) remotes"
done

# the authoritative gate
nix build .#checks.x86_64-linux.pytests --no-link --print-build-logs 2>&1 | grep -E 'TOTAL collected|RESULT|FAIL'
```
