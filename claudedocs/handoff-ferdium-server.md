# Handoff: ferdium-server

## Goal
Deploy Ferdium Server (self-hosted multi-messenger backend) on the homelab Talos cluster, exposed at `ferdium.zacx.dev` behind Authelia, so the Ferdium desktop client can sync without a cloud account.

## State now
**WORKING END TO END.** The Ferdium desktop client logs in against the self-hosted server at
`ferdium.zacx.dev` and syncs. Confirmed by the operator 2026-09-03.

| Merged | What |
|---|---|
| devrc **#1240**, **#1241**, **#1245** | client packages; handoff updates |
| homelab-infra **#647** | manifests, both gateways, Authelia rule, relay firewall |
| homelab-infra **#651** | incident fix — dead promptver upstreams crashing the gateway |
| homelab-infra **#668** | registration closed + scoped Authelia bypass for the API |

**Open:** homelab-infra **#653** — gateway config `subPath` → directory mount. Unmerged on
purpose: it rolls the gateway pod on each cluster.

### Final verified state
- users = 1 (`zachlowden1@gmail.com`); `IS_REGISTRATION_ENABLED=false`, and signup returns
  `{"message":"Registration is disabled on this server","status":401}` — the flag gates the API,
  not just the dashboard.
- Login through the **public edge** returns HTTP 200 + token; a deliberately wrong password
  returns 401 `User credentials not valid`. Both measured — the wrong-password control is what
  makes the 200 mean anything.
- `GET /` still 302s to `login.zacx.dev`; `POST /v1/auth/signup` still 302s (excluded from the
  bypass by rule negation). 8117 remains dropped from the public internet.
- Vaultwarden holds the PLAINTEXT, which is correct: the client hashes it before sending.

## Architecture (researched, not yet implemented)

```
Ferdium client → Cloudflare → production nebula gateway (10.0.0.2)
  → homelab nebula gateway (10.42.0.10:8117) → Ferdium Server (ferdium.svc:3333)
```

Authelia runs on the production cluster. Cloudflare handles TLS termination. The homelab nebula gateway proxies traffic to internal services.

## Next steps (ranked)
10. Digest-pin `ferdium/ferdium-server:latest` — upstream publishes no version tag, so the
    running image can change under any reconcile or restart with no diff in git.
    forcing: none
11. Commit or discard `nix/programs/alacritty/default.nix`, uncommitted in `~/workspace/devrc`
    and baked into the workbench's built generation, so the two hosts are not identical despite
    reporting the same sha.
    forcing: regression
12. A gate asserting every `proxy_pass … .svc.cluster.local` in both gateway configs resolves to
    a Service defined in the repo. The promptver pair was found by checking all 37 by hand.
    forcing: none
13. Merge homelab-infra **#653** during a window where a brief mesh interruption per cluster is
    acceptable.
    forcing: incident — the 2026-09-02 gateway CrashLoopBackOff
14. Restart Brave on the `personal - other` profile — it runs a stale extension build
    (`b817ef1e88267a40` vs expected `66b98084daecd880`) and **cannot open tabs at all**, which
    silently degrades the `browser` skill on that profile.
    forcing: none

## Ferdium Server config (reference)

| Env var | Value |
|---|---|
| `NODE_ENV` | `production` |
| `APP_URL` | `https://ferdium.zacx.dev` |
| `DATA_DIR` | `/data` |
| `DB_CONNECTION` | `sqlite` |
| `DB_SQLITE_JOURNAL_MODE` | `WAL` |
| `DB_SQLITE_SYNCHRONOUS` | `FULL` |
| `IS_REGISTRATION_ENABLED` | `true` (enable first, then `false`) |
| `IS_DASHBOARD_ENABLED` | `true` |
| `JWT_USE_PEM` | `true` |
| `CONNECT_WITH_FRANZ` | `false` |

Port: 3333 (container) → 8117 (nebula gateway) → homelab Service port 80.

## Port allocation
| Port | Service | Status |
|---|---|---|
| 8117 | Ferdium Server | planned |

## Gotchas / decisions / dead-ends
- Ferdium client login prompt can be bypassed by self-hosting Ferdium Server — the client has a "custom server" option
- No official Helm chart for Ferdium Server — use raw manifests (same pattern as demo-site)
- SQLite is fine for personal use (single replica, RWO PVC)
- `noto-fonts-emoji` was renamed to `noto-fonts-color-emoji` in current nixpkgs — caught during home-manager switch
- The homelab cluster's Authelia access control is in the **production** cluster's Flux (not homelab) — it runs on the production cluster
- The `homelab-*` services in production cluster point to `10.0.0.2` (production gateway nebula IP), which routes to `10.42.0.10` (homelab gateway nebula IP)
- Authelia has an existing `cam` user addition in an uncommitted diff on homelab-talos trunk — unrelated to this work but same file

