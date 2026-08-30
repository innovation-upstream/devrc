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
- 🔴 **The partial-fleet path is now VERIFIED against a genuinely unreachable host — this was
  the one claim in the feature with no live observation behind it, and it holds.** Eight
  cases across two instruments with different failure shapes; the **five** where a control
  could discriminate were each re-run as the SAME command on a complete fleet, and gave a
  different answer. Details in "Partial-fleet verification" below. **No defect was found**,
  so the ladder ends here.
- **Still NOT verified:** `live_scan`'s `status: "unavailable"` branch (NO host answers). The
  local host is scanned without ssh, so it cannot be made to fail genuinely — reaching that
  branch requires a stub, which is exactly what the rank-1 item existed to stop trusting. Say
  "stub-only" about it rather than "verified".
- Open issues: **#1030** is HALF closed — #1062 fixed the backlog mechanism, the
  `socket.py` timeout mechanism is confirmed STILL LIVE at 60 s. **#1029 closed** by
  #1071. **#1031 closed** by #1076. **#1028 closed**, verified fixed by #1023.

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

## Partial-fleet verification — 2026-08-29, the rank-1 item, CLOSED

**The gap it closed.** All five audit rounds replaced `session-manager` with a stub, so
layers 2–3 — *does the real `session-manager` mark a host `reachable: false` and still
return the other host's rows?* — had never run. It does:

```
laptop:    reachable=False  windows=0   error='ssh: connect to host … port 22: Connection timed out'
workbench: reachable=True   windows=50  error=''
```

**Instruments.** Nothing downstream of the TCP connect was simulated; both run the real
openssh binary and the real `session-manager`.

| | what it substitutes | failure shape | laptop error text |
|---|---|---|---|
| 1 — `PATH` shim rewriting the destination address | the destination IP only | `Connection timed out` after **4.013 s** (`ConnectTimeout=4`) | names `192.0.2.1` |
| 2 — `unshare -rn` + `-F /dev/null` | **nothing**; the address stays `10.42.0.100` | `Network is unreachable`, instant | names `10.42.0.100` |

Instrument 2 needs `-F /dev/null` because openssh refuses a root-owned system `ssh_config`
under the namespace's uid map — a namespace artifact, not the condition under test. ⚠ Its
first form (`unshare -rn` alone) failed at **config parse, not at the network**: a real ssh
failure, but the wrong mechanism, and it would have corroborated nothing. The two shapes
above are genuinely different and agree on every verdict.

**Controls.** Instrument 1: positive — the laptop answers without the shim (rc 0); negative —
the identical command fails with it (rc 255); pass-through — the same laptop still reachable
at its LAN address through the shim, so it is not a blanket ssh-killer. Instrument 2: the
identical command reaches the laptop outside the namespace.

**Results.** The five rows below plus the `--deep` decisive pair and the positive-survives
case = eight. ⚠ **A control is only listed where the SAME command could discriminate** —
"exit 4 under a partial fleet" proves nothing unless that command exits 3 under a complete
one. Two rows (`several matches` and `no --tail`) have no same-command control: their
full-fleet behaviour is contract-derived, not measured here. Say so rather than implying a
control for all eight.
`<W-only>` / `<L-only>` are terms matching exactly ONE window, on the workbench and on the
laptop respectively. The literals are not reproduced — they came from other sessions' task
text and this repo is public. Re-derive a pair for your own fleet: any token that
`find-session.py <tok> --live --json` reports with one row, one on each host.

| case | terms | full fleet | laptop DOWN |
|---|---|---|---|
| several live matches, `--tail` | `handoff` | 3 | **3** + `candidate list is INCOMPLETE` |
| zero live matches, `--tail` | `zzqq-…` | **3** | **4** + `NOT 'there is nothing to tail'` |
| one live match, `--tail` | `<W-only>` | 0, no caveat | **0** + `resolved on PARTIAL coverage` |
| **match lives only on the DOWN host**, `--tail` | `<L-only>` | 0, tails the laptop row | **4**, never "not running" |
| zero live matches, **no** `--tail` | `zzqq-…` | 0 | **0** (contract: only `--tail` yields 3/4) |

