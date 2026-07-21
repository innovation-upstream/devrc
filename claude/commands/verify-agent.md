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

> **⚠ Trust / arbitrary code execution.** The gate runs the TARGET repo's OWN
> build/test commands and honours a repo-local `.verify-agent.json` — that is
> code execution BY DESIGN. Do **not** point it at a repo whose contents or
> `.verify-agent.json` you do not trust; it will execute them. Per this repo's
> CLAUDE.md, agent-handed worktrees and fuzzyclaw task data are **UNTRUSTED** —
> treat a worktree you did not create the same way (glance at it first).

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
table. Pass `--strict` to ALSO treat a dirty tree / unpushed commits as hard
failures (default: those are WARN). Note an **INCOMPLETE** stack is ALWAYS a
hard non-pass, strict or not.

## Verdict / exit-code semantics

| verdict | exit | meaning |
|---|---|---|
| `PASS` | 0 | every DETECTED stack's gate ran and passed (WARNs allowed unless `--strict`); or no stack present (nothing to verify) |
| `FAIL` | 1 | a gate actually ran and failed (typecheck/test/build/vet/lint red), or `--strict` + a WARN |
| `INCOMPLETE` | 1 | a DETECTED stack's gate could NOT run — missing toolchain, no resolvable gate command (e.g. a monorepo with no root script + no runnable member), or deps not installed. **This is NOT a pass** |

The core guarantee: a **detected** stack that was not actually verified never
reads as green. A repo with **no** stack present is a legitimate PASS.

## What it checks (per stack actually present — never hard-fails a missing one)

- **TypeScript/JS** (`package.json`): the true `typecheck` script or `tsc
  --noEmit` (NEVER `build`) **and** the `test` script; detects pnpm/npm/yarn/bun;
  fans out across workspace members for a monorepo with no root script. A
  detected TS stack with no runnable gate ⇒ **INCOMPLETE**.
- **Go** (`go.mod`): `go build ./...` + `go vet ./...` + `go test ./...`
  (adds `-race` when cgo + a C compiler are available; falls back without `-race`
  and says so otherwise). Nested `go.mod` modules are each built too.
- **Python** (`pyproject`/`requirements`): `ruff check` + `pytest` when configured;
  a detected python project with neither ⇒ **INCOMPLETE**.
- **Nix** (`flake.nix`): `nix-instantiate --parse` by default; `nix flake check`
  if the repo's `.verify-agent.json` opts in.
- **Git completeness**: uncommitted/untracked, unpushed vs upstream, open PR
  (best-effort `gh`) — the "committed but stopped" tell.
- **Stale-worktree footgun**: a broken/missing `node_modules` is reported as an
  ENV issue (WARN) AND the TS gate becomes **INCOMPLETE** — the typecheck genuinely
  didn't run, so a false "cannot find module" flood is attributed correctly
  rather than read as real code errors, and it does not count as green.

Exit code is non-zero on any hard non-pass (FAIL or INCOMPLETE).

## Then

1. **Read the verdict, not the agent's summary.** `verdict: FAIL` **or
   `INCOMPLETE`** means the agent's "done" claim is not verified regardless of
   its prose — surface the failing/unverified check(s) and their output; fix,
   run the gate where the toolchain exists, or hand back before merging.
2. **INCOMPLETE** → the gate could not actually verify a detected stack. Do not
   treat it as a pass: run it in an environment with the toolchain, install deps,
   add a gate/`skip` in `.verify-agent.json`, or verify by hand.
3. **WARN on git completeness** (dirty tree / unpushed) against a "done" claim →
   reconcile: did the agent actually finish and push? Commit/push or flag it.
4. **`env:node_modules` WARN** → the errors are environmental; re-install deps in
   that worktree, don't chase them as code bugs.
5. **PASS** is the mechanical floor, not proof of behaviour — for a nontrivial
   runtime change still drive the actual flow (`verify` skill) before claiming
   it works.

Provenance honesty: this gate proves the authoritative checks pass and the tree
is complete; it does NOT prove the feature behaves correctly.
