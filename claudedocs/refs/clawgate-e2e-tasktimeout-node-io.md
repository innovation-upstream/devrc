# `clawgate-e2e` `TaskRunTimeout` on trunk — node I/O, not a stranded advisory lock

Demoted evidence for **rank 61** of `claudedocs/handoff-tmux-webapp.md`. Measured 2026-09-14.
The handoff carries a pointer to this file; the numbers live here so the handoff stays inside its
size budget (`test_no_handoff_doc_exceeds_its_budget` — `claudedocs/refs/` is exempt, handoff docs
are not).

## Symptom + exact repro

`tekton/clawgate-e2e` hit `TaskRunTimeout` at its 40m **task** budget on two consecutive `trunk`
revisions, both on node `talos-xr6-r7p`:

| run | revision | window (UTC) |
|---|---|---|
| `clawgate-e2e-2rkwn` | `1894511` | 01:32–02:12 |
| `clawgate-e2e-9g5m6` | `24be212` | 01:58–02:38 |

## The hypothesis this refutes

The handoff and the subsystem store both carried **a stranded `pg_advisory_lock` holder** as the
leading theory. It is wrong. `SQLSTATE 57014` *is* present — which is exactly why it read as a lock.

- The `57014` cancellations land on the **migration DDL** (`CREATE TABLE IF NOT EXISTS
  schema_migrations`, and the Phase 2 schema), **not on a lock wait**.
- The single `STATEMENT: SELECT pg_advisory_lock($1)` line is preceded by
  `FATAL: terminating connection due to administrator command` — the harness terminating its own
  migrator, not a stranded holder blocking one.

## The one-read discriminator

The postgres sidecar's own `checkpoint complete` lines. **The checkpointer takes none of the locks a
migration contends on**, so `write=` / `sync=` / `total=` are a lock-free measurement of the same
disk. **fsync alone:**

```
2rkwn:  write=3.208 s, sync= 37.824 s, total=109.098 s
        write=0.039 s, sync=  1.062 s, total= 42.959 s
        write=0.026 s, sync=  0.947 s, total= 99.772 s
        write=0.648 s, sync=130.644 s, total=142.531 s
9g5m6:  write=0.001 s, sync= 32.107 s, total= 71.615 s
        write=0.001 s, sync= 35.244 s, total=132.602 s
        write=1.180 s, sync= 13.185 s, total= 39.656 s
```

No lock produces a 130-second fsync.

## Corroboration off the database

- **Neither run was aborted by a failing test.** They exhausted the budget mid-suite: 2rkwn reached
  test **126**, 9g5m6 reached **158**, against a real total of **224**.
- DB-independent Playwright tests stalled in the same windows: a 1–2 s test took **44.6 s**, another
  **2.2 m**; several passed on retry (2.2 m → **16.3 s**).
- Every failure in both runs was **timeout-length** (45 s – 2.2 m, Playwright's own per-test
  timeout). RULES' load-flake tell is decisive: *load inflates every test in the run, a failed
  assertion inflates exactly one.*
- `node_pressure_io_stalled` on `talos-xr6-r7p`, with the two runs that **succeeded on that same
  node** earlier the same day as controls:

  | window | mean | frac of minutes >50% stalled |
  |---|---|---|
  | `xsjhs` **succeeded** 21:59–22:28 | 0.334 | 0.28 |
  | `6bjbn` **succeeded** 01:04–01:28 | 0.247 | 0.12 |
  | `2rkwn` **timeout** 01:32–02:12 | 0.445 | 0.38 |
  | `9g5m6` **timeout** 01:58–02:38 | 0.466 | 0.38 |

## Why the collision happens

`clawgate-e2e`'s `required` rule is `NotIn [talos-jkj-deb, talos-uvh-gtj]` — three candidates, **no
weighting between them**. `devrc-ci` is *pinned* to `talos-xr6-r7p` by its node-local RWO
`nix-store-cache` PVC and cannot move.

Placement over 3 days to 2026-09-14 (Loki pod/node labels):

| pipeline | `talos-xr6-r7p` | `tekton-ci-1` | other | n |
|---|---|---|---|---|
| `devrc-ci` | **1057 (62%)** | 281 (17%) | 365 | 1703 |
| `clawgate-e2e` | **30 (37.5%)** | 48 (60%) | 2 | 80 |

**11 concurrent `devrc-ci` pods** shared the node with the e2e run during both failures (plus 3
clawgate-e2e, 1 clawgate-ux, 1 comic-flex-ci).

## Controls

