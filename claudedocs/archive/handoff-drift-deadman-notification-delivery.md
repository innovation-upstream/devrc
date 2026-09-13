# Handoff: drift-deadman + notification delivery — 2026-08-12

## Goal
A host had silently stopped receiving changes and nobody noticed for days. Build a passive deadman
that catches it, then make its alert actually reach a human — which turned out to be the hard half.

## State now
- **Repo:** `innovation-upstream/devrc`, `main` @ `6979c07`. Base clone clean, both hosts converged + switched.
- **The deadman is LIVE**: `drift-check.timer` `enabled/active` on the workbench (6-hourly, workbench-only —
  gated on `~/.server-mode`, which only that host has). Next run 23:30.
- **Verified end-to-end on the host that runs it** (not inferred): timer fires autonomously → real check →
  real exit code → unit `failed` → `OnFailure` → toast **rendered on screen while dunst was paused**.
- **Notification volume ~330/day → ~60/day**, deployed and live.
- DND is now **off** on the workbench (`paused=false`, queue drained to 0). Nobody knows who unpaused it —
  dunst has not restarted since 08-05, so something called `set-paused false`.

### Landed this session (all merged to `main`)
| PR | what |
|---|---|
| #365 | clawgate task-api doc: create returns `{id}` only — killed a phantom-null provenance report |
| #366 | rescued 3 un-pushed `rig-control` commits stranded on the workbench |
| #368 | rescued the stranded `handoff-rig-control-brightness.md` |
| #370 | rescued an uncommitted espanso `:acq` edit |
| #367 / #369 / #371 | the drift deadman + 2 rounds of audit fixes |
| #372 / #373 | analyze-service: bootstrap probe refused outright; the mirror silent-zero in `list_scopes` |
| #381 | dunst: unit-failure toasts defeat DND (`override_pause_level=100`, `fullscreen="show"`) |
| #406 | enabled the deadman |
| #409 | dunst `history_length` 40 → 300 |
| #416 | scoped the settings.json parity check to real drift |
| #423 / #427 | tests must never reach a real launcher — and the guard now covers all 17 targets, not 1 |
| #430 | notification volume ~330 → ~60/day |
| #433 | `CPU_MON_IGNORE=…,farthest` |

## Open investigations — live diagnosis state

### The deadman has not yet had an autonomous GREEN run
- **Symptom:** the unit's last timer run was `17:30:15` with `ExecMainStatus=15` (host parity).
- **Observed:** that run **predates both fixes** — #416 (parity scoping) shipped after it, and the
  laptop permissions sync ran at `18:08:50` (backup `~/.claude/settings.json.bak-20260812T180850`).
  A manual `./scripts/drift-check.sh` on the workbench after both returned **`exit=0`** with
  `[parity] settings.json top-level key sets AGREE apart from the per-host keys below.`
- **Ruled out:** a real remaining parity difference — the manual run is green, and the three
  ignored keys (`theme`, `effortLevel`, `voice`) print with their reasons.
- **Leading hypothesis:** simply stale; the next timer run will be the first green one.
- **Next probe (verbatim):**
  ```
  ssh zach@192.168.50.250 'systemctl --user show drift-check.service -p Result -p ExecMainStatus -p InactiveExitTimestamp'
  ```
  Expect `Result=success ExecMainStatus=0` with a timestamp after 23:30 on 08-12.

### earlyoom coalescing is validated as a mechanism, never in situ
- **Symptom:** earlyoom bursts produced 163–208 toasts/day. #430 coalesces them via a shared dunst stack tag.
- **Observed:** dunst 1.13.2 collapses a shared `stack_tag` (proven, with a bogus-option negative control
  that the config parses). **No real OOM burst has been watched through the rule.**
- **Ruled out:** #409's earlier attempt failed for a *harness* reason, not a real one — `fullscreen_suppress`
  was routing its probes straight to history, so tagged AND untagged controls both read 0. That
  confounder is understood and fixed; the zero was from a control that never observed anything.
- **Next probe:** wait for a real OOM burst, then
  `journalctl --user -u systembus-notify --since -1d | wc -l` against
  `dunstctl history | jq '[.data[0][]|select(.appname.data=="system-notify")]|length'` — the first
  counts kills, the second counts toasts that were actually shown. Coalescing works iff they diverge.

