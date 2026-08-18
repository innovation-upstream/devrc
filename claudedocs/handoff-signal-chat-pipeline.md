# Handoff: signal-chat-pipeline — 2026-08-17

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
A Signal equivalent of the `mailbox` skill: **receive → store → query → automate**, with an
outbound path that CANNOT transmit without a clawgate approval. Phone linked as a secondary
device; messages land in Postgres `signal` schema + attachments in MinIO.

## State now

- **Branches:** devrc `main`, homelab-talos `trunk` (behind 1 — unrelated). Both clean of my
  work; the two dirty devrc files (`flake.lock`, `scripts/opencode/opencode.jsonc`) are
  another session's, do not touch.
- **Six PRs landed**, all merged and deployed:

  | PR | repo | what |
  |---|---|---|
  | #511 | devrc | proposal revision + dispatch brief |
  | #514 | devrc | consumer, DB layer, MinIO, clawgate gate, 387 tests (4 rounds) |
  | #524 | devrc | Dockerfile + build-push + AST dependency test |
  | #331 | homelab | signal-cli-rest-api + `signal` schema |
  | #332 | homelab | signal-cli 0.14.5 → 0.14.7, pinned by digest |
  | #333 | homelab | consumer Deployment (+ scoped MinIO credential) |

- **LIVE and verified at the consumer:** `signal-api` 1/1 (signal-cli **0.14.7**),
  `signal-consumer` 1/1, schema Job Complete (5 tables, 6 indexes). Phone **IS LINKED** —
  `accounts.json` on the PVC holds one LIVE account (was `{"accounts":[]}` before).
- **Consumer is CONNECTED** — its own TCP table shows ESTABLISHED to Postgres `:5432` and
  signal-api `:8080`.
- 🔴 **NOT VERIFIED: nothing has ever been ingested.** All four tables are **0 rows**. Step 7
  (send a real message, watch a row land) is the only thing that verifies any of this and it
  has NOT been done. Everything above is about deploys and connections, not about the
  pipeline working.

## Open investigations — live diagnosis state

### Step 7 has never run — 0 rows, and the zero is NOT yet attributable
- **Symptom:** `select count(*) from signal.messages` → **0**, several minutes after linking,
  with the consumer Running/Ready and connected. Consumer log lines: **0**.
- **Observed (values):** all of `signal.{messages,contacts,groups,attachments,reactions}` = 0.
  `/proc/1/cmdline` = `python3 /app/scripts/signal/consumer.py run`. `PYTHONUNBUFFERED=1` set
  (buffering ruled out). TCP table decoded: `10.244.0.220:34574 → 10.244.2.206:5432
  ESTABLISHED` and `10.244.0.220:51400 → 10.244.2.14:8080 ESTABLISHED`.
- **Ruled out — "the consumer never connected."** I read the absence of a `/v1/receive` line
  in signal-api's gin log as proof it hadn't connected. **WRONG:** gin logs a request when the
  handler RETURNS, and a websocket stays open — which is why an earlier 8-second probe *did*
  appear (`200 | 8.0016s`) once it closed. **Absence of a log line means still-connected.**
- **Ruled out — network.** From the consumer pod: Postgres, signal-api and MinIO all TCP OK.
- **Ruled out — buffered stdout.** `PYTHONUNBUFFERED=1` is set in the image.
- **Leading hypothesis:** nothing has been sent, so there is nothing to ingest. Unproven.
- 🔴 **Next probe (verbatim) — send a Signal message from the phone (an image attachment too,
  to exercise MinIO + the scoped credential), then:**
  ```bash
  export KUBECONFIG=$KC_HOMELAB
  CP=$(kubectl -n signal get pod -l app=signal-consumer -o jsonpath='{.items[0].metadata.name}')
  kubectl -n signal exec $CP -- python3 -c "
  import os,psycopg2; c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
  for t in ('messages','contacts','attachments'):
      cur.execute(f'select count(*) from signal.{t}'); print(t, cur.fetchone()[0])"
  kubectl -n signal logs deploy/signal-consumer --tail=30
  ```

