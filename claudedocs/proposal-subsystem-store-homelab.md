# Proposal: move the subsystem store into homelab, behind an auth'd ingress

**Status:** proposal, nothing built. Every number about the *current* store below was measured
2026-08-16; every number about the *proposed* system is an estimate and is labelled as one.

**Goal (as stated):** make the store simple to reach from anywhere.

---

## 0. The gating decision, before any design

🔴 **The store's own policy file forbids exactly the thing that "migrate it to a server"
usually means.** `~/.claude/analyze-service-index/README.md`:

> the scopes hold **client-sensitive** infrastructure detail and **must never gain a git remote**.

This is not just prose. Three tests in `scripts/tests/test_analyze_service_index_commit.py`
enforce it, and one of them is deliberately paranoid:

| test | asserts |
|---|---|
| `test_a_normal_run_adds_no_remote` | the daily commit timer never configures a remote |
| `test_a_configured_remote_is_never_pushed_to` | **even if a remote exists**, no push, and no `refs/remotes/*` may appear |
| `test_a_configured_remote_is_not_pushed_to_on_a_CLEAN_run` | same, on the no-op path |

**The design below keeps all three passing unchanged, and does not touch git remotes at all.**
Replication happens over the HTTP API, not `git push`. That is a deliberate choice: it honours
the letter of the policy, so no test has to be weakened and no reviewer has to decide whether
this migration is "the exception".

But the *intent* behind the policy — client-confidential content must not end up somewhere it
can leak — is a live question that only you can settle, and it has one sharp edge:

🔴 **Every other `*.zacx.dev` host is Cloudflare-proxied** (`cloudflare-proxied: "true"` in each
DNS Ingress). Proxied means TLS terminates at Cloudflare's edge, so **plaintext
client-confidential entries would transit a third party**. `clawgate.zacx.dev` already accepts
that for tasks and chat. This store is a different content class: it is, by its own README,
client-sensitive infrastructure detail about a paying client.

**Two exposures were put to the operator:**

- **A. Nebula-only** — `store.homelab.lan` / a Nebula address, no public DNS, no Cloudflare.
  "Anywhere" = anywhere on the mesh. Content never leaves your infrastructure.
- **B. Public via `store.zacx.dev`** — same edge chain as clawgate. Reachable from a machine with
  no Nebula client at all, at the cost of client-confidential content crossing Cloudflare.

### ✅ DECIDED 2026-08-16: **B — public `store.zacx.dev`.**

A was recommended and not taken; this is the operator's call and the rest of this document is
written to B. Recording it here so a later reader does not re-open a settled decision, and so the
consequences below are read as *requirements*, not as an argument for A.

**What B commits us to, stated plainly:**

1. **Client-confidential entries transit Cloudflare in plaintext.** `cloudflare-proxied: "true"`
   terminates TLS at CF's edge. This is the same trust already extended to clawgate's tasks and
   chat; it is now also extended to client infrastructure detail.
2. 🔴 **The `/api/*` path has no edge auth. The bearer token is the only thing between the public
   internet and the store.** Authelia's forward-auth chain 302s to a login page, which a CLI
   cannot satisfy — that is the entire reason for the split in §2b. Under A a routing mistake
   exposed the API to the mesh; under B it exposes it to the internet. Everything in §2b marked
   **(B-required)** exists because of this and is not optional.
3. **The origin IP is Cloudflare's problem, not ours** — proxied mode keeps the Hetzner gateway
   address hidden and gives WAF/rate-limiting as a free outer layer. This is a real argument
   *for* proxied over a direct A record, and it partly offsets point 1.

**One sub-decision left open, defaulting to the precedent:** proxied (`"true"`) matches every
other `*.zacx.dev` host and buys the WAF; unproxied (`"false"`) keeps TLS terminating on your own
Traefik at the cost of publishing the gateway IP. **Defaulting to proxied** — it is a
one-annotation change either way, and no other part of the design depends on it.

---

## 1. What makes this much cheaper than it looks

Both CLIs already accept `--store`:

```
scripts/lib/subsystem_recall.py:2412   p.add_argument("--store", default=str(DEFAULT_STORE_ROOT), …)
scripts/lib/subsystem_touch.py:4587    p.add_argument("--store", default=str(DEFAULT_STORE_ROOT), …)
```

and `DEFAULT_STORE_ROOT` is a single module-level `Path` (`subsystem_touch.py:366`). So:

**The pod is the existing code, unmodified, pointed at a PVC.** The HTTP layer imports
`subsystem_recall` and calls `recall()` / `search()` / `read_entry()` — the same functions the
CLI calls — and returns their rendered text. It does not reimplement the digest, the index,
the `🔴 N OPEN` badge, the malformed-entry degradation, the no-match-with-closest-candidate
message, or the sensitivity fail-safe.

