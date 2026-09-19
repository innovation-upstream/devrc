# Proposal: Claude Code Usage Tracker extension

Status: scoped and IMPLEMENTED 2026-09-19 (branch `claude-usage-tracker`; the recon section remains the reference for the API schema). Decisions below are operator-confirmed.

## Goal

Know which Claude account sessions still have usage quota — and when each resets —
without flipping between accounts and guessing. A snapshot of every account's
usage is persisted each time claude.ai is opened (per account), a toast summarizes
the current account, and the toolbar icon opens a popup listing ALL known
accounts.

## Recon findings (measured live 2026-09-19, workbench)

- **Usage endpoint**: `GET https://claude.ai/api/organizations/<org-uuid>/usage`
  → cookie-auth'd JSON. Observed schema (normalize defensively, fields may be null):
  - `five_hour {utilization, resets_at, locked_reason}` — the session window
  - `seven_day {utilization, resets_at}` — weekly all-models
  - named model windows (`seven_day_opus`, `seven_day_sonnet`, codenames like
    `nimbus_quill`) — mostly null; treat as opaque extra rows when non-null
  - `limits[]` — display-ready: `{kind: session|weekly_all|weekly_scoped, percent,
    severity, resets_at, scope.model.display_name, is_active}`
  - `extra_usage` — credits: `{is_enabled, monthly_limit (minor units),
    used_credits, currency, disabled_reason}`
  - `seven_day_breakdown.rows[]` — per-surface weekly split incl.
    `{key: "claude_code", display_name: "Claude Code", percent}` ← the namesake row
- **Account/org discovery**: `GET /api/organizations` → `[{uuid, name, ...}]`;
  active account = the org whose usage was fetched. Name embeds the email.
- **Auth**: session cookie. Fetch MUST run in page context
  (`credentials:"include"`) via a content script — service-worker-context fetch
  may drop SameSite cookies (unverified, so design for the safe path).
- **Panel**: SPA hash route `claude.ai/new#settings/usage`; refetches on remount.
  We do not scrape the DOM — we call the same API the panel calls.
- **Staleness is structural**: only the ACTIVE account's cookie exists, so
  inactive accounts can only be as fresh as their last page-open snapshot.
  Make "as of Xm ago" first-class in the UI.
- **Tooling gap**: browser-bridge cannot capture network traffic (fixed op
  allowlist; CDP limited to eval/screenshot/input/emulate). Worked around via
  in-page fetch/XHR monkey-patch + hash-router re-trigger. Future browser-bridge
  `net` op (CDP Network domain) would close this — out of scope here.

## Architecture (operator decisions applied)

Pure MV3 extension at `scripts/claude-usage/` — **no sidecar** (single Brave
profile, in-product account switcher). Reuses devrc extension conventions
(dl-router's manifest discipline, sync-first-turn listener registration,
node:test suites) without its server.

```
scripts/claude-usage/
  extension/
    manifest.json          # MV3; host_permissions: https://claude.ai/*; notifications, storage, tabs, alarms
    content_probe.js       # runs on claude.ai; fetches /api/organizations + /usage in page context; messages SW
    service_worker.js      # orchestration: triggers, storage, thresholds, toasts, badge
    popup.html/js          # all-accounts dashboard
    lib/normalize.js       # pure: raw usage JSON → normalized record (shared CS/SW/popup/tests)
    lib/timefmt.js         # pure: resets_at → countdown strings, staleness
  tests/  *.test.mjs       # node:test, collected by the node gate tier
```

### Data model (chrome.storage.local)

```
accounts[orgUuid] = {
  orgName,                  // includes email
  session:  {utilization, resetsAt},
  weekly:   {utilization, resetsAt},
  extraRows:[{label, percent, resetsAt}],      // limits[] weekly_scoped + named windows
  credits:  {enabled, used, limit, currency, disabledReason},
  codeWeeklyPercent,                            // seven_day_breakdown claude_code row
  severity, isActiveLimit,
  asOf: epochMs,
  history: [[epochMs, sessionUtil, weeklyUtil], ...]   // ring buffer, 7d @ ≥15min cadence
}
lastToast = {orgUuid, kind, at}   // toast dedup
```

