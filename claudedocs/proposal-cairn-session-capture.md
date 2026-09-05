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
| 5 | **Handoff ships, `SessionEnd` re-ships** | the whole transcript on every CLEAN exit; the handoff-time floor on a crash or kill — see §5.1, and note `SessionEnd` is a PER-HOST operator act that is not wired on both hosts today |
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

- it ships a **192 KiB tail**; decision 1 wants whole files, up to 23.48 MB — §4 row 1's max,
  i.e. the largest single transcript FILE on the host;
- it is **ambient and periodic**; decision 2 wants an explicit per-session act;
- it posts the payload **inline in JSON** to an HTTP endpoint; decision 3 wants bytes in
  object storage and only a pointer in the API.

🔴 **This is a real duplication and it should be named rather than hidden.** Two shippers
will hold two answers to "which sessions matter", and those answers will drift — the exact
shape `claude/RULES.md` calls out as regenerating the same bug at every site. The mitigation
is not to merge them (their triggers genuinely differ) but to **share the parts that are one
rule**.

**The one clearly-shareable surface is `project_of`** (`build_transcript_push.py`), which maps
a transcript to its project label. It exists at exactly one site, is **not** in the shared
discovery module, and its own docstring warns that it is *"a LABEL, NOT AN IDENTIFIER"* that
must not be un-slugified. A second shipper needing a project label for object metadata or a
key prefix would hand-roll it in four lines, and the two copies would disagree the first time
a directory name contains a literal `-`. ⚠ An earlier revision of this section deleted this
sentence as collateral while rewriting the paragraph below, leaving the section with no
positive advice at all — which reads as "nothing is safe to share" and is the opposite of what
was found.

**Not the byte-boundary logic** — it exists to drop the leading partial JSON record from a
*tail*, and decision 1 ships whole files, so the new shipper has no caller for it.

🔴 **AND TRANSCRIPT DISCOVERY IS ALREADY SHARED — REUSING IT IS A DECISION, NOT A FREEBIE,
AND IT ANSWERS §9.6 IN THE OPPOSITE DIRECTION.** `scripts/lib/transcript_search.py` already
exposes `iter_transcripts` / `is_corpus_member`; the existing feeder imports it, and
`test_transcript_search.py`'s two-way site ledger scans the tree so a fourth hand-rolled walk
cannot pass unseen — i.e. the repo actively rewards reuse. But that module carries
`EXCLUDED_DIR_NAMES = ("subagents",)` and `is_corpus_member` rejects anything beneath it.
Measured 2026-09-05: the exclusion and the `agent-*` prefix **coincide exactly** — 4,955 of
4,955 `agent-*` files are under a `subagents/` directory and 0 of 913 real-session files are.

So an implementer who takes "share discovery" at face value, reuses `iter_transcripts` — the
correct-looking move, and the one the ledger test pushes toward — **silently ships parent
transcripts only**, which is precisely the "drops most of the evidence this proposal exists to
preserve, while looking complete" outcome §5.0 names. The ledger test stays green throughout,
because reusing the shared walk is what it exists to reward. **If §9.6 resolves to "ship the
set", the shared walk must be widened or deliberately bypassed, and that is a change to a
module with an enforced site ledger — not a free adoption.**

## 4. Volume — measured, not estimated

🔴 **AN EARLIER REVISION OF THIS SECTION MEASURED THE WRONG POPULATION AND ITS CONCLUSION
WAS WRONG BY ~5×.** It reported a median of 739 KB and concluded "well under a megabyte per
handoff". That median is real but it is the median of **all** `.jsonl` files, and **84% of
those are `agent-*.jsonl` subagent transcripts** this design does not ship. The arithmetic
was right; the population was wrong. Kept here rather than silently corrected, because the
error is the instructive part: **a percentile is a claim about a population, so name the
population or the number means nothing.**

Measured on the workbench, 2026-09-05. Four populations, because the answer spans ~12×
between them — and which one applies is decided by a question §9 has not closed:

