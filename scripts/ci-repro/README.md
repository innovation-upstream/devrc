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
(`nodeSelector: kubernetes.io/hostname: talos-xr6-r7p`). Measured 2026-08-31 on run
`devrc-ci-86zxj`: **12 pipelineruns overlapped** the failing window — **7** devrc-ci,
**4** gitops-validate, **1** auditloop.

🔴 **The shared surface is `nix-store-cache`, not the workspace.** Each run's `source`
workspace is a **`volumeClaimTemplate`** — a per-run `local-path` PVC, ~4 Gi, holding
(per the triggertemplate's own comment) "only a 12.8 MB clone plus two build logs".
The volume every concurrent run *shares* is the single static `nix-store-cache` PVC
(30 Gi, `local-path`, bound to that same node) — consistent with
`claude/skills/tekton/SKILL.md:41`. All 239 PVCs in `tekton-ci` are `local-path`, so
they are all the node's local disk either way; the point is which one is contended.

⚠ An earlier revision of this file said the workspace was `emptyDir medium=disk`.
That was **wrong** and is corrected here: the pod's `tekton-internal-workspace`
emptyDir is Tekton's own plumbing, not the pipeline's `source` workspace.

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

`test_subsystem_store_api.py:15342-15348` monkeypatches `api._fsync_dir`, and carries
a 🔴 comment noting that patching the stdlib's `os.fsync` is process-global and would
stall any other thread. This shim is **strictly wider** than what that comment warns
against. It is used anyway because it needs no repo edit and therefore tests the
shipped code path exactly as CI runs it — but the narrower monkeypatch is the right
tool if you only need `_fsync_dir`, and the warning above applies to this shim too.

## Two fixes that look right and are not

🔴 **Raising `HANG_TIMEOUT` again is worse than doing nothing.** 60.0 is already the
symptom fix (raised from 15 on 2026-08-29) and it did not hold. The test file computes
~324 hung-call sites × 60 s ≈ 5.4 h against a 45 m task budget — and blowing that
budget is the documented state where nothing is posted and the required checks stay
`pending` forever, clearable only by a fresh push.

⚠ **"CPU/memory requests cannot fix it" — too strong; corrected.** k8s requests govern
CPU and memory, **not** disk IOPS, so requests would not make fsync faster. But every
devrc-ci run is `nodeSelector`-pinned to one node, so non-zero requests are exactly the
standard mechanism for making excess runs **Pending instead of co-scheduled** — i.e.
they are one way to implement the concurrency cap listed below. (`computeResources:
null` is not a devrc oversight: **479/479** taskruns in that namespace declare none, so
this is a platform-wide default, and changing devrc alone would not stop under-declared
neighbours oversubscribing the node.)

The real levers are infra and are **not** this repo's to apply: unpin the node or spread
disk-heavy pipelines; cap concurrent runs per node (distinct from `tekton-supersede`,
which only collapses redundant runs of the *same* PR); or give **`nix-store-cache`**
isolated or faster storage.

## Counts in this file

`12` overlapping runs, `479` taskruns, `~324` call sites and the two `local-path`
figures were measured on 2026-08-31 and **nothing asserts on them** — they will drift.
Re-derive before quoting them; the commands are in the git history of this file's PR.