- 🔴 **A `home-manager switch` succeeding says NOTHING about whether the source was committed.** This effort produced a textbook instance: a ✅ in a handoff, a resolving binary, and an edit in no commit. Where a deploy step reads from a working tree, "it works here" and "it is in the commit" are separate claims — make both. Caught only because `git status` showed `M nix/graphical.nix` on a session that expected a clean tree.
- 🔴 **`noto-fonts-emoji` is `noto-fonts-color-emoji`** in current nixpkgs. The old name fails the switch with an attribute error, not a warning.
- 🔴 **Widening a nix list to `with pkgs;` extends that scope across the whole expression, `++ lib.optional (…)` included.** `nix-instantiate --parse` cannot see a shadowed `lib`; only an eval that checks the *tail* still resolves can. Assert the conditional entry (`deep-search` here), not just the packages you added.
- 🔴 **`~/workspace/devrc` carries unrelated dirty files** — `nix/programs/alacritty/default.nix` and `nix/system/apply-tmp-churn-retention.sh` — plus untracked `output.txt` and two `scripts/diagnose-*.sh`. None are this effort's. They are a latent `ship.sh` fast-forward blocker, which is the failure mode where a host silently stops receiving every future change while still looking healthy. Left as found deliberately; worth triaging before the next ship.
- The base clone had **never received this doc** (it lived only on `origin/main`), so `handoff_doc.py` would have resolved an absent base and refused with `stale-base`. Fast-forward the base clone before running `/handoff` against a doc a worktree authored.
- **Port 8117 was confirmed, not assumed.** Both gateway configs top out at 8116 in that band.
- The `cam` Authelia user sitting uncommitted on homelab-talos `trunk` is **unrelated** to this work but lives in the same file as rank 5. Branching off `origin/trunk` excludes it; a worktree off the dirty checkout would not.
- **Do not `git stash` in homelab-talos**, and do not copy an `.envrc` into a worktree of it — `.envrc` is *tracked* there, a documented exception to the usual worktree recipe.

- 🔴 **An 81xx port on the production relay is INTERNET-OPEN unless it is in the `diffsona` deny-list of `k0s/host-firewall/relay-firewall.sh`** — that list is the only filter on the node's public interface, and adding `listen 0.0.0.0:<port>` without the matching DROP publishes an unauthenticated path straight past Authelia. This is the measured `8102` failure the file documents: a SYN to `:8102` was answered **from the host** 71µs later. `scripts/check-relay-guard.py` gates it. 🔴 **That script is NOT Flux-reconciled** — merging the PR does not deliver it; copying it to the relay node is an owed human step.
- 🔴 **Ferdium Server's `/health` never touches the database.** It is a real route (upstream `start/routes/web.ts`, outside every middleware group) but `HealthController` returns a hardcoded `{api,db}:success` with an upstream `TODO`. **A Ready pod is not evidence that sync works.** Related: the fallback route `/*` redirects to `/`, so a typo'd probe path returns 302 and reads as success.
- **No WebSockets** — no `ws`/`socket.io` dependency and no upgrade handler in `server.ts`; client sync is plain REST on `/v1/...`. So neither nginx block carries `Upgrade` headers, deliberately.
- **`APP_KEY` is deliberately unset.** Setting it on a fresh volume takes the entrypoint's `else` branch, which `cat`s a nonexistent file, and the env-schema check then rejects the empty key.
- 🔴 **The biggest UNVERIFIED risk: whether the Ferdium desktop client's background sync survives an Authelia forward-auth gate.** The client holds its own bearer token; as an Electron app its top-level navigation can follow the 302 and log in, but whether background requests then carry the `zacx.dev` session cookie was never tested. **If sync fails with 302s to login.zacx.dev while a browser works fine, that is this gap** — and the fix is a scoped bypass for the client's API prefix, NOT deleting the Authelia rule.
- **`ferdium/ferdium-server:latest` is a mutable tag** — upstream publishes no version tag. A digest pin is an owed follow-up.
- **`scripts-tests` was already red on `origin/trunk`** before any of this work: a pristine baseline worktree produced byte-identical verdict lines (`files_run=54 tests_ran=1316`, same failed/broken sets). Do not attribute it to the ferdium diff.

- 🔴 **FERDIUM PASSWORDS: EVERY REAL CONSUMER SENDS `sha256_base64(password)`, NOT THE
  PLAINTEXT — so the account must be registered with the HASH.** `ferdium-app` builds
  `Authorization: Basic base64(email + ":" + sha256_b64(password))`, and the server's own web
  dashboard controller hashes what the form submits. The ONLY path that takes a raw plaintext is
  a hand-rolled Basic-auth call to `v1/auth/login`. Register via the API with
  `printf %s '<plaintext>' | openssl dgst -sha256 -binary | base64` as the `password` field, and
  keep the PLAINTEXT in the password manager — the client hashes it for you. Corroborated three
  ways: the client's `password-helpers` (`sha256` → base64, no salt), the server's Franz-import
  handler using the identical expression, and empirically — submitting the hash into the web form
  fails (double-hashed) while the client and the dashboard both accept the plaintext.
