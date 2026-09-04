# ci-repro — reproduce a CI failure on the dev host, on demand

Diagnostics for gate failures that only appear in CI. Not tests; nothing here runs
in `run-tests.sh` or either `nix` check derivation, and this directory is
deliberately outside `scripts/tests/` so it cannot perturb that runner's two-way
pinned target/floor table.

⚠ `slowfsync.c` is currently the repo's only `.c` file and **nothing builds or
lints it** — the `gcc` line below is the only thing keeping it honest. Compile it
before trusting it.

## `slowfsync.c` — the store-api gate failure is fsync latency, not seed/ordering

**What it answers.** `scripts/tests/test_subsystem_store_api.py` fails in
`tekton/devrc-pytests` on PRs whose diff cannot reach it — docs-only PRs included —
and passes on the dev host. That combination read for months as a mysterious flake
with a seed/ordering hypothesis.

**The mechanism.** `server.py:_replace_bytes` fsyncs the file (`:2012`) and then the
parent directory (`_fsync_dir`, `:1961`) **inside the request, before the response is
written**; fsync blocks in uninterruptible sleep. When one fsync exceeds
`HANG_TIMEOUT` (60.0) the client raises `TimeoutError` at `socket.py:720` and the
gate reports a **code failure for an I/O stall**. The suite already classifies this
correctly and says so unprompted:

```
MECHANISM = SERVER_BLOCKED_IN_FSYNC   (handler threads=1 [... =SERVER_BLOCKED_IN_FSYNC], accept loop parked=True)
```

**Why CI and not here.** `devrc-ci` is pinned to one node
(`nodeSelector: kubernetes.io/hostname: talos-xr6-r7p`), so a burst of pushes stacks
concurrent runs onto one machine's disk.

🔴 **Of the twelve overlapping pipelineruns, only 7 were devrc-ci — and 7 is NOT the
node's contention set either.** Measured 2026-08-31: gitops-validate is pinned to a
**different** node (`talos-uvh-gtj`) and the one auditloop run in that window was on
`talos-deu-s2q`, so neither was contending with this suite. But **do not stop here and
size a cap at 7** — the mechanism is node-local *device* contention (see the lever
section), so every other pipeline resident on `talos-xr6-r7p` counts too, whatever
volumes it names. 12 is wrong in one direction and 7 in the other.
⚠ `claude/skills/tekton/SKILL.md` says "every run lands on `talos-xr6-r7p`" in its
node-pinning paragraph — true of *devrc-ci* runs, misleading as written, and the likely
source of the 12. **That same file already resolves it** further down, recording the
`devrc-ci` / `gitops-validate` node split from homelab-infra #396 — so read both before
editing either. (`grep -n 'talos-uvh-gtj' claude/skills/tekton/SKILL.md`.)

🔴 **AND THE FAILING WRITE IS ON NEITHER NAMED VOLUME.** The gate pod mounts
`nix-store-cache` at `/nix` and the per-run `source` PVC (a `volumeClaimTemplate`,
~4 Gi) at `/workspace/source` — but the stalling `os.fsync` in the CI traceback
targets
`/tmp/nix-build-devrc-pytests.drv-0/pytest-of-nixbld13/pytest-0/popen-gw3/…/store`,
i.e. the **step container's ephemeral layer**. `devrc-ci-gate` sets no `TMPDIR`/`TMP`
and mounts nothing at `/tmp` (verified on the pod spec). So relocating
`nix-store-cache` would remove *neighbouring* nix traffic from the shared device but
**cannot move the failing writer**. Whether the ephemeral layer and the local-path PVs
share one physical device is *inferred* from the standard Talos `/var` layout, **not
measured** — measure it before acting on it.

⚠ Two earlier revisions of this file got the storage wrong in different ways: first
`emptyDir medium=disk` (that was the pod's `tekton-internal-workspace`, Tekton's own
plumbing), then "the volume every concurrent run shares is `nix-store-cache`" (false
for 5 of the 12, and not where the failing write lands). Both are corrected above.
tekton-ci's PVs are spread over four nodes — `talos-xr6-r7p` 100, `talos-deu-s2q` 52,
`talos-uvh-gtj` 51, `talos-jkj-deb` 43 — so "all `local-path`" does **not** mean "all
one disk".

