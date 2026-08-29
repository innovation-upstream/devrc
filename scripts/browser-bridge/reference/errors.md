# Error catalogue + the stale-extension playbook

**Load this when:** any op returned an error string you don't recognise — look it up
below · an op the CLI knows answers `unknown_op` · you clicked reload ↻ in
`brave://extensions` and the old behaviour persists · `health` still shows the old
`extension_version` after a reload · a bridge that was WORKING suddenly returns
nothing / `extension_not_connected` with no user action · you reloaded ↻ and the
instance you're driving is still dead.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`. This file is the catch-all: no error string
should strand you.

## Triage — start here

1. **A call that WORKED now fails or returns nothing** → `browser health` FIRST,
   before debugging the page or the CLI: the extension drops mid-session with no
   error. Fix: ↻ **in the profile you are driving**. A STALE BUILD is a DIFFERENT
   failure — Remove + Load unpacked, not a restart. → `reference/errors.md`
2. **Empty / half-built / `data.hidden:true` read** → throttled: `wake`, re-read.
3. **`null` from `js`/`eval`** → traps 1 then 2; fall back to `text`/`html` before
   concluding the bridge is down. **`unknown_op`** → stale extension (1). Any other
   error string → `reference/errors.md`.
4. **Never diagnose a site OUTAGE from a browser read** — "broken for real users?"
   needs server-side evidence (RUM, metrics, pod health, an anonymous `curl`).

(Moved from SKILL.md 2026-08-21 to restore its working headroom: #669 added
content without the eviction the byte ceiling requires. The outage rail stays
inline in SKILL.md — it is a correctness rail, not a debugging step.)

## Error shapes (from `/cmd`)

- `503 extension_not_connected` → extension not loaded/paired, or Brave closed.
- `504 timeout` → extension picked it up but didn't answer (tab unresponsive).
  **Exception — `ping`.** It runs on a SHORT deadline
  (`BROWSER_BRIDGE_PING_TIMEOUT`, default 10s) and the extension executes ONE
  command at a time, so a ping queued behind another session's heavy `nav` /
  `--fullpage screenshot` times out while the profile is perfectly healthy. Wait
  for the other op and re-run. **A ping 504 is not evidence of a stale build and is
  not a reason to restart Brave** — only a repeat failure on an IDLE profile is.
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
- `404 unknown_instance` → **read which of the two the CLI printed — they need
  OPPOSITE actions.** The status is the same for both; the message is not.
  - *"instance 'X' is **KNOWN but NOT CONNECTED**"* → your label is **right**; that
    Brave profile dropped its long-poll. **FULLY RESTART Brave** (a
    `brave://extensions` reload often no-ops). **Do NOT retry** — it fails
    identically until the profile reconnects. Measured 2026-08-02: 48 of 52
    `unknown_instance` failures used the CORRECT label, and `eval` was re-issued
    **37 times** against it because the old single message read as "wrong label".
  - *"instance 'X' is **UNKNOWN**"* → nothing has ever registered that key. Wrong
    label (or that profile was never wired up); the CLI lists the keys it has seen.
  Both print the dropped profiles' `last seen` + `last unanswered op` — that names
  whether a profile died mid-op or went quiet while idle.
- `503 extension_not_connected` → same split: *"HAS seen …"* means it was wired up
  and dropped (restart Brave, stop retrying); *"has EVER connected"* means load the
  extension and paste the token.
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
  top-frame input / `eval --frame` / `emulate` / `wake` / an emulated `nav`) was
  aimed at a non-`http(s)` tab (`chrome://`, `file:`, extension, devtools,
  **`about:blank`**, …); the bridge refuses to attach `chrome.debugger` there.
  Point the op at a real `http/https` tab.
  - **`cdp_attach_refused:about:` from `emulate` is the common one**: a tab from a
    bare `browser open` sits at `about:blank`, and you cannot emulate it (nor `nav`
    out of it under emulation — the emulated `nav` attaches on the tab's *current*
    url). Do `browser open <url>` → `browser emulate <preset>` → re-`browser nav
    <url>`; see `~/workspace/devrc/scripts/browser-bridge/reference/emulation.md`.
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

## ⚠ The extension can DROP mid-session — and ↻ is PER-PROFILE

Distinct from a stale build: this is a bridge that was **working in this very
session** and stops. Measured twice in one session (2026-07-31) — a healthy bridge
went `extension_connected:false` with **no user action, no error on the Claude
side**, and no signal other than ops that suddenly returned nothing. MV3 service
workers are evictable and the long-poll can lose its peer; treat a drop as normal
wear, not as evidence of a bug in the page, the CLI or the server.

