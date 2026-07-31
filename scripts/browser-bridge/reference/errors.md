# Error catalogue + the stale-extension playbook

**Load this when:** any op returned an error string you don't recognise — look it up
below · an op the CLI knows answers `unknown_op` · you clicked reload ↻ in
`brave://extensions` and the old behaviour persists · `health` still shows the old
`extension_version` after a reload.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`. This file is the catch-all: no error string
should strand you.

## Error shapes (from `/cmd`)

- `503 extension_not_connected` → extension not loaded/paired, or Brave closed.
- `504 timeout` → extension picked it up but didn't answer (tab unresponsive).
- `429 rate_limited` / `429 queue_full` → the per-instance concurrency backstop is
  shedding load — you're dispatching too fast / too many at once. **Back off and
  retry** (the body carries a `retry_after` seconds hint). A normal handful of ops
  is never throttled; this only fires on a sustained flood. Knobs (env, on the
  server): `BROWSER_BRIDGE_RATE_PER_SEC` (default 5, sustained/sec; 0 = unlimited),
  `BROWSER_BRIDGE_BURST` (default 20; clamped to ≥1 when rate>0 — a <1 burst
  would rate_limit every /cmd forever), `BROWSER_BRIDGE_MAX_QUEUE` (default 32,
  0 = unlimited).
- `409 ambiguous_instance` → >1 instance connected and no `--instance` — pick one.
- `409 no_owned_tab` → `close` with nothing to close — run `browser open` first (or `--tab`).
- `409 superseded` → the instance was replaced by a newer connection; retry.
- `404 unknown_instance` → the `--instance` key matches no connected instance.
- `400 unknown_op` / `missing_field:url|js` → bad command.
- `op '<op>' failed in the browser: unknown_op` (op-level — the CLI knows the op
  but the **extension** doesn't) → the loaded extension is an older build. See the
  stale-extension playbook below; use `tabs` + `--tab <id>` meanwhile.
- `Failed to capture tab: image readback failed` (a `captureVisibleTab` readback
  race on the fast path) → the SW now falls through to the **CDP `Page.captureScreenshot`**
  primary path, which captures a background/occluded tab directly, so this should no
  longer surface as an op error on current builds. If it does, reload the extension.
- `400 bad_tab` → a non-numeric `tab` (only reachable via a raw API POST; the CLI
  already validates `--tab`).
- `owned_tab_gone` (op-level, in `.result`) → your owned tab was closed; ownership
  is auto-dropped, so just re-issue (it falls back to the active tab / re-`open`).
- `cdp_attach_refused:<scheme>` (op-level, in `.result`) → a CDP op (screenshot /
  top-frame input / `eval --frame`) was aimed at a privileged tab (`chrome://`,
  `file:`, extension, devtools, …); the bridge refuses to attach `chrome.debugger`
  there. Point the op at a real `http/https` tab.
- `401 unauthorized` → token mismatch (re-paste in the extension options).
- `wake_with_frame_unsupported` → `--wake` was combined with `--frame`. Run
  `browser wake`, then the frame read (`~/workspace/devrc/scripts/browser-bridge/reference/spa-wake.md`).
- `frame_not_found:<url> cascade[…]` / `frame_eval_failed:<reason>` /
  `ambiguous_frame:<n>` / `oopif_depth_cap:5` / `oopif_target_cap:50` →
  `~/workspace/devrc/scripts/browser-bridge/reference/frames-cdp.md`.
- `op_not_allowed:<op>` / `nav_scheme_denied:<scheme>` → the autonomous
  browser-agent's op or scheme gate, `~/workspace/devrc/scripts/browser-bridge/reference/agent.md`.
- `Cannot access a chrome:// URL` (with a `null` result) → `eval`/`js` can't run on
  `chrome://` / `brave://` pages. Not a bridge fault.

## The LOADED extension may be older than the CLI

The CLI is always current; the **extension build running in Brave is not**. Brave
does not hot-reload unpacked extensions, so the live service worker can be far
behind. Symptom, seen for real:

```
$ browser --instance work open https://example.com
op 'open' failed in the browser: unknown_op
```

**`open` answering `unknown_op` means the loaded extension predates owned-tab
support** — per-session tab isolation is unavailable until the user reloads it.
Don't debug the server. Meanwhile work without `open`: run `browser tabs`, pick an
existing tab, and pass `--tab <id>` on every op (or just read the active tab for a
one-shot). Same reasoning for any op that answers `unknown_op` (e.g. `upload` on a
pre-0.2.0 extension).

The CLI **detects this for you**: any op the CLI dispatches that returns a
server-side `unknown_op` is mapped to a clear message + non-zero exit telling you
the loaded extension is OLDER than the CLI and to reload/restart. **`browser
health` also shows the build** — each instance's loaded `extension_version` vs the
bridge's `extension_version_current` (repo manifest); a mismatch = stale.

**⚠ Reload ↻ is UNRELIABLE — a full Brave restart is the reliable fix.** The
extension's long-poll keeps the OLD service worker permanently alive, so clicking
↻ in `brave://extensions` often does NOT swap in the new build. If a reload
doesn't take (the op still returns `unknown_op`, or `health` still shows the old
`extension_version`), tell the user to **fully quit and reopen Brave**.

**Nuance (measured 2026-07-30): a ↻ reload DID take** — swapping in a new build after an
earlier full restart in the same Brave session. So ↻ is worth trying **first**, but only
when you have a **deterministic tell** for whether it took. Don't reason about it; test
it. Pick something the new build emits that the old one cannot, e.g. the nested-OOPIF
`cascade[…]` trace: old build → a bare `frame_not_found:<url>`, new build → the same
error **plus** the trace. That turns "did the reload take?" from an unfalsifiable guess
into one command. With no such tell, skip ↻ and do the full restart.

Symptom of a stale build: an op the CLI knows returns `unknown_op`, or `health` still
shows the old `extension_version`. The `browser-bridge` **server** (not the extension)
DOES restart automatically on a `home-manager switch` (X-Restart-Triggers) — only the
extension needs the manual step.