### Behavior

1. **Snapshot trigger**: `tabs.onUpdated` (complete) + `onActivated` for
   `claude.ai` tabs → inject/ask `content_probe.js` → it fetches orgs + usage
   and messages the SW. Account switch (org uuid differs from last active) →
   immediate snapshot + toast for the new account.
2. **Persist** normalized record + history sample (dedup samples closer than 15 min).
3. **Toast** (chrome.notifications, dl-router precedent):
   - on every claude.ai page open: current account — "Session 9% · resets 4h46m · Weekly 47%"
   - threshold alerts: session or weekly ≥80%, `locked_reason` set, or credits
     `disabled_reason` — alert even without a fresh page open, via a light alarm
     (15 min) that re-probes ONLY while a claude.ai tab exists
   - dedup: same (account, kind) not re-toasted within 30 min; silent on 401
     (logged out → mark account stale)
4. **Icon**: badge shows active account's session % (color = severity;
   gray = stale >6h). Click → popup.
5. **Popup** (operator chose popup over tab): one row per account — name,
   session % + reset countdown, weekly %, credits left, "as of Xm", sparkline;
   active account first. Countdowns computed at render from persisted `resetsAt`.

### Error handling
- 401/403 → mark stale, no toast spam, badge gray.
- Schema drift → normalizer keeps unknown fields out, never throws on nulls
  (all recon-observed null variants included in fixtures).
- Clock: persist raw ISO `resets_at`; compute countdowns at display; expired
  reset → "resets soon / unknown until next snapshot".

## Test coverage (complete — all suites node:test, wired into the gate)

Registered as a new target in `scripts/run-node-tests.sh` + its floor table
(plus `TARGET_FLOORS` expectations pinned two-way, same as existing targets).

| suite | pins |
|---|---|
| normalize.test.mjs | golden SYNTHETIC fixture per observed schema (all nulls present), unknown-field tolerance, claude_code row extraction, severity/is_active mapping |
| timefmt.test.mjs | countdown boundaries (59s/60s, 23h/1d), expired, future, null resets_at, tz-independent (UTC-pinned) |
| thresholds.test.mjs | ≥80 crossing fires once, no re-fire within dedup window, recovery re-arms, locked_reason/credits alerts, alarm-only path |
| store.test.mjs | upsert per org, ring-buffer cap + 15min dedup, 7d eviction, migration/default shapes |
| probe-protocol.test.mjs | CS↔SW message shapes, 401/403/network error mapping, account-switch detection |
| trigger.test.mjs | onUpdated/onActivated dedup, claude.ai-only gating, sync-first-turn registration (MV3 cold-start) |
| popup.test.mjs | pure render functions: ordering (active first, then freshest), staleness display, badge %, empty state |
| badge.test.mjs | % formatting, severity colors, stale gray, >2-digit clamping |

Repo is PUBLIC: fixtures must be synthetic (fake org uuids, "user@example.com"
style names) — never the recon captures. Gated by `test_no_captured_text.py`
like everything else.

## Deploy

- `nix/home.nix`: managed copy to `~/.local/share/claude-usage-ext/` (same
  mechanism as browser-bridge-ext), plus the extension's own build/check
  derivation if needed (none likely — plain JS).
- One-time manual step (cannot be automated reliably): Brave → load unpacked
  from that path; note it in the module comment.
- Gate: `scripts/gate.sh --tier node` must pass; scoped iterations via
  `scripts/scoped-tests.sh`.

## Explicitly out of scope (future)
- Cross-profile aggregation sidecar (only if profiles ever multiply)
- Background polling of INACTIVE accounts (impossible — cookie mechanics)
- bar-status pill for usage (separate `bar` subsystem)
- browser-bridge `net` capture op
- Options UI (thresholds are constants; YAGNI)