Three consequences worth stating plainly:

1. **No rendering rewrite**, so no opportunity for local and remote output to disagree.
2. **Token cost does not regress.** The server returns *rendered text*, not JSON the agent must
   re-render. The measured local figures (skill doc, 2026-08-13 — digest 4,876 B / ~1,219 tok;
   `--search minio` 5,267 B / ~1,316 tok; `--search 'nginx ratelimit'` 1,711 B / ~427 tok) carry
   over unchanged, because the identical function produces the identical bytes.
3. **One test suite covers both.** The rendering tests already exist and are store-root-agnostic.

The store is also small enough that none of the usual migration problems apply: **2.4 MB total,
47 entries across 8 scopes** (largest `datapacket-talos` at 1.1 MB / 30 entries), 52 git commits
across all scopes combined.

---

## 2. Architecture

```
                    ┌─────────────────────────────────────────────┐
   workbench ──┐    │  homelab cluster, ns: subsystem-store        │
               │    │                                              │
   laptop ─────┼─── │  Deployment: store-api                       │
               │    │    - the SAME subsystem_recall/_touch modules │
   phone ──────┘    │    - thin HTTP layer, --store /data          │
      (Nebula)      │    - bearer-token auth on /api/*             │
                    │  PVC: 1Gi local-path  ← per-scope git repos  │
                    │  CronJob: daily commit + MinIO backup        │
                    └─────────────────────────────────────────────┘
```

### 2a. Storage
PVC mounted at `/data`, laid out **exactly as the local store is today** — `<scope>/<entry>.md`,
one independent git repo per scope, no remotes. The existing `scripts/analyze-service-index/commit.sh`
timer runs in-cluster as a CronJob instead of on the workbench. Nothing about the on-disk format
changes, which is what makes rollback a `rsync` in the other direction.

Backup mirrors the clawgate precedent (`clusters/workbench/apps/clawgate/backup-cronjob.yaml`):
daily tar to MinIO, 30-day prune. **`local-path` is node-local with no replication** — the PVC is
not a backup, and the store currently exists in exactly one place already, so this is a strict
improvement on today.

### 2b. Auth — two paths, because a passkey cannot be typed by a script

| path | who | mechanism |
|---|---|---|
| `/` (browser UI, if built) | you, in a browser | Authelia forward-auth, `one_factor`, passkey — the clawgate chain |
| `/api/*` | the CLI wrapper, agents | **bearer token only** — no edge auth |

The split is not novel here: Authelia's config already carries a path-scoped bypass
(`resources: ["^/api/event.*$"]` for Plausible, `authelia.yaml:120-125`) and
`tekton-webhook.zacx.dev` skips Authelia entirely in favour of app-level HMAC. A forward-auth
302 to `login.zacx.dev` is unusable from a CLI; that is the whole reason for the split.

**Two IngressRoutes on one host**, copied from `gateway/clawgate-ingress.yaml`:

```yaml
# route 1 — the API. NO authelia middleware. Explicit priority, so this is not
# left to Traefik's implicit longest-rule-wins ordering.
- match: Host(`store.zacx.dev`) && PathPrefix(`/api/`)
  priority: 100
  services: [{ name: homelab-subsystem-store, namespace: nebula, port: 8110 }]

# route 2 — everything else, behind the passkey chain.
- match: Host(`store.zacx.dev`)
  priority: 1
  middlewares: [{ name: authelia, namespace: authelia }]
  services: [{ name: homelab-subsystem-store, namespace: nebula, port: 8110 }]
```

Because route 1 carries no middleware, Authelia never sees `/api/*` and its config needs **no**
bypass rule — only the `store.zacx.dev` → `one_factor` / `subject: user:zach` rule for route 2.
That is simpler than the Plausible `resources:` pattern, but it moves the whole burden onto
route ordering, which is why the priorities are explicit rather than inferred.

#### 🔴 (B-required) hardening — because route 1 faces the internet

None of these are optional under exposure B:

- **Constant-time token comparison**, ≥256 bits of entropy. A `==` on a secret is a timing oracle
  that a public endpoint makes practically exploitable.
- **Uniform, terse 401s.** No scope names, no entry refs, no "unknown scope" vs "bad token"
  distinction in an unauthenticated response — an error that discriminates is an enumeration API.
- **Rate-limit + lock out on repeated 401s**, at the Traefik middleware layer *and* in the app.
  Cloudflare's WAF is the third layer, not the only one.
- **Audit-log every `/api/*` request** — timestamp, path, token id (not the token), result. If
  this store is ever suspected of having leaked, that log is the only thing that can answer it.
- **Health endpoint stays unauthenticated but says nothing** — `200 ok`, no version, no scope
  count, no store revision.
