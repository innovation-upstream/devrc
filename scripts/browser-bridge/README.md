# browser-bridge

A local, token-authenticated command channel that lets a Claude Code skill drive
the user's **real, logged-in Brave** browser. This is authorized personal
automation on the user's own workbench.

It is a **sibling** to the activity-collector's `scripts/collector/browser-ext/`
(a one-way telemetry sink). browser-bridge is a *command* channel and does not
touch the collector or its extension.

## Architecture

```
  Claude skill  ──HTTP POST /cmd──▶  server.py (loopback rendezvous)
   (scripts/browser-bridge/browser)        │
                                     GET /poll (long-poll) ▲   ▼ dispatch
                                            │
                                MV3 extension service_worker.js
                                  (runs in the LIVE Brave session)
                                            │
                          chrome.scripting / chrome.tabs / captureVisibleTab
                                            │
                                  POST /result  ──────────▶ back to the skill
```

Three actors, one loopback meeting point:

1. **`server.py`** — a stdlib `http.server` bound to `127.0.0.1:8788`. Holds a
   command queue and correlates each `POST /cmd` (skill) with the matching
   `POST /result` (extension) by an id.
2. **`extension/`** — a standalone MV3 extension. Its service worker long-polls
   `GET /poll`, executes the op against the active tab, and posts the result.
3. **`browser`** — the bash skill entrypoint Claude calls (`html`, `eval`,
   `tabs`, `nav`, `screenshot`, `health`, `instances`).

### Multiple instances per host

More than one Brave profile can each run the extension and be driven
independently. The server keeps a **registry of connected instances**, each with
its **own command queue** — a command for one profile is never delivered to
another's `/poll` (this is what fixes the 2× contention of the old single-queue
design when two profiles were connected).

- **Routing key.** Each instance has a stable auto-id (`crypto.randomUUID()`,
  persisted by the extension in `chrome.storage.local`) and an optional user
  **label** (set in the extension options). The effective routing key = the
  label if set, else the auto-id. **Labels are the human key and must be unique
  per host.**
- **Targeting.** With exactly one instance connected, no flag is needed
  (back-compat). With more than one and no `--instance`, a command **errors** and
  lists the connected instances (it never guesses). `browser --instance <key>
  <op>` targets one explicitly (the key matches either the label or the auto-id);
  an unknown key errors. `--instance` works for every op.
- **`browser instances`** lists the connected instances (routing key, label,
  auto-id, active-tab url/title) as JSON. `/health` also reports them.
