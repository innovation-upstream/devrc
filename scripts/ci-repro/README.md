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