🔴 **The decisive pair** — `<L-only> --live --deep --tail 3 --json`, identical command and
corpus, only the laptop's reachability differing:

| field | full fleet | laptop DOWN |
|---|---|---|
| exit | 0 | **4** |
| `archive.results[].live_state` | `CLOSED, CLOSED` | **`UNMEASURED, UNMEASURED`** |
| `archive.live_coverage_complete` | `True` | `False` |
| `archive.live_hosts_unreachable` | `[]` | `['laptop']` |
| `tail.coverage_complete` / `refused` | `True` / `False` | `False` / `True` |

The `CLOSED → UNMEASURED` flip is the defect `live_coverage_complete` was written for,
observed live for the first time. `<L-only>` is the strongest single case: the window
**genuinely exists** — the full-fleet control tails it — and with the host down the tool
reports UNMEASURED rather than a measured absence.

**A positive still survives partial coverage.** `<W-only> --live --deep`, laptop down:
`live_state` = `UNMEASURED, LIVE, UNMEASURED, LIVE, …` with `live_coverage_complete: False`.
Finding an id on a host that answered is a measurement whatever the coverage, exactly as
`live_state_of` documents.

🔴 **NEW MEASUREMENT — the headline number degrades 6.7× and the before/after table above
does not say so.** A down laptop costs **exactly 2 ssh calls × `ConnectTimeout=4`** (counted,
not inferred), a fixed cost that does not scale with match count:

| | full fleet | laptop DOWN |
|---|---|---|
| `--live` | 0.73 s | **8.17 s** |
| `--live --deep` | — | **31.98 s** (`--deep` makes a SECOND scan, so it pays the timeout twice) |

Not a defect — `ConnectTimeout=4` is a deliberate `session-manager` choice — but the "1.25 s,
one command" headline is a FULL-FLEET number, and this doc calls the partial fleet "the
COMMON degraded state". Quote the pair, never the 1.25 s alone.

## Open investigations — live diagnosis state

### `test_subsystem_store_api.py` flake — the BACKLOG mechanism is CLOSED; the TIMEOUT one is NOT
🔴 **STATUS 2026-08-30. Read this before the block below, which is preserved as the
diagnosis that led here and is now partly historical.**

- **Mechanism A (backlog) — FIXED, #1062 (`430fe3e1`), closes #1030.** `build_server` never
  set `request_queue_size`, so the accept queue was the stdlib default **5** against a suite
  that fires 8 concurrent appends. Controlled experiment through the real `build_server`,
  200 racers × 3 rounds, varying ONLY the backlog: **5 → 389 `ConnectionResetError`;
  128 → 0; 4096 → 0.** Fixed as `LISTEN_BACKLOG = 128`, a **class** attribute —
  `TCPServer.__init__` calls `listen()` before returning, so an instance assignment moves
  the name and leaves the socket at 5. The regression test counts LANDINGS ON THE SOCKET
  (fills the accept queue while nothing accepts), so it is deterministic and kills that
  wrong fix.
  ⚠ **`net.ipv4.tcp_abort_on_overflow=0` appears to rule this mechanism out, and does not.**
  That sysctl means overflow drops the SYN rather than resetting, so the theory looks dead.
  The failing writers' elapsed times cluster at **1.0 s and 2.2 s** — TCP SYN-retransmit
  intervals — i.e. the reset lands AFTER a retry. Do not re-derive this dead end.
