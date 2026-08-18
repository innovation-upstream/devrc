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
- ✅ **STEP 7 PASSED — 2026-08-18.** Real phone traffic lands in Postgres. Measured at 19:21
  UTC: `messages` 5, `contacts` 5, `groups` 1, `reactions` 2, `attachments` 0. The five
  messages span 18:04:57–18:48:28 UTC and cover **both directions** — an inbound group
  message, an inbound 1:1, and three `sync_outbound` echoes of messages sent from the phone.
  Reaction→target linkage resolves correctly (both reaction rows point at message 1).
  🔴 **Still NOT verified: the attachment/MinIO leg** (see below) and there are **no probes**.
- ✅ **The outbound-reaction defect step 7 found is FIXED, MERGED and DEPLOYED** — devrc
  #537 (squashed `8f2cedd`), consumer **0.1.1** digest `a3ef7385…`, homelab-infra
  `897e837d`. 🔴 **Deployed, NOT verified in function** — see next steps item 1.

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

🔴 **BOTH REMAINING ITEMS NEED ZACH AT HIS PHONE — no agent can do them.**

1. **Verify the reaction fix LIVE. React to any message from your phone, then run
   the check below.** The fix is merged (devrc #537) and DEPLOYED (consumer 0.1.1,
   digest `a3ef7385…`, rolled out 2026-08-18 20:5x UTC, pod reconnected to
   signal-api + Postgres). 🔴 **That is a claim about the DEPLOY, not about the
   FUNCTION.** Nothing has exercised the fixed path in production — the same
   distinction that made step 7 necessary in the first place. Until a reaction
   you sent appears in `signal.reactions` with YOUR contact id, this is unverified.
2. **Send an IMAGE from the phone** — the attachment/MinIO leg has still never
   carried real traffic (`attachments` = 0, attributable to input: no stored
   envelope contains an attachment key). This exercises `_minio.py` and the
   scoped `signal-consumer` credential end to end.
3. **Backfill the one lost reaction and drop its ghost row.** `raw_envelope` on
   `signal.messages` id 3 still holds the reaction; the fix stops NEW ghosts but
   removes no existing one. Priced from the consumer, not the writer:
   `list_conversations()` groups a bodyless outbound row with no `dest_contact_id`
   and no `group_id` into **its own conversation with `display_name` NULL**, and
   its timestamp sorts it to the **TOP** of the list. So it is user-visible, not
   cosmetic.
4. **The outbound-`editMessage` ghost — the SAME bug, still open.** Any unmodelled
   `syncMessage.sentMessage` variant with `message: None` (nested `editMessage`,
   `sticker`, `payment`, `groupCallUpdate`) still falls through to `_base_message()`
   and leaves a bodyless ghost row. This is the next instance of this pipeline's own
   lesson, found by the round-1 audit and deliberately left out of #537's scope.
5. **Add a liveness signal to the consumer.** Evidenced, not theorised: **0 log
   lines in 20h** of successful ingestion. No HTTP, no probes — a pod reaching
   nothing stays Running/Ready forever, and row count is the only health signal
   that exists. This is the failure mode the whole step-7 diagnosis talked itself
   into and back out of.
6. **Move the mutation harness into the repo.** Three batteries were run across
   #537 and all of them live only in scratchpads. Land under `scripts/signal/tests/`.
7. **Close `approve_draft`'s read-then-write TOCTOU** — last of the family whose
   three siblings were fixed in #514 round 4.
8. **Bump cadence for the signal-cli image.** Stable lags: `0.100`/`:latest` shipped
   0.14.5 while 0.14.6/0.14.7 existed. Tracking stable ALONE re-breaks linking.

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

## How to verify

```bash
export KUBECONFIG=$KC_HOMELAB
kubectl -n signal get pods
kubectl -n signal exec deploy/signal-api -- signal-cli --version          # expect 0.14.7
kubectl -n signal exec deploy/signal-api -- cat /home/.local/share/signal-cli/data/accounts.json

# 🔴 THE OUTSTANDING CHECK — react to a message FROM YOUR PHONE, then run this.
# It must print a row whose reactor is YOUR OWN contact. A count alone cannot
# answer it: other people's reactions on the same target are already in there.
kubectl -n signal exec $CP -- python3 -c "
import os,psycopg2; c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
cur.execute('''select r.id, r.emoji, r.is_remove, (r.message_id is not null) resolved
  from signal.reactions r join signal.contacts k on k.id = r.contact_id
  where k.signal_uuid = %s''', (OWN_UUID,))
rows = cur.fetchall()
print('OWN reactions stored:', len(rows))
for r in rows: print('  ', r)
print('VERDICT:', 'FIX VERIFIED LIVE' if rows else 'NOT YET - react from the phone')"
# (OWN_UUID = the account's own signal_uuid; read it from any sync_outbound row's
#  raw_envelope ->> 'sourceUuid'.)

# Row counts (step 7 PASSED 2026-08-18 — these are now NON-ZERO; a zero here is a REGRESSION):
CP=$(kubectl -n signal get pod -l app=signal-consumer -o jsonpath='{.items[0].metadata.name}')
kubectl -n signal exec $CP -- python3 -c "
import os,psycopg2; c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
for t in ('messages','contacts','attachments'):
    cur.execute(f'select count(*) from signal.{t}'); print(t, cur.fetchone()[0])"

# devrc suite (authoritative; 90 = could-not-vouch, read the log)
scripts/gate.sh --tier pytest

# The attachment leg — STILL UNVERIFIED. Send an IMAGE from the phone, then:
kubectl -n signal exec $CP -- python3 -c "
import os,psycopg2; c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
cur.execute('select count(*) from signal.attachments'); print('attachments', cur.fetchone()[0])"

# Is a zero attributable to INPUT rather than a defect? Walk the envelopes for an
# attachment key -- a bare 0 cannot tell "never exercised" from "broken":
kubectl -n signal exec $CP -- python3 -c "
import os,json,psycopg2; c=psycopg2.connect(os.environ['SIGNAL_PG_DSN']); cur=c.cursor()
cur.execute('select id, raw_envelope from signal.messages')
for mid, env in cur.fetchall():
    d = json.loads(env) if isinstance(env,str) else env
    print(mid, 'attach' in json.dumps(d).lower())"
```
