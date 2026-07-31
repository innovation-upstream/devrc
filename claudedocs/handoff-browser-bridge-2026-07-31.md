# Handoff: browser-bridge — wake, nested-OOPIF, agent unblocked, skill restructure — 2026-07-31

## Goal
Take browser-bridge from "feature-complete but partly broken in reality" to verified, and
cut its per-task token cost. **10 PRs merged, every behavioural one live-verified against
real Brave.** `browser agent` executed successfully for the first time ever.

## State now
- Branch: `main`, both hosts converged (laptop `.155`, workbench `.250`), `home-manager
  switch` applied, `browser-bridge` service **active** on the current `server.py`.
- ⚠ `main` moves FAST — other sessions merged 6+ PRs *during* this session. **Always
  `git fetch` and check which branch you are on before any pull/checkout** (see Gotchas).
- Working tree carries long-standing operator-owned uncommitted files on both hosts
  (~17 laptop / ~33 workbench). **Not mine, left untouched.**

### DONE — merged + live-verified
| PR | What | How verified |
|---|---|---|
| #212 | Nested-OOPIF cascade (`eval --frame` / `upload --frame`) | grandchild → `"grandchild-reached"`; depth 6 → `oopif_depth_cap:5` |
| #215 | `screenshot`→`.png` @0600 +24h prune; `html --max-bytes`; `js` alias | `js '1+1'`→2 (telemetry logs `op=eval`); cap truncated 492,211 B; PNG mode `-rw-------` |
| #216 | Agent gate file-capture; `upload` out of agent op set; `whoami` narrowed | gate resolves browser-only |
| #222 | Cookie limits + in-page credentialed-fetch pattern | `credentials:"include"`→404 vs `"omit"`→401 (cookie attached, no value leaked) |
| #225 | **`wake`** — un-throttle a hidden tab, NO focus theft | `WAKE-RIG-SHELL`→`WAKE-RIG-RENDERED`; `xdotool getactivewindow` unchanged |
| #226 | Rescued false-outage post-mortem; remedy `activate`→`wake` | docs |
| #233 | **SKILL.md restructure** 56,031→11,322 B + 8 reference files | 75-string coverage check, independently spot-checked |
| #234 | **Readiness probe** — `browser agent` could not start at all | live: first-ever successful agent run |
| #235 | Rescued 3 stranded gotchas (incl. `data-testid` stripped in prod) | docs; core 11,870 B (ceiling 12,288) |
| (in flight) | `docs/session-lessons-browser-bridge` — RULES.md / CLAUDE.md / agent.md | — |

**`browser agent` end-to-end proof (2026-07-31):**
```
$ browser --instance personal agent --allow-domains example.com \
    "go to https://example.com and report the exact H1 heading text"
{"answer":"Example Domain","evidence":["browser(op=\"text\", selector=\"h1\") returned: Example Domain"],"steps_used":2,"status":"ok"}
```

## Open investigations — live diagnosis state

### 1. Is deepseek-flash good enough to be the DEFAULT for open-ended browsing?
- **UNMEASURED.** Only two trivial tasks have ever run. This gates the `browser agent`-first
  default flip, which is deliberately NOT shipped.
- **Ruled out as causes of the historical zero-adoption:** opencode version (both hosts
  1.18.4); the `*: allow` permission-map scare (false alarm — the gate checks the `tools`
  map, which resolves browser-only). The real causes were the two blockers, both now fixed.
- **Next probe:** ~10 real goals drawn from transcripts, ~$0.06, ~20 min. Watch for: blind
  selector synthesis; the throttled-background-tab trap producing confident wrong `ok`s (now
  mitigable — the agent can call `wake`); skipped `frames` discovery. Design + decision rule:
  `claudedocs/browser-bridge-token-and-agent-design-2026-07-30.md`.

### 2. Can a hidden tab read the clipboard during a `wake`? (SUSPECTED, low stakes)
- `navigator.clipboard.readText()` needs a focused document + granted `clipboard-read`;
  `wake` supplies the "focused" half via focus emulation.
- **Mitigated regardless:** #225 sends `setFocusEmulationEnabled({enabled:false})` in a
  `finally`, so the window is bounded to the wake. Confirmed live: tab reports `hidden`
  immediately after.
- **Next probe:** on a site with `clipboard-read` already granted, copy a sentinel, then
  `browser js 'navigator.clipboard.readText()' --wake` on a hidden tab.

## Next steps (ranked)
1. **Measure deepseek** (investigation 1) → then ship the agent-default flip as its own PR.
2. **Stable extension path + manifest version bump.** The session's biggest cost was
   infrastructure, not bugs: 3 Brave restarts, a silently-reverted staged build, a deleted
   fixture dir. Move the extension to `~/.local/share/browser-bridge-ext/` so no git
   operation can invalidate a verification; bump the manifest per change so `whoami`'s
   loaded-vs-repo actually answers "is the new build loaded?".
3. Optional: symlink `reference/` in `nix/home.nix` so the core can use relative paths
   (today it uses repo-absolute paths — works on both hosts, no Nix change needed).