## Use it

```bash
SO=/tmp/slowfsync-$USER-$$.so          # unique: this box runs parallel agents
gcc -shared -fPIC -o "$SO" scripts/ci-repro/slowfsync.c -ldl

# validate the INSTRUMENT before trusting its verdict — must report ~65s
LD_PRELOAD="$SO" python3 -c \
  "import os,tempfile; fd,p=tempfile.mkstemp(); os.write(fd,b'x'); os.fsync(fd)"

# control — expect: 8 passed, rc 0
nix develop . --command python3 -m pytest \
  scripts/tests/test_subsystem_store_api.py::TestTheActorComesFromTheTOKEN -q

# reproduction — expect: 1 failed, TimeoutError, MECHANISM = SERVER_BLOCKED_IN_FSYNC
nix develop . --command env LD_PRELOAD="$SO" python3 -m pytest \
  scripts/tests/test_subsystem_store_api.py::TestTheActorComesFromTheTOKEN -q
```

🔴 **Preload it for ONE test selection.** `LD_PRELOAD` is inherited across `exec()`
and the latch is per-process, so a whole-file or whole-suite run gives **every** xdist
worker and every `git`/`bash`/`nix` subprocess its own 65 s stall. Measured: parent
65.0 s *and* child 65.0 s in a two-process control.

Measured 2026-08-31 — control `8 passed in 4.63s` / rc 0; reproduction
`1 failed, 7 passed` / rc 1, failing on the **identical test with the identical
parametrisation** as CI run `devrc-ci-86zxj` (sha `5de43017`):

```
TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-…-kelp-forest-zach]
```

Independently re-run by an auditor: control `8 passed in 5.38s`, reproduction
`1 failed, 7 passed in 64.28s`, with the stall landing on the intended call site
(`_replace_bytes:2012` ← `append_bullet:2115` ← `_append_bullet:3989`).

### What this does and does not prove

It proves fsync latency **suffices** to produce the exact observed failure. It does
**not** prove ordering plays no part — sufficiency is not necessity. What refutes the
seed/ordering hypothesis is the CI evidence itself: the suite's own classifier named
`SERVER_BLOCKED_IN_FSYNC` on the failing run. The reproducer's job is to make that
mechanism testable on demand rather than to eliminate rivals.

### Why LD_PRELOAD and not the narrower tool already in the repo

`test_subsystem_store_api.py` monkeypatches `api._fsync_dir` inside
`TestAHungRoundTripSAYSWhichSideBlocked`, and carries a 🔴 comment noting that patching
the stdlib's `os.fsync` is process-global and would stall any other thread. Find it:

```bash
grep -n '_fsync_dir", _stall' scripts/tests/test_subsystem_store_api.py
```

(By name, never by line. A line citation into that file shipped wrong **three times** in
this PR alone — twice from counting against the pre-PR tree, and once because the very
commit that "fixed" it inserted lines above its own citation.) This shim is **strictly
wider** than what that comment warns
against. It is used anyway because it needs no repo edit and therefore tests the
shipped code path exactly as CI runs it — but the narrower monkeypatch is the right
tool if you only need `_fsync_dir`, and the warning above applies to this shim too.

## Two fixes that look right and are not

🔴 **Raising `HANG_TIMEOUT` again is worse than doing nothing.** 60.0 is already the
symptom fix (raised from 15 on 2026-08-29) and it did not hold. The test file carries
this arithmetic itself, next to the constant — search for `per-hung-call` in
`test_subsystem_store_api.py`. Re-derive the multiplier rather than trusting either
copy:

```bash
F=scripts/tests/test_subsystem_store_api.py
echo $(( $(grep -c 'fetch(' $F) + $(grep -c 'await_audit(' $F) ))   # hung-call sites
```

