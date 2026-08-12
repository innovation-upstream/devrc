# Handoff: subsystem-store — 2026-08-12

## Goal
A durable store for subsystem data + history with clean Claude Code integration.
**Now fulfilled end to end**: the store exists, is versioned and contained, and a
second writer riding `/handoff` has written its first entry in a new scope. What
remains is a measurement — whether entries keep accruing — not a build.

## State now

- **Branch/PR:** `main`, clean tracked tree. All work below merged and deployed to both hosts.
- **DONE this session** (squash shas on `main`):
  - `#361` — index becomes a **per-scope git repo**, hourly autocommit, no remote, `ProtectSystem=strict` / `ProtectHome=tmpfs` / `PrivateTmp` / `PrivateNetwork` / `InaccessiblePaths=/dev/shm`.
  - `#362` — schema repairs: optional kind-qualified filenames, **ambiguity errors rather than shadows**, `sensitivity:` fail-safe default, `repo:`→`scope:`, the **liveness convention**.
  - `#375` — decision record for the rejected store design.
  - `#378` — `scripts/lib/subsystem_resolver.py`.
  - `#398` — both summarisers emit `changed_paths`; opencode's extractor fixed (read tool inputs top-level; they nest under `state.input`, camelCase).
  - `#408` — six fatal shapes guarded in the claude tailer; `run()` propagates failure (it returned 0 unconditionally, disabling the unit's only alerting channel).
  - `#415` (`3fd97fd`) — `scripts/lib/subsystem_touch.py` + `/handoff` step 4: **the second writer**.
  - `#421` (`e7c1007`) — `--session <uuid>` path source, read from the session's own transcript.
  - `#424` (`7994ba1`) — `--pr <n>[,…]` path source, **the only one that can see a subagent's work**.
  - Closed as superseded: `#364` (by #366), `#410` (by #407).
- **IN FLIGHT:** `#418` — the previous revision of this doc, still OPEN. This file supersedes it; push to the same branch (`docs/handoff-subsystem-store`) rather than opening a second PR.
- **Deploy:** both hosts converged and switched. Verified live, not assumed.
- **Backfill:** 585 historical opencode sessions re-emitted (362 files, 94 commits, 37,311 lines that had read as zero).

### The loop closed — measured, not asserted
- `--pr` over this session's 9 merged PRs → **23 paths**, every PR reconciled (`N reported, N read`).
- The first entry written: `~/.claude/analyze-service-index/devrc/opencode.md`.
- Re-running the identical command then flipped `scope-absent` → **`resolved`**, matching `via filename 'opencode'` on 3 real paths.
- **Census moved off its anchor:** `22 entries · datapacket-talos 21 + devrc 1 · created_by handoff 1 · unstamped 21` (anchor was `21 / 1 scope / 21 unstamped`).
- The autocommit **bootstrapped the new scope on first sight**: `initialised a new repository (branch trunk, no remote)` → `committed 660d1ee`. Both scopes: `datapacket-talos` 3 commits, `devrc` 1 commit, **0 remotes each**.
- 🔴 **The identity-seeding fallback fired for the first time ever** — `analyze-service index <analyze-service-index@localhost>`. It had never been exercised because the pre-existing scope carries a repo-**local** git identity; a brand-new scope has none and `GIT_CONFIG_GLOBAL=/dev/null` hides the global one. That code path was written in #361 and only proved itself today.

## Open investigations — live diagnosis state

### The reopening gate in the decision record asks the wrong question
- **Symptom:** `claudedocs/decision-subsystem-store-rejected-2026-08-11.md` says revisit at "≥5 entries outside the current single scope, or ≥5 non-infra entries".
- **Observed:** P1's measurement showed the binding constraint is **coverage**, not entry class. Of 290 path-carrying sessions, **12** were in the one indexed scope and 7 of those resolved — a **58% hit rate inside covered scope**. The resolver works; the index covered ~4% of the repos worked in.
- **Leading hypothesis:** the gate should read "does the index cover the repos where work happens" — was 1 of ~12, now 2 of ~12.
- **Next probe:** none. This is a one-paragraph edit to that document.

### 155 of 290 path-carrying sessions have `cwd` basename literally `empty`
- **Symptom:** grouping `session-summary` rows by the last component of the `cwd` **column** returns `empty` for 155 of 290 sessions carrying ≥1 changed path.
- **Observed:** `cwd = 'empty'` is false for all (not the literal string); the bucket is `depth=4`, i.e. `/a/b/empty`; basename query returns exactly `empty 4 155`; `countIf(repo='')` is **0** for both sources.
- **Ruled out:** `cwd` absent or empty (measured 0 both sources). Reading `cwd` from the *payload* — **my error**: opencode's payload has no `cwd` field; it is a top-level **column**.
- **Leading hypothesis:** an opencode default/placeholder working directory. Unconfirmed.
- **Next probe:** `SELECT DISTINCT cwd FROM activity.events WHERE kind='session-summary' AND splitByChar('/', trimBoth(cwd))[-1]='empty' LIMIT 5`, then look at that directory on disk.
- **Why it matters:** those sessions can never resolve to a subsystem — 53% of the corpus permanently excluded. Changes no verdict already reached.

### opencode can emit a stringified list as a repo-relative path
- **Symptom:** `str(inp.get("filePath") or "")` — `str(["a.py"])` is `"['a.py']"`, which `to_repo_relative` **accepts** and emits. Manufactured data in the module whose contract is that nothing is invented.
- **Observed:** searched the emitted corpus for that shape — **zero occurrences**. The path exists; it has never fired.
- **Ruled out:** that #408's claude-side fix covers it — it does not. #408 deliberately **validates and skips** rather than coercing, precisely because coercion manufactures data.
- **Next probe:** decide validate-and-skip on the opencode side, or a documented known-limitation.

## Next steps (ranked)

1. **Correct the reopening gate** in the decision record (above). One paragraph; it is the durable record and currently misleads.
2. **Merge `#418`** after pushing this revision to its branch.
3. **Watch the census.** `python3 scripts/lib/subsystem_touch.py --census`. It has moved once. If `created_by: handoff` entries do not keep accruing across repos over the next few weeks, the writer is not sticking and should be reconsidered — the same discipline that killed the original design.
4. **Investigate the `empty` cwd bucket** (probe above).
5. **Decide the opencode stringified-path question.**
6. **The floors line in `scripts/run-tests.sh` has now conflicted on eight consecutive PRs** (twice on one). Per-target floors (#397) removed the base-dependent global total but made every test-adding PR touch the table. Worth measuring separately.
7. **Worktree debt** — 17+ agent worktrees under `.claude/worktrees/`.

## Gotchas / decisions / dead-ends

- 🔴 **A large subsystem store was proposed and REJECTED on measurement** — no `type:`-driven sections, no dependency graph, no multi-verb CRUD. Read `decision-subsystem-store-rejected-2026-08-11.md` before re-proposing.
- 🔴 **But the "no demand" half was CIRCULAR, and that was my error.** The only writer was an infra-recon command pointed at two cluster repos, so only infra entries in one scope *could* exist. `#415` closes exactly that. The surviving arguments are narrower: the `type:` taxonomy and graph were unjustified, and an opt-in multi-verb CRUD would not have stuck.
- **Adoption, measured across 17 commands:** six have never been invoked once. The lesson is not "opt-in fails" but **opt-in survives when it rides a ritual already performed, and dies when it *is* the ritual**.
- **Three path sources, blind in different directions — never merge their outputs.** git window: misses work that merged during the session. `--session`: misses **subagent** work (`isSidechain` excluded — 196 of 733 file-tool calls) and `Bash`-written files. `--pr`: sees subagents, but is the **branch union** — another session's commits, hand-made ones — and misses anything that never reached a PR. The flags are mutually exclusive on purpose.
- **`gh pr view --json files` silently truncates at 100** while `changedFiles` reports the truth. Guarded cap-agnostically by `len(paths) < changedFiles`. My own verification missed it because I checked against a 4-file PR — one measurement, generalised.
- **`--show-toplevel` was rejected deliberately:** it would make every agent worktree its own scope, sharding the store into hundreds of one-entry scopes.
- **Working-tree residue, not branch age, dominates git-window over-reporting** — a live run returned 6 of 6 paths belonging to other sessions.
- 🔴 **The store must never gain a remote**; no line of it may reach a public repo. `devrc` is PUBLIC. Each scope's `README.md` is authoritative over any command file.
- **Instrument validation kept being the actual finding.** Five mutation harnesses this session produced wrong verdicts before correction — a stale `.pyc`; a sweep whose `git checkout --` reverted the change under test; a parser regex-matching a traceback body; one environment-dependent on the pytest launch directory; one that overwrote the repo's `conftest.py`. A green sweep is a claim about the harness until a control proves otherwise.
- **`FETCH_HEAD` is repo-global** and is clobbered by concurrent sessions here — it produced one false verification. Resolve to an explicit remote-tracking ref and assert the sha.
- **`git diff --stat <branch>..origin/main` renders the branch's own edits with inverted sign.** I misread that as a collision and told an agent so. `git log <base>..origin/main -- <path>` is the right question.
- **zsh eats `:s`/`:h`/`:p` after an unbraced `$VAR` in a git ref** — `$B:scripts/...` silently reads the wrong thing. Brace it.

## How to verify

```bash
D=/home/zach/workspace/devrc
# the writer, both sources (read-only; the tool never writes)
python3 $D/scripts/lib/subsystem_touch.py --repo $D --session <your-scratchpad-uuid>
python3 $D/scripts/lib/subsystem_touch.py --repo $D --pr <n>[,<n>...]

# the falsifiability counter — anchor was 21 / 1 scope / 21 unstamped
python3 $D/scripts/lib/subsystem_touch.py --census

# both scopes versioned, contained, and with NO remote
for s in datapacket-talos devrc; do
  T=~/.claude/analyze-service-index/$s
  echo "$s: $(git -C $T rev-list --count HEAD) commits, $(git -C $T remote -v | wc -l) remotes"
done
systemctl --user show analyze-service-index-commit.service \
  -p ProtectSystem -p ProtectHome -p PrivateNetwork -p InaccessiblePaths --no-pager

# the authoritative gate (the local tier cannot reach the floor on this host)
nix build .#checks.x86_64-linux.pytests --no-link --print-build-logs 2>&1 | grep -E 'TOTAL collected|RESULT'
```
