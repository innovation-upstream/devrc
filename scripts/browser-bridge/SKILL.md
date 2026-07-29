---
name: browser
description: Drive the user's LIVE, logged-in Brave browser from Claude Code — read the active tab's HTML, run JS in it, list/navigate tabs, and screenshot the visible tab — via the local token-authenticated browser-bridge (loopback rendezvous server + MV3 extension). Use when the user asks you to look at / read / scrape / interact with a page THEY have open, act on an authenticated site they're logged into, check what's on their screen in Brave, navigate their browser, or grab a screenshot of their current tab. NOT for headless fetching of public URLs (use WebFetch) — this is specifically their real, authenticated session.
---

```
~/workspace/devrc/scripts/browser-bridge/browser <subcommand>
```

**Run it by that exact path** (also `~/.claude/skills/browser/browser` if the
skill dir is symlinked). Don't hunt for it under `~/.claude/skills/...`.

## Quick start / binary path

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser   # ← the executable
$BB health                          # is an extension connected?
$BB --instance <key> open <url>     # open a NEW tab this session owns
$BB --instance <key> --tab <id> html   # act on a specific tab
```

## Concurrency / don't do this

- **Concurrent drivers (esp. sibling subagents) → each `open` and thread its own
  `--tab <id>`.** Sibling subagents of one parent SHARE a session id (they
  inherit `CLAUDE_CODE_SESSION_ID` + `$TMUX_PANE`), so without explicit `--tab`
  they fight over the SAME active tab. Have each driver `browser open` (capture
  the returned `tabId`) and pass `--tab <id>` on every subsequent op.
- **Do NOT run a bare high-rate `eval` loop** (no `--instance`/`open`). It shares
  the one active tab AND saturates the single serial extension connection; the
  server now **rate-limits** it and returns **HTTP 429** (`rate_limited` /
  `queue_full`) — the `browser` CLI prints a back-off message and exits non-zero.
  Batch what you need into fewer `eval`s, or space them out.

# browser — drive the live Brave session

`browser-bridge` lets you operate the user's **real, logged-in Brave** browser.
Commands go: the `browser` CLI → a loopback rendezvous server (`127.0.0.1:8788`,
bearer-token auth) → a standalone MV3 extension in the live Brave session →
executed against the **active tab** → result back to you.

This is authorized personal automation on the user's own workbench. It is a
**sibling** to the activity-collector browser extension (telemetry) — different
subsystem, do not conflate.

Full architecture, security model, and deploy: `scripts/browser-bridge/README.md`.

## Entrypoint

`scripts/browser-bridge/browser <subcommand>` (JSON on stdout, pretty-printed if
`jq` is present):

Prefix any op with `--instance <key>` to target a specific connected profile
(`browser --instance work html`) and/or `--tab <id>` to target an explicit tab.

| command | does |
|---------|------|
| `browser health`            | connected instances + count: `{"ok":true,"extension_connected":bool,"count":N,"instances":[…]}` |
| `browser instances`         | list connected instances as JSON (routing key, label, instanceId, active-tab url/title) |
| `browser [--instance K] open [url]`        | open a NEW tab THIS session owns (default `about:blank`, created in the background); records ownership; returns its `tabId`. Use for multi-step work. |
| `browser [--instance K] close`             | close this session's owned tab and drop ownership |
| `browser [--instance K] release`           | drop ownership WITHOUT closing the tab |
| `browser [--instance K] [--tab T] html`              | `outerHTML` of the owned tab (else the active tab) |
| `browser [--instance K] [--tab T] text [selector] [--max-bytes N]` | **cheap read** — visible `innerText` of the owned/active tab (optional CSS `selector`), whitespace-normalized + byte-capped (default 32768; `0`=uncapped; a truncation note is appended). ~98% smaller than `html` — prefer it |
| `browser [--instance K] [--tab T] eval '<js>'`       | run JS in the owned/active tab, return its value |
| `browser [--instance K] tabs`              | list open tabs (`.data.ownedTabId` flags this session's owned tab) |
| `browser [--instance K] [--tab T] nav <url>`         | navigate the owned/active tab to `<url>` |
| `browser [--instance K] [--tab T] screenshot [path]` | captureVisibleTab; prints the data URL, or writes a `.png` to `path` |
| `browser [--instance K] agent "<goal>" [flags]` | run the **autonomous opencode browser-agent** in its OWN isolated tab against `<goal>`; returns a compact `{answer,evidence,steps_used,status}` (see below) |
| `browser --print-session-id`               | print the derived per-session id (debug) and exit |

Result payloads land under `.result.data` in the JSON (the envelope is
`{"ok":true,"result":{"id","ok","data":{...}}}`).

## `browser agent "<goal>"` — autonomous read/navigate in an isolated tab

Offload an open-ended "go read X and tell me Y" browsing task to a **cheap
autonomous agent** (opencode + DeepSeek `deepseek-v4-flash` via OpenRouter) so it
never burns YOUR context on transient page HTML — only a compact structured
result comes back.

```bash
browser agent "go to news.ycombinator.com and report the top 3 story titles" \
  [--instance K] [--allow-domains a.com,b.com] [--deny-domains x.com] \
  [--steps N] [--timeout S] [--dry-run]
