# ci-repro — reproduce a CI failure on the dev host, on demand

Diagnostics for gate failures that only appear in CI. Not tests; nothing here runs
in `run-tests.sh` or either `nix` check derivation, and this directory is
deliberately outside `scripts/tests/` so it cannot perturb that runner's two-way
pinned target/floor table.

## `slowfsync.c` — the store-api "flake" is fsync contention, not seed/ordering

**What it answers.** `scripts/tests/test_subsystem_store_api.py` fails in
`tekton/devrc-pytests` on PRs whose diff cannot reach it — docs-only PRs included —
and passes on the dev host every time. That combination read for months as a
mysterious flake with a seed/ordering hypothesis.

**The mechanism.** `server.py:_replace_bytes` issues an `fsync` *before* the
response is written, and fsync blocks in uninterruptible sleep. When the disk is
busy enough that one fsync exceeds `HANG_TIMEOUT` (60.0), the client raises
`TimeoutError` at `socket.py:720` and the gate reports a **code failure for an I/O
stall**. The suite already classifies this correctly and says so unprompted:

```
MECHANISM = SERVER_BLOCKED_IN_FSYNC   (handler threads=1 [... =SERVER_BLOCKED_IN_FSYNC], accept loop parked=True)
```

**Why CI and not here.** `devrc-ci` is pinned to one node
(`nodeSelector: kubernetes.io/hostname: talos-xr6-r7p`), the gate workspace is
`emptyDir medium=disk`, and the nix caches are `local-path` PVCs — so every
concurrent pipelinerun contends on **one physical disk**. Measured 2026-08-31:
**12 pipelineruns overlapped** the failing window (5 devrc + 4 gitops-validate +
auditloop), matching the figure the test file's own 2026-08-29 note cites.

## Use it

```bash
gcc -shared -fPIC -o /tmp/slowfsync.so scripts/ci-repro/slowfsync.c -ldl

# validate the INSTRUMENT before trusting its verdict — must take ~65s
LD_PRELOAD=/tmp/slowfsync.so python3 -c \
  "import os; fd=os.open('/tmp/p.tmp',os.O_CREAT|os.O_WRONLY,0o644); os.write(fd,b'x'); os.fsync(fd)"

# control — expect: 8 passed, rc=0
nix develop . --command python3 -m pytest \
  scripts/tests/test_subsystem_store_api.py::TestTheActorComesFromTheTOKEN -q -p no:randomly

# reproduction — expect: 1 failed, TimeoutError, MECHANISM = SERVER_BLOCKED_IN_FSYNC
nix develop . --command env LD_PRELOAD=/tmp/slowfsync.so python3 -m pytest \
  scripts/tests/test_subsystem_store_api.py::TestTheActorComesFromTheTOKEN -q -p no:randomly
```

Measured 2026-08-31 — control `8 passed in 4.63s` / rc 0; reproduction
`1 failed, 7 passed` / rc 1, failing on the **identical test with the identical
parametrisation** as the CI run (`devrc-ci-86zxj`, sha `5de43017`):

```
TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-…-kelp-forest-zach]
```

It modifies no repo file and affects only the process it is preloaded into. It
stalls the **first** fsync only, so a run costs one stall rather than one per
fsync.

## Two fixes that look right and are not

🔴 **CPU/memory requests will not fix it.** The gate task has
`computeResources: null`, so "declare requests" is the natural reading — but
Kubernetes requests govern CPU and memory, **not disk I/O**, and this is an fsync
stall. `null` is also not a devrc oversight: **all 449** taskruns in that
namespace declare none.

🔴 **Raising `HANG_TIMEOUT` again is worse than doing nothing.** 60.0 is already
the symptom fix (raised from 15 on 2026-08-29) and it did not hold. The test file
computes ~320 hung-call sites × 60 s ≈ 5.3 h against a 45 m task budget — and
blowing that budget is the documented state where nothing is posted and the
required checks stay `pending` forever, clearable only by a fresh push.

The real levers are infra and are **not** this repo's to apply: unpin the node or
spread disk-heavy pipelines, cap concurrent runs per node (distinct from
`tekton-supersede`, which only collapses redundant runs of the *same* PR), or give
the workspace isolated storage.
