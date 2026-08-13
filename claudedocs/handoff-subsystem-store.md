# Handoff: subsystem-store — 2026-08-13

## Goal
A durable store for subsystem data + history with clean Claude Code integration.
**Built, deployed, verified end to end.** The read half is now cheap enough to run every
`/resume` and searchable. What remains is a short list of decisions, not a build.

## State now

- **Branch:** `main` at `6eaeb61`, clean tracked tree (4 untracked: `.envrc`, `.opencode/`,
  `claudedocs/proposed-rules-cut/`, `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`).
- **Merged this session:** `#436` step-4 probe-first + cwd-mismatch routing · `#418` the
  previous revision of this doc · `#440` the recall **digest** · `#441` the reopening-gate
  correction · `#442` `--search` + the 100-cap paginated index.
  (`#437` clawgatectl, `#438` clickup, `#439` clawgate stuck-detector landed from other sessions.)
- **Deploy:** `ship.sh rc=0` — both hosts converged and **verified**, 429 (workbench) / 391
  (laptop) managed artifacts checked, **0 dangling on either**. `drift-check.sh` clean.
- **`/resume` step 4 verified end to end** against the deployed artifact, not the repo:
  `~/.claude/skills/resume/SKILL.md` resolves into `/nix/store/…-devrc-claude-skills/` and is
  `cmp`-identical to `origin/main`. Everything dirty is untracked, so the flake source *is*
  `origin/main`.

### The numbers that were the point
```
30 entries
  by scope:      civitai 1 · civitai-app-starters 1 · datapacket-talos 26 · devrc 1 · homelab-talos 1
  by created_by: analyze-service 1 · handoff 8 · unstamped 21
```
Anchor recorded before any second writer existed: **21 entries · 1 scope · 21 unstamped.**
Five scopes now, three of which did not exist this morning.

**`/resume` step 4 cost: 7,871 → ~1,212 tokens (84.6% less) AND strictly more complete** —
lists all entries instead of hiding 13 of 25 behind a `--limit 12`.

## Open investigations — live diagnosis state

### `--session` throws away 112 cleanly-attributable paths, and its refusal states a falsehood
- **Symptom + exact repro:** a session whose cwd is repo A but whose edits are absolute paths
  into repo B is refused: `subsystem-touch: transcript cwd does not match: session <id> ran in
  <A>, but --repo resolves to <B>.` Reported twice in one day by two different agents.
- **Observed (with values):** the refusal's stated reason — *"Every path in a transcript is
  relative to the session's own cwd"* (`subsystem_touch.py:1404`) — is **false**. Paths come
  from the tool call's `file_path`, which is absolute when the caller passed one;
  `changed_paths.py:270 to_repo_relative()` already accepts absolutes and buckets
  outside-cwd ones into `outside`, of which only `len(outside)` is emitted (`:280`).
  Measured over 120 recent transcripts: **1,072 distinct file-tool paths, 945 outside cwd
  (88%)** — 602 agent scratchpad, 143 temp worktree (only 32 still on disk), **112 another
  real repo under `~/workspace` (absolute, unambiguous)**, 54 other, 34 home/dotfiles.
  Independently corroborates the extractor's own 2026-08-11 figure (470 of 3,290 under cwd,
  14.3%).
- **Ruled out:** that this is the "four blind windows" story. In the first report the session
  had **1 file-tool call in 158 records** (the handoff doc itself), 0 subagent turns, 0
  file-writing `Bash` — no window was hiding work. In the second the agent correctly fell
  through to `--commit` and wrote a good first entry for `homelab-talos`. **Both agents
  behaved correctly; the guard's outcome was right and its stated reason was wrong.**
- **Leading hypothesis:** the guard's safety property (never read an A-relative path as
  B-relative) is sound and must stay. It over-refuses only because it discards absolute paths
  that resolve under `--repo`, which need no inference at all. Would take the session window
  from 127 to 239 attributable paths.
- **Next probe:** none needed to decide — the yield is measured. Implement: correct the
  message, then report only paths that are `os.path.isabs()` **and** resolve under
  `_toplevel(repo)`. Do **not** design around the 143 temp-worktree paths: mostly
  unrecoverable, and 38 such worktrees were removed this session.

### The >2-page index shape has never existed in real data
- **Symptom:** three shipped defects in `#442`'s pagination, all invisible to a green
  3,679-test suite.
- **Observed:** largest real scope is 26 entries against a 100-line cap, so the feature's own
  boundary was unreachable from live data. Found only by building a synthetic 150- and
  250-entry scope. Root cause of the worst one: the truncation notice was gated on
  `listing_total > len(listing)` — *"this page is not the whole index"*, true on the last page
  too — so page 2 of 2 announced 100 phantom entries and routed to `--page 3`.
