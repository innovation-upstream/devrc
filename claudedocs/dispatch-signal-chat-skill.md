# Dispatch brief: Signal chat skill

Companion to `claudedocs/proposal-signal-chat-skill.md` (revised 2026-08-16). Two agents, two
repos, one hard ordering dependency.

## Gate — CLEARED 2026-08-16

| Decision | Answer | Effect on dispatch |
|---|---|---|
| **D1** durability/privacy | **full bodies + attachments** | DDL unchanged; Agent 1 unblocked |
| **D2** phone number | **existing personal number** | Operator step 1 unblocked; register from the home IP |
| **D3** send workflow | **draft → clawgate approval** | Agent 1 scope **grows**: split send surface (`draft_message()` / `send_approved()`), clawgate integration cloned from `scripts/mail-actions/clawgate.py`, and a 10th suite `test_approval_gate.py` |

**Agent 1 is dispatchable now.** Agent 2 still waits on operator step 1 (link the phone via QR
— manual, cannot be delegated) and, for the consumer Deployment only, on Agent 1's PR merging.

---

## Agent 1 — devrc: consumer, DB layer, MinIO, tests, skill

**Blocked on:** nothing — D1/D3 answered. **Not blocked on:** the phone, the cluster, D2 —
everything here is hermetic and runs against fakes.

**Isolation:** `isolation: "worktree"` — this agent modifies files and devrc is a shared
checkout.

⚠ **The worktree does not carry `.envrc`** (gitignored), so the flake toolchain is absent and
the agent will reinvent workarounds. First action inside the worktree:
`cp $DEVRC/.envrc <worktree>/ && direnv allow <worktree>`. Both or neither — an `.envrc` that
exists but was never allowed errors on every `cd`.

### Brief

> Implement the devrc half of `claudedocs/proposal-signal-chat-skill.md` on a feature branch
> ending in a PR. Read the proposal in full first; it is the spec, including the four 🔧 schema
> corrections and the nine-suite test plan, both of which are load-bearing.
>
> Build: `scripts/signal/{consumer.py,_signal_db.py,_minio.py,clawgate.py}` +
> `scripts/signal/tests/` (10 suites + `fixtures/`) + `claude/skills/signal/SKILL.md`.
> Clone the patterns from `scripts/mail-actions/{_db.py,_minio.py,clawgate.py}` and
> `scripts/mail-actions/tests/` — same context-manager shape, same port-forward-vs-direct
> split, same fixture style, same graceful-no-op-without-token behaviour.
>
> **D3 = draft → clawgate approval, not direct-send** (proposal §7). The send surface is split
> in two — `draft_message()` and `send_approved()` — so no single call composes and transmits.
> 🔴 The gate must be **structural**: an un-approved draft must have no code route to the
> Signal API, not merely a documented convention to call approve first. This is the one guard
> where a green-but-bypassable test is worse than no test.
>
> **Hermetic only.** No live Postgres, MinIO, Signal API, or network in any test.
>
> **Register the new target in BOTH places or the gate fails naming it:**
> `scripts/signal/tests` into `HERMETIC_TARGETS` (`scripts/run-tests.sh:288`) *and* into
> `TARGET_FLOORS` (`:431`). They pin each other two-way (`--check-floors`).
> 🔴 The floor is MEASURED, never computed: `git add` the new files FIRST (an untracked test is
> silently absent from the flake source, so the gate reports the old count), run the
> authoritative gate, read that target's `collected=`, write what the gate prints
> (`collected - min(50, max(1, collected/20))`). Never do arithmetic across a conflict.
>
> **Verification bar — report the evidence, not the claim:**
> - every regression test shown **RED at base, GREEN at HEAD**; report the matrix. Label any
>   invariant guard as such rather than counting it as regression coverage.
> - **mutation-test all four 🔧 corrections plus the D3 approval gate** (five): break each on
>   purpose, confirm a test fails with *that guard's specific* error, and confirm the case is
>   reachable (no earlier check short-circuits it). Report survivors — a survivor is the
>   finding, and a surviving mutant on the approval gate blocks the PR.
> - fixture values pairwise distinct, and distinct from any constant an assertion names.
> - any "0 duplicates"/"0 matches" assertion gets a positive control first: feed a case that
>   MUST be non-zero, watch it move, report the pair.
> - `test_skill_doc.py` derives its list from the module source, never a hand-written literal.
> - run the gate via `scripts/gate.sh`; its exit status is authoritative; **90 = could-not-vouch**,
>   read the log. Never read a status through a pipe or a trailing `echo`.
>
> **Git:** feature branch, never `main`. Never `git stash` (repo-global). Never `git add -A` —
> stage explicit paths. Every new file must be `git add`ed or the flake silently omits it from
> the deploy. Re-check `git branch --show-current` immediately before each commit.
>
> Deliver a PR. In the description, state plainly what you verified live vs. what you did not.

