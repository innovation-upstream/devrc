---
name: verify-agent
description: "Deterministic post-agent verification gate: re-run the AUTHORITATIVE build/typecheck/test/vet gate + git-completeness + stale-deps checks against a repo/worktree, and READ that verdict instead of trusting an agent's 'done' claim."
argument-hint: "[TARGET path] [--strict] — default: cwd. e.g. '~/workspace/civitai', '. --strict'"
allowed-tools: Bash, Read
---

# /verify-agent — mechanical gate over an agent's "done" claim

An agent (subagent/dispatched Task) just reported "build green / committed / done."
Do **not** take that prose at face value — the recurring failures are: a proxy
command hid a real error (`vite build` does NOT typecheck), the agent "committed
but stopped" (dirty tree / unpushed commits), or a stale worktree `node_modules`
symlink produced a flood of false "cannot find module" errors. Run the
deterministic gate and read ITS output.

This is the **cheap mechanical structural gate**. It is NOT `/audit-pr`
(adversarial LLM review) and NOT the `verify` skill (e2e behaviour). Use those
too when warranted — this one is the fast, always-run floor.

## Run

`$ARGUMENTS` → optional TARGET path (default: cwd / the worktree just handed
back) + optional flags passed through (`--strict`, `--no-gh`, `--timeout N`).

The script is `scripts/verify-agent-work` in this repo (devrc). Invoke it against
the target. It needs a `python3`; if the bare call reports no interpreter, wrap
it in `nix-shell -p python3 --run "…"`.

```
scripts/verify-agent-work <TARGET> --json
```

Prefer `--json` so you parse the structured verdict; drop it for the readable
table. Pass `--strict` to treat a dirty tree / unpushed commits / stale deps as
hard failures (default: those are WARN, only real gate failures are FAIL).

## What it checks (per stack actually present — never hard-fails a missing one)

- **TypeScript/JS** (`package.json`): the true `typecheck` script or `tsc
  --noEmit` (NEVER `build`) **and** the `test` script; detects pnpm/npm/yarn/bun.
- **Go** (`go.mod`): `go build ./...` + `go vet ./...` + `go test -race ./...`.
- **Python** (`pyproject`/`requirements`): `ruff check` + `pytest` when configured.
- **Nix** (`flake.nix`): `nix-instantiate --parse` by default; `nix flake check`
  if the repo's `.verify-agent.json` opts in.
- **Git completeness**: uncommitted/untracked, unpushed vs upstream, open PR
  (best-effort `gh`) — the "committed but stopped" tell.
- **Stale-worktree footgun**: a broken/missing `node_modules` is reported as an
  ENV issue and the TS gates are SKIPPED, so false "cannot find module" errors
  are attributed correctly rather than read as real code errors.

Exit code is non-zero on any hard failure.

## Then

1. **Read the verdict, not the agent's summary.** If `verdict: FAIL`, the agent's
   "done" claim is wrong regardless of what its prose said — surface the failing
   check(s) and their output; fix or hand back before merging.
2. **WARN on git completeness** (dirty tree / unpushed) against a "done" claim →
   reconcile: did the agent actually finish and push? Commit/push or flag it.
3. **`env:node_modules` WARN** → the errors are environmental; re-install deps in
   that worktree, don't chase them as code bugs.
4. **PASS** is the mechanical floor, not proof of behaviour — for a nontrivial
   runtime change still drive the actual flow (`verify` skill) before claiming
   it works.

Provenance honesty: this gate proves the authoritative checks pass and the tree
is complete; it does NOT prove the feature behaves correctly.