| population | files | median | p90 | max | total |
|---|---|---|---|---|---|
| all `.jsonl` — **NOT what ships** | 5,864 | 0.74 MB | 2.53 MB | 23.48 MB | 6.73 GB |
| real sessions (excluding `agent-*`) | 913 | **2.62 MB** | 4.97 MB | 23.48 MB | 2.45 GB |
| **sessions that produced a handoff** — what decision 2 selects, **parent file only** | 37 | **3.82 MB** | 5.22 MB | 10.59 MB | 148 MB |
| the same 37 sessions, **parent + their subagent transcripts** | 288 files / 37 sessions | **8.68 MB** *(per session)* | 20.23 MB | 31.00 MB *(per session)* | 375 MB |

⚠ Every percentile in this table uses one convention — the sorted value at `int(n × 0.9)`,
no interpolation. Stated because a different convention gives a visibly different p90 for
row 4 (16.65 MB rather than 20.23 MB) and nothing in the table would show which was used.

Rows 3 and 4 are the population decision 2 selects, counted two ways — see the next
paragraph but one for which of them to size off. Both were derived from the
`Claude-Session-Id` trailer on commits touching `claudedocs/handoff-*.md` since 2026-07-01:
37 distinct ids, **all 37** resolving to a transcript on disk, none of them an `agent-*`
file.

🔴 **The selection effect runs the WRONG WAY, and that is the point of the row.** Sessions
that produce a handoff are the long ones, so the per-handoff filter picks the **large tail**,
not the median — 3.82 MB (row 3) against 2.62 MB (row 2) against 0.74 MB (row 1).

🔴 **AND THE ROW YOU SIZE OFF DEPENDS ON AN ANSWER §9.6 HAS NOT GIVEN YET.** Row 3 is the
parent transcript alone. If §9.6 resolves to "ship the set" (§5.0 frames it), row 4 is the
real unit — **2.3× the median and 2.5× the total**, with a largest single session of
**31.00 MB**.
⚠ **That 31.00 MB is a SESSION TOTAL, not an object size** — under "ship the set" it
arrives as many PUTs (the 37 sessions hold 288 files, mean object ~1.3 MB), and the largest
single *file* on the host is 23.48 MB, row 1's max. So it does not describe a bigger PUT
than §5.2's worked example; it describes more of them.

⚠ **Per-object sizing: row 1's 23.48 MB is a deliberate OVER-estimate, and here is why it is
not simply the wrong row.** The largest single file among the 37 selected sessions is
**10.59 MB** — 2.2× smaller — so nothing shippable today approaches 23.48 MB, and row 1 is
the row labelled *NOT what ships*. The reason to size above the selected maximum anyway is
§4's own coverage caveat below: the selection is devrc-only and **the true population is
larger**, so a session with a 23 MB parent can enter it without anything else changing. Size
a per-object limit off row 1's max for that headroom, a per-handoff cost off row 4's total,
and do not cross the two — but do not claim 23.48 MB is what ships today, because it is not.
33 of the 37 sessions have subagent bytes at all, and the worst **within those 37** is a
3.82 MB parent with 19 subagent files totalling 26.56 MB — a 30.39 MB session of which
shipping one object captures **12.6%**. Two further reasons both rows are floors:
`SessionEnd` re-ship only grows them, and the coverage caveat two paragraphs below is real.

⚠ **An earlier revision quoted a worse-looking example here — a 3.79 MB parent with 60
subagent files, 8% captured — and that session is NOT one of the 37.** It is from the
913-session population, and its 46.99 MB total contradicted the 31.00 MB row-4 max stated
three lines above it. Recorded rather than quietly swapped, because the mistake is this
section's own thesis: **a figure imported from a different population is wrong even when the
figure itself is exact.**

🔴 **AND THE FIRST DRAFT OF THIS VERY RETRACTION RE-IMPORTED THE SAME NUMBER, ONE LEVEL UP.**
It closed with *"the largest sessions on the whole host do reach ~47 MB"* — which is 46.99 MB,
the total of the session being retracted, promoted from "an example that does not belong here"
to "the host maximum". Measured: the host maximum is **94.02 MB** (an 11.72 MB parent plus
82.30 MB across 29 subagent transcripts) and **9 sessions exceed 47 MB**. A reader sizing for
the widened population §4's own caveat anticipates would have been **2× low**, from a sentence
written to warn against exactly that. Recorded because three separate revisions of this one
paragraph made the same mistake in three different places: **retracting a number is not the
same as not using it.**