- **Ruled out:** that a green gate says anything here. It was green through all three.
- **Next probe:** when any scope crosses 100 entries, re-run the four page cases against the
  real store (`--page 1/2/last/past-end`) rather than trusting the synthetic tests.

## Next steps (ranked)

1. **DIFFED AND DECIDED — NOT EXECUTED.** All 23 diffed, see "Worktree verdict" below: 22 DROP,
   1 KEEP (`#355`), and one salvage the guess would have missed. 🔴 **The drops have NOT been
   run** — 24 worktrees were still present at 2026-08-13 13:10 (a `git worktree list` count
   above that includes any agent worktrees created since). **Do step 2 BEFORE executing any
   drop:** `ea82146` is inside the DROP set and holds the only copy of the two rules.
2. **Salvage two rules from `zach/rules-multiagent-lessons` (`ea82146`), then drop it.**
   `cross-repo-worktree` and `sibling-agent-kill` are absent from BOTH `claude/RULES.md` and
   `claude/RULES-ARCHIVE.md` on `main` — see the verdict section for the measurement. Needs a
   branch + PR, and both must fit under the `test_rules_size.py` ceiling (read the constants
   there; budget an eviction in the same commit).
3. **Fix the `--session` cwd-mismatch message and add the absolute-path window** (above).
4. **Watch the census.** `python3 scripts/lib/subsystem_touch.py --census`. 21→30 across 5
   scopes with 8 handoff-written and now 1 `analyze-service`-written. If it stalls, the
   writers are not sticking.
5. Investigate the `empty` cwd bucket (155 of 290 path-carrying sessions, `depth=4`,
   `/a/b/empty`); decide the opencode stringified-path question (`str(["a.py"])` is accepted
   by `to_repo_relative`; zero occurrences in the emitted corpus — the path exists, has never
   fired).
6. **The floors line in `scripts/run-tests.sh`** has conflicted on ~9 consecutive PRs. Worth
   measuring separately.

## Worktree verdict — 2026-08-13, all 23 diffed

**Method, and why the obvious diff is the wrong one.** `git diff origin/main <head>` is
dominated by what **main** added since the branch forked — it reported 100–500 changed files
for branches that touched 3, and reads as "lots of unique work" when the arrow points the
other way. The measurement that answers the question is: **take the lines the branch ITSELF
added (`merge-base..head`), and count how many are present in main's current tree.** Script
kept at `scratchpad/landed.sh`.

🔴 **The instrument was validated before its verdicts were read** — positive control
`c8b9f8d` (`#442`, known merged) → **100%**; negative control `ed17f4f` (`#355`, never
merged) → **2%**. It discriminates. The first run of it was also *wrong* (`grep -c` emitting
`0` twice into an arithmetic context) and the negative control is what exposed that.
🔴 A **low score is not proof of lost work** — reworded, restructured and *retired-path*
content all score low. Every head under ~95% was opened and read; the verdict below is from
the reading, not the number. Two grep traps fired during that reading and were caught by
controls: `\|` under `grep -E` (literal, not alternation) and a `&&`-chain whose "file does
not exist" message actually meant "count was 0".

### Ancestry collapses 5 outright (no diffing needed)
`91aaa21`, `52fd995` are plain `main` commits, 0 own commits. `a901486` ⊂ `9d61558` ⊂
`80fb7f1`; `aa8fff4` ⊂ `8cb8692`; `191a336` ⊂ `5bce915`.

