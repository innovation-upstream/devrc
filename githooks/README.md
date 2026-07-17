# githooks — auto-run the PR adversarial audit on push

Version-controlled, global git hooks. Today this is one feature:

**Auto-run `/audit-pr` on push of a feature branch, route only 🔴/🟡 findings to
your phone (clawgate), never block the push.** It replaces the hand-typed
"dispatch a subagent to audit this PR for risks/regressions/…" ritual.

## Files

| File | Role |
|---|---|
| `pre-push` | Global dispatcher. Chains to any repo-local pre-push first (never clobbers it), then fires the audit **backgrounded** so the push is never delayed. |
| `audit-on-push.sh` | The backgrounded worker: branch + diff-size + flag gates, then headless `claude -p "/audit-pr current"`, then routes 🔴/🟡 to clawgate. |
| `install.sh` | Sets `git config --global core.hooksPath` to this dir. `--uninstall` reverts. |
| `audit-on-push.env.example` | Config template → copy to `~/.claude/audit-on-push.env`. |

## Install (safe — changes nothing about your push UX)

```bash
~/workspace/devrc/githooks/install.sh
```

This sets the **global** `core.hooksPath` and seeds `~/.claude/audit-on-push.env`
with `AUDIT_ON_PUSH=shadow`. In shadow mode it runs the audit and logs what it
*would* send, but **sends nothing**. Your pushes are unchanged.

## Flag states (`~/.claude/audit-on-push.env`)

- `off` — do nothing.
- `shadow` — run + log what it would surface, send nothing. **Default.**
- `on` — actually POST 🔴/🟡 findings to clawgate (phone buzzes).

```bash
# watch what shadow mode decides:
tail -f ~/.claude/audit-on-push.log
# go live once you trust the signal:
sed -i 's/^AUDIT_ON_PUSH=.*/AUDIT_ON_PUSH=on/' ~/.claude/audit-on-push.env
# back to silent:
sed -i 's/^AUDIT_ON_PUSH=.*/AUDIT_ON_PUSH=shadow/' ~/.claude/audit-on-push.env
# remove the global hook entirely:
~/workspace/devrc/githooks/install.sh --uninstall
```

Other knobs in that file: `AUDIT_MIN_LINES` (default 40 — skip trivial diffs),
`AUDIT_TIMEOUT` (default 300s for the headless call).

## Trigger gates (all must pass, else it exits silently)

1. `AUDIT_ON_PUSH != off`
2. Branch is a **feature branch** — `zach/*`, `feat*`, `fix*`, `feature/*`,
   `hotfix/*`, `chore/*`, `refactor/*`, `wip/*`, or any `*/*`.
   **Never** `trunk` / `main` / `master` / `develop`.
3. Diff (HEAD vs merge-base with upstream/default) ≥ `AUDIT_MIN_LINES` lines.

Only then does the single LLM call (the audit) run. Everything before it is
deterministic + cheap. Clean / 🟢-only audits are suppressed → no notification.

## Notification surface

clawgate (`/api/send`, type `permission`) — the channel already wired for Claude
Code prompts. The POST is fire-and-forget (returns immediately); creds come from
`~/.claude/clawgate.env`. The card shows the branch + a one-line verdict, with
each 🔴/🟡 finding in the context list.

## Composition with repo-local hooks (important git limitation)

`core.hooksPath` is **single-valued**. Two cases:

- **Repo uses default `.git/hooks`** (most repos, incl. devrc): the global hook
  runs. If that repo also has a `.git/hooks/pre-push`, this dispatcher **chains
  to it first** and respects a block from it (the local gate wins; audit
  skipped). No clobbering.
- **Repo sets its own repo-local `core.hooksPath`** (e.g. `datapacket-talos` via
  `scripts/install-hooks.sh` → `.githooks`): that **overrides** the global one,
  so this global hook does **not** run there at all and the repo's gitops-gate is
  authoritative. We deliberately do not touch project repos. If you want the
  audit in such a repo too, add a call to `audit-on-push.sh` from that repo's
  own `.githooks/pre-push` (not done here — that's a project-repo edit).

## Caveats

- **Headless `claude` auth**: relies on `claude -p` being authed for the user
  running the push. Verified working at build time. If auth lapses the call
  fails → logged, no notification, push unaffected.
- **Noise risk**: the LLM decides 🔴/🟡. Shadow mode exists precisely to measure
  the false-positive rate before going live. Read the log for a week first.
- **Cost**: one audit-sized `claude -p` call per qualifying push. The size + flag
  gates keep it from firing on trivial / non-feature pushes.
- **Background timing**: the audit runs after the push returns, so findings
  arrive seconds-to-minutes later, asynchronously. It is a safety net, not a gate.

## Recommended rollout

1. `install.sh` (shadow). Push feature branches as usual for ~a week.
2. `tail ~/.claude/audit-on-push.log` — check the 🔴/🟡 it *would* have sent are
   real and not noisy.
3. Flip `AUDIT_ON_PUSH=on`. Re-evaluate `AUDIT_MIN_LINES` if it's too chatty/quiet.