**So: size off row 4 unless and until §9.6 resolves to parent-only.** Sizing off row 3 while
§9.6 is open is how a bucket, a PUT timeout or a per-handoff cost estimate comes out low by
more than 2×.

⚠ **This table is a SNAPSHOT of a population that GROWS AS THE SYSTEM IS USED**, the same
caveat §5.0 carries for the corpus counts — every new handoff adds a session to it. Only the
coverage caveat below was stated originally, and coverage and time are different limits.
Re-measured 2026-09-05 after a day's work: still **37**, so no drift was observed — but the
method is time-dependent by construction, and a later re-derivation returning a different `n`
means the table is **stale, not wrong**. ⚠ One audit reading of this reported 43; it did not
reproduce here under the stated method, which is itself the reason to state the method.

⚠ **Scope of the 37-session sample, stated rather than buried:** devrc handoffs only, and
only commits carrying the trailer. Handoffs in other repos are not counted, so the true
population is larger and the totals are floors.

**opencode**, for scale only: **2.4 GB** — a SQLite DB plus `storage/`, `snapshot/` and
`tool-output/`. Not comparable to the rows above and not summed with them.

⚠ The `6.73 GB` in row 1 is the `.jsonl` population summed in decimal bytes. A `du -sh` of the
directory reads `6.6 GB` because it is GiB and includes ~7,000 non-`.jsonl` files. Two
different measurements; do not pair them.

🔴 **opencode is a separate project, not a flag on this one.** It has no `.jsonl` — "the
whole session" there means an exporter with its own fidelity question (which tables, what
about `tool-output/`, is a DB snapshot a session). Ship Claude first; §9 keeps opencode
open. **Name the front-matter field so it does not assume jsonl.**

## 5. The design

### 5.0 🔴 "A session's transcript" is a SET, not a file — and this is OPEN

**This is the first thing an implementer needs and the proposal does not yet answer it.**

On this host a session writes **several** transcripts: the parent `<session-id>.jsonl` plus
one `agent-<hash>.jsonl` per dispatched subagent. Measured: **4,951 of 5,864** files are
`agent-*`, holding **4.28 GB of 6.73 GB — 64% of the bytes**.

⚠ **These counts are a SNAPSHOT of a live corpus and they drift within a session** — §3
quotes 4,955 and this section 4,951, taken minutes apart on the same day, and a later
reading gave 4,957. The durable claims are the **ratio** (~85% of files, ~64% of bytes) and
the **exact coincidence** in §3; the absolute counts are not, and nothing pins them.
(The ratio is 84.4% of files — quoted as "84%" above; do not read the two as different
measurements.)

Those files are not addressable the way the parent is. `build_transcript_push.py` states the
convention the whole join rests on — *"The session id IS the filename stem … the same id the
attention queue and session-manager's `claude_session_id` carry, which is what makes the join
work at all."* For an `agent-*.jsonl` the stem is `agent-<hash>`, which is **not** a session
id. Sampled 6 subagent transcripts: in every one the stem is absent from the `sessionId`
values inside the file, and the single `sessionId` present is the **parent's**.

So keying the object "on session id" — which is what §5.1 said before it was rewritten to
defer to this section — has two readings, and neither is safe by default:

- key on the **in-record `sessionId`** → parent and all N subagent files collide on one
  object key, each push overwriting the last. §8 control 4 ("assert the sha256 changed")
  would pass while the object is simply the last writer;
- key on the **filename stem** → subagent objects are keyed `agent-<hash>`, joinable to no
  cairn entry, no attention-queue row and no `session-manager` view.

🔴 **And this is where the goal in §1 is won or lost.** `CLAUDE.md` records that the operator
works **entirely via agents**. The receipts — the tool calls, the files read, the commands
run — are in the subagent transcripts. The parent carries only each subagent's final report.
Shipping "the session's transcript" as one file therefore drops most of the evidence this
proposal exists to preserve, while looking complete.

**Decide before implementing (§9.6):** does a session ship as one object or a set; and if a
set, what is the key that keeps the subagent objects joinable to the parent.

### 5.1 Two triggers, one object per transcript

Handoff ships the transcript as it stands. A `SessionEnd` hook overwrites it with the final
bytes.