### DROP — 22
| worktree | head | evidence |
|---|---|---|
| `devrc-perm-junk` | `6974089` | `#380` merged; **100%** of its 371 added lines in main |
| `devrc-resume-handoff` | `d2e02d4` | `#326` merged; **99%** — the only gap is `claude/commands/resume.md`, a path main **retired** |
| `devrc-skilldocs` | `42f4022` | `#213` merged. Its 2 later commits fixed a stale "73 pass / 2 skip" e2e figure — **main's clawgate SKILL.md contains no `73` at all**, the sentence was rewritten. Fix is moot |
| `agent-a0604347…` | `c8b9f8d` | `#442` merged; **100%** |
| `agent-a0fc45cd…` (`p377-docfix`) | `191a336` | **99%**; also a strict ancestor of `integration-v2` |
| `agent-a1d607f8…` (`fix361-worktree`) | `51cc025` | **99%**. Residual is *older wording* — its `die "git is not on PATH"` is in main as `die "the version-control binary is not on PATH"` |
| `agent-ae800ad6…` (`fix361-round3`) | `a5e1f98` | **99%**; same — main's `commit.sh` has `ASI_IGNORED`/`ASI_CANDFILE`/`ASI_SORTED`/`ASI_NOHOOKS`/`cleanup()` and is the newer copy |
| `agent-aa81bad9…` (`pr362-fix-83`) | `9404ab4` | scored **0%** only because it edits retired `claude/commands/`. Every marker of its store-safety spec is in `claude/skills/analyze-service/SKILL.md` — incl. the literal sha `60e6d9d` |
| `agent-af19599e…` (`integration-v2`) | `5bce915` | **99%** of 4,036 added lines; an integration branch of `#377`/`#391`/`#384`/`#392`, all landed |
| `agent-a3847d8a…`, `agent-acc29d9b…` | `91aaa21`, `52fd995` | 0 own commits — plain `main` checkouts |
| `agent-a3c74c3d…` | `0ada9b8` | **92%**; script+tests 100%, SKILL.md restructured into `reference/*.md` |
| `agent-a463578a…` | `80fb7f1` | **95%**; the 66 residual script lines are the superseded busy/idle/stale counter model. Its headline case IS in main — `_NO_SERVER_RE` + the doc at `scripts/session-manager:62,1432` |
| `agent-a4f43a71…` | `ac35b32` | **99%**; 7 residual lines are prose reflow |
| `agent-a5abb942…`, `agent-a91600df…`, `agent-af945752…` | `aa8fff4`, `a901486`, `9d61558` | strict ancestors of heads already dropped |
| `agent-a688672b…` | `8cb8692` | **100%** |
| `agent-a9bbee8b…` | `6b86d64` | **97%**; residual is the **pre-rename** `AGENT_IDLE_THRESHOLD_SECS`, which landed in `#439` as `STUCK_THRESHOLD_SECS` |
| `…/scratchpad/wt-436` | `6e16ed1` | **100%** |
| `/tmp/sa2` | `a41f3e5` | `#332` merged; **85%**. Residual is `claude/commands/prune-skill.md` (retired) plus `test_real_skills_classify_…`, a corpus test pinned to `manage-alerts`/`app-blocks` — skills that do not exist here |

### KEEP — 1
- `agent-ad6f8275…` / `airvpn-killswitch-env` `ed17f4f` — **`#355`, OPEN, marked
  🔴 DO-NOT-MERGE-YET**. 1,607 added lines, **2%** in main. Genuinely unlanded, deliberately.

### SALVAGE then drop — 1 (the case the "superseded intermediate" guess would have lost)
`devrc-rules-lessons` / `zach/rules-multiagent-lessons` `ea82146` — `#313` **merged**, and it
scores **1%** by bytes, which means nothing: RULES.md is continuously reworded. Measured by
**anchor** instead, 20 of its 22 lessons are in main. Two are not, in **either** `RULES.md`
or `RULES-ARCHIVE.md`:

- 🔴 **`cross-repo-worktree`** — `isolation: "worktree"` builds the worktree from your
  **CURRENT** repo, not the repo the task names. Dispatch at a different repo than your cwd
  and every agent silently gets a worktree of the wrong one, with no error. Tell: an agent
  reporting that a file named in its brief does not exist.
- 🔴 **`sibling-agent-kill`** — filter resolved PIDs by `/proc/<pid>/cwd` to your own
  worktree before killing; a box-wide `-f` pattern reaches a *sibling agent's* processes. One
  auditor clearing its own hung run killed ~15 PIDs and destroyed another agent's in-flight
  test run, whose next attempt collapsed with 0 files collected and exit 144 — both readable
  as code defects. Main's `pgrep`/`pkill` bullet stops at `/proc/<pid>/cmdline`.

Two near-misses, deliberately NOT salvaged: `cheap-control` and `count-tests` anchors are
gone but their rule text is in main (`count-tests` → `count-not-exit-code`); `stale-gate-base`
survives compressed inside the `merged-tree` bullet as "check how far behind main a PR is".

🔴 **Removing a DETACHED worktree makes its commits unreachable and GC-able** — the 8
detached heads have no branch ref holding them. Tag them before removal if any of the
"superseded" calls above is to stay reversible:
`git tag preserved/<name> <sha>` for `0ada9b8 80fb7f1 ac35b32 aa8fff4 8cb8692 a901486
6b86d64 9d61558`. Branch-backed worktrees are safe to `git worktree remove` — the ref keeps
the commits.

## Gotchas / decisions / dead-ends

- 🔴 **`nix build` returns a CACHED result silently** — 0-byte log, exit 0, and `grep FAILED`
  matches nothing. That zero is indistinguishable from "no failures". Use `--rebuild` to force
  a genuine re-run, but **`--rebuild` errors on a drv that was never built** (`some outputs …
  are not valid, so checking is not possible`). For a cached drv, `nix log <drv>` *is* the real
  build output. A valid cached output is itself proof the suite passed — `flake.nix:268` fails
  the derivation when `run-tests.sh` exits non-zero.
