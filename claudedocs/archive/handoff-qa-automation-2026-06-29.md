# Handoff — QA-audit automation + activity-telemetry correction — 2026-06-29

## What this session was
Started from "use the activity telemetry to find automation opportunities," which led to: (1) **correcting** the activity pipeline (a 20× browser-data bug), (2) building **deterministic automation** off the telemetry, and (3) the big thread — building **re-runnable UX-audit loops for naida + vetr** to replace manual in-browser QA, which **caught a live revenue bug in vetr**.

Operate via skills (all updated this session): `activity`, **`ux-audit-loops`** (NEW). Cross-session detail: memories `activity-telemetry-pipeline`, **`qa-ux-audit-harness`**.

## State now (all merged unless noted)
**Activity telemetry — corrected + extended:**
- Browser `active_ms` was ~20× inflated (structurally broken on i3: `chrome.idle` is system-wide + blur unreliable → counted other-app time as browser-active). **RETIRED** it; attention is now **i3-derived** (i3 "Brave-focused" intervals ∩ active-tab domain). devrc #25 (mutex race fix), #26 (harness: retire active_ms invariants, add `derived_attention_consistent`), #27 (strip active_ms/focus/idle from the ext → nav+scroll only, manifest 1.4.0), #28 (duration garbage bound 24h→7d). homelab-infra #79 (dashboard panel → "Browser attention by domain (i3-derived)"). Harness `OVERALL: PASS`. Ext re-deployed on laptop + verified (focus/active_ms events at 0).
- New tooling: `scripts/session-analysis/activity-scan.py` (weekly automation/bottleneck/signal report) + `scripts/dogfood-cycle` (collapses the manual civitai dogfood loop). devrc #29/#30.

**naida UX-audit loop — DONE.** `ZacxDev/naida-ai` #38/#39/#40/#41/#42. `make ux-audit` (walk) / `make ux-audit-draft` (Haiku-drafted notes). Local DEV_MODE, keys-stripped. Nix flake. Laptop copy: `~/workspace/scratch/naida-ai`.

**vetr UX-audit loop — DONE + already paid off.** `vetrllc/vetr-app` #67 (hermetic stack on NixOS) / #68 (the loop, 14 funnel views) / #69 (authnet default) / #70 (telemetry off + origin-classified findings) / #71 (CDPATH fix). Runs the team's hermetic E2E stack (docker MySQL + Laravel + SPA + Playwright). **Authnet sandbox validated** (creds in workbench `~/.config/vetr/authnet.env`).
- **🔴 Found a LIVE revenue bug** (the payoff): under Authorize.net (prod), pooled-consult + servicer-booking **pay-now checkout is broken** — renders Stripe `<PaymentElement>` with a null `client_secret` → empty form, can't pay. Documented: `vetrllc/vetr-workspace` `payment-rails-paynow-checkout-bug.md` (#1, merged). **Another agent is handling the fix** — do not touch it; when it lands, add a regression-guard assertion to the vetr loop.

## Next steps (ranked)
1. **THE ORIGINAL UNTOUCHED THREAD — email automation on the live `mail` table** (skill `mailbox`, memory `selfhosted-mail-inbox`). This was the higher-leverage "heavy automation" goal from session start (triage / task+receipt extraction / digests) and is STILL not started — we went deep on activity+QA instead. Strong candidate for next focus.
2. **Operator: laptop housekeeping for the loops** — replace `~/workspace/scratch/vetr`-side `~/.config/vetr/authnet.env` PROD creds with the SANDBOX ones (harness forces sandbox endpoint → prod bounces E00007); use a FRESH OpenRouter key for draft passes (the one pasted this session is in transcript — rotate it).
3. **Verifier (run in ~a week):** `activity-scan --days 7` — if the manual vetr/naida QA browser-time + the civitai/dogfood command counts DROP, these automations are real leverage; if not, cut them.
4. **Lower / standing:** revive the dead vetr `e2e (hermetic)` CI (expired `VETR_API_REPO_TOKEN`, failing since ~2026-06-12); fix the vetr X-Timezone availability-display prod bug (its regression spec is red); the activity "weekly LLM digest" cap (only if it'd drive a decision — else don't reflexively build more).

## Gotchas / decisions
- **active_ms is RETIRED — do not reintroduce it.** Browser attention = i3∩domain (query in the `activity` skill). The URL is the `text` column, NOT `payload.url`.
- **Both prod payment targets are UNSAFE to automate:** vetr `app.vetr.com` (live Authorize.net) + naida `naida-ai-demo.zacx.dev` (live AI/email/LinkedIn + shared org). Loops are LOCAL-ONLY; vetr forces `ANET_ENDPOINT=sandbox`.
- vetr migrated Stripe→Authorize.net via env flag (code default still `stripe` for rollback; prod runs `authnet`). The harness defaults authnet to match prod.
- Memory-is-a-hypothesis bit twice this session: the "8/8 GREEN" handoff claim (harness was actually red), and "sandbox creds exist" (the file held PROD creds). Re-verify live before acting.
- Skills updated: `activity` (active_ms retirement, i3-derived query, harness invariants, activity-scan); `ux-audit-loops` (NEW). devrc `CLAUDE.md` activity line corrected (6 sources, restart-gotcha fixed) — UNCOMMITTED, part of the working-tree drift.

## How to verify
```bash
# Activity harness green + sources flowing (workbench)
RPW=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["reader-password"]' <(git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml))
CLICKHOUSE_URL=http://192.168.50.94:30123 CLICKHOUSE_USER=activity_reader CLICKHOUSE_PASSWORD=$RPW python3 ~/workspace/devrc/scripts/validation/validate.py   # expect OVERALL: PASS
# naida loop:  cd ~/workspace/scratch/naida-ai && nix develop -c make ux-audit
# vetr loop:   cd ~/workspace/vetr-app && make ux-audit   (authnet default; sandbox creds in ~/.config/vetr/authnet.env)
# activity-scan: CLICKHOUSE_URL/USER/PASSWORD env → python3 ~/workspace/devrc/scripts/session-analysis/activity-scan.py --days 7
```