- The handoff-time object is the **floor**. `SessionEnd` does not fire on a crash or a
  kill, so without it a crashed session would attach nothing at all. **So decision 5 buys
  "the whole transcript on a clean exit", not "always"** — §2 records it that way.
- The object is therefore **at least as complete as the handoff moment**. Write that down
  where a reader will see it: a short object is not corruption.
- **Overwrite must be idempotent**, on whatever key §5.0 resolves to. Handoff can run
  several times in one session — the skill's own docs warn that re-running appends findings
  twice — and each run must land on the same key.

🔴 **`SessionEnd` is a PER-HOST OPERATOR ACT, and it is wired on ONE host today.** Measured
2026-09-05: the workbench's `~/.claude/settings.json` registers `SessionEnd`; the laptop's
does not (`PermissionRequest`, `PostToolUse`, `PreToolUse`, `SessionStart`, `Stop`,
`SubagentStop`, `UserPromptSubmit` — no `SessionEnd`). That file is per-host and **unmanaged
by nix** by design, and `drift-check.sh` rc 15 compares only top-level key *names*, so this
does not surface as drift. Consequence: on a host without the hook, **every** session attaches
only the handoff-time floor, permanently and silently. Wiring it is an operator act per host,
not a change this proposal can ship, and §8 control 4 must name the host it ran on.

### 5.2 Transport

Host → object storage **directly over the mesh**.

🔴 **The binding constraint is the store's third-party rule, NOT the pod's capacity.** The
store README: content *"must never transit a third party … A git remote, a sync target or an
API endpoint is permitted only on infrastructure Zach owns and reaches over the nebula
mesh."* Cairn's own public API already fronts through a CDN, so "not the pod" does **not**
imply "over the mesh" — an implementer who accepts only the capacity argument and reaches
MinIO through a public ingress satisfies it completely while putting unredacted multi-client
transcripts through a third party. **Name the rule when writing this down.**

The capacity point is true and secondary: the pod is single-replica, `Recreate` and
PVC-backed (all three verified) and is designed to render markdown, so a 23.48 MB PUT — §4
row 1's max, the largest single transcript FILE on the host — through it is a category
change. But that is a reason not to use the pod — it is not the reason the route must stay on
the mesh.

### 5.3 What cairn holds — and one recommendation no recorded decision covers

Front-matter on each touched entry gains a session reference carrying `id`, `sha256`,
`bytes`, `captured_at`, `host`.

🔴 **THE FIELD SHAPE IS CONSTRAINED, AND THE OBVIOUS SPELLING IS SILENTLY DESTRUCTIVE.**
Cairn front matter is parsed by `parse_front_matter` in `lib/subsystem_resolver.py`, which is
hand-rolled and **line-based**. It handles `key: value`, an inline flow list `key: [a, b, c]`,
and a block list of one-line `- item`s. **There is no case for a nested mapping**: the key
comes back empty and every child is promoted to a phantom top-level key by its own internal
colon.

⚠ **Do not read the parser's docstring as documenting this** — an earlier revision of this
paragraph cited it, and the citation was wrong in a way that would close the case for a reader
who checked it. The docstring records the same corruption for a **block list**, under the
heading *"THE BLOCK FORM IS PARSED BECAUSE NOT PARSING IT CORRUPTED THE MAPPING"* — i.e. that
shape was **fixed**. The nested-mapping case is unfixed and undocumented there; the evidence
for it is the reproduction below and nothing else.

Reproduced against the live parser while writing this, with the exact five fields above under
a `session:` key:

```
{'service': …, 'scope': …, 'session': '',
 'id': '…', 'sha256': '…', 'bytes': '…', 'captured_at': '…', 'host': '…'}
```

The pointer is **gone**, `cairn recall` renders the entry as clean, and §5.4's digest ledger —
the thing meant to make a bad pointer visible — was never written to be checked. So the field
must be a **single scalar or an inline flow list**, or the parser must be widened first.
⚠ Widening it is a **two-repo change**: `parse_front_matter` sits in `ZacxDev/cairn` too, at
the same line, and §7 puts that repo out of scope. ⚠ Also unmeasured: the same docstring
records that block-list lines are currently **zero across the live store** and says to re-take
that count before relying on the shape. This field would be its first user.