At the time of writing that is ~324 (× 60 s ≈ 5.4 h); the file's own text says ~320
(≈ 5.3 h). Either way it dwarfs the budget.
⚠ **That budget is NOT 45 m** — the test file says 45 m and the live values are the
gate task's `timeout: 1h0m0s` inside pipeline `timeouts: {tasks: 1h10m0s, pipeline:
1h25m0s}`. The conclusion survives (5.4 h ≫ 1 h, and even 15 s × 324 ≈ 81 m exceeds
it), but do not quote 45 m; the test file's copy of that number is stale and is left
untouched here rather than silently corrected in a comment-only change.

⚠ **"CPU/memory requests cannot fix it" — too strong; corrected.** k8s requests govern
CPU and memory, **not** disk IOPS, so requests would not make fsync faster. But every
devrc-ci run is `nodeSelector`-pinned to one node, so non-zero requests are exactly the
standard mechanism for making excess runs **Pending instead of co-scheduled** — i.e.
they are one way to implement the concurrency cap listed below. (`computeResources:
null` is not a devrc oversight: **every** taskrun in that namespace declares none — the
ratio is what matters and has held at every reading; the absolute count drifts — so
this is a platform-wide default, and changing devrc alone would not stop under-declared
neighbours oversubscribing the node.)

The real levers are infra and are **not** this repo's to apply, ranked by whether they
can move the write that actually stalls:

1. **Bound the disk-heavy work on that NODE, or unpin devrc-ci.** 🔴 **Capping
   devrc-ci alone does not bound the node** — the mechanism is node-local *device*
   contention, so any pod there competes regardless of which volumes it names.
   `talos-xr6-r7p` is not devrc-ci's — **8 distinct pipelines** were resident when this
   was written, devrc-ci being one. A cap set at "7 devrc-ci runs" bounds one of eight.
   ⚠ Two of those became free to land there only on **2026-08-31**: `auditloop-ci` and
   `remix-ux-audit` lost their hostname pins that day (homelab-infra #597), so their
   large pod counts are mostly *pre-un-pin fossils* — the numbers understate what they
   will do next, not overstate it. `naida-ux-audit` is **still** hostname-pinned to this
   node, which makes it a concrete lever this section's own logic calls for.
   Re-derive by PIPELINE (not by name prefix — that splits one pipeline across rows):

   ```bash
   KC=~/workspace/homelab-talos/homelab-kubeconfig   # no default KUBECONFIG here, by design
   kubectl --kubeconfig $KC -n tekton-ci get pods \
     --field-selector spec.nodeName=talos-xr6-r7p -o json \
     | jq -r '.items[].metadata.labels["tekton.dev/pipeline"] // "none"' | sort | uniq -c | sort -rn
   ```

   🔴 **Pass `--kubeconfig` explicitly.** This box has *no* default `KUBECONFIG` by
   design; a bare `kubectl` errors loudly, but with some *other* cluster exported the
   `--field-selector` matches nothing and returns a **silent zero** — which reads as
   "devrc-ci is alone on the node", the exact inverse of this section's point.

   Distinct from `tekton-supersede`, which only collapses redundant runs of the *same*
   PR. Non-zero `computeResources` is one way to implement a cap — it does not touch
   IOPS, but it makes excess runs Pending instead of co-scheduled.
2. **Give the gate's ephemeral layer (`/tmp`) faster or isolated storage** — that is
   where the stalling fsync lands. Requires first measuring whether it shares a device
   with the local-path PVs; see the ⚠ above.
3. **Relocating `nix-store-cache`** only removes neighbouring nix traffic from the
   shared device. Worth doing if 1 and 2 are blocked; it cannot move the failing write.

## Counts in this file

Every number here was measured on 2026-08-31 and **nothing asserts on any of them**.
They drift in both directions — the taskrun count was read as 449, 479, 499 and 468 at
four points while this PR was open. **Re-derive before quoting.** The commands are
here, not in a comment thread:

```bash
KC=~/workspace/homelab-talos/homelab-kubeconfig

