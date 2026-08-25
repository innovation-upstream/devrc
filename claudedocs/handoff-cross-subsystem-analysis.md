---
---
# Handoff: cross-subsystem-analysis — 2026-08-24

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## 🔴 Read this before acting on the earlier version of this doc

The first revision of this file (commit `66d13091`) carried **six recommendations, four of
which do not survive verification**. They were produced by tracing/reading only — nothing in
that revision was measured against live state, and the doc did not say so. This revision
replaces them with measured results.

**Four of the six should not be implemented, and were not.** Two were factually wrong about the
code, one recommended a feature that already ships, and one recommended a change the source
explicitly considered and rejected. Of the remaining two: Rec 4 is **resolved in this PR** (half
of it had already shipped and the archive line was stale), and Rec 5 is deliberately **not
filed** — it has no closing condition. Details below, each with the command or file:line that
settles it.

## Goal
Comprehensive identification, tracing, and analysis of five subsystems: subsystem-index,
session-manager, check-clickup-addressed, clawgate, and object-leak — then a verification
pass over the recommendations that analysis produced.

## State now
- Branch: `handoff-cross-subsystem-analysis`, based on `b0ca088c` (= `origin/main` at time of writing)
- Docs-only. No code changed in any repo, in either revision of this doc.
- No deploy state. **No change was made to clawgate**, in this repo or in `homelab-talos`.
- Decision on the one real finding (clawgate LAN posture): **leave as-is**, operator's call,
  taken 2026-08-24. See "Finding 1".

## Verification results — the six recommendations

### ❌ Rec 1 "Close clawgate auth gap: enforce `requireHookToken` on `POST /api/auto-approve-all`"
**Wrong mechanism, and wrong about which surface is open.**

`requireSession` is a **no-op pass-through** — `containers/clawgate/internal/api/auth.go:40-42`
is literally `return next`. Human auth was removed on purpose; the file comment states
clawgate is fronted by Authelia forward-auth on its public path and **the LAN is treated as
trusted-open**. So every `requireSession` route is unauthenticated on the LAN, not just the
one the original doc named.

`requireHookToken` is enforce-when-set (`auth.go:49-61`), and `CLAWGATE_HOOK_TOKEN` **is**
set in the deployed secret (`clusters/workbench/apps/clawgate/secrets.enc.yaml:10`, wired at
`deployment.yaml:102-106`).

Measured live against the LAN NodePort, no credentials presented, with both controls:

| route | middleware | observed |
|---|---|---|
| `GET /api/tasks` | `requireHookToken` | **401** ← negative control: the probe *can* see a rejection |
| `GET /api/requests` | `requireSession` | **200** |
| `GET /` | `requireSession` | **200** |

```bash
NODE=$(KUBECONFIG=$KC_WORKBENCH kubectl get node nixos \
  -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')   # 192.168.50.250
curl -s -o /dev/null -w "%{http_code}\n" "http://$NODE:30302/api/tasks?limit=1"   # 401
curl -s -o /dev/null -w "%{http_code}\n" "http://$NODE:30302/api/requests"        # 200
```
Read-only routes only — do **not** probe this with `POST /api/auto-approve-all`; that arms
the global approval firehose for real.

Two corrections to the original doc:
- It claimed `DELETE /api/tasks/{id}` (agent-pod teardown) is part of the unauthenticated
  surface. **False** — it is `requireHookToken` (`server.go:434`) and returns 401.
- The proposed fix would have **broken the UI**: `/api/auto-approve-all` is posted by htmx
  from the browser (`internal/ui/components.go:1047,1067`), which carries no hook token. It
  would have disabled the operator's own control while leaving ~30 other UI routes —
  including `POST /ui/decision/{id}`, which approves or denies any pending request — open.

The distroless image has no shell, so `kubectl exec … sh` cannot confirm the token; the
401/200 pair above is what establishes it behaviourally.

### ❌ Rec 2 "Retire transcript mode from check-clickup-addressed"
**Not dead code.** It is a deliberate opt-in (`--transcripts`), **already off by default**
since 2026-08-22, so the ~60s of the ~90s runtime it costs is *already* avoided on every
default run. The source states the decision plainly at
`scripts/check-clickup-addressed/check-addressed.py:1067-1073`: *"It stays available, it is
no longer the default."*

