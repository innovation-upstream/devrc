# Handoff: find-session-live-first — 2026-08-29

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
Make "find the thing I lost track of — is it still running, which tmux window, where did it
leave off" a **single cheap command**, for tracking ticket/task progress. It previously took
two tools and a human gluing them together: an observed run cost 127 s, $0.0056 and 9 tool
calls, 5 of them pure flailing.

## State now
- **PR #989 MERGED** as squash `890df043`. This doc's own PR **#1032 MERGED** as `6e7e85bf`.
- 🔴 **DEPLOYED — this changed after the first draft, and it was done by ANOTHER session, not
  by this work.** Verified on **both** hosts (a single-host measurement cannot demonstrate a
  two-host claim):

  | | workbench | laptop |
  |---|---|---|
  | `readlink -f ~/.claude/skills/find-session/SKILL.md` | `/nix/store/djplda84w8bi6ydiz4filgvvark1j0b7-devrc-claude-skills/…` | **identical hash** |
  | `grep -c -- "--live"` on that file | 9 | 9 |

  `--match` docs: 8 hits in `~/.claude/skills/session-manager/reference/payload-contract.md`.
  End-to-end from the checkout on `main`: `find-session.py devrc --live` → `LIVE (2 matched;
  searched: laptop, workbench)`.
- **The `ship.sh` blocker is gone.** `feat/flake-lock-and-discord-ext` landed (#1010,
  `2a8a8982`). The base clone is on `main` and was `merge --ff-only`'d to `6e7e85bf`; it was
  **behind 1, never ahead** — no un-pushed commits, so the diverged-host hazard did not occur.
- **Still NOT verified:** the partial-fleet path against a genuinely unreachable host. See
  ranked item 1 — this is the one claim in the whole feature with no live observation behind it.
- Open issues: **#1029** (three guard-walkability gaps), **#1030** (flake, second mechanism),
  **#1031** (two deferrals). **#1028 closed**, verified fixed by #1023.

**What shipped — carried forward verbatim, this is durable and not status:**

| | before | after |
|---|---|---|
| the whole question | 127 s, 9 tool calls | **1.25 s, one command** |
| resolved tail ("where it left off") | not answered | 1.62 s |

- `session-manager --match <substr>` — filters `task`/`label`/`codename`; `path` only under
  `--match-path`. **Measured: matching `task` gave 1 useful hit; matching `path` gave 29 of
  72 rows** (nearly every window shares a repo path), which is why `path` is opt-in.
- `session-manager detail <bad addr>` exits 3 and **names the indices that do exist**
  (`session 'scratch3' has windows ['1','2']; you asked for index '9'`). Previously a silent
  empty list — the defect that ate 5 of the 9 tool calls in the observed run.
- `hotkey_display` — one writer. `v` → `Alt+v`, `V` → `Alt+Shift+V`. 🔴 **Case is
  significant and is NOT a shift-modifier convention**: `M-v` = `scratch3`/violet, `M-V` =
  `scratch4`/Vapor, per `scripts/tmux-scratch-slots.sh:15-16`. The original transcript's
  answer said `Alt+Shift+V` for a `v` row and would have opened the wrong window.
- `find-session.py --live [--deep] [--tail N]` — live scan first (1.8 s), transcript walk
  (30.1 s) only on zero live matches or `--deep`; archive hits annotated LIVE/CLOSED/
  UNMEASURED; ambiguous `--tail` refused with candidates listed.

**Audit: 5 rounds** (`/audit-pr`), ended by the skill's own stop rule. Round 5's auditor was
asked directly and answered *"Has the ladder left the PR? Yes — it left it at round 4."*
Payload lines per round: **428 → 254 → 286 → 132 → 16**; by the end 96% of the diff was
guards pinning guards and no finding was reachable by a `find-session` user.

Gate on the **merged** tree (`c0e39f23` = `origin/main` + head): PR's own suites **838
passed**; node 1300/1300. Two pre-existing `main` failures at the time, both reproduced on a
clean `origin/main` control with the implicated files byte-identical — since fixed by #1023.

## Open investigations — live diagnosis state

### `test_subsystem_store_api.py` concurrent-append flake — TWO distinct mechanisms, only one fixed
- **Symptom + exact repro:** `tekton/devrc-pytests` (a **required** check with
  `enforce_admins: true`) fails intermittently on
  `test_EIGHT_concurrent_appends_all_survive` /
  `test_two_CONCURRENT_appends_of_DIFFERENT_bullets_BOTH_survive`, blocking every PR until a
  fresh push happens to sample green. No admin override.
