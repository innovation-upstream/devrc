---
clawgate-task: none
---
# Handoff: signal-skill-eval — 2026-09-03

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Evaluate the signal skill's performance, quality, and token efficiency using activity telemetry data from ClickHouse.

## State now
- Branch: `main` (devrc), clone fast-forwarded. No code in flight.
- Clawgate: `resolve` ⇒ **rc=5, 0 tasks** (positive control passed — the endpoint answered 2 links for another session). No `clawgate-task:` field written. The doc's existing `clawgate-task: none` is still UNREADABLE (`field` ⇒ rc=2); see ranked item 4.
- **devrc#1285 MERGED** — squash `2b1d35526522`, `mergedAt=2026-09-04T17:07:22Z`, branch deleted, **both gates SUCCESS** (`tekton/devrc-pytests`, `tekton/devrc-nodetests`) on head `2baebb1f`. Verified BY CONTENT on `origin/main` (squash ⇒ ancestry is meaningless): floor `867` present, drift-guard present, parse block relocated.
- **Verified LIVE from `origin/main`, not from a branch:** `browser-agent --dry-run` with no goal and a fresh HOME ⇒ **rc=2 in 4 ms** with the correct error. Was 91,000 ms.
- **devrc#1271 MERGED earlier** — squash `01551061f5b8` (the `activity` skill correction), merged on an explicit operator decision with `devrc-pytests` RED; that red is the flake #1285 has now fixed.
- Issue **devrc#1284** OPEN by design (`--dry-run` cost), with a mechanical closing condition.

## Open investigations — live diagnosis state
### Signal skill token consumption patterns
- **Symptom:** Signal skill sessions show extremely high output/input ratio (40,000%+) suggesting over-generation
- **Observed:** Average session duration 1,213 minutes (20.2 hours), millions of tokens per session
- **Ruled out:** Not a cache inefficiency (96.5% hit rate is excellent) — via: measurement
- **Leading hypothesis:** Long-running coordination sessions with multiple skills generate excessive output
- **Next probe:** Analyze specific session transcripts to identify output reduction opportunities

### CLOSED — "signal skill over-generates output" was an instrument artifact, not a finding
- **Symptom + exact repro:** the prior doc reported 1,024 signal-skill sessions, 96.5% cache hit rate, a 40,000%+ output/input ratio and a 1,213-minute mean duration, and concluded the signal skill over-generates. Reproduce the defect with the doc's own recorded predicate:
  `JSONExtractString(payload,'skills_used','signal') IS NOT NULL`
- **Observed (with values):** four-arm control over `source='claude' AND kind='session-summary'`:
  - with the predicate ⇒ **1,946** distinct sessions
  - predicate REMOVED ⇒ **1,946** (identical — the filter selects nothing)
  - `IS NULL` negative control ⇒ **0** (the predicate can never be false)
  - real test `!= ''` ⇒ **6** sessions, all 6 on `host=laptop`, spanning 2026-08-28 → 2026-09-02
  ClickHouse `JSONExtractString` returns `''` on a missing key, never `NULL`, so `IS NOT NULL` is a tautology.
- **Ruled out:** "the signal skill has a token-efficiency problem" — the population it was measured on was never signal sessions, and the real population (6) is far too small to rank a skill on. via: measurement
- **Ruled out:** "output volume is a meaningful cost lever" — over 30 days output is **445,946,626 tokens = 0.293%** of all tokens (cache read 147.22 B = 96.9%, cache creation 4.73 B = 3.1%, uncached input 0.93 M). Eliminating 100% of output would cut ~0.3% of tokens; by Opus list rates it is ~9.7% of cost against ~90% for cache read+creation. via: measurement
- **Ruled out:** "the 40,000% output/input ratio indicates over-generation" — it reproduces exactly (**47,838%**) and is an artifact of the denominator: `input_tokens` counts only UNCACHED input, 932,199 tokens, i.e. 0.0006% of real input. The ratio divides output by a rounding error. via: measurement
- **Ruled out:** "long-running sessions are the right selector for high consumption" — `corr(duration_minutes, cache_read)` = **0.296**. `duration_minutes` is wall-clock span, so a resumed session reads as ~20 h while idle; the median 888 min is an artifact, not work. via: measurement
- **Ruled out:** "the very large per-repo CLAUDE.md is the differentiator" — context/turn is nearly uniform across repos (241K–323K). The repo with the 113 KB CLAUDE.md sits at **301K/turn**, BELOW devrc's 319K and homelab-talos's 323K. The ~42K-token fixed preamble is ~15% of a turn; the rest is accumulated tool output. via: measurement