The evidence behind retiring it is **n=3 tickets on a single day**. Deleting a working,
mutation-tested, opt-in path on a 3-sample basis trades optionality for maintenance surface
with no measured benefit — the runtime win was banked when the default flipped. The
inert-flag warning (`:1128-1135`) and the `not_scanned` state discipline mean an operator
cannot mistake a scan-less run for a scanned one, which was the actual hazard.

### ❌ Rec 3 "Add cross-scope query to subsystem-store-api"
**Already shipped, in both layers.**
- Library: `scripts/lib/subsystem_recall.py:2532` — `search(..., all_scopes: bool = False)`;
  CLI flag `--all-scopes` at `:2863`.
- HTTP API: `scripts/subsystem-store-api/server.py:1728` already parsed `all_scopes` from
  the query string. ⚠ **2026-08-25: that HTTP layer is RETIRED and deleted**
  (`claudedocs/decision-subsystem-store-api-retired-2026-08-25.md`). The library half above
  is untouched and still the answer — the rejection of Rec 3 stands on it alone.

The original doc's "cross-scope query gap identified" was a reading error, not a gap.

### ✅ Rec 4 "Ship object-leak stamping or retract the proposal" — RESOLVED in this PR
**Half of it had already shipped; the archive line was stale in both directions.** Fixed here.

- **ClickUp — shipped.** devrc **#768** (merged 2026-08-24) put the `agent/<producer>` tag and
  body marker at the create choke point: `claude/skills/clickup/lib/agent-marker.mjs` holds the
  grammar, `applyAgentStamp()` in `claude/skills/clickup/api/tasks.mjs` applies it, and
  `createTask`/`createSubtask` spread the body and attach the tag. It is *branched on*, not a
  dormant field, and covered by `test/agent-marker.test.mjs`.
- **GitHub — dropped, no producer left.** Never implemented, no open PR, and the producer it
  targeted is gone: `scripts/task-spec-drafter/drafter.sh` now denies `gh issue create`
  (`DRAFTER_DENY_GH`), and `clank-resolver/bot.py` makes no GitHub API calls at all.

`RULES-ARCHIVE.md` is corrected in this PR to say both, so the closing condition is met.

🔴 **How this one was gotten wrong, twice.** The first revision of this doc asserted stamping
was unshipped by *quoting the archive line* rather than checking the mechanism — the same
trace-and-report failure this doc was written to correct, reproduced inside the correction. The
check that settled it was one grep for the label constant. Then, verifying the GitHub half, a
`gh pr list --repo ZacxDev/homelab-talos` returned `[]` — **wrong slug**; the repo is
`ZacxDev/homelab-infra` and `homelab-talos` is only the local directory name. An empty PR list
from a bad slug is byte-identical to "nothing was ever proposed". Re-run with the slug from
`git remote get-url`, never from the directory name.

### 🟡 Rec 5 "Reconciliation signal between the clawgate ClickUp mirror and check-clickup-addressed"
Net-new feature, no measured need behind it. Not a gap — no evidence was gathered that the
two sources actually disagree. If it is ever picked up, **measure the disagreement rate
first**; a daily diff report that always prints zero is a new permanently-green gate.

### ❌ Rec 6 "Make the writeback guard escalation resettable"
**Explicitly considered and rejected in the source**, with reasoning stronger than the
recommendation's — `scripts/claude-hooks/clawgate-writeback-guard.py:348-357`:

> There is deliberately NO `--rearm` flag: the escape from a dismissal is a new session,
> which costs nothing and is unambiguous.

The same block already names the false negative the recommendation "discovered" and prices
it: *"the price of the message being true"*. Re-proposing it without engaging that argument
is a regression in the reasoning, not a finding.

## Findings that DID survive

### Finding 1 — clawgate's web UI is unauthenticated on the LAN, by design
Every `requireSession` route answers **200 with no credentials** to anything that can reach
the workbench node on NodePort 30302 — including approve/deny of any pending permission
request and the global auto-approve firehose. Since clawgate is the approval gate in front of
agents that run commands, a device on the wifi is a genuine escalation path.

This is a **deliberate, documented decision** (`auth.go:14-22, 35-39`), not an oversight, and
the machine surface that carries the destructive API — task CRUD, agent-pod teardown — is
hook-token gated and verified returning 401.