🔴 **Actionable rule: re-run `browser health` the moment a call unexpectedly fails
or returns nothing — BEFORE you start debugging the page or the CLI.** It is one
cheap command and it collapses the whole "is the page broken?" branch.

The fix is the same manual step as a stale build (the user clicks ↻ on the card in
`brave://extensions`) — but you have to notice it first, and:

⚠ **↻ must be done in the PROFILE you are driving.** `brave://extensions` is
per-profile, so reloading it in profile A leaves profile B still disconnected —
while `health` keeps reporting a connected count **from A**, which reads as "the
extension is fine". Name the profile when you ask the user to reload, and confirm
recovery with `browser --instance <key> health`, not a bare `health`.
→ multi-instance semantics: `~/workspace/devrc/scripts/browser-bridge/reference/tabs-instances.md`

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
health` also shows the build** — each instance's loaded `extension_version` and
`extension_build`, the corresponding `_expected` values (from the DEPLOYED tree
at `~/.local/share/browser-bridge-ext/`, else the repo copy), and an explicit
`extension_stale` verdict: `true` = reload it, `false` = **verified current**,
**`null` = undecidable** — `null` is NOT "fine". `browser whoami` carries the
same fields plus `bridge.extension_build_current`.

🔴 **An ALL-CLEAR is computed from `extension_build`, NEVER from the version
(#324).** `extension_version` is `chrome.runtime.getManifest().version` — it
describes the manifest of the extension the worker LOADED — and `extension_id`
is derived from the load PATH, so **neither describes the code that is
running**. MEASURED 2026-08-04: two Brave profiles loading the SAME directory
reported an identical id, an identical `0.7.3` and `extension_stale: false`,
while one ran `main` and the other an unmerged 0.7.2 build whose source existed
**on no disk**. No version-shaped signal can separate those two rows, so a
version MATCH can never produce `false`.

`extension_build` is a generated LITERAL (`extension/build_id.js`) that the
service worker **imports**, so it is frozen into the loaded module graph and
travels with the code — a stale worker reports the stale marker by construction.

⚠ **The version now MOVES per build.** `manifest.json` is `<release>.<build>`,
e.g. `0.8.1.43738`, where the 4th component is derived from the marker
(`extension/README.md` § *Versioning*).

🔴 **This does NOT widen the reach of any branch above, and an earlier draft of
this paragraph claimed it did.** The version-mismatch branch at
`server.py:887` runs only `if not stale` — i.e. only when both markers are
present and AGREE. A code change moves the marker, so the markers DISAGREE,
`stale` is already `true`, and that branch is never reached. The marker was
already catching everything else, which is the whole reason it exists.

**The state that DOES reach `:887` is a release bump**, and it is routine rather
than exotic. Bumping `0.8.1.43738` → `0.9.0.43738` by hand changes only the
version string — which is normalised OUT of the marker's digest — so the marker
does not move at all. A worker loaded before the bump therefore reports a
matching marker with a disagreeing version, which is exactly `:887`. (An earlier
draft named a degraded config instead, where the server can read the deployed
`manifest.json` but not its `build_id.js`. That is the WRONG branch: `:879`
short-circuits on a missing marker and the verdict comes from `:883-884`.)

What the per-build version actually buys is **off-bridge legibility**: a human
can spot a stale profile in `brave://extensions`, with no bridge call at all,
because the two profiles now show different numbers. That is a real win and it
is the only one — it is not a detection improvement, it is a *reporting* one.

The verdict is **ASYMMETRIC**, because the two directions are not equally
knowable. `false` requires two markers that are present and identical — that is
the whole of it, and nothing else can produce it. `true` additionally comes from
a version MISMATCH with both sides known, marker or no marker: a mismatch is
positive proof that the loaded code is not the deployed code, so a missing
marker must not discard it. Everything else **fails closed** to `null` — a
marker missing on either side with versions that agree, or with either version
unknown. A profile still running ≤0.7.3 therefore reads `null` (or `true`, if
its version disagrees), which is correct.

Two things follow, both measured in the same session:

* **Staleness is PER PROFILE.** One profile can be current while another is not,
  at the same instant, on one host, from one directory. Check each one.
* **A full Brave restart is NOT sufficient.** Brave was genuinely fresh (oldest
  process 392 s, deploy 9 h earlier) and still ran the old code; restarting the
  bridge service did not help either. **Per-profile Remove + Load unpacked at
  `brave://extensions` is what reloads it.** The mechanism is UNEXPLAINED —
  don't let a story about MV3 worker caching harden into a finding.

