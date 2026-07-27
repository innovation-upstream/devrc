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
   `tabs`, `nav`, `screenshot`, `health`).

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
`400 unknown_op|missing_field:<f>`, `401 unauthorized`, `403 bad_host`.

## Running the tests

```bash
# Python (server.py) — headless, no Brave, no network beyond loopback:
nix-shell -p python312Packages.pytest --run "pytest scripts/browser-bridge/tests"

# Extension protocol logic (pure, no chrome.* runtime):
nix-shell -p nodejs --run "node --test scripts/browser-bridge/tests/protocol.test.mjs"
```

The Python suite (`tests/test_server.py`) also runs as part of
`scripts/run-tests.sh` (it's in the hermetic set). It covers: token gen + `0600`
perms, `401`/`403` gates, `/health` connection state, a `/cmd` round-trip against
an in-process fake extension, `503`/`504` no-extension/timeout paths, unknown-op
+ bad-JSON errors, and request↔reply id correlation (incl. out-of-order replies).
The chrome.* glue in `service_worker.js` genuinely needs a real browser and is
covered by the manual checklist in `extension/README.md`.

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

⚠ After editing anything in `extension/`, click the **reload** ↻ on the
extension card in `brave://extensions` — Brave does not hot-reload unpacked
extensions.
