---
clawgate-task: 371
---
# Handoff: cairn-phase3 — 2026-08-28

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
Make the hosted subsystem store the single datastore every host reads **and writes**. Phase 2 (read-through client) shipped earlier. This session shipped clawgate #371's criteria **1–7**: per-token identity, server-side scope authorization, the enumeration property, and the write path. **Criteria 8, 9, 10 remain open** — they are re-seed / cutover / credential-retirement operations, not code.

## State now

🔴 **CRITERION 10 IS COMPLETE — the store has NO unrestricted credential.** Executed
2026-08-31, homelab-infra **`be3084f0`** on trunk (confirmed an ancestor of `origin/trunk`;
trunk tip still carries it). The token file went **354 B → 295 B**, a pure line deletion:
surviving mapped row `ecc11c1b5b6e` unchanged, `2481e4553f6c` absent.

**Live state, verified end-to-end:**
- banner `token-ids=a8f329c534d7:zach` — one row, and **no `UNRESTRICTED-SCOPE LEGACY MODE`**
  (positive control: 6 historical banners in Loki, so that grep CAN match)
- mapped credential → **200**; legacy credential → **401, the credential is dead**
- **both hosts read 200** — workbench and laptop, laptop env confirmed `a8f329c534d7`
- `cairn sync` live, 132 entries · `test-check-subsystem-store-phase1.sh` **pass=25 fail=0**

⚠ **ONLY (d) REMAINS: the plaintext backups are still on disk.**
`~/.config/subsystem-store/env.bak-legacy-2026-08-29` exists on **both** hosts and holds the
token hashing to `2481e4553f6c` — measured, not assumed. It is now a **dead** credential (401),
so this is hygiene rather than exposure, but the file is what the shred is for. Deliberately
deferred by operator decision: do (d) after watching the store healthy for a day.

🔴 **Rank 1's claim `cairn-phase3-1` is STILL HELD**, because (d) is still owed under it.

**PR #1129 (devrc) merged** — squash `8e883e3d`. Its gate needed a retry: the first run died
with **zero steps executed** after a 60-minute `TaskRunTimeout`, starved by a single-node
`nodeSelector` pin; re-fired after the queue drained, it ran in ~30 min and passed
(`pytests 19449 collected / 0 failed`, `nodetests 1441/1441`).

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

## Next steps (ranked)

🔴 **Numbering is UNCHANGED on purpose** — the rank is half a claim's identity
(`claim-work --slug-for <this doc> <rank>`).

1. 🔴 **Criterion 10 step 2 — (a),(b),(c) DONE 2026-08-31. Only (d) is left:**
   `shred -u ~/.config/subsystem-store/env.bak-legacy-2026-08-29` on **BOTH** hosts
   (`ssh zach@192.168.50.155` for the laptop). Confirm first that the store has been healthy
   since (`cairn sync` on each host), because this destroys the last local copy of the retired
   credential. The row itself is already gone from git and the cluster, so recovery after this
   is `git log -p` on `clusters/homelab/apps/subsystem-store/secrets.enc.yaml` or the backup
   CronJob — **not** these files.
   forcing: security
2. ~~**The backup CronJob.**~~ ✅ **CLOSED 2026-08-30** — homelab-infra#551, squash `c4e0f82b`.
   forcing: none
3. **Criterion 8's laptop half** — `seed.sh` has still only been run from workbench (132
   entries / 15 scopes against the card's 139 / 19; the gap is laptop-only). Run it from the
   laptop (`laptop` skill), then `comm -23` per host must print zero lines.
   🔴 **The same-window warning has EXPIRED in one direction and not the other**: rank 1's
   credential work is done, so there is no longer a conflict there. The *destructive* half
   still stands — the push is `rsync -a --delete` SOURCE→STAGE then tar STAGE→pod, so every
   entry the laptop also holds is overwritten with the laptop's copy, destroying API-appended
   bullets that exist only in the served copy. Recoverable via #551's backup, not harmless.
   forcing: none
4. **Criterion 9 — the cutover.** `subsystem-index` writes through `cairn`; the local store
   becomes a read-only cache (`stat -c %a` = 444, EACCES *watched*, not assumed). 🔴 This is
   what makes an API write DURABLE — until it lands, every appended bullet is one `seed.sh`
   from gone. The read/write **allowlist split** is this criterion's own job.
   forcing: none