### OPEN — the real lever: text-only assistant turns each cost a full context re-read
- **Observed (with values):** 30 days, 1,027 sessions, deduped `argMax(field, ingested_at)` per session:
  - `corr(assistant_message_count, cache_read)` = **0.967** — turns is THE driver
  - `corr(bash_calls, cache_read)` = **0.935**; Bash is **151,372 of 197,568** tool calls (**76.6%**), mean 167/session
  - mean assistant turns/session **563.3**; mean tool calls/session **259.1** ⇒ **0.46 tool calls per assistant turn**
  - mean assistant turns per USER message = **19.8**
  - cache-read cost per assistant turn ≈ **301 K tokens**; median context/turn 280 K, p95 461 K; heavy sessions 422–528 K
- **Leading hypothesis:** ≥54% of assistant turns carry no tool call at all (a FLOOR — parallel tool calls pack several into one message, so tool-carrying turns are fewer and text-only turns more). Each such turn triggers a full ~301K context re-read to emit ~1K of text: a ~300:1 ratio. Conservatively over half of all cache-read volume is spent on turns that invoke no tool. The lever is turn COUNT, not output verbosity.
- **Next probe:** classify text-only turns into removable (narration/preamble/status) vs necessary (final answer, question to user) on a sample of the heaviest sessions:
  `python3 ~/workspace/devrc/scripts/session-analysis/insights.py --days 14 --json`
  and cross-check against raw transcripts under `~/.claude/projects/*/`.

### CORRECTION to the block above — the "6 sessions, all laptop" figure was itself wrong
- **Symptom:** the CLOSED block above records the real signal population as **6 sessions, all 6 on laptop**, from ClickHouse `skills_used != ''`. That is the count in a **derived** surface, reported as if it were usage.
- **Observed (with values):** `find-session --skill signal` (transcript-derived, every reachable host; stderr carried only the opencode caveat and named **no** unreachable peer) returns **10** sessions — **9 laptop / 1 workbench**. ClickHouse `skills_used` returns **6**, a strict SUBSET. The 4 missing: `50e9157d…` (workbench), `6fb90d0d…`, `9f8092ed…`, `ef3bf4ba…`.
- **Ruled out:** "the 4 missing sessions were never ingested" — each HAS `session-summary` rows in ClickHouse (9, 13, 13 and 19 respectively); their `skills_used` map is simply **empty**. All 4 started 2026-08-19 → 2026-08-25. via: measurement
- **Ruled out:** "`skills_used` is reliable from its 2026-08-04 first appearance" — that date is when the field first appears, NOT when it became reliable; coverage is partial at least three weeks later. via: measurement
- **Leading hypothesis:** `skills_used` undercounts signal by **40%**. Treat transcripts (`find-session --skill`) as the defining surface and `skills_used` as derived. The token/cost findings in this doc are UNAFFECTED — they aggregate over all sessions and never filter on `skills_used`.
- **Next probe:** measure the undercount on a second skill to see whether 40% is signal-specific or general: compare `find-session --skill <other>` against the same `!= ''` test.

