# Kickoff: implement `scripts/session-manager`

**Date:** 2026-08-11
**Spec:** `claudedocs/proposal-session-manager.md` (merged #389, then §2.3/§6.2 amended here)
**Status:** ready to dispatch

---

## 1. Scope — build this, not the whole proposal

| Phase | Build? | Why |
|---|---|---|
| 1 — core script | ✅ | the deliverable |
| 2 — cross-host SSH | ✅ | the point of "cross-host" |
| 3 — `tail` only | ✅ | read-only, cheap |
| 3 — `signal` / `kill` | ❌ **defer** | destructive, cross-host, over SSH. Separate PR with its own guard tests |
| 4 — skill | ✅ | the entry point (see §2 — a **skill**, not a slash-command) |
| 5 — agent-ops refactor | ❌ defer | rewrites a working 1409-line file to fix an unmeasured cost |
| 6 — bar pill | ❌ defer | duplicates a number the popup already shows |

**Definition of done:** `session-manager --json` returns both hosts' sessions with the
ClickHouse correlation attached; the skill triggers; the suite is green **and** proven
non-vacuous; PR open.

## 2. Decisions already made — do not re-litigate

1. **Laptop is `zach@10.42.0.100`** (nebula). `10.42.0.10` is the *homelab gateway* — using
   it succeeds against the wrong host and silently reports its tmux state as the laptop's.
   Pin this literal in a test (§3 test 12).
2. **No `claude/commands/sessions.md`.** PR #377 migrates all 17 commands to skills. Ship
   the skill in §2.2 of the spec; its front-matter `description` is the trigger surface.
3. **fuzzyclaw is usable, but ONLY intersected with live `tmux list-windows`.** 89% of the
   400 task files are stale (measured). Follow `scripts/tmux-scratch-status.sh:28-34`, which
   already does exactly this. The intersection is a guard, not a filter — it gets a mutation
   test.
4. **ClickHouse: workbench endpoint only** — both hosts ship to the same homelab pod.
5. **This query is verified live; use it verbatim** (there is no `first_message` column):
   ```sql
   SELECT session,
          argMax(project, ingested_at)        AS project,
          argMinIf(text, ts, kind = 'prompt') AS first_msg,
          max(ts)                             AS last_seen
   FROM activity.events
   WHERE source IN ('claude', 'opencode') AND ts > now() - INTERVAL 1 DAY
   GROUP BY session ORDER BY last_seen DESC LIMIT 20
   ```

## 3. Test coverage — `scripts/tests/test_session_manager.py`

Every test below must be **watched to fail** before it counts. For new code that means:
stub the function to a no-op / delete the guard, confirm the test goes red **with that
guard's specific failure**, restore. Report the matrix in the PR.

**Pure parsing (no I/O)**
1. `parse_panes()` on the 8-field pipe format; plus a `pane_title` that itself contains `|`
   (field-count edge) and a pane with empty fields.
2. Codename resolution via `_SLOT_RE` — one valid slot line, one malformed.
3. Stale classification measured at **two points** — exactly at `--stale-threshold` and well
   inside/outside it. One point is not a general claim about a threshold.

**fuzzyclaw staleness guard — the critical one**
4. task file whose `window_id` is live → included.
5. task file whose `window_id` is not live → excluded.
6. **Mutation test:** delete the intersection → test 5 must go red with *this* guard's
   failure, and be reachable (no earlier check rejects the fixture first).
7. unparseable JSON → skipped, no crash.
8. **Field ledger:** assert the exact set of task-file keys consumed. Fails if the set grows
   *or* shrinks — the file carries `transcript_path` too, undocumented in the spec.
9. Fixtures use **pairwise-distinct** values per field, so a wrong-field bug can't pass.

**ClickHouse layer**
10. Pin the literal SQL as a contract — do **not** derive the expectation from the code.
11. **Silent-zero guard.** "0 rows" and "query failed / unreachable" must be distinguishable
    in both output modes. Test all three: real empty, unreachable, and CH error (`Code: 47`
    shape) — the error must surface, never collapse to an empty list.
12. `--no-ch` → the CH client is never constructed (assert, don't infer).

**Cross-host**
13. Pin the SSH target literal `zach@10.42.0.100`. This is the regression guard for the
    review's headline bug; it fails silently in production, so only a literal catches it.
14. SSH failure → laptop marked **unreachable and visible as such** in table *and* JSON, exit
    0 with partial data. A host that vanishes silently is the "reports nothing to do instead
    of erroring" failure mode.
15. SSH success → panes parsed, tagged `host=laptop`.

**Output + exit contract**
16. `--json` golden test with literal expected values for the documented schema.
17. Table mode renders zero sessions + an unreachable host without crashing.
18. Distinct exit codes for "ran, found nothing" vs "could not run". Never let a caller read
    success off a truncated run.

**Instrument validation (do this, report it)**
19. Negative control: break `session-manager` on purpose, confirm the new suite goes **red**.
    A suite never watched to fail is not evidence.

## 4. Repo constraints that will bite

- 🔴 **`git add` every new file.** An unstaged new file is silently omitted from the flake
  deploy — the switch succeeds and the file simply is not there.
- 🔴 **Bump `MIN_TESTS` in `scripts/run-tests.sh:161`** (currently `6745`) to the re-measured
  total. It is set with no headroom, so new tests fail the gate until it moves. The
  per-directory floor applies too.
- **Authoritative gate:** `nix build .#checks.x86_64-linux.pytests`. Read the *counts*, not
  the exit code — the runner parses pytest's summary for exactly this reason.
- **Public repo.** `test_no_public_ips` / `test_no_client_hostnames` run over `claudedocs/`
  too (verified: injecting a public IP turns them red). Private/nebula IPs are fine.
- **Worktree isolation, feature branch, PR.** Never commit to `main` in either checkout.
  Copy `.envrc` in — a fresh worktree has no dev env (`pytest` is absent; use
  `nix-shell -p python3Packages.pytest`).
- **Merged ≠ deployed.** The skill only goes live on `home-manager switch` / `ship.sh`.
- `chquery.py` is a library (`CHClient` / `CHConn`, no `__main__`) at
  `scripts/validation/chquery.py` — needs a `sys.path` insert.
- zsh reserves `status`; use `git -C <path>`, never `cd`.

## 5. Kickoff message

> Implement `scripts/session-manager` per `claudedocs/proposal-session-manager.md`, scoped by
> `claudedocs/kickoff-session-manager.md` §1 — Phases 1, 2, `tail` only, and 4. Do **not**
> build `signal`/`kill`, the agent-ops refactor, or the bar pill.
>
> §2 lists five decisions already made and evidenced; treat them as given. §3 is the required
> test coverage — every test must be watched to fail before it counts, and the mutation test
> on the fuzzyclaw live-window intersection (test 6) is mandatory: that guard is load-bearing
> because 89% of the task files are stale.
>
> Work in a worktree on a feature branch, `git add` every new file, bump `MIN_TESTS`, and gate
> on `nix build .#checks.x86_64-linux.pytests` reading the counts rather than the exit code.
> Open a PR reporting the red→green matrix and the negative-control result.