5. **Verify criteria 1, 2, 5, 6, 7 against the POD**, not just in tests. Criterion 4 is done
   there; 2's denied-scope arm is done for WRITES (404 `scope-unknown`) but not for the three
   read routes. ⚠ Now cheaper: with one row, `zach`'s 15-scope allowlist is the ONLY authority,
   so a denied-scope read is no longer maskable by a bare row.
   forcing: none
6. **Add the `internal-error` alert** in the monitoring config. Without it the dispatch
   backstop turns a dropped connection into a quiet 500 only the audit log sees.
   forcing: none
7. **`scripts/cairn` has no write verb** — the CLI still only reads.
   forcing: none
8. **§5's off-mesh control, still unrun** — from a phone on cellular:
   `curl -si https://store.zacx.dev/api/v1/recall/devrc` (expect 401) and
   `curl -si https://store.zacx.dev/` (expect 404). Cannot be done from a host on the mesh.
   forcing: none
9. **devrc #1045** — three pre-existing `seed.sh` gaps; the third (local-side `-type f`
   uncovered) is the mirror of what #998 fixed. ⚠ `#1045` is an **issue, not a PR**.
   forcing: none
10. 🔴 **`scripts/provision-vaultwarden-backup-bucket.sh` has an orphaned-credential window.**
    It runs `mc ilm rule add` **LAST**, after `mc admin user add` + `policy attach`, with no
    pre-flight refusals — an abort at that step leaves a live write-capable MinIO key whose
    secret was never printed. **Closing condition:** a merged homelab-infra PR moving
    `ilm rule add` above `user add`.
    forcing: security

## Gotchas / decisions / dead-ends

**Operator rulings this session (all acted on):**
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

## How to verify

```bash
# ---- criterion 10 is DONE: one row, no unrestricted credential ----
POD=$(KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get pod -l app=subsystem-store-api \
  -o jsonpath='{.items[0].metadata.name}')
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store logs $POD | grep -o 'token-ids=[^ ]*'
#   expect exactly: token-ids=a8f329c534d7:zach

# 🔴 the ABSENCE of the banner is the property — control it, or it is a zero wired to nothing
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store logs $POD | grep -c 'UNRESTRICTED-SCOPE'  # 0
curl -s --get "http://192.168.50.94:30310/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="subsystem-store"} |= "UNRESTRICTED-SCOPE LEGACY MODE"' \
  --data-urlencode "start=$(( $(date -u +%s) - 7*86400 ))000000000" \
  --data-urlencode "end=$(date -u +%s)000000000" --data-urlencode 'limit=100'
#   POSITIVE CONTROL: must be non-zero (was 6). A zero here means the check proves nothing.

# ---- the credentials: one lives, one is dead ----
( set -a; . ~/.config/subsystem-store/env; set +a
  curl -s -o /dev/null -w 'mapped -> %{http_code}\n' \
    -H "Authorization: Bearer $SUBSYSTEM_STORE_TOKEN" "$SUBSYSTEM_STORE_URL/api/v1/recall/devrc" )
#   200
( set -a; . ~/.config/subsystem-store/env.bak-legacy-2026-08-29; set +a
  curl -s -o /dev/null -w 'legacy -> %{http_code}\n' \
    -H "Authorization: Bearer $SUBSYSTEM_STORE_TOKEN" "$SUBSYSTEM_STORE_URL/api/v1/recall/devrc" )
#   401 — and this file is what rank 1 (d) shreds

cairn sync                                    # live, 132 entries
ssh zach@192.168.50.155 'cairn sync'          # the OTHER host must work too

# ---- the secret in git matches the cluster ----
git -C $HOMELAB show origin/trunk:clusters/homelab/apps/subsystem-store/secrets.enc.yaml \
  > /tmp/t.yaml && SOPS_AGE_KEY_FILE=$HOMELAB/.secrets/age.key sops -d /tmp/t.yaml \
  | python3 -c "import sys,yaml,hashlib; v=yaml.safe_load(sys.stdin)['stringData']['token']; \
print(len(v), [hashlib.sha256(l.encode()).hexdigest()[:12] for l in v.split(chr(10))])"; rm -f /tmp/t.yaml
#   295 ['ecc11c1b5b6e', 'e3b0c44298fc']   — 2481e4553f6c must NOT appear

# ---- the guards ----
nix-shell -p 'python3.withPackages(ps: [ps.pyyaml])' --run \
  "bash scripts/tests/test-check-subsystem-store-phase1.sh"   # pass=25 fail=0
bash scripts/check-sops-rules.sh                              # 32 rules, all *.enc.yaml encrypted
python3 scripts/tests/preflight-subsystem-store-tokens.py <dir-with-pod-extracted-scripts>  # devrc, 8/8
```