- **Separate read and write tokens.** A recall-only token on a laptop cannot append or overwrite.
- **Token rotation must be a one-command operation** and must be exercised once before cutover —
  a rotation path that has never been run is not a rotation path.

🔴 **The token must be readable from a FILE, not only an env var.** Measured previously and
recorded in memory: the agent exec sandbox strips `$GITHUB_TOKEN` from agent-run commands — the
container has it, the agent's `printenv` shows nothing. clawgate 0.7.49 moved to a file-based
credential for exactly this. So: `--token-file`, defaulting to `~/.config/subsystem-store/token`
(mode 0600, delivered by home-manager), with `$SUBSYSTEM_STORE_TOKEN` as a fallback, not the
primary.

Token lives in a SOPS-encrypted secret (`secrets.enc.yaml`) on the cluster side, per the repo's
`*.enc.yaml` convention.

### 2c. Writes are APPENDS, not whole-file PUTs

🔴 **This is the part most likely to be got wrong, so it is worth being explicit.** The obvious
API — `GET /entry`, `PUT /entry` — is a last-writer-wins whole-file replace, and with the
workbench, the laptop and in-cluster agents all writing, that silently destroys bullets. Two
agents appending different work-history bullets to one entry is not a hypothetical; it is the
normal case.

The store's own write model is already append-shaped: `/handoff` and `/analyze-service` add a
dated bullet under `## Nuance / work-history`. So the API should expose that operation directly:

```
POST /api/v1/entry/{scope}/{ref}/bullets     {"text": "- 2026-08-16: …"}
```

Appends are commutative and idempotent when keyed on a content hash, which makes concurrent
writers a non-problem rather than a conflict-resolution problem. Whole-file `PUT` stays available
for `## Pointers` edits and for the `OPEN:` → `RESOLVED <sha>:` rewrite, but takes an
**`If-Match` revision** so a blind overwrite is rejected rather than silently winning.

### 2d. Reads stay offline-capable

🔴 **Do not make `/resume` depend on the network.** `subsystem_recall.py` documents itself as
"READ-ONLY. No clock, no network, no git, no prompt" (`subsystem_recall.py:1149`), and it runs on
the `/resume` hot path. If a recall becomes a live HTTP call, then a DNS blip, a suspended laptop
or a cluster reconcile turns "orient me" into an error — or worse, into an empty screen.

So the wrapper is **read-through with a local cache**: it syncs the store to
`~/.cache/subsystem-store/` and runs the unmodified local `subsystem_recall.py --store` against
it. The reader keeps its no-network property; the *wrapper* owns the network.

---

## 3. The CLI wrapper

`scripts/lib/store.py` (or `scripts/store`), one entrypoint, argument-compatible with today's
CLIs so no skill prose has to be rewritten twice:

```bash
store recall --repo $DEVRC              # same digest, same bytes
store recall --scope datapacket-talos --search "nginx ratelimit"
store touch  --repo $DEVRC --pr 507
store validate --scope datapacket-talos
store sync                              # explicit pull/push, also run by a timer
```

### Token efficiency
- **Server-side rendering.** The API returns the finished digest, never JSON to be re-rendered.
  The agent pays for the same ~1,219-token digest it pays for today, not for a payload plus a
  rendering step.
- **`--search` unchanged as the cheap path** (427–1,316 tok measured) — remote does not make the
  expensive default any more attractive.
- **One command, one round trip.** Same shape as `obs-read`: port-forward → query → teardown in
  a single deterministic call, rather than an agent hand-rolling `kubectl`/`curl`.

