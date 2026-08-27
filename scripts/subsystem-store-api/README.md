# subsystem-store-api — phase 1 + 1.5

Read-only HTTP layer over the `/analyze-service` subsystem index, so the store is
reachable from more than the workbench. Design: `claudedocs/proposal-subsystem-store-homelab.md`.

**Not referenced from `CLAUDE.md` on purpose.** That file loads every session and
this is not yet something a session uses — the CLI wrapper that would make it
usable is phase 2. Add the pointer when there is something to point at.

## What phase 1 is, and where it stops

| in | out, until |
|---|---|
| the pod, seeded from the local store | — |
| read-only `GET` API, bearer token **SET** | writes / append endpoints → **phase 3, criteria 4-10** |
| per-client rate limit + lockout, `CF-Connecting-IP` keying | a WRITE-scoped token → **phase 3, criteria 4-10** |
| per-token identity + **scope allowlist** on every read route (phase 3, criteria 1-3) | — |
| cluster-internal `ClusterIP` | 🔴 IngressRoute + DNS → **an UNMERGED PR** |
| byte-identity verified against local | the CLI wrapper + read-through cache → **phase 2** |

🔴 **Phase 1.5 landed the hardening and NOT the exposure.** The store is still
unreachable from the internet: the IngressRoute and DNS Ingress live in a
homelab-infra PR that is deliberately open and unmerged, because merging to
`trunk` there IS deploying and that merge is the go/no-go.

The local store at `~/.claude/analyze-service-index/` stays **authoritative and
untouched**. Rollback is `kubectl delete ns subsystem-store`.

🔴 **No git remote is involved, anywhere.** The store's README forbids one and
three tests in `scripts/tests/test_analyze_service_index_commit.py` enforce it.
Replication happens over this API; those tests are untouched.

## Files

| file | what |
|---|---|
| `server.py` | the HTTP layer. Imports `subsystem_recall`, returns `render_text`/`render_search` verbatim |
| `Dockerfile` | image, built from the **repo root** as context (the modules live in `scripts/lib`) |
| `build-push.sh` | build + push to Harbor. Refuses to push if `/data` in the image is non-empty |
| `seed.sh` | `rsync` the local store into a stage, optionally `tar`-push it into the pod. Never writes to the source |
| `verify-byte-identity.sh` | the phase-1 acceptance comparator: pod digest vs local CLI digest, per scope |

Manifests: `homelab-talos` → `clusters/homelab/apps/subsystem-store/`.

## Endpoints

```
GET /healthz                              unauthenticated; body is exactly "ok\n"
GET /api/v1/recall/{scope}[?mode=&ref=&limit=&page=]
GET /api/v1/search/{scope}?q=…[&threshold=&max_hits=&context=&all_scopes=]
```

Every `/api/*` response carries `X-Store-Status`, `X-Store-Exit` (the CLI's own
exit code, from the CLI's own `_exit_for`), `X-Store-Revision` (the scope's
git HEAD, or `unknown` — never a fabricated sha) and `X-Store-Snapshot`.

🔴 **This server does NOT serve the authoritative store — it serves a COPY, and
nothing syncs that copy.** `seed.sh` pushes it by hand; there is no CronJob and
no timer. Yet every report opens with `ALL N entries in <scope>/, none omitted`
— a completeness claim that is *true of this disk* and says nothing about the
source. MEASURED 2026-08-20, four days after cutover: the public endpoint
answered `200` with `ALL 5 entries in devrc/` while the source held **9**, and
one served entry was a 40-day-old version of a file edited that morning.
Reachability, auth, the client-IP chain and the firewall all verified green
throughout, because none of them compares served bytes to the source.

So every report now **dates itself**, in the body (first, before the claim it
qualifies) and in `X-Store-Snapshot`:

```
seeded=<when seed.sh took the stage> newest=<newest entry mtime> entry-files=<N>
```

Two independent facts, because each covers the other's blind spot — a quiet week
makes `newest` old while the copy is current; a forgotten re-seed makes `seeded`
old while `newest` merely lags. **Each failure is its own name, never an
omission**: `seeded=UNSTAMPED` (no stamp), `seeded=UNREADABLE` (present but
empty/unreadable), `newest=UNREADABLE` (the walk hit an error) — all distinct
from a genuinely empty store, which reads `newest=NONE entry-files=0`. The walk
uses `os.walk(onerror=…)` rather than `Path.rglob` precisely because `rglob`
swallows a permission error and would report an unreadable store as an empty one.