- 🔴 **THE VERIFICATION TRAP THAT COST THIS EFFORT A DAY: I VERIFIED THE ONE PATH NOBODY USES.**
  A raw Basic-auth `curl` to the login API returned 200 + a token, and that was reported as "the
  credential is confirmed". It confirmed only that the plaintext matched the stored hash on the
  single code path that does no client-side hashing — the path no human or client ever takes.
  Both real consumers were broken the whole time and the green check could not see it. **Ask
  which path the USER takes, and exercise THAT one.** A credential check must run through the
  actual client, or through the same transformation the client applies.
- 🔴 **There is NO password-change API.** `PUT /v1/me` merges `request.all()` into a settings
  JSON column and never touches auth. There is a web account page (`POST /user/account`), but it
  is useless when the stored credential is wrong in a way that blocks the dashboard too. The only
  reliable repair is: delete the user row, briefly re-enable registration, re-register with the
  correct value, and let Flux restore the flag to `false` on its next reconcile — which it does
  by itself, so no second PR is needed.
- **There is no web signup form.** `user/signup` 302s to `user/login`; `signup` 302s to `/`. The
  fallback route `/*` redirects to `/`, so a typo'd probe path returns a 302 that reads as
  success — check the redirect target, not just the status.
- **The Ferdium `/health` endpoint returns a hardcoded `{"api":"success","db":"success"}`** with
  an upstream `TODO` and never queries the database. A Ready pod is not evidence sync works.
- **Ferdium's first run clones a recipes repo**, so readiness legitimately takes ~2 min after the
  image pull. A `connection refused` readiness probe in that window is expected.
- **Stored password format is Adonis PHC scrypt** (`$scrypt$n=16384,r=8,p=…`), not bcrypt — do
  not hand-craft a replacement hash into SQLite.

## How to verify
```bash
# 1. The account, and that registration is shut (the flag gates the API, not just the UI):
KUBECONFIG=$KC_HOMELAB kubectl -n ferdium exec deploy/ferdium -- \
  sh -c 'sqlite3 /data/ferdium.sqlite "select id,email from users;"'
curl -sS -X POST -H 'Content-Type: application/json' \
  -d '{"firstname":"a","lastname":"b","email":"probe@example.invalid","password":"abcdefgh12"}' \
  http://10.42.0.10:8117/v1/auth/signup      # expect "Registration is disabled on this server"

# 2. 🔴 Login through the PUBLIC EDGE, with the wrong-password control. Read BOTH lines —
#    a 200 alone proves nothing, and the password here is the sha256_b64 the CLIENT sends:
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  -u "zachlowden1@gmail.com:$(printf %s '<plaintext>' | openssl dgst -sha256 -binary | base64)" \
  https://ferdium.zacx.dev/v1/auth/login     # expect 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  -u 'zachlowden1@gmail.com:WRONG' https://ferdium.zacx.dev/v1/auth/login   # expect 401

# 3. The edge is still gated where it should be:
curl -sSI https://ferdium.zacx.dev/ | head -1                       # 302 -> login.zacx.dev
curl -sS -o /dev/null -w '%{http_code}\n' -X POST -d '{}' \
  -H 'Content-Type: application/json' https://ferdium.zacx.dev/v1/auth/signup   # 303 (gated)

# 4. 8117 must stay dropped from the internet — read all three, one proves nothing.
#    🔴 The relay's public IP is DERIVED here, never written down: this repo is
#    PUBLIC and scripts/tests/test_no_public_ips.py fails the suite on a literal.
#    Do not "simplify" this back to a hardcoded address — it caught exactly that.
RELAY=$(awk '/ssh:/{f=1} f&&/address:/{print $2; exit}' "$HOMELAB/k0s/diffsona.yaml")
nix-shell -p netcat-openbsd --run "for p in 8117 8114 8118; do nc -vz -w 6 $RELAY \$p; done"
#   8117 timeout · 8114 timeout (guarded control) · 8118 refused (unguarded control)

# 5. The only thing that proves the GOAL: the desktop client logs in with the PLAINTEXT and syncs.
```
## Open investigations — live diagnosis state
### (CLOSED) Does the desktop client survive the Authelia forward-auth gate?
- **Resolved 2026-09-03.** It did not, and could not: Authelia intercepted the whole `/v1/` API
  (`POST /v1/auth/signup` → 303, `GET /v1/auth/login` → 302, both to `login.zacx.dev`). Fixed by
  the scoped bypass in #668, which excludes signup. Verified working through the public edge.
