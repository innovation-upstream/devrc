# Handoff: ferdium-server

## Goal
Deploy Ferdium Server (self-hosted multi-messenger backend) on the homelab Talos cluster, exposed at `ferdium.zacx.dev` behind Authelia, so the Ferdium desktop client can sync without a cloud account.

## State now
**Ferdium: DONE — the desktop client logs in against the self-hosted server and syncs,
operator-confirmed 2026-09-03. The gateway config-staleness follow-on: DONE AND LIVE.**
Nothing about this effort is in flight. The remaining ranked items are all optional/deferred.

| Merged | What |
|---|---|
| devrc **#1240**, **#1241**, **#1266** (`f972992e`), **#1278** (`a7dac5bd`) | client packages; handoff |
| homelab-infra **#647** | manifests, both gateways, Authelia rule, relay firewall |
| homelab-infra **#651** | incident fix — dead promptver upstreams crashing the gateway |
| homelab-infra **#668** | registration closed + scoped Authelia bypass for the API |
| homelab-infra **#678** (squash `8b5ab4589`) | production nginx.conf → configMapGenerator |

Closed, not merged: homelab-infra **#653** (superseded — see Gotchas) and devrc **#1245**
(obsolete duplicate of this doc). Both branches preserved.

Carried forward — measured previously, still the state of the deployment:
- users = 1 (`zachlowden1@gmail.com`); `IS_REGISTRATION_ENABLED=false` and signup returns
  `{"message":"Registration is disabled on this server","status":401}`.
- Login through the public edge: 200 + token; wrong-password control: 401. Both measured.
- `GET /` still 302s to Authelia; `POST /v1/auth/signup` still 302s. 8117 still dropped
  from the internet.
- Vaultwarden holds the PLAINTEXT — correct, because the client hashes before sending.

**#678 post-merge, verified live 2026-09-04:** Flux reconciled on its 5m interval; pod
`nebula-gateway-6hjw7` (22h) → `nebula-gateway-svc2r`, 3/3 Running, **0 restarts**.
- DaemonSet now references the hashed CM `nebula-gateway-nginx-config-f27hbb48cg`, not the
  bare name; Flux pruned the old bare ConfigMap.
- `nginx -t` in the new pod passes.
- Running config sha `970d4bbe…` — **identical to the repo copy**, so the roll carried no
  config change. That was the whole safety argument for taking the window.
- `scripts/check-nebula-relay-hosts.sh`: all 27 probes answer, both bucket POSITIVE controls
  reach MinIO, mesh healthz 200, **zero 502/503/504**. One `000` on `mail.zacx.dev` — see
  Open investigations; three signals say it is not this change, but there is no before/after.

**Clawgate**: still no task. `clawgate_handoff.sh resolve` exited **5** (0 tasks for this
session). An unknown session id also answers 200 with an empty array, so this cannot
distinguish "touched no task" from "wrong id". No field written; not a clean bill of health.

**IN FLIGHT — the one thing this session did not land:** devrc **#1281**, this doc's own
update. It is `OPEN`, rebased onto current main, and its `tekton/devrc-pytests` check is
RED on a test a docs-only diff cannot reach (see Open investigations).

## Architecture (researched, not yet implemented)

```
Ferdium client → Cloudflare → production nebula gateway (10.0.0.2)
  → homelab nebula gateway (10.42.0.10:8117) → Ferdium Server (ferdium.svc:3333)
```

Authelia runs on the production cluster. Cloudflare handles TLS termination. The homelab nebula gateway proxies traffic to internal services.

## Next steps (ranked)
10. Digest-pin `ferdium/ferdium-server:latest` in
    `clusters/homelab/apps/ferdium/deployment.yaml:45` (homelab-infra) — upstream publishes
    no version tag, so the running image can change under any reconcile or restart with no
    diff in git. Verified still a mutable tag 2026-09-04.
    forcing: none
11. Commit or discard the dirty files in `~/workspace/devrc` so the two hosts are not
    silently divergent. Contents CHANGED during this session (another session is working in
    that clone): now `nix/programs/alacritty/default.nix` modified, plus untracked
    `nix/system/apply-nebula-relay.sh`, `nix/system/check-nebula-relays.sh`, `output.txt`,
    `scripts/diagnose-nix-disk.sh`. The clone is on `main`, **0 ahead / 0 behind**, so there
    is no `ship.sh` fast-forward blocker from commits — only uncommitted drift.
    forcing: regression