🔴 **`scope-empty` and `store-unreachable` do not render alike.** Reached the
store and found nothing → `200` + `X-Store-Status: scope-empty`. Read nothing at
all → `503` + `X-Store-Status: store-unreachable`, carrying the reader's own
"this is NOT 'nothing recorded yet'" sentence. A `200` is a claim the store was
read, and only the first of those can make it.

## Operating it

```bash
# build + push (immutable tag, no default)
scripts/subsystem-store-api/build-push.sh 0.1.0

# seed the pod from the local store (source is read-only)
scripts/subsystem-store-api/seed.sh \
    --store ~/.claude/analyze-service-index \
    --stage /tmp/store-stage \
    --push subsystem-store/subsystem-store-api

# reach it — phase 1 has no ingress, by design
kubectl -n subsystem-store port-forward svc/subsystem-store-api 18102:8102

# the acceptance check, every scope
scripts/subsystem-store-api/verify-byte-identity.sh \
    --store ~/.claude/analyze-service-index \
    --url http://127.0.0.1:18102 \
    --token-file <(kubectl -n subsystem-store get secret subsystem-store-token \
                     -o jsonpath='{.data.token}' | base64 -d)
```

The verifier sends `CF-Connecting-IP: 127.0.0.1` (override with `$CLIENT_IP`).
⚠ **Over a port-forward that header is INERT** — the peer is `127.0.0.1`, which
is not a trusted proxy, so the server ignores it and buckets the request under
the peer. It is sent so the same invocation also works when `--url` points at
the nebula gateway, where the peer *is* trusted and the header becomes the
client identity. See "Rate limit, lockout and the client address" below.

⚠ `verify-byte-identity.sh` prints a `diff` on failure, and that diff is store
content. Redirect it to a file on a shared terminal.

## Why byte-identity is asserted "modulo one line"

`render_text` prints `  store: <root>`, and the pod's root is `/data` while the
workbench's is `~/.claude/analyze-service-index`. The streams therefore cannot be
byte-identical, and a verifier that said they were would be lying. The script
canonicalises exactly that line and `cmp`s the result, and prints beside the
verdict how many lines differ **with no canonicalisation** and how many of those
are the store-root line — `2` and `2` for pod-vs-workbench.

## The token is a SET, and rotation is by overlap

The token file holds **one ROW per line, current first**. Blank lines are
ignored. Up to `MAX_TOKENS` (4) — every line is a live credential, so an
uncapped set is an accumulation nobody has retired, and the server refuses to
start rather than serve one.

🔴 **Two rows holding the same token collapse only if they are IDENTICAL** —
same token, same identity, same folded scope list. If they disagree the server
refuses to start with `duplicate token in rows N and M`, naming both
authorities. This is not pedantry: dropping the second row before parsing it
made `<tok>` followed by `<tok> zach kelp-forest` — "scope a credential its
holder already has", the migration's own first step — load as ONE unrestricted
`legacy` row, with no error and a banner that said `1 of 1 token rows are bare`
over a two-line file. **To scope an existing token, EDIT its bare row**; adding
a second row below it is refused.

A row is one of two shapes:

```
<token>                                    # LEGACY: identity `legacy`, ALL scopes
<token>   <identity>   <scope>,<scope>     # MAPPED: named holder, scoped
```

🔴 **Whitespace now separates the three FIELDS of one row, not two tokens.**
Before phase 3 the file was split on any whitespace, so `<tokenA> <tokenB>` on
one line was two credentials. It is now a row with a token in the identity
field — refused at startup with `malformed token row N of M`, never
reinterpreted. Put one row per line.

