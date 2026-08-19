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

- **Branches:** devrc `main`, homelab-infra `trunk`. Nothing of mine in flight; every PR
  below is merged. The dirty files in either checkout belong to other sessions — do not
  touch. Both repos moved a lot overnight from other work; my changes were re-verified
  present **by content** (not ancestry — a squash merge never makes the branch head an
  ancestor).
- **Shipped and merged:** devrc #537 (outbound-reaction fix), #540 + #546 (liveness
  heartbeat + the build-control pairing), #544 (the `main` gate flake), #528/#538/#548
  (handoffs). homelab-infra `f4454452` (consumer 0.1.2 + emptyDir + heartbeat path +
  probe, all in one commit). Both hosts converged via `ship.sh`.
- **Deployed:** consumer **0.1.2**, digest `7ada2254…`. Image built from a PRISTINE
  worktree at `origin/main` and verified by running the pushed digest, because
  `build-push.sh` uses the repo root as its Docker context and the base clone carries
  other sessions' WIP.

### 🔴 The 16-hour soak — the strongest evidence in this doc

Measured 2026-08-19, ~16h after the 0.1.2 rollout:

| | |
|---|---|
| pod | 1/1 Running, **0 restarts**, 16h |
| probe (`consumer.py health`) | `HEALTHY`, heartbeat 16.5s old, exit 0 |
| heartbeat counters | `connected=True`, `stored=27` — advancing, not frozen |
| messages / contacts / groups | 47 / 12 / 2 (were 25 / 5 / 1) |
| attachments | **19**, across 9 messages (was 1) |
| reactions | 7 (was 2) |

**Zero restarts over 16h is the finding.** The round-1 audit's sharpest question was
whether the probe would report unhealthy for a consumer that is fine — a full disk, a
clock step, scheduling jitter, an idle account — because a false restart loop is worse
than the silence it replaced. Sixteen hours of real traffic with no restart is the answer
that no unit test could give.

- ✅ **The attachment / MinIO leg is verified AT SCALE, not by one sample.** All **19/19**
  objects are present in MinIO with **byte sizes matching the Postgres rows**; 0 missing,
  0 mismatched. Spread over 9 messages, so multi-attachment messages work too.
