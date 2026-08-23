# githooks — pre-push test gate + adversarial-audit-on-push

Version-controlled, global git hooks. Two features, in order on every push:

1. **Blocking test gate (devrc only).** Run the devrc Python suite before the
   push and **block the push if any test fails.** No-op for every other repo.
2. **Auto-run `/audit-pr` on push of a feature branch**, route only 🔴/🟡
   findings to your phone (clawgate), **never block the push.** Replaces the
   hand-typed "dispatch a subagent to audit this PR for risks/…" ritual.

## Files

| File | Role |
|---|---|
| `pre-push` | Global dispatcher. Chains to any repo-local pre-push first (never clobbers it), runs the **blocking test gate**, then fires the audit **backgrounded** so the push is never delayed. |
| `tests-on-push.sh` | SYNCHRONOUS worker: self-detects devrc, filters on changed files, runs `scripts/run-tests.sh --set all` in the repo's **own devShell** (`nix develop`, so a venv owned by the caller's cwd cannot shadow the interpreter), and (mode `on`) **blocks on a test failure (exit 1) or a repo-content guard (exit 2)**. An ENVIRONMENT precondition (exit 3) → warn + allow. No-op for non-devrc repos. |
| `audit-on-push.sh` | The backgrounded worker: branch + diff-size + flag gates, then headless `claude -p "/audit-pr current"`, then routes 🔴/🟡 to clawgate. |
| `install.sh` | Sets `git config --global core.hooksPath` to this dir. `--uninstall` reverts. |
| `audit-on-push.env.example` | Config template → copy to `~/.claude/audit-on-push.env`. |

## Test gate (`tests-on-push.sh`)

The hermetic subset of the suite is enforced independently by
`nix flake check` (`flake.nix` → `checks.x86_64-linux.pytests`, run offline in
the nix sandbox — see `scripts/run-tests.sh` for the exact dir list). This
pre-push worker is the **dev-host tier**: it runs the FULLER set (`--set all`)
before the push so any dev-host-only suites are exercised too, and (mode `on`)
BLOCKS a push whose tests genuinely fail.

**Mode** — `TESTS_ON_PUSH`, from env or `~/.claude/audit-on-push.env` (parallels
the audit's flag):

- `off` — skip the gate entirely.
- `shadow` — run the tests, report the result, **never block** (warn-only).
- `on` / `enforce` — run the tests, **block** the push on a genuine failure.
  **Default (devrc only).**

Behaviour, all failing in the **safe direction**:

- **devrc only** — the worker exits 0 immediately for any repo that isn't the
  devrc flake, so the global hook never starts running pytest on unrelated repos.
- **Changed-files filter** — the gate only runs when the pushed commits touch
  `scripts/`, `flake.nix`, or `flake.lock`; docs-only / nix-non-flake pushes skip
  it. Any ambiguity (new branch whose base can't be resolved, unparseable stdin,
  a `git diff` error) **fails toward RUNNING** — it never silently skips a code
  push.
- **Infra flakiness degrades, never blocks** — the env is the repo's **devShell**
  (never a trusted ambient pytest — the modules import requests/psycopg2/minio/
  yaml at collection). Env preparation is a **separate step** from the pytest
  run: if the env can't be built (offline, uncached, substituter hiccup, no
  `nix`) the worker **warns and allows the push** (exit 0). 🔴 Two DISJOINT
  mechanisms produce that, and conflating them sends people hunting for a
  message that was never printed:
  - the hook's own `degrade()`, **before the runner is ever invoked** — this is
    the offline/substituter/devShell-build case;
  - the runner exiting **3**, for its own environment preconditions (GUARDs
    1b/1c, a failed `cd $ROOT`, the spool `mkdir`, and GUARD 1 **only when run
    outside a sanctioned gate env**).

  🔴 A REPO-CONTENT guard (runner exit **2** — target list, floor table,
  launcher stubs, spool wiring) **BLOCKS** even though zero tests ran: those are
  defects in the repo. So: exit 1 blocks, exit 2 blocks, exit 3 degrades.

  🔴 **GUARD 1 is in both lists.** Its input `REQUIRED_TOOLS` is repo content,
  but its usual failure is environmental — so since devrc#705 it classifies by
  **cause**: `DEVRC_GATE_ENV=1` (set by the devShell's `shellHook` and by
  `checks.pytests`) means the environment already supplies everything
  `gateTools` declares, so a still-missing tool is a repo defect → **exit 2,
  blocks**; unset means the caller simply isn't in the gate env → **exit 3,
  degrades**. Before that, a typo in `REQUIRED_TOOLS` aborted with 3 and the
  push went through with zero tests run.

  🔴 A Tekton PR gate (`tekton/devrc-pytests`, `tekton/devrc-nodetests`) now
  runs on PRs; the older "no CI" claim here was true when written and is not
  now. It runs `nix build .#checks.x86_64-linux.<leg>` and does **not** enter
  the devShell, so it is armed by `checks.pytests`'s own `DEVRC_GATE_ENV`
  export rather than the `shellHook` this hook uses — the two are not
  redundant. (The `nodetests` leg carries no marker and needs none.)
  Its red is **no longer advisory**: measured 2026-08-23, `main`'s
  `required_status_checks.contexts` lists BOTH legs (`strict: false`,
  `enforce_admins: true`), so either one red blocks a *merge*. This hook is
  still the only tier that blocks **a push** — that qualifier is what keeps the
  claim true; unqualified "only tier that blocks" was already wrong (`main`
  requires 1 approving review too) and is now wrong twice over.
- **Escape hatch** — `DEVRC_SKIP_TESTS=1 git push …` skips the gate for one push
  regardless of mode (the flake check / CI still enforce the hermetic subset).

> **flake-check gotcha:** `nix flake check` only sees **git-tracked** files. A
> **new** test file must be `git add`ed before the check (or the pre-push gate,
> which copies via the flake) will run it — an untracked new test is invisible.

## Install

```bash
~/workspace/devrc/githooks/install.sh
```

This sets the **global** `core.hooksPath` and seeds `~/.claude/audit-on-push.env`.
Two independent knobs, seeded from the example:

- **Audit** (`AUDIT_ON_PUSH=shadow`) — logs what it *would* send, sends nothing;
  the audit side changes nothing about your push UX until you flip it to `on`.
- **Test gate** (`TESTS_ON_PUSH=on`) — **in the devrc repo, pushes now run the
  Python suite and block on a genuine failure.** It is a no-op in every other
  repo. Set `TESTS_ON_PUSH=shadow` (warn-only) or `off` to change that, or
  `DEVRC_SKIP_TESTS=1 git push …` to skip a single push.

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
