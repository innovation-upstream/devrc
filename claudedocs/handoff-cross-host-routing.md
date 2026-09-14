---
clawgate-task: 574
---
# Handoff: cross-host-routing — 2026-09-14

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

Make a Claude Code session on one host able to (a) tell which machine a peer session is on and
(b) act on the other machine's browser — closing the gap where a `bw://` tab reference minted on
the laptop was a dead end from workbench. Three clawgate cards (561, 562, 574) plus one issue
(#1601) that 562's audit surfaced.

**This effort is COMPLETE and deployed.** What follows is state-of-record plus four discretionary
follow-ups, none of which has an external forcing function.

## State now

- Branch / PR: **none open.** `devrc` is clean on `main`; all four PRs merged and deployed.
- Both hosts converged at `dbefe6fa` via `ship.sh` (workbench + laptop, `✅ VERIFIED … + switched`).

**DONE this session** (all merged to `devrc` main, all squash commits):

| what | PR | squash | clawgate |
|---|---|---|---|
| `bw://` wrong-host refusal → runnable handoff, exit **4** | #1598 | `08a9cdec` | 561 ✅ |
| `scripts/peer-host` — which machine a peer session is on | #1599 | `af943906` | 562 ✅ |
| `host_label` DERIVES identity, never defaults to `workbench` | #1630 | `06287019` | issue #1601 ✅ |
| foreign `bw://` ref executes on the naming host over SSH | #1662 | `dbefe6fa` | 574 ✅ |

**Verified live on the DEPLOYED artifacts** (not worktrees), both directions:
- `peer-host` on PATH both hosts → `~/workspace/devrc/scripts/peer-host`; `datapacket-talos-ef`→`workbench`, `vetr-20`→`laptop`.
- `host_label` with `ACTIVITY_HOST` unset: workbench→`workbench`, laptop→`laptop` (base returned `workbench` on both — that was the bug).
- Cross-host screenshot: wb→laptop `2256x1389` written locally; laptop→wb `3427x1229` written on the laptop. **The differing viewport geometries are the evidence the capture ran on the far machine** — a local capture carries the caller's dimensions.
- Zero E2BIG, zero stderr, zero leaked temp files in both directions.

**IN FLIGHT:** nothing.

## Open investigations — live diagnosis state

### `test_browser_agent.py::test_the_release_handler_EXITS_rather_than_resuming[INT]` — "pre-existing load flake" is INFERRED, never measured
- as-of: 2026-09-14

- **Symptom + exact repro:** fails intermittently during full-suite runs of
  `scripts/browser-bridge/tests/`; passes when run alone. Seen on #1630's and #1662's branches.
- **Observed (with values):** passes **alone at `origin/main`** (6.8 s) and **alone at HEAD**
  (6.6 s). I ran the **full** `scripts/browser-bridge/tests/` suite at `origin/main` under load
  and got **943 passed, 0 failed** (388 s) — i.e. I could **not** reproduce the red at base. The
  #1662 implementer saw it fail with the box at **load average 55–65**; my clean base run was at a
  materially lower load. So the two observations are not comparable.
- **Ruled out:** the PR's own diff as a cause — `git diff origin/main` touches **zero** lines of
  `browser-agent`, measured on both #1630 and #1662.
  via: command
- **Ruled out:** "it is a handler defect" — PR **#1585** (MERGED, squash `22ddd8dc`, test file
  only) established it is a **starved instrument**: the test `killpg`s the wrapper after a warm
  marker, and a stall splits the outcome into three bands (<3 s → 143/130, the real verdict;
  3–12 s → rc 2, passes for the wrong reason; >12 s → wrapper already exited, zombie,
  `proc.wait()` returns stored status **0**).
  via: doc
- **Ruled out:** "#1585 fixed it" — the `[INT]` test **does** consult the guard #1585 added
  (`_warm_window()`, `test_browser_agent.py:1745`, sole caller `:2111`) and it **still flaked**
  afterwards, on two separate PRs. So the guard narrowed the window without closing it.
  via: measurement
- 🔴 **Name correction, measured:** the subsystem-index bullets for this call the helper
  `_warm_window_lost()` — that name exists in **zero** files. The real one is `_warm_window()`.
  A sibling `OPEN:` bullet states its closing condition in terms of the non-existent name, so it
  is unsatisfiable as written; the action itself IS still open (`test_a_run_killed_mid_bootstrap_RELEASES_the_warm_lock`
  at `:1649` does not call the guard — measured 0). Corrected in the index 2026-09-14.
  via: command
- **Ruled out (WEAKLY):** "it also flakes at base under equal load" — this is the **inference**,
  not a measurement. Nobody has run the full suite at base at load 55–65.
  via: assumed
- **Leading hypothesis:** wall-clock starvation per #1585's band analysis; the 5 s deadlines lose
  to a saturated box. Not a defect in any branch that has been blamed for it.
- **Next probe:** run the full suite at `origin/main` **while deliberately loading the box to
  ~55–65** and see whether `[INT]` reds there. Until that runs, "pre-existing" is unproven:
  ```bash
  git -C /home/zach/workspace/devrc worktree add /tmp/wt-intflake --detach origin/main
  # saturate: e.g. `nproc`-many busy loops in another shell, confirm `uptime` ~55-65
  cd /tmp/wt-intflake && python3 -m pytest scripts/browser-bridge/tests/ -q
  ```

## Next steps (ranked)