🔴 **The older shape of this gap: an op answers `unknown_op` while `health`
reports a MATCHING `extension_version`.** That combination reads as a
contradiction and sends people debugging the server or the CLI instead. It is not
a contradiction — the *loaded* worker predates the op even though the manifest
version agrees. Observed on `wake` (2026-08-01). **Believe the op and the build
marker, not the version**, and go to the reload ladder below.

**⚠ Reload ↻ is UNRELIABLE, and a full Brave restart has been measured not to be
enough either.** The extension's long-poll keeps the OLD service worker alive, so
↻ often does NOT swap in the new build. If a reload doesn't take (the op still
returns `unknown_op`, or `ping` still shows the old `buildMarker`), the reliable
step is a **per-profile Remove + Load unpacked**; try a full quit/reopen first
only because it is cheaper, not because it is sufficient.

**Nuance (measured 2026-07-30): a ↻ reload DID take** — swapping in a new build after an
earlier full restart in the same Brave session. So ↻ is worth trying **first**, but only
when you have a **deterministic tell** for whether it took. Don't reason about it; test
it.

**The tell is `browser ping`** (extension ≥ 0.3.1). Pass `--instance` — two
profiles are normally connected, so a bare call gets `409 ambiguous_instance`:

```bash
browser --instance <label> ping
# NEW build → {"pong":true,"extensionVersion":"0.4.0","id":"<ext-id>","ops":[…,"ping"]}
# OLD build → op 'ping' returned unknown_op — … FULLY RESTART Brave …   (exit 1)
```

`ping` takes no tab and touches no page — it only reports the LOADED service
worker's own manifest version, its extension id and its op set. An older build
has never heard of the op name, so it cannot fake a pass. Non-zero exit on the
old build.

**`id` answers the other question: WHICH DIRECTORY did Brave load?** An unpacked
extension's id is derived from its absolute path, so the repo-path load and the
deployed-path load report the **same version but different ids** — version alone
cannot confirm the migration took. ✅ That derivation is **MEASURED**
(2026-08-01, Brave/Chromium on both NixOS hosts, unpacked extensions, two
paths — not generalised to packed extensions or other browsers): the id is
`sha256(absolute path)` → first 32 hex chars → each nibble `0-f` mapped to
`a-p`, with **no per-profile component**, so two profiles on one directory
report one id.

```python
h = hashlib.sha256(path.encode()).hexdigest()[:32]
ext_id = "".join(chr(ord("a") + int(c, 16)) for c in h)
```

Known values: repo path `~/workspace/devrc/scripts/browser-bridge/extension` →
`pkkoninbaeicfalpdkkmcknhnacjjjpi`; deployed path
`~/.local/share/browser-bridge-ext` → `bgbkamdlkdleahpgdgmjipjbgmepgenk`. So you
can **predict the id in advance** and check a re-point against it, not just
before-vs-after. Read the "before" id off the `brave://extensions` card *before*
clicking Remove — Remove wipes `chrome.storage.local`. The server still does not
compute an expected id (open follow-up); `whoami`'s
`bridge.extension_dir_expected` just tells you which directory it *should* be.

**Contract for any future extension change that must be provably loaded:** bump
`extension/manifest.json` AND add a new discriminator (a new op name, or a new
field in `ping`'s reply). Without one, reload-vs-restart is unfalsifiable — that
ambiguity cost three full Brave restarts in one session. With no such tell for
the build in front of you, skip ↻ and do the full restart.

**Where the extension loads from.** It should be
`~/.local/share/browser-bridge-ext/` — a real copy written by home-manager, which
no `git checkout`/`stash`/branch switch/worktree op can change under a running
verification. ⚠ Not immune to everything: a `home-manager switch` (or `ship.sh`)
run by a concurrent session on another branch still rewrites that tree — the
deploy removes the SILENT class, not every class. If `brave://extensions` shows
the extension's path inside `~/workspace/devrc/`, it is on the OLD git-mutable
path: re-point it (remove the card → **Load unpacked** → the
`~/.local/share/browser-bridge-ext/` directory → re-paste token/port/label in
Options), once per Brave profile. Full per-profile steps and the rollback
procedure: `scripts/browser-bridge/extension/README.md`.

Symptom of a stale build: an op the CLI knows returns `unknown_op`, or `health` still
shows the old `extension_version`. The `browser-bridge` **server** (not the extension)
DOES restart automatically on a `home-manager switch` (X-Restart-Triggers) — only the
extension needs the manual step.
