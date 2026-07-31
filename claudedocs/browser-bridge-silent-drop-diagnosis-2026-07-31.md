# browser-bridge: silent instance drop requiring a manual ↻ — diagnosis

**Date:** 2026-07-31 · **Host:** laptop (192.168.50.155) · **Method:** read-only (journal, deployed
server, loaded extension source, live `health` probes). No service restart, no repo modification,
no Brave interaction beyond read-only CLI ops.

**Artifacts read**
- Deployed server: `/nix/store/gfqxad8c71hfi53nzqjhhpdb9r6378q4-home-manager-files/.config/browser-bridge/server.py`
  (1738 lines, symlinked from `~/.config/browser-bridge/server.py`) — this is what PID 2231 runs.
- Loaded extension (v0.2.0): `/home/zach/workspace/devrc/scripts/browser-bridge/extension/service_worker.js`
  and `.../extension/protocol.js`.
- `journalctl --user -u browser-bridge`, 3-day window.

---

## Summary

| Question | Answer |
|---|---|
| Q1 root cause | **An unbounded `await` inside `execute()` wedges the poll loop permanently**, because `loop()` is guarded by a non-reentrant `running` flag that only a fresh service-worker evaluation can reset. Strongly supported, not yet mutation-proven — see *Confidence*. |
| Case (a) or (b)? | **Primarily (a), with a bounded (b) window of ≤ ~65 s.** The server *does* correctly forget the instance. |
| Q2 defect | `/health`'s `extension_connected` is `bool(insts)` — a bare OR across all instances, so one live profile masks a dead one forever. |

---

## 1. Root cause for Q1

### 1.1 The mechanism

`extension/service_worker.js:983-1019`:

```js
async function loop() {
  if (running) return;          // :984  ← non-reentrant guard
  running = true;               // :985
  try {
    while (true) {
      ...
      const r = await pollOnce(cfg);
      if (r.kind === POLL_COMMAND && r.cmd) {
        const envelope = await execute(r.cmd);   // ← unbounded await
        await postResult(cfg, envelope);
      }
    }
  } finally {
    running = false;            // :1017 ← unreachable while awaiting
  }
}
```

`running` is a module-global (`:52`) and is assigned in exactly three places (`:52`, `:985`, `:1017`).
The `finally` at `:1017` can only run if the `while (true)` loop exits — which never happens while the
loop is parked on an `await`. So:

1. A command arrives and `execute()` is awaited.
2. `execute()` never settles.
3. `pollOnce()` is never called again → the server stops seeing `/poll` for this instance.
4. The MV3 keepalive (`chrome.alarms` `bridge-keepalive`, `periodInMinutes: 1`, `:1033-1035`)
   fires on schedule and calls `loop()` — which hits `if (running) return` at `:984` and **does nothing**.
5. The instance is dead until the worker is re-evaluated. `↻` in `brave://extensions` does exactly
   that, which is why it is the only thing that fixes it.

**The keepalive alarm is structurally incapable of recovering from this failure.** It only recovers
from worker *termination*, not from a wedged-but-alive worker. This is the crux, and it is why the
alarm's existence does not rebut the diagnosis.

### 1.2 What can hang unboundedly

There is **no `AbortController`, no `AbortSignal`, and no `Promise.race`** anywhere in
`service_worker.js` (grepped: zero hits). `execute()` (`:910-919`) wraps the op in `try/catch` but
applies **no wall-clock bound**:

```js
const data = await OPS[cmd.op](cmd);   // :914 — no timeout
```

CDP-routed ops *are* bounded — `withCdpSession` (`protocol.js:670`) enforces
`CDP_ATTACH_TIMEOUT_MS = 8000`, `CDP_COMMAND_TIMEOUT_MS = 8000`, `CDP_OP_BUDGET_MS = 15000`
(`protocol.js:623-625`) via `promiseWithTimeout`. **That is the important nuance: the CDP path is
not the culprit — it is the one path with a budget.**

The unbounded awaits are the **non-CDP `chrome.*` extension APIs**:

| Op | Path | Bounded? |
|---|---|---|
| `frames` (`:674-680`) | `chrome.webNavigation.getAllFrames` + `tabVisibilityState` — comment explicitly says *"No debugger"* | **No** |
| `screenshot` fast path (`:644-655`) | `chrome.tabs.captureVisibleTab` via `captureWithRetry` | **No** |
| `targetTab(cmd)` (every op) | `chrome.tabs.get` / `chrome.tabs.query` | **No** |
| `pollOnce` (`:935-947`) | bare `fetch()`, no `signal` | **No** |

### 1.3 The evidence that selects this hypothesis

