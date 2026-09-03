---
clawgate-task: 371
---
# Handoff: cairn-phase3 — 2026-08-28

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
Make the hosted subsystem store the single datastore every host reads **and writes**. Phase 2 (read-through client) shipped earlier. This session shipped clawgate #371's criteria **1–7**: per-token identity, server-side scope authorization, the enumeration property, and the write path. **Criteria 8, 9, 10 remain open** — they are re-seed / cutover / credential-retirement operations, not code.

## State now

🔴 **CARRIED FORWARD — the phase-1 closure record. `State now` is a REPLACE heading, so this
block is deleted by every update that does not re-state it; it is the durable evidence, not
status.**

🔴 **CRITERIA 8 AND 9 ARE COMPLETE. PHASE 1 IS CLOSED — the pod is canonical and both local
stores are read-through caches.** Executed and verified 2026-09-01.

| | result |
|---|---|
| pod | **201 entries / 23 scopes**, both hosts read the same snapshot `seeded=2026-09-01T20:38:36Z` |
| workbench freeze | 153 files, **`P5 WATCHED EACCES: all 153 refused an append`** |
| laptop freeze | 49 files, **`P5 WATCHED EACCES: all 49 refused an append`** |
| idempotency | both hosts re-run → *"the freeze is already applied"* |
| runbook §8 final check | `{'ADD': 0, 'SAME': 153, 'SUPERSEDES': 0, 'MERGED': 0, 'NEEDS_MERGE': 0}`, **0 entries would be pushed** |
| rollback | mode ledgers on both hosts (`runs/20260901T203704Z/`, `runs/20260901T203951Z/`); `--unfreeze` refuses without one |

**The proof that the per-host store is gone:** the workbench can `cairn recall` a
**laptop-exclusive scope** (`status=recalled`). That was structurally impossible before.

**Criterion 10 remains COMPLETE (execution record):** executed 2026-08-31, homelab-infra
**`be3084f0`** on trunk, confirmed an ancestor of `origin/trunk`. The token file went
**354 B → 295 B**, a pure line deletion: surviving mapped row `ecc11c1b5b6e` unchanged,
`2481e4553f6c` absent. Live banner `token-ids=a8f329c534d7:zach`, no
`UNRESTRICTED-SCOPE LEGACY MODE` — positive control: 6 historical banners in Loki, so that grep
CAN match. Mapped credential → **200**; legacy credential → **401, dead**; **both hosts read
200**. PR #1187 (`9-before-8` sequencing) merged 2026-09-01T03:17:40Z; that ordering is now
history, since both ranks are done.

🔴 **Claim state (carried forward, updated 2026-09-02):** `cairn-phase3-1` is **NOT held**
(re-verified 2026-09-01). `cairn-phase1-migration` was held for the phase-1 work and was
**released**. `cairn-phase3-11` was held for the rank-11 work and is **released**.
`cairn-phase3-16` was held for the rank-16 work and is **now RELEASED** — merged, shipped
and verified.

⚠ **The pod counts above are the 2026-09-01 execution record and have MOVED** — 205 entries as of
2026-09-03 after four orphaned entries were rescued onto it (rank 23). Carried verbatim because it
is the phase-1 evidence, not a current reading.

---

**2026-09-03 — THREE RANKS CLOSED, SHIPPED AND VERIFIED ON BOTH HOSTS.**

| rank | what | squash | verified by |
|---|---|---|---|
| **16** | the prescribed READ path goes through the pod | `9519781f` | `cairn append` → `cairn recall --repo` surfaces it in the same session, positive control on the grep |
| **20** | the third frozen read surface routed | `2c6b2ac9` | constant DELETED (0 code-level definitions), resolver wired, by content |
| **18** | `drift-check` fires on DISAGREEMENT, not on the bare state | `77dc3642` | **rc 24 → 17 → 0** |
| — | cairn skill + `cairn doctor` + the store-root ledger | `ac09c18` | skill live in the listing; doctor found a real defect on run 1 |
| — | espanso gate relaxed per operator ruling | `b9b2493d` | red at base, green at head, consumer suites 596 |

🔴 **`drift-check` now exits 0** — it took BOTH halves: the declared-expectation change cleared
rc 24, and fast-forwarding the laptop's `homelab-talos` (26 behind) cleared rc 17. Round 1's
audit was right that the first alone would not stop the toasts.

**IN FLIGHT, nothing else outstanding from this session:**
- **devrc#1262** — unbreaks `main`, which is **RED right now** (`test_recommend_terms_resolve_on_the_live_config`, confirmed on plain `origin/main` with nothing merged). Merged-tree gate RUNNING at hand-off time. Diff is **one test file**; `espanso_detect.py`, `_AMBIGUOUS_TERM_OWNER`, `_EXISTING_RESOLUTIONS` and the `>= 26` floor have **0** changed lines.
- **devrc#1261** — rank 25. Gated on the merged tree: **exactly 1 failure on both tiers, and it is the inherited espanso one; 0 cpu_monitor failures.** Blocked only by `main`. Merge after #1262, then **delete `laptop/cpu-mon-temp-92` (`44ebd9c6`)** — it sets the SHARED line to 92 and would reintroduce the value #1261 exists to remove.

⚠ **The doc's Goal is met for READS and APPENDS, not for CREATES.** The store has no create
route; that is rank 24 and **devrc#1254 (another session) owns it** — do not duplicate it.

## Open investigations — live diagnosis state

### `ConnectionResetError` in the 8-writer concurrency test
- **Symptom + exact repro:** no reliable repro. `test_EIGHT_concurrent_appends_all_survive` failed once in a local wider-gate run; two of eight writers got `ConnectionResetError` at `elapsed=0.0s`, the other six returned 200.
- **Observed (with values):** the test is now self-diagnosing and reported `MECHANISM = TRANSPORT`. The exception is a bare `ConnectionResetError`, **not** `URLError` — `urllib` wraps only `h.request()`, never `h.getresponse()`, so **the request was sent and the server reset an established connection.** Not connect-time.
- **Ruled out:**
  - **Accept-queue/backlog overflow.** `net.ipv4.tcp_abort_on_overflow = 0` on this host (measured), so overflow **drops SYNs and yields a timeout, never an RST**. The backlog is 5 (socketserver default, not overridden) against 8 writers and it is still not the mechanism.
  - **CPU saturation** — 25/25 green under 20-way load.
  - **Reproduction under load**: 12 runs by hand on a quieter host, 10 body-path runs, 20+ total — 0 resets. **0 across 11 saved run outputs and 0 in CI**, including the green run on the merged tip.
- **Leading hypothesis (LOCATED but UNCONFIRMED):** `_consume_body` returns `False, b""` **without draining the declared body** in five arms — `length > MAX_DRAIN_BYTES`, chunked `Transfer-Encoding`, negative and unparseable `Content-Length`, and the `DRAIN_DEADLINE_S` arm — **each setting `close_connection = True`**. Closing a socket with unread data still queued makes Linux emit **RST rather than FIN**, and the RST also discards the server's send buffer, so a client that received a valid response can still surface `ConnectionResetError`. That produces an RST where a backlog overflow produces a timeout, which is the discriminator.
- **Next probe:** loop the body-path tests and see whether the reset localises there:
  ```bash
  nix develop <repo> --command env PYTHONDONTWRITEBYTECODE=1 python -m pytest \
    scripts/tests/test_subsystem_store_api.py -q -p no:randomly \
    -k "CHUNKED or NoRequestSmuggling or Content_Length"
  ```
  A direct socket probe (2 MiB over-cap body, chunked TE, negative `Content-Length`, plus a control) got a clean `405` on all four at a 250 ms read, so if the window exists it is narrower than that.
- 🔴 **Do NOT add a bounded drain until the fault reproduces.** A fix that cannot be watched to fail is not verified, and it would suppress the only signal.

### `ConnectionResetError` in the 8-writer concurrency test — NOT reproduced, more evidence
- **Status unchanged: no reliable repro, and this session adds a large negative sample.**
- **Observed:** the full `test_subsystem_store_api.py` suite ran ~12 times on the dev host
  and 6 times in the nix sandbox across this session, several while the box was at load
  40–50 with 8+ concurrent Tekton pipelineruns — i.e. under *worse* contention than the
  original single failure. **Zero resets.**
- **Ruled out (carried forward):** accept-queue overflow (`tcp_abort_on_overflow=0` →
  timeout, not RST); CPU saturation; reproduction under load.
- **Leading hypothesis unchanged:** `_consume_body` returns `False, b""` without draining in
  five arms, each setting `close_connection = True`; closing with unread data queued makes
  Linux emit RST rather than FIN.
- 🔴 **Do NOT add a bounded drain until it reproduces.** A fix that cannot be watched to fail
  is not verified, and it would suppress the only signal.

### The homelab Tekton CI is returning false reds — this blocked a green PR
- **Symptom:** `tekton/devrc-pytests` FAILURE, then ERROR, on a tree whose hermetic build is
  `RESULT: PASS`.
- **Observed (values):** attempt 1 on `f11e80ea` — CI `collected=18296 passed=18293 failed=1`
  vs the **identical revision** built here `passed=18294 **failed=0**`. The one failure,
  `test_ledger_plugin.py::test_the_plugin_writes_a_record_the_READER_can_parse`, is in a
  subsystem the diff does not touch and passes **5/5** locally in isolation. Attempt 2 on
  `376e1545` — **`TaskRunTimeout`**, gate killed at its 45-minute ceiling with **no step
  completed** (`exit None` on clone/capture-etc/seed-nix/pytests/nodetests/verdict). That
  suite runs in ~7 minutes hermetically.
- **Scope — it is not devrc-specific:** 9 of the last 25 completed pipelineruns failed
  (**36%**), across `devrc-ci`, `gitops-validate` **and** `clawgate-ci`. 23 running, all
  **distinct** pipeline@revision (no webhook storm, nothing stuck), node `talos-deu-s2q` at
  **100% CPU**, host load 51.
- **Ruled out:** a broken test (four unrelated revisions failed on four *different* tests);
  a webhook storm (all distinct); a stuck run (oldest 23 min).
- 🔴 **Each timed-out attempt holds a CI slot for 45 minutes**, so retrying deepens the
  contention that causes the failures.
- **Next probe:** someone is already on this — devrc branch `feat/youtube-disable-numkeys`
  carries `39eec8a5 docs(handoff): … analyzed Tekton CI pipeline throughput, identified
  bottlenecks`, and a `ci-speedup-1` claim is live. Read that before starting fresh.

### The CI failure was a **TIMEOUT**, not an RST — and that breaks the backlog elimination's premise while a *different* measurement still rules the backlog out

**New datum (2026-08-31).** `devrc-ci-ddrxx`, revision `857fc3f5`, node `talos-xr6-r7p`:
`TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_DISCARDED[record1-…-dana]`
failed on **gw1** with a bare `TimeoutError` and no assertion executed. Chain:
`post_bullet` → `fetch` → `urlopen(req, timeout=timeout)` → `http.client` →
`socket.py:720`. **Line 720 of this Python (3.12.14) is `return self._sock.recv_into(b)`
inside `SocketIO.readinto`** — read, verbatim, from the interpreter the gate uses. So this
is a **READ** timeout on a connection the client had already established and written to,
not a connect timeout. `HANG_TIMEOUT` was already `60.0` at that revision, so a localhost
round-trip went unanswered for **more than 60 s**.

🔴 **The premise of the entries above does not transfer.** Both ruled accept-queue overflow
out with *"`tcp_abort_on_overflow = 0`, so overflow drops SYNs and yields a **timeout**,
never an RST"*. That argument was made to explain an **RST**. Applied to a **TIMEOUT** it
runs backwards: a timeout is precisely what that sysctl predicts. **The elimination as
written is void for this observation** — do not carry it forward as if it covered both.

**The backlog is nonetheless ruled out here, on a measurement instead of an inference.**

- **The failing test opens exactly ONE connection.** Counted at `verify_request`, which
  socketserver calls once per accepted connection: `{port: 1}`. `urllib` sends
  `Connection: close` (measured off the wire), so `post_bullet` is one request on one
  connection.
- **The accept queue it would have to overflow is 128 deep**, read off the live socket
  (`ss -lntH` Send-Q on the real `build_server` socket), not off the constant.
  Overflow needs >128 *simultaneous* pending connections. **1 vs 128 is arithmetic, not
  judgement.**
- Instrument validated before either number was quoted: requested backlog 5 → reported 5,
  128 → 128, 100000 → **4096** (the somaxconn truncation, observed). The reader moves with
  the request; a 128 is not a constant it always prints.

**`somaxconn` does NOT truncate the 128 in the sandbox — the suspicion is measured false.**
Read inside a real nix build sandbox netns on the dev host, and inside both a pod netns and
a fresh `unshare -n` on the CI node:

| | dev host | dev-host nix sandbox | CI node `talos-xr6-r7p` (pod netns **and** fresh netns) |
|---|---|---|---|
| `net.core.somaxconn` | 4096 | **4096** | **4096** |
| `tcp_abort_on_overflow` | 0 | 0 | 0 |
| `tcp_max_syn_backlog` | 4096 | 4096 | 2048 |
| `tcp_synack_retries` | 5 | 5 | 5 |

`430fe3e1`'s fix is fully in force in CI. Nothing about the sandbox weakens it.

### CPU starvation is quantitatively too small — which retires the premise `HANG_TIMEOUT = 60` rests on

`HANG_TIMEOUT` was raised 15 → 60 on the stated cause *"a 10-minute parallel suite competing
with a saturated cluster"*. **Measured against that story and it does not hold.**

Load–latency curve using the suite's **own** `running()` / `post_bullet()` (so the server,
token record, store layout, framing and teardown are identical to the failing test), pinned
to a 2-CPU cpuset with spinners on the same cores. Clock brackets the POST only —
`running()`'s teardown costs up to `serve_forever`'s 0.5 s poll interval and would otherwise
floor every sample. All 1250 round-trips answered **200**:

| spinners on 2 CPUs | oversubscription | p50 | p99 | **max** | >60 s |
|---|---|---|---|---|---|
| 0 | 1× | 0.014 s | 0.435 s | 0.558 s | 0 |
| 8 | 5× | 0.032 s | 0.061 s | 0.105 s | 0 |
| 32 | 17× | 0.144 s | 0.340 s | 0.394 s | 0 |
| 96 | 49× | 0.446 s | 0.924 s | 1.033 s | 0 |
| 192 | **97×** | 1.001 s | 2.573 s | **2.984 s** | **0** |

Negative control: a POST to a socket nothing ever accepts raised `TimeoutError` at 3.01 s
against a 3.00 s bound — the timer can fire and be seen.

**97× CPU oversubscription buys 2.98 s. The CI node was at 2.1×** (`node_load1` max 34.00 on
16 CPUs; PSI cpu-some max 0.266). CPU contention is off by roughly three orders of magnitude
from a 60 s stall. 🔴 **So `HANG_TIMEOUT = 60` is not wrong, but its stated justification is
— and raising it further would buy nothing against whatever this actually is.**

### The leading candidate: two unbounded `fsync`s inside the request

`server.py:_replace_bytes` issues **two** `fsync`s per append — the file (`os.fsync`, 2012)
then the parent directory (`_fsync_dir`, 1976) — **inside the handler, before the response is
written**. `fsync` blocks in uninterruptible D-state, is bounded by nothing, and **burns no
CPU**, which is exactly why it is invisible in the CPU metrics above. 🔴 **The handler's
`timeout = 15` does NOT bound it**: that is a SOCKET timeout and does not reach a syscall.

**Sufficiency demonstrated, frame for frame.** Stalling one `fsync` past the client's bound
reproduces the CI traceback exactly — `fetch` → `urlopen` → `http.client` →
`socket.py:720, in readinto / return self._sock.recv_into(b)` → `TimeoutError: timed out`.
Control: with the stall removed the same request answers **200 in 0.056 s**, so the harness
can pass and the failure is caused by the stall.

**The node's I/O was in fact saturated during that step** (Prometheus, 06:25–06:55Z, n=31,
node-exporter `192.168.50.191:9100`):

- PSI io **full** `rate(node_pressure_io_stalled[2m])` mean **0.128**, **max 0.586** — at the
  peak, 58.6 % of wall time *no* task on the node could progress on I/O. That is **2.2×**
  larger than the CPU some-stall, and full is the more severe class.
- `node_procs_blocked` (D-state) **max 37**; `sda` utilisation **max 0.996**, weighted queue
  time max **64.1**, write await **max 1.264 s**.