```

- **Output (stdout):** one compact JSON object — never raw HTML:
  `{"answer":"…","evidence":["…"],"steps_used":N,"status":"ok|partial|blocked"}`.
  Exit 0 for `ok`/`partial`, non-zero for `blocked`/errors.
- **Own isolated tab (structural safety).** The wrapper `open`s a NEW background
  tab, and the agent is **permission-locked + guard-shimmed** so it can ONLY run
  `browser --tab <that-tab> …` — it can never touch your active tab. The tab is
  closed on EVERY exit path (success, timeout, error).
- **Guardrails:** a step budget (`--steps`, default 12), a wall-clock `--timeout`
  (default 120s) with a hard kill, `--deny-domains`/`--allow-domains` enforced by
  the guard shim (a denied `nav` is refused), and `--dry-run` (intercepts
  navigating/form-submitting ops — logs, doesn't execute). The full opencode JSON
  transcript + guard audit are kept in a scratch dir.
- **⚠ Privacy:** the pages the agent reads are sent to **OpenRouter/DeepSeek**.
  Do NOT point it at high-secret authenticated pages casually.
- **Prereqs:** `opencode` on PATH with the OpenRouter key already in its auth
  store (`~/.local/share/opencode/auth.json`), the extension connected, and the
  agent def symlinked (see README → Deploy). If any is missing you get a clean
  error and no orphaned tab.

## Per-session tab isolation (use `open` for multi-step work)

Two Claude sessions driving one browser used to interleave on the ONE shared
active tab (session A `nav`s, session B `nav`s in between, A reads B's page).
Now each session can own its own tab:

- **Multi-step workflow → `browser open <url>` first.** The server records this
  session's owned tab (keyed by a stable per-session id sent automatically on
  every request) and routes your subsequent `html`/`eval`/`nav`/`screenshot`/
  `close` to it — a parallel session doing the same on its own tab can't clobber
  you. Finish with `browser close` (closes the tab) or `browser release` (keeps
  the tab, drops ownership).
- **One-shot read → just `browser html` (no `open`).** With no owned tab, ops
  fall back to the active tab — the historical "read the tab the user has open"
  behaviour, which is a single read and inherently safe.
- **`--tab <id>`** targets a specific tab explicitly (overrides owned/active).
- **Double `open` is safe (idempotent).** Calling `browser open` again in a
  session that already owns a **live** tab returns that SAME tabId (it does not
  leak a second tab). If the owned tab was closed, `open` makes a fresh one.
- **Self-heal.** If the user manually closes your owned tab, the next op fails
  with `owned_tab_gone` and the bridge drops your ownership — your NEXT command
  automatically falls back to the active tab. `browser close` always clears the
  mapping, even if the tab was already gone.
- **Session id source:** `CLAUDE_CODE_SESSION_ID` → `$TMUX_PANE` → a
  per-process-tree token (see README). It is routing-only, never trusted for
  auth. If two drivers ever resolve the same id they share a tab (degrades to
  the old behaviour — no worse).
- **⚠ Concurrent drivers that may share a session id (subagents): pass explicit
  `--tab`.** Sibling subagents of ONE parent share identity — a subagent inherits
  the parent's `CLAUDE_CODE_SESSION_ID` and the same `$TMUX_PANE`, and there is NO
  subagent-unique env var — so two parallel subagents derive the SAME session id
  and would own the SAME tab (re-introducing the clobber). Per-session isolation
  only separates concurrent **top-level** sessions. When you spawn concurrent
  drivers that each drive the browser, have EACH one `browser open` (capture the
  returned `tabId`) and then pass its OWN `--tab <id>` on **every** subsequent op
  (`browser --tab <id> nav …`, `--tab <id> html`, …). An explicit `--tab`
  overrides owned-tab routing entirely, so indistinguishable drivers never
  collide. (Each `close`s its own `--tab <id>` at the end.)
- **Contention → FIFO, not failure.** If two sessions DO target the same tab
  (both active, or one `--tab`s another's tab), the commands queue in arrival
  order (bounded by `cmd_timeout`) rather than fail.
- **Lifecycle:** idle ownership is reclaimed after a TTL, which RELEASES it but
  does NOT close the real Brave tab (only `browser close` closes it).

## Multiple instances (per host)

Several Brave profiles can each run the extension and be driven independently —
each has its own command queue (routing key = the profile's **label** if set,
else a stable auto-id; labels must be unique per host).

- **One instance connected** → no `--instance` needed (back-compat).
- **More than one and no `--instance`** → the command **ERRORS** and lists the
  connected instances. Do NOT retry blindly — run `browser instances`, pick the
  right key, and re-issue with `--instance <key>`. (The bridge never guesses.)
- **`browser --instance <key> <op>`** targets one (key = label or auto-id); an
  unknown key errors.
- **Newest supersedes:** a fresh connection for an already-held key drops the old
  one; an in-flight command on the dropped connection returns a `superseded`
  error — just retry. The displaced connection's own `/poll` gets a distinct
  `409 superseded` (not the idle `204`) and the extension **backs off ~30s** (and
  shows a "superseded — set a unique label" state) instead of re-registering
  instantly, so two profiles sharing a label can't mutual-supersede in a tight
  loop. If you see `superseded` steadily, two profiles share a label → fix it.

## Security contract (why it's safe)

- **Loopback only** (`127.0.0.1:8788`) — never bound to an external interface.
- **Bearer token** on every request — auto-created `0600` at
  `~/.config/browser-bridge/token` on first server start. The `browser` CLI
  reads it; a web page can't. Defeats DNS-rebinding.
- **Host-header allowlist** — only `127.0.0.1`/`localhost`/`::1`.

## Telemetry (metadata-only)

Every handled command emits one **best-effort** activity event
(`source=browser-bridge`, `kind=cmd`) into the personal telemetry pipeline
(`activity.events`), so browser-skill usage is queryable / visible to
`adoption-scan`. It records **only** metadata — op, instance key, outcome,
latency, and the active tab's **bare domain** — **never** the eval source, page
HTML, screenshot bytes, full URLs, or any page content. It runs off the critical
path and can never delay or break a command. Nothing you need to do; noted so you
know browser usage is being counted. Details: `README.md` → Telemetry.

## Before you rely on it

1. `browser health` — if `extension_connected:false` or it errors, the extension
   isn't loaded/paired. Tell the user to load + pair it (see below); you cannot
   do this for them (it's a manual Brave step).
2. **Verify it's the LIVE authenticated session**: after `browser html`, confirm
   the returned markup contains **logged-in-only** content (their name, account
   menu, inbox contents). If it looks like a logged-out/anonymous page, the wrong
   tab is active or they're not logged in — say so rather than proceeding.

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
- `400 bad_tab` → a non-numeric `tab` (only reachable via a raw API POST; the CLI
  already validates `--tab`).
- `owned_tab_gone` (op-level, in `.result`) → your owned tab was closed; ownership
  is auto-dropped, so just re-issue (it falls back to the active tab / re-`open`).
- `401 unauthorized` → token mismatch (re-paste in the extension options).

## Gotcha: reload the unpacked extension after any change

Brave does **not** hot-reload unpacked extensions. If `extension/` was edited,
the user must click **reload** ↻ on the card in `brave://extensions`, or the old
service-worker code keeps running. The `browser-bridge` **server** does restart
automatically on a `home-manager switch` (X-Restart-Triggers).

## One-time setup (hand these steps to the user)

1. `home-manager switch --flake ~/workspace/devrc --impure` (starts the service).
2. Brave → `brave://extensions` → Developer mode → **Load unpacked** →
   `scripts/browser-bridge/extension/`.
3. Extension **Options** → paste the token from `~/.config/browser-bridge/token`,
   port `8788`, optionally set a unique **label** (required only if a second
   profile also connects), **Save**.
4. `browser health` → `extension_connected:true`.

For a second profile, repeat in that profile and give it a **different** label,
then target with `browser --instance <label> <op>`.