🔴 **RECOMMENDATION: the pointer is an OPAQUE ID, never a resolvable URL** — no bucket, no
path, no endpoint in the entry.

⚠ **This contradicts no recorded decision.** Decision 6 is *"pointer on every cairn entry the
session touched"* and says nothing about the pointer's form, so this is a gap being filled,
not a dissent. An earlier revision of this section called it "against the letter of decision
6", which sent the reader looking for a conflict that does not exist — and inviting deference
to a decision nobody made is a good way to lose a cheap recommendation.

**The hazard, stated in the direction it actually runs.** `plan-cairn-integration.md`
decision 11 already gates sharing on `sensitivity:` — *client-confidential can never be
shared* — so the obvious scenario (a client entry leaking a pointer) **cannot fire**, and an
earlier revision of this section argued exactly that already-closed case. What survives
decision 11 is the mirror image, and it is worse: a **shareable** entry — this repo's own
scope, marked `internal` — carries a pointer into an object that contains that same session's
**client-confidential** work, because one session routinely touches both. Sharing the
innocuous entry hands over a reference into multi-client content.

An opaque id keeps decision 6 exactly as chosen while making the shared artifact an
**identifier, not content**. It costs nothing and it is reversible; a URL baked into N entries
is not.

⚠ **What "separate credential" does and does not buy.** Resolving an id requires the
transcript credential. That is **not** separation "by construction": decision 3 separates the
*data*, not the *credentials*, and both would live in the same user's `~/.config`
neighbourhood on the same box, readable by any process running as that user — including a
shipper that holds one while running inside a session that holds the other. The separation is
real only against an adversary holding exactly one credential and no host access. Say that,
rather than implying more.

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
- 🔴 **write the credential to a `0600` file BEFORE anything can create the user** — not
  after, and never to stdout. Both halves are load-bearing and an earlier revision of this
  bullet kept only the second. `ZacxDev/homelab-infra#683`'s own heading is *"the reordering
  is not the fix"*: what closes the class is the secret existing on disk **before** the
  provisioning step, because the fallible steps after user-creation are host-side and no
  ordering inside the pod can reach them. Write-after-create leaves an orphaned live
  write-capable key whose secret exists nowhere — the exact window #683 closed. The
  stdout half matters too (these scripts are run by agents, so a printed secret lands in a
  transcript), and with decision 8 it would land there **permanently**;
- carry **no anonymous access policy**, proven by probing it rather than by reading the
  policy JSON — and see §8 control 6, because probing it correctly is harder than it looks;
- keep the **cairn read token unable to reach the bucket**, and vice versa — pinned by a
  test, not asserted by a comment. ⚠ Note the limit stated in §5.3: this separates
  credentials, not the host they both sit on.
- 🔴 **register the credential in `SECRETS.md` with a rotation coupling, in the same change
  that mints it.** That file exists to make a new-host bootstrap deterministic instead of
  manual archaeology. ⚠ **Naming the population, since this document's own §4 is about not
  doing that:** of `SECRETS.md`'s 8 host-file rows, 6 bear a secret and **2 of those 6** carry
  a rotation-coupling clause — both of them hosted-service bearer tokens with a k8s source of
  truth, which is exactly what this credential would be. So the precedent is strong for this
  shape and is *not* a universal convention. A credential minted outside that file is
  invisible at bootstrap and at rotation either way.

🔴 **The bucket has NO tenancy boundary, and the store it attaches to does.** Under
`plan-cairn-integration.md` the store's future is multi-tenant with sharing gated on
`sensitivity:`. This bucket is single-tenant by credential and multi-client by content, held
forever — so the transcript credential is an **all-clients-at-once** credential. Nothing in
this design changes that, and it should be understood before the credential is minted rather
than discovered when the store gains a second tenant.

⚠ **There is no retraction story, and decision 8 makes that permanent.** If a secret is found
in a shipped object, deleting the object leaves the N fanned-out pointers dangling. §5.4 makes
a dangling pointer *detectable*; nothing here makes it *retractable*, and nothing sweeps the
entries that reference it. That is a real gap, not an oversight being papered over — it is
listed in §9.

