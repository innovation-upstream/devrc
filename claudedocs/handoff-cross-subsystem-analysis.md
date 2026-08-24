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

**Nothing from the original recommendation list was implemented, and none of it should be.**
Two were factually wrong about the code, one recommended a feature that already ships, and
one recommended a change the source explicitly considered and rejected. Details below, each
with the command or file:line that settles it.

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
- HTTP API: `scripts/subsystem-store-api/server.py:1728` already parses `all_scopes` from
  the query string.

The original doc's "cross-scope query gap identified" was a reading error, not a gap.

### 🟡 Rec 4 "Ship object-leak stamping or retract the proposal"
**Legitimate, and still open.** `claude/RULES-ARCHIVE.md:1484` reads "**Stamping — IN FLIGHT,
not shipped.**" A deferred promise in an archive nobody re-reads is exactly the object-leak
shape the section itself describes.
Closing condition: either the `agent/<producer>` label mechanism exists and the line names
it, or the line is deleted. Mechanical, checkable by reading that one line.

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
- object-leak — not a file; anchor section in `claude/RULES-ARCHIVE.md` (~1416–1505)

Structural observations from the original pass that no verification contradicted: the
"library layer never writes" invariant, silent-zero discipline (classified empties rather
than bare zeros), mutation-tested suites, and the honest defaults — fuzzyclaw off, transcript
scan off, stamping openly marked unshipped.

## How to verify this doc
- clawgate auth pair: the two `curl` lines above — **expect 401 then 200**. A 200 on the
  first means the hook token was unset or removed; a 401 on the second means an app-level
  gate was added and Finding 1's decision was reversed.
- Cross-scope search exists: `grep -n "all_scopes" scripts/lib/subsystem_recall.py scripts/subsystem-store-api/server.py`
- Transcript mode is opt-in, not absent: `python3 scripts/check-clickup-addressed/check-addressed.py --help | grep transcripts`
- Writeback guard's rejection of `--rearm`: `sed -n '348,357p' scripts/claude-hooks/clawgate-writeback-guard.py`
- Stamping still unshipped: `grep -n "IN FLIGHT" claude/RULES-ARCHIVE.md`

## Next steps, ranked
1. **Rec 4** — resolve the `agent/<producer>` stamping promise in `RULES-ARCHIVE.md:1484`:
   ship the label or delete the sentence. Smallest real item here.
2. Nothing else from the original list. Recs 1, 2, 3 and 6 are closed as **not actionable**
   for the reasons above; re-opening any of them needs new evidence, not a re-reading.