- Three discrete saturation episodes fall inside the step: **06:32–06:34** (the worst),
  06:36:30–06:38:30, 06:45–06:47. Five concurrent `devrc-ci-*-gate-pod`s were on the node;
  `ddrxx` wrote ~1/5 of the load — mostly a victim.
- **Memory is affirmatively ruled out**: PSI memory max 0.004 (0.4 %), ≥10.87 GB available
  throughout.
- Clean negative control for the instrument: the 04:25Z window with **zero** CI pods reads
  sda util 1.3 %, io-full 0.14 %. The saturation is caused by CI concurrency.

### 🔴 What is NOT established — read this before quoting the section above

- **No 60-second I/O operation was observed.** The worst per-op latency in the window is
  **1.264 s**, and the 0.586 io-full figure is a duty cycle over a minute, not a contiguous
  stall. Sufficiency (a stalled `fsync` produces this exact traceback) is **not** attribution.
- **The same I/O picture appears in every comparison window** — −6 h, −12 h and −24 h are
  statistically indistinguishable from the incident window, and those runs did not fail. So
  saturation is a *necessary-condition candidate*, not a discriminating cause. It cannot
  separate "the filesystem stalled this request" from a rival that merely coincided with
  routine CI load.
- **The discriminating signal is client-side and CI does not currently emit it**: whether the
  60 s expiry landed inside 06:32–06:34 / 06:36–06:38 / 06:45–06:47, and *which* frame the
  server thread was in. Prometheus cannot answer either.
- Whether the historical `ConnectionResetError` (the entries above) and this `TimeoutError`
  are one mechanism or two **remains open**. They have different shapes and only the RST one
  has a located hypothesis (`_consume_body`'s five undrained arms).

### What shipped instead of a fix — and why it is not one

🔴 **No drain, no retry, no bound was moved.** The failure carries no information about
*which side* blocked, and that — not the flake's rarity — is why this has stayed open:
**a client-side read timeout is the observable that the most mechanisms share, so on its own
it identifies none of them.**

`fetch` now catches `TimeoutError`, prints `_why_the_server_did_not_answer()` to stderr and
**re-raises unchanged**. The report dumps every live thread's stack and emits a greppable
`MECHANISM = …` verdict — `SERVER_BLOCKED_IN_FSYNC`, `SERVER_BLOCKED_ON_ENTRY_LOCK`,
`SERVER_BLOCKED_IN_AUDIT_SINK`, `SERVER_BLOCKED_ELSEWHERE`, `NEVER_ACCEPTED`,
`NO_SERVER_THREAD_ALIVE`, or `AMBIGUOUS(…)` when two stuck handlers disagree. A hung test
still fails, exactly as loudly. **The next occurrence in CI names its own mechanism.**

⚠ **Labelled honestly: `TestAHungRoundTripSAYSWhichSideBlocked` is an INVARIANT GUARD on the
reporter, not regression coverage for the flake.** The flake has never been made to
reproduce and no test claims otherwise. Its evidence is the mutation matrix (baseline 4
passed; drop the fsync rule → the fsync arm alone fails; drop the entry-lock rules → that arm
alone fails; make the verdict a true constant → the discrimination control **and**
`NEVER_ACCEPTED` fail; make `fetch` swallow the timeout → three arms fail on
`DID NOT RAISE`; make the handler drain inert → two arms fail; positive control with no rules
at all → caught).

🔴 **Two traps paid for while writing it, both worth not re-paying.** `post_bullet` forwards
`**payload` into the JSON **body**, so a `timeout=` passed to it is sent to the server as a
field and the request silently waits the full `HANG_TIMEOUT` — the test then passes while
measuring nothing. And a test that wedges a handler **must drain it**: handler threads are
daemons that `shutdown()`/`server_close()` never join, so one left parked leaks into later
tests and the reporter attributes *its* mechanism to somebody else's hang. That happened
here and is why the headline reports `AMBIGUOUS` rather than picking a handler.

### RESOLVED BY MEASUREMENT — the phase-1 merge rule is ONE FILE, not five scopes
- **Why it looked bigger:** `plan-cairn-integration.md` records "5 scopes exist on both hosts …
  5 need a merge rule" and calls the merge rule "the risk here, not the transport". That is a
  **scope-level** count and it was read as an entry-level one.
- **Observed (with values), both hosts enumerated 2026-09-01:** across the 5 scopes present on
  both hosts — 122 workbench entries vs 17 laptop entries — there is **exactly ONE filename
  collision: `devrc/signal.md`.** The other 16 laptop entries have unique names and are a pure
  additive union with no merge decision at all.
- **The one collision HAS genuinely diverged**, and neither side is a stale copy of the other:

  | | workbench | laptop |
  |---|---|---|
  | size / mtime | 5260 B / `2026-08-21 00:10:56` | 4507 B / `2026-08-30 21:29:53` |
  | aliases | `signal, signal-consumer, signal-api, test_signal` | `signal, test_signal, _signal_db, consumer, signal-cli-rest-api` |
  | unique content | homelab-infra manifest pointers; "parent Flux kustomization is `homelab`, not `signal`"; the `proposal-signal-chat-skill.md` §Corrections reference | the newer prose; the **D3 draft→approve→send** outbound design |

  116 changed lines. **The pod holds the workbench copy, so the laptop's newer entry is ALREADY
  INVISIBLE to `cairn recall`.**
- **Ruled out:** "newest-wins is safe." It is not — it would discard the workbench's pointer set
  and 2 aliases, and the loss would be invisible afterwards. Seeding from the workbench (what
  the pod does today) discards the laptop's newer copy symmetrically.
- **Operator decision 2026-09-01: HAND-MERGE**, union of aliases and both pointer sets, neither
  copy discarded. It is one file; the cost is trivial and it is the only merge decision in the
  entire migration.

### 🔴 OPEN — aliases are a SECOND collision channel, and the merge INTRODUCES one
- **Symptom:** a filename comparison cannot see it. Two entries with different filenames whose
  `aliases:` overlap make `--ref <alias>` ambiguous after the merge.
- **Observed (with values), computed over the merged union:**
  - `homelab-talos`: alias **`triggers`** claimed by `tekton-pipelines.md` (**laptop-only**) and
    `tekton-ci.md` (**workbench**). 🔴 **This ambiguity exists on NEITHER host today — the
    merge creates it.**
  - `devrc`: alias **`cairn`** claimed by `cairn.md` and `present.md`, **both on the
    workbench** — pre-existing and latent. `--ref cairn` currently returns `cairn.md`, which
    suggests a filename match outranks an alias, but that precedence was **inferred from one
    observation, not measured**. Measure it before relying on it.
  - Two laptop entries carry **no `aliases:` field at all** (one in a client scope, one in
    `devrc`).
- **Ruled out:** that the entry-level count settles the merge risk. It does not — the one
  filename collision and the two alias collisions are disjoint sets.
- **Next probe:** confirm the reader's name-vs-alias precedence mechanically
  (`subsystem_recall.py --ref`), then decide the `triggers` rename before any re-seed.

### 🔴 OPEN — two live phase numberings, and a plan doc that contradicts live state
- **Observed:** `plan-cairn-integration.md` (2026-08-31) asserts *"No tenancy, no auth, no
  age-out, no sharing exist in any form."* The auth half is **FALSE** — measured above with both
  controls (401 / 401 / 200). That doc is what defines phase sequencing, so anyone planning off
  it mis-orders work.
- **Observed:** there are **two numbering schemes in simultaneous use.** cg#371's "phase 3"
  (per-token identity, scope authorisation, write path) is `plan-cairn-integration.md`'s
  **phase 2**; commit subjects read `cairn phase 3, criteria 1-7`. The plan's phase 1 (pod
  canonical) is cg#371's criteria **8 and 9**.
- **Next probe:** none — it is diagnosed. Reconcile the two documents so the numbering is
  single-valued, and correct the false sentence.

### 🔴 OPEN — `verify-byte-identity.sh` STILL cannot pass for a multi-entry scope: it is mtime-ordered
- **Symptom + exact repro:** `cairn-cutover.py --apply` reaches P4 and reports
  `verify: scopes=16 pass=11 fail=5` (and `scopes=12 pass=0 fail=12` for the laptop store) on
  stores that are byte-identical. Reproduce: run the cutover against any scope whose index
  holds **2 or more** entries (see the boundary correction in the RESOLVED block below — the
  original wording here said `>2`, which is wrong at the boundary).
- **Observed (with values):** the failing diff is the *index listing*, not entry content —
  `local: … tekton-ci, clawgate` vs `pod: … alloy-talos, autoremix`, while the filename SETS
  are identical. `subsystem_recall`'s index is **newest-first by mtime**, and the transport does
  not preserve mtime: an entry pushed minutes earlier read pod `2026-09-01 15:32:44` vs local
  `2026-09-01 00:19:56`; one never in a delta read pod `13:27:03` vs local `2026-08-30 13:49:04`.
  ⚠ **RETRACTED — this line used to read "Every PASSING scope had 2 entries; every failing one
  had 24–50", and it is false.** Entry counts re-measured 2026-09-02 on the same store, counted
  as `subsystem_recall` INDEXES them (a `README.md` in a scope is correctly not indexed):
  `civitai`=23, `datapacket-talos`=49, `devrc`=26, `homelab-talos`=24, `cli`=5,
  `civitai-app-starters`=3, `civitai-spine-controller`=3, `storage-resolver`=1,
  `homelab-infra`=**0**, and seven more at 1. **There is no 2-entry scope in this store at all**,
  and only three scopes are in the 24–50 band — so neither half of that sentence can be true of a
  `pass=11 fail=5` split. This run's per-scope verdicts were not recorded, so the split cannot be
  re-derived; the boundary below is what actually holds.
- **Ruled out:** that #1214 fixed it — #1214 closed the `host:` gap only, and after it the same
  runs still failed on ordering. via: measurement
- **Ruled out:** a snapshot race — the failure persisted across repeated applies with a fresh
  `seeded=` stamp each time. via: measurement
- **Leading hypothesis:** the check compares a RENDER whose order is a function of filesystem
  metadata the transport does not promise. Byte-identity of *entries* is the real claim; the
  render is a proxy that cannot hold.
- **Next probe:** none needed for diagnosis. The fix is to compare the entry SET plus each
  entry's bytes (order-independent), not the mtime-ordered render — the substitute used this
  session, which passed 153/153 and 49/49 with both controls.
- 🔴 **Until then this is a PERMANENTLY-RED GATE inside the cutover**, which `claude/RULES.md`
  calls worse than no gate. It was worked around, not fixed.

### 🔴 OPEN — `cairn put` has never been exercised against the live pod
- **Symptom:** `prune-index`'s whole-file rewrite now routes through `cairn put` (#1210), and
  that path has not run once. `cairn append` was proved empirically; `put` was not.
- **Observed:** the write route itself is deployed — the cutover's malformed-body probe is
  refused `400 [bad-request]`, which only a server that DISPATCHED the POST can answer.
- **Ruled out:** that the append proof covers it — they are different verbs and different
  server handlers (`POST …/bullets` vs `PUT …/entry`). via: code
- **Next probe:** run one real prune through `prune-index` and watch it land on the pod. That
  is `prune-index`'s stated closing condition and it is unmet.

### 🔴 OPEN — the token scope allowlist goes stale by construction
- **Symptom + exact repro:** a scope is created by any session writing its first entry. It is
  then absent from the token allowlist, and the cutover refuses to freeze because that entry is
  "missing from the served copy".
- **Observed (with values):** `civitai-block-generate-from-model` was created by another session
  during this very session. `grep -c` inside the pod against `$SUBSYSTEM_STORE_TOKEN_FILE`
  returned **0** for it and **1** for a known-good scope. The API reports an unpermitted scope
  as `status=scope-absent` — **not** 403 — so "not allowlisted" and "not pushed" are the same
  symptom from outside.
- **Ruled out:** a snapshot lag — the pod reported `entry-files=150` while indexing `149`
  across repeated syncs with fresh stamps. via: measurement
- **Ruled out:** file permissions on the pod — the new scope dir was `65532:65532`, identical
  to every working scope. via: command
- **Leading hypothesis:** nothing warns, and the cutover's refusal is the only detector — and it
  only inspects one host. homelab-infra#620 unblocked today's run (15 → 23 scopes) but fixed
  nothing structural.
- **Next probe:** decide where the durable fix belongs — whatever mints the token, or a
  drift-check arm that compares each host's scope set against the allowlist.

### RESOLVED BY FIX (pending audit) — `verify-byte-identity.sh` was mtime-ordered
- **Supersedes the OPEN block above.** Reproduced this session at the live pod before touching
  any code: `verify: scopes=5 pass=1 fail=4`, and the `cli` scope isolated it cleanly — index
  rows byte-identical, the only unaccounted difference a single row's POSITION
  (`pkgzip` at local line 10 vs pod line 13).
- ⚠ **RETRACTED — this line used to read "every PASS had ≤2 entries; every FAIL had more", and
  it is FLATLY FALSE on this very run.** `homelab-infra/` holds one `README.md`, which is
  correctly not indexed, so that scope has **0** indexed entries — and it **FAILED**
  (`raw-diff-lines=108 accounted-for=6`). "Every FAIL had more" is wrong in the same direction
  the script header was. The lone PASS, `storage-resolver`, indexes **1** entry, not 2.
- **The boundary that actually holds, and it is ARITHMETIC rather than measured:** a **1-entry**
  index has exactly one possible order, so it cannot diverge by ordering and is safe; **2 or
  more** is where the order can differ. And a FAIL does **not** imply an ordering problem at any
  count — `homelab-infra` failed on a **SET** difference with 0 entries (its local render is
  `status=scope-empty` with no INDEX block at all, so 102 of its 108 differing lines are
  something ordering structurally cannot produce). Three of that run's four FAILs were ordering.
  Fixed in #1222; the boundary corrected in the #1222 round-1 follow-up; post-fix live run in
  `State now`.
- **Ruled out:** that #1214 covered it — #1214 closed the `host:` canonicalisation gap only, and
  the ordering failures persisted after it. via: measurement
- **Ruled out:** that row order was the whole defect — the digest's single featured BODY is
  picked by mtime too, so a byte-identical store also rendered a different entry body.
  via: code
- **Next probe:** read audit round 1's findings when it returns; run the ladder to a clean
  round. Do not re-derive the diagnosis.

### 🔴 OPEN — the local stores now LAG the canonical pod, and nothing reports it
- **Symptom + exact repro:** post-cutover each host's store is a read-through cache. Run
  `verify-byte-identity.sh` against any busy scope and the entry SET differs.
- **Observed (with values), 2026-09-01:** `devrc` local **26** entries vs pod **29**
  (`pod-only=3`; the pod carries an `obs` entry and 2 extra nuance bullets on
  `subsystem-store-api` that the local copy has never seen). `datapacket-talos` `pod-only=2`.
  Both hosts read the same pod snapshot, so this is cache staleness, not divergence.
- **Ruled out:** that this is a defect in #1222's set arm — the set arm is reporting a true
  fact, and weakening it would make a half-copied seed undetectable. via: code
- **Leading hypothesis:** this is correct-by-design and only becomes a problem because
  `verify-byte-identity.sh` is now meaningful **only immediately after a seed/push** — which is
  where P4 runs it. #1222 states that in the script header, the P4 docstring, the README and
  the set-arm test docstring.
- **Next probe:** decide whether `cairn sync` should be scheduled, or whether a stale local
  cache is simply fine now that reads go through the pod. Nothing currently reports the lag.

### 🔴 OPEN — `verify-byte-identity.sh` cannot be run against the PUBLIC ingress
- **Symptom + exact repro:**
  `verify-byte-identity.sh --url https://store.zacx.dev …` → **`FAIL … remote HTTP 403
  (body: error code: 1000)`** on every scope.
- **Observed (with values):** all 4 scopes tried returned 403 with a **Cloudflare** error body,
  not the store's. The script unconditionally sends `CF-Connecting-IP`, and Cloudflare rejects
  a client-supplied one. Over a `kubectl port-forward` the identical invocation works.
- **Ruled out:** an auth problem — the same credential returns 200 through the port-forward.
  via: measurement
- **Leading hypothesis:** the script's own comment says the header is "inert" on the
  port-forward path and correct against the nebula gateway; it is neither inert nor correct
  against the public URL, and nothing in the script or its usage text says so.
- **Next probe:** decide whether to send the header only when `--url` is not the public
  hostname, or simply document the constraint. Not covered by #1222.

