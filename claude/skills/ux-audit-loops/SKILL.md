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
| Repos | the two code clones must be **SIBLINGS** (the e2e workflow uses `../vetr-api`). **The path is per-host — resolve it, don't assume it**: on the laptop both sit INSIDE the workspace checkout at `~/workspace/scratch/vetr/{vetr-app,vetr-api}` (verified 2026-08-25; bare `~/workspace/vetr-api` does NOT exist there, and `~/workspace/vetr-app` is an unrelated stray dir, not a git repo). `~/workspace/scratch/vetr` itself is `vetrllc/vetr-workspace` (docs/plans) AND the parent of both clones. Confirm with `git -C <dir> remote -v`. Code: `vetrllc/vetr-app` (React Router v7 SPA), `vetr-api` (Laravel) |
| Run | `make ux-audit` (brings up the hermetic stack → walk → teardown). `KEEP_UP=1` leaves it up. `PAYMENT_GATEWAY=stripe make ux-audit` for the legacy path (default is **authnet**, matching prod). |
| Target | the team's **hermetic E2E stack** on NixOS via `<vetr-app>/scripts/e2e-hermetic-nixos.sh`: docker `mysql:8.4` → vetr-api (nix php84+exts, composer, APP_ENV=e2e, MAIL=array, `migrate:fresh --seed=E2ESeeder`, `e2e:mint-tokens`, `artisan serve --no-reload :8000`) → vetr-app SPA built+served :5174 → Playwright (nix chromium). **NEVER** app.vetr.com / api.vetr.com (prod = live Authorize.net, real charges). |
| Views | **15** `captureView` calls in `tests/e2e/ux-audit/vetr-funnel.audit.ts` (counted 2026-08-25): welcome→choose-experience→customer-signup→customer-signup-success→vet-wizard-1..6→vet-dashboard→customer-dashboard→customer-appointments→book-consult→checkout (stops before payment) |
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
- **`nix develop` — only TWO repos have a flake, and `vetr-api` is NOT one of them** (verified 2026-08-25). `naida-ai` and `vetr-app` each ship `flake.nix` + `.envrc` (direnv); **`vetr-api` has NEITHER — there is no shell to enter there.** That is deliberate, not a gap: `vetr-app/flake.nix` provisions the PHP side for its sibling — `php84.withExtensions [redis intl gd exif]` (`:18`) and `php.packages.composer` (`:25`, commented "for vetr-api in the hermetic stack"), plus `nodejs_22`/`gnumake`/`chromium`/`jq`. **So run the vetr loop from `vetr-app`'s shell, never from `vetr-api`.** If `make` is "command not found", you're not in the shell (or use `cd tests/e2e && npm run ...`). `docker` stays AMBIENT (the flake deliberately does not provide it).
- Chromium: the ux-audit config resolves it host-agnostically (`PLAYWRIGHT_CHROMIUM` → `chromium`/`chrome`/`brave` on PATH → bundle). Do NOT `npx playwright install` (NixOS linker); `npx` is broken → `./node_modules/.bin/...`.
- vetr bring-up does `unset CDPATH` (a caller's CDPATH corrupted `$(cd&&pwd)` dir captures — PR #71).
- Run dirs `tests/e2e/ux-audit-runs/<ts>/` are gitignored.

## What this loop caught (vetr) — FIXED, kept as the payoff record
The vetr walk's first run under authnet surfaced a LIVE revenue bug: `appointment-checkout.tsx`
/ `booking-checkout.tsx` rendered `<StripeCheckout>` UNCONDITIONALLY, but authnet returns
`client_secret:null` → empty form, can't pay. Report: `vetrllc/vetr-workspace`
`payment-rails-paynow-checkout-bug.md`.

**The fix has SHIPPED — do not re-fix it** (verified 2026-08-25). Both files now branch on
`useGateway()` (`appointment-checkout.tsx:27,92,124`; `booking-checkout.tsx:22,41,68`) and
render `<StripeCheckout>` only when `gateway === "stripe"`, with an authnet branch beside it.
Three regression specs guard it — `e2e/authnet-{checkout-render,card-save,servicer-booking}.spec.ts`,
**one `test()` each** (3 cases, not 4). They are **self-skipping**: each needs
`E2E_OWNER_HAS_SAVED_CARD=true`, `PAYMENT_GATEWAY=authnet` read live from `/config`, and
(for checkout-render) `E2E_BOOKING_VET_ID`+`E2E_OWNER_PET_ID` — **without those env vars they
skip, so a green run is NOT evidence the guard executed.** Confirm they RAN before quoting them.

## operator notes
- 🔴 **Two different bars — don't conflate them.** The loop's **automatic verifier is MECHANICAL, and it is not `findings.md`**: (a) the walk's `guard()` helper records the error, writes the partial markdown and **RE-THROWS**, so a missing/broken route **fails the run** (`vetr-funnel.audit.ts:50-64`); (b) the run asserts `≥1` screenshot captured (`:422`); (c) the `authnet-*` specs above are pass/fail assertions. That is what `close-the-loop`'s ledger should score. Whether `findings.md` reads well enough to hand to Claude is **quality tuning by human judgement** — useful, but per `close-the-loop` §"Cheap *automatic* verifier" a human judging open-ended LLM output is explicitly **not** a cheap verifier, so it must never be cited as the thing that closes this loop.
- Laptop draft pass needs a **fresh** OpenRouter key (env). Laptop `~/.config/vetr/authnet.env` still holds PROD creds → replace with sandbox before running vetr there (harness forces sandbox endpoint, so prod bounces E00007).
- Verifier for the whole initiative: re-run `activity-scan --days 7` in a week — manual vetr/naida QA browser-time should drop if these loops are being used.
