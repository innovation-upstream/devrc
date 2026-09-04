# Handoff: ferdium-server

## Goal
Deploy Ferdium Server (self-hosted multi-messenger backend) on the homelab Talos cluster, exposed at `ferdium.zacx.dev` behind Authelia, so the Ferdium desktop client can sync without a cloud account.

## State now
**Ferdium itself: DONE, working, unchanged.** The desktop client logs in against the
self-hosted server and syncs; operator-confirmed 2026-09-03. Nothing about Ferdium proper
is in flight.

| Merged | What |
|---|---|
| devrc **#1240**, **#1241**, **#1266** (`f972992e`), **#1278** (`a7dac5bd`) | client packages; handoff |
| homelab-infra **#647** | manifests, both gateways, Authelia rule, relay firewall |
| homelab-infra **#651** | incident fix — dead promptver upstreams crashing the gateway |
| homelab-infra **#668** | registration closed + scoped Authelia bypass for the API |

(devrc **#1245** is NOT in that list — it is an obsolete duplicate of this doc, still OPEN
and CONFLICTING. See rank 15. The earlier version of this table listed it as merged; it
never was.)

Carried forward — measured previously, still the state of the deployment:
- users = 1 (`zachlowden1@gmail.com`); `IS_REGISTRATION_ENABLED=false` and signup returns
  `{"message":"Registration is disabled on this server","status":401}`.
- Login through the public edge: 200 + token; wrong-password control: 401. Both measured.
- `GET /` still 302s to Authelia; `POST /v1/auth/signup` still 302s (excluded from the bypass).
  8117 still dropped from the internet.
- Vaultwarden holds the PLAINTEXT — correct, because the client hashes before sending.

**The gateway config-staleness follow-on moved.** homelab-infra **#653** (subPath →
directory mount on both gateways) is **superseded, not merged**. Replaced by
homelab-infra **#678** (`fix/prod-gateway-configmap-generator`), OPEN / MERGEABLE /
CLEAN, commit `242c85b9c`, **not merged — needs a scheduled window**.

Why it changed: `417a6386b` landed on trunk **2026-09-03 18:40 CDT**, a day after #653
was opened and ~3h before this session. The prior handoff had no knowledge of it. It:
- added `resolver 10.96.0.10 valid=30s ipv6=off` to the homelab nginx and converted all
  39 name-targeted `proxy_pass` to `set $upstream` → **request-time resolution**. That is
  the exact hazard #653 cited as its forcing reason, and it is gone.
- made homelab `nginx.conf` a `configMapGenerator` input → hash-suffixed ConfigMap name →
  **any edit now rolls the gateway by itself**. A stronger fix for "ConfigMap updates do
  not reach the container" than a directory mount.
- removed the dead promptver relays from the production side.
- explicitly listed the production ConfigMap as **owed**. #678 is that owed half.

**#678 verification (measured, both clusters read live):**
- extracted `gateway/nginx.conf` is **byte-identical** (`sha256 970d4bbe…`) to the config
  the production gateway is serving right now, read out of the running container.
- production's running config, its live ConfigMap and `origin/trunk` were **all three
  identical** beforehand → no pending config edit rides along with the roll.
- `kubectl kustomize` base vs branch differ by **exactly 2 lines**: ConfigMap name and
  DaemonSet volume ref, both gaining `-f27hbb48cg`. Name-reference transformer rewired
  the mount correctly.
- `scripts/kustomize-validate.sh clusters/production/apps/nebula` on the branch **and** on
  a pristine `origin/trunk` worktree: **byte-identical output**, PASS both, 148 res / 119
  valid / 0 invalid.
- **negative control**: a `configMapGenerator` pointing at a missing file makes that same
  command exit 1 naming the absent path — the gate can go red on this defect shape.
- verified from the **commit**, not just the working tree.

**Live gateway state as of 2026-09-04T02:40Z:**
- homelab: resolver live (2 occurrences in the running config), **0** static `.svc`
  `proxy_pass`, `nginx -t` passes, pod references hashed CM `…-97b4gbm584`. All **36**
  non-comment `.svc` upstreams resolve to existing Services (positive control: a bogus
  name correctly reported missing).
