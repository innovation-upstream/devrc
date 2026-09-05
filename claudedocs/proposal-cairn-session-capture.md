# Proposal: session capture attached to cairn entries — 2026-09-05

**Status: PROPOSED, NOTHING BUILT.** No code, no bucket, no credential, no front-matter
field. This is the argument and the shape; the decisions in §2 are settled and are not to
be re-litigated, and everything in §9 is still open.

🔴 **This repo is PUBLIC and the store is client-confidential.** No scope name, entry name,
bucket name, endpoint or credential appears below. Scopes are referred to by role. The
implementation prints real names at run time on the operator's terminal — deliberately the
only place they appear. This follows `plan-cairn-phase1-cutover.md`, which set the
precedent.

---

## 1. What this is

When a session produces a handoff, ship that session's transcript to object storage and
attach it to the cairn entries the session touched, so a future reader of an entry can
reach the receipts behind a bullet rather than re-deriving them.

The job cairn does is **per-person agent re-entry**. Today an entry carries the conclusion;
the evidence that produced it dies with the session. This closes that.

## 2. Decisions already taken — do not re-litigate

Settled by the operator across two rounds of questions on 2026-09-05:

| # | decision | consequence accepted |
|---|---|---|
| 1 | **Full forensic record** — the whole transcript, not a tail | real object storage, not an inline JSON field |
| 2 | **Cairn-owned shipper, triggered per session** at handoff time — not the 5-minute timer | the operator controls what ships; volume collapses to sessions that produced a handoff |
| 3 | **Pointer in cairn, bytes in the homelab MinIO tenant** | entries stay small and renderable; the store's third-party rule is satisfied |
| 4 | **Ship raw** — no redaction; the storage boundary is the control | every credential ever echoed in a captured session lands in the bucket |
| 5 | **Handoff ships, `SessionEnd` re-ships** | the only arrangement under which "always the whole transcript" is literally true |
| 6 | **Pointer on every cairn entry the session touched** | one session fans out across N entries and M scopes |
| 7 | **Push failure warns; handoff still succeeds** | a handoff can exist with no session attached, and must say so |
| 8 | **Indefinite retention** | the bucket accumulates unredacted content permanently |

Decisions 4 and 8 compound: the bucket is a permanent, unredacted secrets-grade asset.
That is the operator's call, made explicitly. What follows from it is §6 — the boundary
has to be built like it is the only control, because it is.

## 3. Why not the shipper that already exists

`scripts/transcript-push.sh` + `scripts/lib/build_transcript_push.py` (#1310, rank 25) push
Claude Code transcripts to clawgate on a 5-minute timer. It is good code and it solved the
hard parts — a byte-boundary-safe tail with the leading partial JSON line dropped,
`truncated` as a first-class field, a sha256 digest so unchanged sessions are not re-sent,
and a distinct exit code per failure condition.

It is nonetheless **the wrong instrument here, and the reasons are structural, not stylistic**:

- it ships a **192 KiB tail**; decision 1 wants whole files up to 23 MB;
- it is **ambient and periodic**; decision 2 wants an explicit per-session act;
- it posts the payload **inline in JSON** to an HTTP endpoint; decision 3 wants bytes in
  object storage and only a pointer in the API.

🔴 **This is a real duplication and it should be named rather than hidden.** Two shippers
will hold two answers to "which sessions matter, and how much of each", and those answers
will drift — the exact shape `claude/RULES.md` calls out as regenerating the same bug at
every site. The mitigation is not to merge them (their triggers genuinely differ) but to
**share the parts that are one rule**: transcript discovery, `project_of`, and the
byte-boundary logic belong in one module both call. If that extraction is not done, expect
the two to disagree about which file belongs to which project within a few months.

## 4. Volume — measured, not estimated

Measured on the workbench, 2026-09-05:

| | |
|---|---|
| Claude Code transcripts | **6.6 GB**, **5,840** `.jsonl` files |
| median file | **739 KB** |
| p90 | **2.5 MB** |
| largest single file | **23 MB** |
| opencode session data | **2.4 GB** — a SQLite DB plus `storage/`, `snapshot/`, `tool-output/` |

The per-handoff trigger is what makes decision 1 affordable: the population that ships is
sessions-that-produced-a-handoff, not all 5,840. At the median that is well under a
megabyte per handoff.

🔴 **opencode is a separate project, not a flag on this one.** It has no `.jsonl` — "the
whole session" there means an exporter with its own fidelity question (which tables, what
about `tool-output/`, is a DB snapshot a session). Ship Claude first; §9 keeps opencode
open. **Name the front-matter field so it does not assume jsonl.**

## 5. The design

### 5.1 Two triggers, one object

Handoff ships the transcript as it stands. A `SessionEnd` hook overwrites it with the final
bytes. (`SessionEnd` is already a wired hook event on this host — verified, not assumed.)

- The handoff-time object is the **floor**. `SessionEnd` does not fire on a crash or a
  kill, so without it a crashed session would attach nothing at all.
- The object is therefore **at least as complete as the handoff moment**. Write that down
  where a reader will see it: a short object is not corruption.
- **Overwrite must be idempotent**, keyed on session id. Handoff can run several times in
  one session — the skill's own docs warn that re-running appends findings twice — and each
  run must land on the same key.

### 5.2 Transport

Host → object storage **directly over the mesh**. Not through the cairn pod: it is
single-replica, `Recreate`, PVC-backed, and designed to render markdown. A 23 MB PUT
through it is a category change, not a feature.

### 5.3 What cairn holds — and the one change I recommend against the letter of decision 6