**Decision 2026-08-24: leave the posture as-is.** Recorded here so the next session does not
re-derive it as a bug. If it is ever revisited, the lever is `requireSession` itself — the
comment notes re-adding an app-level gate is a one-function change rather than ~30 route
edits. The cost to weigh: the operator reaches the UI over that same LAN NodePort
(`nix/graphical.nix` references 30302), so any gate needs a credential in browser and phone,
with real lockout risk on the approval path.

### Finding 2 — the original revision of this doc was unverified and did not say so
Four of six recommendations were wrong in ways that a single `grep` or `curl` would have
caught. The failure mode was **tracing code and reporting the trace as a finding**: reading
that a route is wrapped in `requireSession` without reading what `requireSession` does, and
recommending a feature without grepping for its flag. Both are one command away.

## Subsystem inventory (line counts spot-checked, accurate)
- `scripts/lib/subsystem_resolver.py` 1,753L · `subsystem_touch.py` 6,032L · `subsystem_recall.py` 3,063L
- `scripts/session-manager` 4,395L — read-only cross-host tmux + agent-activity view
- `scripts/check-clickup-addressed/` — 5 files; `check-addressed.py` is the orchestrator
- clawgate — Go + htmx PWA in `homelab-talos/containers/clawgate/`; GitOps from `trunk`
  deploys the **manifest, not the code**, so `git log` is not evidence code is live
- object-leak — not a file; the `## object-leak` anchor section in `claude/RULES-ARCHIVE.md`
  (currently the last section, running to EOF). **Cite the anchor, not a line range** — this
  PR's own edit shifted every number after 1484, and `test_rules_size.py` pins the anchor
  while nothing pins the lines.

Structural observations from the original pass that no verification contradicted: the
"library layer never writes" invariant, silent-zero discipline (classified empties rather
than bare zeros), mutation-tested suites, and the honest defaults — fuzzyclaw off, transcript
scan off, stamping openly marked unshipped.

## How to verify this doc
- clawgate auth pair: the two `curl` lines above — **expect 401 then 200**. A 200 on the
  first means the hook token was unset or removed; a 401 on the second means an app-level
  gate was added and Finding 1's decision was reversed.
- Cross-scope search exists: `grep -n "all_scopes" scripts/lib/subsystem_recall.py`
  (the second path this line used to name, `scripts/subsystem-store-api/server.py`, was
  deleted with the hosted service on 2026-08-25 — a `grep` over a missing file exits 2 and
  prints nothing, which reads like "the feature is gone" and is not)
- Transcript mode is opt-in, not absent: `python3 scripts/check-clickup-addressed/check-addressed.py --help | grep transcripts`
- Writeback guard's rejection of `--rearm`: `sed -n '348,357p' scripts/claude-hooks/clawgate-writeback-guard.py`
- ClickUp stamping is real and wired: `grep -n "applyAgentStamp" claude/skills/clickup/api/tasks.mjs`
  — **expect a definition AND a call site**. A definition alone is a dormant field, not a stamp.
- No GitHub producer to stamp: `grep -c "gh issue create" scripts/task-spec-drafter/drafter.sh`
  returns a hit **inside `DRAFTER_DENY_GH`** (a denial, not a use); `grep -c github
  ~/workspace/homelab-talos/clusters/workbench/apps/clank-resolver/bot.py` → 0.

## Next steps, ranked
**None.** All six are closed:

- Recs 1, 2, 3, 6 — **not actionable**; the premises are wrong (see above). Re-opening any of
  them needs new evidence, not a re-reading.
- Rec 4 — **resolved in this PR** (ClickUp shipped, GitHub dropped, archive corrected).
- Rec 5 — **not filed as work.** Per RULES.md's proactivity gate, an item with no named closing
  condition is not a work object. Nobody has measured whether the clawgate ClickUp mirror and
  check-clickup-addressed ever actually disagree, so "add a reconciliation signal" has no
  condition that could end it and would ship a report that always prints zero. If it is ever
  picked up, the first step is measuring the disagreement rate — that has a closing condition;
  the report does not.

Recording this so the list is not mistaken for a queue: this doc mints **no** open objects,
which is the object-leak rule applied to itself.