Generate a token:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(43))'   # 58 chars
```

### Identities and scope allowlists (phase 3, criteria 1-3)

A **mapped** row names who holds the token and which scopes it may read. The
identity is lowercase `[a-z0-9-]`, at most 32 characters — deliberately BELOW
the 43-character token floor, so a token can never be misread as an identity.
Scopes are comma-separated, each matching `[A-Za-z0-9_-]+`, folded by the
reader's own `normalize_ref` so `Kelp_Forest` and `kelp-forest` are one scope.

A scope outside the allowlist answers **exactly what a scope that never existed
answers** — `200`, `X-Store-Status: scope-absent`, and a body byte-identical to
the genuinely-absent one. There is no "forbidden" response, because a response
that discriminates is an enumeration API. What the caller sees narrows with it:
`known_scopes`, the cross-scope malformed block, `?all_scopes=1` search results,
the `/snapshot` tar members and `X-Store-Revision` are all the caller's own.

⚠ **`X-Store-Snapshot` (and the freshness prose) stays STORE-WIDE.** It carries
a total `entry-files=` count across scopes the caller cannot name. That is
deliberate — it is the staleness guarantee the snapshot design rests on, and
scoping it would make "how old is this copy" a function of your allowlist — but
it is a residual count leak, recorded here rather than left to be discovered.

`identity=<name>` is an **additional** audit field; `token=<fingerprint>` is
unchanged and is still the only thing that can tell one holder's current
credential from its previous one.

🔴 **A bare legacy row is still valid, and any file containing one makes the
process shout on stderr at startup**, naming the count and the legacy
fingerprints. That is the migration (mapped rows land beside the old shared
token) and the rollback (put the one line back — no code change). Rotating a
MAPPED credential uses a second identity (`zach-prev`), not a second row naming
the same one: two rows claiming one identity with disagreeing allowlists have no
defined precedence, so they are refused.

### Rotating, and how you know it is safe to finish

🔴 **The audit line names the fingerprint of the token that MATCHED**, not the
one the server was configured with. That is the whole safety of overlap
rotation — without it, "nobody uses the old token any more" is a guess:

```
store-api audit ts=… ip=203.0.113.7 peer=trusted method=GET path=/api/v1/recall/x \
  token=dbb22a50030d auth=ok result=200 status=recalled
```

1. `sops clusters/homelab/apps/subsystem-store/secrets.enc.yaml` — put the NEW
   token on the first line, keep the old one below it. Commit; Flux applies.
2. Restart the pod. Its startup line prints every fingerprint in file order,
   each with its identity: `token-ids=<new>:legacy,<old>:legacy`.
3. Roll clients onto the new token. Watch the audit stream until the OLD
   fingerprint stops appearing:
   `kubectl -n subsystem-store logs deploy/subsystem-store-api | grep 'token=<old>'`
4. Only then delete the old line, commit, restart. A request presenting it is
   now an ordinary 401.

Step 3 is the step that does not exist without step 2's fingerprint, and step 4
is the one people skip — a rotation that leaves the old credential live is not a
rotation. The whole sequence is exercised in-band by
`TestTokenSetAndOverlapRotation::test_a_ROTATION_end_to_end_old_still_works_then_stops`
and against the real process by `TestTheDeployedEntrypoint`.

## Rate limit, lockout and the client address

| knob | default | env |
|---|---|---|
| failed auths before a lockout | 5 | `SUBSYSTEM_STORE_MAX_FAILURES` |
| window they must fall inside | 60 s | `SUBSYSTEM_STORE_FAILURE_WINDOW_S` |
| lockout duration | 900 s | `SUBSYSTEM_STORE_LOCKOUT_S` |

A malformed value **exits 78** at startup rather than defaulting silently.

🔴 **The client is keyed on `CF-Connecting-IP`, and nothing else.** Cloudflare
overwrites it on every proxied request, which is the only reason it can be
trusted — and it stops being trustworthy the moment Cloudflare is not the sole
public ingress. `X-Forwarded-For` is never read: it is caller-supplied, so an
attacker keyed on it gets a fresh bucket per request *and* can lock out a third
party by forging theirs. Behind Cloudflare + Traefik the TCP peer address is the
gateway's for everybody, so keying on that is the mirror failure — one abuser
locks out the world.

### 🔴 …and the header is only read from a TRUSTED PEER

| knob | default | env |
|---|---|---|
| peers whose `CF-Connecting-IP` is read | **none — required** | `SUBSYSTEM_STORE_TRUSTED_PROXIES` |

`CF-Connecting-IP` is caller-supplied bytes. "Cloudflare overwrites it" is true
of a request that *arrived from Cloudflare* and says nothing about one that did
not — so before this setting existed, anything able to address the pod on 8102
(a pod in the cluster, a `kubectl port-forward`, a second IngressRoute) could
name a **third party** in the header, send five bad tokens, and lock that third
party out for fifteen minutes. The victim saw a 401 indistinguishable from a
wrong credential.

The rule is the standard reverse-proxy one:

```
client = CF-Connecting-IP   if the TCP peer is in the allowlist
       = the TCP peer       otherwise (and the header is not read at all)