**Both recorded drops in the 3-day journal are immediately preceded by a `cmd_timeout` on exactly
these unbounded ops, and by nothing else.**

Drop 1 — `work`, 2026-07-29 (the operator's report):
```
18:02:34  dispatch  frames      key=work      ← last command the worker ever picked up
18:02:54  cmd_timeout op=frames               ← 20 s server-side timeout
18:03:14  cmd_timeout op=screenshot
18:03:14  cmd_unknown_instance op=tabs   target=work   ← instance now stale/forgotten
18:03:45  cmd_unknown_instance × 5 (eval, open, frames, screenshot, close)
```
Note `18:02:34 dispatch frames` has **no matching `cmd_ok`** — every other dispatch in the window
does. The worker took the command and never came back.

Drop 2 — `personal`, 2026-07-30 (same signature, *different profile*):
```
15:59:56  cmd_timeout op=screenshot
16:00:22  cmd_timeout op=screenshot
16:00:31  cmd_unknown_instance op=screenshot  target=personal
```

This second drop is the single most useful piece of evidence, because it **falsifies the
profile-specific and heavy-SPA-specific framings**. The fault reproduced on `personal` too. It is
op-correlated, not profile-correlated. `work` is over-represented only because that profile is the
one that gets driven at `frames`/`screenshot`.

Counter-check (the ops that timed out but did *not* kill the instance): `10:43 eval` on 07-29 and
`18:48 open` on 07-30 both produced a `cmd_timeout` with **no** subsequent `cmd_unknown_instance`.
`eval` routes through `withCdp` → bounded → the op fails, the loop iterates, the instance survives.
That is the predicted asymmetry and it holds.

### 1.4 Hypotheses eliminated

| Hypothesis | Verdict | Discriminating evidence |
|---|---|---|
| MV3 evicted the worker; nothing revived it | **Eliminated** | `chrome.alarms` survives worker termination and wakes it. A terminated worker re-evaluates with `running = false` → `loop()` runs → auto-recovery within ≤60 s. Observed behaviour requires a *manual* ↻, which only makes sense if the worker is **alive and wedged**. |
| Laptop suspend/resume killed the long-poll | **Eliminated** | Zero suspend/resume events in the 3-day journal (`PM: suspend`, `systemd-sleep`, `Suspending system` — no matches). The machine did not sleep. |
| Wifi transition dropped the socket | **Eliminated as the cause** | Only one wlan event in the drop window (`17:48:34` GTK rekey, 14 min before the drop). A transport error is also *survivable*: it lands in the `catch` at `:1011` and retries. |
| Backoff reaches a state it never leaves | **Eliminated** | `nextBackoffMs` (`protocol.js:142-145`) caps at `capMs = 30000`. Worst case is a 30 s retry cadence, which recovers. |
| Re-entrant `loop()` spawns duplicate fighting pollers | **Eliminated** | The `running` guard at `:984` makes `loop()` strictly non-reentrant. The guard is not the *cause* of duplication — it is the cause of the *wedge*. |
| Server evicts a live instance (instance-level TTL) | **Eliminated as a false-positive source** | `CONNECT_STALE_S = 40.0` (`server.py:164`) is evaluated in `_live_instances_locked` (`:1081-1088`) as `active_polls > 0 or (now - last_poll) < 40`. A *polling* instance can never be reaped: `active_polls` is incremented for the whole poll and `last_poll` is refreshed in a `finally` (`server.py:841-856`). The server drops the instance only because the extension genuinely stopped polling. `_owner_ttl` (900 s) governs **tab ownership only**, never instance liveness. |
| Heavy civitai SPA / long CDP op exceeding the ~5-min worker limit | **Weakened, not required** | The CDP path is the one with a 15 s budget. Drop 2 (`personal`, on `auditloop`) had no heavy SPA involved. |

### 1.5 The stable `instanceId` is a red herring

`instanceId()` (`service_worker.js:58-64`) reads `chrome.storage.local`, generating a UUID only if
absent. It is **persistent across worker restarts, ↻, and browser restarts**. So `work`'s id being
unchanged since session start tells us nothing about worker liveness and is *not* evidence that the
worker survived. Worth stating explicitly so it is not re-derived as a clue.

### 1.6 Confidence

**High on the mechanism, not yet proven on the trigger.**

- *Proven by code reading:* `execute()` is unbounded; `frames`/`screenshot`/`targetTab`/`pollOnce`
  have no timeout; `running` is only reset by a fresh worker evaluation; the keepalive alarm cannot
  clear it. These are direct reads of the loaded v0.2.0 source, not inference.
- *Proven by log:* the poll loop stopped iterating immediately after taking a `frames` command that
  never returned a result; identical signature on a second profile.
- **Not proven:** *which* `chrome.*` call actually hung, and *why* it hung. I did not observe a
  hanging `getAllFrames`/`captureVisibleTab` live, and could not without an extension-side log.
  A wedged `pollOnce` `fetch()` is an equally-fitting sub-hypothesis (same bug class, same fix) and
  the journal cannot distinguish it — a wedged fetch and a wedged `execute` look identical from the
  server, which sees only "polls stopped".

That residual ambiguity does **not** change the fix: every candidate is *an unbounded await under a
non-reentrant `running` guard*, and all of them are closed by the same two changes (§4).

---

## 2. Reproduction / detection

### 2.1 Reproduction

I could not reproduce on demand read-only — triggering it requires driving `frames`/`screenshot`
against the operator's live tabs and would have knocked the bridge out. Deliberately not attempted.

A safe reproduction that does not depend on winning the hang race, for use once a fix branch exists:
add a temporary op that `await new Promise(() => {})`, dispatch it, then confirm (i) the instance
goes stale in ~40 s, (ii) the keepalive alarm fires and does not recover it, (iii) only ↻ restores it.
That directly mutation-tests the `running`-guard claim rather than assuming it.

### 2.2 Detection — the cheap always-on detector (recommended regardless)

The reason this case is still partly open is that **the extension side is completely unobservable**.
The server logs `dispatch`/`cmd_ok` but has no `poll_start` / `poll_gap` / `instance_lost` event, so
a drop leaves no trace at all unless someone happens to send a command. That is exactly why the
operator's second drop appears nowhere in the journal.

Two low-cost additions that would close the case on the next occurrence:

1. **Server-side `instance_lost` / `instance_connected` events** — in
   `_live_instances_locked` (or a small reaper), log once when a known instance transitions
   live→stale, with `key`, `instance_id`, `last_poll` age, and **the id/op of the last command
   dispatched to it that never produced a result**. That last field alone would have named `frames`
   as the wedging op without any of the above inference.
2. **Extension-side heartbeat breadcrumb** — before and after `await execute(cmd)`, write
   `{op, id, phase, ts}` to `chrome.storage.local` (a single rolling slot, no growth). After the
   next drop, the options page (or `browser whoami`) can report *"wedged in `frames` since
   18:02:34"*. This converts the diagnosis from inference to observation.

---

## 3. The Q2 contract defect (precise)

**Defect:** `/health`'s `extension_connected` is a bare disjunction over all instances.

`server.py:1504`:
```python
self._send(200, {"ok": True,
                 "extension_connected": bool(insts),   # ← true if ANY instance is live
                 "count": len(insts),
                 "extension_version_current": manifest_version(),
                 "instances": insts})
```
Backed by `Registry.connected` (`server.py:1156-1158`), same `bool(...)` shape.

**Measured behaviour when an instance dies** (from the 07-29/07-30 logs plus live probes today):

| Surface | Behaviour | Honest? |
|---|---|---|
| `instances[]` | The dead instance **disappears** after ≤ `CONNECT_STALE_S` (40 s) | ✅ |
| `count` | Drops 2 → 1 | ✅ |
| `extension_connected` | **Stays `true`** as long as *any* profile lives | ❌ **the defect** |
| `browser --instance work <op>` | Fails **fast and clearly** — measured **0.15 s**: `no connected instance matches --instance 'work'. Connected: …` | ✅ |
| An op during the ≤65 s transition window | Queued, hangs the full **20 s** `cmd_timeout` (observed twice, 07-29 18:02:54 / 18:03:14) | ⚠ bounded |

**What a caller can and cannot tell today:** a caller that reads `instances[]`/`count`, or that
names `--instance work`, gets the truth immediately and cheaply. A caller that reads only the
`extension_connected` boolean — which is what "is the bridge up?" naturally reaches for — is
**lied to indefinitely**. The field's name promises a global property it does not have; it means
"at least one instance is connected".

**Case (a) vs (b), settled:** this is **case (a)** — the extension really disconnected and the
server correctly forgot it. There is a genuine but **bounded** case-(b) window of at most
~65 s (≤25 s for an in-flight `/poll` thread to reach its deadline + 40 s `CONNECT_STALE_S`) during
which the instance is still listed and ops queue and hang for 20 s. The operator's *"reads as a
working bridge until an op fails"* is explained by the `extension_connected` aggregate, **not** by
a phantom instance entry. That is the better outcome of the two: the fix is an honesty fix on one
field, not a lifecycle rewrite.

---

## 4. Proposed fixes, ranked

> 🔴 **Sequencing constraint — PR #242 owns nearly every file below.** #242
> (*"git-immune extension deploy path + ping probe, manifest 0.3.0"*, currently `MERGEABLE`/`CLEAN`)
> touches `server.py` (+156/−42), `browser` (+31/−3), `service_worker.js` (+36/−1),
> `protocol.js` (+19/−2), `manifest.json`, and all four test files. **Every fix below will conflict
> with it. Sequence all of this after #242 merges.** PR #243 touches only `opencode/` +
> `browser-agent` and does not overlap.

### Fix 1 — durability: bound every op (closes the root cause)
**Files:** `extension/service_worker.js`, `extension/protocol.js`, `tests/service_worker.test.mjs`

Wrap `await OPS[cmd.op](cmd)` in `execute()` (`:914`) in `promiseWithTimeout` — the helper already
exists in `protocol.js:639-644`. Give it a wall-clock ceiling above the CDP budget
(`CDP_OP_BUDGET_MS = 15000`) but below the server's 20 s `cmd_timeout` — ~18 s — so the extension
returns a *clean* `op_timeout` error envelope rather than letting the server time out blind.
Also give `pollOnce`'s `fetch` an `AbortSignal.timeout` of ~`poll_timeout + 5 s`.

This is the fix that stops the drop. It is one choke point (`execute`), consistent with *one rule,
one place* — do **not** patch `frames` and `screenshot` individually, that regenerates the bug at
every future op.

### Fix 2 — durability: make the keepalive able to recover (defence in depth)
**Files:** `extension/service_worker.js`

`running` must be a *liveness* claim, not a latch. Record `lastLoopTickAt` on each iteration; in the
alarm handler, if `running && now - lastLoopTickAt > ~90 s`, treat the loop as dead and restart it.
Worth doing even with Fix 1, because Fix 1 only bounds the awaits we currently know about — Fix 2
recovers from the next unbounded await someone adds.

### Fix 3 — honesty: `extension_connected` must not mask a dead named instance
**Files:** `scripts/browser-bridge/server.py`, `scripts/browser-bridge/browser`, `tests/test_server.py`

The field cannot be silently redefined (callers depend on "is anything up"). Preferred shape:
keep `extension_connected` as-is, add an explicit **`known_instances`** / **`missing`** pair —
the server remembers keys it has seen this process-lifetime and reports which are no longer live —
and have `browser health` render a visible `work: DISCONNECTED (last seen 18:02:34)` line. That
turns the operator's silent failure into a glanceable one.

Note #242 already adds `extension_stale`; this should be designed to sit alongside it, not collide.

### Fix 4 — observability: the detector from §2.2
**Files:** `scripts/browser-bridge/server.py` (`instance_lost` log event), `extension/service_worker.js`
(storage breadcrumb)

Lowest risk of the four and the only one that would let the *next* occurrence be diagnosed from
evidence rather than inference. If only one thing ships before #242 merges, this is the one worth
carving out — though it too touches `server.py`.

**Recommended order:** #242 merges → Fix 1 + Fix 4 together (fix + the evidence to confirm it) →
Fix 3 → Fix 2.

---

## 5. What I could not determine

- **Which specific `chrome.*` call hung, and why.** The candidates (`webNavigation.getAllFrames`,
  `tabs.captureVisibleTab`, `tabs.get`, `pollOnce`'s `fetch`) are indistinguishable from the server's
  vantage point, which observes only "polls stopped". Closing this needs the §2.2 breadcrumb.
- **Whether the wedge is in `execute()` or in `pollOnce()`.** Same bug class, same fix, but I cannot
  prove which. I have deliberately not asserted `frames` *caused* the hang — only that the worker
  took a `frames` command and never returned, twice with `screenshot` alongside.
- **The operator's second drop of this session has no journal trace.** Only one `work` drop
  (2026-07-29 18:03) and one `personal` drop (2026-07-30 16:00) appear in the 3-day window. The
  second `work` drop produced no evidence because no command was sent while it was down. I did not
  correlate it to a timestamp and am not going to guess one.
- **Whether Chrome honours `periodInMinutes: 1`** for this extension (Chrome has historically floored
  alarm periods for unpacked/MV3 extensions). Not determinable read-only, and **not load-bearing**:
  the alarm demonstrably cannot clear the `running` latch at any period.
- **Not tested live:** I did not drive `frames`/`screenshot` against either profile to attempt a
  reproduction, since a successful reproduction would have taken the operator's bridge down.
- **Not verified:** none of the proposed fixes were written or run. This document is diagnosis only.