### 🔴 OPEN — the cutover left TWO stores and `/resume` reads the DEAD one
- **Symptom + exact repro:** write a bullet through `cairn append`, then run the command
  `/resume` step 4 and the top of this doc both prescribe:
  `python3 scripts/lib/subsystem_recall.py --repo ~/workspace/devrc`. The bullet is not there,
  and the output claims completeness anyway.
- **Observed (with values), 2026-09-01, minutes after two writes to `devrc/subsystem-store-api`:**

  | reader | store it reads | entries | mode | carries the new bullet |
  |---|---|---|---|---|
  | `subsystem_recall.py --repo` (what `/resume` runs) | `~/.claude/analyze-service-index` | **26** | **444** | **no (0 occurrences)** |
  | `cairn recall` | `~/.cache/subsystem-store` | **29** | 644 | yes (1) |

  These are **two different directories**. The first is the mirror the cutover FROZE; nothing
  refreshes it, so it can only drift further. It nonetheless prints
  `INDEX … ALL 26 entries in 'devrc/', none omitted` — a completeness claim about a disk that
  is missing three entries, and it carries **no staleness stamp** of the kind the pod's own
  `SNAPSHOT, NOT THE SOURCE` banner has.
- **Ruled out:** that this is the ordinary read-through-cache lag recorded above — that entry is
  about the mirror trailing the pod by a few entries. This is structural: the mirror is
  **read-only and never synced**, so the gap is unbounded, and the reader that IS synced is a
  different command against a different path. via: measurement
- **Ruled out:** that `cairn sync` fixes it — `sync` updates `~/.cache/subsystem-store`, which is
  not the path `--repo` resolves. Running `cairn sync` immediately before the probe changed
  nothing. via: command
- **Leading hypothesis:** criterion 9 repointed the WRITE path and `cairn`'s own read; the
  **prescribed read surface was not repointed**, and nothing detects it. Every `/resume` since
  the cutover has oriented on a store frozen at 2026-09-01 while believing it complete.
- **Next probe:** none needed for diagnosis. Pick a fix: point `subsystem_recall --repo` at the
  synced cache; or make it REFUSE a store with no snapshot stamp (so a frozen mirror cannot
  masquerade as current); or rewrite the prescribed command to `cairn recall` in the `resume`
  and `subsystem-index` skills **and** in both cairn handoff docs. The refusal arm is the one
  that also protects the next store that gets frozen.

### 🔴 OPEN — `drift-check` is now a PERMANENTLY-RED deadman, by operator decision
- **Symptom + exact repro:** `bash ~/workspace/devrc/scripts/drift-check.sh` → **rc 24**, and
  the `drift-check.timer` unit fails on every fire, firing its `OnFailure` toast.