**1. Same revision, idle node.** `24be212` re-run pinned to `tekton-ci-1`:
**224 passed, 2 skipped, 7.5 m of tests**; pipeline green in 11m40s, against a 40 m timeout on
xr6-r7p. This also clears the two specs that had failed *even on retry* in 9g5m6
(`auto-approve.spec.ts:152`, `launch-link.spec.ts:172`) — stall casualties.

⚠ That run's `finally` reporter posted, flipping `24be212`'s `tekton/clawgate-e2e` status
red → `success`. **A manual control run rewrites a gate verdict — intend that before creating one.**

**2. The preference steers — paired probe, with its control arm.** A bare Pod carrying the task
pod's real totals (**2100m / 2624Mi** = step 2000m/2368Mi + postgres sidecar 100m/256Mi), same
tolerations and affinity, created and deleted:

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| **with** `preferred` toward `ci-burst` | `tekton-ci-1` | `tekton-ci-1` | `tekton-ci-1` |
| **control** (no preference, = trunk) | `talos-xr6-r7p` | `talos-xr6-r7p` | `talos-xr6-r7p` |

## The fix

`ZacxDev/homelab-infra#819` — `preferredDuringSchedulingIgnoredDuringExecution` weighting the
`ci-burst` label the podTemplate **already tolerates**. Preferred, not required: the `required`
exclusion is untouched, so the three-candidate set, the absent `nodeSelector` and the "a full cluster
means PENDING rather than the control plane" property all still hold.

🔴 **The PR's own CI run does not test the change.** TriggerTemplates are Flux-reconciled cluster
objects, so the run #819 triggers uses the **live** podTemplate from `trunk`. The scheduling probe
above is what verified it, because CI structurally cannot.

Guard: `scripts/tests/test_clawgate_e2e_burst_preference.py`, registered `RUN` in
`scripts/tests/ci-manifest.txt`. 9 mutants, each killed by its **own** assertion message, run against
that file alone so no kill is credited to a sibling guard; unmutated control green before and after;
template byte-identical afterwards.

Suite, branch vs pristine-trunk baseline, run **sequentially** under the closure `ci-manifest.txt`
documents: `files_run` 90→91, `tests_ran` 2499→2503, `failed=''` both sides, identical `broken` set
(3 CEL files, pre-existing).

## What this does NOT fix

`talos-xr6-r7p` is **chronically** I/O-saturated — 25–33% stalled even during *successful* runs, and
0.32 while #819 was written. This moves `clawgate-e2e` out of the blast radius; it does not treat the
disk, and `devrc-ci` is still pinned there by its PVC. **Rank 18 (`clawgate-ci`'s `go` leg reds on
postgres-backed tests under contention) is the same mechanism** and is not fixed by #819 —
`clawgate-ci` has no such preference.

## Method notes worth reusing

- **A reaped Tekton pod is not lost evidence — Loki outlives it.** Both pods were gone within
  ~30 min, which reads as "the window closed". Streams:
  `{namespace="tekton-ci", pod=~"clawgate-e2e-<run>-.*", container="step-e2e"}` and
  `container="sidecar-postgres"`. ⚠ `obs-read` prints a per-stream **summary** with one sample line,
  not the lines — for content, port-forward `svc/loki-gateway` and query
  `/loki/api/v1/query_range` with `direction=forward` and a real `limit`.
- **Adding a file to `scripts/tests/` breaks CI until it is registered in
  `scripts/tests/ci-manifest.txt`.** `run-ci-suite.sh` enforces the ledger in both directions and
  exits **2** (`error`, never a silent pass). `RUN` = unittest discover, `RUN-PYTEST` = pytest, and
  **any skip inside a `RUN` file marks the whole file `broken`** unless the line carries
  `ALLOW-SKIPS` — so `self.skipTest()` in a new guard is a CI break, not a neutral opt-out.
- **Do not run two `nix-shell` CI suites concurrently on one host** when comparing branch vs
  baseline. `test_vetr_e2e_infra_attribution.py` hardcodes `127.0.0.1:5174`, so the second run
  fabricates `Address already in use` → `rc=2 ran=0`. Its own harness says *"This is NOT a finding
  about the commit"*, and the arithmetic gave it away (30 tests lost, 4 added = the observed −26).
- ⚠ `nix-shell` in a repo with an exported `shellHook` creates `$PWD/.venv`, which then **shadows the
  interpreter nix just provided** (`No module named pytest` from `<worktree>/.venv/bin/python3`).
  `run-ci-suite.sh` defends against this itself (`_pin_interpreter`); an ad-hoc
  `nix-shell --run "python3 …"` does not, and it leaves a stray `.venv/` in the worktree.