- 🔴 **A pytest failure does NOT print `FAILED` here.** The runner uses `-q … -rs`
  (`run-tests.sh:1015`), which overrides the short summary to skips only. Look for
  `=== FAILURES ===`, the pytest summary line, and `FAIL  <dir>  (… failed=N …)`. Runner lines
  are prefixed `devrc-pytests>`, so **never anchor a grep at `^`** — that silently matched
  nothing here and would have read as green.
- 🔴 **Never pipe `ship.sh` or a gate to `tail`** — the pipe destroys the exit status. Reported
  `exit 0` over a real `rc=12` this session. Redirect to a file and echo `$?`.
- 🔴 **`rev-list origin/main..HEAD` is NOT a merged-ness test.** A squash merge never makes the
  branch head an ancestor, so it stays non-zero forever — it flagged **all 52** worktree
  branches as unmerged work. Classify by PR state (`gh pr list --state all --json
  headRefName,state`) or by `git diff origin/main <head>` being empty.
- 🔴 **A deploy can delete an unmanaged directory.** A `ship.sh` switch removed
  `~/.claude/skills/clickup` — described in `home.nix:1201` as a *"standalone repo"* and the
  only copy. Recovered only because `#438` had committed 32 of its files hours earlier; its
  nested `.git` was **not** captured and is gone. Check what a switch will touch before
  running one over an unmanaged path.
- **Four path sources, blind in opposite directions — never merge their outputs.** git window:
  misses work merged during the session. `--session`: misses **subagent** work (196 of 733
  file-tool calls), `Bash`-written files, worktree-mandated repos, cross-repo sessions.
  `--pr`: sees subagents, but is the **branch union** and misses direct pushes (144 of 200
  recent `datapacket-talos` trunk commits carry no `(#N)`). `--commit`: the primitive the
  others reduce to. The flags are argparse-exclusive on purpose.
- **`Edit` vs `Write` on an entry, MEASURED:** `Write` silently loses a concurrent append;
  `Edit` is bounded so the other bullet survives. 🔴 It does **not** reliably fail loudly — an
  earlier version of this rationale said it did and was wrong. The real safeguard is
  **re-read and re-apply to current bytes**.
- **A brand-new scope is `git init`ed, identity-seeded and committed by the store's own hourly
  timer** — measured three times now (`homelab-talos` was unversioned when written, 1 commit
  and 0 remotes an hour later). Do not create the repo yourself.
- 🔴 **The store must never gain a remote**; no line of it may reach a public repo. `devrc` is
  PUBLIC. Each scope's `README.md` is authoritative.
- **The `st_blocks` failure was a flake and the structural-split diagnosis was WRONG** —
  retracted, do not re-derive. `#435` fixed it. Separately: no stored gate log has **ever**
  recorded `failed=2` (248 TOTAL lines across 271 logs: values are 0,1,3,20,27,28,29,30,144),
  and nix keeps only the most recent log per derivation, so a red run later rebuilt green
  loses its evidence entirely.

## How to verify

```bash
D=/home/zach/workspace/devrc
# the read half — digest, index, search (READ-ONLY, never writes, no network, no subprocess)
python3 $D/scripts/lib/subsystem_recall.py --repo $D
python3 $D/scripts/lib/subsystem_recall.py --scope datapacket-talos --list
python3 $D/scripts/lib/subsystem_recall.py --scope datapacket-talos --search "nginx ratelimit"
python3 $D/scripts/lib/subsystem_recall.py --scope datapacket-talos --search "zzz kryptonite"  # must report NO MATCH + closest candidate

# all four write windows (read-only; the tool never writes)
python3 $D/scripts/lib/subsystem_touch.py --repo $D --session <scratchpad-uuid>
python3 $D/scripts/lib/subsystem_touch.py --repo $D --pr <n>[,...]
python3 $D/scripts/lib/subsystem_touch.py --repo $D --commit <sha>[,...]

# the falsifiability counter — anchor was 21 / 1 scope / 21 unstamped
python3 $D/scripts/lib/subsystem_touch.py --census

# every scope versioned, contained, NO remote
for s in $(ls -d ~/.claude/analyze-service-index/*/ | xargs -n1 basename); do
  T=~/.claude/analyze-service-index/$s
  echo "$s: $(git -C $T rev-list --count HEAD) commits, $(git -C $T remote -v | wc -l) remotes"
done

# the authoritative gate — read the CONTENT, never the exit code of a pipe
nix build .#checks.x86_64-linux.pytests --no-link --print-build-logs > /tmp/gate.log 2>&1; echo "rc=$?"
grep -aE 'TOTAL +collected|RESULT: (PASS|FAIL)' /tmp/gate.log

# deploy + host parity (read-only deadman)
scripts/drift-check.sh
```