12. A gate asserting every `proxy_pass … .svc.cluster.local` in both gateway configs resolves
    to a Service defined in the repo (homelab-infra). **Narrower than when first written**:
    homelab now resolves at request time so a missing Service degrades one route instead of
    refusing to boot, and production has no hostname upstreams at all. A correctness check,
    no longer an outage guard.
    forcing: none
14. Restart Brave on the `personal - other` profile — it runs a stale extension build
    (`b817ef1e88267a40` vs expected `66b98084daecd880`) and **cannot open tabs at all**, which
    silently degrades the `browser` skill on that profile.
    forcing: none
16. `k0s/host-firewall/relay-firewall.sh` is NOT Flux-reconciled, so `417a6386b`'s PORTS
    change reaches diffsona only via `k0sctl apply` or a hand copy plus unit restart. Owed by
    that commit, not by this effort's work. Unverified whether it has been done.
    forcing: none
17. Make the relay-guard twin-path assertion actually run in CI. `scripts/tests/ci-manifest.txt:550`
    marks `test-check-relay-guard.sh` **SKIP** (it reads pinned history at `11f67175^` and the
    gitops-validate clone is `--depth 1`), so the pin added in #678 fires only for a human
    running the battery locally. Two ways, either sufficient: deepen that clone, or move the
    assertion into `scripts/check-relay-guard.py` itself, which DOES run on every PR.
    forcing: none
18. Commit a baseline for `scripts/check-nebula-relay-hosts.sh` (homelab-infra) so a future
    gateway roll is diffable rather than inferential. This session had to reason from three
    indirect signals about `mail.zacx.dev` precisely because no baseline existed.
    forcing: none
19. `claudedocs/handoff-nebula-pre-departure-hardening.md:261` says "expect 104 pass / 0 fail";
    the relay-guard harness is now **105** after #678 added the twin-path assertion. Left
    alone here because that doc belongs to another initiative with a live `claim-work` claim.
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

- 🔴 **A PR's own risk analysis can be INVERTED by a commit that lands after it is opened, and
  nothing marks the PR as stale.** #653 was opened 2026-09-02 16:20 CDT and was accurate then;
  `417a6386b` landed on trunk **2026-09-03 18:40 CDT**, 26h later, and inverted it — it
  added a `resolver` + `set $upstream` (killing its stated forcing reason) and a
  `configMapGenerator` (a better fix for its stated problem) — and `gh pr view --json mergeable`
  said `CLEAN` throughout, because the base moved on files whose TEXT did not collide.
  **Before acting on any PR older than a day: `git log <base> -- <the files it touches>` since
  its `createdAt`.**
- 🔴 **THE SEAM THIS EFFORT'S OWN VERIFICATION COULD NOT SEE.** #678 shipped with six
  verification bullets and a negative control, every one real — and every one scoped to the
  kustomize tier. `scripts/check-relay-guard.py` hardcoded the deleted path, so the change
  turned the production relay-port security checker OFF: rc 0 "all 42 relay listen port(s) are
  guarded" became rc 2 "**nothing was examined**". Two aggravating factors worth remembering:
  `gitops-validate-pipeline.yaml:1326` maps a non-1 exit to **`error`, not `failure`** — the
  status a reader is trained to dismiss as a broken gate — and `relay-firewall.sh` is a
  DENY-LIST, so a port absent from it is ACCEPTED. **Ask which surface your verification does
  NOT load**; here it was `scripts/`.
- 🔴 **A GUARD CAN BE ONE HOP AWAY FROM WHAT IT CLAIMS TO GUARD.** The fix for the above added a
  harness assertion pinning `_GATEWAY_NGINX`. The checker reads that constant EXACTLY ONCE, at
  the `NodeSpec(nginx_config=…)` assignment; what it resolves at runtime is
  `root / NODES[n].nginx_config`. Mutating the ASSIGNMENT while leaving the constant correct
  **survived the whole green suite** while the checker itself exited 2. Pin the field the code
  READS, never the constant that feeds it — and build the mutant that isolates your guard: a
  missing-file mutant was killed by pre-existing controls and proved nothing.
- 🔴 **`fail` was not the failure helper.** The first draft of that assertion called `fail "…"`;
  the harness's helper is `no()` and `fail` is the integer COUNTER. It would have printed
  "command not found", incremented nothing, and left the run reporting `fail=0` — green whether
  or not the thing it guards is broken. **The passing run said nothing; only running the
  mutation found it.**
- 🔴 **A MUTATION DELTA MUST BE MEASURED AGAINST THE SAME ENVIRONMENT'S BASELINE.** Round 2 of
  the audit reported `95/2` (mutant, in a `cp -a` copy with `.git` removed) against `105/0`
  (clean, in the real worktree) — both numbers correct, the PAIR misleading. The no-git copy
  skips 9 git-dependent assertions, so its baseline is `96/1`; the mutation moved exactly ONE
  assertion, not ten.