### Does linking backfill conversation history? — UNRESOLVED, and the current zero cannot answer it
- **Question:** does a newly linked device pull existing history?
- **Observed:** 0 rows in every table after linking. **That zero is NOT evidence about
  Signal's behaviour** — the pipeline has never ingested anything, so "no history is sent" and
  "our consumer isn't working" produce an identical empty table.
- **Ruled out:** nothing yet.
- **Leading hypothesis:** forward-only. Signal is E2EE, so the servers hold nothing a new
  device can fetch; any transfer must be pushed by the primary phone during linking. Signal
  Desktop implements such a flow; **whether signal-cli participates is UNVERIFIED** —
  upstream's FAQ does not cover it and the linking wiki page failed to load.
- **Next probe:** settle step 7 first. Once a live message lands, the 0 becomes attributable
  and the answer follows for free.

### Did the 0.14.5 → 0.14.7 bump fix anything? — attribution unproven, do not claim it did
- **Observed:** on 0.14.5 `finishLink` returned `code -3 "Link request error: Connection
  closed! (IOException)"`. On 0.14.7 it returned `code -1 "Link request timed out, please try
  again."` — then succeeded once the scan happened inside the window.
- **What is established:** 0.14.7 changed the **error message**, and that clearer error is
  what exposed the real 120s constraint.
- 🔴 **What is NOT established:** that 0.14.5 could not have linked. PR #332's rationale (an
  outdated client being rejected) is **unproven**; the timing explanation alone accounts for
  every failure. Keep the bump on its own merits (two releases of protocol fixes) — do not
  record it as the fix.

## Next steps (ranked)
1. **Step 7 — send a real Signal message and confirm a row lands.** Nothing else matters until
   this passes; every other claim in this doc is about plumbing, not function.
2. **Add a liveness signal to the consumer.** It serves no HTTP and has **no probes**, so a pod
   reaching nothing stays Running/Ready forever — row count is currently the only health
   signal. This is exactly the failure mode I talked myself into and back out of above.
3. **Move the mutation harness into the repo.** It lives only in a subagent's scratchpad, so
   nobody can re-run or audit it — and it silently stopped testing two guards for a round
   (ANCHOR-MISS). Land it under `scripts/signal/tests/`.
4. **Close `approve_draft`'s read-then-write TOCTOU** — last member of the family whose three
   siblings were fixed in #514 round 4. Benign (two racing approvals yield one row, never a
   duplicate send), but it is the odd one out now.
5. **Bump cadence for the signal-cli image.** Stable lags: `0.100`/`:latest` shipped 0.14.5
   while 0.14.6/0.14.7 existed. Tracking stable ALONE re-breaks linking. Check the CI tags.

## Gotchas / decisions / dead-ends

- 🔴 **`finishLink` has a 120-SECOND window from `startLink`.** Measured: uri written 20:29:01,
  result 20:31:01, exactly 120s. The whole generate→render→send→read→open-Signal→scan loop must
  fit inside it. **Get the camera live FIRST, then generate the QR.** Three link attempts failed
  purely on this.
- 🔴 **The REST wrapper hides link failures completely.** `/v1/qrcodelink` calls `startLink`,
  returns the QR in ~1s, then completes the handshake in a **background goroutine that logs
  NOTHING at any level** — 4 failed attempts, `level=error` count **0**. Go around it: talk to
  the JSON-RPC daemon on `127.0.0.1:6001` inside the pod (`startLink` then `finishLink`) and
  read the real response. That is the only thing that made this diagnosable.
- 🔴 **`/dev/tcp` under `sh` is a FALSE egress failure.** The container's `/bin/sh` is **dash**,
  which has no `/dev/tcp`, so my probe reported FAIL for Signal *and* github alike. The
  uniformity is the tell. Re-test with `curl` — github 200, egress fine.
- 🔴 **Signal's TLS cert is signed by Signal's own CA**, so `curl`/`openssl` report `unable to
  get local issuer certificate` **and that is expected, not a fault** — signal-cli bundles that
  root. A TLSv1.3 handshake presenting `Signal Messenger, LLC` means egress WORKS.
