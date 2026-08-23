# Kickoff: make hook registration complete itself on switch

**Date:** 2026-08-13
**Status:** ready to dispatch — one small unit
**Target:** `nix/home.nix` (an activation entry), plus deploying the registrar

---

## 1. The gap, found by hitting it

`scripts/claude-hooks/next-step-nudge.py` merged (#452) and shipped to both hosts. The file
deployed correctly. It then sat **inert on both machines** because nothing registered it in
`~/.claude/settings.json`, and nothing ever would have.

Two causes, both confirmed:

- **`register-nudge-hook.py` is not deployed.** `home.nix` mentions it only in comments
  (`:1012`, `:1035`) — there is no `home.file` entry, unlike its five siblings
  (`bash-guard.py:944`, `guard_core.py:963`, `audit-pr-nudge.py:986`,
  `shell-env-nudge.py:993`, `search-tool-nudge.py:1004`). Checked directly:
  `~/.claude/hooks/register-nudge-hook.py` does not exist on the workbench.
- **Nothing invokes it.** No activation entry, no systemd unit, no shell hook. It has only
  ever been run by hand, from the repo.

Measured before the manual fix:

| host | nudge deployed | registrar deployed | Stop hooks | nudge registered |
|---|---|---|---|---|
| workbench | yes | **no** | 3 | **no** |
| laptop | yes | **no** | 1 | **no** |

Running `python3 scripts/claude-hooks/register-nudge-hook.py` on each host fixed it
immediately and cleanly — workbench 3 → 4 hooks, laptop 1 → 2, pre-existing hooks preserved,
`SubagentStop` untouched, no other top-level key changed. So the registrar itself is correct;
only its delivery is missing.

⚠ `settings.json` is **per-host and unmanaged** by design — that is why this needs an
activation step rather than a `home.file`.

## 2. What to build

**a. Deploy the registrar** alongside its siblings — a `home.file` entry for
`scripts/claude-hooks/register-nudge-hook.py` following the exact shape used at
`nix/home.nix:944-1004`. Note `dropStaleClaudeHooks` (`:1301`) exists because `force = true`
alone does not displace a pre-existing regular file in that directory; check whether this
path needs the same treatment.

**b. Run it on switch** via a `home.activation` entry. Precedent to copy:
`activityCollectorEnv` (`:449`) and `browserBridgeExtension` (`:527`), both
`lib.hm.dag.entryAfter ["writeBoundary"]` — which is the right slot, since the registrar must
run *after* the hook files are in place.

Requirements:
- **Idempotent.** It already is — re-running registers nothing new. Verify, don't assume.
- **Must never fail the switch.** A registrar error has to warn and continue, not abort
  activation. A broken registration is an inconvenience; a failed `home-manager switch` blocks
  every other change on that host.
- **Must not clobber.** The registrar documents that it "NEVER clobbers hooks it doesn't own";
  three other hooks own `Stop` on the workbench (`task-hook.sh`, clawgate's stop hook,
  `claude-notify.py`) and one on the laptop. Losing clawgate's would silently break remote
  approval.
- **Say what it did.** Print the registered/unchanged line, as the other activation entries do
  — a silent activation step is how this failure went unnoticed in the first place.

## 3. Verification

- 🔴 **The bug was invisible because deploy succeeded.** The switch reported success, the file
  was present, and the feature did nothing. So the test must assert the **end state in
  `settings.json`**, not that the activation ran.
- **Both directions**: from a settings file with no nudge → registered; from one already
  carrying it → unchanged, byte-identical.
- **Preservation**: a fixture whose `Stop` array already holds three foreign hooks must come
  back with four, all three originals intact and in order.
- **Failure path**: an unreadable or malformed `settings.json` must warn and let the switch
  finish. Test it.
- Do **not** test against the live `~/.claude/settings.json` — use a copy under a temp `HOME`,
  as `scripts/claude-hooks/tests/test_register_nudge_hook.py` already does.
- Gate: `nix build .#checks.x86_64-linux.pytests`. **Read counts, not exit codes** — a cached
  build prints an *empty* log, use `nix log`. `nix-instantiate --parse nix/home.nix >/dev/null`
  before switching. Re-pin the relevant `TARGET_FLOORS` entry via the file's own
  `_suggested_floor`; show the arithmetic.

## 4. Out of scope
- Changing the nudge itself, or any suppressor. It is registered and verified firing on both
  hosts.
- Making `settings.json` nix-managed. It is deliberately per-host and mutable; the whole design
  assumes a registrar rather than a declarative file.

## 5. Why this is worth doing rather than remembering
The manual step is invisible when skipped: the file deploys, the switch reports success, and
the feature is simply absent. That is the same shape as this repo's `git add`-or-the-flake-omits-it
trap, and the same shape as `merged ≠ deployed` — a success report about one layer read as a
success report about the layer above. Any future nudge lands the same way.