- 🔴 **`grep -c <pattern>` counts LINES CONTAINING the string, not directive INSTANCES.** 46
  lines matched `proxy_pass` in the production nginx config; 4 were `proxy_pass_request_headers`
  and one was a comment — the real directive count is 42, and the raw line count is 47, not 46.
  The tell was a triple that did not add up (total 46 / IP-literal 42 / non-IP 0).
- 🔴 **`git add <already-`git rm`d path>` fails the WHOLE `add` invocation.** Passing a staged
  deletion alongside two real paths aborted on `pathspec did not match any files` and staged
  NEITHER of the others; the commit then removed a ConfigMap without adding its replacement.
  `git status -s` immediately before the commit is what caught it.
- 🔴 **`resume-state.sh` MISATTRIBUTES a cross-repo PR written as `<repo> **#N**`.** It resolved
  all five homelab-infra numbers against devrc and emitted two confidently FALSE drift lines
  (`PR #653 MERGED`, `PR #651 CLOSED without merge`). Markdown bold between the repo name and
  the number defeats the qualifier regex; it should print `UNATTRIBUTED`. **Write
  `ZacxDev/homelab-infra#653` in handoffs.**
- **`git worktree add -b <branch> origin/main` sets the UPSTREAM to `origin/main`** — so
  `branch.<name>.merge` is `refs/heads/main` and a bare `--push` from the handoff tool would
  have pushed the commit straight onto `main`. Check
  `git rev-parse --abbrev-ref --symbolic-full-name @{u}` before any push from a fresh worktree.
- **The homelab-infra gate needs its toolchain supplied explicitly** or it fails for INSTRUMENT
  reasons that read as findings:
  `nix-shell -p kustomize kubeconform "python3.withPackages(ps: [ps.pyyaml])" --run "bash scripts/kustomize-validate.sh <root>"`.
  Its root argument is an exact path from `--list-roots`; a trailing glob matches nothing.
- **`$KC_NEBULA` does not exist — the production cluster handle is `$KC_PROD`.** devrc's
  CLAUDE.md names `KC_NEBULA`, which is stale; `env | grep ^KC_` is the arbiter.
- **The audit ladder stopped on the ATTRIBUTION gate, not on a clean round.** Rounds 2 and 3
  each found real defects, but both changed ZERO payload lines — the ladder had left the PR and
  was auditing scaffolding it had written itself. Recorded so a reader can tell this from a
  converged ladder: they look identical in the findings list.