- 🔴 **gin logs on handler RETURN, so an open websocket produces NO log line.** Absence of a
  `/v1/receive` entry means still-connected, the opposite of what it looks like.
- 🔴 **Flux here is two-level.** `flux reconcile kustomization signal` fails `not found` until
  its parent reconciles: the parent is **`homelab`** (path
  `clusters/homelab/flux-system/root-kustomizations/system`). Reconcile `homelab` first.
- 🔴 **`docker manifest inspect` ignores the daemon's insecure-registries** and fails on Harbor
  with an x509 error; `docker pull`/`push` work fine. Use `--insecure` for manifest reads. Do
  not read this as "Harbor is broken".
- 🔴 **The sops age key is `$HOMELAB/.secrets/age.key`** (`SOPS_AGE_KEY_FILE` is *relative* in
  `.envrc`). It is gitignored, so a **worktree does not have it** — pass the base clone's
  absolute path. Same class as the `.envrc` trap, new shape.
- **MinIO credential is SCOPED, not root** (operator decision). User `signal-consumer`, policy
  `signal-attachments-rw`, two statements, one bucket. Action list derived from the code —
  `_minio.py` calls only `put_object`/`stat_object`/`make_bucket`/`bucket_exists`, so there is
  no `DeleteObject` and no wildcard resource. **Both controls watched:** positive (put+stat on
  its own bucket succeeded), negative (denied list+write on **both** other buckets, `archive`
  and `taxes-2026-invoices`). The bucket *count* is recorded so "denied everywhere" cannot be
  confused with "nothing to test against".
- **Harbor pull-by-digest was unexercised in this cluster** before this (16 pods, all
  tag-pinned). It works.
- **The proposal was wrong SIX times** and each correction is in
  `claudedocs/proposal-signal-chat-skill.md` §Corrections: the module targeted two mutually
  exclusive servers (ingest a 404, every send a 400); `UNIQUE` doesn't dedupe over NULL;
  reactions precede their target; attachments had no unique constraint; the sync echo needs an
  identity fix as well as a timestamp one; and the §4 DDL cannot express D3 at all. **There is
  no SSE and no `/api/v1/events`** — ingest is a websocket on `/v1/receive/{number}`.
- **Four audit rounds on #514, and every round but the last found a real defect in the
  PRECEDING fix** — including two regressions the fix round itself introduced (a transient
  fault on `commit` dropping the message while counting it stored; a missing `websocket-client`
  laundered into an infinite reconnect). Budget for it.
- 🔴 **Three separate checks in #514 passed WITHOUT exercising what they claimed** — a guard
  test satisfied by a string both guards' messages contain; a ledger test that re-implemented a
  mini walk instead of calling the real function; a mutant unkillable because a Python
  precondition short-circuited before the SQL predicate under test. Assume the next one exists.
- **My own instruments were broken twice mid-diagnosis**, both giving confident wrong answers:
  the `/dev/tcp` probe above, and a poll that returned instantly because the result file is
  written at `startLink` time so testing for its *existence* proved nothing. Both caught by the
  same tell — a result too uniform or too fast to be real.

## How to verify

```bash
export KUBECONFIG=$KC_HOMELAB
kubectl -n signal get pods
kubectl -n signal exec deploy/signal-api -- signal-cli --version          # expect 0.14.7
kubectl -n signal exec deploy/signal-api -- cat /home/.local/share/signal-cli/data/accounts.json

# THE verification — send a Signal message from the phone first, then:
CP=$(kubectl -n signal get pod -l app=signal-consumer -o jsonpath='{.items[0].metadata.name}')
kubectl -n signal exec $CP -- python3 -c "
import os,psycopg2; c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
for t in ('messages','contacts','attachments'):
    cur.execute(f'select count(*) from signal.{t}'); print(t, cur.fetchone()[0])"

# devrc suite (authoritative; 90 = could-not-vouch, read the log)
scripts/gate.sh --tier pytest
```
