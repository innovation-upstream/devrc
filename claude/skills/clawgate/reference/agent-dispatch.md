# clawgate — agent dispatch: durable facts

Read when: you are **dispatching, debugging or reasoning about the agent loop** (a task that should
have produced a PR, a dispatch that never started, the test fixture, `POST /agents`).

🔴 **Current STATUS of the loop lives in `/home/zach/workspace/homelab-talos/containers/clawgate/HANDOFF.md`,
not here.** Two point-in-time claims were written into the skill on 2026-07-31 and were BOTH
superseded within two days — the checkpoint/kickoff-deadline breakage (**fixed in 0.7.80**, pinned by
`provision_kickoff_ctx_test.go` + `checkpoint_test.go`) and the private-clone diagnosis (written
against vendored chart **0.7.0**; the chart is now **0.7.1**, shipped in clawgate **0.7.82**).
Read HANDOFF before repeating any status claim. What follows is only what does NOT go stale.

🔴 **Agents clone over HTTPS with a token credential helper — NOT over SSH.** A previous revision of
this file claimed the opposite and that the `.gh-token` helper "no longer exists"; both were wrong.
Verified on `trunk` 2026-08-12: `internal/agents/provision.go:616` builds
`https://github.com/%s.git`, and `internal/agents/values.go:25` defines `gitCredentialHelper`, which
reads the token from `/root/.gh-token` (written at startup from the `GITHUB_TOKEN` Secret env — note
it does **not** exist yet at clone time, see the comment at `values.go:96`). This matches
`troubleshooting.md` → "Agent `git push` fails with an empty password". The only `git@github.com` in
the tree is a commented-out example in the vendored chart's `values.yaml`.

## The loop DOES close unattended
Verified 2026-07-30/31 over two deliberate runs (clone → implement → test → branch → commit → push →
PR → comment the URL → `ready_for_review`), 138 s and 149 s, the second producing +473 lines with no
scope cut. **So "the 5-minute kickoff deadline is why the loop has never worked" is DEAD as an
opening theory** — it was dormant 2026-07-05 → 07-30 because nothing was dispatched. Don't open with
"make the timeout configurable".

## The test fixture is `ZacxDev/clawgate-loop-sandbox` (public)
Zero dependencies on purpose (`node --test`, no install/build step), so a dispatch exercises the
PIPELINE, not the toolchain. Node because the agent image is Debian 12 + Node 22 with **`go`, `gh`,
`chromium`, `pnpm` all ABSENT**. ⚠ `node --test <dir>` differs across Node versions — write the glob
(`test/*.test.js`). Recipe: task at that repo → dispatch → expect a PR URL comment +
`ready_for_review` in ~2–3 min.

## `POST /agents` is FORM-ENCODED, not JSON
⚠ **There is no `clawgatectl` verb for dispatch and there cannot be a trivial one** — the route is
form-encoded and answers with an HTML fragment, not JSON, so it stays curl. Registered behind
`requireSession` — a literal pass-through (`internal/api/auth.go`) — so on the LAN NodePort it needs
**no auth**:

```bash
curl -sS -X POST http://192.168.50.250:30302/agents \
  --data-urlencode 'action=dispatch' --data-urlencode 'note_id=124' \
  --data-urlencode 'repo=ZacxDev/clawgate-loop-sandbox' --data-urlencode 'repo_branch=main'
```

🔴 **Security consequence:** `clawgate.zacx.dev` is protected ONLY by the Authelia edge. Any future
webhook must live on a **separate hostname**, never a path bypass there — a bypass would put
**unauthenticated agent dispatch** on the internet.

## ⚠ A dispatch that cannot START fails SILENTLY
`provisioningStuckTimeout` (**15m**, `internal/agents/reconcile.go`) marks the **agent** `error` —
but the task stays `in_progress`, `agents.kicked_off` stays `false`, and nothing surfaces it.
**Read the POD LOGS first**: the clawgate-side message ("gateway for X not ready after 3m0s; leaving
provisioning for reconciler to recover") describes the symptom, not the cause, and a reconciler
cannot recover an unrecoverable pod. Do not read it as a provisioning flake — readiness was ~17–21 s
on every dispatch that could start.
