# Handoff: activity-telemetry + self-hosted mail — 2026-06-27

## Goal
Two personal self-instrumentation systems, both now BUILT and LIVE: (1) the activity-telemetry pipeline (where time/attention/reading goes, 6 sources → ClickHouse → Grafana), and (2) a self-hosted mail inbox (forward Gmail → Postgres for automation). We're past "build it" and at the **decision point: use the data, or stop instrumenting.**

**Operate via skills** (authoritative, both updated + synced to laptop this session): `activity`, `mailbox`. Cross-session detail in memories: `activity-telemetry-pipeline`, `selfhosted-mail-inbox`, `laptop_host`.

## State now
- **devrc** `main` @ `7f192e9` (both hosts CONVERGED to main this session — workbench was 10 behind, now clean). No open devrc PRs.
- **homelab-talos** `trunk` — all activity/mail PRs merged (note: trunk tip is unrelated clawgate/task-drafter work from elsewhere).

**Activity pipeline — DONE + GREEN + VISUALIZED:**
- **6 sources live** (laptop has all; headless workbench has only `zsh`/`claude`): `zsh`, `tmux`, `keys`, `browser`, `claude`, **`i3`** (new — window/workspace focus = attention even when not typing; devrc PR #20).
- **browser source**: idle-aware `active_ms` (PR #21), **scroll capture** `scroll_pct`+`scroll_ms` (PR #23+#24), `app=brave`, icon (PR #22), manifest v1.3.0. Hand-loaded in Brave (see gotchas).
- **harness 8/8 GREEN** (PR #19 fixed the duration-vs-active conflation). X-Restart-Triggers (PR #16) means `ship.sh` restarts daemons on code change.
- **Dashboard**: new row "Attention (i3 focus) & Reading (scroll)" deployed (homelab PR #78) — attention-by-app (Alacritty 705m/Brave 231m/7d), by-workspace, reading-depth-by-domain, i3-switches. Live at `https://grafana.homelab.lan`.
- Laptop can now run homelab `kubectl` via the `homelab-kube-tunnel` SOCKS service (PR #18); `ship.sh` syncs skills laptop-ward (PR #17).

**Mail inbox — LIVE + verified:**
- Gmail (2 accounts) forward → `inbox.zacx.dev` (Cloudflare grey-cloud MX → Hetzner gateway :25 → nebula → homelab) → `mail-receiver` (aiosmtpd, Harbor image) → Postgres `mail` table (ns `mailbox`). Real mail flowing.
- **Spam-filtered** (PR #77): `via_gmail` (deterministic — 100% of real mail is Gmail-forwarded) + `category` generated columns. Query: `WHERE via_gmail AND category IN ('personal','security')`.

**CORRECTION (later same session): harness was NOT green — browser `active_ms` was ~20× inflated.** Re-verifying live state (not trusting the "8/8 GREEN" claim above) surfaced `OVERALL: FAIL` on `per_host_hour_active_cap`: **12h of browser "active" time logged in one 24h day** (true ~30min). Root cause: `service_worker.js` did a **non-atomic** `getState → mutate → setState` on `chrome.storage.session`; the concurrent handlers (`onActivated`/`onCommitted`/focus/idle) clobbered each other → lost idle-pauses (spans ran to the 1h cap) + double-fired tab switches each carrying the full span (proven: 2× 3.6M at one ts = 2h phantom). Idle/blur *detection* was fine. **FIXED + MERGED: PR #25** (`fix/browser-active-ms-race`, on `main` @ `ab6df86`) — extracted a mutex-serialized `state_store.js`, added redundant-nav suppression, and windowed the `per_host_hour_active_cap` invariant to a trailing 48h (append-only store can't recover from a one-time glitch on an all-time scan). 31 JS + 68 py tests green.

**CORRECTION 2 (next day): the mutex fix was INCOMPLETE — browser `active_ms` is structurally unfixable in-extension on i3.** After PR #25 deployed (laptop reloaded), the double-fire was gone (0 duplicate-ts) BUT per-hour active was still impossible (8.87h "active" in ~7h wall-clock; two 60-min caps 27min apart). **Decisive i3 cross-check:** during a window where Brave logged 60 min active, the i3 source showed the user was in **Alacritty for a straight ~10-min stretch** (focus share Alacritty 29 vs Brave 11). Root cause #2 (the real one): `chrome.idle` measures **system-wide** input (terminal typing keeps Chrome "active") and `chrome.windows.onFocusChanged(blur)` is **unreliable on i3** → the extension counts other-app time as browser engagement. The "is Brave focused" truth lives in **i3, not Chrome** — so no in-extension accounting can fix it.

**RESOLUTION (merged, verified): retire extension `active_ms`, derive attention downstream.**
- **Derived metric = i3 "Brave-focused" intervals ∩ active-tab domain timeline** (domain from browser `nav` events — note URL is in the **`text` column**, not `payload.url`). Mathematically bounded by i3 dwell, so it *cannot* inflate. **Verified:** the 11:00 UTC hour reads **11.9 min** (was 144.4) = matches i3 Brave dwell 12.4 min; sum-per-domain 315min ≤ Brave i3 dwell 342min (7d).
- **homelab-infra PR #79 (MERGED):** dashboard panel "Browser active time by domain (s)" → "Browser attention by domain (i3-derived, s)".
- **devrc PR #26 (MERGED, `main`):** harness — *retired* `active_ms_capped` + `per_host_hour_active_cap` (metric gone, not silenced), added `derived_attention_consistent` guard (per-domain ≤ Brave i3 dwell). **Live harness `OVERALL: PASS`.** (Supersedes the PR-#25 48h-window stopgap.)
- Lesson: extension `active_ms` was untrue the WHOLE time the dashboard showed it; the i3-dwell attention panel was always the correct number.

**DONE (was deferred): vestigial-event stripping — devrc PR #27 (MERGED).** Removed the dead `active_ms` accumulator (`active_time.js`), focus/idle listeners, `active_ms`/`state` payload fields, and matching harness helpers; `idle` permission gone; manifest v1.4.0; receiver tolerant of a legacy client during the reload window. Ext is now nav(url/title)+scroll only (~2000 dead focus/idle events/day stop flowing). JS 21/21 + py 83/83 green. **Operator: needs a Brave reload to activate** (`cp -fL ~/.config/activity-collector/browser-ext/*.js ~/.local/share/activity-browser-ext/` → reload in brave://extensions).

**IN FLIGHT / not done:**
- **Human spot-check** still never formally done, but the i3 cross-check + derived-metric validation effectively ground-truthed the data this session. The activity thread is otherwise WRAPPED.

## Next steps (ranked)
1. **THE FORK is now live and on TRUSTWORTHY data.** The measurement layer is correct + complete + guarded. Recommendation carried into the decision: **STOP on activity and pivot to email automation on the live `mail` table** (the original "heavy automation" goal — triage, task/receipt extraction, digests, alerting). The activity thread has hit diminishing returns; further mining risks a well-instrumented saw-sharpen. A thin **weekly LLM digest** over the (now-true) activity data is the only remaining activity-side option if a digest is actually wanted — but it's a cap, not a new altitude.
2. **Lower:** browser ext is hand-loaded (not nix-declarative) — if it churns, wire a `--load-extension`/auto-copy step. Optional Civitai-alert Gmail filter (95/day noise; already hidden by `category`).

## Gotchas / decisions / dead-ends
- **Browser ext is NOT fully nix-managed.** Loaded unpacked in Brave from `~/.local/share/activity-browser-ext/` (real-file copy; Chromium dislikes the nix-store symlink dir). A `service_worker.js`/content-script change needs: ship → `cp -fL ~/.config/activity-collector/browser-ext/*.js ~/.local/share/activity-browser-ext/` → **reload in `brave://extensions`** (+ reload the page — content scripts only inject post-reload).
- **Scroll capture dead-end → fix:** the first impl used `import(chrome.runtime.getURL("scroll_track.js"))` in the content script — **fails silently on CSP**. PR #24 replaced it with a shared-isolated-world-global pattern (two ordered content scripts; `scroll_track.js` sets `globalThis.__activityScrollTracker`, no `web_accessible_resources`). Uses **capture-phase** scroll listening to catch SPA inner-container scroll.
- **Query gotchas:** extract scroll with `JSONExtractInt(toString(payload),'scroll_pct')` — `payload.scroll_pct` subcolumn is NOT available. i3 dwell = gap-to-next-focus via `leadInFrame`, capped 30min. nav `url`=destination tab but `active_ms`/`scroll_*`=LEAVING tab.
- **STALE-`origin/main`-ref bit repeatedly:** after merging a PR via `gh`, the local `origin/main` ref is stale until `git fetch`. Always `git fetch origin main` before `git show origin/main:...` or building from it.
- **Tmux stale hooks:** `home-manager switch` does NOT reload a running tmux server's hooks → new hooks don't take until `tmux source-file` / fresh server. (Caught the `tmux` source emitting nothing.)
- **Decisions:** raw data is never destroyed in this fidelity dataset (capped contributions in the harness/dashboard, not mutated rows). Mail spam is TAGGED not rejected (a hard reject would drop Gmail's own forwarding-confirmation emails). i3 stores NO dwell (computed from ts-gaps downstream) to avoid the same inflation `duration_ms` had.

## How to verify
```bash
# Activity: all 6 sources flowing + harness green (workbench)
git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml > /tmp/s.yaml
RPW=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["reader-password"]' /tmp/s.yaml); rm -f /tmp/s.yaml
CH=http://192.168.50.94:30123
curl -s --user "activity_reader:$RPW" --data-binary "SELECT source,count(),max(ts) FROM activity.events WHERE ts>now()-1800 GROUP BY source ORDER BY source FORMAT PrettyCompact" "$CH/"
CLICKHOUSE_URL=$CH CLICKHOUSE_USER=activity_reader CLICKHOUSE_PASSWORD=$RPW python3 ~/workspace/devrc/scripts/validation/validate.py   # expect OVERALL: PASS
# Mail: classified inbox
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig
kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c "select category,count(*) from mail where via_gmail group by category;"
# Dashboard (the spot-check): https://grafana.homelab.lan → Activity & Productivity → host=laptop
```