- production: plain ConfigMap, `subPath: nginx.conf`, **42** `proxy_pass` directives, every
  one an IP literal on `10.42.0.10:<port>` — **zero** hostname upstreams, so it cannot arm
  a startup-resolution failure at all.
- both gateway pods rolled ~170m ago, after `417a6386b` reconciled.

**Clawgate**: still no task. `clawgate_handoff.sh resolve` exited **5** (0 tasks for this
session) with its positive control confirming the board is reachable — a real reading, but
it cannot distinguish "touched no task" from "wrong session id". No field written.

**Deploy status**: nothing deployed this session. #678 is staged only; no cluster touched.

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
    reporting the same sha. Tree is still dirty: `flake.lock`, that file,
    `nix/system/apply-tmp-churn-retention.sh`, plus untracked `output.txt`,
    `nix/system/check-nebula-relays.sh` and two `scripts/diagnose-*.sh`.
    forcing: regression
12. A gate asserting every `proxy_pass … .svc.cluster.local` in both gateway configs resolves
    to a Service defined in the repo. **Narrower than it was**: homelab now resolves at
    request time so a missing Service degrades one route instead of refusing to boot, and
    production has no hostname upstreams at all. Still worth having as a correctness check;
    no longer an outage guard.
    forcing: none
13. **Merge homelab-infra #678** during a window where one brief production mesh interruption
    is acceptable, then **close homelab-infra #653 as superseded**. Only the production
    gateway rolls; nothing in #678 touches homelab. Leaving #653 open is the live risk — a
    merge of it would roll BOTH clusters for a change that is redundant on one and
    incomplete on the other. IN FLIGHT: ZacxDev/homelab-infra#678
    forcing: incident — the 2026-09-02 gateway CrashLoopBackOff
14. Restart Brave on the `personal - other` profile — it runs a stale extension build
    (`b817ef1e88267a40` vs expected `66b98084daecd880`) and **cannot open tabs at all**, which
    silently degrades the `browser` skill on that profile.
    forcing: none
15. Close devrc **#1245** — obsolete duplicate of this handoff (branch
    `docs/handoff-ferdium-shipped`, opened 2026-09-02, CONFLICTING), superseded by #1266 and
    #1278 which already merged the richer 207-line doc.
    forcing: none
16. `relay-firewall.sh` is NOT Flux-reconciled, so `417a6386b`'s PORTS change reaches diffsona
    only via `k0sctl apply` or a hand copy plus unit restart. Owed by that commit, not by this
    session's work.
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

- 🔴 **A PUBLIC IP LITERAL IN A HANDOFF DOC IS THE EASIEST WAY TO LEAK ONE, AND THE GATE IS THE
  ONLY THING THAT CATCHES IT.** `tekton/devrc-pytests` failed #1266 on
  `test_no_unallowlisted_public_ip_literal_is_committed`: the verify section had the production
  relay's routable IP hardcoded, in a PUBLIC repo. **A docs-only change is exactly where this
  slips through** — there is no code review reflex on prose, and the line looked like helpful
  precision. Derive it instead and say why in a comment, or the next person "simplifies" it
  back: `RELAY=$(awk '/ssh:/{f=1} f&&/address:/{print $2; exit}' "$HOMELAB/k0s/diffsona.yaml")`.
  🔴 And verify the derivation is not merely quiet — a substitution that yields an empty string
  makes the whole check vacuous while still exiting 0.
- 🔴 **A RED CHECK ON A FILE YOUR DIFF NEVER TOUCHED MEANS "MEASURE THE BASE", NOT "DEBUG YOUR
  CHANGE" — this happened TWICE in one day.** #647's `scripts-tests` red was #646 landing on the
  commit after its base; #1266's `test_recommend_terms_resolve_on_the_live_config` red was a
  guard main had already RETIRED (`68d10b19`, "they re-break on every snippet edit") that the
  branch was still carrying, 11 commits behind. Both fixed by `git rebase origin/trunk|main`
  with ZERO code change. **The control that identifies it in one command:** run the failing test
  on a pristine worktree of the base ref. Here pristine main selected 3 tests and passed while
  the branch selected 4 and failed one — the differing COLLECTION COUNT was the tell, and a
  same-count pass/fail comparison would have missed it entirely.
