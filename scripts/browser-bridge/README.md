# browser-bridge

A local, token-authenticated command channel that lets a Claude Code skill drive
the user's **real, logged-in Brave** browser. This is authorized personal
automation on the user's own workbench.

It is a **sibling** to the activity-collector's `scripts/collector/browser-ext/`
(a one-way telemetry sink). browser-bridge is a *command* channel and does not
touch the collector or its extension.

## Quick start / binary path

The executable lives at
`~/workspace/devrc/scripts/browser-bridge/browser` (also
`~/.claude/skills/browser/browser` if the skill dir is symlinked):

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB health                              # connected instances + count
$BB --instance <key> open <url>         # open a NEW tab this session owns → tabId
$BB --instance <key> --tab <id> html    # act on a specific tab
```

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
| `tabs`       | `chrome.tabs.query({})`                                   | `{tabs:[...],ownedTabId}` |
| `nav`        | `chrome.tabs.update(tab,{url})`                           | `{tabId,url}` |
| `screenshot` | `chrome.tabs.captureVisibleTab` (png)                     | `{url,dataUrl}` |
| `open`       | `chrome.tabs.create({url,active:false})`                 | `{tabId,url}` |
| `close`      | `chrome.tabs.remove(tabId)`                               | `{closed:tabId}` |

`open`/`close` are dispatched to the extension; `release` (drop ownership, don't
close the tab) is handled server-side and never reaches the extension. The
tab-scoped ops (`getHtml`/`eval`/`nav`/`screenshot`/`close`) run against the
calling session's owned tab when it has one (see Session isolation), else the
active tab.

Server envelope: `POST /cmd` → `200 {"ok":true,"result":{id,ok,data}}`, or a
structured error: `503 extension_not_connected`, `504 timeout`,
`409 ambiguous_instance` (>1 connected, no `target`), `409 no_owned_tab`
(`close` with nothing owned), `409 superseded`, `404 unknown_instance`,
`400 unknown_op|missing_field:<f>`, `400 bad_tab` (a non-numeric/non-scalar
`tab` from a raw caller — the CLI already validates `--tab`), `401 unauthorized`,
`403 bad_host`, `429 rate_limited|queue_full` (the per-instance concurrency
backstop — see below; body carries a `retry_after` hint).

## Concurrency backstop (per-instance rate limit + queue cap)

The extension is a **single serial connection**, and the transport used to accept
`/cmd` dispatches with **no backpressure**. An audit found a 44,061-event storm —
**43,991 `eval`s in one hour (~13/sec sustained)** from an unisolated fleet/loop —
that saturated one instance's queue and ballooned latency from ~10 ms to ~5.5 s.

To bound that damage the server enforces two **per-instance** limits (the
extension is the bottleneck, so throttling instance A never affects instance B):

- **Token-bucket rate limit** on accepted `/cmd` dispatches: `BROWSER_BRIDGE_RATE_PER_SEC`
  sustained (default **5/sec**), `BROWSER_BRIDGE_BURST` burst (default **20**). A
  dispatch that would exceed the bucket is **rejected** (never silently queued).
  `RATE_PER_SEC=0` disables it (power-user unlimited). When rate-limiting is
  active (`RATE_PER_SEC>0`), `BURST` is **clamped to ≥1** — a sub-1 burst can
  never hold a whole token, so it would silently rate_limit *every* `/cmd`
  forever; use `RATE_PER_SEC=0`, not a 0 burst, to disable throttling.
- **Queue-depth cap** `BROWSER_BRIDGE_MAX_QUEUE` (default **32**): if an instance's
  pending (admitted-but-unfinished) command count is at the cap, new `/cmd` are
  rejected until it drains — this bounds the latency tail. `MAX_QUEUE=0` disables it.

A rejected dispatch returns **HTTP 429** `{"ok":false,"error":"rate_limited"|"queue_full","retry_after":<s>}`
(caller-visible backpressure). The `browser` CLI prints a back-off message and
exits non-zero, so a runaway loop gets a hard, detectable signal. **The defaults
do not hurt legitimate use:** an interactive/agent workflow is a handful of ops
per burst (well under burst 20 and depth 32); only a sustained high-rate flood is
throttled.

The admission check runs **under the existing lock**, is lock-free of any blocking
wait, and rejects **before** the command joins the per-tab FIFO turnstile — so it
can neither deadlock nor wedge the turnstile (a rejection returns immediately,
leaving no orphaned waiter). The audited concurrency core (single `Condition`, no
lock held across a blocking wait, turnstile self-releases in `finally`) is
unchanged.

**Observability (so the next storm is attributable):** a throttle both `log()`s a
distinct `{"event":"throttled","key":…,"reason":…,"sess":…}` line AND emits a
telemetry event (`outcome="throttled"`, `payload.reason`) into `activity.events`.
`payload.sess` is a **coarse, non-reversible** fingerprint (first 8 hex of
sha256 of the `X-Session-Id`) — enough to attribute a flood to a session without
storing the raw id, kept metadata-only (no page content). This is the ONLY event
that carries the session hash.

## Session isolation (concurrent-session tab clobbering)

The transport is correct (each `/cmd` gets a unique cid, a FIFO outbox, and a
cid-correlated reply — no cross-delivery). The old clobber was **semantic**:
every op targeted "the active tab of the last-focused window", so two Claude
sessions driving one instance interleaved on **one shared tab** (A `nav X` then
`getHtml`; B `nav Y` in between; A reads Y). Command-level serialization existed;
per-session (multi-step **workflow**) isolation did not.

**Fix — a per-session owned tab:**

- **Per-session id.** The `browser` skill sends a stable `X-Session-Id` on every
  `/cmd`, derived (in order) from `CLAUDE_CODE_SESSION_ID` (Claude Code's own
  session UUID — identical across every Bash-tool call in a session, verified on
  the 2.1.x CLI) → `CLAUDE_SESSION_ID` (defensive alternate) → `$TMUX_PANE` (each
  session runs in its own tmux pane) → a random token cached under the shell's
  PPID (the Claude Code process, stable across a session's calls). It is used for
  **routing only** and is **never** trusted for auth — bearer + Host still gate
  every request. If two sessions ever resolve the same id they share a tab
  (documented degradation — no worse than before).
- **⚠ Sibling subagents share identity (known limitation).** Per-session
  isolation covers concurrent **top-level** sessions, NOT sibling subagents of
  one parent. Empirically (dumped from inside two parallel subagents) a subagent
  inherits the **parent's** `CLAUDE_CODE_SESSION_ID` *and* runs in the same
  `$TMUX_PANE`, and the harness injects **no** subagent-unique, stable env var
  (no agentId/taskId/tool-use id is visible to the subagent's own shell), so two
  sibling subagents derive the SAME session id and would own the same tab. The
  robust fix is **explicit tab handles**: each such driver runs `browser open`
  (which returns a real `tabId`) and then threads its OWN `--tab <id>` on every
  subsequent op. An explicit `--tab` **overrides** owned-tab routing entirely, so
  two indistinguishable drivers never collide. See SKILL.md → Concurrent drivers.
- **Ownership.** `browser open [url]` creates a tab (background, `active:false`,
  so parallel sessions don't fight over the foreground) and the server records
  `(instance_key, session_id) -> tabId`. That session's tab-scoped ops then route
  to its tab; a session with **no** owned tab falls back to the active tab (the
  one-shot read path). `--tab <id>` overrides explicitly. `browser close` closes
  the tab + drops ownership; `browser release` drops ownership only.
- **Idempotent `open` (no orphaned tab).** A second `open` from a session that
  already owns a **live** tab returns that SAME tab (the server passes the owned
  tabId as `reuseTabId`; the extension reuses it) instead of creating a second
  real tab — so a double `open` never orphans/leaks the first tab. If the owned
  tab is **gone**, `open` transparently creates a fresh one.
- **Self-heal on a vanished owned tab.** If the user manually closes an owned
  background tab, the next tab-scoped op dispatches the stale tabId and the
  extension returns `owned_tab_gone` (ok:false). The server then **drops** that
  session's ownership so the NEXT command self-heals to the active-tab fallback
  (instead of staying wedged to the dead tab until the TTL reclaims it). `browser
  close` clears the mapping **unconditionally** — even when the tab was already
  gone — and the extension treats an already-gone close as success (idempotent).
- **FIFO on remaining contention.** With isolation most sessions use different
  tabs and don't contend. When two commands DO target the same tab (both active,
  or one `--tab`s another's owned tab) they are serialized **FIFO in arrival
  order** — competing commands **queue** (blocking) rather than fail-fast,
  bounded by `cmd_timeout` so a queued command can't block forever. Commands to
  **different** tabs never block each other.
- **Lifecycle (reversible — flag for veto).** Ownership has an idle TTL
  (`BROWSER_BRIDGE_OWNER_TTL`, default 900 s). On expiry the mapping is
  **released** so a dead session doesn't leak ownership, but the real Brave tab
  is deliberately **NOT closed** (never yank a visible tab out from under the
  user) — only an explicit `browser close` closes it.
- **Screenshot caveat (honest).** `captureVisibleTab` only ever captures the
  **visible** tab of a window. Screenshotting a session's **background** owned
  tab therefore briefly activates it → captures → restores the previously-active
  tab (a short visible flicker in that window), so we never silently capture the
  wrong tab. A screenshot of an owned tab that is already visible is unaffected.
- **Backward-compat.** A single session with no `open` (and even no
  `X-Session-Id`) behaves exactly as before: active-tab ops, no `tabId` injected.
  The multi-instance `--instance` targeting, ambiguity/supersede semantics, and
  telemetry are unchanged.

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
sanity check (each declared PNG exists and its IHDR size matches). It also covers
**session isolation**: `open` recording `(instance,session)->tabId`, two sessions
getting distinct ownership, ops routing to the owning session's tabId (and the
interleaved-two-sessions clobber test), the active-tab fallback + `--tab`
override, `close`/`release` (drop ownership; `close` dispatches a remove-shaped
command, `release` never touches the extension), `tabs` ownedTabId annotation,
per-tab FIFO serialization (same-tab commands serialize in arrival order,
different tabs don't block, a queued command still honours `cmd_timeout`), TTL
reclaim (injected clock — released, not closed), the `no_owned_tab` error, the
session id being routing-only (bearer/Host still enforced), and backward-compat
(single session, no open → unchanged active-tab dispatch). It also covers the
**self-heal + idempotency hardening**: an `owned_tab_gone` op dropping the stale
ownership (self-heal to active-tab) while an unrelated `--tab` gone tab does NOT
evict a healthy mapping, `close` clearing ownership even when the tab was already
gone, an idempotent double `open` returning the reused tab (no orphan) vs opening
fresh when the owned tab is gone, a malformed `tab` (list/dict/bool) returning a
clean `400 bad_tab` instead of a 500 (+ the `_coerce_tab`/`_is_tab_gone` helper
units), and an explicit `--tab` overriding owned-tab routing (the subagent
escape hatch). It also covers the **per-instance concurrency backstop**: the
token-bucket admission (burst passes then `rate_limited`, refill-over-fake-time
resume, cap-at-burst), the queue-depth cap (`queue_full` at `MAX_QUEUE`, drain
resumes), strict per-instance isolation (throttling A never throttles B), the
disable path (rate=0/max_queue=0 → unlimited), a rejected submit leaving NO
turnstile/waiter residue (no deadlock), the HTTP `429 rate_limited` + throttle
telemetry event (metadata-only, a COARSE non-reversible session hash — never the
raw id), the HTTP `429 queue_full`, that the production defaults never throttle a
normal small burst, and the `browser` CLI backing off (non-zero exit) on a 429.
Current totals: **92 Python** (`test_server.py`) + **22 node**
(`protocol.test.mjs`). The chrome.* glue in `service_worker.js` genuinely needs a
real browser and is covered by the manual checklist in `extension/README.md`.

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
