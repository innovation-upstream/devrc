# Handoff: skill-tiering-and-ci-cache — 2026-08-25

> No `clawgate-task:` front matter: `clawgate_handoff.sh resolve` returned **NOTHING RESOLVED
> (0 tasks)**, exit 5. An unknown session id answers 200 with an empty array, so that result
> cannot distinguish "touched no task" from "wrong id". Not a clean bill of health; no task
> was created to fill the gap.

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
Close the skill-listing-budget thread, and fix the Tekton CI contention it surfaced. Both are
now DONE and merged. What remains is **two decisions and one re-measurement** — nothing is
half-built.

## State now
- Branches: none open. All work merged. `devrc` base clone is **5 behind `origin/main`** and
  has unrelated WIP from other sessions — do not treat that as this thread's.

| PR | repo | what | state |
|---|---|---|---|
| #785 | devrc | retire `ux-sweep`/`gpu-operator-check`/`session-audit`; ratchet `13_741`→`12_929`; docstring rewritten to the real budget model | MERGED |
| #784 | devrc | `claudedocs/proposal-skill-listing-tiers.md` — the analysis + adversarial refutation | MERGED |
| #792 | devrc | tier mechanism: `claude/skill-tiers.json`, `scripts/sync-skill-tiers.py`, drift-check arm (rc 22), tests | MERGED |
| #798 | devrc | marks `handoff-skill-listing-budget.md` SUPERSEDED so `/resume` stops serving the false model | MERGED |
| #396 | homelab-infra | `gitops-validate` gets its own `nix-store-cache-2` on `talos-uvh-gtj` | MERGED + Flux-reconciled + VERIFIED |

🔴 **Tiering is MERGED but NOT ADOPTED.** `skillOverrides` is absent from `~/.claude/settings.json`
on BOTH hosts; `claude/skill-tiers.json` is inert until someone runs
`scripts/sync-skill-tiers.py --apply`. That is deliberate — see Next steps #1.

