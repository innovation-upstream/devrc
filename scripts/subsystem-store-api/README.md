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
| read-only `GET` API, bearer token **SET** | writes / append endpoints → **phase 3** |
| per-client rate limit + lockout, `CF-Connecting-IP` keying | separate read/write tokens → **phase 3** |
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
exit code, from the CLI's own `_exit_for`) and `X-Store-Revision` (the scope's
git HEAD, or `unknown` — never a fabricated sha).

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

The verifier sends `CF-Connecting-IP: 127.0.0.1` (override with `$CLIENT_IP`)
because the server requires one — see "Rate limit, lockout and the client
address" below.

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

The token file holds **one token per line, current first**. Whitespace-separated
is also accepted; blank lines are ignored; duplicates collapse. Up to
`MAX_TOKENS` (4) — every line is a live credential, so an uncapped set is an
accumulation nobody has retired, and the server refuses to start rather than
serve one.

Generate one:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(43))'   # 58 chars
```

### Rotating, and how you know it is safe to finish

🔴 **The audit line names the fingerprint of the token that MATCHED**, not the
one the server was configured with. That is the whole safety of overlap
rotation — without it, "nobody uses the old token any more" is a guess:

```
store-api audit ts=… ip=203.0.113.7 method=GET path=/api/v1/recall/x \
  token=dbb22a50030d auth=ok result=200 status=recalled
```

1. `sops clusters/homelab/apps/subsystem-store/secrets.enc.yaml` — put the NEW
   token on the first line, keep the old one below it. Commit; Flux applies.
2. Restart the pod. Its startup line prints every fingerprint in file order:
   `token-ids=<new>,<old>`.
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

An absent, unparseable or **duplicated** header therefore **fails closed**: a
uniform 401, and nothing counted, because there is no bucket to count into.
Anything reaching the pod directly (a port-forward, `verify-byte-identity.sh`)
must send the header itself; that is not a hole, since addressing the pod
directly already bypasses the edge.

Every rejection is the same 401 on the wire — body, code and header set — and is
told apart only in the audit log's `status=` field: `unauthorized`,
`no-client-ip`, `locked-out`, `lockout-triggered`. An error that discriminates is
an enumeration API; a log that does not is a post-mortem with no evidence.

## Deferred, and why (not oversights)

- **Separate read/write tokens** — there is no write path until phase 3, so a
  write-scoped token today is a label on a capability that does not exist.
- **Backup CronJob, daily-commit CronJob** — the workbench copy is authoritative
  until phase 3, so the PVC is a second copy, not the only one.
- **A `rotate-token` script** — the procedure above is four steps across a SOPS
  file and a `kubectl` restart in another repo, and §2b's "one command" is worth
  building only once phase 2's wrapper exists to be the thing that holds it.
  Stated here rather than claimed as done.
