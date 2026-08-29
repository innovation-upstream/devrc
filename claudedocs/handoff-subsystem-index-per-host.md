# Handoff: subsystem-index-per-host — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

🔴 `--repo` takes a **PATH**. A bare name resolves against your cwd and exits 3 — that
was fixed this session (#978) so the error now names the mistake and offers `--scope`.

## Goal
The `/analyze-service` subsystem index store is **per-host and unreplicated**, but every
tool reported its findings as global facts. Make the tools honest about whose disk they
read, fix the docs and errors that sent callers down a dead end, and fix a real audit-log
race in the store-api. Replication itself was explicitly NOT in scope.

## State now
- Branch: `main`. 🔴 **The workbench checkout is `[ahead 1, behind 1]`** — commit
  `f04bb5c6 "espanso"` (Zachary Lowden, 2026-08-28 23:07) is **committed to `main` and
  never pushed**. That is the exact state `CLAUDE.md` says silently blocks `ship.sh`
  (`merge --ff-only` refuses, the host is skipped and left as found while still looking
  healthy). Not this session's commit; not touched. **Push it or move it to a branch.**
- 🔴 **Three files are uncommitted AND read by nix at build time**, so the workbench's
  deployed generation is `origin/main` PLUS them, while the laptop is clean — the two
  hosts run different artifacts despite reporting the same sha:
  `scripts/discord-embed-ext/extension/{embed_enlarge.js,manifest.json}` (+67 lines,
  version `0.1.0`→`0.2.3`) and `tests/embed_enlarge.test.mjs`. Hashed against each file's
  last 30 commits: **no match** — genuine uncommitted work, not a stale orphan. Ships on
  every switch; one `git checkout` from gone.

### DONE this session — merged and deployed
| PR | squash | what |
|---|---|---|
| devrc#963 | `20a07e14` | the tools said "the store" and meant one disk |
| devrc#965 | `dec0a939` | `--repo <path>` in the handoff/resume prescriptions |
| devrc#978 | `4c03d43e` | the `--repo` error names the mistake, routes to `--scope` |
| devrc#996 | `1b1f71ad` | audit BEFORE responding + serialised audit sink |

Also done, outside a PR (the store is outside every repo, versioned by its own hourly
autocommit — never run git in it):
- **Scope collision fixed.** `~/workspace/homelab-talos` and `~/workspace/homelab-infra`
  were two checkouts of ONE GitHub repo (`ZacxDev/homelab-infra`); scope derives from the
  **directory name**, so the store held two scopes for one repo (13 entries vs 1).
  `gitops-validate.md` moved into `homelab-talos/`; the `homelab-infra` scope is retired
  with a tombstone README on its `policy:` line. Verified: 14/14 parse, no duplicate refs,
  census total unchanged at 115.
- **The duplicate checkout was retired** (`rm -rf ~/workspace/homelab-infra`). Before
  deleting, 4 branch tips not reachable from any origin ref were fetched into
  `homelab-talos` as `refs/heads/rescued/*`. Three were squash-merge leftovers whose
  content is byte-identical to trunk; **one was genuinely unlanded** —
  `rescued/fix/gitops-validate-right-size-requests` (`61b449e8`).

### Deploy status — be precise
- Both hosts converged to `7d3aec1a` and **switched**; 558 / 507 managed artifacts
  resolve, 0 dangling, 0 stale; cross-host agreement confirmed. Two commits have landed
  on `main` since (`259a34ec`, `f04bb5c6`) so the hosts are now behind again.
- 🔴 **#996 is merged but its artifact is NOT deployed.** `scripts/subsystem-store-api/`
  builds a **container image**; `home-manager switch` does not touch the running pod. The
  merged code and the running pod are separate claims. The pod was not rebuilt or rolled.
- **Verified live end-to-end** (not inferred): the same command on both machines prints
  `host: nixos-d48f5d710b47` (workbench) and `nixos-8d9fd8d444fb` (laptop), and
  `--repo vetr-app` says *"THIS HOST's store … FIRST-ENTRY CASE FOR THIS HOST"* on the
  workbench while the laptop lists a populated 4-entry `vetr-app` scope.

## Open investigations — live diagnosis state

### `TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_DISCARDED` flakes in Tekton
- **Symptom + exact repro:** no local repro found. Observed once as
  `tekton/devrc-pytests = failure` on devrc PR #996 head `9175cedf`, check description:
  `FAILED: pytests — FAILING: TestTheActorComesFromTheTOKEN.test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-kkkkkkkkkkkkkkkkkkkkLLLLLLLL`
- **Observed (with values):**
  - dev-host, whole file, PR branch: `614 passed`
  - nix sandbox (Tekton's own tier) on the **PR branch tree**:
    `TOTAL collected=18288 passed=18286 skipped=2 failed=0` → `RESULT: PASS (exit=0)`
  - nix sandbox on the **merged tree** vs current main: same numbers, PASS
  - isolation flake probe, 25 runs each with `-p randomly`:
    **0 red / 25 on the PR branch, 0 red / 25 on unmodified `main`**
  - second Tekton run on head `e9abc961`: **both checks success**
- **Ruled out:**
  - *Introduced by #996* — `git diff origin/main...<branch>` contains **0** hits for
    `TestTheActorComesFromTheTOKEN`; `_WRITE_INTERLEAVE` is byte-identical to main
    (3 occurrences both sides).
  - *A semantic conflict with newer `main`* — `git log <branch>..origin/main -- <the 3
    files>` is **empty**. (⚠ `git diff` over that range is NOT the check: it lists the
    PR's own files because main lacks its changes. That misread cost a probe.)
- **Leading hypothesis:** a latent timing dependency in a real-concurrency test (it came
  from #948, *"two writers appending at once both survive"*), exposed only under CI CPU
  starvation. This repo's own manifests measure `step-scripts-tests` throttled **18.1%**
  of periods; the workbench is idle by comparison.
- **Next probe:** run the class under artificial CPU pressure rather than more repeats —
  ```bash
  nix develop ~/workspace/devrc -c env PYTHONDONTWRITEBYTECODE=1 \
    systemd-run --user --scope -p CPUQuota=25% \
    python3 -m pytest scripts/tests/test_subsystem_store_api.py -q \
      -k TestTheActorComesFromTheTOKEN -p randomly
  ```
  🔴 A re-run cleared the gate; that is a **workaround, not a fix**. The timing dependency
  is still there. Do not charge this to #996.

### homelab-infra #460 — required check red for an infrastructural reason
- **Symptom + exact repro:** `tekton/gitops-validate` = FAILURE on PR #460.
- **Observed (with values):** the TaskRun condition is
  `TaskRunTimeout: failed to finish within "20m0s"`, and **all 13 steps terminated
  `exit=0`** (`mint-token clone status-pending seed-nix warm-tools kustomize gitleaks
  render-diff sops-rules relay-guard clickup-mirror scripts-tests verdict`). Four runs
  started within 22s (04:28:49–04:29:11); three timed out, one succeeded.
- **Ruled out:** *caused by the diff* — #460 is **comments-only**; parsed YAML objects are
  identical to `trunk`, proven with three positive controls including a
  **byte-length-preserving** mutant inside the folded `description` scalar.
- **Leading hypothesis:** the pipeline's own documented `warm-tools` cost — its store
  entry carries an OPEN bullet measuring a **median 505s of the 20-minute budget** and
  only **46.5%** of runs producing real verdicts.
- **Next probe:** re-run the check; if it times out again with all steps green, the
  20m task budget is the bug, not the PR.

## Next steps (ranked)
1. **Decide #460** (`ZacxDev/homelab-infra`, one file:
   `clusters/homelab/apps/tekton-pipelines/triggers/ci-priority-classes.yaml`). Re-run the
   check, or merge on the evidence that every leg passed and the change is provably inert.
   IN FLIGHT: homelab-infra#460.
2. **Rescue the workbench's stranded work** (`~/workspace/devrc`): push or branch
   `f04bb5c6`, and commit/PR the 3 `scripts/discord-embed-ext/` files. Until then the two
   hosts run different artifacts and `ship.sh` can silently skip this host.
3. **Reconcile the two index-write protocols.** `claude/skills/subsystem-index/SKILL.md`
   states that `/analyze-service` follows
   `claude/skills/analyze-service/reference/write-back.md`, which **materially conflicts**:
   a `y/N` gate this file calls retired, and `Write` where this mandates `Edit` anchored on
   `## Nuance / work-history`. Both markers still present (verified 2026-08-29). The skill
   itself says *"Reconciling them is open work"*. The template hardcodes
   `created_by: handoff`, correct only while there is one caller.
4. **Store hygiene** — run `/prune-index`. Currently 6+ entries over the hard cap
   (`devrc/tests.md` grew **32,058 → 39,176 B during this session**), 21 of ≥365 path
   pointers do not resolve, 48 RESOLVED bullets are evictable (4 resolve only in a repo
   other than their own scope's — confirm attribution before evicting).
5. **Decide the store-api pod** (`subsystem-store` ns on homelab). Running 12d,
   ClusterIP-only, **no consumer on either host** (`grep -c subsystem-store` in
   `subsystem_recall.py` = 0). ⚠ It now serves **129** files, up from 75 earlier today —
   it HAS been re-seeded, so the "stale snapshot" half of the earlier finding is obsolete.
   Build phase 2, or `kubectl delete ns subsystem-store`.
6. **Decide replication.** Recommendation: **git-native sync over nebula**, not the hosted
   API — the store is already 14 independent git repos with an hourly autocommit, a daily
   age-encrypted backup and escrow verification, and the store README's predicate now
   permits a remote on nebula-reachable infrastructure Zach owns. Cost to weigh: three
   tests in `scripts/tests/test_analyze_service_index_commit.py` currently **enforce** that
   the committer configures no remote and pushes to none. That is a deliberate policy
   reversal, not a refactor.
7. **File the `FORGED_actor` flake** as its own item (see Open investigations).
8. **Land or drop `rescued/fix/gitops-validate-right-size-requests`** (`61b449e8`, in
   `~/workspace/homelab-talos`). Its right-sizing is **superseded** — trunk reached
   1.575 CPU via #389+#401, tighter than the branch's 1.80. The other three `rescued/*`
   refs are content-identical to trunk and can be deleted.

## Gotchas / decisions / dead-ends
- 🔴 **The store is PER-HOST and unreplicated, and the two copies are essentially
  disjoint.** Measured: workbench **115 entries / 14 scopes**, laptop **33 / 11**. Four
  scope names exist on both; across those four, workbench holds 104 entries and laptop 10,
  with **exactly 1 entry name in common**. `civitai` 23 vs 1, `datapacket-talos` 47 vs 2,
  `homelab-talos` 15 vs 3 — **zero overlap in all three**.
- **`scope_for_repo` is the ONE shared scope-derivation seam** both reader and writer
  import. Guards belong there, never duplicated into `subsystem_recall`.
- **`this_host()` prints a 12-hex machine-id PREFIX** (`MACHINE_ID_DISPLAY_CHARS`), never
  the whole id — it reaches committed `claudedocs/` in a PUBLIC repo and no content gate
  screens for a machine id. 🔴 `host_label()` and `machine_id()` are untouched: the backup
  object key keeps the FULL id. Truncating a key prefix would repoint every future backup
  object — a data-loss shape, not a privacy fix.
- **#996 changes behaviour on a raising audit sink**: it now fails before any byte is on
  the wire, so a landed write can be reported `500`. Bounded because `If-Match` is
  **mandatory** on that route (428 without it, `If-Match: *` refused) and a retry after a
  landed write gets **412**, so the client learns rather than double-writing.
- **The audit lock is a CLASS attribute on purpose** — `ThreadingHTTPServer` builds a new
  handler per connection, so a per-instance lock would be inert.
- 🔴 **Reading a `nix build` or `ship.sh` result through a pipe destroys the status.**
  `… | tail; echo $?` gives *tail's* rc — hit twice this session, once printing
  `NIX_BUILD_RC=0` over `RESULT: FAIL (exit=1)`. Redirect to a file; grep the runner's own
  `RESULT:` line.
- 🔴 **`-k` filters hide exactly the failures that matter.** A filtered green on the class
  Tekton named looked like exoneration and was evidence about 8 tests. The #996 agent hit
  the same shape independently — it verified an edit with a `-k` filter that excluded the
  seam guard, and both tiers caught what the narrow run did not.
- 🔴 **Three vacuous guards were found this session, all the same shape: a guard whose
  name is wider than its body.** (a) a docstring claiming it prevented aliasing
  `this_host` to `host_label` while every guard injected into the patched name — the
  mutant survived all 16; (b) a bound derived from the constant it was bounding — at 31 it
  leaked 31 of 32 chars and at 0 both hosts printed identically, **suite green both
  times**; (c) `test_an_ABSOLUTE_path_drops_the_cwd_clause`, whose fixture used a
  non-existent path so `resolve()` returned it unchanged and the clause was dropped by the
  *equality* branch — it passed on buggy code.
- **A squash merge never makes the branch head an ancestor of the base.** Verify a landing
  by CONTENT, never `merge-base --is-ancestor`.
- **`main` moved 6+ times during this session**, and `strict` is false on devrc — so every
  green PR check is a claim about that branch, not about the tree the merge creates.
  Gating the merged tree is by hand, every time.

## How to verify
```bash
# 1. the per-host fix is live on BOTH machines (identities must DIFFER)
cd /tmp && python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc | head -3
ssh zach@192.168.50.155 'cd /tmp && python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --scope vetr-app --list | head -3'

# 2. the --repo error names the mistake and routes to --scope (exit 3, unpiped)
cd /tmp && python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo datapacket-talos; echo "rc=$?"

# 3. the scope collision is closed: homelab-infra scope retired, 0 entries
python3 ~/workspace/devrc/scripts/lib/subsystem_touch.py --census | grep -E "homelab-(infra|talos)"

# 4. #996 is on main but its POD is NOT redeployed — these are separate claims
git -C ~/workspace/devrc show origin/main:scripts/subsystem-store-api/server.py | grep -c _audit_lock
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get deploy -o wide
```