### CLOSED (mechanism identified AND empirically reproduced) — the CI red was a wall-clock timeout under load, not a defect
- 🔴 **REPRODUCED LOCALLY ON IDENTICAL CODE — three runs of `scripts/browser-bridge/tests`, three different results:** full-suite run **890 passed / 0 failed**; repeat 1 **888 passed / 2 failed** (952.70s); repeat 2 **889 passed / 1 failed** (938.31s). (A 4th run died with `FileNotFoundError` because the worktree was removed out from under the still-running background job — see Gotchas.) Non-determinism on a fixed tree IS the proof; it needs no CI sample and does not depend on the abandoned `devrc-ci-wzm79`.
- **Symptom:** `tekton/devrc-pytests` FAILURE on the PR while `main` was green and the diff was one markdown bullet in `claude/skills/activity/SKILL.md`.
- **Observed (with values):** the traceback ends in `subprocess.py:1209 in communicate` under `subprocess.run(["bash", str(CLI), *args], timeout=CLI_TIMEOUT_S)` at `test_browser_tab_ref.py:510` — a `TimeoutExpired`, so **no assertion ever executed**. 2 of 890 `browser-bridge` tests, marks clustered `.......F.........F........`. `CLI_TIMEOUT_S=300`. My pipeline ran **31m** against the main control `devrc-ci-v75hh` (`093b279a`) at **10m**, which SUCCEEDED. Cluster carried **8–11 concurrent PipelineRuns** throughout.
- **Ruled out:** "the diff caused it" — the failing test drives the browser-bridge CLI's `bw://` argument parsing via a spawned `bash`; a markdown file cannot reach it, and `main` (same tree minus that file) passed in CI. via: measurement
- **Ruled out:** "main is permanently red / this is an inherited failure" — RETRACTED. `devrc-ci-v75hh` on `093b279a` SUCCEEDED, so main was green. The earlier claim was built from four `Failed` PipelineRun statuses with **no log read** (the pods were already GC'd) — an absence that cannot distinguish mechanisms. via: measurement
- **Ruled out:** "per-target timings show no load inflation (1.1–1.4x)" — WRONG COMPARISON. Those were pure-Python targets; `browser-bridge` is the one that spawns a `bash` subprocess per test and is the load-sensitive one. via: measurement
- **Leading hypothesis:** exactly the flake class this repo already documents in `scripts/browser-bridge/tests/cli_budget.py` — *"being wall-clock it flaked under CI load. Measured 2026-08-25: `test_browser_cli_backs_off_on_429` failed exactly that way in the devrc-pytests gate."* That module also names the correct remedy for the systemic case: **a per-test `pytest-timeout` budget, NOT lowering `CLI_TIMEOUT_S` back under the CLI's own 4x60s bound.**
- **Next probe:** implement the per-test `pytest-timeout` budget `cli_budget.py` prescribes; the flake is already proven, so another green run would add nothing. Reproduce on demand with: `for i in 1 2 3; do python3 -m pytest scripts/browser-bridge/tests -q | tail -1; done` under concurrent load.

### OPEN — a LOCAL-ONLY failure that is NOT the CI one and is still unexplained
- **Symptom:** `scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py::test_a_body_file_written_by_a_heredoc_on_the_same_line_is_read` fails on THIS machine, deterministically, in <0.25s.
- **Observed:** `assert allowed(cmd)` -> `assert False` at line 429, for a `cat > /tmp/body.md <<'EOF' … EOF` followed by `clawgatectl task create --body-file /tmp/body.md`. Local full suite: `TOTAL collected=21045 passed=21041 skipped=3 failed=1`.
- **Ruled out:** "my change caused it" — reproduced identically on a detached worktree at `855c2ad7`, which predates every commit of this session. via: measurement
- **Ruled out:** "it is the same failure CI sees" — CI reports this file **PASS 310/310**; CI's 2 failures are in `browser-bridge`, which passes locally 890/890. Opposite in both directions. via: measurement
- **Leading hypothesis:** a host-environment dependency in the PreToolUse guard's heredoc parsing (this box vs the CI image). Not a flake — it is deterministic here.
- **Next probe:** run that single test in the CI image, or bisect the guard's heredoc branch against `$SHELL`/env differences.

### CLOSED — the CI flake was a 90 s warm timeout paid before argument validation
- **Symptom:** `tekton/devrc-pytests` FAILURE with a `subprocess.TimeoutExpired` at `test_browser_tab_ref.py:510`, no assertion executed.
- **Observed (with values):** `--durations` was BIMODAL, not load-shaped — `test_agent_without_any_tab_is_untouched` **234.22 s**, `test_the_free_text_list_covers_agent_too` **155.98 s**, `test_POSITIVE_CONTROL_…` **90.10 s**, the other 51 tests ~0.5 s each. Direct repro, fresh HOME, no goal: **91 s**, stderr `could not warm the isolated opencode config dir … timeout 90s … needs to npm-install @opencode-ai/plugin` THEN `a goal is required`. `browser-agent` parsed its args ~300 lines BELOW the warm/bootstrap block.
- **Ruled out:** "load flake, fix with a `pytest-timeout` budget" (the pre-registered rank 1) — `cli_budget.py` prescribes that for preventing a CI Task-ceiling ABORT, not for this; it would fire on a starved-but-healthy test and mask the cause. via: code
- **Ruled out:** "`main` is permanently red / inherited failure" — RETRACTED. `devrc-ci-v75hh` on `093b279a` SUCCEEDED. The earlier claim came from four `Failed` PipelineRun statuses with **no log read** (pods GC'd). via: measurement
- **Resolution:** moved arg parsing above the warm block (91,000 ms → 4 ms on `origin/main`), and stubbed `browser-agent` for the two goal-supplying tests. `test_browser_tab_ref.py` 508.93 s → 28.76 s; `browser-bridge` target ~950 s → ~170 s; slowest single test 234.22 s → 0.51 s. Margin against `CLI_TIMEOUT_S=300`: 1.28× → 588×.

### OPEN — `test_clawgate_task_interview_guard` fails on THIS HOST only, on every branch
- **Symptom + exact repro:** `nix develop <wt> --command bash -c "cd <wt> && python3 -m pytest scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py::test_a_body_file_written_by_a_heredoc_on_the_same_line_is_read -q"` ⇒ `1 failed in ~0.2s`.
- **Observed:** `assert allowed(cmd)` → `assert False` at line 429, for `cat > /tmp/body.md <<'EOF' … EOF` followed by `clawgatectl task create --body-file /tmp/body.md`. Full local gate: `TOTAL collected=21058 passed=21054 skipped=3 failed=1`.
- **Ruled out:** "this session caused it" — reproduces identically on a detached worktree at `855c2ad7`, which predates every commit of this arc. via: measurement
- **Ruled out:** "CI sees it too" — CI reports that file **PASS 310/310**, twice (runs `devrc-ci-9j6sc`, `devrc-ci-hc6np`). via: measurement
- **Leading hypothesis:** a host-environment dependency in the PreToolUse guard's heredoc parsing (this box vs the CI nix image). Deterministic here, so not a flake.
- **Next probe:** run that single test inside the CI image, or bisect the guard's heredoc branch against `$SHELL`/env differences between the two.

## Next steps (ranked)
1. Explain the local-only `test_clawgate_task_interview_guard` failure. It makes the FULL local gate red on every branch on this host, so `RESULT: FAIL` is now background noise here — which is exactly how a real failure gets waved through. Repo: `devrc`. Touches `scripts/claude-hooks/`.
   forcing: regression — a permanently-red local gate trains every session on this host to ignore it, and this session already had to special-case it twice to read its own results.
2. Decide devrc#1284 (`browser agent --dry-run` runs a full model session: ~90 s warm + up to 120 s). Either make it cheap or document the cost in `--help`/README so the name stops implying validation. Repo: `devrc`. Touches `scripts/browser-bridge/`.
   forcing: none
3. Quantify the `skills_used` ClickHouse undercount beyond `signal` (measured 40% low there: `find-session --skill signal` = 10, `skills_used` = 6), then decide backfill vs the documented caveat already merged in #1271. Repo: `devrc`. Touches `scripts/collector/`.
   forcing: none
4. Repair this doc's `clawgate-task: none` front-matter field so `/resume` stops emitting a GAP line every read. Repo: `devrc`.
   forcing: none
5. Sample ~5 of the heaviest sessions' transcripts and classify text-only assistant turns as removable vs necessary, to size the saving the token analysis implies. Repo: `devrc`. Touches `scripts/session-analysis/`.
   forcing: none

## Gotchas / decisions / dead-ends
- Clawgate task resolution returned NO SESSION ID (expected for opencode sessions)
- ClickHouse queries require SOPS age key for credential decryption
- Signal skill is primarily used on laptop (88% of sessions)

- 🔴 **`JSONExtractString(payload,'k','sub') IS NOT NULL` is ALWAYS TRUE in ClickHouse** — it returns `''` on a missing key. Any session-selection predicate built this way silently selects the whole table and every derived statistic becomes a population statistic. The negative control that catches it in one query: run the same aggregate with the predicate REMOVED and with `IS NULL`; if arm 1 equals arm 2 and arm 3 is 0, the filter is inert. Test membership with `!= ''`.
- The `activity` skill already documents the correct entry point — **`find-session --skill NAME`**, not hand-written SQL — and records the same measured 6 signal uses. Reading the skill first would have prevented this entirely.
- `skills_used` attribution is **forward-only, first rows 2026-08-04**, and only **227 of 1,027** sessions in the 30-day window carry a non-empty map. No skill-level attribution is possible before that date; report "no recorded use since <date>", never "never used".
- `duration_minutes` is wall-clock transcript span, NOT active time — a `claude --resume` session spans days of idle. Never use it to rank sessions by work done (`corr` with cache_read is 0.296).
- `session-summary` rows are append-only — always dedupe with `argMax(<field>, ingested_at)` grouped by `session`, or every aggregate double-counts.
- Cost shares assume the measured model mix (`claude-opus-5` in 802 of the 30-day sessions, haiku-4-5 in 100, `<synthetic>` in 347).
- SOPS flag ordering matters: `sops -d --extract '…' --input-type yaml <(git show …)` — putting `--input-type yaml` AFTER the process substitution yields an empty password and a confusing `AUTHENTICATION_FAILED` from ClickHouse rather than a decrypt error.
- devrc is a **PUBLIC** repo — client repo names are deliberately kept out of this doc.

- 🔴 **The vacuous predicate exists NOWHERE in devrc's committed tooling** — scanned `scripts/` + `claude/` for `JSONExtract*(...) IS [NOT] NULL`; **0 hits**, with a positive control proving the regex matches the two known-bad forms and ignores the safe `!= ''`. So the earlier "audit the codebase for this shape" item was **retired**: the defect was introduced ad-hoc in a session and only ever lived in this handoff. The durable fix is the skill correction (devrc#1271), not a code change.
- 🔴 **`find-session --skill` writes its report to STDOUT and its caveat to STDERR — do not split those streams with `cmd 2>&1 >/dev/null` in zsh.** MULTIOS copies stdout into the redirect too, so that idiom returns stdout while looking like it returns stderr, and a "no unreachable peer" conclusion drawn from it is unfounded. Redirect each to its OWN file and read both.
- 🔴 **A poll loop that emits only on TRANSITIONS is indistinguishable from a dead poller.** A 20-minute Monitor over two `pending` gates exited 0 having printed nothing, because its filter emitted only newly-settled checks — the docs' "silence is not success" trap. Emit a per-poll heartbeat so *still pending* and *dead* are different observations.
- ClickHouse aggregate aliasing: `SELECT any(host) AS host … GROUP BY host` is `ILLEGAL_AGGREGATION`. Alias to a different name (`AS h`) or nest the aggregate in a subquery.

- 🔴 **The PRIMARY CLONE `~/workspace/devrc` was switched to `feat/mention-system-repos` mid-session by another session.** `handoff_doc.py` commits to whatever branch the checkout sits on, so running it against `$DEVRC` at that moment would have landed this doc on a teammate's feature branch. **Check `git -C $DEVRC branch --show-current` immediately before any handoff write, and use a worktree off `origin/main` when it is not on main** — which is what this update did.
- 🔴 **A `TimeoutExpired` reads as a test failure but no assertion ran.** Before attributing a red test to a diff, look at where the traceback ENDS: `communicate()` / `subprocess.py` means the process was killed by the harness's own net, and the verdict is about wall-clock, not behaviour.
- 🔴 **Never `git worktree remove` a tree a BACKGROUND job is still using.** Removing `wt-activity` while the 3x repeat loop was mid-run killed its third iteration with `FileNotFoundError`. The job reported `exit code 0` regardless, so the loss was visible only in the output text — check for live processes under a worktree before removing it.
- 🔴 **A background command ending in `| tail -N` writes NOTHING until it finishes** — `tail` cannot flush. A 25-minute run looked like a 0-byte dead job. Redirect to a file and read it, or drop the tail.
- 🔴 **A `jq` error inside `cmd | jq ... || echo "none"` prints the FALLBACK, which reads as a clean negative.** A precedence bug (`... // "?" | .[0:8]` binding across later fields) produced `Cannot index string` and therefore "NO run for dc148dad yet" — while the run existed and was Running. Never let a parse failure share an exit path with a real zero.
- Merge method here is SQUASH, so `git merge-base --is-ancestor` is false forever after. Verify by content (`git show origin/main:<path> | grep -c`) plus `gh pr view --json mergedAt,mergeCommit`.

- 🔴 **A bare `pytest <target>` is NOT the gate — only `scripts/run-tests.sh` is.** CI went red on #1285 with `failed=0`: `FAIL scripts/browser-bridge/tests (collected=912 above drift ceiling 895, floor 716)`. Adding tests moves the per-target COUNT band, and bare pytest does not enforce it. The full gate HAD been run earlier in the session — before those tests existed — and then abandoned for the faster runner. **After adding tests, run the repo's own gate.** Re-pin by copying the number the failure prints (`867`), never by re-deriving; the rule is `collected - min(50, max(1, collected/20))`.
- 🔴 **Three instrument failures this session, one shape: a check real about one thing and MUTE about the thing that mattered.** (a) A regression guard drafted against `_oc_calls()` would have been VACUOUS — the fake opencode deliberately never logs `debug agent`, so it passes on the old code too; switched the observable to the config dir and measured BOTH arms. (b) `find … | xargs command grep -l 'browser-agent'` returned NOTHING while a direct `command grep -c` on the same file returned **24** — a spelling check masquerading as a containment check, and it produced a false scope claim in a PR body. (c) The bare-pytest/gate gap above. **Prove a zero can go non-zero before quoting it.**
- 🔴 **A `TimeoutExpired` reads as a test failure but no assertion ran.** Look at where the traceback ENDS — `communicate()` / `subprocess.py` means the harness's own net killed it, so the verdict is about wall-clock, not behaviour.
- 🔴 **An audit round can introduce a false-coverage claim while fixing false-coverage claims.** #1285 round 1 added a 🔴 comment asserting `test_opencode_config.py` §7 gates the parse block's position. Round 2 built that mutant: **11 passed**, `browser-bridge` **891 passed** — §7 pins `preflight < bootstrap_call`, and moving the block reorders neither. Fixed by writing the guard that makes the claim true, not by rewording it.
- 🔴 **Ladder stop:** #1285 stopped on the ATTRIBUTION gate — two consecutive rounds of **zero executable payload** (proved by `cmp` on comment-stripped source: 360 lines, identical) — **not** on a clean round. Round 2 found real defects. The two are indistinguishable in a findings list unless written down.
- 🔴 **The devrc primary clone's branch is unpredictable** — it was on `feat/mention-system-repos` mid-session. `handoff_doc.py` commits to whatever branch the checkout sits on, so a handoff would have landed on a teammate's branch. **Check `git -C $DEVRC branch --show-current` immediately before any handoff write**, and use a worktree off `origin/main` when it is not on main.
- 🔴 **`gh issue create` with `--body-file "$VAR/x.md"` is REFUSED** by the closing-condition gate — it cannot evaluate a shell variable, so it cannot read the body, and it blocks rather than failing open. Pass a literal path.
- Never quote line numbers in a tracker: #1284's pointers were staled by #1285 within the hour. Name symbols instead.
- A background command ending in `| tail -N` writes nothing until it exits (`tail` cannot flush) — a 25-minute run looked like a 0-byte dead job.
- Verify a squash merge BY CONTENT (`git show origin/main:<path> | grep -c`), never by ancestry; `--is-ancestor` is false forever after a squash.

## How to verify
```bash
# 1. The shipped fix, from origin/main (not a branch) — expect rc=2 in single-digit ms
V=$(mktemp -d); git -C ~/workspace/devrc worktree add --detach "$V" origin/main -q
H=$(mktemp -d); time HOME="$H" bash "$V/scripts/browser-bridge/browser-agent" --dry-run
git -C ~/workspace/devrc worktree remove --force "$V"

# 2. The gate — run THIS, not bare pytest, after any test-count change
nix develop ~/workspace/devrc --command bash ~/workspace/devrc/scripts/run-tests.sh ~/workspace/devrc
#   expect: PASS scripts/browser-bridge/tests (collected=912 passed=912 floor=867)
#   the ONE expected red on this host is test_clawgate_task_interview_guard (ranked item 1)

# 3. The merges, by content
gh pr view 1285 --repo innovation-upstream/devrc --json state,mergedAt,mergeCommit
git -C ~/workspace/devrc show origin/main:scripts/run-tests.sh | grep -c 'browser-bridge/tests|867'   # 1

# 4. The authoritative skill-usage surface (ClickHouse skills_used undercounts by 40%)
python3 ~/workspace/devrc/scripts/find-session.py --skill signal >/tmp/fs.out 2>/tmp/fs.err
grep -c 'claude --resume' /tmp/fs.out   # 10 (9 laptop / 1 workbench)
```