1. **#1656 — sibling activity units write bytecode into nix-store deploy paths.** `devrc`,
   `nix/home.nix` + `scripts/tests/test_transcript_push.py`. `activity-collector` is fixed
   (`PYTHONDONTWRITEBYTECODE=1`); every sibling (`keylog`, `claude-activity-source`,
   `browser-bridge`, `browser-activity-receiver`, `claude-log-rotate`, `keylog-spin-capture`,
   `mention-known-repos-refresh`) has `guard=0`. 🔴 Unlike #1630, their `__pycache__` **already
   exists on disk**, so the fix needs a one-time purge alongside the setting or it closes the door
   after the fact. Guard shape to copy: `test_the_ACTIVITY_COLLECTOR_unit_REFUSES_TO_CACHE_BYTECODE`.
   forcing: none
2. **#1602 — two independently-written readers of `~/.claude/sessions/*.json` already disagree.**
   `devrc`, `scripts/session-resolve` vs `scripts/peer-host`. They differ on stale records (kept
   vs dropped), `tmux`-less records (invisible vs first-class), and scope (local vs every host).
   Surfaced only because consolidating created the second reader.
   forcing: none
3. **#1616 — `test_an_out_of_scope_host_is_not_reported_as_a_failed_search` cannot observe its own
   claim.** `devrc`, `scripts/tests/test_peer_host.py:808`. Its fixture has no out-of-scope host,
   so the named branch never executes; mutating `scripts/peer-host:623` to
   `for h in HOST_NAMES` leaves it GREEN. The hazard IS covered by
   `test_host_scoping_excludes_rather_than_silently_missing`, so this is a misleading name, not a
   coverage hole.
   forcing: none
4. **Settle the `[INT]` flake** per the Open-investigations probe above, or accept it in writing.
   `devrc`, `scripts/browser-bridge/tests/test_browser_agent.py`. It has now cost attribution work
   on two separate PRs.
   forcing: none

## Gotchas / decisions / dead-ends

- 🔴 **`local out` does NOT clear an inherited `export` attribute** (bash). Zach's interactive zsh
  exports `out=/home/zach/workspace/homelab-talos/outputs/out`. A function doing
  `local out; out="$(…600 KB…)"` therefore puts the payload in the **environment as one string**,
  and `MAX_ARG_STRLEN` (**131,072 B**, far below `ARG_MAX`) makes **every subsequent `execve`
  return E2BIG** — `rm`, `env`, `wc` all failed. Symptom was a baffling
  `rm: Argument list too long` with a 29-byte argument. Fix: keep large payloads out of shell
  variables entirely. **Generic variable names in a shared shell are a live hazard.**
- 🔴 **The bridge must stay loopback-only.** `server.py` binds `127.0.0.1` AND enforces a
  Host-header allowlist (`_ALLOWED_HOSTS`) against DNS-rebinding. Cross-host was solved with **SSH
  exec** — no new listener, the far bridge still loopback, the CLI runs *on* that host. Exposing
  it on nebula was considered and **rejected**; do not revisit without a reason that survives that
  contract.
- **Decision (Zach, 2026-09-14): every op proxies cross-host, no read-only/mutating split, no
  opt-in flag.** A read-only-first design was offered and declined. Accepted consequence: a
  cross-host `nav`/`click`/`type` changes the other machine's screen and `browser activate` raises
  a window there. **Do not re-introduce a gate "for safety"** — a test fails if one is.
- 🔴 **A fix can ship INERT.** #1630's collector change would have been dead in production: the
  deployed `collector.py` is a *flattened* store symlink with no `scripts/lib` beside it, so
  `import host_label` could never resolve. Caught only by checking the deployed artifact, not the
  tests. `abspath` (not `realpath`) is what makes the deployed `lib/` reachable.
- **Dead end: `[ref]` in the harness peer listing is not derivable.** Two independent sweeps
  (~17 candidate inputs × 17 hash algorithms × offsets, plus raw substring, over all 48 records)
  found zero consistent derivation. `peer-host` accepts name / pid / session-id prefix / tmux
  address instead. `test_no_ref_selector_is_implemented` pins the absence deliberately.
- **#1630's audit ladder ran six rounds and every round's findings were in the PREVIOUS round's
  fix**, never in the derivation being protected. It caught an append that corrupted a systemd
  `EnvironmentFile` (would have eaten `CLICKHOUSE_PASSWORD`), guards invisible to the
  merge-gating tier (nested `nix-build` cannot run in `checks.pytests`), and the inert import
  above. The diff did **not** shrink on rework (+1641 → +3433); the risk profile did.
- **main's mutation-anchor red is FIXED upstream** by `c1600c93` (#1665) — it was 6 anchors going
  0x after #1648 moved the ceiling's constants. It was inherited, not caused, by #1630/#1662; do
  not re-diagnose it.

## How to verify

```bash
# peer-host, live on both hosts, as a bare command from ANY repo's cwd
peer-host datapacket-talos-ef        # -> workbench
peer-host vetr-20                    # -> laptop
peer-host no-such-peer               # -> rc 3 ; a duplicate name -> rc 2

# host_label derives rather than defaulting (the #1601 fix)
env -u ACTIVITY_HOST HOST_LABEL_ENV_FILE=/nonexistent python3 \
  ~/workspace/devrc/scripts/lib/host_label.py          # workbench here, laptop there

# cross-host bw:// — foreign ref runs on the naming host, result comes back here
B=~/workspace/devrc/scripts/browser-bridge/browser
ssh zach@10.42.0.100 "$B --instance work tabs"        # pick a laptop tabId
$B context bw://laptop/work/<tabId>                    # rc 0, laptop's tab, no paste step
$B screenshot /tmp/x.png bw://laptop/work/<tabId>      # PNG lands HERE; geometry = laptop's
ls /tmp/browser-proxy-err.* 2>/dev/null | wc -l        # MUST be 0 (the E2BIG leak regression)

# the guard that must never break: a foreign ref must not drive the LOCAL same-labelled profile
#   label `work` exists on BOTH hosts — that is the only case where it can go wrong.
```