## How to verify
```bash
# ── #678, now MERGED and LIVE. Re-run any of these to confirm it still holds. ──
export KUBECONFIG=$KC_PROD
POD=$(kubectl -n nebula get pods --no-headers | grep nebula-gateway | awk '{print $1}' | head -1)

# 1. The DaemonSet must reference the HASHED ConfigMap, not the bare name. The bare
#    name reappearing means the generator was reverted and staleness is back.
kubectl -n nebula get pod $POD -o jsonpath='{range .spec.volumes[*]}{.name}={.configMap.name}{"\n"}{end}' | grep nginx
#    expect: nginx-config=nebula-gateway-nginx-config-f27hbb48cg

# 2. The running config must still match the repo — this is what makes a roll safe.
kubectl -n nebula exec $POD -c nginx -- cat /etc/nginx/nginx.conf | sha256sum   # 970d4bbe…
git -C $HOMELAB show origin/trunk:clusters/production/apps/nebula/gateway/nginx.conf | sha256sum
kubectl -n nebula exec $POD -c nginx -- nginx -t

# 3. 🔴 The security checker must EXAMINE something. rc 0 alone is not enough —
#    read the port COUNT; "nothing was examined" is rc 2 and reads as a broken gate.
cd $HOMELAB && nix-shell -p "python3.withPackages(ps: [ps.pyyaml])" \
  --run "python3 scripts/check-relay-guard.py"
#    expect: OK: all 42 relay listen port(s) are guarded … across 2 node(s)
cd $HOMELAB && nix-shell -p "python3.withPackages(ps: [ps.pyyaml])" \
  --run "bash scripts/tests/test-check-relay-guard.sh"     # expect pass=105 fail=0

# 4. The mesh must actually carry traffic — a Ready pod is not evidence.
cd $HOMELAB && bash scripts/check-nebula-relay-hosts.sh
#    expect every host to answer, both bucket POSITIVE controls OK(<Error>), mesh healthz 200,
#    zero 502/503/504. mail.zacx.dev 000 is expected (no HTTPS listener) — see Open investigations.

# ── Ferdium itself (unchanged; the account/edge/firewall checks recorded above still apply) ──
curl -sSI https://ferdium.zacx.dev/ | head -1        # 302 -> login.zacx.dev
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

### devrc #1281's `tekton/devrc-pytests` is RED on a test the diff cannot reach
- **Symptom + exact repro:** `gh pr checks 1281 --repo innovation-upstream/devrc` →
  `tekton/devrc-pytests  fail  FAILED: pytests — FAILING:
  TestARefusedWriteIsIndistinguishableFromAnAbsentOne.test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_dif…`
  The PR's entire diff is one file: `claudedocs/handoff-ferdium-server.md`.
- **Observed (with values):** the test lives in `scripts/tests/test_subsystem_store_api.py`.
  Run on the DEV-HOST tier via `nix develop ~/workspace/devrc -c python3 -m pytest
  scripts/tests/test_subsystem_store_api.py -k TestARefusedWriteIsIndistinguishableFromAnAbsentOne -q`:
  **5 passed** on a pristine `origin/main` worktree (`46264622`), and **5 passed** on a
  pristine worktree of this branch's base (`a7dac5bd`). `tekton/devrc-nodetests` is green
  (`tests=1449 pass=1449 fail=0`).
- **Ruled out:** "my docs-only diff caused it" — the diff touches one markdown file and cannot
  reach `subsystem_store_api`. via: code
- **Ruled out:** "the test is red at this branch's base and a rebase fixes it" — it passes at
  `a7dac5bd` on the dev-host tier, and the branch has since been rebased onto `46264622`
  anyway. via: measurement
- **Ruled out:** "the test or its module changed between the branch base and main" —
  `git log a7dac5bd..origin/main -- scripts/tests/test_subsystem_store_api.py
  scripts/lib/subsystem_store_api.py` is EMPTY. via: command
- **Ruled out:** "it is unique to this PR" — devrc **#1288** shows the identical failing test
  in `gh pr checks`. via: measurement
- **Leading hypothesis:** a TIER-specific failure. The dev-host tier (`scripts/gate.sh`) and
  the sandbox tier Tekton runs (`nix build .#checks.x86_64-linux.pytests`, a `cp -r` store
  copy with **no `.git`**) are documented in devrc's CLAUDE.md as structurally able to
  disagree. A test named `POSITIVE_CONTROL … the APPEND comparison CAN see the dif…` is
  exactly the shape that behaves differently without a git dir or without network.
- **Next probe:** run the tier that is actually failing, alone —
  `nix build .#checks.x86_64-linux.pytests` from a pristine `origin/main` worktree. If it is
  red there too, this is a base-wide sandbox-tier break and belongs to whoever owns that test,
  not to #1281. 🔴 Build the two check derivations ONE AT A TIME; a combined invocation
  produces false failures under store contention.

### `mail.zacx.dev` returns 000 in the relay-hosts probe — not attributed
- **Symptom + exact repro:** `bash scripts/check-nebula-relay-hosts.sh` (homelab-infra) prints
  `mail.zacx.dev  000`. Every other one of the 27 probes answers.
- **Observed (with values):** its only Ingress is `nebula/mailpit-dns`, class
  **`external-dns-only`** — it publishes a DNS record and routes no HTTP. `postfix-relay-7dfff4489f-p79mk`
  is **60d old, 0 restarts** (untouched by the #678 roll). The NEW gateway pod listens on both
  `0.0.0.0:25` and `0.0.0.0:2525` (`netstat -ltn` inside the `nginx` container).
  `naida-mail-spf-dmarc.yaml` is a `DNSEndpoint` carrying SPF/DMARC, not an HTTP service.
- **Ruled out:** "the #678 roll broke it" — postfix-relay never restarted, and the new gateway
  carries both SMTP stream listeners. via: measurement
- **Ruled out:** "an HTTPS backend exists and is down" — the Ingress class is
  `external-dns-only`, so there is no HTTP backend to be down. via: code
- **Leading hypothesis:** expected steady state. `mail.zacx.dev` is an SMTP/DNS host with no
  HTTPS listener, and the prober probes it over HTTPS like every other name in its list.
- **Next probe:** 🔴 the honest gap is that NO before-run of this prober was captured, so this
  is inference from three signals, not a before/after. Fix the class, not the instance:
  land rank 18 (commit a baseline), then re-run and diff. An outbound `nc` to :25 from the
  workbench is NOT the probe — it hung, and ISP port-25 blocking would confound it anyway.
