# Handoff: browser-bridge session attribution — 2026-08-20

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

Started as a question — *"find sessions where I call opencode and use the browser skill"* —
which required scanning 1.49M transcript records because `activity.events` could not answer
it: `source='browser-bridge'` rows had an **empty `session` column, 0 of 6,937 over 14 days**.
The goal became making that join answerable from telemetry, and closing what was found
on the way.

## State now

- **Branch:** `main`, clean tree (3 untracked, none deployed). Both hosts converged at the
  time of writing; main has since moved (other sessions are landing PRs continuously).
- **Deploy/verify status: DEPLOYED AND VERIFIED.** The "honest gap" this line used to carry —
  that the fix covered less traffic than the headline implied — is **CLOSED and refuted**: the
  unattributed rows were all synthetic. See the RESOLVED section below.

### Merged and deployed this session

| PR | what | verification actually performed |
|---|---|---|
| #549 `13f4278` | browser-bridge `session` column, tier-prefixed `X-Session-Id` | live: last empty cmd row `19:20:51`, first filled `19:21:12`, unit restarted `19:20:56` |
| #551 `f199f23` | `activate` consent gate (`focus:true`) | live: non-TTY caller → `i3:"withheld"`, screen unmoved, **0** i3 focus events vs a **293** positive control |
| #564 `f95edb5` | `RULES.md` ceiling eviction | 37,448 B, 914 headroom, gate green |
| #570 `d621460` | opencode pin re-keyed 1.18.16 → 1.18.18 | measurements re-derived, four carried forward as explicitly unverified |
| #578 `33f4275` | the `guards-narrower` rule | +38 B net, paid for by eviction in the same commit |
| #580 `c1d30a4` | AGENTS.md cost claims corrected | premise **half-refuted** — nothing cut |
| #583 `975d757` | `opencode-dispatch` preflight + skill | deployed; skill appears in the listing |
| #584 `f33a9f8` | `opencode:` session tier via `shell.env` | **live end-to-end join, below** |

### The join works — measured 2026-08-20 16:44 UTC

From inside a real `opencode run`:

```
$ ~/workspace/devrc/scripts/browser-bridge/browser --print-session-id
opencode:ses_fdff0fcd8ffeA1ebWc9FdhNcJQ
```

and the resulting bridge row:

```
ts:       2026-08-20 16:44:33.725
session:  ses_fdff0ae65ffeOUzSkKJ7Mq23PK
op:       whoami
sess_src: opencode
```

joined against `source='opencode'` on the same id: **1 bridge command ⋈ 1 opencode session.**
An exact key join, not a string match or a time-window correlation.

## RESOLVED 2026-08-20 — the `sess_src='unknown'` producer was THE TEST SUITE

🔴 **The worry this document was built around is refuted. 100% of the `unknown` population is
SYNTHETIC — not one genuinely-real bridge command was ever unattributed.** #549 worked
correctly the whole time; the fail-closed bucket was faithfully reporting that headerless
*test* callers are not attributable. The fear recorded below as "the column is populated for
20% of traffic" was wrong: the real answer is "the column is populated".

**Fixed in #614 (`4548e6b2`), merged + shipped to both hosts.**

### How it was diagnosed (causal, not inferential)

A bracketed control: run one test file, measure the ClickHouse delta.

```
BEFORE unknown=938   ->  pytest test_site_notes.py (70 passed)  ->  AFTER unknown=955
DELTA +17            (reproduced twice; notcivitai.com +1)
```

`+17` matched the production histogram exactly — the unknown rows arrive in runs of exactly
17 or exact multiples (34, 68). Four independent lines agreed:

1. **Domains are fixtures.** The unknown population has only **6** distinct domains —
   `civitai.com`, `example.com`, `""`, `notcivitai.com`, `model-benchmarking.example.test`,
   `x.test`. `notcivitai.com` exists in exactly ONE file in the repo
   (`tests/test_site_notes.py`, a negative control for suffix matching); `.test` is the
   reserved testing TLD. Real `claude`-attributed traffic has a long tail —
   `radio.civitai.com`, `auth-staging.civitaic.com`, `pr-4154.civitaic.com` — **none of which
   ever appears in the unknown set.**
2. **The journal disproves the unit.** 18 unknown rows landed in a 5-minute window in which
   `browser-bridge.service` logged **0** `dispatch` lines. The unit *does* log `op=text` (636
   over 3 days), so this is not a logging blind spot — those rows never traversed the systemd
   server. In-process test servers explain it exactly.
3. **Run structure.** 1,018 of 1,024 rows sit in test-shaped runs; the 6 strays are also
   fixtures (`x.test`, `model-benchmarking.example.test`).