# overlapping pipelineruns in a window, and which node each ran on
kubectl --kubeconfig $KC get pipelineruns -A -o json | jq -r '.items[]
  | select(.status.startTime != null)
  | select(.status.startTime <= "<END>" and (.status.completionTime // "9999") >= "<START>")
  | .metadata.name'
kubectl --kubeconfig $KC -n tekton-ci get pods -o json \
  | jq -r '.items[] | "\(.spec.nodeName)\t\(.metadata.name)"' | sort

# taskruns declaring no compute resources
kubectl --kubeconfig $KC -n tekton-ci get taskruns -o json \
  | jq '[.items[] | select(.spec.computeResources == null)] | length'

# PVs per node
kubectl --kubeconfig $KC get pv -o json | jq -r '.items[]
  | .spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]' \
  | sort | uniq -c

# live budgets
kubectl --kubeconfig $KC -n tekton-ci get pipelinerun <run> -o jsonpath='{.spec.timeouts}'
```

⚠ **Pipelineruns are pruned aggressively, so an old window is not re-derivable at all.**
Retention is `keep: 20` per resource, hourly — not the `keep: 100`/daily that two stale
comments in `homelab-talos` still claim. Derive it, do not trust either:

```bash
kubectl --kubeconfig $KC get tektonconfig config -o jsonpath='{.spec.pruner}'
```

Several of the runs in the window above were already pruned while this PR was open.
Treat that breakdown as a **record**, not as something a later reader can reproduce —
and note this cuts the other way too: the window is 5× less recoverable than the
superseded figure implied.

---

## `slow_respond.py` — the RAW-SOCKET half of the same gate failure (devrc#1165)

**What it answers.** `tekton/devrc-pytests` failed on a different test each run with

```
AssertionError: a SECOND complete response followed the 200 on one connection
                — a pooling proxy hands it to the next client: b''
assert 0 == 1
```

plus a `BrokenPipeError` in the same run. The message describes `> 1`; the failure was
`== 0`. Nothing sent a second response — the client read an **empty socket**.

**Why this is a second instrument and not a duplicate.** `slowfsync.c` above stalls one
fsync past `HANG_TIMEOUT` (60.0) and lands on the `fetch(...)`/`http.client` sites,
which raise `TimeoutError`. The **raw-socket** sites are a separate and far more
sensitive population: they read with `sock.settimeout(settle)` where `settle` was
**3.0**, and they *swallowed* the timeout. A stall of just over **3 s** — twenty times
smaller than the one `slowfsync.c` has to manufacture, and correspondingly far more
common under the disk contention this file already documents — made the reader return
an empty buffer and sail on, with no exception raised anywhere.

So this shim delays the **response** rather than the fsync, and by **seconds** rather
than by a minute. It needs no compiler and no `LD_PRELOAD`.

**Usage.**

```bash
# control — shim inert, same command line
PYTHONPATH=scripts/ci-repro python3 -m pytest scripts/tests/test_subsystem_store_api.py \
  -p slow_respond -k "TestTheBackstopNeverSendsASecondResponse or TestNoRequestSmuggling"

# reproduction — 5 s stall against a 3.0 s drain
SLOW_RESPOND_S=5 PYTHONPATH=scripts/ci-repro python3 -m pytest \
  scripts/tests/test_subsystem_store_api.py -p slow_respond --slow-respond-selftest \
  -k "TestTheBackstopNeverSendsASecondResponse or TestNoRequestSmuggling"
```

`--slow-respond-selftest` fails loudly unless the patch actually attached and the delay
is non-zero — a shim that silently failed to attach reports a clean green that means
nothing. Both of its arms have been watched to fire.

**Measured, at the commit before the fix:**

| run | result |
|---|---|
| control (`SLOW_RESPOND_S` unset) | `8 passed`, rc 0 |
| reproduction (`SLOW_RESPOND_S=5`) | `3 failed, 5 passed`, rc 1 |

The three failures reproduced the CI text verbatim, including
`the PUT sent a second response too: b''` / `assert 0 == 1` and the `BrokenPipeError`
— whose mechanism is now visible: the client's 3.0 s drain expired and closed the
socket, so the server's own `wfile.write(body)` hit a broken pipe.

**After the fix** (`_raw_exchange` waits for a framed response, then lingers to catch an
extra one) the same armed command gives `13 passed`.

⚠ **Two raw readers in that file were deliberately NOT converted** —
`test_an_absolute_form_target_that_breaks_urlsplit_is_a_401_not_a_CRASH` and
`test_an_UNKNOWN_method_is_the_same_uniform_401_not_a_501_page` still open-code a
`settimeout(5)` drain. They share the race but not the mis-description: their
assertions (`assert data, "the request got no response at all"`) already say what
actually happened. They are also far less exposed, since both are answered by the
pre-auth 401 path before any disk work. Converting them means restructuring two
unrelated tests, so it was left out of the fix rather than done quietly.

---

## `slow_arm.py` — a GUARD that could not reach its own stall site in CI

**What it answers.** `TestAHungRoundTripSAYSWhichSideBlocked` failed in CI
(`devrc-ci-dpmck`, PR #1147, `collected=20075 passed=20071`) with

```
AssertionError: the server never reached the stall site, so the hang under test
was NOT the one this test set up — the report below would be about a DIFFERENT hang
assert False
 +  where False = is_set()
```

The precondition did the right thing — it refused to report a verdict when its own
setup had not landed. The problem was that the setup **did not reliably land**, so the
guard failed where the code was fine: a flake with a better error message, blocking
every PR exactly as the original one did.

**The mechanism.** The precondition was `assert stalled.is_set()`, sampled at the
instant the client's `timeout=CLIENT_BOUND` (**0.25 s**) expired. Within those 250 ms
the server had to accept the connection, spawn a handler thread, parse the request,
authenticate, meter, resolve the path, read the entry **and** reach `_fsync_dir`. On an
idle dev host that takes a few ms. Under the CI node's concurrency it does not always.

There was a second, quieter race in the same helper: the report had to be taken before
the fixed `SERVER_STALL` (1.2 s) sleep elapsed, or the verdict described a server that
had already unblocked.

**The fix is synchronisation, not a bigger number.** `SERVER_STALL` is **gone**. The
caller now `stalled.wait(HANG_TIMEOUT)`s for the stall site to be reached, and the
handler is then held by a second Event until the report has been taken. Neither side
guesses the other's timing, so there is no pair of bounds left to tune. It is also
*faster*: the control run went 4.19 s → 3.06 s, because nothing sleeps 1.2 s any more.

**Usage.**

```bash
# control — shim inert, same command line
PYTHONPATH=scripts/ci-repro python3 -m pytest scripts/tests/test_subsystem_store_api.py \
  -q -p slow_arm -k TestAHungRoundTripSAYSWhichSideBlocked

# reproduction — 0.5 s on the request path, against a 0.25 s CLIENT_BOUND
SLOW_ARM_S=0.5 PYTHONPATH=scripts/ci-repro python3 -m pytest \
  scripts/tests/test_subsystem_store_api.py -q -p slow_arm --slow-arm-selftest \
  -k TestAHungRoundTripSAYSWhichSideBlocked
```

| run | at parent commit | at HEAD |
|---|---|---|
| control | `4 passed` | `4 passed` |
| armed `SLOW_ARM_S=0.5` | **`2 failed, 2 passed`** | **`4 passed`** |
| armed `SLOW_ARM_S=3.0` (12x the bound) | — | `4 passed` |

Both arms of the class fail at the parent commit, which confirms the defect is in the
shared `_hang_and_report` helper rather than in one test.

⚠ **The guard can still go RED — proved by mutation, not by assertion.** A `wait()`
that masked a real regression would be worse than the flake:

| mutant | result |
|---|---|
| `_replace_bytes` no longer calls `_fsync_dir` | RED — `Failed: DID NOT RAISE TimeoutError` (the server stops stalling entirely) |
| handler parked *before* the stall site for longer than `HANG_TIMEOUT` (`SLOW_ARM_S=90`) | RED — `the server never reached the 'fsync' stall site within 60s`, after 91 s |
| `("fsync", …)` removed from `_HUNG_SERVER_RULES` | RED — `MECHANISM = BLOCKED_ELSEWHERE`, so the classifier is still genuinely under test |

The middle row is the important one: the client still times out, but the stall site is
genuinely never reached — exactly the case a `wait()` could have swallowed. It does not.

⚠ **NOT reproduced by natural scheduling on the dev host, and that is stated rather
than glossed.** Pinned to ONE core with `-n 4`, then with 27 CPU hogs plus three
fsync-looping `dd` writers on that core, the pre-fix guard still passed **5/5** on the
class and **650/650** on the full file at 2.5x wall-clock inflation (81 s → 207 s). This
is a 24-core host with fast NVMe; the arming path is a few ms of CPU, so a ~50x
slowdown would be needed to miss 250 ms. The deterministic injection above reproduces
the exact failure and the mutation matrix bounds the fix — but the *natural* CI
scheduling failure was not reproduced here.

---

## `slow_cairn_fsync.py` — the SAME stall, twelve times more sensitive (devrc#1242)

**What it answers.** `tekton/devrc-pytests` fails on
`test_cairn_write.py::TestAppendLands::test_a_bullet_is_appended_and_the_status_is_named`
— and, because `--dist loadfile` puts a whole file on one worker, on its siblings in
the same run — with

```
AssertionError: 🔴 cairn: the write did NOT happen — http://127.0.0.1:PORT unreachable: timed out
                Nothing was queued and nothing was written locally.
assert 7 == 0
```

🔴 **It is the same fsync mechanism as the store-api half above, but this was VERIFIED
rather than assumed, and it does not simply inherit that root cause.**
`test_cairn_write.py` imports `http.server` and stands up its OWN loopback servers; it
carries no `MECHANISM` classifier of its own. The transfer was established from the CI
log directly, not by analogy: on `devrc-ci-jfg67` the store root was
`/tmp/nix-build-devrc-pytests.drv-0/pytest-of-nixbld1/pytest-0/popen-gw1/…` — the step
container's ephemeral layer, exactly as documented above — and the message names a
client-side `timed out`, not a refused connection.

🔴 **THE BOUND IS `--timeout 5`, NOT `HANG_TIMEOUT` 60.0.** `run_cairn` passes it to the
client subprocess, so a single fsync slower than **five seconds** breaches it. That is
**twelve times tighter** than the store-api half, which is why this file is the more
frequent casualty of the same contention, and why sizing a reproducer at 65 s here
would overstate how much latency it takes.

**Why a third instrument.** `slowfsync.c` is `LD_PRELOAD`, which is inherited across
`exec()` — and here the store server is IN-PROCESS while the cairn client is a
SUBPROCESS, so preloading would stall both sides and muddy which one timed out.
Patching `os.fsync` inside the test process stalls exactly the server. No compiler.
Full usage, controls and measurements are in the plugin's own docstring.

**What changed in the repo, and what did not.**

* `testlib/hang_mechanism.py` (new) gives this suite the `MECHANISM =` verdict the
  store-api suite already had, and `test_cairn_write.py`'s write assertions now carry
  it. Under the reproduction the failure reads
  `MECHANISM = SERVER_BLOCKED_IN_FSYNC (handler threads=2 [...=BLOCKED_ELSEWHERE
  ...=SERVER_BLOCKED_IN_FSYNC], accept loop parked=True)` plus the store's filesystem.
  🔴 **DIAGNOSIS, NOT TOLERANCE** — no bound moved, nothing retries, the test fails
  exactly as before. A gate that reports a code failure for an I/O stall trains
  everyone to click through, and that was the whole cost.
* 🔴 **The headline is deliberately NOT a consensus of the handler threads.** Two
  servers are live here — the store and the shim in front of it — so when a write times
  out they legitimately disagree: the shim is parked in `urlopen`, the store in
  `fsync`. Measured. A rule requiring agreement would answer `AMBIGUOUS` for the
  textbook case the classifier exists to name.
* 🔴 **`hang_mechanism` scans frames' SOURCE LINES, never their FILENAME**, which is the
  known-and-unfixed defect `_HUNG_SERVER_RULES` carries above (a worktree named
  `devrc-fsync` misclassifies every hang there). Pinned by
  `test_hang_mechanism.py::test_a_checkout_PATH_containing_a_token_does_not_decide_the_verdict`,
  which was watched to fail when the filename is folded back in. **The store-api
  classifier was NOT rewired onto the shared module** — it has its own tests and that is
  its own edit; two copies exist today and that is a known debt, not an oversight.
* **Nothing here fixes the stall**, and the disk-siting half was already fixed:
  `b4fde334` (#1219) moved this file's store onto tmpfs. ⚠ **Both reproductions above
  ran with that mitigation fully in force** (`/dev/shm/devrc-store-…`), so this is a
  **latency** dependency, not a filesystem one. tmpfs makes a breach far less likely; it
  does not remove the 5 s bound, and `store_siting` falls back to disk in five
  documented ways without saying so. The levers for the stall itself remain the infra
  ones ranked in the store-api section.

⚠ **A PR branched before `b4fde334` still carries the old disk-backed fixture**, because
branch protection sets `strict: false` and never rebases. Both failures examined here
(#1209, #1233) were such branches. Rebasing is what picks the mitigation up — the fix
does not reach an open PR on its own.
