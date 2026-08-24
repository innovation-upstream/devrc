---
name: ux-audit-loops
description: "Run the re-runnable naida + vetr UX-audit harnesses — walk the app's funnel, screenshot every view, capture console/network/axe findings, then re-run to verify. Local-only. Use for: UX-audit or QA-sweep naida or vetr, \"make ux-audit\", the funnel review, the hermetic vetr e2e stack. Hosted crawler -> `auditloop`."
---

# UX-audit loops (naida + vetr)

Re-runnable loops that automate the manual "click through → write notes → hand to Claude →
implement → re-run". Cross-session detail: memory `qa-ux-audit-harness` (read first).

**The loop:** `make ux-audit` (free walk → screenshots + deterministic findings) →
`make ux-audit-draft` (opt-in, paid: vision LLM fills the per-view `**UX notes:**` scaffold)
→ review/edit `findings.md` → hand to an implementing Claude → re-run to verify + re-audit.

## naida
| | |
|---|---|
| Repos | laptop `~/workspace/scratch/naida-ai` · workbench clone `~/workspace/naida-ai` (repo `ZacxDev/naida-ai`) |
| Run | `make ux-audit` / `make ux-audit-draft` / `make ux-audit DRAFT=1` (or `cd tests/e2e && npm run ux-audit[-draft]`) |
| Target | LOCAL `DEV_MODE=true` Go server, **all paid keys stripped** (`env -u OPENROUTER_API_KEY` Cloudflare/Deepgram/Unipile) → auth bypassed, no AI/email/LinkedIn side effects. **NEVER** `naida-ai-demo.zacx.dev` (live AI+email+LinkedIn + shared org). |
| Views | 10 (sales-audit flow: list→archived→questionnaire→create→detail/upload/post-upload/invite→review→/q) |
| Auth | DEV_MODE bypass — no test user |

## vetr
| | |
|---|---|
| Repos | workbench `~/workspace/vetr-app` + `~/workspace/vetr-api` (SIBLINGS — the e2e workflow uses `../vetr-api`) · laptop workspace `~/workspace/scratch/vetr` (`vetrllc/vetr-workspace`, docs/plans). Code: `vetrllc/vetr-app` (React Router v7 SPA), `vetr-api` (Laravel) |
| Run | `make ux-audit` (brings up the hermetic stack → walk → teardown). `KEEP_UP=1` leaves it up. `PAYMENT_GATEWAY=stripe make ux-audit` for the legacy path (default is **authnet**, matching prod). |
| Target | the team's **hermetic E2E stack** on NixOS via `<vetr-app>/scripts/e2e-hermetic-nixos.sh`: docker `mysql:8.4` → vetr-api (nix php84+exts, composer, APP_ENV=e2e, MAIL=array, `migrate:fresh --seed=E2ESeeder`, `e2e:mint-tokens`, `artisan serve --no-reload :8000`) → vetr-app SPA built+served :5174 → Playwright (nix chromium). **NEVER** app.vetr.com / api.vetr.com (prod = live Authorize.net, real charges). |
| Views | 14 (welcome→choose-experience→signup→6-step vet-registration wizard→dashboards→appointments→booking funnel→checkout-UI, stops before payment) |
| Auth | Sanctum bearer token (from `e2e:mint-tokens`) seeded into `localStorage["CapacitorStorage.token"]` |
| Payments | prod runs `PAYMENT_GATEWAY=authnet` (Stripe→Authorize.net migration, env-flag). Harness defaults authnet, **forces `ANET_ENDPOINT=sandbox`**. Sandbox creds in `~/.config/vetr/authnet.env` (ANET_LOGIN_ID/TRANSACTION_KEY suffice — add-card is server-token HOSTED CIM, not inline Accept.js, so PUBLIC_CLIENT_KEY/SIGNATURE_KEY not needed). |

## The draft pass (both apps)
Opt-in, PAID, runs the vision LLM FROM the harness (never through the app), reads
`OPENROUTER_API_KEY` only here. Default model `anthropic/claude-haiku-4.5` (10/10 reliable,
cheap; `UX_DRAFT_MODEL=anthropic/claude-sonnet-4.6` for sharper). Downscales screenshots
≤1568px (pngjs) before sending; `max_tokens` 2048; per-view failures non-fatal. No key →
exits cleanly, leaves the blank scaffold.

## Findings are origin-classified (not a denylist)
`findings.md` leads with **first-party** console/network counts (the app's own origin = real
signal); cross-origin third-party (Faro/GA/Stripe.js beacons that fail in the hermetic env)
are bucketed separately as "environmental — not app bugs", never dropped. vetr also disables
Faro at build (`VITE_FARO_ENABLED=false`). Origin-based via `new URL().origin`, survives new SDKs.

## NixOS / run gotchas
- **`nix develop`** in each repo provides the toolchain (go/php/node/make/chromium); `flake.nix` + `.envrc` (direnv). If `make` is "command not found", you're not in the shell (or use `cd tests/e2e && npm run ...`).
- Chromium: the ux-audit config resolves it host-agnostically (`PLAYWRIGHT_CHROMIUM` → `chromium`/`chrome`/`brave` on PATH → bundle). Do NOT `npx playwright install` (NixOS linker); `npx` is broken → `./node_modules/.bin/...`.
- vetr bring-up does `unset CDPATH` (a caller's CDPATH corrupted `$(cd&&pwd)` dir captures — PR #71).
- Run dirs `tests/e2e/ux-audit-runs/<ts>/` are gitignored.

## ⚠ Known live bug this surfaced (vetr)
Under authnet, the pooled-consult + servicer-booking **pay-now checkout is broken**:
`appointment-checkout.tsx`/`booking-checkout.tsx` render `<StripeCheckout>` UNCONDITIONALLY
(consumes a Stripe `client_secret`) but authnet returns `client_secret:null` → empty form,
can't pay; some no-card primitives 500. Full report: `vetrllc/vetr-workspace`
`payment-rails-paynow-checkout-bug.md` (merged). Fix = make checkout FE gateway-aware
(mirror `gateway-add-card-form.tsx`) + authnet charge primitives. When the fix lands, add a
regression-guard assertion that the checkout renders a usable form under authnet.

## operator notes
- The real validation is whether `findings.md` is something you'd hand to Claude — run it and judge; tune the walk/scaffold if the shape's off.
- Laptop draft pass needs a **fresh** OpenRouter key (env). Laptop `~/.config/vetr/authnet.env` still holds PROD creds → replace with sandbox before running vetr there (harness forces sandbox endpoint, so prod bounces E00007).
- Verifier for the whole initiative: re-run `activity-scan --days 7` in a week — manual vetr/naida QA browser-time should drop if these loops are being used.