4. **Per-file measurement at `origin/main`** — `test_site_notes.py` **17**, `test_server.py`
   **0-1** (the teardown race, and two observers differ — it is intermittent), the other
   seven **0**.

### Root cause — two halves, both real

- **Module-scoped fixture.** `test_server.py` had an `autouse` fixture redirecting
  `ACTIVITY_SPOOL_DIR` to a tmp dir, with a docstring claiming it protected *"EVERY test's"*
  telemetry. pytest scopes a module-declared fixture to that module; there was no
  `conftest.py`. Guard narrower than its description.
- **A teardown race.** `emit_cmd_event()` runs deliberately OFF the critical path, *after* the
  HTTP response (see server.py's BEST-EFFORT CONTRACT). A handler thread still in flight when
  the test ends emits *after* `monkeypatch` restores `ACTIVITY_SPOOL_DIR` to its ambient
  value — production. This is why `test_server.py` leaked despite owning the fixture.

🔴 **The obvious fix for the second half is INERT.** A session-scoped backstop written the
natural way — `yield` + `mp.undo()` — reopens the identical hole one level up. Measured with a
deterministic probe (a non-daemon thread emitting after a delay, so the interpreter joins it
at exit): no backstop = **1** row to production, backstop **with** `mp.undo()` = **1**
(byte-identical, i.e. inert), backstop **without** undo = **0**. The shipped fixture never
undoes itself, on purpose.

🔴 **Do not read `unknown` as "server.py is stale"** — retracted in #584 and again here; the
deployed copy was byte-identical to the repo throughout.

### Still open — the leak is WIDER than browser-bridge

A full `gate.sh --tier pytest` run still writes **43 real rows** to production, all
`source=tool`, from directories #614 did not touch:

| directory | rows/run | conftest.py |
|---|---|---|
| `scripts/task-spec-drafter/tests` | 23 | absent |
| `scripts/opencode/tests` | 16 | absent |
| `scripts/tests` | ~4 | present, but sets `ACTIVITY_SPOOL_DIR` per-test — some tests, not all |

**13** test directories under `scripts/` have `.py` tests and no `conftest.py`, so the class
recurs. 🔴 **The right fix is NOT another per-directory conftest.** `run-tests.sh:1436-1479`
and `scripts/tests/conftest.py:11-18` already record this exact lesson — a protection
installed from one directory covered 1 of 17 targets while reading as systemic. One line
beside `NOLAUNCH_DIR` — `export ACTIVITY_SPOOL_DIR="$(mktemp -d)"` — covers all 24 targets
including the non-pytest `HOOK_TESTS`/`SHELL_TESTS` no conftest can reach. The `scripts/tests`
row is the nastiest shape: a per-test `monkeypatch.setenv` looks protected on inspection while
every test that forgot it leaks.

~1,024 synthetic rows remain in `activity.events`, identifiable by fixture domain, if a purge
is wanted.

## Next steps (ranked)

1. ~~**Diagnose the `unknown` producer**~~ — **DONE**, see the RESOLVED section above (#614).
   The successor task is the **wider leak**: the one-line `ACTIVITY_SPOOL_DIR` export in
   `run-tests.sh` beside `NOLAUNCH_DIR`, which closes all 24 targets at once (43 rows/run).
2. **Decide `wip/opencode-kubectl-exec-allow`** (`62f0539`, on origin, NOT deployed) — found
   uncommitted in the shared checkout, 2 days stale, not authored by this session. It removes
   `*kubectl*exec*` from opencode's bash `ask` list and from `DANGEROUS_FAMILIES`, lowering the
   ask-count pin 51→50. Net effect: an autonomous opencode agent could `kubectl exec` into a
   pod **without approval**. Coherent and deliberate, but unreviewed.
3. **Open a PR for `wip/resume-open-investigations-are-recall`** (`99aa87a`) — someone's
   uncommitted improvement to the `resume` skill, preserved rather than deployed. It is a good
   change: it says a handoff's open-investigation block is RECALL, not live state. *(Which is
   why this doc's block above carries values and eliminations, not narrative.)*
4. **The historical-claims ledger cannot tell "historical by design" from "stale and
   forgotten"** — `reason` in `HISTORICAL_VERSION_CLAIMS` is never asserted on; it appears only
   in failure-message f-strings, and an **empty** reason string passes. Structurally the same
   blind-spot shape #570 fixed in `_VERSION_RE`.
5. **The gate reports `RESULT: FAIL (exit=1)` while naming no failing target.** Cost two agents
   and this session multiple cycles; every diagnosis required `nix log <drv>` plus grep. The
   highest-traffic tool in the repo omits the one fact you need.
6. Smaller, all recorded and none blocking: the flaky `test_the_throttle_path_carries_both_the_hash_and_the_join_key`
   (2/12 runs, 3s `_wait_events` budget); `_clean_session_field` accepts C1 control chars
   (0x80–0x9F); `browser --tab <parent> emulate` now raises `not_owned_tab` across the
   claude→opencode boundary; opencode `task` subagents get their own id so **routing** (not
   telemetry) fragments within one run.

## Gotchas / decisions / dead-ends

- 🔴 **Every red gate this session was someone else's**, inherited from a base that moved:
  `test_rules_size` (a rule added without its eviction), the browser-bridge floor (twice),
  `test_opencode_engine` (the flake bumped the binary), `test_prune_skill_size` (my own #551
  invalidating a size figure). **None was ever the PR under test.** Gate the merged tree.
- 🔴 **The integration branch found what four branch gates could not.** #583 and #584 had the
  **identical** `.git`-in-the-nix-sandbox bug (`git ls-files` exits 128 at `/build/src`, read
  as "untracked"). The audit caught it in #584; nobody checked the sibling. It was in #583 in
  five places. A per-PR audit structurally cannot see that.
- **`ls -l` on a home-manager symlink reports the LINK's size** (89 B), not the target's
  (42,671 B). Use `stat -Lc %s`. This nearly made me refute a correct measurement.
- **`sed` with `|` as delimiter silently no-ops on a pattern containing `|`.** It reported
  success and changed nothing; only diffing caught it.
- **`-x` and mutation testing do not mix** — first-failure-wins mis-attributed two mutants to
  an unrelated flaky test and produced a **false KILL** that hid a real finding.
- **`nix build` exit status through a pipe is the pipe's**, and zsh has `pipestatus`, not
  `PIPESTATUS`. The authoritative signal is whether the derivation's **output path exists**.
  A `RESULT: PASS` line in the log can coexist with a non-realised derivation (interrupted build).
- **The AGENTS.md "7.7k uncached tokens/request" premise is HALF true.** The tokens are real
  (measured 8,329; the file grew to 43,676 B). The "uncached" half is **refuted**: 330/332
  billed sessions (99.4%) have `tokens_cache_read > 0`, cache reads ~50× cheaper, so the file
  is ~$0.12 of $1.77 (~7%). Caching already worked on 1.18.4 — it was never version-dependent.
  The original `cache.read=0` was true of **browser-agent's own single-shot shape** and got
  generalised. **Not a cost lever; nothing was cut.**
- **Do not adopt browser-agent's isolated `OPENCODE_CONFIG_DIR` as a general fix** — it also
  drops `plugin/guard.js`, the enforcement layer. Safe there (that agent has no shell), unsafe
  generally.

## How to verify

```bash
# 1. the Claude-side attribution and the opencode-side join, in one query
CH=http://192.168.50.94:30123
# 🔴 --input-type yaml is REQUIRED: process substitution gives sops a /proc/self/fd/N path
# with no file extension, so it cannot infer the format and dies with
# "Could not unmarshal input data: invalid character 'a' looking for beginning of value".
git -C ~/workspace/homelab-talos show \
  origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml > /tmp/act.enc.yaml
RPW=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d \
  --input-type yaml --extract '["stringData"]["reader-password"]' /tmp/act.enc.yaml)
curl -s --user "activity_reader:$RPW" --data-binary "
  SELECT JSONExtractString(payload,'sess_src') s, count(), countIf(session!='')
  FROM activity.events WHERE source='browser-bridge' AND kind='cmd'
    AND ts > now() - INTERVAL 24 HOUR GROUP BY s FORMAT TSV" "$CH/"

# 2. the focus gate — from a NON-TTY caller this must print withheld and NOT move the screen
~/workspace/devrc/scripts/browser-bridge/browser --instance work activate | \
  python3 -c 'import json,sys; d=json.load(sys.stdin)["result"]["data"]; print(d["i3"], d.get("i3_detail"))'
# expect: withheld not_requested

# 3. the opencode tier, end to end (~$0.02, one deepseek turn)
opencode run --dir /tmp/ocv -m openrouter/deepseek/deepseek-v4-flash \
  'Run exactly: ~/workspace/devrc/scripts/browser-bridge/browser --print-session-id'
# expect: opencode:ses_...   (NOT claude:... — that would mean the leak guard regressed)
```

🔴 **Merged ≠ deployed.** `server.py` and the plugin are `home.file` copies needing
`scripts/ship.sh`; the `browser` CLI and the `opencode` SKILL.md are `mkOutOfStoreSymlink`s
and apply immediately. `readlink -f` is the arbiter, never a diff.
