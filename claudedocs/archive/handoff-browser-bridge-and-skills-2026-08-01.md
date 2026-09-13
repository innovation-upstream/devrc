# Handoff: browser-bridge hardening + skill compression — 2026-08-01

## Goal
Take `browser agent` from "runs, capability unmeasured" to a measured default; harden the
bridge's deploy path, poll loop and instance reporting; then cut the per-invocation token
cost of every skill. All of it gated on live verification rather than green suites.

## State now
- Branch `main` @ **`bcbd8cc`**. Both hosts converged + switched. All 4 Brave profiles on
  extension **0.7.0**, `extension_stale:false`.
- Suites: **node 454 / 0**, **pytest 366 / 0**, `nix build .#checks.x86_64-linux.pytests` **green**.
  (The flake gate was RED at `main` for an unknown period before today — 41 `test_server.py`
  tests were silently skipping for want of `curl` on PATH. Fixed in #251.)
- Skills: **277 KB → 192 KB (−31%)**. `scripts/browser-bridge/SKILL.md` **12,007 / 12,288**
  (281 B headroom).
- Working tree carries long-standing operator-owned uncommitted files (`.npmrc`,
  `.serena/project.yml`, `nix/pkgs/tools/default.nix`, two `scripts/tmux-task-*.sh`).
  **Not mine, left untouched.**

### DONE — merged and live-verified
| PR | What | How verified |
|---|---|---|
| #242 | Git-immune extension path (`~/.local/share/browser-bridge-ext/`, atomic `mv -T --exchange`) + `ping` op | activation ran for real; 0 leftovers; `ping`→`unknown_op` on old build, `pong` on new |
| #243 | Deterministic hidden-tab auto-wake | live audit: `auto_wake` → `auto_wake_exec`×2 → `auto_wake_ok woke:true settleMs:1500` |
| #248 | `known_instances`/`missing` + `instance_lost` detector | e2e on an isolated port: one `DISCONNECTED` line, 0.058s fail-fast, exactly one `instance_lost` |
| #249 | Poll-loop wedge bound at one choke point (`EXEC_OP_BUDGET_MS` 18s) | wedge test red on revert; 4 audit rounds |
| #250/#255 | Device emulation + `documentPredatesEmulation` hint | live: `393x852 dpr=3`, 1179×2556 PNG, hint fires and goes silent after re-nav |
| #251 | Position-independent CLI flags; **flake gate red→green** | both orders → identical wire body; gate rc 0 |
| #257 | Session id from POSIX session, not `$PPID` | **e2e over ssh on the workbench** — see below |
| #263 | `context` op was **dead on main** — never in `server.py` `ALLOWED_OPS` | pytest 342/3 → 345/0 |
| #253 | `browser agent`-first default flip | measurement-gated; evidence-rule corrected |
| #259–#262, #260, #264, #265, #267 | Skill compression, close-the-loop restructure, frontmatter, ceiling | coverage checks; byte counts |

### The measurement that gated the flip
`claudedocs/browser-bridge-deepseek-measurement-2026-07-31.md`. **p = 13/14**, ~$0.0025/run,
median 22.65s, laptop `.155` / `personal` / opencode 1.18.4 / `deepseek-v4-flash`, **each goal
run once**. One confident-wrong `ok` (7.7%) — the throttled-shell case.

## Open investigations — live diagnosis state

### 1. The auto-wake CURE is unproven (mechanism is proven)
- **Symptom:** F2 — agent read an unrendered shell and answered `WAKE-RIG-SHELL (waiting for
  frames)` with `status:"ok"` and faithful evidence quoting it.
- **Observed:** auto-wake **mechanism** verified live (audit trail above). But F2 is **not
  reproducible**: with `BROWSER_AGENT_AUTO_WAKE=0`, and again with
  `BROWSER_AGENT_ALLOWED_OPS=nav,text,html,eval` (wake denied outright), both controls
  returned the **correct** answer. Manual immediate read still shows
  `hidden=True, "WAKE-RIG-SHELL (waiting for frames)"`, so throttling still bites — the rig
  simply renders before the agent's slower read lands.
- **Ruled out:** environment change (throttle still reproduces manually); auto-wake being
  inert (audit shows it firing).
- **Leading hypothesis:** the rig's render deadline is shorter than agent round-trip latency.
- **Next probe:** add a wake-rig fixture that stays an unrendered shell for **≥30s**, then
  re-run F2 with and without `BROWSER_AGENT_AUTO_WAKE=0`. That makes the cure testable on
  demand instead of by luck.

### 2. auditloop credentials — possibly unrotated (OPERATOR DECISION)
- **Observed:** the auditloop skill carries two 🔴 "chat-exposed → ROTATE" notes.
- **Unknown:** whether rotation ever happened. Not determinable from the repo.
- **Why it matters:** a `$50`-cap key on a public-internet app. Cheap insurance either way.
- **Next probe:** operator confirms; if unrotated, rotate and delete the notes.

### 3. Three close-the-loop items marked "operator: not yet" since June
`nats` backup gap, context-capturing durability guard, node-saturation guard. **Confirm they
are parked, not forgotten.**

## Next steps (ranked)
1. **Answer #2 (credentials)** — the only open item with a security dimension.
2. **Build the ≥30s wake-rig fixture** (#1) so the auto-wake cure stops being unfalsifiable.
3. **Review PR #266** — `docs(browser-bridge): wake is not a passive read; tab discard loses
   injected state`. **Not mine**; another session's, still open.
4. Clawgate follow-ups **approved but not applied** (in #260's body): the Task-API-table move,
   and the kubeclaw 0.7.0→0.7.1 re-sync note — *if the re-sync is genuinely pending, do the
   re-sync rather than delete the note*.
5. `close-the-loop`'s graduated-autonomy signal **does not exist** — it must be rebuilt on
   checkpoint decisions over runbook-dispatched work. `clawgate_permission_decisions_total`
   is a **usage** counter (~4k prompts/day, ~97% auto-approved) and is NOT a successor.

## Gotchas / decisions / dead-ends

- 🔴 **A live probe against a DIRTY tree proves nothing.** The `context` op was dead on `main`
  (in `protocol.js`, absent from `server.py`'s `ALLOWED_OPS`, so the server rejected it before
  dispatch). It looked fine because an **uncommitted** `server.py` edit had been baked into the
  deployed copy by a switch. The #242 drift test caught it; I overrode it and three agents who
  read it correctly.
- 🔴 **Validate a harness against a known-bad state before trusting green.** NINE harnesses
  reported success while testing nothing: runner absent from PATH; `diff` defaulting to unified
  output so a byte-identical control passed; a seed tree indistinguishable from its target; a
  bash subshell inheriting `$$`; a crashed sweep leaving a mutation applied; a
  `Promise.race`-against-a-spinner whose dangling promise hung a file instead of reporting; the
  repo's own flake gate skipping for want of `curl`; and **two frontmatter extractors that
  re-matched `---` in the body** and reported false parse-failures.
- 🔴 **A green mutation sweep is a claim about the mutations you imagined.** 18/18 and 20/20
  each had blind spots only a differently-constructed sweep found — one where dropping a
  signature component survived and would have silently reintroduced the bug the PR fixed.
- 🔴 **Merged ≠ deployed.** `~/.local/share/browser-bridge-ext/` and
  `~/.config/browser-bridge/server.py` change only on `home-manager switch`. Sequence is
  merge → `--ff-only` → **switch** → reload. That decoupling is the *point* of the git-immune
  path, and it is exactly what makes it easy to trip on.
- 🔴 **`gh pr merge --delete-branch` on a STACKED PR closes the child**, and GitHub **refuses to
  reopen** it. Branch survives; PR object does not. Merge the parent *without*
  `--delete-branch`, or retarget the child first. (Cost us #247 → reopened as #249.)
- **The recurring defect was prose, not logic.** ~10 fix rounds where the code was right and the
  comment overclaimed. What held: state the **invariant, not the location**, and record why
  earlier versions were wrong (`service_worker.js` `pollOnce` — "THIRD position this check has
  occupied … the check belongs immediately before the side effect it guards, with NO await
  between them").
- **`emulate` ordering:** owned-tab-only; **load → emulate → re-nav** is the only working order.
  You cannot emulate `about:blank` (`chrome.debugger` attaches to http/https only). Emulation
  applied to an already-loaded document does **not** install `ontouchstart`/`TouchEvent` —
  metrics/media/UA-CH apply live, create-time properties do not.
- **`text`/`html`/`js` are NOT emulated** — they take `chrome.scripting`, never CDP. The envelope
  now says so (`emulated:false` + `notEmulatedRead`). `js --wake` *is* emulated.
- **Extension id is path-derived and PREDICTABLE**: `sha256(abs path)` → first 32 hex → nibbles
  `0-f`→`a-p`. Measured, not inferred. Path only — **no profile component** (both profiles on
  both hosts gave one id). Repo path → `pkkoninbaeicfalpdkkmcknhnacjjjpi`; deployed →
  `bgbkamdlkdleahpgdgmjipjbgmepgenk`.
- **A byte ceiling needs a real margin.** `SKILL.md`'s 12,288 ceiling was re-breached three times
  in one day at 2–11 bytes free, and once by 974 when a feature commit added docs without
  evicting. **<100 bytes free is not a margin.**
- **Frontmatter descriptions are ~95% keywords already** — the whole trim recovered only
  ~53 tokens/session. Reported as a negative result rather than padded. **Trigger terms must be
  preserved verbatim; the only safe method is an empty term-set diff.**
- **DEAD END (verified):** dropping the server so the long-poll dies does NOT load new extension
  code. Chrome pins the loaded version until an explicit reload. Don't retry.
- ⚠ **Never kill Brave** — `restore_on_startup` is unset on all profiles; tabs do not return.
  ↻ now *is* falsifiable via `browser ping`, so a reload is a small ask, not a gamble.

## How to verify
```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB whoami                       # host + instances + extension_stale per instance
$BB --instance <label> ping      # {pong, extensionVersion:"0.7.0", id, ops}
$BB --instance <label> context   # was DEAD on main before #263

# session-id fix, the original ssh repro (was: drifted across a subshell)
ssh zach@192.168.50.250 'BB=~/workspace/devrc/scripts/browser-bridge/browser
  echo "in subst: $($BB --print-session-id)"; echo -n "direct:   "; $BB --print-session-id'
# both must be identical, shaped sid:<sid>:<ticks>

# emulation, the ONLY working order (owned tab, no --tab needed)
$BB --instance <label> open https://example.com     # wait ~3s for commit
$BB --instance <label> emulate iphone-15            # expect documentPredatesEmulation:true
$BB --instance <label> js 'innerWidth+"x"+innerHeight' --wake   # 393x852  (flag AFTER expr also fine)
$BB --instance <label> emulate --reset; $BB --instance <label> close

# suites + gate
node --test scripts/browser-bridge/tests/*.test.mjs        # 454
nix-shell -p python3Packages.pytest --run "python -m pytest scripts/browser-bridge/tests -q"   # 366
nix build .#checks.x86_64-linux.pytests                    # green
```