4. Smaller: `nav --wait-for <selector>` (28 hand-rolled polling bigrams in real sessions);
   failure-path coverage for `instances`/`release`/`tabs` (currently zero); CLI
   error-translation layer (only 2 of 9 HTTP branches tested).

## Gotchas / decisions / dead-ends
- 🔴 **An audit/review fix RESETS the verification gate.** Twice this session an adversarial
  audit correctly identified a gap, and the fix for it broke the feature live. The
  own-tab `source.tabId` check silently ate every nested CDP event → a **completely inert**
  feature that still passed 428 green tests and a second clean audit.
- 🔴 **Four features passed tests AND clean audits while broken in reality**: inert cascade,
  screen-stealing wake predecessor, unrunnable agent, world-readable screenshots in `/tmp`.
- 🔴 **Chrome does NOT populate `source.tabId` on sub-session `Target.attachedToTarget`**
  (flat mode). Prove ownership by **session parentage**. Signature of this bug class:
  `frame_not_found` and *never* a depth cap — "inert, not capped".
- **Build a deterministic "is the new build loaded?" tell into every extension change**
  (a new op name → `unknown_op` on the old build). Without one, reload-vs-restart is
  unfalsifiable — that ambiguity cost 3 full Brave restarts.
- **DEAD END (verified):** dropping the server so the long-poll dies and letting the MV3
  worker idle-evict does **NOT** load new code. Chrome pins the loaded version until an
  explicit reload/restart. Don't retry.
- ⚠ **Never kill Brave to force a reload** — `restore_on_startup` is UNSET on both profiles,
  so the operator's tabs would NOT return. Ask for the ↻ click; ↻ *does* work (it worked
  twice this session) — it's just unfalsifiable without a tell.
- **The server runs `~/.config/browser-bridge/server.py` (nix-managed), NOT the repo file.**
  Editing the repo changes nothing until `home-manager switch`. To live-test an unmerged
  server change: stop the service, run the branch's `server.py` manually on 8788, restore
  after. The EXTENSION loads from the repo path — so another session's checkout silently
  reverts a staged build.
- **`vcap.me` now resolves to a REAL PUBLIC IP** (103.224.182.214) — do not use it as a
  loopback alias. Use `lvh.me`, `127.0.0.1.sslip.io`, `127.0.0.1.nip.io`.
  ⚠ **`lvh.me` is on Phantom Wallet's phishing blocklist** and gets tabs hijacked to
  `chrome-extension://…/phishing.html` — the rig uses `sslip.io`.
- **`pkill -f <pat>` matches your OWN shell** and killed the running command twice (exit
  144). Resolve PIDs first: `pgrep -f <pat>`, skip `$$`, verify `/proc/<pid>/cmdline`, `kill`.
- **`gh pr view --json mergeable` is the ONLY conflict authority** — `git merge-tree` with
  two args gave a confident false "no conflicts".
- **Counting a string that also appears in a fixture's own source proves nothing.** A grep
  for `POISON` "failed" the isolated-world check; the real discriminator was the returned
  document's SIZE and content.

## ⚠ Stranded work (NOT mine — needs owners' decisions)
- **`standup.sh` in a workbench stash** — `ssh 192.168.50.250`, `git -C ~/workspace/devrc
  stash list` → `stash@{0}`/`stash@{1}` hold `claude/skills/standup/standup.sh` at **288
  lines vs main's 272**. **Do not drop these stashes.**
- **`docs/auditloop-skill-push-grounding`** was accidentally rebased onto main by this
  session. Content verified byte-identical; only the base moved. Restore with
  `git reset --hard origin/docs/auditloop-skill-push-grounding` if preferred.
- Laptop has 9 stashes, mostly repeated `ship-auto` snapshots. Left in place.

## How to verify
```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB whoami                       # ORIENT FIRST — host + instances + loaded-vs-repo version
$BB wake                         # woke:true; visibilityState visible → hidden after
$BB agent --allow-domains example.com "go to https://example.com and report the H1"
# Full wake proof (rig on 8901):
python3 -m http.server 8901 --bind 127.0.0.1 \
  --directory ~/workspace/devrc/scripts/browser-bridge/tests/fixtures/oopif-rig &
export DISPLAY=:0 XAUTHORITY=/home/zach/.Xauthority
nix-shell -p xdotool --run 'xdotool getactivewindow getwindowname'   # BEFORE
T=$($BB open http://127.0.0.1:8901/wake-rig.html | grep -oE '"tabId": [0-9]+' | grep -oE '[0-9]+')
$BB --tab $T text   # WAKE-RIG-SHELL, hidden:true
$BB --tab $T wake   # woke:true
$BB --tab $T text   # WAKE-RIG-RENDERED
nix-shell -p xdotool --run 'xdotool getactivewindow getwindowname'   # AFTER — MUST MATCH
$BB --tab $T close
# Suites (prerequisite, NOT verification):
node --test scripts/browser-bridge/tests/*.test.mjs
nix-shell -p python3Packages.pytest --run "python -m pytest scripts/browser-bridge/tests -q"
```
