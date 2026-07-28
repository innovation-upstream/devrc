---
name: browser
description: Drive the user's LIVE, logged-in Brave browser from Claude Code — read the active tab's HTML, run JS in it, list/navigate tabs, and screenshot the visible tab — via the local token-authenticated browser-bridge (loopback rendezvous server + MV3 extension). Use when the user asks you to look at / read / scrape / interact with a page THEY have open, act on an authenticated site they're logged into, check what's on their screen in Brave, navigate their browser, or grab a screenshot of their current tab. NOT for headless fetching of public URLs (use WebFetch) — this is specifically their real, authenticated session.
---

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

Prefix any op with `--instance <key>` to target a specific connected profile:
`browser --instance work html`.

| command | does |
|---------|------|
| `browser health`            | connected instances + count: `{"ok":true,"extension_connected":bool,"count":N,"instances":[…]}` |
| `browser instances`         | list connected instances as JSON (routing key, label, instanceId, active-tab url/title) |
| `browser [--instance K] html`              | active tab `outerHTML` (+ url, title) |
| `browser [--instance K] eval '<js>'`       | run JS in the active tab, return its value |
| `browser [--instance K] tabs`              | list open tabs |
| `browser [--instance K] nav <url>`         | navigate the active tab to `<url>` |
| `browser [--instance K] screenshot [path]` | captureVisibleTab; prints the data URL, or writes a `.png` to `path` |

Result payloads land under `.result.data` in the JSON (the envelope is
`{"ok":true,"result":{"id","ok","data":{...}}}`).

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
- `409 ambiguous_instance` → >1 instance connected and no `--instance` — pick one.
- `409 superseded` → the instance was replaced by a newer connection; retry.
- `404 unknown_instance` → the `--instance` key matches no connected instance.
- `400 unknown_op` / `missing_field:url|js` → bad command.
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