- **Observed (with values):** sampling during #989 — Tekton@`0c874add` FAIL; local
  `nix build --rebuild`@`0c874add` FAIL; second rebuild@`0c874add` PASS; `origin/main` PASS;
  Tekton@`22ac6012` PASS. The captured failure self-classified
  `MECHANISM = TRANSPORT … ConnectionResetError: [Errno 104]`, **`wall=0.18s` for 8 racers**
  — neither of the test's own two enumerated mechanisms (lost append / wall-clock bound).
  `request_queue_size` is **never set anywhere in the tree** (re-checked after #1023);
  `ThreadingHTTPServer`'s default backlog is 5, against 8 simultaneous connects.
- **Ruled out:** *that #1023 fixed it.* #1023 (`8e33bf1d`) raised `HANG_TIMEOUT` 15 s → 60 s
  for a **`TimeoutError` out of `socket.py`** under scheduler starvation (measured ~60% of
  runs failing repo-wide on unrelated branches). A connection reset at 0.18 s cannot be a
  timeout, so that change does not reach this mode. Both are real; they fail the same test
  for unrelated reasons. Also ruled out: that #989 caused it — the file is byte-identical to
  `origin/main`.
- **Leading hypothesis:** listen-backlog overflow in the test's own harness, not a store bug.
- **Next probe:** set the backlog and re-sample —
  `nix build .#checks.x86_64-linux.pytests --rebuild` 20× consecutively.

### Something writes the operator's GLOBAL git config mid-run — uncharacterised
- **Symptom:** 4 `DEVRC-GITENV-VIOLATION` teardown errors in a full-suite run, all naming the
  same delta on `/home/zach/.config/git/config` (`b679f677b62c → 6413e667a239`).
- **Observed:** none of the 4 tests is in #989's diff; the file is outside the tree; the
  sandbox tier cannot see it and passed. Seen once, in another agent's audit run.
- **Ruled out:** #989 as the writer (its diff touches no git config).
- **Leading hypothesis:** a concurrent agent session. Not enough evidence to name it.
- **Next probe:** `inotifywait -m ~/.config/git/config` during a multi-session window, or
  check whether a hook writes it.

## Next steps (ranked)
🔴 **RENUMBERED once, deliberately.** The original item 1 (deploy) is DONE, so everything
shifted up by one. `claim-work --list` was checked first and **no live claim carried a
`find-session-live-first-*` slug**, so no claim was re-pointed. Keep this numbering stable now.

1. **Verify the live-first loop against a genuinely unreachable host.** 🔴 **No round of the
   5-round audit ladder ever did this** — the laptop answered every probe, so every
   partial-fleet check used a stub `session-manager` (real subprocess, real JSON parse, real
   `main()`, real exit code; simulated unreachability). The round-1 blocker *was* the
   partial-fleet path. Suspend the laptop, then run the `--live --deep` and `--live --tail`
   cases in **How to verify**. Repo: `devrc`.
2. **#1030** — the store-api flake's SECOND mechanism (see the investigation block). ⚠ Someone
   else holds `devrc-store-api-timeout-flake`, which is the *timeout* mechanism #1023 already
   fixed — a different failure of the same test. Do not read that claim as covering this.
   Repo: `devrc`.
3. **#1029** — three residual guard-walkability gaps in
   `scripts/tests/test_find_session_skill_contract.py`. Repo: `devrc`.
4. **#1031** — two knowingly-deferred items (`excluded_shells` measured-zero asymmetry; the
   row-field ledger's substring `__doc__` guard). Repo: `devrc`.
5. **Inherited from `handoff-find-session-opencode.md`:**
   `scripts/claude-hooks/tests/test_bash_guard.py:294`'s "no catastrophic backtracking" check
   still asserts on wall-clock and flakes under load. Its sibling item (dirty
   `embed_enlarge.js`) is CLOSED — it landed as #1010. Repo: `devrc`.

🔴 This list is a WORK QUEUE and `claim-work` is its lock — `claim-work --slug-for <this doc>
<rank>`, then `claim-work <slug> --subject "<text>"`, and sweep `gh pr list --state open` too.

## Gotchas / decisions / dead-ends
- 🔴 **The recurring defect in this work was never a logic bug — it was A CLAIM WIDER THAN
  THE THING THAT ENFORCES IT.** Both round-1 blockers, two of round 2's findings, four of
  round 3's six, and both of round 4's 🟡s. Round 5 proved it is *structural*: it added a
  fourth exit-2 cause to both the contract and the shipped doc **without implementing it**
  and got 131/131 green. Each new guard written to pin prose is itself a new prose claim.
  That is why the ladder was stopped rather than run again.
- **Live-first is the whole design.** The archive walk is 30.1 s; the live scan is 1.82 s and
  already carries `task`/`path`/`label`/`hotkey`/`status`/`waiting_signals`/
  `claude_session_id`. For "check on something I believe is in flight", the archive is the
  wrong instrument.
- **Match `task`, never `path`.** 1 hit vs 29 of 72. `path` is repo-level and near-constant.
- **Decided: a corpus selector does NOT force the archive leg.** `--live --opencode-only` can
  still leave the selected corpus unsearched when the live scan matches; it prints a notice.
  Forcing it would put 30 s on every `--live --claude-only`. Flagged, not taken.
- **Decided: partial-fleet coverage is gated COARSELY.** An archive hit carries a session id
  and cwd but no host, and cross-host `claude --resume` makes local-only inference unsound.
  A positive is a measurement regardless of coverage, so `LIVE` survives a partial fleet and
  only `CLOSED` needs full coverage. Cost: a hit reads UNMEASURED whenever any peer is down.
- **No new script, no new skill — deliberate.** The skill listing is charged to a context
  budget every session; a new entry point would cost that. `--match` went on
  `session-manager`, `--live` on `find-session.py`.
- 🔴 **`claude/skills/session-manager/SKILL.md` had 152 B of spare budget** (16,101 B against
  a 16,384 B ceiling, 131 B working floor). All new docs went to
  `reference/payload-contract.md`. Do not raise `MAX_BYTES`.
- **Dead end: a hand-typed coverage count.** It was measured wrong twice in consecutive
  rounds. The counts were ultimately **deleted**, not corrected — understated coverage that
  reads as precise is worse than no number.
- ⚠ **Concurrency cost, recorded:** #1028 was filed 3 minutes before #1023 fixed the same
  three things. Sweep `gh issue list` / `gh pr list --state open` before filing.

- 🔴 **`~/.claude/skills/session-manager/SKILL.md` contains ZERO mentions of `--match`, and
  that is CORRECT — do not read it as a failed deploy.** The skill body had 152 B of headroom
  against its 16,384 B ceiling, so every `--match`/`detail` doc went to
  `reference/payload-contract.md` (8 hits there). Grepping the skill body for a flag is the
  wrong deploy check for this subsystem; grep the reference file.
- 🔴 **"Deployed" was DISCOVERED, not performed by this work.** The first draft of this doc
  said NOT DEPLOYED and was correct when written; another session shipped in between. Re-run
  the two-host table above rather than trusting either statement — `readlink -f` is the
  arbiter, never a diff, and the store hash matching across hosts is what makes it a two-host
  claim rather than two single-host ones.
- ⚠ **Concurrent duplicate work is the standing hazard here, measured twice in one session.**
  #1028 was filed 3 minutes before #1023 fixed the same three things; and `claim-work --list`
  now shows others already holding `devrc-opencode-pin-1-18-21`,
  `espanso-ask-tiebreak-main-red` and `devrc-store-api-timeout-flake`. **Sweep
  `claim-work --list` AND `gh issue/pr list` before filing or starting.**
- **A `git log <sha>..origin/main` that comes back empty is a claim about the moment you
  fetched.** `main` moved twice inside this session's closing minutes. Re-fetch immediately
  before acting, not when you formed the plan.

## How to verify
```bash
# the whole use case, one command (~1.3s)
python3 ~/workspace/devrc/scripts/find-session.py <term> --live
# ...and where it left off, when the match is unique (~1.6s)
python3 ~/workspace/devrc/scripts/find-session.py <term1> <term2> --live --tail 20

# the two defects the original transcript hit
python3 ~/workspace/devrc/scripts/session-manager detail scratch3:9      # rc 3 + real indices
python3 ~/workspace/devrc/scripts/session-manager --json --lean --no-ch --match <t> \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['filters'])"

# hotkey case — v and V MUST differ (Alt+v = violet/scratch3, Alt+Shift+V = Vapor/scratch4)
python3 ~/workspace/devrc/scripts/session-manager --json --lean --no-ch \
  | python3 -c "import json,sys; r=json.load(sys.stdin); \
print({w['hotkey']: w['hotkey_display'] for h in r['hosts'].values() for w in h.get('windows') or [] if w.get('hotkey')})"
```

**DEPLOY check — run on BOTH hosts, compare the store hash:**
```bash
readlink -f ~/.claude/skills/find-session/SKILL.md          # same /nix/store hash on both?
grep -c -- "--live" ~/.claude/skills/find-session/SKILL.md  # expect >0
grep -c -- "--match" ~/.claude/skills/session-manager/reference/payload-contract.md  # NOT the SKILL.md
ssh zach@10.42.0.100 'readlink -f ~/.claude/skills/find-session/SKILL.md; grep -c -- "--live" ~/.claude/skills/find-session/SKILL.md'
```
🔴 Verify against the DEPLOYED artifact, not the checkout, and on BOTH hosts. `readlink -f` is
the arbiter of live-vs-stale, never a diff. Identical `/nix/store` hashes across the two hosts
is what upgrades two single-host readings into a two-host claim.
