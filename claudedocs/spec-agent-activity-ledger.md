# Spec: the agent activity ledger — replacing fuzzyclaw's one load-bearing field

**Date:** 2026-08-12
**Status:** spec, not yet dispatched
**Prerequisite for:** retiring fuzzyclaw, retiring the agent-ops TUI

---

## 1. Why

fuzzyclaw is deprecated and to be removed. Measured on the workbench, it is the **sole**
source of three things in `session-manager`:

| | default (fuzzyclaw off, as shipped in #419) | `--fuzzyclaw` |
|---|---|---|
| rows with an age | **0** | 30 |
| statuses | `idle 23, busy 7` — **no `stale`** | `stale 16, idle 7, busy 7` |
| rows with a `claude_session_id` | **0** | 30 |

So the default view today already has no age, no staleness, and no ClickHouse correlation
(`detail`'s session history cannot resolve without the session id). That is a regression
shipped in #419 on the strength of a dogfood finding that fuzzyclaw "contributes nothing" —
true of its `status` field, which read `paused` for every live row, and false of the source.

**Task titles are NOT affected** — they come from pane titles.

## 2. What fuzzyclaw actually provided, and what replaces it

Exactly three fields matter. Everything else it wrote was noise.

| field | used for | replacement |
|---|---|---|
| `last_activity` | age, staleness | per-runtime writer (§3) |
| `claude_session_id` | the ClickHouse join | per-runtime writer (§3) |
| `window_id` | the pane↔record join | already available — `tmux list-panes -F '#{window_id}'` returns one for **every** pane (39/39 measured) |

⚠ `scripts/session-manager` carries a docstring asserting *"the pane format carries no
`window_id`"*. **That is false** and it justified the entire two-key guard/join design — which
produced one 🔴, three fix rounds and three audits. Fix the claim as part of this work; the
simplification it unlocks is out of scope here but should be flagged.

## 3. Design: one field, three writers, one reader

Do not hunt for a single clever signal — the three runtimes have genuinely different shapes.
Define the record, and let each runtime write it.

### Record
```
{ runtime, session_id, last_activity_ts, window_id?, host?, transcript_path? }
```
`window_id`/`host` are null for runtimes with no tmux presence. **Null means "does not apply",
never zero** — the existing convention throughout this tool.

### Writer 1 — Claude Code (local, tmux)
A devrc-owned hook, ~20 lines, in `scripts/claude-hooks/` (six already ship there; copy the
pattern). Fires on `SessionStart`/`Stop`, writes `{window_id, session_id, transcript_path,
ts}`. This is precisely what `scripts/tmux-task-hook.sh` does today via
`fuzzyclaw hook stop` — scoped to the fields that matter and owned here.

Restores **both** the age and the `claude_session_id`, so the ClickHouse join comes back with
it. No separate work needed.

### Writer 2 — opencode (local, tmux)
`scripts/opencode/plugin/guard.js` already exists and the collector already has an `opencode`
source. Same record, same location, from the plugin.

### Writer 3 — clawgate agents (in-cluster, no tmux)
No local writer. `GET /api/agents` (0.7.86, verified 200/824 bytes) returns `lastActivityAt`
already defined as `max(newest persisted transcript row, updatedAt)` — exactly this semantic.
`session-manager` fetches it.

### Reader
`session-manager` joins on `window_id` for local runtimes, and takes clawgate rows directly.

## 4. The entity-model change — decide this deliberately

Consolidating around `session-manager` as *the* "what's being worked on" tool means its
primary entity **stops being a tmux window and becomes an agent run**. Clawgate agents have no
pane, no window index, no host in the tmux sense.

**Decision: one table, add a `kind` field** (`tmux` | `cluster`). Clawgate agents are rows with
the tmux-only fields null, the way remote rows already null `age_secs`.

**Not** a separate section: that forces every consumer to learn two shapes and merge them by
hand, which is the `idle`-bucket conflation inverted — a mistake this tool has now paid for
twice (agents mixed with shells; done mixed with waiting).

## 5. The bar inversion

`tmux-scratch-status.sh` (the status-left `●` waiting marker) and `tmux-claude-counters.sh`
both read fuzzyclaw today. `session-manager` computes a strictly better waiting signal — but it
is a 1–2s scan with an SSH hop and the bar refreshes every 4s, so the bar cannot call it.

**Invert the direction:** a systemd-user timer runs `session-manager --json` on the **same 45s
cadence as `bar-status-poll`** and writes `~/.cache/bar-status/sessions.json`. The status-left
marker and the counters read that file. This is the repo's existing proven pattern.

🔴 The cache **must carry its own timestamp and measured/unmeasured state**, so a stale or
missing cache renders as *unknown*, never as *nothing waiting*. Both existing caches have
failed exactly here: clawgate's `detail` truncates to 6 of 11, and agent-ops printed
`updated 7d ago` over 20-second-old data.

## 6. Removal sequence — supersede first, four phases

1. New ledger writes **alongside** fuzzyclaw. Both live. Verify the new one on **both hosts**.
2. Migrate readers **one at a time**: `session-manager`, `agent-ops` (or its successor),
   `tmux-scratch-status.sh`, `tmux-claude-counters.sh`, `verify-agent-work`,
   `validation/{reconcile,refsources}.py`.
3. 🔴 **A test that fails if any fuzzyclaw read reappears.** Without it, it grows back — the way
   `pkill -f` and blind `git add` did until they were gated.
4. Only then remove `scripts/tmux-task-{hook,resume}.sh` and
   `nix/pkgs/tools/tmux-fuzzyclaw.nix`.

**Never pull the writers first.** They feed six consumers; removing them degrades all six at
once and you cannot attribute which breakage is which.

## 7. agent-ops retirement

Measured: **0 interactive shell invocations** of `agent-ops`, `session-manager` or `standup` in
30 days, against **55 agent references** to agent-ops (30 claude prompts, 25 opencode). The
operator states they do not use it directly. ⚠ TUI usage via `prefix+A` / the bar button could
**not** be measured — the `float,float` window class is shared by btop, disk-explore,
airvpn-detail, deadman and mail-triage, so the 79 window events are unattributable. Treat the
consumer as *agents, not the human*, and do not claim the TUI is unused.

| agent-ops section | disposition |
|---|---|
| `render_blocked` (clawgate) | already in `session-manager` |
| `render_active_runs` | already in `session-manager`, cross-host |
| `render_prs` | `standup` owns it, and is now correct |
| `render_health` (cluster alerts) | `standup` |
| `render_momentum` | `/initiative-scan` |
| mail count | the bar already has a pill — drop |
| `render_local_health` (systemd units) | **genuinely orphaned** — fold into `standup` |
| the `/proc` detector | 🔴 **KEEP** — move to a shared module |

The `/proc` detector must survive the TUI: `scripts/i3status-agent-ops` depends on it, and it
is strictly more accurate than `session-manager`'s `pane_current_command =~ /claude/`, which is
why 15 windows render `? unk`.

## 8. Verification this work must carry

- **Positive control on the ledger**: a fixture that MUST produce a non-zero age/session-id
  count. Report the pair — "N on the control, M live". A reader that returns 0 ages is
  indistinguishable from one wired to nothing, which is precisely the state the default view
  has been in since #419.
- **Both hosts.** The laptop path has repeatedly gone unexercised (unreachable during two
  agent runs). A ledger verified only on the workbench is verified on half the fleet.
- **Watched to fail.** Every guard red before the change, with its own assertion.
- `scripts/session-manager` is extensionless and loaded via `SourceFileLoader`, whose `.pyc`
  cache keys on `(int(mtime), size)` — a fast mutate→restore loop silently runs the previous
  mutant's bytecode. `PYTHONDONTWRITEBYTECODE=1`, `python3 -B`, `-p no:cacheprovider`, fresh
  tree per mutant.

## 9. Open

- Does the ledger live on disk (per-host, like fuzzyclaw) or in ClickHouse? Disk is simpler and
  survives a CH outage; CH is already cross-host. **Recommendation: disk**, because the bar and
  `session-manager` must both work with the homelab unreachable.
- Retention/cleanup. fuzzyclaw accumulated 401 files, 90% stale. Whatever writes this should
  prune, or the same rot returns under a new name.