Front-matter on each touched entry gains a session reference carrying `id`, `sha256`,
`bytes`, `captured_at`, `host`.

🔴 **RECOMMENDATION: the pointer is an OPAQUE ID, never a resolvable URL** — no bucket, no
path, no endpoint in the entry. Resolving it requires the transcript credential, which is
separate from the cairn read token by construction (decision 3 already put them apart).

The reason is decision 6 meeting a decision the programme already took.
`plan-cairn-integration.md` decision 5 reads: *"Cross-tenant reads — opt-in sharing per
scope or entry — makes tenancy a security boundary, not a cache key."* A session routinely
touches this repo's scope and a client scope in the same run. Fanning the pointer out means
a `sensitivity: client-confidential` entry carries a reference to an object that also holds
that session's work on **other** clients, retained forever. The moment entry-level sharing
exists, sharing that entry shares that reference.

An opaque id keeps decision 6 exactly as chosen while making the shared artifact an
**identifier, not content**. It costs nothing and it is reversible; a URL baked into N
entries is not.

🔴 **An unauthorized resolve must render as NOT AUTHORIZED, never as missing.** Those two
are the same observable otherwise, and an empty result cannot distinguish two mechanisms —
a reader would read "this session was never captured" off a permission error.

### 5.4 The digest ledger is not optional

`bytes` + `sha256` + `captured_at` + `host` alongside the id. Without them a pointer to a
missing, truncated or superseded object reads **identically** to a healthy one. It is also
the shipper's own positive control: a run that shipped and a run that shipped nothing must
be distinguishable in its output, and a reassuring zero is otherwise indistinguishable from
a shipper wired to nothing.

### 5.5 Failure semantics

Fail **open**. A failed push prints the reason and the exact re-push command; the handoff
still succeeds and the doc records that no session was attached. Handoff's job is the doc
and the entries — the transcript is an attachment, and a heavily-used skill must not become
dependent on the mesh being up.

🔴 **"Warns" is a claim about the WRITE, and the write is not where this fails.** The
failure to design for is the silent one: a push that reports success while writing nothing,
or writing to the wrong key. That is what §8 exists for.

## 6. The boundary is the only control, so build it like one

Decision 4 removed redaction from the design. Everything that would have been defence in
depth now rests on storage. Concretely, the implementation must:

- use its **own bucket and its own credential** — not the archive tenant's backup
  credential, which exists for a different blast radius;
- **never print the credential.** Write it to a `0600` file and print the *path*.
  `ZacxDev/homelab-infra#683` fixed exactly this defect in the sibling provisioning script:
  it printed the secret to stdout, and it is run by agents, so every run re-staged a
  transcript-capture incident that had already forced one rotation;
- carry **no anonymous access policy**, proven by an **unauthenticated GET as a control**
  rather than by reading the policy JSON;
- keep the **cairn read token unable to reach the bucket**, and vice versa. Decision 3 puts
  them apart; a test should pin it rather than a comment asserting it.

🔴 **You currently have NO detector for a credential landing in a transcript.** Redaction
was declined and that is settled — but a **report-only** scan over the bytes being shipped
blocks nothing, costs one pass over data already being read, and turns an invisible event
into a number. With indefinite retention, a secret that lands there is there forever. **Not
v1; recommended as the first follow-on.**

## 7. Explicitly out of scope

- **opencode capture** — §4. Different extraction, different fidelity question.
- **Redaction of any kind** — decision 4.
- **Anything in `ZacxDev/cairn`.** The OSS extraction deliberately removed `cairn who`
  because session forensics on named hosts *"is not an operation on a store"*. Whole-session
  capture sits further across that same line. This is devrc-side, private, and stays there.
- **Retiring the clawgate feeder.** It serves a different consumer and keeps running.

## 8. How the implementation must prove itself

Not "the tests pass" — these specific controls, because each names a way this silently
does nothing:

1. **Positive control on the shipper.** A session that MUST produce an object: watch the
   count move from 0 to 1 and report the pair, never the zero alone.
2. **Negative control.** Point it at an unreachable endpoint and watch it warn and exit
   non-zero rather than reporting a successful no-op.
3. **Idempotency, measured.** Two handoff runs in one session → **one** object, **one**
   pointer per entry. Assert the set does not grow.
4. **The `SessionEnd` overwrite actually overwrites.** Ship at handoff, append to the
   session, fire `SessionEnd`, assert the stored `sha256` **changed** and the pointer did
   not duplicate.
5. **A crash path attaches the floor.** Kill a session without `SessionEnd`; the
   handoff-time object must still be there and its digest must match what was shipped.
6. **Anonymous GET against the bucket → denied.** The control for §6, run against the real
   bucket, not a fixture.
7. **The cross-credential separation**, both directions.

🔴 Every one of these must be watched to FAIL before it is trusted. A guard pinning an
invariant the bug never violated is an invariant guard and must be labelled as one.

## 9. Open — decide before building

1. **Does the pointer-shape recommendation in §5.3 stand?** It is the one place this
   proposal argues against the letter of a settled decision, and it is cheap to accept and
   expensive to retrofit.
2. **What is the front-matter field called**, and does the subsystem-index skill or the
   handoff tool own writing it? The handoff doc already carries `clawgate-task:` as
   precedent for a linkage field.
3. **Which entries count as "touched"?** The handoff run knows what it wrote; whether that
   set is the right one, or too wide, has not been measured.
4. **opencode** — schedule, or park indefinitely.
5. **Does the shared-module extraction in §3 happen now or later?** Later is defensible;
   never is how the two shippers drift.