### Definition of done
- 10 test suites green in the authoritative gate, with the red-at-base matrix reported.
- Mutation results reported for all four 🔧 corrections **and the approval gate**, survivors
  named. A surviving mutant on the approval gate blocks the PR.
- `--check-floors` passes; the floor is the gate's own printed number.
- `SKILL.md` description written as routing surface (key use case → Zach's literal phrases →
  disambiguation from `mailbox`).
- PR open, nothing committed to `main`.

---

## Agent 2 — homelab-talos: Flux manifests

**Blocked on:** D2, operator step 1 (phone linked), **and Agent 1's PR merging** — the consumer
image is built from devrc source.

🔴 **Do NOT pass `isolation: "worktree"`.** A worktree is built from the *current* cwd's repo,
not the one the brief names — dispatching from devrc would hand this agent a devrc worktree it
cannot do the work in. Instead the agent runs the recipe itself:
`git -C $HOMELAB worktree add ../homelab-talos-signal -b feat/signal origin/trunk`.
That still satisfies the isolation mandate.

🔴 **`homelab-talos` is GitOps-reconciled — committing to trunk IS deploying.** Its own
`CLAUDE.md` declares this, which is what makes it the standing exception to the
feature-branch rule. Work on a branch, and land deliberately.

### Brief

> Implement §1 of `claudedocs/proposal-signal-chat-skill.md` (read it first) in
> `homelab-talos`: `clusters/homelab/apps/signal/` mirroring
> `clusters/homelab/apps/mailbox/`, plus the parent Flux Kustomization at
> `clusters/homelab/flux-system/root-kustomizations/system/signal.yaml` (mirror `mailbox.yaml`
> there — note this path, the draft proposal had it wrong).
>
> Constraints that are not negotiable: **pinned image tag, not `:latest`**; `replicas: 1`
> (signal-cli key material conflicts across instances); `openebs-nvme-1tb` for the state PVC;
> SOPS-encrypted `secrets.enc.yaml` for the Postgres DSN.
>
> Check `homelab-talos/tests/` for existing manifest-validation precedent and follow it; if
> there is none, at minimum confirm `kustomize build` succeeds for the new app and for the
> parent kustomization.
>
> Do not deploy the consumer Deployment until Agent 1's PR has merged and its image exists.

### Definition of done
- `kustomize build` clean for the new app and the parent.
- No `:latest`, `replicas: 1` present, secrets SOPS-encrypted.
- Branch pushed; landing on trunk is a deliberate, separately-confirmed step.

---

## Sequencing

```
Agent 1 (devrc PR) ────────────┐
                               ├──▶ Agent 2 deploys consumer ──▶ step 7 verify
operator links phone ──────────┤
                               └──▶ Agent 2 deploys signal-cli-rest-api + schema
```

Agent 2's first two objects (API + schema) need only the linked phone; the consumer Deployment
needs Agent 1 merged. Agent 1 needs neither the phone nor the cluster — it can start now, in
parallel with the phone linking.

## Verification is the operator's, at the end

Step 7 — send a test message from another device, confirm the Postgres row and the MinIO
object — is the **only** thing that verifies this works. Nothing an agent reports about a
hermetic suite is evidence about the live pipeline; a green gate and a successful `kustomize
build` are prerequisites, not verification.