🔴 **You currently have NO detector for a credential landing in a transcript.** Redaction
was declined and that is settled — but a **report-only** scan over the bytes being shipped
blocks nothing, costs one pass over data already being read, and turns an invisible event
into a number. With indefinite retention, a secret that lands there is there forever. **Not
v1; recommended as the first follow-on.**

## 7. Explicitly out of scope

- **opencode capture** — §4. Different extraction, different fidelity question.
- **Redaction of any kind** — decision 4.
- **Anything in `ZacxDev/cairn`.** The OSS extraction deliberately removed `cairn who`
  because session forensics on named hosts is *"not an operation on a store"*. Whole-session
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
   not duplicate. **Name the host it ran on** — §5.1: the hook is wired on one host today.
   ⚠ If §9.6 resolves to one-object-per-session, this control passes trivially for the wrong
   reason whenever subagent files collide on the key; it is only meaningful once the key is
   decided.
5. **A crash path attaches the floor.** Kill a session without `SessionEnd`; the
   handoff-time object must still be there and its digest must match what was shipped.
6. **Anonymous access denied — and the control must distinguish DENIED from ABSENT.**
   🔴 The obvious version passes on a publicly-readable bucket: GET a key that was never
   uploaded and an anonymous-download bucket answers `404 NoSuchKey` while a locked one
   answers `403 AccessDenied`. Both are "not 200". So: **GET a key that EXISTS, and assert
   `403` specifically** — not "denied", not "not 200". This is §5.3's own rule turned back on
   §8: an empty result cannot distinguish two mechanisms. Cover anonymous **LIST** and **PUT**
   as well; a public-read policy grants list alongside read, and a denied GET says nothing
   about either. Run against the real bucket, not a fixture.
7. **Cross-credential separation, both directions, each with a named observable:** the cairn
   read token against a bucket key → `403`; the transcript credential against a cairn read
   route → `401`. An earlier revision named neither operation nor expected result, which any
   test satisfies — including one that type-checks past a wrong argument.
8. **Decision 7's actual behaviour, which no earlier control covered.** Make the push fail
   (unreachable endpoint) and assert **handoff exits 0** *and* the doc carries the
   no-session-attached line. Control 2 asserts the *shipper* exits non-zero, which is the
   opposite-signed observable and is equally satisfied by a handoff that fails hard — the
   precise failure decision 7 exists to forbid.

🔴 Every one of these must be watched to FAIL before it is trusted. A guard pinning an
invariant the bug never violated is an invariant guard and must be labelled as one.

## 9. Open — decide before building

1. **Does the pointer-shape recommendation in §5.3 stand?** No recorded decision covers the
   pointer's form, so this fills a gap rather than dissenting. Cheap to accept, expensive to
   retrofit.
2. **What is the front-matter field called and SHAPED?** Constrained by §5.3: a scalar or an
   inline flow list, or `parse_front_matter` gets widened first in **two** repos. Also: does
   the subsystem-index skill or the handoff tool own writing it? (`clawgate-task:` is
   precedent for the *idea* of a linkage field, though it is a handoff-doc field rather than
   a cairn-entry one.)
3. **Which entries count as "touched"?** The handoff run knows what it wrote; whether that
   set is the right one, or too wide, has not been measured.
4. **opencode** — schedule, or park indefinitely.
5. **What does the new shipper do about `transcript_search`, and does it share `project_of`?**
   Not "does the extraction happen" — discovery is **already** a shared module with an
   enforced site ledger, so the question is narrower: reuse it and inherit its `subagents/`
   exclusion, widen it, or bypass it deliberately. That half is downstream of question 6.
   `project_of` is the separate, unconditional half: it is one-site today and the second
   shipper will want it. §3.
6. 🔴 **Does a session ship as ONE object or a SET?** §5.0. This is the largest open
   question, it decides the object key, and the goal in §1 depends on the answer — 64% of the
   bytes are in subagent transcripts, and one-object-per-session drops them.
7. **Is there a retraction path when a secret is found in a shipped object?** §6. Deleting
   the object leaves N pointers dangling and nothing sweeps them. Under decision 8 this is
   permanent, so "no path" is an answer — but it should be a chosen one.
8. **Who wires `SessionEnd` on the second host, and what detects that it is missing?**
   §5.1. It is unmanaged per-host state that `drift-check.sh` does not currently see.
