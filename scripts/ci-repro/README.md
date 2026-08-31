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

🔴 **The contention set is 7, NOT 12 — do not quote the 12.** Twelve pipelineruns
overlapped the failing window, but only the **7 devrc-ci** ones were on the pinned
node. Measured 2026-08-31: gitops-validate is pinned to a **different** node
(42/42 pods on `talos-uvh-gtj`) and the one auditloop run was on `talos-deu-s2q`.
Neither mounts anything devrc-ci mounts. Sizing a concurrency cap against 12 would
size it against runs that were never there.
⚠ `claude/skills/tekton/SKILL.md:48` says "every run lands on `talos-xr6-r7p`" — true
of *devrc-ci* runs, misleading as written, and the likely source of the 12.

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
`TestAHungRoundTripSAYSWhichSideBlocked` — `grep -n '_fsync_dir", _stall' ` finds it —
and carries a 🔴 comment noting that patching the stdlib's `os.fsync` is process-global
and would stall any other thread. (Cited by name, not by line: that file's comment
block shifts on every edit, and a line citation into it shipped wrong twice.) This shim is **strictly wider** than what that comment warns
against. It is used anyway because it needs no repo edit and therefore tests the
shipped code path exactly as CI runs it — but the narrower monkeypatch is the right
tool if you only need `_fsync_dir`, and the warning above applies to this shim too.

## Two fixes that look right and are not

🔴 **Raising `HANG_TIMEOUT` again is worse than doing nothing.** 60.0 is already the
symptom fix (raised from 15 on 2026-08-29) and it did not hold. The test file's own
arithmetic (`:155-158`) is ~320 hung-call sites × 60 s ≈ 5.3 h; re-counted live in
this PR's tree it is 211 `fetch(` + 113 `await_audit(` = **324**, so ≈ 5.4 h. Either
way it dwarfs the budget.
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
null` is not a devrc oversight: **479/479** taskruns in that namespace declare none, so
this is a platform-wide default, and changing devrc alone would not stop under-declared
neighbours oversubscribing the node.)

The real levers are infra and are **not** this repo's to apply, ranked by whether they
can move the write that actually stalls:

1. **Cap concurrent devrc-ci runs on the pinned node, or unpin it.** Sizes against
   **7**, not 12. Distinct from `tekton-supersede`, which only collapses redundant
   runs of the *same* PR. Non-zero `computeResources` is one way to implement this —
   it does not touch IOPS, but it makes excess runs Pending instead of co-scheduled.
2. **Give the gate's ephemeral layer (`/tmp`) faster or isolated storage** — that is
   where the stalling fsync lands. Requires first measuring whether it shares a device
   with the local-path PVs; see the ⚠ above.
3. **Relocating `nix-store-cache`** only removes neighbouring nix traffic from the
   shared device. Worth doing if 1 and 2 are blocked; it cannot move the failing write.

## Counts in this file

`7`/`12` runs, `499` taskruns, `324` call sites, the per-node PV split and the live
timeouts were measured on 2026-08-31 and **nothing asserts on any of them** — they
drift, and did so within one session (449→479→499 taskruns while this PR was open).
Re-derive before quoting; the `kubectl`/`grep` commands are in this PR's comments.