- ✅ **Inbound reactions work** — 2 → 7 with no intervention.
- 🔴 **The outbound-reaction fix (#537) is STILL UNVERIFIED.** `OWN reactions: 0` after
  16h. No reaction has been sent *from the phone* since it shipped, so the fixed branch
  has still never executed in production. Deployed is not verified, and 16h of unrelated
  green does not touch this claim.
- **Ghost rows: still 1** (the pre-fix row id 3). No NEW ghost appeared across 22 further
  messages — supportive, but NOT proof, since no outbound reaction occurred to exercise
  the path.

## Open investigations — live diagnosis state

### ✅ CLOSED — step 7 ran, and it found a live data-loss defect
- **Resolved:** the leading hypothesis was right — nothing had been sent. Once real messages
  were sent, rows landed with no intervention. The consumer had been working the whole time.
- 🔴 **BUT step 7 was not a formality — it surfaced a real bug review had missed.**
  **Outbound reactions were silently dropped.** A reaction Zach makes on his own phone arrives
  as `syncMessage.sentMessage.reaction` with `message: None`. The sync branch in
  `consumer.py` runs BEFORE the inbound `dataMessage` branch and handled only `remoteDelete`,
  so the reaction fell through to `_base_message()` and produced **exactly the two defects the
  remote-delete case exists to prevent**: the reaction dropped from `signal.reactions`, and a
  bodyless ghost row left in `signal.messages` (that is live row **id 3**).
- 🔴 **How it nearly read as clean — the trap to remember.** Two OTHER group members had
  reacted to the same message, so `signal.reactions` held **2 rows carrying that exact
  `target_sent_timestamp`** and a count check said "fine". Zach's own contact (id 5) appears in
  **none** of them. **Only the reactor IDENTITY discriminates stored from lost here**; the
  count cannot, and the regression test asserts identity for that reason.
- **Fixed** in `fix/signal-outbound-reaction`: a `reaction` case in the sync branch mirroring
  the `remoteDelete` one. Red at `origin/main` (3 failed, `sync_outbound` where `reaction`
  belongs), green at HEAD (400 passed). Five-mutant battery, all KILLED under
  `PYTHONDONTWRITEBYTECODE=1`, including a positive control; the identity mutant dies on its
  own assertion.
- 🔴 **The lost reaction is RECOVERABLE — `raw_envelope` on row id 3 still holds it.** A
  backfill can replay it. Not yet done.

### The attachment / MinIO leg has NEVER run — and the 0 IS attributable
- **Observed:** `attachments` = 0.
- 🔴 **That zero is attributable to INPUT, not to a defect** — I walked all five stored
  envelopes for any key matching `attach`: **none has one**. No attachment has ever been sent,
  so there is nothing to ingest. This is the discriminating check; the bare 0 alone could not
  distinguish "never exercised" from "broken".
- **Consequence:** the scoped MinIO credential and `_minio.py` remain **unexercised by real
  traffic**. Both were verified by hand (positive: put+stat on its own bucket; negative: denied
  on both other buckets) — but never end-to-end.
- **Next probe:** send an image from the phone, then re-check `attachments` and the bucket.

### 🔴 The consumer has emitted ZERO log lines in its entire life
- **Observed:** `kubectl logs deploy/signal-consumer` → **0 lines**, across 20h, while
  successfully ingesting 5 messages, 5 contacts, 1 group and 2 reactions.
- **This is stronger than "no probes"** (next-steps item 2 below): there is no success output
  either, so **row count is the only health signal that exists**. A consumer reaching nothing
  and a consumer working perfectly produce byte-identical logs.

### ✅ ANSWERED — linking does NOT backfill history; the pipeline is forward-only
- **Answer: forward-only**, as hypothesised. The oldest stored row (18:04:57) postdates the
  linking; nothing older than the link ever appeared, and the tables were populated purely by
  traffic sent after it.
- **This became answerable only once step 7 passed** — exactly as this section predicted. With
  a working consumer, the continuing absence of history is now evidence about Signal's
  behaviour rather than about our code.
- **Consequence for the skill:** `signal` can only ever answer questions about messages
  received *since* 2026-08-18. Say so rather than implying full history.

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

1. 🔴 **React to any message from your phone.** One action, ~5 seconds, and it is the only
   unverified claim left in this whole effort. Then run the OWN-reaction check under
   "How to verify". Everything else here is closed or is optional cleanup.
2. **Backfill the one lost reaction and drop its ghost row.** `raw_envelope` on
   `signal.messages` id 3 still holds it. Priced from the CONSUMER, not the writer:
   `list_conversations()` groups a bodyless outbound row with no `dest_contact_id` and no
   `group_id` into its own conversation with `display_name` NULL, and its timestamp sorts
   it to the **top** of the list. User-visible, not cosmetic.
3. **The outbound-`editMessage` ghost — the SAME bug, still open.** Any unmodelled
   `syncMessage.sentMessage` variant with `message: None` (nested `editMessage`,
   `sticker`, `payment`, `groupCallUpdate`) still falls through to `_base_message()` and
   leaves a bodyless ghost row. This is the proof that this pipeline's own lesson —
   *enumerate the other shapes in that wrapper* — is unfinished.
4. **Move the mutation harness into the repo.** Six batteries ran across #537 and #540 and
   every one lived only in a scratchpad that is now deleted. `scripts/signal/tests/`.
5. **Close `approve_draft`'s read-then-write TOCTOU** — last of the family whose three
   siblings were fixed in #514 round 4.
6. **Bump cadence for the signal-cli image.** Stable lags; tracking it ALONE re-breaks
   linking, and re-linking means fighting the 120s window again.

## What #537 established, beyond the fix itself

🔴 **Six PRs, four audit rounds and 387 green tests passed over an entire message
shape being dropped on the floor.** Nothing found it but real traffic. And the sync
branch had ALREADY been patched once for exactly this asymmetry (`remoteDelete`) —
the sibling case was still missed. **When you fix an own-device branch for one
message shape, enumerate the others in that wrapper** (item 4 above is the proof
that lesson is still unfinished).

🔴 **The count/identity trap, worth remembering verbatim.** The missing reaction
nearly read as clean: two OTHER group members had reacted to the same message, so
`signal.reactions` held **2 rows carrying that exact `target_sent_timestamp`** and a
count check said "fine". The account's own contact appeared in none of them. **Only
the reactor IDENTITY discriminates stored from lost; the count cannot.**

🔴 **Four harness defects, each of which reported confident false coverage:**
- a mutant reported **ANCHOR-MISS, not KILLED** — the anchor also matched an
  unrelated branch, so it never landed. A mutant that never ran is not a survivor,
  and it is not a pass either.
- two **operand-order** mutants survived a green 416-test run because every fixture
  set `source` and `sourceNumber` to the SAME value, collapsing both implementations
  into identical output. Not equivalent mutants: signal-cli can put a UUID in
  `source`, so the order decides whether a UUID is written into a phone column.
- the fixture emoji equalled both the corpus value AND the asserted constant, so a
  mutant hardcoding it survived 400 tests while corrupting every stored reaction.
- the **seam**: parser and DB layer were each tested alone and each was clean. The
  🔴 lived exactly between them, invisible until one fixture built both.

🔴 **Consolidation is a bug-finding instrument.** The reaction dict was open-coded at
two sites and that is WHY the sync site shipped without guards the inbound site
already had. Unifying them closed a pre-existing inbound hole in the same change —
and the *dispatch predicate* in front of the helper was still divergent after the
first consolidation, caught only by the delta re-audit. One rule, one place, includes
the `if`.

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

### 🔴 My INSTRUMENTS failed seven times; the code defects were ordinary

Every one produced a confident WRONG answer, and not one was caught by looking harder at
the same evidence — each needed a differently-constructed check. This was the dominant
cost of the session, not the bugs.

- **`nix build … | tail` returns TAIL's exit status.** Reported `exit 0` over a genuinely
  RED gate. The repo documents this; knowing it did not stop me. Capture the status
  directly (`cmd > log 2>&1; echo $?`) and read the `RESULT:` line.
- **`nix build --rebuild` on a never-built derivation exits 1 with "some outputs are not
  valid, so checking is not possible"** — that is "cannot check", NOT "tests failed". As a
  control it would have proven `main` red when `main` was green.
- **A mutation anchor that matched TWO sites reported ANCHOR-MISS, not KILLED** — the
  mutant never landed. A mutant that never ran is not a survivor and is not a pass.
- **Operand-order mutants survived because every fixture set both fields to the SAME
  value**, collapsing the two implementations into identical output.
- **A fixture emoji equal to the constant its own assertion named** let a hardcoding
  mutant survive 400 tests while corrupting every stored reaction.
- **An autouse fixture cannot unset env parsed at IMPORT time.** The hermetic-env fixture
  was completely inert while its docstring claimed it had been measured. The false claim
  is the dangerous half. Pop the vars at conftest MODULE scope, above every import.
- **A regression test green by TIMING LUCK.** It asserted on a counter that only
  increments on success, so once the sink was broken it could never grow; it passed
  because a beat was in flight when the path was swapped. 5/60 red on an idle machine.

### 🔴 A guard that only a BUILD can check is a declaration, not an invariant
`build-push.sh` control 3 pins the CLI's subcommand set and refuses to push on any
difference — correct, and it caught `health`. But nothing kept that list in sync with the
parser, so the gate was green, #540 merged, and the mismatch surfaced only at BUILD time,
after the decision point. #546 adds a two-way pin at the gate, with a positive control,
because two empty sets compare equal.

### The gate could not run at all for ~1h, and that is an environment fact worth knowing
The workbench sat at **load 138–146** from other sessions. `nix build` takes >10 min
there; the Bash tool caps foreground at 600s and the harness kills tracked background
tasks. **The workaround that works: `setsid nohup … &` writing to a log**, then poll the
log — a detached process survives where a tracked one is reaped. Also seen: 43 accumulated
worktrees, and a `stash`-named process 7h old. Nothing was killed — at that granularity a
sibling agent's live work is indistinguishable from an orphan.

### Deploy pairing, and why it is four things in ONE commit
`readOnlyRootFilesystem: true` + no volume + a SYNCHRONOUS first beat = CrashLoopBackOff
on the first rebuild, **before any probe exists**. So image digest, `emptyDir`,
`SIGNAL_HEARTBEAT_PATH` and the probe must land together; the manifest's own comment had
recorded that this pod "writes nothing to the filesystem, which is what makes
readOnlyRootFilesystem viable", and that invariant is now false with exactly one exception.

### No migration exists for `signal.consumer_health`, and `ensure_schema()` will not say so
`CREATE TABLE IF NOT EXISTS` is silent about a table that already exists with the WRONG
types. Against a database that ever ran the pre-release TIMESTAMPTZ build, every beat
fails forever while the FILE probe stays green — nothing restarts, nothing alerts.
**Verified NOT to affect the live homelab DB** (the table did not exist, so it was born
`bigint`). Recovery elsewhere: `DROP TABLE signal.consumer_health;` — it is a single
disposable status row.

## How to verify

```bash
export KUBECONFIG=$KC_HOMELAB
CP=$(kubectl -n signal get pod -l app=signal-consumer -o jsonpath='{.items[0].metadata.name}')

# 1. Liveness — the probe exactly as k8s runs it. Exit 0 = healthy.
kubectl -n signal exec $CP -- python3 /app/scripts/signal/consumer.py health
kubectl -n signal exec $CP -- python3 /app/scripts/signal/consumer.py health --from-db
kubectl -n signal get pods -l app=signal-consumer     # RESTARTS must stay 0

# 2. 🔴 THE OUTSTANDING CHECK — react to a message FROM THE PHONE first, then:
#    A COUNT CANNOT ANSWER THIS. Other people's reactions on the same target are
#    already in the table; only the REACTOR IDENTITY separates stored from lost.
kubectl -n signal exec $CP -- python3 -c "
import os,json,psycopg2
c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
cur.execute('select raw_envelope from signal.messages where is_outbound order by id desc limit 1')
own=json.loads(cur.fetchone()[0]).get('sourceUuid') if 0 else None
cur.execute('select raw_envelope from signal.messages where is_outbound order by id desc limit 1')
r=cur.fetchone()[0]; d=json.loads(r) if isinstance(r,str) else r
cur.execute('select id from signal.contacts where signal_uuid=%s',(d.get('sourceUuid'),))
o=cur.fetchone()
cur.execute('select count(*) from signal.reactions where contact_id=%s',(o[0] if o else -1,))
n=cur.fetchone()[0]
print('OWN reactions:', n, '->', 'FIX VERIFIED LIVE' if n else 'NOT YET')"

# 3. Attachment integrity — every row's object must exist AND match its size.
kubectl -n signal exec $CP -- python3 -c "
import os,sys,psycopg2; sys.path.insert(0,'/app/scripts/signal')
import _minio
c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
cur.execute('select minio_bucket, minio_key, size_bytes from signal.attachments')
rows=cur.fetchall(); ok=0
with _minio.MinioSignal() as m:
    for b,k,sz in rows:
        try: ok += 1 if m.client.stat_object(b,k).size==sz else 0
        except Exception: pass
print('objects present AND size-matching:', ok, '/', len(rows))"

# 4. Ghost rows — bodyless outbound with no attachment. Expect 1 (the pre-fix row).
#    Any INCREASE means the reaction fix regressed.
kubectl -n signal exec $CP -- python3 -c "
import os,psycopg2
c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
cur.execute('''select count(*) from signal.messages m where m.is_outbound and m.body is null
 and not exists (select 1 from signal.attachments a where a.message_id=m.id)''')
print('ghost rows:', cur.fetchone()[0])"
```

```bash
# devrc gate — AUTHORITATIVE, and read the CONTENT not the exit code.
cd ~/workspace/devrc && nix build .#checks.x86_64-linux.pytests --no-link; echo "EXIT: $?"
nix log /nix/store/<drv> | grep -E "^  FAIL|TOTAL|^RESULT"
```