### The dunst `waiting` queue count never reconciled
- **Observed:** 244 (#409) → 48 mid-session → 90 → 83 → **0 now**, while ~190/day were being dispatched
  into a paused dunst. Something caps or evicts and nobody determined what.
- **Status:** now moot in practice (DND off, queue drained). Recorded because the arithmetic never
  worked and the explanation is still unknown — if DND goes back on, it matters again.
- **Cannot be probed directly:** dunst 1.13.2 exposes the waiting queue **only as a count**; no
  `dunstctl` subcommand, no `org.dunstproject.cmd0` method and no `--json` form enumerates its contents.

## Next steps (ranked)
1. **Confirm the first green autonomous run** (probe above). Until then the deadman is proven to fire,
   not proven to go quiet correctly on a schedule.
2. **Live with ~60/day for a day.** If it still grates the knobs are measured:
   `GLOBAL_COOLDOWN=900` → 30/day, `=1800` → 17.6/day. One env var on the unit, reversible.
3. **`#355` — `🔴 DO-NOT-MERGE-YET: airvpn killswitch — close the fail-open`.** Six days old. A fail-open
   killswitch parked in a do-not-merge state is the classic thing that gets forgotten.
4. Residual from #427: an unpinned `PATH=/usr/bin:/bin` clobber at
   `scripts/collector/tests/test_ch_regrowth.py:1092`; nothing reaches a launcher through it today.

## Gotchas / decisions / dead-ends
- 🔴 **A guard that exists is not a guard that covers.** The no-launch guard protected 1 of 17 test
  targets while reading as systemic. `run-tests.sh` runs one pytest per target directory.
- 🔴 **The toasts were fired by our own verification discipline.** The 158-toast burst came from
  *base-ref measurement sandboxes*: at older revisions there is no `_toast_runner` seam, so `fire_toast`
  inlines `subprocess.run` and a monkeypatch on that attribute cannot stop the launch. Proving a seam
  test red requires exactly the tree state in which the seam does not exist.
- 🔴 **`systemctl show` on a NON-EXISTENT unit returns `Result=success ExecMainStatus=0`.** Nearly
  reported the notifier as clean off that. The instance is `notify-failure@drift-check.service.service`
  — `%n` already includes `.service`.
- 🔴 **An empty `dunstctl history` cannot distinguish "no toast" from "toast still displayed".** History
  holds only *displayed-then-dismissed*. `dunstctl count displayed` / `is-paused` are the signals that differ.
- 🔴 **dunst's docs are wrong about pause levels.** `dunst.5` says a notification shows when its override is
  *greater than* the level; the implementation compares `>=`. `set-paused true` sets level **100**, the
  max — so following the man page would have concluded the bypass was impossible. Measured, not read.
- 🔴 **`fullscreen_suppress` was filterless** and routed failure toasts straight to history whenever any
  fullscreen window had focus. A second, independent delivery hole invisible from inside the script.
- 🔴 **`CPU_MON_IGNORE` matches the comm, which Linux truncates to 15 chars.** Farthest Frontier arrives as
  `Farthest Fronti`, so the intuitive `frontier` entry matches nothing and looks like it worked.
  `is_ignored` also splits on spaces, so a two-word entry becomes two substrings.
- **`~/.claude/settings.json` is per-host and unmanaged by design** (`nix/home.nix:839,910`). #416 scopes
  the parity check with an explicit, fail-closed enumeration (`theme`/`voice`/`effortLevel`), each carrying
  its reason. `permissions` deliberately stays IN the comparison — the laptop had none at all.
- **Deliberately NOT done:** raising `CLAUDE_NOTIFY_MIN_SECONDS`. Raising it deletes information;
  coalescing defers it, and it is what makes the volume replay exact rather than a model.
- **An unrelated file was lost and recovered**: `nix/pkgs/tools/screenarc.nix` was untracked on the laptop,
  got cleaned by something, and survived only in the stash stack (`git show 'stash@{0}^3:<path>'`).
  Zach has since said screenarc is not needed — no action.

## How to verify
```bash
# the deadman, end to end, on the host that runs it
ssh zach@192.168.50.250 'cd ~/workspace/devrc && ./scripts/drift-check.sh; echo "exit=$?"'   # expect exit=0
ssh zach@192.168.50.250 'systemctl --user list-timers drift-check.timer --no-pager'          # enabled/active

# the alert actually reaches a human, even under DND (run ONLY if you want a real toast):
#   set DRIFT_REPO at a diverged /tmp clone, systemctl --user start drift-check.service,
#   then check `dunstctl count displayed` MOVES while `waiting` does not.

# notification volume after the cut
ssh zach@192.168.50.250 'grep -c . ~/.claude/claude-notify.log'   # compare across days
```
🔴 **Never flush, clear or unpause the workbench's dunst queue** — it is user data.