- **Mechanism B (`TimeoutError` out of `socket.py`) — STILL LIVE, and #1023 did NOT hold.**
  Observed 2026-08-29 on `devrc-ci-hrqf4` (PR #1040, a **docs-only** diff):
  `TimeoutError: timed out` at `socket.py:720` in
  `TestTheActorComesFromTheTOKEN.test_a_FORGED_actor_in_the_body_is_DISCARDED`. #1023 raised
  `HANG_TIMEOUT` 15 s → 60 s for exactly this; it recurred at 60 s. **This closes the open
  question in #1030's last comment**, which reported the same test failing on docs-only
  #1034 and said "I did not capture this run's exception — it may be a fourth". It is not a
  fourth; it is B.
- 🔴 **Why B happens: devrc CI is PINNED TO ONE NODE.** The PipelineRun carries
  `nodeSelector: kubernetes.io/hostname=talos-xr6-r7p`. So "the cluster drained" is the
  wrong load measure — during the failure that node was at **91%** CPU while the others sat
  at 22–52%, and 87% at re-trigger. Any further work on B should start there, not at the
  cluster-wide run count.
- **Load-vs-assertion evidence, same tree, local vs Tekton:** the 203-test target went
  3.30 s → **136.90 s (41×) and still PASSED**, while every other target inflated 1.8–2.6×.
  Load inflates EVERY test; a real assertion inflates exactly one.

**The original diagnosis, preserved:**

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
🔴 **RENUMBERED THREE TIMES.** Deploy, then the partial-fleet verification, then ranks 1–3
(#1030/#1029/#1031) all closed, so everything has shifted up by four in total. `claim-work
--list` was checked before each renumber. 🔴 **The slug `claim-work --slug-for` prints for a
rank is POSITIONAL — the same string names a DIFFERENT item after every renumber. Derive it
fresh; never reuse one copied from an older version of this list.** All three
`find-session-live-first-*` claims were released on completion.

**CLOSED since the last revision — do not re-do these:**

| was | issue | landed |
|---|---|---|
| rank 1 | **#1030** store-api listen backlog (mechanism A only) | **#1062** → `430fe3e1` |
| rank 2 | **#1029** three guard-walkability gaps | **#1071** → `3d8caaa1` |
| rank 3 | **#1031** both deferred items | **#1076** (open at time of writing) |

⚠ **#1030 is only HALF closed.** #1062 fixed the backlog mechanism; the `socket.py`
`TimeoutError` mechanism is confirmed still live at `HANG_TIMEOUT = 60 s` — see the
investigation block, which now carries the captured exception and the node-pinning lead.
Whether #1030 stays open or is re-filed against mechanism B is a judgement for whoever picks
it up; the evidence is on the issue.

1. **Inherited from `handoff-find-session-opencode.md`:**
   `scripts/claude-hooks/tests/test_bash_guard.py:294`'s "no catastrophic backtracking" check
   still asserts on wall-clock and flakes under load. Its sibling item (dirty
   `embed_enlarge.js`) is CLOSED — it landed as #1010. Repo: `devrc`.
2. **The store-api flake's mechanism B** — the `socket.py` `TimeoutError` under scheduler
   starvation, which #1023 was meant to fix and did not. Start at the **node pinning**
   (`nodeSelector: talos-xr6-r7p`), not at the cluster-wide run count. Repo: `devrc` +
   `homelab-talos` (the pipeline definition lives there). Closing condition is NOT yet
   written; write one before starting.

🔴 This list is a WORK QUEUE and `claim-work` is its lock — `claim-work --slug-for <this doc>
<rank>`, then `claim-work <slug> --subject "<text>"`, and sweep `gh pr list --state open` too.

## Gotchas / decisions / dead-ends
- 🔴 **The recurring defect in this work was never a logic bug — it was A CLAIM WIDER THAN
  THE THING THAT ENFORCES IT.** Both round-1 blockers, two of round 2's findings, four of
  round 3's six, and both of round 4's 🟡s. Round 5 proved it is *structural*: it added a
  fourth exit-2 cause to both the contract and the shipped doc **without implementing it**
  and got 131/131 green. Each new guard written to pin prose is itself a new prose claim.
  That is why the ladder was stopped rather than run again.
- 🔴 **SUBSTITUTE AS FAR FROM THE CLAIM AS YOU CAN GET — five rounds stubbed the subsystem the
  claim was ABOUT.** The stub was honest work (real subprocess, real JSON, real `main()`, real
  exit code) and still could not see layers 2–3, because it *was* layer 2. Verification did
  not need a suspended laptop; it needed the substitution pushed one layer down, to the
  transport — after which `session-manager`, the report JSON, the coverage predicates and the
  exit codes were all genuine. **Ask which layer your fake occupies and whether the claim
  lives above it.** A netns pushes it to zero layers, at the cost of blacking out the network
  for everything in the process tree.
- 🔴 **RUN THE TWO CHECK DERIVATIONS SEQUENTIALLY — a combined `nix build` produces FALSE
  FAILURES.** `nix build .#checks.x86_64-linux.pytests .#checks.x86_64-linux.nodetests`
  builds both at once, and the nested-`nix` tests inside them contend on the store:
  measured 2026-08-30, `SQLite database … is busy` evaluating `nix/home.nix` plus
  `OperationalError('database is locked')` in dl-router — **2 failures on a tree that passes
  0 one at a time.** Load-dependent, so earlier combined runs were green and looked fine.
  A combined run's GREEN is trustworthy (a contended run fails loudly, it does not fake a
  pass); its **RED is not**, until re-checked sequentially. This cost a near-miss report of
  "#1029 broke the gate".
- 🔴 **A RED required check: read the EXCEPTION, never the test name.** The same test in
  `test_subsystem_store_api.py` failed three different ways in one session, each wanting a
  different response: `ConnectionResetError` (mechanism A — a real defect, now fixed),
  `TimeoutError` at `socket.py` (mechanism B — starvation, legitimately re-triggerable),
  and 45 × `big-lock: Permission denied` (CI infrastructure, nothing to do with the tree).
  A fourth red was neither — it was the PR sitting on a **red `main`**, where re-triggering
  can never help. "Red check → re-trigger" is the reflex that turns a gate into noise.
- ⚠ **`nix build` reports exit 0 for a FAILED build when its output is piped.** `… | tail`
  swallows the status; the run that failed 45 tests printed `NIXBUILD_RC=0`. Read the
  runners' own `RESULT:` lines, which is what the repo already tells you to do — this is the
  documented trap, hit anyway.
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

**PARTIAL-FLEET check — no sudo, no suspend, nothing global mutated.** Instrument 2 is the
better one (it substitutes no address at all); instrument 1 reproduces a *sleeping* laptop's
4 s timeout rather than an instant unreachable, which is the shape that costs 8 s.
```bash
# --- instrument 2: real address, genuinely unroutable inside a netns ---
mkdir -p /tmp/fsv/bin && cat > /tmp/fsv/bin/ssh <<'EOF'
#!/usr/bin/env bash
# -F /dev/null ONLY: openssh refuses a root-owned system ssh_config under the
# namespace's uid map. No address substitution.
exec "$(readlink -f /run/current-system/sw/bin/ssh)" -F /dev/null "$@"
EOF
chmod +x /tmp/fsv/bin/ssh

# POSITIVE CONTROL FIRST — this must REACH the laptop, or the run below proves nothing
ssh -F /dev/null -o BatchMode=yes -o ConnectTimeout=4 zach@10.42.0.100 'echo REACHED'

# then, with the laptop genuinely unreachable:
unshare -rn env PATH=/tmp/fsv/bin:$PATH \
  python3 ~/workspace/devrc/scripts/find-session.py <term> --live --tail 3   # 0 / 3 / 4
# ...and run the SAME command WITHOUT `unshare` every time. A partial-fleet exit code
# means nothing until the full-fleet control shows a DIFFERENT one.
```
🔴 **`unshare -rn` WITHOUT `-F /dev/null` is a trap that looks like it worked**: ssh fails,
`session-manager` reports `reachable: false`, every downstream assertion passes — but the
error is `Bad owner or permissions on …/ssh_config.d/…`, a **config-parse** failure, not a
network one. It is a second sample of "ssh failed", not evidence about an unreachable host.
Read the `error` string before believing the `reachable: false`.

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