- **Newest supersedes.** If a NEW connection (different auto-id) registers for a
  routing key that already has a live connection, the old one is dropped and any
  in-flight command on it resolves to a `superseded` error (no orphaned waiter).
  This handles a duplicate/stale connection cleanly. ⚠ Two *different* profiles
  sharing one label is a misconfiguration — **give each profile a unique label.**
  The displaced connection's own `/poll` gets a **distinct `409 superseded`
  signal** (not the idle `204`), on which the extension **backs off ~30s** (and
  surfaces a "superseded — set a unique label" state) rather than re-registering
  instantly. That deliberately breaks what would otherwise be a mutual-supersede
  **livelock** (two same-label workers re-polling at loopback speed, burning CPU
  and flooding the journal). The supersede is logged **once per displacement**,
  never per poll. (The server-side signal + once-logging are unit-tested; the
  extension's back-off can only be verified in a real browser — see below.)
- **Wire protocol.** `/poll` carries the instance identity in the
  `X-Bridge-Instance-Id` / `X-Bridge-Label` headers (+ optional
  `X-Bridge-Active-Url`/`-Title` for cheap `instances`/`health` enrichment);
  `/result` echoes its `instanceId` in the body; `/cmd` accepts an optional
  `target`. All of these stay bearer-authed and Host-checked — the security gate
  is unchanged. A legacy extension that polls with no identity is assigned one
  synthetic instance (`LEGACY_INSTANCE_ID`) so it still works unnamed.

### Transport: HTTP long-poll (not WebSocket)

An MV3 service worker can't bind a socket, so the local server is the rendezvous.
We use an **HTTP long-poll command queue**:

- The extension issues `GET /poll`, which blocks until a command is queued (or
  ~25s → `204`, then it immediately re-polls). A pending fetch keeps the MV3
  worker alive, so **the long-poll itself is the keepalive** — no RFC6455 ping.
- `POST /cmd` enqueues a command and blocks (bounded) for the reply.

Long-poll was chosen over a hand-rolled stdlib WebSocket because the whole
rendezvous stays pure `http.server` + `threading` and is **fully unit-testable
with stdlib alone** against an in-process fake extension — no new pip deps
(matching the receiver's stdlib-only footprint; the nix unit pins python312).

## Security model

The socket is loopback-only, but a malicious web page could still try to reach
it (DNS-rebinding). Two independent gates defeat that:

- **Bearer token** on *every* endpoint — the skill and the extension's long-poll
  alike. The secret is auto-created `0600` at `~/.config/browser-bridge/token`
  (`secrets.token_urlsafe`) on first start. Missing/wrong → `401`. A rebinding
  page cannot read the token file, so it cannot forge a request.
- **Host-header allowlist** — only `127.0.0.1` / `localhost` / `::1` accepted;
  anything else → `403`. A rebind victim page carries a foreign `Host`.

The extension holds `<all_urls>` host permissions + `scripting` (maximal — it
must run in whatever tab is active). This can be scoped down later; noted in
`extension/README.md`.

## Ops

| op | maps to | returns |
|----|---------|---------|
| `getHtml`    | `chrome.scripting` → `document.documentElement.outerHTML` | `{url,title,html}` |
| `eval`       | `chrome.scripting.executeScript` (MAIN world) of `js`     | `{url,value}` |
| `tabs`       | `chrome.tabs.query({})`                                   | `{tabs:[...]}` |
| `nav`        | `chrome.tabs.update(active,{url})`                        | `{tabId,url}` |
| `screenshot` | `chrome.tabs.captureVisibleTab` (png)                     | `{url,dataUrl}` |

Server envelope: `POST /cmd` → `200 {"ok":true,"result":{id,ok,data}}`, or a
structured error: `503 extension_not_connected`, `504 timeout`,
`409 ambiguous_instance` (>1 connected, no `target`), `409 superseded`,
`404 unknown_instance`, `400 unknown_op|missing_field:<f>`, `401 unauthorized`,
`403 bad_host`.

`GET /health` → `{"ok":true,"extension_connected":bool,"count":N,"instances":[{key,label,instanceId,activeTab},…]}`.
`GET /instances` → `{"ok":true,"count":N,"instances":[…]}`.

## Telemetry (activity pipeline)

Each **handled command** (`getHtml`/`eval`/`tabs`/`nav`/`screenshot`, incl. its
error/ambiguous outcomes) emits **one** event into the personal
activity-telemetry pipeline, so browser-skill usage is first-class self-telemetry
in ClickHouse `activity.events`:

- **`source="browser-bridge"`, `kind="cmd"`** — a *distinct* source from the
  collector's `browser` (nav/scroll) source; the two are kept separate.
- **Fields:** `text` = the active-tab bare **domain** (or the op when no domain),
  `duration_ms` = server-side latency, `exit_code` = 0/1, and a tiny `payload`
  JSON `{op, key, outcome[, domain]}` (`key` = the `--instance` routing target,
  empty for the implicit single-instance case; `outcome` ∈
  `ok|no_extension|ambiguous|unknown_instance|superseded|timeout`).
- **METADATA-ONLY (privacy):** it emits **only** the op name, instance key,
  outcome, latency, and the active tab's **bare domain** — **never** the eval
  source, page HTML, screenshot bytes/data-URLs, a full URL with path/query, or
  any page content.
- **Best-effort / fire-and-forget:** emitting runs **after** the HTTP response is
  sent and can never delay or break a command — a missing collector, unwritable
  spool, or any exception is swallowed and the command still succeeds. No new
  deps: it reuses the collector's `scripts/collector/keylog/spool_emit.py`
  (single source of truth for the v1 line format), located by its stable
  absolute repo path (`~/workspace/devrc/…`) because `server.py` is deployed as a
  flat `/nix/store` symlink so `__file__` can't find the sibling collector tree.
- `health`/`instances`/`poll`/`result` are noise and deliberately do **not** emit.

Feeds the `activity.events` table (and, once its registry learns the source,
`adoption-scan`). The live end-to-end check (needs the running collector + real
Brave): run any `browser` command, then confirm a `source='browser-bridge'` row
landed in `activity.events`.

## Icon

A gruvbox-tinted **bridge / chain-link** glyph (blue loopback node linked to the
yellow browser node on a dark rounded field). Source is
`extension/icons/icon.svg`; the committed PNGs (`icon-16/32/48/128.png`) are
rasterised from it and wired into `manifest.json` (`icons` + `action.default_icon`).
Regenerate after editing the SVG:

```bash
cd extension/icons
nix-shell -p librsvg --run 'for s in 16 32 48 128; do rsvg-convert -w $s -h $s icon.svg -o icon-$s.png; done'
```

## Running the tests

```bash
# Python (server.py) — headless, no Brave, no network beyond loopback:
nix-shell -p python312Packages.pytest --run "pytest scripts/browser-bridge/tests"

# Extension protocol logic (pure, no chrome.* runtime):
nix-shell -p nodejs --run "node --test scripts/browser-bridge/tests/protocol.test.mjs"
```

The Python suite (`tests/test_server.py`) also runs as part of
`scripts/run-tests.sh` (it's in the hermetic set). It covers: token gen + `0600`
perms, `401`/`403` gates (incl. the instance-scoped `/poll` + `/result`),
per-instance `/health` + `/instances`, a `/cmd` round-trip against an in-process
fake extension, `503`/`504` no-extension/timeout paths, unknown-op + bad-JSON
errors, request↔reply id correlation (incl. out-of-order), and the multi-instance
registry: routing by key, independent queues (no cross-delivery), the ambiguity
error, unknown-target, label-vs-auto-id key resolution, supersede-on-duplicate
(incl. an in-flight command resolving to `superseded` with no orphaned waiter,
the displaced poll returning the distinct `409 superseded` signal instead of the
idle `204`, and the supersede being logged exactly once per displacement — the
no-churn/livelock-fix contract), legacy no-handshake back-compat, and an icon
sanity check (each declared PNG
exists and its IHDR size matches). The chrome.* glue in `service_worker.js`
genuinely needs a real browser and is covered by the manual checklist in
`extension/README.md`.

## Deploy (nix)

`nix/home.nix` deploys `server.py` to `~/.config/browser-bridge/server.py` and
runs it as the `browser-bridge` systemd **user** service (loopback, port 8788,
`X-Restart-Triggers` so `home-manager switch` restarts it on a code change). The
runtime token file lives alongside it in the same real dir.

```bash
home-manager switch --flake ~/workspace/devrc --impure
systemctl --user status browser-bridge
```

## End-to-end manual verification (the one step that needs real Brave)

1. `home-manager switch …` — starts the `browser-bridge` service on 127.0.0.1:8788.
2. Load the extension: Brave → `brave://extensions` → enable **Developer mode** →
   **Load unpacked** → select `scripts/browser-bridge/extension/`.
3. Open the extension's **options** (⋯ → Options / "Extension options"), paste the
   token from `~/.config/browser-bridge/token`, port `8788`, **Save**.
4. `scripts/browser-bridge/browser health` → `{"ok":true,"extension_connected":true}`.
5. Focus a tab where you are **logged in** (e.g. Gmail), then
   `scripts/browser-bridge/browser html | grep -i <your-name-or-account-marker>` —
   seeing logged-in-only markup **proves it's the live authenticated session**,
   not a fresh fetch.

### Verifying multiple instances

1. In a **second** Brave profile, load the same unpacked extension and pair it
   (token + port).
2. Give each profile a **unique label** in the extension options (e.g. `work`
   and `personal`), Save, and reload each extension card.
3. `scripts/browser-bridge/browser instances` → both show up (keys `work` /
   `personal`, each with its active-tab url).
4. `scripts/browser-bridge/browser html` with both connected → **errors** and
   lists the instances (it won't guess).
5. `scripts/browser-bridge/browser --instance work html` → returns the `work`
   profile's active tab; `--instance personal html` → the other. That per-tab
   difference confirms targeting.

⚠ After editing anything in `extension/`, click the **reload** ↻ on the
extension card in `brave://extensions` — Brave does not hot-reload unpacked
extensions.