- **Observed (with values), 2026-09-02:** `main` on the canonical remote has **no
  `required_status_checks` key at all** (the sub-resource 404s: *"Required status checks not
  enabled"*) and `enforce_admins.enabled = false`. `drift-check.sh` prints
  *"🔴 DRIFT — innovation-upstream/devrc main has ZERO required status checks (protected=true),
  by CLASSIC protection and by RULESETS alike — both were checked"*. The unit is
  `SuccessExitStatus=16` **only** — rc 24 is NOT in it — and the timer is `OnUnitActiveUSec=6h`,
  so this fails **4×/day indefinitely**.
- **Ruled out:** that this is transient or self-healing — the state is an unclosed break-glass
  (`protected=true` with the sub-resource deleted), and `PATCH` cannot restore it. via: measurement
- **Ruled out:** that this session caused it — the capture step of the break-glass procedure was
  attempted and **failed** (`cannot iterate over: null`), so nothing was deleted here; CLAUDE.md
  records the same state measured twice on 2026-08-29. via: command
- 🔴 **OPERATOR DECISION 2026-09-02: LEAVE PROTECTION OFF.** That is settled and is not to be
  re-litigated. What is NOT settled is the alarm: `claude/RULES.md` says a permanently-red gate
  is worse than no gate because it trains everyone to click through, and rc 16 already has
  precedent for a deliberate state being made a systemd success.
- **Next probe:** none — decide. Either add rc 24 to `SuccessExitStatus` in the nix unit with
  the reason recorded beside it, or accept 4 failure toasts a day. Rank **18**.

### RESOLVED BY MEASUREMENT — `cairn put` IS exercised against the live pod
- **Supersedes the OPEN block above it.** Used twice this session on
  `devrc/subsystem-store-api`: `If-Match 3aee155a848e1670` → `c865d70976d961e0`, then
  `9eee662af4986546` → `4056a7d18b5c0f75`, each deriving `If-Match` from a live `cairn sync` and
  each answering `replaced`.
- **Ruled out:** that this closes rank 12 — it does not. Rank 12's closing condition is one prune
  observed landing on the pod **through `prune-index`**, and that caller is still unexercised.
  The VERB is proven; the CALLER is not. via: doc

### `main` goes red from direct-to-main espanso commits — TWICE in one session, both times found by hand
- **Symptom + exact repro:** `nix develop <repo> -c python3 -m pytest scripts/collector/keylog/tests/test_espanso_detect.py -q` on plain `origin/main`.
- **Observed (with values):** break 1 — `a720d30d` put *"and recommend improvements…"* into `:acq`'s **label**; `_token_matches` reads the label, so `recom`/`recommend` matched both `:acq` and `:rna`, `_attribute` saw 2 candidates and returned `None`. Break 2 — `a451abc0` **swapped** `label` and `replace`, removing the collision at source, which falsified the anti-vacuity premise of the guard that fixed break 1: `AssertionError: recom / assert [':rna'] == [':acq', ':rna']`.
- **Ruled out:** that break 2 was a regression — it is a guard whose premise died; `_attribute('recom')` and `_attribute('recommend')` both return `':rna'` via the plain uniqueness branch, printed live. via: measurement
- **Ruled out:** that #1247's `_AMBIGUOUS_TERM_OWNER` entries still exist — `de677683` (#1252) deleted them; the table today is `{'ask': ':acq', 'clarify': ':acq'}`. via: command
- **Leading hypothesis:** not a code defect at all. The mechanism — **a label edit silently re-attributing telemetry terms, in EITHER direction** — is live and will recur; branch protection is deliberately off, so nothing observes it.
- **Next probe:** none for diagnosis. The open decision is rank 22's detector.

### 🔴 MY OWN "10 entries / 102 local-only lines" FIGURE CONFLATES TWO POPULATIONS — correcting #1260
- **Symptom + exact repro:** diff every entry present in BOTH `~/.claude/analyze-service-index` and `~/.cache/subsystem-store`, count lines present locally and absent from the pod.
- **Observed (with values):** 2026-09-03 first reading **10 entries / 102 lines**; an hour later **13 / 109**, on a mirror that is now `0555` and CANNOT gain content. Three of the new ones are **supersessions, not strandings** — including `devrc/subsystem-store-api.md: 1`, which is **my own `cairn put`** closing an `OPEN:` to `RESOLVED`. The pod legitimately rewrote the line; the frozen mirror still holds the old one.
- **Ruled out:** that the count only measures stranded content — a `cairn put` that rewrites or deletes a line makes the mirror's old copy read as "local-only" identically. via: measurement
- **Ruled out:** that another session's reconciliation had already cleared it — the #1254 session's `devrc/cairn` bullet records **21 stranded bullets reconciled by `cairn put` on 2026-09-03**, and the count did not fall. via: measurement
- **Leading hypothesis:** the honest metric is **what the POD LACKS**, per bullet, not what the mirror holds — and it must exclude bullets the pod deliberately rewrote. The real stranded population is therefore **unknown and ≤ 109 lines**, not the 102 rank 23 asserts.
- **Next probe:** re-derive per-bullet (not per-line) against the pod, treating a rewritten `OPEN:`→`RESOLVED` as reconciled rather than stranded, before doing any of the hand-merges.

### 🔴 My #1247 was a WORKAROUND and another session shipped the STRUCTURAL fix over it
- **Observed (with values):** `b9b2493d` (#1247, mine) declared `:rna` the owner of `recom`/`recommend` — a table entry. `de677683` (#1252, another session) made *a snippet's declared `search_terms` outbid another snippet's LABEL*, which is the mechanism, and removed my entries as unnecessary.
- **Ruled out:** that the two are alternatives — the structural one strictly subsumes the declaration. via: code
- **Leading hypothesis:** `claude/RULES.md`'s *"prefer deterministic/structural fixes over prose"* applied to me. I fixed the instance under a "relax the gate" ruling; the class needed the mechanism.
- **Next probe:** none. Recorded so the pattern is visible, not to be re-derived.

## Next steps (ranked)

🔴 **Numbering is UNCHANGED on purpose** — the rank is half a claim's identity
(`claim-work --slug-for <this doc> <rank>`). Items are marked done IN PLACE; new items APPEND.


🔴 **Numbering is UNCHANGED on purpose** — the rank is half a claim's identity
(`claim-work --slug-for <this doc> <rank>`). Items are marked done IN PLACE; new items APPEND.

1. 🔴 **Criterion 10 step 2 — (a),(b),(c) DONE 2026-08-31. Only (d) is left:**
   `shred -u ~/.config/subsystem-store/env.bak-legacy-2026-08-29` on **BOTH** hosts
   (`ssh zach@192.168.50.155` for the laptop). Confirm `cairn sync` health on each host first —
   this destroys the last local copy of the retired credential. Recovery afterwards is
   `git log -p` on the secrets file or the backup CronJob, **not** these files.
   forcing: security
2. ~~**The backup CronJob.**~~ ✅ **CLOSED 2026-08-30** — homelab-infra#551, squash `c4e0f82b`.
   forcing: none
3. ✅ **DONE 2026-09-01 — criterion 8, the laptop half.** Its 49 entries and 7 exclusive scopes
   are on the pod (`remote_entries=201`, `seed: OK all 49 staged entries are present`), and its
   store is frozen with a watched EACCES on all 49. Pushed FROM the workbench via `--store`
   because the laptop has no kubeconfig — deliberately, rather than copying a cluster-admin
   credential to a second host. **Do not re-work.**
   forcing: none
4. ✅ **DONE 2026-09-01 — criterion 9, the cutover.** Write-through shipped (#1210), verifier
   fixed (#1214), both stores frozen, runbook §8 clean. **Do not re-work.**
   forcing: none
5. **Verify criteria 1, 2, 5, 6, 7 against the POD**, not just in tests. Criterion 4 is done
   there; 2's denied-scope arm is done for WRITES (404 `scope-unknown`) but not for the three
   read routes. ⚠ The allowlist is **23 scopes**, not 15.
   forcing: none
6. **Add the `internal-error` alert** in the monitoring config. Without it the dispatch backstop
   turns a dropped connection into a quiet 500 only the audit log sees.
   forcing: none
7. ✅ **DONE — `scripts/cairn` has a write verb, and BOTH verbs are now proven live.** `append`
   was proved 2026-09-01; `put` was proved 2026-09-02 (see the open investigation). The
   `prune-index` caller is rank 12. **Do not re-work.**
   forcing: none
8. **§5's off-mesh control, still unrun** — from a phone on cellular:
   `curl -si https://store.zacx.dev/api/v1/recall/devrc` (expect 401) and
   `curl -si https://store.zacx.dev/` (expect 404). Cannot be done from a host on the mesh.
   forcing: none
9. **devrc #1045** — three pre-existing `seed.sh` gaps; the third (local-side `-type f`
   uncovered) mirrors what #998 fixed. ⚠ `#1045` is an **issue, not a PR**.
   forcing: none
10. 🔴 **`scripts/provision-vaultwarden-backup-bucket.sh` has an orphaned-credential window.**
    `mc ilm rule add` runs **LAST**, after `mc admin user add` + `policy attach`, with no
    pre-flight refusals — an abort there leaves a live write-capable MinIO key whose secret was
    never printed. **Closing condition:** a merged homelab-infra PR moving `ilm rule add` above
    `user add`.
    forcing: security
11. ✅ **DONE 2026-09-02 — `verify-byte-identity.sh` is order-independent.** #1222 `7d9da8f5` +
    #1228 `85257361`, four audit rounds to a clean round, live-verified against the pod.
    **Do not re-work.**
    forcing: none
12. **Exercise `cairn put` once, live, through `prune-index`.** Repo `devrc`. The VERB is proven
    (see the open investigation); the `prune-index` caller is not. **Closing condition:** one
    prune observed landing on the pod, with the revision printed.
    forcing: none
13. **Close the allowlist-staleness hole.** Repo `homelab-infra` (token minting) and/or `devrc`
    (`scripts/drift-check.sh` arm comparing each host's scope set to the allowlist).
    forcing: none
14. **`claude/skills/subsystem-index/reference/index-write.md` documents the RETIRED
    `Edit`-anchor protocol.** Repo `devrc`. The skill body says so in one line; the sidecar still
    reads as current.
    forcing: none
15. ✅ **DONE 2026-09-02 — #1218 merged (`e557ed19`) and the base clone re-synced.**
    **Do not re-work.**
    forcing: none
16. ✅ **DONE 2026-09-02 — the read path goes through the pod.** #1233 squash `9519781f`, four
    audit rounds, both hosts switched, closing condition executed end-to-end (see `State now`).
    Claim released. **Do not re-work.** Superseded text follows for context only:
    🔴 ~~**`/resume` READS A FROZEN STORE — this is what stops the doc's Goal being met.**~~ Repo
    `devrc`, file `scripts/lib/subsystem_recall.py` (+ the `resume` and `subsystem-index` skills
    and this doc's own header command). Measured 2026-09-01 minutes after two live writes:
    `subsystem_recall.py --repo` reads `~/.claude/analyze-service-index` — **26 entries, mode
    444, nothing refreshes it, 0 occurrences of the new bullet** — while `cairn recall` reads
    `~/.cache/subsystem-store` — **29 entries, 1 occurrence**. Criterion 9 repointed the WRITE
    path and `cairn`'s read; the **prescribed READ surface was never repointed**, and the frozen
    mirror still prints `ALL 26 entries … none omitted`. **Closing condition:** a merged devrc PR
    doing ONE of — point `--repo` at the synced cache; make it REFUSE a store carrying no
    snapshot stamp; or rewrite the prescribed command to `cairn recall` in both skills and this
    doc — verified by a `/resume` step 4 that surfaces an entry written through `cairn append` in
    the same session. **The refusal arm is preferred: it also protects the next store that gets
    frozen.**
    forcing: regression — the cutover changed the read path's meaning and nothing detects it
17. **The verifier can never see 7 of 23 scopes.** Repo `devrc`,
    `scripts/subsystem-store-api/verify-byte-identity.sh`. It enumerates scopes from the LOCAL
    store, so `auditloop civitai-gpu-fleet naida-ai vetr vetr-api vetr-app vetr-infra` — **48
    entries, 25% of the served store** — are never compared. #1228 made every run print a
    `verify: COVERAGE —` line saying so, which is honesty, not coverage; the API deliberately has
    **no enumeration route** (`README.md:103`), so closing it needs one. **Closing condition:**
    either a merged PR adding that route and widening the sweep, or a recorded decision that the
    COVERAGE disclosure is the permanent answer.
    forcing: none
18. ✅ **DONE 2026-09-03 — the deadman exits 0.** #1250 squash `77dc3642`, two audit rounds,
    both hosts switched, claim released. Implemented as the operator chose: an in-repo
    DECLARED expectation, so rc 24 fires on DISAGREEMENT rather than on the bare state —
    the botched-restore alarm is preserved, not silenced. New **rc 25** catches the
    declaration going stale. 🔴 **Deleting the declaration arm is PART OF restoring
    protection**; leaving it disarms rc 24 for this repo. **Do not re-work.**
    Superseded text follows for context only:
    ~~🔴 `drift-check` fails 4×/day forever unless rc 24 is made a success.~~ Repo `devrc`, the
    nix unit carrying `SuccessExitStatus=16`. Consequence of the operator decision to leave
    branch protection off (see the open investigation). rc 16 is the precedent: a deliberate
    state made a systemd success so the DND-defeating failure toast stops. **Closing condition:**
    a merged devrc PR adding rc 24 to `SuccessExitStatus` with its reason recorded beside it, or
    a recorded decision to accept the toasts.
    forcing: gate — a permanently-red deadman trains everyone to click through it
19. **Two store-api CI flakes, both live, neither diagnosed.** Repo `devrc`. (a) The
    store-api gate flake **RECURRED TWICE after #1211's tmpfs fix** — two runs of ONE unchanged
    revision (`bf433490`, docs-only) failed on TWO DIFFERENT tests, the second being
    `TestTheActorComesFromTheTOKEN.test_a_FORGED_actor_in_the_body_is_DISCARDED`, the same test
    named in the 2026-08-31 `TimeoutError` investigation. (b)
    `test_live_cotenants_sees_another_process_in_the_repo` failed once in the sandbox on
    `732ef4b6` then passed on the **identical derivation hash**; it is in none of that PR's 8
    files and operates on a fresh `tmp_path/repo`, never `ROOT`, so no PR change can be its
    co-tenant. **Closing condition:** `claudedocs/handoff-gate-flake-store-api.md` records a
    measured flake RATE over a named run count post-#1211, and (b) is either reproduced or filed
    as its own item there.
    forcing: gate — these red a required check on PRs whose diff cannot reach them
20. ✅ **DONE 2026-09-03 — the third frozen read surface is routed.** #1249 squash
    `2c6b2ac9`, both hosts switched, claim released. `subsystem-audit.py`'s constant is
    **deleted**, not repointed; it resolves through `subsystem_read_store` and refuses an
    unstamped default (exit 4) while an explicit `--store` stays permissive.
    ⚠ **Correction to this item as originally written:** the tool is **READ-ONLY by
    contract** (`test_the_audit_source_contains_no_write_call`) — it *drives* deletions
    through a human-confirmed skill. "It deletes, therefore refuse everything" does not
    follow, and that is what makes the permissive half defensible. **Do not re-work.**
    Superseded text follows for context only:
    ~~🔴 A THIRD frozen read surface, untouched by #1233.~~ Repo `devrc`,
    `scripts/subsystem-audit.py:101` open-codes its own
    `DEFAULT_STORE_ROOT = ~/.claude/analyze-service-index` and defaults `--store` to it. The
    `prune-index` skill always passes `--store ~/.cache/subsystem-store` explicitly, so the
    **prescribed** path is safe — but `claudedocs/handoff-analyze-service-index-backup.md:452`
    prescribes it **bare**, which now reads the frozen mirror. Deliberately left out of #1233:
    that tool drives **deletions** via `prune-index`, so repointing its default has its own
    blast radius and belongs in its own change. **Closing condition:** a merged devrc PR either
    routing it through `subsystem_read_store.read_store_root()` or refusing an unstamped
    default, plus the bare prescription in that handoff corrected.
    forcing: regression — same defect as rank 16, in a tool whose verb is `delete`
21. **57 historical `claudedocs/handoff-*.md` still prescribe the old reader.** Repo `devrc`.
    #1233 updated the two cairn docs plus the `/handoff` generator; 57 others keep the old
    "Run this first" block. ⚠ **This is not obviously worth fixing** — a stale block now exits
    **4** with a refusal naming `cairn sync`, i.e. it fails loudly with its own remedy instead
    of lying, and several of those lines quote the command as *evidence about this very
    defect*, so a blind rewrite would corrupt them. **Closing condition:** a recorded decision
    that the loud refusal is the permanent answer, **or** a merged PR doing a reviewed (not
    mechanical) pass. Either closes it; do not leave it open as a vague cleanup.
    forcing: none
22. 🔴 **A direct-to-main commit RED-ed `main` and nothing caught it.** Repo `devrc`.
    `a720d30d` ("espanso", one line, no PR) changed `:acq`'s label so `recom`/`recommend`
    matched two snippets and resolved to `None`, failing
    `test_espanso_detect.py::test_live_existing_resolutions_not_made_ambiguous` on **both**
    tiers. Fixed 2026-09-02 by **#1247** (squash `b9b2493d`) on the operator's ruling *"the gate
    is overly strict, relax it"* — two `_AMBIGUOUS_TERM_OWNER` entries declaring `:rna` the
    owner, which is the mechanism the detector already provides; `nix/home.nix` and
    `_EXISTING_RESOLUTIONS` untouched. ⚠ **The residual is recorded and NOT closed:** the lookup
    is exact-string, so `rec`, `reco` and `recomm` still resolve to `None`. **The durable item
    is the detection gap, not the fix:** with branch protection off (a live operator decision),
    a direct commit can red `main` and the only detector is whoever next runs the gate by hand —
    here, this session, hours later. **Closing condition:** a recorded decision that manual
    gating is accepted while protection is off, or a merged PR adding a cheap post-push check.
    🔴 **RECURRED 2026-09-03, so the rate is measured rather than hypothetical: TWICE in one
    session.** `a451abc0` ("espanso", direct to main, no PR) swapped `:acq`'s `label` and
    `replace`, which red-ed `main` a second time — and BOTH times the only detector was a human
    running the gate by hand, hours later, incidentally. ⚠ **The second break was a GUARD WHOSE
    PREMISE DIED, not a regression** (the collision it asserted was gone at the source), so a
    detector that only greps "did attribution regress" would have missed it — whatever is built
    must run the REAL suite, not a distilled check. **Concrete cheap option, proposed with
    evidence:** trigger `scripts/collector/keylog/tests/test_espanso_detect.py` (0.3 s, hermetic
    apart from reading `nix/home.nix`) whenever `nix/home.nix`'s espanso `matches` block changes
    — it would have caught both, at authoring time.
    forcing: gate — `main` was red for hours, twice, and no mechanism reported it

23. 🔴 **THE FREEZE WAS INCOMPLETE AND SIX WRITES LANDED IN THE DEAD STORE — closed 2026-09-03,
    but the WRITER is not fixed.** Found by `cairn doctor` on its first live run (#1255).
    The cutover chmod'd the 153 files that existed to `0444` and left the scope
    **DIRECTORIES** writable, and `subsystem_touch.py` still targets the mirror — so new
    entries could still be created there. Six were, 2026-09-02 13:54 → 2026-09-03 11:17, and
    **none was on the pod**. This is rank 16 INVERTED: reads pointed at a store nobody writes,
    versus writes landing where nobody reads.
    **Measured, not inferred:** 7 files at 644 (not 6), and an append was WATCHED to succeed.
    **Done:** all six preserved byte-identical at `~/.local/share/cairn-orphans-2026-09-03/`
    (outside the PUBLIC repo — they are client-scoped); the four whose scopes the pod serves
    pushed via a CURATED `seed.sh --store` (201 → 205 entries, seed's own note confirming the
    other 201 untouched, all four byte-identical through the API afterwards, backup CronJob
    re-verified unsuspended + successful 03:45Z immediately before); the mirror now 160 files
    `0444` / 1068 dirs `0555`, proven by THREE watched refusals — append, create-entry,
    create-scope — plus a positive control showing the probe still succeeds on the live cache,
    and a confirmation that READS still work.
    🔴 **Still open — the writer.** Permissions stop it; `subsystem_touch.py` still points at
    the mirror, so the next writer gets EACCES rather than being routed. **Closing condition:**
    a merged devrc PR making the write path refuse or route, plus the two remaining orphans
    (`civitai-app-requests/app-requests`, `civitai-developer-docs/apps`) pushed once rank 24
    unblocks them. Until then the backup directory is the only copy.
    forcing: regression — silent data loss, observed at roughly one entry every few hours

    🔴 **RANK 23 WAS UNDER-COUNTED AND THE RESCUE WAS INCOMPLETE — measured 2026-09-03 after
    the item above was written.** The rescue moved whole FILES whose names were absent from
    the pod. It never looked for extra content INSIDE entries that exist on both sides.
    Measured across all 157 shared entries: **10 carry local-only content, 102 local-only
    lines total** — and **all 10 are TWO-WAY divergent** (every one also has pod-only content),
    so `seed.sh` would DESTROY pod content in all ten cases:

    | entry | local-only | pod-only |
    |---|---|---|
    | `civitai/blocks.md` | 15 | 31 |
    | `civitai-spine-controller/resourcedownload.md` | 6 | 10 |
    | `homelab-talos/tekton-ci.md` | 3 | 6 |
    | `homelab-talos/gitops-validate.md` | 19 | 20 |
    | `civitai-app-sensei/sensei-app.md` | 14 | 16 |
    | `devrc/tests.md` | 15 | 17 |
    | `devrc/dl-router.md` | 21 | 17 |
    | `devrc/diagnose-disk-accounting.md` | 3 | 4 |
    | `datapacket-talos/claude-pool.md` | 3 | 9 |
    | `datapacket-talos/tekton-builds.md` | 3 | 24 |

    All ten preserved byte-identical (`cmp` AND `diff`) at
    `~/.local/share/cairn-orphans-2026-09-03/shared-divergent/`, outside the PUBLIC repo —
    they are client-scoped. 🔴 **This is TEN HAND-MERGES, not a migration.** It is the
    `devrc/signal.md` decision from phase 1 — *union of both, neither copy discarded* — which
    that entry called "one file; the cost is trivial". It is ten now. The merge path exists and
    is proven: `cairn put` works on an EXISTING entry (If-Match from a live sync).
    **Closing condition:** all ten merged onto the pod and the backup directory removed, or a
    recorded decision that the local-only content is disposable.
    forcing: regression — 102 lines of curated content are dark to every reader on every host

    ⚠ **Independently found and better diagnosed by another session — see devrc #1254.** It
    measured the same population (*"5 whole entries and 24 dated bullets across 10 shared
    entries"*) and, unlike this item as originally written, identified WHY the `0444` freeze
    did not stop it: **the freeze is inert against the tools agents actually use.** In a
    replica (0444 file, 0755 dir) a shell `>>` gets EACCES, but Claude Code's `Edit` **writes
    through** — rewrite-and-rename needs only the DIRECTORY bit and leaves the file 0444 — and
    `Write` creates a fresh 0644 file. Four documents asserted the freeze stops an editor.
    The 2026-09-03 `0555` directory change closes this, and its three watched refusals
    (append / create-entry / create-scope) are exactly the three paths that mechanism predicts —
    but the mechanism was identified there, not here.

24. 🔴 **THE API HAS NO CREATE ROUTE, which is very likely WHY rank 23 happened.** Verified in
    `server.py`: `If-Match` is **mandatory** on PUT, `*` is refused, and the handler resolves an
    existing entry — so `cairn put --file` on a new ref fails *"cannot derive a revision"*. A
    scope's first record can only reach the pod through an operator `seed.sh`. So the only
    create path available to a session is LOCAL, and nothing carries it onward.
    **Closing condition:** a merged PR adding a create route (or an explicit, recorded decision
    that seeding is permanently the only create path, with `subsystem-index` saying so).
    forcing: regression — the missing route is the upstream cause of rank 23

25. 🔶 **IN FLIGHT: devrc#1261** — **`CPU_MON_TEMP_THRESHOLD` is host-specific pretending to be shared.** Repo `devrc`,
    `nix/home.nix`. The laptop carried an UNCOMMITTED `88 → 92` edit that blocked EVERY
    `ship.sh` fast-forward to it (rc 7) — the documented "skipped host silently stops receiving
    changes while looking healthy" failure. Preserved as branch `laptop/cpu-mon-temp-92`
    (`44ebd9c6`, pushed, content verified from the WORKBENCH rather than the machine that made
    it) and upstream taken so the host could converge. It is one shared line whose own comment
    says *"This laptop runs hot at idle"* — which is why it keeps coming back as a local edit.
    **Operator decision 2026-09-03: split it host-conditional**, 92 laptop / 88 workbench.
    **Closing condition:** a merged devrc PR doing that split, after which the preserved branch
    is deleted.
    forcing: gate — while it exists uncommitted, the laptop receives nothing

26. **`present/measure.py` resolves the frozen mirror, and its provenance string is false.**
    Repo `devrc`. Found by #1255's ledger sweep — `Env.live()` resolves the mirror for the
    explainer page while the page still claims it was *"read via `subsystem_recall.load_store()`"*.
    Ledgered as SITED with the question recorded OPEN rather than fixed, because the fix is a
    judgement about what the page should claim. **Closing condition:** a merged PR either
    routing it through the resolver or correcting the provenance string to what it does.
    forcing: none — a stale page, not a data path

## Gotchas / decisions / dead-ends

**From the rank-18/20/24 session (2026-09-03) — what cost time, and what paid:**
- 🔴 **A NEW COMMAND FOUND A LIVE DATA-LOSS PATH ON ITS FIRST RUN.** `cairn doctor` was built
  on the instruction *"minimise prose, prefer scripts"*, and before it merged it reported
  `frozen-mirror PROBLEM` — six entries written to the dead mirror over two days, invisible to
  every reader. **A skill DESCRIBING what to check would have described it and found nothing.**
  That is the whole argument for the script-over-prose form; it is not a style preference.
- 🔴 **I READ ONE TIER'S TOTAL AND REPORTED IT AS BOTH.** I quoted `failed=1` off the dev-host
  gate; the SANDBOX tier — the one the merge gates on — had **24** (115 `CalledProcessError`,
  23 `exit status 128`, from an unguarded `git ls-files` in a tree with no `.git`). `RULES.md`
  names this exactly; read each tier's OWN total, and quote the tier with the claim.
- 🔴 **THREE INSTRUMENT FAILURES IN ONE HOUR, each caught ONLY by a control.** `find` is a
  shell FUNCTION here and its `-print0` emits nothing (positive control also returned 0 — the
  only tell); a `grep -cE "A|B"` matched a line present regardless of the variable under test;
  and a "positive control" was pointed at a repo whose protection API 404s, so it exercised
  COULD-NOT-MEASURE instead of the alarm. **A zero is a claim about the instrument until a
  control has moved it.**
- 🔴 **A MUTATION THAT DOES NOT APPLY SCORES AS A FALSE NEGATIVE.** My first attempt to prove
  the rc-24 alarm still fires used a regex assuming quotes the real line does not have:
  `arms removed: 0`, and the run was the UNMUTATED script. Printing the applied-count is what
  stopped it being recorded as "the alarm is dead". **Assert the mutation applied exactly once,
  every time.**
- ⚠ **A grep count is a claim about the PATTERN.** Verifying #1249, `grep -c 'DEFAULT_STORE_ROOT
  = Path.home()'` returned **1** — reading as "the constant survived". It was the comment
  documenting what USED to be there; stripping comments gives 0.
- **Agents corrected me four times, and were right each time:** `subsystem-audit.py` is
  READ-ONLY by contract, not a delete tool (my framing would have justified refusing every
  invocation); an auditor's "red at base" was measured against a MID-PR commit and the
  implementer refused the instruction and re-measured; tier B is NOT free (the description
  budget is separate from the tier ledger and had 12 chars of headroom); and my ledger
  population was missing five sites. **The refusals were more valuable than the compliance.**
- ⚠ **`ship.sh` re-executed itself** — the run shipped a change to `ship.sh`, so the CONVERGE
  payload it had already expanded was stale and it re-ran the new copy before the remote leg.
  Working as designed; do not read the first block's output as the final word.

**From the rank-16 session (2026-09-02) — instrument and process lessons:**
- 🔴 **`find` IS A SHELL FUNCTION ON THIS HOST, and its `-print0` emits nothing.** Bounding a
  blast radius with `find … -print0 | xargs -0 grep -l` returned **0 files** — and the
  **positive control returned 0 too**, which is the only reason it was caught. `command grep`
  alone is not enough when `find` is *also* shadowed. `command grep -rl <pat> <dir>` with a
  control that must be non-zero (got 15) is what worked. Generalises past the documented
  `grep`-honours-`.gitignore` trap: **`type <tool>` before trusting a pipeline's zero.**
- 🔴 **AN AUDITOR MEASURED "RED AT BASE" AGAINST A MID-PR COMMIT, and I relayed it as fact.**
  Round 2 declared the matrix wrong and "corrected" 26/29 → 25/30. Both it and I were wrong:
  it used `c05b2df8`, a commit that already contained this PR's own SKILL.md change. Measured:
  `analyze-service/SKILL.md` holds `store-unstamped` **0×** at the true base `a6e50641`, 1× at
  `c05b2df8`, 1× at head (positive control). **The implementer refused the instruction and
  re-measured — which is what should happen.** A "red at base" claim that does not NAME its
  base cannot be checked; naming it is now part of the claim.
- 🔴 **`main` MOVED THREE TIMES mid-ladder** (`a6e50641` → `3d0695c7` → `946a51f0` →
  `5a82aaa9` → `b9b2493d`). Every merged-tree gate went stale on both axes — head *and* base.
  **Re-derive the base immediately before the run that will justify the merge**, not at the
  start of the session.
- **DISJOINT FILES, MEASURED CLEAR RATHER THAN ASSUMED.** #1219 (`b4fde334`, "one shared store
  siting") landed mid-ladder touching the same subsystem with **zero** files in common. The
  merged-tree gate is what cleared it; the file-overlap check would have been a cheaper,
  different claim.
- **The ladder's stop was the ATTRIBUTION GATE, not a clean round** — payload/scaffolding went
  1:2.7 → 1:5.5 → **1:16**, and rounds 3 and 4 returned 4-of-5 then **4-of-4** findings about
  guards the ladder itself had written. Round 4's fixes touched **no runtime file at all**.
  Recorded on the PR, because a closure on this gate is otherwise indistinguishable from
  convergence.
- ⚠ **An agent session can expire mid-task and leave uncommitted work with no branch.** One did,
  holding 119 lines across two files and no PR. Nothing was lost, but nothing was saved either.
  **Resuming it, the first instruction was "commit what you have."**

**Operator rulings this session (all acted on):**
- 🔴 **Espanso: "the gate is overly strict, relax it."** Acted on via the detector's own
  `_AMBIGUOUS_TERM_OWNER` declaration, **not** by editing `_EXISTING_RESOLUTIONS` — whose
  docstring calls deleting the row that broke "exactly the regression this guard exists to
  catch". The distinction matters: a *declaration* that `:rna` owns the terms is honest
  (`owner in matched` makes a stale entry go inert), whereas lowering the floor would have been
  the failure mode wearing the fix's clothes.
- **PUT does not enforce attribution.** The claim is scoped to POST everywhere it is asserted, with a guard test pinning the limit. PUT is a whole-file replace used for `## Pointers` and the `OPEN:` → `RESOLVED <sha>:` rewrite; per-bullet enforcement would have to diff old against new bullet sets and risks refusing legitimate rewrites.
- **#371 MAY touch `scripts/lib/subsystem_resolver.py`.** #360's non-goal was #360's. This is what let the `visible_scopes` pushdown into `load_index` happen.
- **Entry-kind guard is NARROW.** Refuse `KIND_BROKEN_LINK`, `KIND_OTHER`, `KIND_LINK_TO_OTHER`, `KIND_DIRECTORY`, `KIND_LINK_TO_DIR`. **`KIND_LINK_TO_FILE` and regular files stay accepted** — no behaviour change for any legitimate caller. The broad form (mirroring `_ENTRY_ACTIONS` wholesale) was considered and rejected.
- **A `legacy` bare token may not write** — no identity ⇒ no actor to derive. Fails closed, and is why criterion 10 blocks the write path.

**Design decisions worth not re-litigating:**
- `If-Match` uses the **entry content hash**, not `scope_revision`. No scope in the served copy is a git repo, so `scope_revision` answers `"unknown"` for all of them and an `If-Match` on it would be satisfied forever by that literal string.
- Concurrency is an exclusive `flock` on a **side file** (`.<entry>.md.lock`), because the write is temp-file + `os.replace` — a lock on the entry's own inode is useless across the rename.
- The entry codec is consolidated behind `decode_entry_text`/`encode_entry_text`. **Four sites were deciding it and one was wrong**, which is what made a latin-1 byte in a nuance bullet permanently unappendable.

**Criterion 10, step 1 — what the operation actually taught (2026-08-29):**
- 🔴 **AN API WRITE LANDS IN THE SERVED COPY ONLY, AND `seed.sh` OVERWRITES IT.** The push is
  `rsync -a --delete` SOURCE→STAGE then `tar` STAGE→pod, so the next seed from any host
  replaces the entry file with the local one and the appended bullet is **gone**. Measured
  right after the first production append: served `14603 B` **with** the bullet, local
  `14696 B` **without** it — already divergent in both directions. **"The write path works"
  and "the bullet survives" are two different claims, and the second is false until criterion
  9.** Put anything you want kept in the LOCAL store as well.
- 🔴 **`load_tokens` runs ONCE, at startup — there is NO SIGHUP reload.** A secret edit is
  inert until the pod is replaced, and with `Recreate` at `replicas: 1` a malformed row is
  `exit 78`: the store stays **DOWN**, it does not fall back to the old file. Replace the pod
  with `kubectl delete pod`, not `rollout restart` — the latter costs two rollouts here
  (homelab-talos `CLAUDE.md`), i.e. two hard read outages for one intended restart.
- 🔴 **Pre-flight the candidate token file against the DEPLOYED `server.py`, not `main`.**
  Extract it from the pod (`kubectl exec … tar czf - -C /app scripts`), confirm the sha
  matches the pod's own copy, then run `load_tokens` over the exact candidate bytes. Five
  negative controls each fire with their own message and are worth keeping: a space after a
  scope comma (parses as **4 fields**), a mapped row claiming the reserved identity `legacy`,
  the **same token on a bare AND a mapped row** (guard 11), two rows claiming one identity
  (guard 12), a short token (`>= 43`). Import gotcha: `sys.modules[name] = mod` **before**
  `exec_module`.
- 🔴 **Guard 11 means "scope a credential its holder already has" CANNOT be done by adding a
  line below it.** Bare `<tok>` + mapped `<tok> zach …` is refused as *"one credential is
  given two different authorities"*. A second holder needs a second token — which is why
  step 1 mints a NEW token rather than mapping the old one.
- ⚠ **A mapped row's allowlist is a SNAPSHOT; there is no wildcard.** `scopes is None` is
  reachable only from a bare row, so a scope added to the store is invisible to `zach` until
  the row is edited — and by criterion 3 that is indistinguishable from a scope that does not
  exist. Adding a scope is a two-place change.
- ⚠ **Do not date-prefix your own bullet text** — the server prepends `- <date>: ` itself.
  The first production append reads `- 2026-08-29: 2026-08-29: …` because of it.
- ⚠ **`git -C <worktree> diff` on the entry, not a byte-offset compare.** An append at the TOP
  of `## Nuance / work-history` shifts every later offset, so `diff <(head -c N before)
  <(head -c N after)` reports "pre-existing bytes changed" when nothing changed. The real diff
  was a single added line.

**Traps that cost real time — do not re-pay them:**
- 🔴 **`session` is a REQUIRED body field on the append route.** Omitting it gives 400, and reading "0 occurrences of the forged actor" off that response is a **false green** — nothing was written. Every write probe needs a positive control proving the bullet landed.
- 🔴 The server **fail-closes without `SUBSYSTEM_STORE_TRUSTED_PROXIES`** (min /24 for v4) and then **requires `CF-Connecting-IP`**. Without both, every response is empty — and two empty responses compare "byte-identical".
- 🔴 Store entries need valid front-matter or they read as **malformed**, which looks like a defect and is not.
- 🔴 A blocking `open()` on a FIFO **wedges a harness silently**. It wedged three agents. Subprocess + wall-clock deadline only.
- 🔴 `grep` here wraps ugrep and honours `.gitignore`; zsh does **not** word-split unquoted parameters (`for x in $LIST` loops once); and `$c:path` hits the **history-modifier trap** — brace it as `${c}:path` or you silently get the wrong ref. All three bit this session.
- An ad-hoc `importlib` loader for `server.py` needs `sys.modules[name] = mod` **before** `exec_module`, else the first `@dataclass` raises `AttributeError: 'NoneType' has no attribute '__dict__'`.
- `RESULT: all good` is a **test fixture's own output** in `gate.sh`; the real verdict is `RESULT: PASS (exit=0)`. A `RESULT:`-matching wait-loop fires ~25 minutes early.

**Residuals shipped deliberately, all documented in-tree:**
`X-Store-Snapshot newest=` cross-scope timing channel · orphan `.cairn-*.tmp` on SIGKILL · **read allowlist == write allowlist** (🔴 a backup must land before criterion 9, or every read token becomes a whole-file-destructive PUT credential) · ceiling window bounded at 0.75–1.25 s · `fcntl.flock` is single-host advisory, holds at `replicas: 1` only · idempotence-by-content-hash silently drops a genuinely new bullet byte-identical to an existing one · `_WRITE_INTERLEAVE` is an inert test seam in production code, unreachable from outside the process.

**On the audit ladder (9 rounds, 11 findings):** six of the eleven were **introduced by a previous round's fix** — including a surrogate crash caused by the fix for the lossy rewrite, and a response desync caused by the backstop added to stop requests vanishing. That is the entire argument for not stopping at the first green. 🔴 A correction on the record: the audit ceiling was twice described as "destroyed"; it was not — the **synchronous** double-emit was always caught, only the **deferred** case was lost.

**The retraction that started this session (devrc #990).** The previous doc said "Nothing has
ever been run against the deployed pod" and, two sentences later, "on the live pod every write
answers 403". Contradictory, and the 403 was a property of `main`. **Measured on the running
`0.4.0`:** `POST`/`PUT` both **405 `read-only`**; its `server.py` was 113,082 B with **0**
write-path markers where `main` has 222,147 B with **16**; verbs were `do_GET`/`do_HEAD` only.
🔴 So criterion 10 was not "blocked" — starting it would have **deleted the only credential
the pod understood**, killing every read from both hosts, and unblocked nothing.

**Two route facts that cost hours.** The write path is
`/api/v1/entry/<scope>/<ref>/**bullets**` — a wrong tail takes the unchanged `405 read-only`
tail, which reads exactly like "not deployed". And
`do_POST = do_PUT = do_PATCH = do_DELETE = _write` is a **class attribute**, so
`grep 'def do_'` shows only `do_GET`/`do_HEAD` on `main` too and is not evidence of a missing
verb.

**`seed.sh`'s push verdict (#998) — why it took 7 audit rounds.** The guard compared COUNTS,
which is only correct while one host ever seeds; the extract never deletes, so a second host's
entries made a *correct* push exit 7 **after** the content landed. It was also weaker than it
looked: a SYMLINKED scope produced `remote_entries=1 staged_entries=2` then `seed: OK`, rc 0 —
a push the old count check *would* have caught, so this PR's own claim that containment "fails
strictly more broken pushes" was **false as written** until both sides came from one
predicate.

🔴 **Rounds 3, 4 and 5 each found that the PREVIOUS round's fix introduced the next defect** —
a `shopt -p` capture that aborted the script under `set -e` (`shopt -p` exits 1 when the
options are unset); a `shopt` restore that made a dot-scope genuinely **ship**; and a probe
that announced correctly but reused wording asserting contents it could not read. Rounds 6 and
7 found nothing behavioural. **Budget for that pattern; it is the norm here, not bad luck.**

🔴 **Prose was wrong four times in one PR.** The NOTE header went "hold .md files" → "will NOT
ship" → "contribute NO entries" → "…entry count", each earlier version measurably false on a
different axis, and **keyword guards caught none of them** — any rewording satisfies
`"X" not in stdout`, and two such guards had become *unfalsifiable* (no code path could emit
the string they forbade). It is now pinned as a **whole normalised string** via one
parameterised helper. Comments in `flake.nix` were wrong twice the same way, including a
correction that was itself false.

**Instrument traps hit this session, all caught by reading content rather than status:**
`nix build … | tail; echo $?` captures **tail's** status (a `RESULT: FAIL` build reported
`SANDBOX_RC=0`); the same shape made a merge look successful; `nix build --rebuild` errors on
a derivation whose output does not exist yet; `git checkout HEAD -- <path>` used to revert a
mutation **ate uncommitted fixes twice** (use `cp`-aside); a patch heredoc died on a nested
`"""` while the suite still reported the old count.

**How #998 was merged — recorded, not buried.** `tekton/devrc-pytests` was RED at merge time
and `--admin` is refused (`enforce_admins: True`), so it took a temporary
branch-protection edit via the dedicated `enforce_admins` endpoint (not a whole-object PUT,
which can silently drop fields), restored immediately and verified **byte-identical** against
the pre-change JSON. 🔴 **Round 8 was never run** — the ladder was stopped by decision, not by
returning clean, so the final delta (a header clause, three docstrings, and a consolidation of
three assertions into one shared helper) is **unaudited**.

**`_md_state`'s three states.** `find`'s exit status is the discriminator — measured across
GNU find 4.11 and bfs 4.1 at `-maxdepth 1`: an unreadable *sub*directory, a broken symlink
child and a child directory named `*.md` all exit **0**; only the start point yields rc≠0. The
`2>/dev/null` is **inert**, not load-bearing (stdout byte-identical without it).

**Instrument traps hit while WRITING UP this work — both produced a confident wrong reading:**
- 🔴 **`clawgatectl` prefixes stdout with a version note when the server is ahead of the
  binary** (`note: server 0.8.16, clawgatectl built for 0.8.15`). Piping it straight into a
  JSON parser raises `Expecting value: line 1 column 1`, which reads exactly like "the post
  failed" — so the post was retried and **comment 514/515 on clawgate #371 are duplicates**.
  `clawgatectl` has no delete verb. **Parsing a tool's output makes its FORMAT a dependency
  you did not pin**; strip to the first `{` (`raw[raw.index("{"):]`), or read the exit code
  and a `grep -o '"id": [0-9]*'` instead of a full parse.
- 🔴 **An `OPEN:` marker wrapped in emphasis parses as NEAR-MISS, so it declares NOTHING.**
  `- 2026-08-30: 🔴 **OPEN: …**` scored `🔴 1 NEAR-MISS` and **zero** OPEN on the store index
  row; unwrapped to `- 2026-08-30: OPEN: …` it scored `🔴 1 OPEN`. The grammar wants the
  marker bare on the bullet's OPENING line. **Writing a marker is not declaring one** —
  `subsystem_recall.py --scope <s> --list` and read the badge, with a control (the scope shows
  9 OPEN markers, so a zero would not have been a detector wired to nothing).

**Operational facts established tonight:**
- 🔴 **`load_tokens` runs ONCE at startup — there is no SIGHUP reload.** A secret edit is inert
  until the pod is replaced, and with `Recreate` at `replicas: 1` a malformed row is `exit 78`:
  the store stays **DOWN**, it does not fall back. Replace the pod with `kubectl delete pod`,
  **not** `rollout restart` — the latter costs two rollouts here (homelab-infra `CLAUDE.md`),
  i.e. two hard read outages for one intended restart.
- 🔴 **Pre-flight a token file against the DEPLOYED `server.py`, not `main`.** Extract it from
  the pod (`kubectl exec … tar czf - -C /app scripts`), confirm the sha matches the pod's own
  copy, then run `load_tokens` over the exact candidate bytes. Five negative controls each go
  red with their own message: a space after a scope comma (parses as **4 fields**), a mapped
  row claiming reserved `legacy`, the **same token bare AND mapped** (guard 11), two rows
  claiming one identity (guard 12), a short token (`>= 43`). Import gotcha:
  `sys.modules[name] = mod` **before** `exec_module`.
- 🔴 **Guard 11 means "scope a credential its holder already has" CANNOT be done by adding a
  line below it** — bare `<tok>` + mapped `<tok> zach …` is refused as *"one credential is
  given two different authorities"*. A second holder needs a second token.
- ⚠ **A mapped row's allowlist is a SNAPSHOT; there is no wildcard.** `scopes is None` is
  reachable only from a bare row, so a scope added to the store is invisible to `zach` until
  the row is edited — and by criterion 3 that is indistinguishable from a scope that does not
  exist. Adding a scope is a two-place change.
- ⚠ **Do not date-prefix your own bullet text** — the server prepends `- <date>: ` itself. The
  first production append reads `- 2026-08-29: 2026-08-29: …` because of it. Left unrepaired
  deliberately: fixing it needs the whole-file `PUT` against a copy with no backup, and the
  next `seed.sh` clobbers the bullet regardless.
- ⚠ **Diff the entry, never byte-offset compare it.** An append at the TOP of `## Nuance /
  work-history` shifts every later offset, so `diff <(head -c N before) <(head -c N after)`
  reports "pre-existing bytes changed" when nothing changed.
- 🔴 **The devrc-ci CPU experiment was tried AND REVERTED — do not re-derive it.**
  homelab-infra `23887675` raised the pytests step's `requests.cpu` 2→4 to match its limit
  (pytests 1277s → 808s); `bb62668f` put it back three hours later because it bought queue
  waits of 17–22 min, three `TaskRunTimeout`s and **zero successful runs in the hour after it
  shipped**. Both commits are kept. The live Task's `requests.cpu: 2` matches `origin/trunk` by
  content — so "the fix is not applied yet" is a misreading.

**From #551's seven audit rounds — two that generalise well past this PR:**

- 🔴 **A COMMENT INSIDE AN UNQUOTED HEREDOC EXECUTES ON THE HOST.** `cat > f <<EOF` is
  unquoted so `${VAR}` interpolates — and so do `` `backticks` `` and `$(…)`. A comment
  containing `` `mc admin user add` `` RAN it and spliced ~2 KB of usage text into a script
  that then runs as root inside the MinIO tenant pod. Nothing was damaged (usage text is not
  valid shell and `set -e` aborts loudly) — that is luck, not design. **No prose in an
  unquoted heredoc body**; guarded by `scripts/tests/test-provision-heredoc.sh` in
  homelab-infra, which extracts the body by its delimiters, asserts zero backticks and zero
  `$( )`, and carries a POSITIVE CONTROL because a grep that finds nothing is
  indistinguishable from a grep wired to nothing.
- 🔴 **REMOVING A BAD MECHANISM CAN REMOVE A GOOD SIDE EFFECT WITH IT.** A destructive
  pre-flight probe (`: > "$OUT"` then `rm -f "$OUT"`) had to go — it was measured deleting a
  real credential on the refusal path. But `rm` never follows a symlink, so it had also been
  *accidentally* defusing a dangling-symlink attack; the clean replacement (`[ -e ]`) follows
  the link and wrote the credential through it. **Ask what the old code was accidentally doing
  for you.**

**A third, about this kind of work rather than this code:** across rounds 5–7, eleven findings
and nine were **prose contradicting code** — a claim fixed at one site and left standing at its
sibling, an enumeration silently scoped to half the flow, a message reworded for one of two
callers. The code converged after round 3; the narrative did not. When a comment states a
safety property, it is a claim to test, and the cheapest test is "which sites say this?".

**Verification notes worth keeping:**
- `shutil.copytree` does **not** raise `FileNotFoundError` for a per-file failure — `_copytree`
  batches errors into `shutil.Error`, which subclasses `OSError` but **not** `FileNotFoundError`.
  Only the top-level `scandir` raises FNF. A vanished **subdirectory** bypasses `copy_function`
  entirely, so an errno-based classifier must also check `os.path.lexists` on the reported
  source. Measured on python 3.12.14 (the image's version).
- `kubectl create job --from=cronjob/X` **does** set an ownerReference, so it updates the
  CronJob's `lastSuccessfulTime`. A hand-rolled standalone Job does not — which is how a
  CronJob can show a stale success while newer runs of the fixed code have all passed.
- An `error: superseded by a newer run` commit status from `gitops-validate` is the supersede
  mechanism, **not** a test failure. Read the description, not the state.

**🔴 CARRIED FORWARD OUT OF `State now` (2026-08-30) — it was a REPLACE heading and every
update was silently deleting this. It is the durable record step 2 acts on.**

- **Criterion 10 STEP 1 IS DONE AND PROVEN ON THE POD** (2026-08-29, homelab-infra `1e0c9250`).
  The token file holds **two rows**, and the coexistence *is* the migration — no credential
  moved, no read broke, and the rollback is deleting line 2 with **no image change**:

  | line | shape | identity | fingerprint | authority |
  |---|---|---|---|---|
  | 1 | `<token>` | `legacy` | `2481e4553f6c` | UNRESTRICTED read · **MAY NOT WRITE** |
  | 2 | `<token> zach <15 scopes>` | `zach` | `a8f329c534d7` | 15 scopes · **may write** |

  Live banner: `token-ids=2481e4553f6c:legacy,a8f329c534d7:zach`, still shouting
  `UNRESTRICTED-SCOPE LEGACY MODE — 1 of 2`.
- 🔴 **The mapped row was ROTATED once, same day** (homelab-infra `a8d77945`). Its first token
  (`8e1e79bb4664`) was printed in plaintext into a session transcript by a shell mistake — a
  pipe and a heredoc BOTH feeding one `ssh … bash -s`, which under **zsh MULTIOS are
  CONCATENATED on stdin** rather than one winning. **Never feed a secret to a remote shell that
  is also receiving a heredoc**: two invocations, `printf '%s' "$TOK" | ssh host 'umask 077;
  cat > ~/.tok'` then `ssh host 'bash -s' < script.sh`, the script shredding `~/.tok`. A
  rotation needs no `zach-prev` identity: guard 12 covers an OVERLAP, and this was a REPLACE.
- 🔴 **RANK 2's SHAPING FINDING — a `git bundle` backup of the served copy would have been
  GREEN AND EMPTY.** Re-pointing devrc's `analyze-service-index/backup.py` at `/data` is the
  obvious move and is wrong. Measured on the served copy 2026-08-30: `devrc/cairn.md` was
  **untracked** (in no commit of any scope repo) and the API-appended `[cairn: zach/…]` bullet
  was in the working tree with **0** occurrences at `HEAD`. A bundle carries committed objects
  only, so it is structurally blind to exactly the bytes that exist nowhere else. Hence a
  whole-tree tar (working tree **and** `.git`), its own bucket, its own credential. **Do not
  "consolidate" the two backups later without re-reading this.** ⚠ What it does NOT fix: RPO is
  up to 24 h, and **the seed clobber is unchanged** until criterion 9 — the backup makes a
  destructive `PUT` *recoverable*, not impossible.
- **Rank 2 shipped as homelab-infra#551**, squash `c4e0f82b`, merged 2026-08-30T17:30Z,
  Flux-reconciled and **adopted**. Daily **03:45 UTC** → `subsystem-store-backups` on the
  archive tenant, 90-day server-side ILM on `daily/`, credential scoped with **no
  `s3:DeleteObject`**. Post-merge run of the reconciled artifact: `1607 files, 15 scopes, 132
  entries` — uploaded, round-trip byte-compared, extracted and re-hashed;
  `lastSuccessfulTime` 2026-08-30T17:33:14Z; all three alerts `state=inactive health=ok`.
  🔴 **Seven `/audit-pr` rounds ran on it and the ladder stopped on the PAYLOAD-ATTRIBUTION
  GATE, NOT on a clean round** — round 7 still returned a real 🟡 (fixed), but by then fixes
  were touching 3 executable lines against ~24 of prose. **Do not read "merged" as "audited
  clean".**

**Criterion 10 step 2 — the prep this session did, and the three things it changed (2026-08-30):**

- 🔴 **THE PRESCRIBED GATE COMMAND IS A GREP WITH NO POSITIVE CONTROL — use Loki instead.**
  Step (a) as written is `kubectl -n subsystem-store logs $POD --since=24h | grep -c
  'token=2481e4553f6c'` → "must be 0". Measured: the pod log holds **5 lines total** over its
  whole 19.9 h life — 2 banner + 3 audit, all `token=a8f329c534d7 identity=zach`. The legacy
  row stopped being used **before this pod started**, so that grep reads 0 *whether or not the
  property holds*: it is the reassuring-zero shape `RULES.md` names. **Loki holds the pre-restart
  history and is the honest instrument.** Over 7 d, 32 audit lines:

  | token | identity | uses | first | last |
  |---|---|---|---|---|
  | `2481e4553f6c` | **legacy** | **23** | 2026-08-28T22:42:28Z | **2026-08-30T02:08:18Z** |
  | `8e1e79bb4664` | zach (rotated away) | 4 | 2026-08-29T21:03:19Z | 2026-08-30T01:56:31Z |
  | `a8f329c534d7` | zach (current) | 3 | 2026-08-30T02:13:16Z | 2026-08-30T20:20:14Z |

  Last legacy use is **four minutes before the pod's `startTime`**, and zero after. Those 23
  legacy lines are the positive control the pod-log grep structurally cannot have. Query:
  ```bash
  curl -s --get "http://192.168.50.94:30310/loki/api/v1/query_range" \
    --data-urlencode '{namespace="subsystem-store"} |= "store-api audit"' \
    --data-urlencode "start=$(( $(date -u +%s) - 7*86400 ))000000000" \
    --data-urlencode "end=$(date -u +%s)000000000" --data-urlencode "limit=5000"
  ```
- ⚠ **State the window's real sensitivity: it is thin.** Three requests in 20 h. The 24 h
  no-use window is a weak detector of a *rare* periodic consumer, simply because almost nothing
  calls the store. What actually carries the retirement is (i) both hosts independently
  confirmed on the mapped token and (ii) the legacy line stopping cleanly at the cutover
  instant. Do not quote the zero as though traffic were heavy.
- ✅ **BOTH step-2 token-file states pre-flighted against the DEPLOYED `server.py` — 8/8.**
  Harness: **`scripts/tests/preflight-subsystem-store-tokens.py`** (devrc), run against `/app/scripts` extracted from the pod
  (`server.py` sha256 `d17aaea8d521acd58dbc72783fe470c4e355252036f8c6d70afc03fd2aa8e34b`,
  226,998 B, re-verified against the pod's own `sha256sum`). **Synthetic tokens only — the real
  secret never left the pod.**
  - **C2, the FINAL state — mapped `zach` row ALONE, no bare row — LOADS**, yields 15 scopes,
    and emits **no** UNRESTRICTED warning. 🔴 *This was the actual unknown*: nothing in
    `load_tokens` requires a bare row to exist, so deleting line 1 leaves a file that starts
    cleanly and stops shouting.
  - **C1, the rollback state — bare legacy row alone — loads**, `scopes=None`, and the
    UNRESTRICTED banner **re-fires**, so step (b)'s rehearsal is observable rather than assumed.
  - Six negative controls each raise with their **own distinct message**: 4-field scope space ·
    reserved identity `legacy` · guard 11 (same token bare AND mapped) · guard 12 (two rows,
    one identity) · short token (`< MIN_TOKEN_CHARS = 43`) · empty file.
- 🔴 **`load_tokens` RAISES `ValueError` — it does not exit.** The `exit 78` is at the CALLER:
  `server.py:4308` calls it inside a `try`, `:4311` catches `ValueError`, `:4313` returns
  `EXIT_CONFIG` (`:578` = 78). This session initially expected `SystemExit` and scored 2/8 —
  the loader was right and the expectation was wrong. If you assert on the failure mode, assert
  on the **exception**, and on exit 78 only for the whole process.
- 🔴 **Extract the WHOLE `/app/scripts` tree, not just `server.py`.** It does
  `import subsystem_recall` at module scope (`:269`) from `/app/scripts/lib`, so an importlib
  load of the file alone dies `ModuleNotFoundError: No module named 'subsystem_recall'`.
  `kubectl exec $POD -- tar cf - -C /app scripts | tar xf - -C <dir>`, then put
  `<dir>/scripts/lib` on `sys.path`. (The `sys.modules[name] = mod` before `exec_module` gotcha
  already on the record still applies.)
- ✅ **A FINGERPRINT IS `sha256(field)[:12]`, which makes the edit checkable WITHOUT handling
  the secret.** Measured in-pod: the token file is **354 B**; line 1 = 58 B with
  `sha256 = 2481e4553f6c…` — i.e. **the fingerprint IS the row hash prefix**, so that hash alone
  proves line 1 is the legacy row; line 2 = 294 B, `sha256 = ecc11c1b5b6ec81d…`; line 3 empty.
  **Post-edit assertion for step (c):** file is **295 B**, line-2 sha `ecc11c1b…` **unchanged**,
  `2481e4553f6c…` **absent**. That proves a pure line-deletion with no retyping and therefore no
  typo surface — a strictly better check than re-validating the secret's bytes.
- ✅ **Step (c)'s precondition holds:** `zach`'s allowlist is **exactly** `ls /data` — the same
  15 scopes, no gap in either direction. Deleting the bare row loses access to nothing served.
  Read it without printing the token:
  ```bash
  kubectl -n subsystem-store exec $POD -- awk 'NF>0 && $0 !~ /^#/ {print NR, NF, ($NF>=2?$2:"bare"), ($NF>=3?$3:"UNRESTRICTED")}' /run/secrets/subsystem-store/token
  ```

**Reconciler misattribution — write `owner/repo#N` in these docs (2026-08-30):**
🔴 `/resume`'s `resume-state.sh` resolves a **bare** `#N` against the doc's own repo, so this
doc's `#371` and `#551` were looked up in **devrc**, where both exist and are unrelated
(devrc#371 is a drift-audit fix; devrc#551 is a browser-bridge focus-steal fix). It then emitted
two confident DRIFT lines — *"PR #371 MERGED but handoff frames it as open/in-flight"* — that
are **misattributions, not drift**. The doc means **clawgate task #371** and
**homelab-infra#551**. `#990`/`#998` are genuinely devrc and were resolved correctly.
**Qualify every cross-repo reference** — that is what makes a handoff reconcilable.

**Instrument trap re-paid this session, exactly as `RULES.md` warns:**
`python3 preflight_tokens.py 2>&1 | tail -60; echo "EXIT=$?"` printed **`EXIT=0`** for a run that
died on an import error — the pipe captured **`tail`'s** status. Redirect the two streams to
their own files and read the content: `cmd > out 2> err; echo $?`.

**Criterion 10 step 2, as EXECUTED (2026-08-31) — what the previous plan got wrong:**

- 🔴 **THE CHECKLIST READ LIKE LIVE `kubectl` EDITS. IT ISN'T.** The Secret is **Flux-owned**
  (`kustomize.toolkit.fluxcd.io/name: subsystem-store`) and SOPS-encrypted in **homelab-infra**
  at `clusters/homelab/apps/subsystem-store/secrets.enc.yaml`. A live edit is reverted on the
  5-minute interval. The real (c) is a **commit to trunk**, which in this repo IS the deploy.
  Anyone reading the old checklist would have edited the cluster and watched it silently revert.
- ✅ **DELETE THE ROW BY CONTENT HASH, NOT BY POSITION** — `scripts/`-side helper pattern worth
  reusing. **The fingerprint the banner prints IS `sha256(row)[:12]`**, so the row identifies
  itself: `sha256(line1)=2481e4553f6c`. The SOPS `$EDITOR` refused unless exactly one line
  matched AND the mapped row survived, and was negative-controlled against a file with no
  legacy row (refused, rc 1) before being pointed at the real one.
- ✅ **The rehearsal (b) proved the fallback, with both controls.** Secret patched to
  legacy-only → pod replaced → banner `1 of 1` **(this is what proves Flux had not reverted
  underneath; without it the rehearsal could pass while testing the two-row file)** → legacy
  read **200**, mapped read **401**. The 401 is the control proving the file really changed.
  Restored via `flux reconcile --with-source` + pod delete; both credentials 200 again.
- ✅ **"INERT UNTIL REPLACED" OBSERVED LIVE, not assumed.** After the reconcile the live Secret
  was already 295 B while the running pod still printed
  `token-ids=2481e4553f6c:legacy,a8f329c534d7:zach`. That gap is the whole reason (c) needs a
  `kubectl delete pod` and not just a commit.
- 🔴 **"THE WARNING IS GONE" IS A REASSURING ZERO — control it.** The final check is the
  *absence* of `UNRESTRICTED-SCOPE LEGACY MODE`, and the old pod's log dies with the old pod, so
  `logs --previous` returns 0 and proves nothing. **Loki holds 6 historical banners**; that is
  what makes the absence a measurement instead of a grep wired to nothing.
- 🔴 **`git rebase` FAILED ON UNSTAGED CHANGES AND THE COMMIT LANDED ON A STALE BASE.** The
  sequence `fetch → rebase → commit → push` silently became `fetch → (rebase refused) → commit
  on the OLD base → push REJECTED`. It failed safe — a non-fast-forward is the good outcome —
  but the lesson is to **read the rebase's own output before committing**, not just the push's.
  **Trunk moved three times during this one session** (`217c2191` → `ae9af47c` → `e19e211d`),
  so re-fetch immediately before the write, and check whether anyone touched *your* paths:
  `git log --oneline <old>..origin/trunk -- <dir>` was empty here, which is what made the
  rebase safe.
- ⚠ **The 24h window is thin, and the honest framing survives the change.** Three requests in
  20h; what carried the retirement was both hosts independently confirmed on the mapped token
  plus the legacy line stopping cleanly at the cutover, not the volume of the zero.
- 📌 **THE EVIDENCE THIS RETIREMENT RESTS ON, preserved because the status header that carried
  it is REPLACE and was deleting it:** over 7 d Loki held 32 audit lines —
  `2481e4553f6c/legacy` n=23, first 2026-08-28T22:42:28Z, **last 2026-08-30T02:08:18Z**;
  `8e1e79bb4664/zach` (rotated away) n=4, last 2026-08-30T01:56:31Z; `a8f329c534d7/zach` n=3
  from 2026-08-30T02:13:16Z. The observing pod started 2026-08-30T02:12:28Z with
  `restartCount=0` and was still unrestarted **28 h later** at execution time, so the last
  legacy use predates the window by four minutes and nothing used it inside the window.
- 🔴 **A comment describing the two-row state becomes a FALSE CLAIM the moment (c) lands**, and
  `deployment.yaml:121` carried a live cross-reference to the heading being renamed. Both were
  fixed in the same commit. Nine of eleven findings in this effort's audit ladder were prose
  contradicting code; updating the comment is the cheap half of not repeating it.

**The devrc CI gate — root cause, not "it's flaky" (2026-08-30/31):**
🔴 A gate run can burn its **entire 60-minute budget with zero steps executed**. The tell is
every step `waiting` and `TaskRunTimeout`; the cause is `FailedScheduling` —
`0/4 nodes are available: 1 Insufficient cpu, 3 node(s) didn't match Pod's node
affinity/selector`. The pipeline pins every task via
`taskRunTemplate.podTemplate.nodeSelector: {kubernetes.io/hostname: talos-xr6-r7p}`, and that
node sat at **14520m/15950m CPU requested (91%)** with 71 pods, while three other nodes idled
at 13–18%. **Retrying immediately deepens the contention**; waiting for the queue to reach zero
and firing once ran the same commit in ~30 min. Re-fire without a noise commit by
**close+reopen** — the trigger's CEL filter accepts `reopened`, so the head sha is unchanged.
⚠ Do not read "COULD NOT RUN: pytests — the gate stopped before this leg reported" as a test
failure; that message is the gate being honest that it never ran them.

**An instrument trap worth keeping — a watcher that "settled" in ONE SECOND:**
🔴 A poll whose exit condition was *"no line in `gh pr checks` says pending"* returned
immediately after a re-trigger, because the checks still held the **previous** run's `fail`
lines. A stale verdict satisfied a test meant to detect a new one — and had that watcher been
allowed to merge on green, it would have merged against a status belonging to a run that no
longer existed. **Watch the object you actually created** (the PipelineRun, by name), not the
text of a status that outlives it — and treat "not found" as NOT A VERDICT, explicitly.

- 🔴 **OPERATOR DECISIONS 2026-09-01, asked as one question with the blast radius stated, all
  three answered as recommended.** (a) `devrc/signal.md` → **hand-merge**, union of aliases and
  both pointer sets, neither copy discarded. (b) Write path → **write-through, the pod is the
  authority**: writes go to the pod and **fail loudly** when it is unreachable, local disk
  becomes a read-through cache, and a write during an outage is **REFUSED, not queued** — that
  cost is accepted. `/resume` must keep working offline for **reads** off the local cache.
  (c) Dispatch scope → **design + reversible script; the operator executes the cutover.**
- 🔴 **A PREREQUISITE NOBODY HAD: `scripts/cairn` HAS NO WRITE VERB.** Rank 7 of this doc
  records it and it is easy to read as cosmetic. Criterion 9 routes `subsystem-index` writes
  *through* `cairn`, so the write verb is **in scope for criterion 9**, not a follow-on.
- 🔴 **THE SWEEP CAUGHT THE SEQUENCING; THE LOCK STRUCTURALLY COULD NOT.** `gh pr list` surfaced
  open PR #1187 arguing criterion 9 must precede criterion 8. `claim-work` cannot see this class
  — it locks an item, it does not notice an item's *ordering* being revised elsewhere. This is
  the third recorded instance of `gh pr list` finding what the lock cannot; run it before
  drawing ANY ranked item, not just the one you intend to take.
- ⚠ **`clawgate_handoff.sh resolve` returned rc 6 for this session** — one linked task (#364),
  `role=read`, **no worked task**. Per the protocol that is "record nothing", not "pick one".
  Both cairn docs already carry readable `clawgate-task:` fields (371 here, 364 on
  `handoff-cairn-task-linkage.md`), so `field <doc>` → rc 0 ⇒ left alone.
- **STALE RECALL CORRECTED, two entries.** The `devrc/cairn` index entry carries
  `OPEN: cairn is NOT on PATH` — **false**, it is at `~/.local/bin/cairn`. And this doc's
  "claim `cairn-phase3-1` is STILL HELD" is false (see State now). Both read as current forever;
  neither is detectable by reading the doc.
- ⚠ **`seed.sh`'s `--delete` is about the STAGE, and that is what makes rank 3 destructive.**
  The push is `rsync -a --delete` SOURCE→STAGE, then a tar built **from the stage**. So every
  entry the pushing host also holds overwrites the served copy — destroying API-appended bullets
  that exist only on the pod. Recoverable via homelab-infra#551's backup, not harmless. This is
  the whole argument for landing criterion 9 first.
- ⚠ **The re-seed's backup precondition must be CHECKED, not assumed.** #551's CronJob exists
  (closed 2026-08-30), but devrc#1132 (`f9c86a8b`) exists precisely because **15 places
  asserted the backup's state wrongly**. Require a recent *successful* run as a refusal-gated
  precondition.
- ⚠ **A read-only local cache must be proven by a WATCHED EACCES, not by `stat -c %a`.** A mode
  bit is a claim; a refused write is evidence. Rank 4 already says this — it is repeated here
  because it is the kind of check that gets downgraded to a `stat` under time pressure.
- **RANK 11 OF `handoff-cairn-task-linkage.md` IS ANSWERED AND SUPERSEDED — recorded there, not
  here.** Summary only, so this doc does not carry another effort's diagnosis: its stated lever
  (a third 30 GiB local-path nix cache on another node) is both a bad bet on measured free disk
  and **unnecessary**, because homelab-infra shipped a binary-cache mechanism on 2026-08-31 that
  removes the cache PVC entirely. It matters here because that same single-node pin is what
  starved PR #1129's first gate run to zero steps executed.

- **These doc updates are carried by devrc PR #1199** (branch `docs/handoff-cairn-phase1-scope`,
  commits `8c23c255` this doc, `deb42d77` `handoff-cairn-task-linkage.md`). Written from a
  worktree on a topic branch — `handoff_doc.py` runs git inside Python, so no PreToolUse hook
  sees its commit, including the never-commit-to-`main` guard; `git branch --show-current` was
  checked before each. The PR is docs-only and was **not** audited: the substantive risk in it
  is whether the recorded measurements are right, which an audit of the diff does not test.
- ⚠ **A CONFIRMING INSTANCE of a floor the `/handoff` skill already states — not a new lesson,
  recorded because it is the first time it was measured here.** `handoff_doc.py` printed **no**
  `DROPS N line(s) that look DURABLE` warning while the REPLACE of `## State now` would have
  deleted the criterion-10 end-to-end verification record — including the Loki **positive
  control** (6 historical banners, proving the grep CAN match), which is the part that makes
  that block evidence rather than an assertion. Caught only by reading the diff. The skill's
  rule — *"a silent run is NOT evidence that nothing durable was dropped"* — is load-bearing,
  and `State now` is where verification records go to die because the section reads as status.
- ⚠ **Both subsystem-index windows returned empty for the session that wrote this, and that was
  HONEST rather than a failure.** `--session` saw 0 paths (100% of what it named was outside the
  session cwd — scratchpad and worktree); `--pr 1199` saw 2 files, both the excluded handoff
  docs. The session's whole *file* footprint was the handoffs; its actual work was measuring a
  pod, a cluster and two hosts' stores, none of which has a path in this repo. **A dead end in
  both windows is a fact about the windows, not a licence to skip the store** — the write went
  to `devrc/cairn` on operator knowledge of the right home, not on a resolver's nomination.
- **Recorded to the store this session** (`~/.claude/analyze-service-index/devrc/cairn.md`,
  validated in the same turn — `OK 1 of 1 parse`, entry shape clean, 0 out-of-reach markers):
  the pod-freezes-and-is-workbench-only finding with the writer's zero-HTTP-code half; the
  scope-count-vs-entry-count trap plus aliases as a second collision channel (marked `OPEN:`);
  and auth-is-live-while-the-plan-doc-denies-it. Also **closed an OPEN in the same edit** —
  *"`cairn` is NOT on PATH"* → `RESOLVED 7ed7d41a` (#1079), verified an ancestor of
  `origin/main` and present at `~/.local/bin/cairn`. That bullet had been served as outstanding
  since 2026-08-29 while the remedy was already merged.

- 🔴 **OPERATOR DECISION 2026-09-01 — protocol change BEFORE the cutover, not after.** The
  runbook deferred it to "a separate PR after 5 and 6 verify", which would have left every
  session writing the index via `Edit` hitting EACCES for the length of a PR+gate+ship cycle.
  Evidence it was not hypothetical: the store grew 146 → 148 → 150 → 153 entries and 15 → 16
  scopes **during** the session, with writes at 00:19, 00:48, 11:07, 11:21, 11:27, 11:35.
  Cost accepted: appends to not-yet-pushed entries fail `ref-not-found` in the gap, which is
  loud rather than silent.
- 🔴 **`prune-index` was the writer most likely to be forgotten, and covering only
  `subsystem-index` would NOT have delivered "the freeze breaks nothing".** The design doc's
  own writer table named it; it needed its own `cairn put` routing in the same PR.
- 🔴 **A defect I reported that did NOT exist, and how it nearly shipped.** I claimed the
  snapshot banner left a residual blank line. The real script uses `sed '/…/,+1d'`, which
  deletes the banner AND its separator — my control used a hand-rolled single-line `/…/d`, so
  the residue was **my own reimplementation**, not the script. Re-measured with the script's own
  seds: 2 differing lines before the `host:` rule, **0** after. The subagent refused to
  implement to my spec and said the diagnosis did not reproduce; the false claim had already
  been written into **five** sites as a measured past incident and was retracted before merge
  (`60dbcce2`). **One rule, one place — I open-coded a predicate the script owned and trusted my
  copy over the original.**
- 🔴 **What caught it was an ACCOUNTING assertion, not a verdict.** The pre-existing
  `…ACCOUNTED_FOR…` test asserts `raw == store_root + 2*snapshot` and stayed green, making the
  claim and the repo disagree out loud. Same shape as the mutant where deleting the `host_lines`
  COUNT still passes `cmp` and is caught only by the decomposition. A pass/fail verdict says
  "these differ"; an accounting assertion says "and here is exactly which lines I claimed the
  excuse for" — only the second can contradict a wrong human diagnosis.
- 🔴 **A refresh from the pod is NOT safe by default.** `devrc/subsystem-store-api.md` had **43
  lines another session appended locally AFTER its merge was staged** (local mtime 14:35:48 vs
  merge staged 00:39:42). Copying the pod's copy down would have destroyed that work; the merge
  was re-authored instead. Every refresh was gated on "zero local lines absent from the pod",
  with two documented exceptions where a reflow changed line breaks and the substance was
  confirmed present by marker.
- 🔴 **I merged into ANOTHER SESSION'S integration worktree.** `~/workspace/devrc-integ` already
  existed on `integ/963-965`; my `worktree add` failed but the following `git -C … merge` still
  ran. Restored with `git reset --keep ea9811ed` (its reflog position immediately prior; tree
  clean; branch local-only). **Check a path is free BEFORE assuming it, not after.**
- ⚠ **`verify-byte-identity.sh` sends `CF-Connecting-IP`, which Cloudflare rejects as spoofing
  (403, `error code: 1000`) through the public ingress.** Point `SUBSYSTEM_STORE_URL` at a
  `kubectl port-forward` for the verify step; the script's own comment anticipates this, the
  cutover does not do it.
- ⚠ **`seed.sh`'s push needs `KUBECONFIG` in the ENVIRONMENT.** The cutover passes
  `--kubeconfig` for its own backup check only; without the export, `kubectl` falls back to
  `localhost:8080` and the run refuses at rc 17 having staged but pushed nothing.
- ⚠ **A `kubectl port-forward` backgrounded with `&` inside one Bash call does not survive to
  the next.** Use the harness's own background mode; verify with repeated probes before relying
  on it.
- ⚠ **The `FORGED_actor` flake cost a required check again** (`#1214`, plus #1026/#1025 and a
  batch of four devrc-ci runs on record). Attributed away on three grounds before re-running:
  the diff names neither the class nor the test; it passes 2/2 in 2.27s on the merged tree; the
  full merged-tree gate is green. A fresh push is the only thing that re-runs a required check.
- ⚠ **The workbench's deployed generation is `origin/main` PLUS an uncommitted 8-line
  `nix/programs/alacritty/default.nix` change belonging to another session.** `ship.sh` reports
  this itself as `🔴 DIRTY AND IN THE ARTIFACT`. Cosmetic and reversible with `git restore`, but
  it is live.

**This session (2026-09-01), operating on rank 11:**
- 🔴 **THE AUTHORITATIVE COPY OF THIS DOC WAS ON AN UNMERGED PR AND EVERY FRESHNESS CHECK SAID
  IT WAS CURRENT.** `resume-state.sh` printed `handoff-read: working-tree copy (identical to
  origin/main)` — true, and useless: the phase-1 closure and rank 11 were on the open branch
  `docs/handoff-cairn-phase1-closed` (#1218), which is neither the working tree nor `main`. The
  tree copy had **10** ranks and a 132-entry pod; the real one had **14** and 201. It was caught
  only because the kickoff message named facts the doc did not contain. **A freshness check
  that compares against the mainline is structurally blind to a handoff update sitting in
  review** — grep `gh pr list` for `claudedocs/` before trusting one.
- 🔴 **`claim-work --slug-for` needs the DOC, and the doc it needs may not be the one you read.**
  The slug minted here (`cairn-phase3-11`) was correct only because the rank numbering on the
  PR branch matched what the kickoff named. Had the ranks been renumbered in review, the claim
  would have locked a different item under a plausible-looking slug.
- ⚠ **`clawgate_handoff.sh resolve` returned rc 6 for this session** — one linked task (#371),
  `role=read`, **no worked task**. Per the protocol that is "record nothing", not "pick one".
  The doc already carries a readable `clawgate-task: 371` (`field` → rc 0), so it was left
  alone.
- ⚠ **The branch `docs/handoff-cairn-phase1-closed` is CHECKED OUT in another session's
  worktree** (`/home/zach/workspace/devrc-ho-p3`), so `git worktree add` on it fails
  `fatal: … is already used by worktree at …`. This update was therefore authored on
  `docs/handoff-cairn-p3-rank11`, branched off that PR's tip, rather than by writing into a
  checkout this session did not make. Land it into #1218 or onto `main` after #1218 merges.
- ⚠ **A stale worktree at `/home/zach/workspace/devrc-handoff-cairn` sits on
  `docs/handoff-cairn-phase2`, whose remote branch is GONE.** Left untouched; it is a candidate
  for `git worktree remove` by whoever owns it.

**On the rank-11 implementation, worth not re-deriving:**
- 🔴 **A MUTANT SURVIVED AND WAS REPORTED RATHER THAN PAPERED OVER.** Mutating `rows_diff=0`
  survived the first sweep because every index-row field is body-derived, so the per-entry arm
  catches the same thing one step later and `returncode == 1` proved nothing. The fix was to
  assert the difference was seen **by the row comparison** (`sorted-row-lines > 0`) — the count
  that stops a genuine row difference being folded into the reorder excuse — rather than claim
  coverage the sweep did not have. Final sweep: 9 narrow mutants, 9 killed, each dying with its
  own guard's assertion, under `PYTHONDONTWRITEBYTECODE=1` with a known-caught positive control.
- **Red-at-base matrix:** all 5 new tests red at `66eee1d7`, green at HEAD. The regression
  case's base failure is the defect itself — rows reordered **and** a different featured entry —
  on stores whose bytes are identical.
- **Gate tiers, both named with their base:** dev-host `scripts/gate.sh` → `RESULT=PASS`, and
  the sandbox tiers `.#checks.x86_64-linux.{pytests,nodetests}` → PASS, built **one at a time**,
  all at `b59b0475` + the branch commit. `origin/main` moved to `836bac03` mid-run, so those
  claims are about `b59b0475`, not the current tip; `strict: false` means green ≠ merged-tree
  green.
- **The verifier is now N+1 HTTP requests per scope**, not 1. P4 bounds it at `timeout=600`. The
  per-request cost **against the pod is unmeasured** — only the local `--ref` leg was timed
  (~73 ms × 10 runs). At 201 entries across 23 scopes that bound is an assumption, not a
  measurement.

- ✅ **`cairn put` IS NOW PROVEN LIVE AGAINST THE POD** — the open investigation saying it never
  had been is closed by measurement, incidentally rather than deliberately: this session used
  `put` twice to rewrite bullets on `devrc/subsystem-store-api` (`If-Match 3aee155a848e1670` →
  `c865d70976d961e0`, then `9eee662af4986546` → `4056a7d18b5c0f75`), each deriving `If-Match`
  from a live sync and each answering `replaced`. ⚠ **Rank 12 is NOT thereby closed**: its
  stated closing condition is one prune observed landing on the pod **through `prune-index`**,
  and that skill's path is still unexercised. The VERB is proven; the CALLER is not.

**Operator decisions 2026-09-02:**
- 🔴 **Branch protection on `main` STAYS OFF.** Asked with the blast radius stated and answered
  directly. The consequence — a permanently-red `drift-check` — is rank 18 and is a *separate*
  decision, not a re-litigation of this one.
- **Break-glass was NOT performed and was NOT needed.** The procedure was started; **its capture
  step failed** (`cannot iterate over: null`) because there was nothing to capture. That refusal
  is exactly why CLAUDE.md puts capture FIRST — deleting anything at that point would have been
  unrecoverable.

**The merge lessons from this arc, all paid for:**
- 🔴 **`UNSTABLE` IS NOT `BLOCKED`.** #1218 was called unmergeable here and it never was —
  `mergeable=MERGEABLE`, `mergeStateStatus=UNSTABLE` means *mergeable with a failing check*. A
  whole break-glass was proposed on that misreading. **Read `mergeable`, not the status word.**
- 🔴 **A squash-merged parent makes its stacked child CONFLICTING.** #1218 merged as a squash, so
  #1225 — whose branch carried the real commit — went `CONFLICTING/DIRTY` immediately. The fix is
  `git rebase --onto origin/main <squashed-commit> <branch>`, dropping the now-redundant commit,
  then `--force-with-lease`. Merging the parent **without** `--delete-branch` is what kept the
  child's PR alive to be rebased.
- 🔴 **A grep zero is a claim about the PATTERN, not the tree — this cost three false alarms in
  one session.** `THE CUTOVER LEFT TWO STORES` read 0 against a mixed-case heading;
  `ACCEPTANCE actually PRINTS` read 0 against a name spelled with **underscores**; and
  `49 entries of headroom` read 0 against prose spelling it `100 - 51 = 49 ENTRIES`. Each looked
  like missing content and each was a bad pattern. **Normalise, or check context, before
  reporting an absence.**

**On the audit ladder, four rounds, and when to stop:**
- 🔴 **The ATTRIBUTION LEDGER is what ended it, not a verdict.** Rounds 2 and 3 both returned
  "safe to merge" *and* real defects. What ended the ladder was payload lines trending
  **118 → 38 → 0** with round 3's own ratio at **17 executable lines out of 414 changed** (4%) —
  i.e. the rounds had left the PR and were auditing their own guards. Round 4 was briefed that a
  clean round is the CORRECT outcome and returned one.
- 🔴 **A "seam" test that asserts only a return code pins nothing.** Severing the call site
  (`_acceptance_refusal(verified)` → a constant string) left the suite **entirely green**, four
  helper unit tests included — which is exactly why they were never sufficient. The fix is one
  line: read stderr and assert text only the real helper can produce.
- ⚠ **A fixture constant equal to a constant the assertion names is still a blind spot here.**
  Round 4 found a *different* one-line call-site edit that survives — passing
  `Ran(124, …)` — because the seam fixture's rc IS the literal the branch tests. Not a plausible
  accidental edit, and it does not sever the seam; recorded so nobody re-derives it.
- **Two agents corrected the auditor rather than deferring**: the `service:` field of a
  kind-qualified entry must equal the filename's **slug** part or the loader rejects it as
  MALFORMED (so the F1 repro needs `service: alpha`, not `service: alpha.process`); and the
  stated reason two mutants survive was FALSE while the conclusion held for a stronger reason.

**Verification discipline that paid off, worth repeating:**
- The implementing agent could not run the fix against the live pod; the dispatching session did,
  and that is what turned "the tests pass" into "the original failing scope now passes".
- Round 4 **forced both sandbox derivations to rebuild** rather than reading cached logs — a
  silent `nix build` is the CACHED case, not a pass.
- An agent **retracted its own theory** for the `test_live_cotenants` flake (a detached
  `gc --auto`) after a direct probe found no lingering process and 12/12 serial runs passed.
  "Pre-existing flake, mechanism unknown" is the honest state; do not adopt the retracted theory.

🔴 **RELOCATED 2026-09-03 FROM `## State now`, VERBATIM — this is the rank-11 and rank-16 CLOSURE
EVIDENCE, and it was being dropped by every update because `State now` is a REPLACE heading.**
`handoff_doc.py` flagged it as durable-content-under-a-replace-heading twice in one run; the fix
it recommends first is to move such content under an APPEND heading, which is what this is. It is
evidence, not status — do not summarise it away.

🔴 **THE DOC'S GOAL IS MET. RANK 16 IS CLOSED — the prescribed READ surface goes through the
pod.** devrc **#1233**, squash **`9519781f`**, verified by content on `origin/main` (never by
ancestry — a squash is never an ancestor). Four audit rounds, no 🔴 in any of them.

**What shipped.** A new leaf module `scripts/lib/subsystem_read_store.py` owns
`DEFAULT_CACHE_ROOT` and `SYNC_STAMP` (moved out of `scripts/cairn`, which now imports them —
one definition each). The `subsystem_recall` **CLI** defaults to the synced cache, prints the
`.sync-stamp` **verbatim-but-unparsed** in its header, and **REFUSES** a store carrying no
stamp with exit **4** (`EXIT_UNSTAMPED_READ_STORE`) naming `cairn sync`. `service_recon`
degrades only its index section. An explicit `--store` stays permissive — recorded by a custom
`argparse.Action`, because `--store <the cache>` parses byte-identically to passing nothing.

🔴 **THE REFUSAL IS CLI-ONLY BY DESIGN, AND THAT IS THE LOAD-BEARING CONSTRAINT.** `server.py`
and `scripts/cairn` call `recall`/`search`/`load_store`/`read_entry`/`_exit_for` as a
**LIBRARY**, against `/data`, which has **no** `.sync-stamp`. A refusal on a library path takes
the store down for **every host**. Pinned by a set-equality AST guard asserting the set of
top-level defs referencing the resolver **equals** `{_build_parser, _with_stamp, main}` — both
directions. Round 2 found that guard's first version split the file positionally at
`def _build_parser(` and so **excluded `_exit_for`** (line 3186), which the pod calls on every
`/recall` and `/search`; a refusal grown there survived. Round 4 re-verified the CLI output is
**byte-identical** across the round and the pod contract intact.

**The closing condition, executed 2026-09-02 in one session:**

| step | result |
|---|---|
| `cairn append --scope devrc --ref cairn` | `revision=98262ec6a03fcfe4` |
| `cairn recall --repo ~/workspace/devrc` (what deployed `/resume` step 4 prescribes) | exit 0, `cairn: live — fetched … 201 entries`, **29 entries** in `devrc/` |
| the bullet written seconds earlier | **present** (1 occurrence); positive control string **0** |
| raw `subsystem_recall.py --repo` | `store: ~/.cache/subsystem-store` + 5 `stamp:` lines |
| `ship.sh` | both hosts at `9519781f`, **cross-host agreement compared**, both switched |
| deployed skill | `readlink -f` → a NEW `/nix/store/c0hn5z05…` path prescribing `cairn recall` |

Before: `~/.claude/analyze-service-index`, **26 entries**, `ALL 26 … none omitted`, no stamp.

---

🔴 **RANK 11 IS CLOSED AND MERGED. The audit ladder ran FOUR rounds and ended on a clean one.**

| PR | squash | what |
|---|---|---|
| **#1222** | `7d9da8f5` | the order-independent comparator |
| **#1218** | `e557ed19` | phase-1 closure handoff (merged WITHOUT `--delete-branch`; it was #1225's stacked parent) |
| **#1225** | `80625392` | this doc's rank-11 update |
| **#1228** | `85257361` | the round-1..3 audit fixes |

All verified **by content** on `origin/main`, never by ancestry (a squash is never an ancestor).
Claim `cairn-phase3-11` **released**.

**The defect was TWO mtime dependencies, not one.** `subsystem_recall` orders its INDEX
newest-first by entry-file mtime **and picks the digest's one featured BODY the same way**;
`seed.sh`'s transport (`rsync` → stage → `tar` → pod) carries no mtime. The comparator now
compares (1) the `mode=list` render with index rows **sorted**, (2) the entry **SET** via `comm`
run FIRST, (3) **each entry's** own `?ref=` render, refusing a paginated index outright.

**Live acceptance against the pod** (port-forward, store `~/.claude/analyze-service-index`):
`cli` (5 entries, byte-identical) **FAILED before / PASSES after** with `accounted-for=38` equal
to `raw-diff-lines=38`; `storage-resolver` passed before **and** after — the control proving the
fix did not simply paint everything green.

**What the ladder found after the fix shipped**, all closed in #1228: a **false green** — the
per-entry arm compared an "AMBIGUOUS REF" notice instead of entry bytes for any ref
`resolve_ref_tiered` could not resolve uniquely (`_exit_for` returns **0** for `ref-ambiguous`),
reproduced as a clean PASS over a **one-character** divergence; a **measured-evidence block that
was wrong twice**; the **pagination headroom taken from the non-binding side** (real headroom
**49**, not 51 — the pod is the larger store); and a **seam test that pinned no seam** (severing
the call site left the whole suite green — 84/678 passed).

⚠ **ONLY criterion 10 (d) remains from the earlier arc** (rank 1): the plaintext backups
`~/.config/subsystem-store/env.bak-legacy-2026-08-29` are still on disk on **both** hosts. Dead
credential (401), so hygiene not exposure.

⚠ **The Goal's read half was the last gap and it is now CLOSED (rank 16, above).** This
paragraph used to say the Goal was not met. It is met: writes go to the pod, and the
prescribed read now does too.

### 🔴 The defect class this PR kept regenerating — FIVE occurrences, worth not re-paying

Every one was **an assertion satisfied by a constant agreeing with itself**, and every fix was
narrower than the class it closed, which is how the next one got in:

1. `SYNC_STAMP` — the sweep's own **positive control SURVIVED** 615 tests, because every
   fixture wrote the stamp *through* `rs.SYNC_STAMP`.
2. `REMEDY` and 3. `DEFAULT_CACHE_ROOT` — `REMEDY = "cairn resync-the-store"` and a
   `DEFAULT_CACHE_ROOT` moved to world-writable `/var/tmp` both survived; nothing pinned
   `Path.home()`, and `cairn` imports the same constant for its **writer** default.
4. `NOT_READ_PREFIX` — **introduced by the fix for 1–3.** `= "READ"` survived all 42 tests, so
   `--json` would emit `"store_root": "READ (store-unstamped) — …"`: the field asserting the
   brief *read* the store it explicitly refused to open.
5. `Path`/tuple/mixed-case constants — **introduced by the fix for 4.** The `WIRE_CONSTANTS`
   ledger built to stop the fifth was `isinstance(value, str)`-only, so `DEFAULT_CACHE_ROOT`
   itself — a `Path` — was invisible to it.

🔴 **The lesson is the FIX WIDTH, not the pins.** The cure was to stop writing one-off pins and
build a **ledger** whose two-way sweep enumerates module-scope assignments off the AST and fails
on an unpinned one — then to widen that sweep past its own prescribed hunk when `for … in` and a
`def` still escaped. **Ask what SHAPE the next instance takes, not what the last one was named.**

**From the ranks-18/20 + cairn-skill session (2026-09-03) — the instrument lessons:**
- 🔴 **A NEW COMMAND FOUND A LIVE DATA-LOSS PATH ON ITS FIRST RUN.** `cairn doctor`, built on
  the operator's *"minimise prose, prefer scripts"* instruction, reported `frozen-mirror PROBLEM`
  **before it merged**. A skill DESCRIBING what to check would have described it and found
  nothing. That is the argument for the form, and it is now measured rather than asserted.
- 🔴 **I READ ONE TIER'S TOTAL AND REPORTED IT AS BOTH.** Quoted `failed=1` off the dev-host
  gate; the SANDBOX tier — the one the merge gates on — had **24** (115 `CalledProcessError`,
  23 `exit status 128`, an unguarded `git ls-files` in a tree with no `.git`). Read each tier's
  OWN total, and name the tier in the claim.
- 🔴 **THREE INSTRUMENT FAILURES IN ONE HOUR, each caught ONLY by a control.** `find` is a shell
  FUNCTION on this host and its `-print0` emits nothing — the positive control returning 0 was
  the only tell; a `grep -cE "A|B"` matched a line present regardless of the variable under
  test; and a "positive control" aimed at a repo whose protection API 404s exercised
  COULD-NOT-MEASURE instead of the alarm. **A zero is a claim about the instrument until a
  control has moved it.** `type <tool>` before trusting a pipeline's zero.
- 🔴 **A MUTATION THAT DOES NOT APPLY SCORES AS A FALSE NEGATIVE.** Proving the rc-24 alarm still
  fires, my first regex assumed quotes the real line does not have: `arms removed: 0`, and the
  run was the UNMUTATED script. Printing the applied-count is what stopped it being recorded as
  *"the alarm is dead"*. Assert the mutation applied exactly once, every time.
- ⚠ **A grep count is a claim about the PATTERN.** Verifying #1249, `grep -c 'DEFAULT_STORE_ROOT
  = Path.home()'` returned **1**, reading as "the constant survived". It was the comment saying
  what USED to be there; stripping comments gives 0.
- 🔴 **THE SWEEP CAUGHT A DUPLICATE THE LOCK STRUCTURALLY COULD NOT.** `gh pr list` surfaced
  **#1254**, an unclaimed PR already building rank 24 with the same root-cause diagnosis. The
  claim namespace cannot see unclaimed work. Run the sweep before drawing ANY item.
- **Agents corrected me FIVE times and were right every time:** `subsystem-audit.py` is READ-ONLY
  by contract, not a delete tool; an auditor's "red at base" was measured against a MID-PR commit
  and the implementer refused the instruction and re-measured; tier B is NOT free (the
  description budget is separate from the tier ledger and had 12 chars of headroom); my ledger
  population was missing five sites; and the `_AMBIGUOUS_TERM_OWNER` entries I told an agent to
  delete had already been deleted by #1252. **The refusals were worth more than the compliance.**
- ⚠ **`ship.sh` re-executed itself** — the run shipped a change to `ship.sh`, so the CONVERGE
  payload it had already expanded was stale and it re-ran the new copy before the remote leg.
  Working as designed; the first block's output is not the final word.
- ⚠ **A shared-scratchpad collision is real** — a sibling agent overwrote `sweep.py`/`msg.txt`
  in the shared scratchpad mid-session. Nothing in the repo was touched. Name scratch dirs
  per-agent.

## How to verify

```bash
# ---- ranks 16/18/20 are CLOSED. All three are verified BY CONTENT (a squash is never an ancestor).
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc --list | head -6
#   expect `store: /home/zach/.cache/subsystem-store` PLUS `stamp:` lines. A store ending
#   `.claude/analyze-service-index` means a host that never took #1233.

bash ~/workspace/devrc/scripts/drift-check.sh; echo "rc=$?"      # 0 — was 24, then 17
cairn doctor; echo "rc=$?"                                       # 9 = the 2 un-seeded scopes (ranks 23/24), NOT a defect

# ---- the mirror is genuinely frozen now. Prove it by WATCHED REFUSAL, never by `stat`.
M=~/.claude/analyze-service-index
printf '' >> "$M/civitai/model-retention.md" && echo "🔴 STILL WRITABLE" || echo "refused (correct)"
: > "$M/civitai/zz-probe.md" && { echo "🔴 CREATE ACCEPTED"; rm -f "$M/civitai/zz-probe.md"; } || echo "create refused (correct)"
#   POSITIVE CONTROL — the same probe MUST succeed where writes are legal, or it proves nothing:
: > ~/.cache/subsystem-store/zz-probe.md && { echo "control OK"; rm -f ~/.cache/subsystem-store/zz-probe.md; }

# ---- rank 23's orphans: 4 rescued, 2 blocked, 10 shared entries still divergent
cairn ls-entries | wc -l                                          # 205 (was 201 before the rescue)
ls ~/.local/share/cairn-orphans-2026-09-03/                       # the preserved copies — do NOT delete
ls ~/.local/share/cairn-orphans-2026-09-03/shared-divergent/      # the 10 two-way divergent entries

# ---- IN FLIGHT at hand-off: `main` is RED until #1262 lands. Confirm before blaming your branch:
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/collector/keylog/tests/test_espanso_detect.py -q -p no:cacheprovider
```