### Determinism
- **Pin an API version in the path** (`/api/v1/`), and have every response carry a
  `store-revision:` line (the scope's git HEAD). Identical query + identical revision ⇒
  byte-identical output, which is something the *local* store cannot promise today, since it
  mutates under you between two reads in one session.
- **Quote the revision in reports.** "recalled at `datapacket-talos@a1b2c3d`" is checkable later;
  "the index said" is not.
- **Exit-code contract preserved exactly:** non-zero only when nothing readable came back. A
  scope that served entries alongside a `MALFORMED` block still exits 0.

### 🔴 The failure mode this must not have
An unreachable store returning "nothing recorded yet" is the **silent-zero** class: `scope-empty`
and `store-unreachable` render identically and one of them is a lie. The wrapper must therefore
distinguish, in the output itself, four states — and never collapse them:

| state | meaning | exit |
|---|---|---|
| `live` | fetched from the pod just now, revision `<sha>` | 0 |
| `cached (stale 3h, revision <sha>)` | pod unreachable, **served from cache — say so loudly** | 0 |
| `scope-empty` | reached the store, genuinely nothing recorded | 0 |
| `store-unreachable, no cache` | nothing was read at all — **not** an all-clear | non-zero |

Same discipline as `obs-read`'s silent-zero guard and `drift-check.sh`'s "links EXAMINED beside
links dangling": a zero has to arrive with evidence that the thing which produces non-zeros was
actually running.

---

## 4. Migration, in phases that each stand alone

| phase | what | rollback |
|---|---|---|
| **1** | Build the pod, seed `/data` from an `rsync` of the 8 scopes. **Local store stays authoritative and untouched.** Read-only API, **cluster-internal only — no ingress yet.** Verify remote digest is byte-identical to local for all 8 scopes. | delete the namespace |
| **1.5** | 🔴 **(B-only gate)** Land the §2b hardening and watch every auth control in §5 fail-closed **before** the IngressRoute exists. Under B the ingress is the moment the store becomes internet-reachable; it is the last thing to land, not the first. | no ingress was created |
| **2** | Ship the wrapper in read-through mode. `/resume` still reads local. Compare the two for a week. | stop using the wrapper |
| **3** | Cut writes over: `--pr`/`--commit`/`--session` and `/handoff` step 4 write via the API; append-only ops; local becomes cache. Move the daily commit timer into the CronJob. | point `--store` back at `~/.claude/…` |
| **4** | Retire the local writer path; add the browser UI **only if you actually want one** — the CLI may be the whole product. | — |

Phase 1 is the honest test of the whole idea and costs almost nothing, because the pod runs
code that already exists.

---

## 5. Verification plan (controls, not assertions)

Because every reassuring number here is the kind that has been wrong before:

- **Positive control on the API:** a query that MUST return a non-zero hit count, watched to
  return it — before believing any zero from the same command shape.
- **Negative control on auth:** a request with no token, and one with a wrong token, both watched
  to be **rejected**. An auth layer that has never been seen to deny is not known to be an auth
  layer. Build the unauthorised case from a realistic request, not a textbook one.
- 🔴 **(B-only) the negative control must be run from OFF the mesh**, e.g. a phone on cellular.
  A 401 observed from the workbench proves the app rejected it; it does not prove the public
  route reaches the app the way you think it does. Route 1 carries no middleware, so a routing
  mistake is silent — and the request that would reveal it is the one that does not come from
  your own network.
- 🔴 **(B-only) prove route 2 is actually gated**, by fetching `/` off-mesh with no session and
  watching the 302 to `login.zacx.dev`. Two routes on one host with explicit priorities is
  precisely the config where "the API route also swallowed the UI" passes every local test.
- **(B-only) confirm the 401 body is uniform** across bad-token, unknown-scope and unknown-ref —
  three requests, one response shape. A discriminating error is an enumeration API.
- **Byte-identity between local and remote** for all 8 scopes' digests at phase 1 — `cmp`, not
  eyeballing, and not `diff` alone.
- **The stale-cache path exercised on purpose:** stop the pod, run a recall, and confirm the
  output says `cached (stale …)` and does **not** read as an all-clear.
- **Concurrent append:** two writers appending to one entry simultaneously; assert both bullets
  survive. This is the defect the whole-file `PUT` design would ship.
- **The three no-remote tests re-run unchanged** — they are the check that this migration did not
  quietly become the thing the policy forbids.

---

## 6. What I would not do

- **Don't make the pod the only copy.** The read-through cache is what keeps `/resume` working on
  a suspended laptop, and it is what makes rollback trivial.
- **Don't `git push` scopes to a forge**, even a private one. It buys nothing the API does not,
  and it forces the three tests to be weakened — which is the moment "client-confidential" stops
  being structurally enforced and starts being a thing everyone remembers.
- **Don't build the browser UI in phase 1.** The store's only two consumers today are two CLIs
  and a skill. A UI is a plausible want, not a demonstrated one.
- **Don't put this on the workbench cluster** just because clawgate is there. The workbench is a
  single NixOS node you also use as a desktop; homelab is the durable one, and the stated goal is
  reaching the store when you are away from the workbench.

## 7. Rough sizing (estimate, not measured)

| piece | estimate |
|---|---|
| HTTP layer over the existing modules | ~300–500 lines, mostly argument plumbing |
| k8s manifests (ns, deploy, svc, pvc, secret, ingressroute, backup cronjob) | 7 files, all with a direct precedent to copy |
| CLI wrapper incl. cache + the four-state guard | ~400 lines |
| tests (append concurrency, auth controls, stale-cache, byte-identity) | the real work; budget more than the server |

Exposure is **settled: B** (§0), so the estimate above now includes the `/api/*` hardening, which
under A would have been optional. Everything else follows from existing precedent in
`homelab-talos`.

**Nothing is blocked.** Phase 1 builds and verifies the pod entirely cluster-internal; the
IngressRoute — the file that makes any of this public — lands at phase 1.5, after the auth
controls in §5 have been watched to fail closed, including from off the mesh.