**#396 deploy+verify status, stated separately:** deployed (Flux `applied revision
trunk@c35c78cd`), live objects confirmed (`Task gitops-validate` → `nix-store-cache-2`;
TriggerTemplate → `talos-uvh-gtj`; other four unchanged), and **verified in anger** by a
hand-made PipelineRun `gitops-validate-verify-uvh`: PVC **Bound**, pod on **talos-uvh-gtj**,
all 7 legs green, real status posted to `c35c78cd`. Cold seed cost **557s vs ~306s warm**
(`warm-tools` 298s vs 116s p50). That run is left in place on purpose — it is a genuine
validation of trunk; the pruner will age it out.

## Open investigations — live diagnosis state

### #396's benefit is UNPROVEN — the mechanism works, the improvement is not yet measured
- **Symptom + exact repro:** n/a — this is an unverified *claim*, not a bug. #396 was justified
  by `gitops-validate`'s p90 pod-start (370s) exceeding its own median runtime (306s). Only the
  mechanism has been proven; the improvement has not.
- **Observed (with values):** pod-start latency per pipeline, measured 2026-08-25 over retained
  TaskRuns (p50/p90/max): `devrc-ci` n=565 8s/31s/1610s (pinned) · `gitops-validate` n=304
  13s/**370s**/1151s (pinned) · `clawgate-ci` n=226 10s/13s/282s (NOT pinned) · `remix-ux-audit`
  n=199 9s/15s/81s (pinned) · `naida-ux-audit` n=94 8s/14s/225s (pinned) · `auditloop-ci` n=20
  12s/24s/79s (pinned). `gitops-validate` per-step medians summed = 306s, of which `warm-tools`
  116s.
- **Ruled out:** *pinning is broadly bad* — FALSE, three pinned pipelines have tails as good as
  the unpinned one. *Remove the cache to free the pin* — FALSE for `devrc-ci`: pytests 1326s
  warm (p50, n=102 real runs) vs 1741s with no cache, ~415s worse every run to avoid a 31s p90
  tail. *hostPath cache per node* — IMPOSSIBLE: Talos enforces PodSecurity `baseline`
  cluster-wide; server dry-run returns `violates PodSecurity "baseline:latest": hostPath
  volumes`, with an emptyDir pod accepted as the control.
- **Leading hypothesis:** removing 304 runs/period from `talos-xr6-r7p` drops `gitops-validate`'s
  p90 toward its ~13s p50 and stops it preempting devrc's `ci-bulk` gate pods.
- **Next probe:** re-run the per-pipeline latency query in ~1 week and compare the p90:
  ```bash
  export KUBECONFIG=$KC_HOMELAB
  kubectl -n tekton-ci get taskruns -o json | python3 -c "
  import json,sys,datetime,statistics
  from collections import defaultdict
  def t(s): return datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
  d=json.load(sys.stdin); lat=defaultdict(list)
  for i in d['items']:
      p=i['metadata'].get('labels',{}).get('tekton.dev/pipeline','?')
      st=i.get('status',{}); c=i['metadata'].get('creationTimestamp')
      first=None
      for stp in st.get('steps') or []:
          r=(stp.get('terminated') or {}).get('startedAt')
          if r and (first is None or r<first): first=r
      if c and first: lat[p].append((t(first)-t(c)).total_seconds())
  for p,v in sorted(lat.items()):
      v=sorted(v)
      if len(v)>=3: print(f'{p:26} n={len(v):4} p50={statistics.median(v):6.0f}s p90={v[int(len(v)*.9)]:6.0f}s')
  "
  ```

### `gitops-validate`'s own cache benefit was never measured
- **Symptom + exact repro:** n/a — a gap, deliberately left.
- **Observed (with values):** its `warm-tools` step costs **116s p50 / 202s p90 even WITH a warm
  cache** (n=112). The cold seed on the new node cost 298s. So the cache is worth roughly
  180s/run to this pipeline — inferred from the seed, never isolated.
- **Ruled out:** running the ablation experimentally — its `status-pending` step POSTs commit
  statuses to real SHAs, and `clickup-mirror` may write externally. Not worth the side effects.
- **Leading hypothesis:** the cache is clearly worth keeping for this pipeline too; #396 keeps
  it, so this gap does not block anything.
- **Next probe:** only worth doing if someone proposes removing this cache. Ablate with BOTH
  arms missing the same steps (`status-pending`, `clickup-mirror`) so the comparison is fair —
  see the dead-end note below about why a one-sided ablation misleads.

## Next steps (ranked)
1. **DECIDE: adopt skill tiering, or leave it.** Standing recommendation: **leave it unadopted.**
   Nothing truncates at 1M (whole listing ~20,050 chars vs a 30,000 budget = 0.67×), and
   `LISTING_TOTAL_CEILING_CHARS` has **68 chars of headroom**, so the next skill of any size reds
   that gate loudly and forces the decision then — with the mechanism already built. Adopting is
   one reversible command: `scripts/sync-skill-tiers.py --apply` (workbench first, soak a week,
   then laptop). ⚠ Before applying, consider promoting **4 of the 13** Tier B skills back to
   Tier A — `sglang`, `initiative-scan`, `close-the-loop`, `verify-agent` all read as
   symptom-triggered rather than named-by-Zach, which is exactly where a mis-tier bites. Costs
   ~1,300 of the 3,907 chars saved. Touches `claude/skill-tiers.json` only.
2. **RE-MEASURE #396's p90 in ~1 week** (query above). This is the claim the change rests on.
3. **DECIDE: the per-entry eviction rule.** Both ratchets say "a new skill cannot be added
   without an eviction" but only bound an *average* entry — `subsystem-index` (182 chars) already
   slipped under the 250-char headroom, which is how it fell to 68. The claim is now honest in
   the code; the stronger rule is a policy change nobody has asked for. Redundant while headroom
   is 68, moot if tiering is adopted. **Recommendation: skip it.**
4. Consider a **second cache for `devrc-ci`** if `talos-xr6-r7p` contention persists after #396.
   Same pattern, `talos-uvh-gtj` is now taken, and `talos-deu-s2q` (4 CPU / 15Gi) is too small —
   so this needs a new node or a different approach. Do NOT remove devrc-ci's cache (measured net
   loss + correctness risk).

🔴 **This list is a WORK QUEUE WITH NO LOCK** — every `/resume` session draws from it, so a
better ranked list produces more duplicate work, not less. Nothing here is in flight; all five
PRs are merged. Items 1 and 3 are DECISIONS for Zach, not work to pick up.

## Gotchas / decisions / dead-ends

**Corrections to the prior handoff, now marked in it (#798) — do not re-derive:**
- The listing budget is `floor(contextWindow × zx(model) × skillListingBudgetFraction)` **characters**.
  🔴 `zx` is NOT a constant: 4 for models through 4.6, **3 for `claude-opus-5`+** ⇒ **6,000 @200k,
  30,000 @1M**. The old "no tokenizer makes it fit" break-even argument is MOOT.
- "Descriptions are being silently dropped today" was **FALSE** for the live config (0.67×,
  always on 1M). The whole initiative addressed a problem that was not occurring.
- *Bundled* skills are EXEMPT from truncation and spend the budget first — the opposite of
  "additional". "bundled" ≠ "builtin": `init`/`security-review` are builtin and NOT exempt.
  Non-devrc entries measured at **7,007** chars.
- The gate undercounts the real charge by **`5n − 1`** (194 at 39 entries, 179 at 36) — quote the
  form, never the figure.
- `skillOverrides` can NEVER touch a plugin skill (resolver hard-returns `on`), and
  `~/.claude/settings.json` is the LOWEST-precedence ordinary scope.
- Latent: the budget pass measures `.length`, the renderer `Bun.stringWidth`. One emoji in a
  description desyncs devrc's gate from what is charged.

**Two of my own conclusions were too broad — the narrow versions:**
- "Removing the cache changes verdicts" → **"removing `seed-nix` changes verdicts."** The devrc
  ablation dropped the volume AND the step, producing 43 failures on a revision that passes with
  it. A fresh second cache still gets seeded, which is what makes #396 safe — proven: the
  verification run passed all 7 legs cold.
- "Unpin the high-volume pipelines" → wrong for `devrc-ci`, where pinning is a net win. I
  recommended this twice from the skill's numbers without measuring, and both errors had the same
  shape: treating one pipeline's figures as a property of the system.

**Dead end — a one-sided ablation measures nothing.** My first devrc experiment compared a warm
arm running ALL steps against a cold arm; worse, the warm arm's `pytests` took 3s because nix
served a cached build for an already-built revision (only 4% of real runs are that fast). Both
arms must run the same step set, and the control must be calibrated against real-run medians.

**CI is congested by Zach's own concurrency, structurally.** 25 agent worktrees in devrc; five
pipelines shared one node because they shared one RWO PVC. `exited with code 255` is the
congestion signature and names whichever step was running; the check then posts `COULD NOT RUN:
<leg>` — a broken gate, not a bad change. 🔴 **Do not "fix" it by re-pushing.** An empty-commit
re-push is the remedy for a *stuck* check, not a saturated node; I did it twice during
congestion, which the runbook explicitly forbids ("push, wait for the queue to drain, push").
Confirmed by other people's PRs: #797/#799/#801 carried the same errors and recovered on their
own once the queue drained.

**Every instrument that mattered failed at least once first, each returning a confident zero:**
grepping the 20KB `bin/claude` wrapper stub instead of the 323MB `.claude-wrapped` payload;
`xargs -0 command grep` (a builtin, so nothing executed); measuring against a stale base clone;
`gh pr view --json statusCheckRollup` reporting `description: null` where the commit status API
had `COULD NOT RUN`; and zsh eating `$SHA:` as a history modifier. A positive control caught each.

**`enforce_admins: true` on devrc `main` with no override** — when both required Tekton legs fail
to report, nothing merges repo-wide. Escape hatch (Zach's call only):
`gh api -X DELETE /repos/innovation-upstream/devrc/branches/main/protection/required_status_checks`.

## How to verify
```bash
# devrc gate (standalone clone, origin REMOVED — never a worktree of the base clone):
nix develop . --command bash scripts/gate.sh          # GATE: RESULT=PASS exit=0

# the tier ledger's two-way pin (the property that makes tiering scale):
nix develop . --command python3 -m pytest scripts/tests/test_skill_tiers.py -q   # 41 passed

# #396 landed and is live (not just merged):
export KUBECONFIG=$KC_HOMELAB
kubectl -n tekton-ci get task gitops-validate -o jsonpath='{.spec.volumes[*].persistentVolumeClaim.claimName}'   # nix-store-cache-2
kubectl -n tekton-ci get triggertemplate gitops-validate-template -o json | grep -A1 nodeSelector                # talos-uvh-gtj
kubectl -n tekton-ci get pvc nix-store-cache-2                                                                   # Bound

# tiering is NOT adopted (expect False on both hosts):
python3 -c "import json,os;print('skillOverrides' in json.load(open(os.path.expanduser('~/.claude/settings.json'))))"
```