- 🔴 **`gh pr checks` WILL HAND YOU A VERDICT ABOUT A DIFFERENT COMMIT.** After force-pushing a
  rebase, it returned the PRE-rebase `fail` — for a test that could no longer even be collected.
  The tell was that the numbers were byte-identical to the previous run
  (`collected=21008 … failed=4`); a genuinely new run essentially never reproduces those exactly.
  **Bind the read to the sha**:
  `gh api /repos/<o>/<r>/commits/$(gh pr view <n> --json headRefOid --jq .headRefOid)/status`.
  Same class as reading a verdict without checking what it is a verdict ABOUT — which also cost
  a `⚠ WARN` this session, missed because the grep pattern did not match the warning's format.
  **Read the whole tool output before quoting any part of it as clean.**
- ⚠ **The devrc base clone is a SHARED checkout and it moves under you.** At this session's end
  it sat on `feat/mention-system-repos` with a modified `flake.lock`, switched by another
  session — not by anything here. Check `git branch --show-current` before any write to it, and
  prefer a worktree off `origin/main`.

- 🔴 **`resume-state.sh` MISATTRIBUTES a cross-repo PR written as `<repo> **#N**`.** This
  handoff writes `homelab-infra **#653**`; the reconciler resolved all five homelab-infra
  numbers (#646/#647/#651/#653/#668) against **devrc** and emitted two confident DRIFT lines
  that were both false — `PR #653 MERGED but handoff frames it as open` (devrc #653 is an
  unrelated conflict-marker fix) and `PR #651 CLOSED without merge`. Real state: homelab-infra
  #653 OPEN, #651 MERGED. The qualifier regex expects `owner/repo#N` and markdown bold between
  the repo name and the number defeats it; it should have printed `UNATTRIBUTED` rather than
  resolving locally. **Write `ZacxDev/homelab-infra#653` in handoffs** until that is fixed —
  and do not trust a bare `PR #N` line in the digest for cross-repo work.
- 🔴 **A superseding commit can land between a PR being opened and being read, and the PR's
  own risk analysis then reads as current forever.** #653's body was accurate, careful, and
  wrong by the time anyone acted on it — `417a6386b` merged 26h after it was opened and
  inverted its premise. `gh pr view --json mergeable` said CLEAN throughout, because the base
  moved on files whose *text* did not collide. **Before acting on any PR older than a day,
  `git log <base> -- <the files it touches>` since the PR's `createdAt`.**
- 🔴 **`grep -c <pattern>` counts LINES CONTAINING the string, not directive INSTANCES.** Cost
  a wrong number in a comment: 46 lines match `proxy_pass` in the production nginx config but
  **4 are `proxy_pass_request_headers`**, a different directive — the real count is 42. The
  tell was that a "total 46 / IP-literal 42 / non-IP 0" triple does not add up; a count that
  is internally inconsistent is the signal, and the fix is to enumerate and read.
- 🔴 **`git add <staged-deletion-path>` fails the WHOLE `add` invocation.** Passing an
  already-`git rm`'d path alongside two real ones aborted on `pathspec did not match any
  files` and staged **neither** of the others; the commit then captured only the deletion —
  a commit that removed a ConfigMap without adding its replacement. `git status -s` right
  before the commit is what caught it. Stage paths in separate `add` calls, or check status
  between staging and committing.
- **`origin/trunk` moved under this session mid-run** (`7ec3fef4c` → `5fdd770fa`, another
  session pushing). Branched off the newer one and confirmed the two target files were
  unchanged across the gap before proceeding.
- **The homelab-infra worktree needs its toolchain supplied explicitly.** `kustomize-validate.sh`
  failed twice for instrument reasons before producing a verdict — first `kustomize: command
  not found`, then the script's own temp `strip-sops.py` failing on `ModuleNotFoundError: yaml`.
  Working invocation:
  `nix-shell -p kustomize kubeconform "python3.withPackages(ps: [ps.pyyaml])" --run "bash scripts/kustomize-validate.sh <root>"`.
  Its root argument is an exact path from `--list-roots`; a trailing glob matches nothing.
- **`$KC_NEBULA` does not exist — the production cluster handle is `$KC_PROD`**
  (`~/workspace/homelab-talos/production-kubeconfig`). CLAUDE.md's handle list names
  `KC_NEBULA`, which is stale; `env | grep ^KC_` is the arbiter.

## How to verify
```bash
# ── Ferdium (unchanged; see the account/edge/firewall checks already recorded above) ──

# ── homelab-infra #678, BEFORE merging ──
# 1. The generated ConfigMap must carry the SAME bytes production is serving now.
#    If these differ, a config edit is riding along with the roll — stop and read it.
KUBECONFIG=$KC_PROD kubectl -n nebula exec nebula-gateway-6hjw7 -c nginx \
  -- cat /etc/nginx/nginx.conf | sha256sum          # expect 970d4bbe…
git -C /home/zach/workspace/homelab-cmgen show \
  HEAD:clusters/production/apps/nebula/gateway/nginx.conf | sha256sum   # must match

# 2. The build must differ from base by exactly the two name lines.
kubectl kustomize $HOMELAB/clusters/production/apps/nebula > /tmp/base.yaml
kubectl kustomize /home/zach/workspace/homelab-cmgen/clusters/production/apps/nebula > /tmp/new.yaml
diff /tmp/base.yaml /tmp/new.yaml    # expect 8 lines: CM name + volume ref, both -f27hbb48cg

# 3. The repo gate, on the branch AND on a pristine base — expect byte-identical output.
nix-shell -p kustomize kubeconform "python3.withPackages(ps: [ps.pyyaml])" \
  --run "bash scripts/kustomize-validate.sh clusters/production/apps/nebula"

# ── AFTER merging #678: the pod rolls. Confirm it came back on the SAME config. ──
KUBECONFIG=$KC_PROD kubectl -n nebula rollout status ds/nebula-gateway --timeout=180s
KUBECONFIG=$KC_PROD kubectl -n nebula get pod -l app=nebula-gateway \
  -o jsonpath='{range .items[*].spec.volumes[*]}{.name}={.configMap.name}{"\n"}{end}' | grep nginx
#   expect nebula-gateway-nginx-config-f27hbb48cg  (hashed — NOT the bare name)
KUBECONFIG=$KC_PROD kubectl -n nebula exec <new-pod> -c nginx -- nginx -t
KUBECONFIG=$KC_PROD kubectl -n nebula exec <new-pod> -c nginx \
  -- cat /etc/nginx/nginx.conf | sha256sum          # still 970d4bbe…
# Then prove the mesh actually carries traffic again — a Ready pod is not evidence:
curl -sS -o /dev/null -w '%{http_code}\n' https://ferdium.zacx.dev/   # expect 302 -> login.zacx.dev
```
## Open investigations — live diagnosis state
### (CLOSED) Does the desktop client survive the Authelia forward-auth gate?
- **Resolved 2026-09-03.** It did not, and could not: Authelia intercepted the whole `/v1/` API
  (`POST /v1/auth/signup` → 303, `GET /v1/auth/login` → 302, both to `login.zacx.dev`). Fixed by
  the scoped bypass in #668, which excludes signup. Verified working through the public edge.

### (CLOSED) Is homelab-infra #653 still the right fix for gateway config staleness?
- **Resolved 2026-09-04. No — superseded by `417a6386b` + #678.**
- **Observed (with values):** trunk still carries `subPath: nginx.conf` on both gateway
  DaemonSets, so the mount half of #653 was genuinely undone. But homelab's ConfigMap is now
  `configMapGenerator`-produced (live pod references `nebula-gateway-nginx-config-97b4gbm584`),
  so homelab already rolls on every config edit **while keeping subPath**.
- **Ruled out:** "the directory mount is what fixes staleness" — homelab is correct today and
  still mounts via subPath; the missing **roll** was the defect, not the mount. via: measurement
- **Ruled out:** "#653's production half is sufficient" — a directory mount updates the file but
  does not reload nginx, which re-reads only on SIGHUP, and production has no generator to roll
  the pod. #653's own body states this. via: doc
- **Ruled out:** "merging #653 is risky because a gateway restart re-runs startup resolution" —
  false on both clusters today: homelab resolves at request time, production has 42 proxy_pass
  directives and **zero** hostnames. via: measurement