```

That is enough. The property that matters is **a forged header must never name a
third party**, and bucketing the forger under its own address satisfies it
completely — such a caller can only ever lock out itself.

### 🔴 The value is NOT the address you would guess

For the homelab deployment it is **`10.244.0.123/32`**, and none of the three
obvious candidates are right:

| candidate | what it actually is | right? |
|---|---|---|
| `10.42.0.10` | the homelab nebula gateway's **nebula mesh** IP, which is what its nginx *listens* on | ✗ |
| `192.168.50.94` | the node's Kubernetes **InternalIP** | ✗ |
| `10.244.0.123` | the node's **`cilium_host` router address** — what the gateway's traffic is SNAT'd to on the way into the pod | ✓ |

**Derive it, do not guess it** — from inside the caller, which is the only
perspective that can be wrong in a way you would notice:

```bash
kubectl -n nebula exec ds/nebula-gateway -c nginx-proxy -- \
  ip route get "$(kubectl -n subsystem-store get pod -l app=subsystem-store-api \
                    -o jsonpath='{.items[0].status.podIP}')"
# 10.244.0.13 dev cilium_host  src 10.244.0.123
#                                  ^^^^^^^^^^^^ this is the value
```

🔴 **It is Cilium-allocated per node and nothing reconciles it.** A node rebuild
can hand out a different router address, and no controller will update this env
var. ⚠ **And the failure is SILENT** — requests are still served, just all
bucketed under one address, so `/healthz` keeps answering and the pod stays
Ready (see below). An earlier revision of this line said "loud and fail-safe",
which was true of the refuse-outright design this replaced and contradicted its
own cross-reference two sections down. A degradation that keeps the health probe
green is the anti-pattern this module names elsewhere; the only tell is
`peer=untrusted` in the log, and it is a manual step on a list nobody keeps.

### Rules on the value

* **There is no default and the process exits 78 without one.** A default would
  be a guess about somebody's network, and the only guess that keeps every
  deployment working is the permissive one — which is the defect itself.
* `0.0.0.0/0` (and `::/0`) is **refused**: the pre-fix behaviour spelled as
  configuration.
* **A prefix wider than `/24` (IPv4) or `/64` (IPv6) is refused too**, because
  refusing only `/0` inspects one entry in isolation: the two halves of the
  address space, each written as a `/1`, parse clean and together trust
  everything, and — the realistic mistake — a pod CIDR like `10.244.0.0/16`
  would hand the client identity to every pod in the cluster, which is exactly
  the attacker this setting exists to stop. List several narrower entries if you
  genuinely need a wide range.
* **Entries must be written in the same address family the peer arrives as.**
  `peer_address` unwraps an IPv4-mapped peer (`::ffff:10.0.0.1` → `10.0.0.1`)
  but the allowlist parser does not, so an entry spelled `::ffff:10.0.0.1` will
  **never match**. Fail-closed, so it is a config error rather than a hole —
  write `10.0.0.1`.
* A request from any other peer is **served normally, bucketed under the peer's
  own address**, and annotated `peer=untrusted` in the audit log. It is *not*
  refused: refusing was tried, and it broke the phase-1 acceptance procedure
  outright (`verify-byte-identity.sh` runs through `kubectl port-forward`, whose
  peer is `127.0.0.1`, not the allowlisted address) while turning one wrong
  address into a total outage that `/healthz` hid.
* `/healthz` is answered **before** any of this, so the kubelet probe (which
  comes from the node and sends no `CF-Connecting-IP`) is untouched.
* The startup line prints the allowlist, so "which peers may set the client
  identity" is readable out of a running pod.

**If the value is wrong**, every API request is served but keyed on the gateway's
address — so one client's failures can lock out the others, and every line says
`peer=untrusted`. That is the symptom to grep for; the pod stays Ready either
way, so the probe will not tell you.

⚠ **What it cannot do.** The pod sees whatever last hop connected to it — in
the homelab deployment the in-cluster gateway, never Cloudflare's own address.
So this proves *the request came through the gateway*, not *the request came
through Cloudflare*.

Narrowing who can occupy that hop is a NetworkPolicy's job — 🔴 **and that
NetworkPolicy is NOT DEPLOYED YET.** `homelab-infra`
`clusters/homelab/apps/subsystem-store/networkpolicy.yaml` exists only on the
unmerged branch of **homelab-infra #330**; on `trunk` that kustomization still
lists four resources (`namespace`, `pvc`, `secrets.enc`, `deployment`) and no
policy. Until #330 merges, **any pod in the cluster can address this pod on
8102** and the app-layer gate above is the only layer there is. Do not read this
paragraph as a compensating control that is already in place — and see that
file's header for why, even once it lands, the two layers overlap far more than
they compose.

An absent, unparseable or **duplicated** header **fails closed** for a TRUSTED
peer: a uniform 401, and nothing counted, because there is no bucket to count
into. (For an untrusted peer the header is never consulted, so its absence is
not an error.)

Every rejection is the same 401 on the wire — body, code and header set — and is
told apart only in the audit log's `status=` field. The full vocabulary, which is
what the Loki rules select on:

| `status=` | meaning |
|---|---|
| `unauthorized` | no/!wrong token, or a path outside `/api/v1/` |
| `no-client-ip` | absent, unparseable or duplicated `CF-Connecting-IP` — fails closed |
| `locked-out` | this client is serving a lockout |
| `lockout-triggered` | this request is the one that started it |
| `malformed-target` | a request target `urlsplit` could not parse |
| `malformed-request` | the request LINE itself was unparseable — no headers exist |
| `method-not-allowed` | a write verb from an authenticated caller (405, not 401) |

An error that discriminates is an enumeration API; a log that does not is a
post-mortem with no evidence.

🔴 **`peer=` is a SEPARATE field from `status=`, on purpose.**

| `peer=` | meaning |
|---|---|
| `trusted` | the peer was in the allowlist, so `ip=` came from `CF-Connecting-IP` |
| `untrusted` | **something addressed the pod directly** — `ip=` is the TCP peer and the header was never read |
| `-` | the request line was too malformed to establish either |

An earlier version of this feature spelled it `status=untrusted-peer` with
`auth=fail`. That put every `kubectl port-forward` — including the documented
byte-identity run — into the Loki auth-fail alert, which is how an alert gets
ignored. Reaching the pod directly is worth **detecting**; it is not an
authentication failure and must not be written as one. Grep it with
`{namespace="subsystem-store"} |= "peer=untrusted"`.

⚠ **A wrong PATH is not a failed AUTH and is not counted.** Only a request that
reaches the token check and fails it moves the lockout. Counting path probes
measured as a client holding the *right* token locking itself out with five
ordinary 404-ish requests. Volumetric probing is the Traefik and Cloudflare
layers' job; this layer is the only one that can see a wrong credential.

## Deferred, and why (not oversights)

- **Separate read/write tokens** — the READ half landed with phase 3 criteria
  1-3 (identity + scope allowlist above). A WRITE-scoped token is still deferred
  for the original reason: criteria 4-10 add no verb yet, so it would be a label
  on a capability that does not exist. `do_POST = do_PUT = do_PATCH = do_DELETE
  = _reject_write` is untouched and every write verb is still a 405.
- **Backup CronJob, daily-commit CronJob** — the workbench copy is authoritative
  until phase 3, so the PVC is a second copy, not the only one.
- **A `rotate-token` script** — the procedure above is four steps across a SOPS
  file and a `kubectl` restart in another repo, and §2b's "one command" is worth
  building only once phase 2's wrapper exists to be the thing that holds it.
  Stated here rather than claimed as done.
