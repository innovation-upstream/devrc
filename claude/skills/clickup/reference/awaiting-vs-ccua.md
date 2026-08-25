# `awaiting` vs `check-clickup-addressed` — why the predicate is not consolidated

**The question this file answers, once:** the predicate *"the newest comment on this task
is NOT the token owner's"* is implemented twice in this repo, in two languages.
`scripts/check-clickup-addressed/` (ccua) already shells out to this CLI. So why doesn't
it call `query.mjs awaiting` and stop re-deriving?

**Because it would be a regression, and the specific losses are enumerated and tested.**
Read this before proposing the consolidation again — the reasoning below is measured, and
every claim in it is pinned by a test that goes red when it stops being true.

## Where the two implementations actually are

| side | file | shape |
|---|---|---|
| JS | `lib/awaiting.mjs :: isAwaiting()` | per TASK, a **boolean** |
| PY | `recent-comments.py :: latest_reply_ts_by()` → `check-addressed.py :: _reply_answers_the_comment()` | per COMMENT, a **three-state** derived across a seam |

⚠️ `recent-comments.py`'s author comparison (`str(c["user"]["id"]) != my_id`) is **not**
the duplicate — that is a per-comment *authorship filter*, a different predicate. The
duplicate is the pair in the table above, and the Python side is the **richer** one, which
is why the consolidation direction that looks obvious is backwards.

## The blockers

Machine-readable in `test/awaiting-contract.fixtures.json` → `blockers`. In severity order:

1. **Population (decisive).** `awaiting` emits a row *only* when the newest comment is not
   the owner's. Every task the owner answered last is structurally absent — and that is
   exactly the population `_waiting_verdict`'s **ANSWERED / suppression** branch is built
   from, the branch carrying `BOT_IDENTITY_CAVEAT` ("an agent may have answered as you").
   Feeding ccua from `awaiting` deletes that whole block with nothing failing.
2. **Comment text.** `awaiting` rows carry no body at all. ccua's entire decision surface
   is the text: the keep-open veto, the RESOLVED-reading-comment flag, `TEXT_CHARS`.
3. **`my_latest_reply` / `my_latest_reply_ms`.** No equivalent exists. It is the fix for
   the D12 seam defect, and its *absence* is itself a reported fact (`UNIDENTIFIED`). A
   two-state boolean cannot carry a three-state field.
4. **`task_priority`.** `awaiting` carries `status` but not `priority`.
5. **Failure mode.** With an unresolvable owner id, `query.mjs awaiting` exits 1 and prints
   nothing. ccua continues, warns on stderr, and *withholds* the key so the consumer takes
   its announce-rather-than-decide branch. Exiting is right for a triage command and wrong
   for a producer feeding a report.

Blockers 2–4 could be added to `awaiting` at the cost of turning a triage command into a
data dump. **Blocker 1 cannot** — it is the command's definition — and blocker 5 is a
deliberate difference in kind.

## What WAS consolidated: the table, not the code

`test/awaiting-contract.fixtures.json` is the single definition of the *contract*, read by
both suites:

* `test/awaiting-contract.test.mjs` measures `isAwaiting()`;
* `scripts/check-clickup-addressed/tests/test_awaiting_contract.py` measures the ccua
  producer → consumer pipeline end to end.

Neither copies the other's column, and **both** recompute the divergence ledger from the
two columns and pin it as a set, so a divergence appearing *or* disappearing is red on
both sides. Nothing else makes these two agree; do not delete the fixture to "simplify".

## The three measured divergences

Cross-language behaviour differences, found by writing the table — not previously known:

* **`owner_reply_in_the_same_millisecond`** — two comments at the same instant. Python
  resolves it by an explicit rule (`mine_ms >= theirs_ms`; a genuine tie counts as
  answered). JS resolves it by **array order** — `newestComment()` keeps the first of an
  equal pair — so reversing the list flips the verdict, and in the API's own order it
  answers the *opposite* of Python.
* **`the_owners_own_comment_has_an_unreadable_date`** — Python declines to claim
  (`UNIDENTIFIED` → the key is withheld → the flag fires *with* a note that the check did
  not run). JS returns a **confident `true`** over a thread it cannot rank. Same
  direction, different confidence, and `isAwaiting()`'s return type cannot express it.
* **`every_comment_has_an_unreadable_date`** — Python declines. JS's answer is a property
  of the response order rather than of the thread.

None is currently reachable in production — all 84 comments sampled when `awaiting` was
written carried a `date`, and an exact-millisecond tie needs two writes in the same
millisecond. They are **pinned rather than fixed**: changing `isAwaiting()`'s behaviour on
an unobserved input is a live behaviour change to a shipped command bought with nothing.
The order-dependence label is proved by reversing the list, not asserted.

## If you are here to close a blocker

Fine — but move the fixture and this file in the same commit, and say in the commit
message which behaviour changed. The guards will tell you if you missed one: the JS half
pins the exact `awaiting` row key set, and the Python half pins that ccua still emits each
blocked field, so a blocker that is genuinely retired fails **both** sides at once.
