#!/usr/bin/env python3
"""Write a session-handoff doc — first time or update — behind a real gate.

🔴 THE SOLE WRITER of `claudedocs/handoff-<topic>.md`, as of 2026-08-23. This
paragraph used to say the opposite ("the write half … when a doc ALREADY
EXISTS"), because step 2 of the skill wrote a first doc itself and only later
runs came here. That was the bug: this module is the only step that COMMITS, and
against an already-written doc it returns `no-change` (exit 5), whose documented
instruction is to stop — so a brand-new handoff was never committed and ended
the session untracked. The skill now drafts into a scratch file in BOTH cases
and routes every write here. The missing-base path was always handled (the
update simply becomes the whole doc); what changed is that it is now the normal
first run, not a hypothetical. It exists because of a measured incident:

  A session re-entered work from a handoff, did ten minutes of genuinely
  valuable analysis — it answered the doc's open question AND corrected a prior
  misreading — and then wrote and PUSHED an updated handoff to a shared
  branch with no confirm gate at all. The operator never approved it.

Both skills involved were correct on their own terms. `/resume` is read-only by
contract and followed it. `/handoff` gates its *index* write ("Write only on
explicit confirm, diff first … on decline, discard"). The gap was underneath:
the handoff DOC's own write+push carried no equivalent gate, and a session
running after a resume inherited no constraint at all.

EIGHT RULES, and this module is what makes seven of them structural rather than
prose an agent can read and then not follow:

a. UPDATING IS NOT FORBIDDEN. The incident's update was correct and valuable;
   suppressing it costs the next session the ten minutes again. Optimising for
   doc stability over state accuracy is backwards. So this tool exists to make
   the update SAFE, not to make it rare — there is no "don't update" path here.

b. THE TWO-PHASE SHAPE IS ON THE PUSH. The default mode writes NOTHING — not
   the doc, not a commit, not a ref — it only prints the diff. Landing it takes
   a SECOND invocation carrying `--confirm` (and `--push`). A decline is
   therefore not a code path that has to behave; it is the absence of one, and
   `TestDeclineWritesNothing` hashes the whole repo tree either side of a
   default-mode run to keep it that way.

   🔴 THE y/N IS GONE — retired 2026-08-23 by operator decision, on the same
   evidence that retired the index write's prompt on 2026-08-15: it was always
   answered `y`, so it bought a round trip and no safety. This paragraph used to
   describe it as "the SAME gate shape `/handoff` specifies for the index
   write", which had ALREADY stopped being true of that write.

   🔴 WHAT THE PROMPT WAS NOT DOING, and what now carries the whole load: the
   refusals (`no-advance`, `no-change`, `behind`, `failed` — every one writes
   nothing) and the three WARNINGS printed above the diff (both base-currency
   tells and the durable-drop report). Those were advisory when a human read the
   diff and answered; they are the only reader now. Do not weaken one on the
   grounds that "the caller will see it" — the caller is the thing that stopped
   being asked.

   ⚠ AND THE PUSH IS NO LONGER BRANCH-LIMITED IN PRACTICE. `branch_is_shared()`
   picks which remedy text prints; it blocks nothing, and because this module
   shells out to git from inside Python, a `bash-guard.py`-style PreToolUse hook
   never sees the inner `git commit` either. Operator was asked explicitly and
   chose to push wherever the checkout sits, `main` included. If that is ever
   revisited, THIS is the place to add the refusal — a prompt is not the fix.

c. THE STATUS HEADER IS REPLACED; THE FINDINGS APPEND. "State now" / "Next
   steps" / "How to verify" are current state and are overwritten. "Open
   investigations", "Findings" and "Gotchas / decisions / dead-ends" are the
   live diagnosis state the skill itself calls "the single highest-value part
   of the handoff", so an update APPENDS to them and the earlier text survives
   verbatim. The incident is the argument: that update *superseded* an earlier
   interpretation, and the value is seeing the prior reading was corrected —
   not finding it silently gone. Appending is deliberately dumb: a new block
   with the SAME heading as an old one still appends, because supersession is
   exactly the case worth keeping both halves of.

d. NO ADVANCE, NO OFFER. `--advanced` is a required, non-empty statement of
   what changed since the doc was written. Without it — or with one of the
   sentinels that MEAN nothing changed — this exits 4 having printed no diff at
   all. Not an empty diff, not a no-op commit: no offer. A resume that goes
   nowhere overwriting a good handoff is the worst case and the one nobody
   notices until they try to retry cleanly. The honesty of `--advanced` is on
   the caller, so there is a second, independent guard that does not depend on
   it: if the merge produces content equal to what is already on disk, that is
   exit 5 `no-change`, also with no diff and no commit.

WHAT IS OUT OF SCOPE HERE. Whether `/handoff` should push at all in a repo
whose trunk is the deploy branch is a per-repo policy question, not this
module's. It pushes only when asked to, only to the remote and branch it is
given, and only together with `--confirm`.

e. A LOCAL COMMIT IS NOT A CHEAP LOCAL STATE, AND MUST NOT BE SILENT. `--confirm`
   without `--push` is a legitimate thing to want and stays exit 0 — but it makes
   a real commit, and that end state is IDENTICAL to the one `status=push-failed`
   spends nine alarmed lines on. Reaching it by the ordinary success path used to
   print one line and say nothing about the commit's fate: not the branch, not
   that it was unpushed. So that path now states the fact — without the alarm,
   because it is information, not a refusal. See `not_pushed_report`.

f. A REPLACE THAT DROPS A DURABLE LINE SAYS SO — AND STILL DOES IT. Rule (c)'s
   allowlist is three prefixes wide, so every OTHER heading replaces, "State
   now" included — the heading an updating session is usually already editing.
   Durable content written there is deleted on the next update, and the loss is
   NOT the gap: the diff below already shows it. The gap is CLASSIFICATION. In a
   large doc's diff a `-` line that is stale status and a `-` line that is a
   measured finding look identical, so the reader must hand-classify every
   deletion on every update — and a session that did exactly that caught a
   completed arc, a survey's negative result and a closure on two CONSECUTIVE
   updates, then recorded a prose gotcha about it. This is the structural form
   of that gotcha: BEFORE the diff, every dropped line that looks durable is
   named, with its base line number and why it was flagged.

   🔴 IT WARNS AND NEVER REFUSES. No exit code, no block, no new failure path —
   replacing genuinely stale status is the ORDINARY case, and a warning that
   could stop the write would become a permanently-red gate everyone learns to
   click through. For the same reason it must be SILENT on ordinary churn:
   measured over the 44 real handoff docs in this repo, the predicate flags
   63 of the 2,626 lines sitting under REPLACE headings (2.4%), so a typical
   status replace prints nothing at all.

   The "looks durable" question is answered in ONE place, `durable_reason`, and
   its openness half is NOT re-implemented here — it is `subsystem_resolver`'s
   `OPEN:` / `RESOLVED <sha>:` / near-miss vocabulary, imported. See there.

g. A RUN STATES WHICH BUCKET EACH SECTION LANDED IN. Rule (c) lived only in this
   docstring and in step 5 of the skill — neither of which is in front of an
   author at the moment they choose a heading. So every run that prints a diff
   also prints one line naming the bucket each touched section fell into, which
   is the fact rule (f)'s warning is downstream of.

h. A BASE THAT IS THE WRONG DOCUMENT SAYS SO, LOUDLY. Rule (g)'s bucket line was
   the ONLY tell when this tool was pointed at a clone 313 commits behind: it
   printed `State now → NEW` and would have rebuilt an 891-line document from a
   290-line base, discarding ~601 lines including a whole incident writeup, and
   exited 0 saying `status=written`. A bare `NEW` token inside a classification
   line is not a warning. So a run now also asks, ABOVE the diff and in rule
   (f)'s voice, whether the base is the document the update was written against —
   from a skeleton heading arriving `NEW` on an established doc, from an update
   larger than the base it merges into, and from the hard question: does the
   repo's own MAINLINE (derived, never a hardcoded `main` — this incident's repo
   uses `trunk`) carry commits to this doc that the checkout lacks?

   🔴 IT WARNS in the ordinary stale case and REFUSES in exactly ONE: no usable
   doc HERE while the mainline has one, where every section arrives NEW and the
   committed document is REPLACED wholesale. That case alone exits 7
   (`stale-base`) and carries an explicit `--allow-replacing-mainline-doc`;
   everything else still warns at exit 0, and 4 `no-advance` / 5 `no-change` keep
   their exact meanings. Working on a deliberately-behind clone is legitimate —
   which is why the warning half stayed a warning. The refusal is scoped to the
   one destructive shape precisely so it does NOT become a gate people learn to
   click through.

   🔴 IT IS SILENT ON THE ORDINARY RUN, MEASURED. All 49 real handoff-doc updates
   in this repo's history were replayed through `merge_report`; the two
   heuristic tells fire on 0 and 1 of them. The rejected looser variants and
   their rates are recorded at `CANONICAL_HEADING_PREFIXES`.

i. ONE DOC PER EFFORT, UPDATED IN PLACE — AND THE TOPIC SLUG IS THE KEY. Operator
   decision 2026-08-28, on a re-measurement of meta-work: `devrc`'s own share
   FELL (19.5% -> 17.4%), so the tooling is not the runaway — the growth is in
   DOCUMENTING of work. 20 of 70 commits to `homelab-talos` in three days were
   handoff docs; a prior audit found 538 docs created in 15 days, 98 of them
   rewritten 3+ times. The cap is therefore on the documenting, not on the
   tooling, and it is TWO refusals here rather than a paragraph in the skill:

     i-a. A `--topic` CARRYING A DATE IS REFUSED, unconditionally and with no
        escape flag. This is the crisp half and it needs no inference at all: a
        slug with a date in it is BY CONSTRUCTION a per-session doc, because
        next session's date differs and the doc can therefore never be updated
        in place. MEASURED over the 123 real `claudedocs/handoff-*.md` in devrc
        + homelab-talos: 55 (44%) carry a full ISO date. Collapsing them by
        stripping the date exposes the duplication the rule exists to stop —
        `remix-session` x8 in homelab-talos, `browser-bridge` x3 in devrc,
        four more 2x families. Every one is the same effort wearing a new
        filename. There is deliberately NO bypass: a date in a handoff topic
        has no legitimate use under a one-doc-per-effort rule, and a bypass
        would be taken every time.

     i-b. CREATING A DOC IN A REPO THAT ALREADY HAS HANDOFF DOCS REQUIRES
        `--new-effort`, and the refusal LISTS the existing docs, newest first.
        This is the half that cannot be made crisp, and it is not pretended
        otherwise: "is this the same effort as one of those?" is a judgement,
        and 🔴 NO FUZZY MATCH IS ATTEMPTED — a similarity heuristic here would
        be exactly the clever-inference guard the operator's standing rule
        forbids, and it would be wrong in both directions on slugs like
        `remix-session` / `remix-hardening-session`. What IS deterministic is
        that creating the N+1th doc stops being the SILENT DEFAULT: the caller
        is shown the list and must make an explicit assertion. A session that
        genuinely starts a new effort types one flag; a session that was about
        to mint `remix-session-2` sees `remix-session` in the list first.

j. A RANKED NEXT-STEP MUST NAME AN EXTERNAL FORCING FUNCTION. Same decision, and
   it is the half that breaks the self-generating loop: each session's handoff
   manufactures the next session's queue, so the work never runs out and none of
   it was ever asked for by anything outside the loop. So every numbered item in
   a `## Next steps` section the update brings must carry `forcing: <kind>`,
   `<kind>` drawn from a CLOSED enumeration (`FORCING_KINDS`) that contains no
   member a previous handoff can satisfy — there is no `followup`, no
   `ranked-list`, no `handoff`. An untagged item or an unrecognised kind is a
   refusal naming the item and printing the vocabulary.

   🔴 THE FIELD MAY SIT ANYWHERE ON THE ITEM, INCLUDING A CONTINUATION LINE, and
   the refusal DIAGNOSES rather than assuming absence. Both halves are one fix
   for one measured failure: the first version searched only the numbered line,
   so it refused 179 of devrc's 257 real ranked items' SHAPE outright and told
   correctly-tagged items `[no forcing: field]` — a remedy already satisfied, so
   the re-run was byte-identical and the handoff could never land. `_item_blocks`
   owns the block boundary; `unforced_report` prints a remedy per CAUSE, and
   names a near-miss (`forcing function: gate`) or a fenced field specifically.

   🔴 WHAT THIS DOES *NOT* DO, stated here rather than discovered later. It
   cannot check that the cited forcing function is REAL, or that it is genuinely
   EXTERNAL. `forcing: incident — the queue is down` is accepted from a session
   inventing it. The enumeration is structural; the evidence beside it is prose
   and is not verifiable by any check this module could run. What it buys is
   that the claim becomes MANDATORY, ATTRIBUTABLE and GREPPABLE, and that the
   closed vocabulary gives a self-generated item no honest label to hide under.

   🔴 SO `none` IS A MEMBER OF THE SET, ON PURPOSE. Refusing self-generated items
   outright would not delete them — it would teach sessions to type `incident`
   falsely, moving the failure underground where nothing can count it. `forcing:
   none` is accepted, and every run that carries one prints a block naming those
   items as declared self-generated and NOT eligible to be worked. That makes the
   population measurable, which is the precondition for capping it.

   ⚠ AND THE "DOES NOT GET WORKED" HALF IS NOT ENFORCED HERE. This module is the
   doc's writer, not the queue's consumer; the skip belongs in `/resume` step 6
   and `claim-work`, and is NOT implemented. What ships here is the declaration
   those consumers would need to read.

EXIT CODES
  0  proposed (diff shown, nothing written) — or written/pushed under --confirm.
     `written` WITHOUT `--push` also reports the branch and that it is not pushed
  2  usage
  3  operational failure (unreadable input, git refused) — nothing written
  4  no-advance      — rule (d), no diff printed
  5  no-change       — merge is a no-op, no diff printed, no empty commit
  6  behind          — --push and the remote moved; nothing written
  7  doc-per-effort  — rule (i): a dated topic, or an unasserted new doc
  8  unforced        — rule (j): a ranked item names no forcing function
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
import typing
from pathlib import Path

# 🔴 ONE RULE, ONE PLACE — rule (f)'s openness half. `subsystem_resolver` owns
# the `OPEN:` / `RESOLVED <sha>:` grammar, the near-miss detector and the narrow
# unmarked-action floor, each with a measured matrix behind it and a
# `openness_population` property that is explicitly "the single source of the
# precedence order". A second regex here would regenerate that module's bugs at
# a second site and disagree with `subsystem_touch --validate` about the same
# line. So it is imported, and `test_handoff_doc.py` pins that the two call
# sites give the SAME verdict rather than trusting the import to stay one.
#
# Same `sys.path` idiom as `subsystem_recall` / `subsystem_touch`: these modules
# are run as scripts and loaded by path in the tests, so there is no package to
# import them relative to. Stdlib-only over there, so this costs a parse.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from subsystem_resolver import parse_journal_bullets  # noqa: E402

# 🔴 ONE RULE, ONE PLACE — rule (h)'s "what is this repo's mainline?" half.
# `subsystem_touch` resolves the same question for its commit window and takes
# the same answer from the same module. A second derivation here would disagree
# with that one the first time a clone's `origin/HEAD` is dangling — a state
# measured in devrc itself — and rule (h) would then measure currency against a
# branch the rest of the toolchain does not consider mainline.
import git_mainline  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAIL = 3
EXIT_NO_ADVANCE = 4
EXIT_NO_CHANGE = 5
EXIT_BEHIND = 6
"""--push was asked for and the branch is BEHIND its remote. Nothing written.

🔴 This exists because the alternative is the state this repo has a rule against.
MEASURED 2026-08-15: `--confirm --push` committed the doc to `main` in a SHARED
base clone, then the push was rejected non-fast-forward because two other
sessions had pushed while the session worked. The commit stayed. An un-pushed
commit on `main` in a devrc checkout is exactly what `ship.sh` skips over —
silently, because `merge --ff-only` refuses and the host is left "as found" — so
that host stops receiving every future change while still looking healthy. It has
bitten this repo twice (2026-08-06, 2026-08-09).

Refusing BEFORE the write keeps the tool's existing property — a failure writes
nothing — instead of trading it for a commit the caller has to know how to undo.
"""

EXIT_DOC_PER_EFFORT = 7
"""Rule (i). The doc's IDENTITY is wrong: a dated topic, or an unasserted new doc.

Two statuses share this code the way `failed` and `push-failed` share 3 — they
are one class ("this run would create a doc that should not exist") with two
different remedies. Nothing is written on either.
"""

EXIT_UNFORCED = 8
"""Rule (j). A ranked next-step names no forcing function. Nothing is written."""

EXIT_STALE_BASE = 9
"""There is no usable doc HERE and the mainline has one, so confirming would
REPLACE the committed document with this delta. Nothing written.

🔴 Distinct from EXIT_BEHIND above, and NEITHER implies the other. That one asks
whether `<remote>/<push-branch>` has moved, and only under `--push`; this asks
whether the BASE DOCUMENT is the real one, against the DERIVED mainline ref. A
feature branch current with its own upstream sails past the other check while the
mainline copy of the doc is still the one being destroyed.
"""


# --- rule (i): one doc per effort --------------------------------------------
#
# 🔴 THE TOPIC SLUG IS THE KEY, and it is a key the CALLER SUPPLIES rather than
# one this module infers. That is the whole design: `--topic` already decides the
# path (`claudedocs/handoff-<topic>.md`), so "same effort" is answered by "same
# slug" and by nothing else. No similarity metric, no token overlap, no embedding
# — a fuzzy match would be wrong in both directions on the real corpus and would
# make the rule unpredictable at the moment a session is trying to obey it.

# i-a. A date ANYWHERE in the slug. Both spellings occur in the corpus and the
# rule must catch both: devrc trails it (`browser-bridge-2026-08-01`) while
# homelab-talos leads with it (`2026-07-18-remix-session`). The bare-year arm
# catches `handoff-q3-2026-cleanup`, which the ISO arm alone would let through.
#
# 🔴 TWO ARMS, NOT THREE — and the third was DELETED after the mutation battery
# scored it SURVIVED. A `\d{4}-\d{2}(?!\d)` year-month arm sat between these two
# and no test could tell whether it was there: for `remix-2026-07-session` it
# matched `2026-07` while the bare-year arm matches `2026`, so BOTH arms reach
# the same verdict and differ only in the token the refusal quotes. That is the
# dead-predicate shape `remote_has_commits_we_lack` already records in this file
# ("two branches reaching one outcome cannot be told apart by any test"). The
# only input it would have caught alone is a year outside 19xx/20xx — a slug like
# `1888-07-notes` — which no handoff doc in either corpus carries. Deleting it
# costs no coverage: `test_every_dated_spelling_in_the_corpus_is_refused` still
# refuses that spelling, through the arm that remains.
_TOPIC_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|(?<!\d)(?:19|20)\d{2}(?!\d)")

#: How many rows any of rules (i)/(j)'s listings show before eliding — the
#: existing-docs list, the unforced items and the self-generated items alike.
#: Each is an aid to recognising what to fix, not an inventory: devrc alone has
#: 77 handoff docs, and 77 lines of filenames would bury the remedy under
#: itself. Same reasoning as `DROPPED_SHOWN_MAX`, one number rather than three
#: so the three blocks cannot drift into different shapes.
EXISTING_SHOWN_MAX = 12


def topic_carries_a_date(topic: str) -> str | None:
    """The date-looking token in this topic slug, or None. Rule (i-a).

    One place, so the CLI refusal and the tests ask the same question — the
    `advance_is_real` pattern.
    """
    m = _TOPIC_DATE.search(topic)
    return m.group(0) if m else None


def existing_handoff_docs(repo: Path) -> list[str]:
    """Every `claudedocs/handoff-*.md` in this repo, NEWEST FIRST.

    Newest first because the doc a session is about to duplicate is
    overwhelmingly the one it or a recent session last touched — putting it at
    the top is what makes the list scannable rather than merely complete.

    Sorted by mtime with the NAME as a tiebreak, so the order is deterministic
    in a fixture repo where every file is written in the same second. A
    non-deterministic list would make the refusal's own text untestable.
    """
    docs = (repo / "claudedocs").glob("handoff-*.md")
    try:
        return [
            p.name
            for p in sorted(docs, key=lambda p: (-p.stat().st_mtime, p.name))
        ]
    except OSError:
        return []


# --- rule (j): a ranked item names an external forcing function ---------------
#
# 🔴 A CLOSED ENUMERATION, WHICH IS THE PART A REWORDING CANNOT WALK. `RULES.md`
# warns that "a guard on WORDS is walkable by REWORDING", and it is right about a
# BLOCKLIST — a list of self-referential phrases to reject would be defeated by
# any synonym. This is the inverted shape: an ALLOWLIST the author must pick from.
# Rewording buys nothing, because a kind outside the set is refused by default.
#
# What each member asserts, and every one of them is a thing OUTSIDE this loop:
#   incident    something is broken or degraded in a live system, now
#   user        a person asked for it — the operator, a customer, a colleague
#   gate        a check that is failing or blocking: CI, a test, an alert, review
#   deadline    a dated commitment to someone outside this session
#   regression  a measured behaviour change against a previous measurement
#   security    an exposure, a vulnerability, a leaked or rotating credential
#   none        🔴 NOT AN EXTERNAL FUNCTION — a DECLARATION that there is none.
#
# 🔴 THERE IS DELIBERATELY NO `followup`, `handoff`, `cleanup`, `polish` OR
# `tech-debt`. Those are the labels a self-generated item would reach for, and
# their absence is what forces such an item onto `none`, where it is counted.
FORCING_KINDS: frozenset[str] = frozenset(
    {"incident", "user", "gate", "deadline", "regression", "security", "none"}
)

#: The kinds that assert something outside the loop — `FORCING_KINDS` minus the
#: honest opt-out. Derived, never a second literal list: adding a kind above and
#: forgetting it here is exactly the drift that would silently un-count items.
EXTERNAL_FORCING_KINDS: frozenset[str] = FORCING_KINDS - {"none"}

#: The field key, in the same spirit as `CLAWGATE_TASK_KEY` — a NAMED FIELD, not
#: a keyword the predicate hunts for in prose.
FORCING_KEY = "forcing"

# 🔴 THE MARKUP CLASS IS DELIBERATE, AND IT IS BOUNDED BY THE ALLOWLIST, NOT BY
# TASTE. `**forcing:** gate` is the field, spelled the way a skill body that
# bolds its field names teaches; refusing it would be a refusal over emphasis
# characters. Widening here is safe for exactly one reason, and it is structural
# rather than a judgement about intent: what follows the colon must be a member
# of a CLOSED vocabulary, so a "false positive" requires prose that literally
# reads `forcing` + punctuation + one of seven kinds — which is the tag.
#
# What this does NOT admit, and both stay NEAR-MISSES reported by
# `_FORCING_ATTEMPT` below rather than silently accepted: `forcing function:
# gate` (a word between the key and the colon) and `forcing = gate` (a separator
# that is not a colon). Those are guesses at the grammar, not the grammar.
_MARKUP = r"[*_`~]{0,3}"

# 🔴 NOT `\b`, AND THE DIFFERENCE IS THE WHOLE POINT: `_` IS A WORD CHARACTER.
# MEASURED at `503d7136`, i.e. against the commit that widened `_MARKUP` above to
# admit emphasis: `**forcing: gate**` parsed to `gate`, while `_forcing: gate_`,
# `__forcing: gate__` and `_forcing_: gate` all came back `kind=None,
# near_miss=None` — `\bforcing` has no boundary to match when the character
# before the key is itself a word character. So the widening admitted ONE of
# markdown's two emphasis characters and refused the other with
# `[no forcing: field]` plus a remedy the author had already carried out: the
# unrecoverable refusal `_FORCING_ATTEMPT` exists to end, reintroduced by the
# change meant to end it, in the exact spelling class it set out to admit.
#
# What the lookaround still excludes — the ONE job `\b` was doing here, and the
# thing to re-check before touching it: `enforcing:` and `reinforcing:` (the
# character before the key is an ASCII letter) and `forcings:` (the one after it
# is). 🔴 "ASCII" IS THE WHOLE SCOPE OF THAT CLAIM, AT EVERY POSITION, AND THE
# COMMENT USED TO OMIT IT: the class is `[A-Za-z0-9]`, so a non-ASCII word
# character excludes nothing at all — `\b` DID exclude it, because Python's `\w`
# is unicode by default. That hole is not the leading key's; it is every
# lookaround's, and the enumeration below is stated by POSITION for that reason.
#
# 🔴 THE ADMISSIONS ARE A GRID, NOT A LIST — an enumeration of examples is what
# undercounted here twice. The widening admits exactly TWO character classes,
# `_` and any non-ASCII word character, at EACH of the FIVE lookaround positions
# across the two patterns — ten combinations, and MEASURED 2026-08-28 all ten
# behave alike: admitted at HEAD, and NO match under the old `\b` spelling of
# the same pattern. The positions, with the probe that isolates each:
#   P1 `_FORCING`'s key, LEADING     — `some_forcing: none`, `éforcing: gate`
#   P2 `_FORCING_ATTEMPT`'s key, LEADING  — `my_forcing = gate`, `éforcing = gate`
#   P3 `_FORCING_ATTEMPT`'s key, TRAILING — `the forcing_fn returns none`,
#      `the forcingé returns none`
#   P4 that pattern's KIND, LEADING  — `forcing = _gate`, `forcing = égate`
#   P5 that pattern's KIND, TRAILING — `forcing the user_id column`,
#      `forcing = gateé`
# (There is no sixth: `_FORCING`'s own KIND, `([A-Za-z-]+)`, carries no trailing
# lookaround at all.) P1 parses to a kind; P2–P5 become NEAR-MISSES. All ten
# occur 0 times over both corpora (devrc 126 docs, homelab-talos 139) and all
# ten are bounded by the same closed-vocabulary argument as the markup class
# above, so none is being fixed — they are RECORDED, because a comment is a
# claim too. Pinned cell-by-cell by
# `test_the_widened_anchors_admit_these_and_the_comment_says_so`.
#
# 🔴 SPELLED OUT AT EACH USE SITE rather than folded into one shared constant:
# `_FORCING` and `_FORCING_ATTEMPT` must stay SEPARATELY MUTABLE, or the
# `forcing-key-anchored-on-word-boundary` / `near-miss-key-anchored-on-word-
# boundary` rows in `mutants-handoff-cap.sh` cannot isolate one from the other
# and neither proves anything about the pattern it names.
_FORCING = re.compile(
    rf"(?<![A-Za-z0-9]){FORCING_KEY}{_MARKUP}\s*:\s*{_MARKUP}\s*([A-Za-z-]+)",
    re.IGNORECASE,
)

#: The FAIL-LOUD half of a strict `_FORCING`, and the reason it exists is the
#: measured failure this pattern was added for: an item that DID carry a tag was
#: refused with `[no forcing: field]` and a remedy it had already satisfied, so
#: the session could not recover — every re-run printed the identical refusal.
#: Same idiom as `subsystem_resolver._NEAR_MISS_MARKER`: keep the grammar strict,
#: and REPORT the attempts it turns away instead of loosening it.
#:
#: The key, then a member of the vocabulary within a short window. Anchored on
#: the closed set at BOTH ends, so it cannot fire on the bare word `forcing` in
#: prose — `unforced_report` only consults it for an item it is already refusing,
#: and the worst case is a refusal that names a line the author is looking at.
#:
#: 🔴 THE ANCHORS ARE THE LOOKAROUNDS `_FORCING` USES, NOT `\b`, AND FOR THE
#: SAME MEASURED REASON — see the comment above it. This pattern is the SAFETY
#: NET for a tag `_FORCING` cannot parse, and at `503d7136` it shared the broken
#: `\b` anchor, so it had the identical hole: `_forcing = gate_` fell straight
#: through to `[no forcing: field]`. A net with the same gap as the thing it
#: catches for is not a net. BOTH ends of BOTH tokens are anchored: the trailing
#: `_` of `_forcing: gate_` is a word character too, so `\b` failed on the KIND
#: as well as on the key.
_FORCING_ATTEMPT = re.compile(
    rf"(?<![A-Za-z0-9]){FORCING_KEY}(?![A-Za-z0-9])"
    rf"[^\n]{{0,40}}?"
    rf"(?<![A-Za-z0-9])(?:{'|'.join(sorted(FORCING_KINDS))})(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# A TOP-LEVEL numbered item. The indent bound is what keeps a nested `1.` inside
# an item's own sub-list from being counted as a rank of its own — the ranks are
# half a claim's identity (`claim-work --slug-for <doc> <rank>`), so miscounting
# them would re-point live claims.
_RANKED_ITEM = re.compile(r"^ {0,3}(\d+)[.)]\s+(\S.*)$")

NEXT_STEPS_PREFIX = "next steps"


class RankedItem(typing.NamedTuple):
    """One numbered next-step, and the forcing kind it declared (if any)."""

    rank: str
    text: str
    kind: str | None
    """Lowercased declared kind, or None when the item carries no field at all.
    A kind OUTSIDE `FORCING_KINDS` is reported as declared — the caller needs to
    see what was typed in order to fix it."""
    near_miss: str | None = None
    """The line that LOOKS like a tag `_FORCING` could not parse, or None.

    Only ever set when `kind is None` — an item that parsed needs no diagnosis.
    Its whole purpose is `unforced_report`: without it a tagged-but-unparsed item
    is told `[no forcing: field]` and handed a remedy it already satisfied, which
    is a refusal no re-run can clear."""
    fenced: bool = False
    """True when the ONLY `forcing:` field this item carries sits inside a code
    fence, where it does not count. Same reason as `near_miss` — the author can
    see the field in their file, so `[no forcing: field]` reads as a lie."""

    @property
    def is_declared(self) -> bool:
        return self.kind in FORCING_KINDS


def _item_blocks(section_body: str) -> list[tuple[re.Match[str], list[str], list[str]]]:
    """`(match, the item's own visible lines, the fenced lines inside it)`.

    🔴 AN ITEM IS A BLOCK, NOT A LINE, and that is this function's whole reason
    to exist. MEASURED over the committed corpus: 179 of 257 ranked items in
    devrc's `claudedocs/` and 99 of 181 in homelab-talos' wrap onto continuation
    lines. Matching the field on the numbered line alone therefore refused the
    MAJORITY shape, and told it that it carried no field.

    Where a block ENDS is NOT "the next ranked item", and that is not a taste
    call — the naive boundary FALSELY TAGS, measured. It attributes a section's
    trailing paragraph to the last item, and the corpus says what that paragraph
    is: 14 blocks are followed by unindented prose after a blank line, 10 of them
    the last item in their section, 7 of those the skill's own copied
    `🔴 **This list is a WORK QUEUE …**` boilerplate. The template block it is
    copied from now also carries "```forcing: none``` is the honest opt-out", so
    appending it VERBATIM under two untagged items and asking the naive boundary
    returns `kind='none'` for item 2 — an item silently declared self-generated
    by text its author pasted from the instructions. Pinned by
    `test_trailing_boilerplate_does_not_tag_the_last_item`. So the walk uses the
    ordinary markdown rule:

      * a line INSIDE a fence always belongs to the item (never a boundary) —
        and it does NOT clear the "a blank line has intervened" memory either;
      * a blank line does not end it — an indented line may follow;
      * an UNINDENTED, non-blank line ends it once a blank line has intervened;
      * anything else continues it (markdown's lazy continuation).

    🔴 THE SECOND HALF OF THE FIRST BULLET IS A FIX, NOT A RESTATEMENT, and it
    is the ACCEPT direction. At `503d7136` the fence branch below reset
    `blanked`, so the first VISIBLE line after a fence close could never be a
    boundary and the rule the third bullet states was simply not the rule the
    code ran. MEASURED: an item whose own properly-INDENTED fence follows a blank
    line swallowed the section's trailing `🔴 **This list is a WORK QUEUE …**`
    boilerplate and `ranked_items` returned `kind='none'` — the untagged item
    ACCEPTED, counted as self-generated, and rule (j) passing. That is the very
    counterfactual this docstring cites two paragraphs up as the reason the naive
    boundary was rejected, re-entered through the fence path. Pinned by
    `test_a_fence_does_not_erase_the_blank_line_boundary`, and its cost side — an
    indented tag after the item's own fence, with and without a blank between —
    by `test_an_indented_fence_does_not_cost_the_tag_that_follows_it`.

    🔴 THAT FIX HAS A MEASURED COST, IT IS DELIBERATE, AND REVERSING IT WOULD
    REOPEN THE ACCEPT BUG. Shape: item, blank line, the item's OWN INDENTED
    fence, then a tag at COLUMN 0. At `503d7136` that parsed (`kind='gate'`);
    here the blank's memory survives the fence, so the col-0 line IS the
    boundary and the tag is dropped — `kind=None, near_miss=None, fenced=False`,
    i.e. `[no forcing: field]` printed at an author who DID write the field on a
    continuation line. The walk cannot tell that col-0 tag from col-0 pasted
    boilerplate, and falsely ACCEPTING an untagged item is worse than refusing a
    tagged one, so the trade stands. Two things pay for it: the corpus impact is
    **0 of 442** ranked items, and `MISSING_FIELD_REMEDY` now says the field
    must be INDENTED, which is what makes this refusal clearable instead of
    unrecoverable. Pinned by the `col-0` params of
    `test_an_indented_fence_does_not_cost_the_tag_that_follows_it` and by
    `test_the_missing_field_remedy_tells_a_FLUSH_LEFT_author_to_INDENT`.

    ⚠ A fence with NO preceding blank still absorbs the following unindented
    line — because THIS walk's boundary requires a blank to have intervened and
    none has. 🔴 THAT IS THIS WALK'S RULE, NOT MARKDOWN'S. The docstring used to
    justify it as "genuine markdown lazy continuation" and that reason is wrong:
    in CommonMark, lazy continuation applies to a PARAGRAPH's continuation
    lines, not to a line following a fenced code block inside a list item —
    there the fenced block has ended and an unindented line is not part of the
    item at all. The BEHAVIOUR is kept, and only its justification changed: it
    is the PERMISSIVE direction (it can only hand an author back a tag they
    wrote, never invent one for an untagged item) and no corpus item depends on
    the strict reading. A fence opened at column 0 after a blank line is a
    KNOWN, UNTESTED gap: markdown ends the list item there, and this walk does
    not.

    Fenced lines are returned SEPARATELY rather than dropped: they must not count
    as a tag (`_unfenced`'s contract, and a pasted sample is not a declaration),
    but an author who put the field in a fence needs to be told that is why.
    """
    all_lines = section_body.splitlines()
    visible = {idx for idx, _ln in _unfenced(section_body)}
    starts = [
        i for i in range(len(all_lines))
        if i in visible and _RANKED_ITEM.match(all_lines[i])
    ]
    out: list[tuple[re.Match[str], list[str], list[str]]] = []
    for n, start in enumerate(starts):
        limit = starts[n + 1] if n + 1 < len(starts) else len(all_lines)
        own: list[str] = [all_lines[start]]
        hidden: list[str] = []
        blanked = False
        for i in range(start + 1, limit):
            line = all_lines[i]
            if i not in visible:
                hidden.append(line)
                continue
            if not line.strip():
                blanked = True
                continue
            if blanked and not line.startswith((" ", "\t")):
                break
            blanked = False
            own.append(line)
        m = _RANKED_ITEM.match(all_lines[start])
        assert m is not None  # `starts` is exactly the lines that matched
        out.append((m, own, hidden))
    return out


def ranked_items(text: str) -> list[RankedItem]:
    """Every top-level numbered item under a `## Next steps` heading of `text`.

    🔴 READS THE UPDATE, NEVER THE MERGED DOC, and the choice is load-bearing in
    both directions. `Next steps` is a REPLACE-bucket heading, so the update's
    items ARE the doc's items — checking the update is checking what lands. And
    checking the MERGE would refuse on legacy items the base already carries,
    turning rule (j) into a permanently-red gate on every repo with history,
    which `claude/RULES.md` names as worse than no gate.

    Fence-aware via `_unfenced`, for the reason `split_sections` is: a handoff
    routinely pastes a numbered list inside a code block, and a sample command is
    not a work item.

    🔴 THE FIELD IS LOOKED FOR OVER THE ITEM'S WHOLE BLOCK — see `_item_blocks`
    for the boundary and the measurement behind it. Neither `FORCING_VOCAB_LINE`
    nor the skill ever said the tag had to sit on the numbered line; the majority
    of real items wrap, so a numbered-line-only search refused the common shape.
    """
    _fm, body = split_front_matter(text)
    _pre, secs = split_sections(body)
    out: list[RankedItem] = []
    for heading, section_body in secs:
        if not heading_text(heading).lower().startswith(NEXT_STEPS_PREFIX):
            continue
        for m, own, hidden in _item_blocks(section_body):
            block = "\n".join(own)
            found = _FORCING.search(block)
            if found:
                out.append(RankedItem(m.group(1), m.group(2), found.group(1).lower()))
                continue
            # Nothing parsed. Diagnose WHY, so the refusal can say something the
            # author has not already done. `_FORCING_ATTEMPT` cannot span a
            # newline, so the per-line walk sees exactly what a block-wide search
            # would — and it yields the LINE, which is what a reader needs.
            out.append(
                RankedItem(
                    m.group(1),
                    m.group(2),
                    None,
                    next((ln.strip() for ln in own if _FORCING_ATTEMPT.search(ln)), None),
                    any(_FORCING.search(ln) for ln in hidden),
                )
            )
    return out


# 🔴 A STATEMENT OF THE GRAMMAR, NOT AN IMPERATIVE, and the change is the point.
# This used to open "Tag each item `forcing: <kind>`" and was printed to EVERY
# refused caller — including one whose items were already tagged, which is a
# remedy that has been carried out telling you to carry it out. A re-run then
# printed the identical bytes and there was no way forward. The vocabulary is
# still needed by every arm (a caller has to see a closed set), so it stays; the
# instruction moved into the per-cause remedies below, which are conditional.
FORCING_VOCAB_LINE = (
    "  The field is `forcing: <kind>`, anywhere on the item's own lines — "
    "`<kind>` one of: " + ", ".join(sorted(EXTERNAL_FORCING_KINDS)) + ".\n"
    "  `forcing: none` is the honest opt-out for an item nothing outside this "
    "loop asked for. It is ACCEPTED and counted, not refused — but an item "
    "carrying it is not eligible to be worked."
)

# 🔴 THE FOUR PER-CAUSE MARKERS `unforced_report` PUTS ON A REFUSED ROW, AND
# THIS IS THEIR SINGLE SOURCE — because there is a SECOND READER outside this
# module. SKILL.md's step-5 legend maps each marker to what the executor should
# DO about it, and only ONE of the four means "add a field"; that legend is the
# whole reason the other three stopped getting the add-a-field remedy.
#
# 🔴 A `SKILL_PINS` ENTRY PER MARKER WOULD NOT COVER THE DRIFT THIS CLOSES. A pin
# asserts the literal is still IN the skill, so renaming `[fenced]` here goes red
# in this module's own tests, gets fixed here, and leaves the skill's legend
# naming a marker the tool no longer prints — with the pin still green, because
# the skill does still contain the old token.
# `test_every_refusal_MARKER_the_module_prints_reaches_the_skill` derives its
# check from `REFUSAL_MARKERS` instead, so a rename here is what goes red there.
#
# Each is the PREFIX its row begins with, not the whole row: the two that carry a
# value (`[unknown kind: 'x']`, `[unparsed forcing field on: …]`) cannot be
# pinned whole, and the token is the half a rename would move.
MARK_NO_FIELD = "[no forcing: field]"
MARK_UNKNOWN_KIND = "[unknown kind"
MARK_UNPARSED = "[unparsed"
MARK_FENCED = "[fenced]"
REFUSAL_MARKERS: tuple[str, ...] = (
    MARK_NO_FIELD,
    MARK_UNKNOWN_KIND,
    MARK_UNPARSED,
    MARK_FENCED,
)

#: Remedy for the plain case: no field anywhere in the item.
#:
#: 🔴 THE SECOND HALF IS NOT DECORATION. "A continuation line counts" alone is
#: read as a promise this walk does not keep: a tag written at COLUMN 0 under
#: the item — after a blank, or after the item's own indented fence — is the
#: BOUNDARY line, so it is outside the block and never scanned. An author who
#: has already written the field there is then told to write it, which is the
#: unrecoverable refusal this whole branch exists to end; naming the INDENT is
#: the only thing that makes that arm clearable. See `_item_blocks` for the
#: measurement and for why the boundary is not loosened instead.
MISSING_FIELD_REMEDY = (
    f"  Tag each item marked {MARK_NO_FIELD} above. A continuation line "
    "counts — the field does not have to sit on the numbered line, but it MUST "
    "be INDENTED: a flush-left line ENDS the item once a blank has intervened, "
    "so a tag at column 0 below one is outside the item and reads as absent."
)

#: Remedy for a near-miss. 🔴 IT MUST NOT REPEAT `MISSING_FIELD_REMEDY`: an item
#: that reaches this arm HAS a field, and being told to add one is the failure
#: this whole branch exists to end.
NEAR_MISS_REMEDY = (
    f"  🔴 The item(s) marked {MARK_UNPARSED}] DO carry something — the quoted "
    "line is there and the check could not parse it. Spell the field as the "
    "literal key, a colon, then the kind: `forcing: gate`. Emphasis around it "
    "is fine (`**forcing: gate**`, `**forcing:** gate`, `_forcing: gate_`, "
    "`` `forcing: gate` ``); a word between the key and the colon is not "
    "(`forcing function: gate`), and neither is any other separator "
    "(`forcing = gate`, `forcing — gate`)."
)

#: Remedy for a field that parses but sits inside a code fence.
#:
#: 🔴 IT MUST NOT SAY ONLY "MOVE IT OUT". The commonest thing a fence under a
#: ranked item quotes is this tool's OWN vocabulary line — an author pasting the
#: instructions, or a transcript of a previous refusal. Obeying a bare "move it
#: out of the fence" on that input promotes a quoted example into a declaration
#: and produces a FALSE `forcing: none`: an item nothing asked for, now counted
#: as honestly self-generated. The refusal itself is right; only the remedy
#: needed to stop assuming the fenced field is the author's own.
#:
#: 🔴 AND IT MUST NAME THE INDENT, for the same reason `MISSING_FIELD_REMEDY`
#: does. An item's fence is normally preceded by a blank line, so an author who
#: obeys "move it out of the fence" by unfencing to COLUMN 0 lands on the
#: boundary line and gets `MARK_NO_FIELD` — a SECOND refusal, telling them to
#: write a field they have now written twice. Naming the indent here is what
#: keeps this arm clearable in one step; the sibling arm was fixed first and
#: this one had the identical hole.
FENCED_FIELD_REMEDY = (
    f"  🔴 The item(s) marked {MARK_FENCED} carry the field INSIDE a code fence, "
    "where it does not count — a pasted sample is not a declaration. If that "
    "field is YOUR declaration, move it out of the fence onto one of the item's "
    "own lines, INDENTED — at column 0 it reads as absent. If it is quoted "
    "output, a copied example or this tool's own vocabulary line, the item is "
    "genuinely untagged and needs one of its own — do NOT promote the quote."
)


def unforced_report(items: typing.Sequence[RankedItem]) -> str:
    """Rule (j)'s refusal text, or "" when every ranked item declared a kind.

    🔴 EVERY REMEDY PRINTED HERE IS CONDITIONAL ON THE CAUSE THAT EARNED IT.
    A refusal that instructs a caller to do a thing the caller has already done
    is unrecoverable: the fix is a no-op, the re-run is byte-identical, and the
    session's handoff — which this module is the sole writer of — never lands.
    """
    bad = [i for i in items if not i.is_declared]
    if not bad:
        return ""

    def _mark(i: RankedItem) -> str:
        # 🔴 EVERY ROW BEGINS WITH ITS `REFUSAL_MARKERS` TOKEN, spelled from the
        # constant and never re-typed here. The skill's step-5 legend is the
        # executor's only map from a marker to what to do about it, and the
        # derived test that keeps the two in step reads those constants.
        if i.kind is not None:
            return f"   {MARK_UNKNOWN_KIND}: {i.kind!r}]"
        if i.near_miss is not None:
            return f"   {MARK_UNPARSED} forcing field on: {_clip(i.near_miss, 72)}]"
        if i.fenced:
            return f"   {MARK_FENCED} `forcing:` found, but inside a code fence"
        return f"   {MARK_NO_FIELD}"

    shown = bad[:EXISTING_SHOWN_MAX]
    rows = [f"  {i.rank}. {_clip(i.text, 96)}" + _mark(i) for i in shown]
    elided = len(bad) - len(rows)
    if elided:
        rows.append(f"  … and {elided} more.")
    # Keyed off EVERY bad item, not just the shown ones: a remedy suppressed by
    # the display cap would be missing for exactly the caller who cannot see the
    # row that needed it.
    remedies = []
    if any(i.kind is None and i.near_miss is None and not i.fenced for i in bad):
        remedies.append(MISSING_FIELD_REMEDY)
    if any(i.near_miss is not None for i in bad):
        remedies.append(NEAR_MISS_REMEDY)
    if any(i.kind is None and i.near_miss is None and i.fenced for i in bad):
        remedies.append(FENCED_FIELD_REMEDY)
    return "\n".join(
        [
            f"status=unforced",
            f"NOTHING WRITTEN — not the doc, not a commit, not a ref.",
            f"{len(bad)} of {len(items)} ranked next-step(s) name no forcing "
            f"function. Operator decision 2026-08-28: a ranked item that names "
            f"no EXTERNAL forcing function does not get worked, so it does not "
            f"get written down as a rank.",
            *rows,
            FORCING_VOCAB_LINE,
            *remedies,
            "  🔴 EXTERNAL means an incident, a person's request, a failing "
            "gate, a deadline, a measured regression or a security exposure — "
            "NOT the previous session's ranked list. That loop is what this "
            "refusal exists to break: each handoff manufacturing the next "
            "session's queue is how the work never runs out and none of it was "
            "ever asked for.",
        ]
    )


SELF_GENERATED_NOTE = (
    "  These are ACCEPTED and the write proceeds — declaring one honestly is the "
    "point. They are not eligible to be worked: a session picking from this "
    "queue should skip them and do something an external signal asked for."
)


def self_generated_report(items: typing.Sequence[RankedItem]) -> str:
    """Rule (j)'s advisory block for `forcing: none` items, or "".

    Silent when there are none, for `dropped_durable_report`'s stated reason: a
    reassuring "0 self-generated items" on every run is a line that gets skimmed
    and then read as a guarantee.
    """
    none_items = [i for i in items if i.kind == "none"]
    if not none_items:
        return ""
    return "\n".join(
        [
            f"🔴 {len(none_items)} of {len(items)} ranked next-step(s) declare "
            f"`{FORCING_KEY}: none` — NO external forcing function:",
            *[f"  {i.rank}. {_clip(i.text, 96)}" for i in none_items[:EXISTING_SHOWN_MAX]],
            SELF_GENERATED_NOTE,
        ]
    )

# Rule (c). A section whose heading starts with one of these is DIAGNOSIS STATE
# and appends; everything else is CURRENT STATE and is replaced. Matching is on
# a lowercased prefix, not the whole heading, because the canonical spellings
# carry a trailing gloss ("Open investigations — live diagnosis state") that an
# updating session will not reproduce character-for-character.
APPEND_PREFIXES: tuple[str, ...] = (
    "open investigations",
    "findings",
    "gotchas",
)

# Rule (d). Lowercased, stripped `--advanced` values that ASSERT no advance.
# A caller who types one of these has answered the question honestly and gets
# the same treatment as one who omitted the flag: no diff, no offer.
NO_ADVANCE_SENTINELS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        ".",
        "n/a",
        "na",
        "nil",
        "no",
        "no change",
        "no changes",
        "none",
        "nothing",
        "nothing new",
        "nothing yet",
        "tbd",
        "unchanged",
        "unknown",
    }
)

_H2 = re.compile(r"^##\s+\S")
_FENCE = re.compile(r"^(`{3,}|~{3,})")

# YAML front matter, and ONLY at the very start of the file: `---` on line 1,
# then everything through the next `---` line. Same strictness as
# `clawgate_task_field` in scripts/lib/clawgate_handoff.sh — a `---` later in a
# markdown doc is a horizontal rule, not a front-matter opener.
#
# ⚠ ONE KNOWN DIVERGENCE from that shell reader, recorded rather than fixed:
# the `\r?\n` after the closing `---` means a document that ENDS at the closing
# delimiter with no trailing newline is front matter to the shell and preamble
# here. That document has no body at all, so the only consumer that can tell —
# the merge — reports it through the preamble-drop warning either way. Fix it if
# a document like that ever turns up; do not "tidy" it without one.
_FRONT_MATTER = re.compile(r"\A---\r?\n.*?^---\r?\n", re.DOTALL | re.MULTILINE)


def split_front_matter(text: str) -> tuple[str, str]:
    """(front_matter, rest) — and `front_matter + rest == text` exactly.

    🔴 WHY THE MERGE HAS TO KNOW ABOUT THIS. Front matter is not a section, so
    it lands in `split_sections`'s PREAMBLE, and `merge` takes the update's
    preamble whenever it has one. That means a delta file whose first line is
    prose rather than a `## ` heading SILENTLY DELETED the doc's front matter —
    including the `clawgate-task:` field /resume reconciles against, which then
    reads as "this doc names no task" rather than as data loss.

    The field is meant to be DURABLE, so it survives a merge structurally
    rather than by everyone remembering to write their delta heading-first.
    """
    m = _FRONT_MATTER.match(text)
    return (m.group(0), text[m.end():]) if m else ("", text)


#: 🔴 THE SAME SPELLING `CLAWGATE_FIELD_KEY` CARRIES IN
#: `scripts/lib/clawgate_handoff.sh`. Two languages, one key — pinned by
#: `test_the_two_languages_spell_the_key_identically`, because a rename on one
#: side is silent on the other and turns the durable field into a body line.
CLAWGATE_TASK_KEY = "clawgate-task"
#: The reason string a dropped preamble `clawgate-task:` line is reported with.
DURABLE_CLAWGATE = "clawgate task"


def _dropped_preamble_task(
    base_pre: str, out_pre: str, line_offset: int, label: str = "(preamble)"
) -> list[DroppedDurable]:
    """`clawgate-task:` lines a wholesale block replacement is about to delete.

    Called for BOTH replaceable blocks — the front matter and the preamble — so
    the two cannot drift apart: whichever one carried the field, losing it is
    reported the same way, with an address the author can open.

    🔴 THE HOLE THE FRONT-MATTER FIX DOES NOT COVER, found by a test written for
    the seam rather than for either component. `split_front_matter` only sees a
    block that is properly CLOSED; an unterminated one is preamble, and
    `merge_report` replaces the whole preamble whenever the update brings its
    own. So a writer who forgets the closing `---` gets the field deleted on the
    next update — the exact silent loss this change exists to stop, one line
    below where it was stopped.

    Deliberately NARROW: only lines carrying this key, not `durable_reason` over
    the whole preamble. A handoff preamble is normally `# Handoff: <topic> —
    <date>`, which carries a DATE, so the general predicate would fire rule (f)
    on every preamble-replacing update in the corpus — a warning on the ordinary
    case, which rule (f)'s own header calls the failure mode to avoid.

    WARNS, never refuses — same contract as the rest of rule (f).
    """
    kept = {" ".join(ln.split()) for ln in out_pre.splitlines() if ln.strip()}
    out: list[DroppedDurable] = []
    for idx, line in enumerate(base_pre.splitlines()):
        if not line.strip().startswith(CLAWGATE_TASK_KEY + ":"):
            continue
        if " ".join(line.split()) in kept:
            continue
        out.append(
            DroppedDurable(label, line_offset + idx + 1, line.rstrip(),
                           DURABLE_CLAWGATE)
        )
    return out


# --- rule (f): does this line look DURABLE? -----------------------------------
#
# 🔴 A FLOOR, NOT A CLASSIFIER, and every renderer of it says "look(s) DURABLE"
# rather than "is". Recall is unknown and unknowable — a durable finding can be
# written in plain prose that no predicate can separate from status — so a
# SILENT run is never evidence that a replace dropped nothing worth keeping. The
# claim it makes is the narrow one: these lines carry a marker that ordinary
# status churn does not.
#
# THREE SIGNALS, tried in that order. The first is imported (see the top of the
# file); the other two exist because that vocabulary alone has almost no reach
# over this corpus. MEASURED 2026-08-20 over the 44 real
# `claudedocs/handoff-*.md` in this repo — 2,626 non-blank, non-fenced lines
# sitting under REPLACE-bucket headings, counted with THIS function's precedence
# (so a line carrying two signals is counted once, under the first):
#
#     openness (imported)      6 lines   0.23%
#     dated claim             46 lines   1.75%
#     evidence verb           11 lines   0.42%
#     ---------------------------------------
#     flagged                 63 lines   2.40%
#
# 🔴 SO THE IMPORTED SCHEMA FIRES ON 6 LINES IN 44 DOCUMENTS, and never once
# through its own `OPEN:` / `RESOLVED <sha>:` markers — all six come from its
# narrow unmarked-action floor. That grammar is the subsystem STORE's journal
# convention, which handoff authors do not write. Importing it is still right (a
# line that DOES declare `OPEN:` under "State now" is durable, and the question
# must not be answered in two places), but shipping it ALONE would have been a
# guard reading as coverage while providing almost none.
#
# Sensitivity is the design constraint, not an afterthought: at ~2% of lines a
# typical "State now" replace of a dozen lines prints nothing. Per SECTION the
# worst case is 40 of 230 (17%) — worst because that assumes a replace carrying
# NOTHING forward; a real update that keeps a flagged line verbatim clears it.
# Widening is not free — see the rejected signals below, each measured on its
# INCREMENTAL half (a match on a line already flagged buys nothing).

# (i) A DATE THE LINE ASSERTS, not one that happens to sit inside a filename.
# Handoff docs cite each other constantly (`handoff-browser-bridge-2026-08-01.md`,
# `apply-nebula-443.sh.LOCAL-preserved-2026-08-02`, `…-eval-2026-07-24.md`), and
# that reference is not a claim about anything. TWO independent nets, each
# measured ALONE over the 67 raw date-bearing lines of the corpus, because they
# overlap almost completely and either one on its own would look unnecessary:
#
#     code-span strip alone     suppresses 21/67
#     leading boundary alone    suppresses 20/67
#     both (shipped)            suppresses 21/67, leaving 46
#
# 🔴 THE BOUNDARY IS LEADING-ONLY, AND A SYMMETRIC ONE WAS MEASURED WRONG. The
# first draft used `(?![\w/.-])` on the trailing side too, by symmetry rather
# than by measurement, and it silently ate four GENUINELY durable lines in the
# corpus — `**DONE 2026-08-20.**` (a `.`), `2026-08-19/20` (a `/`) and
# `merged 2026-08-18T23:28:31Z` (a `\w`) — while suppressing nothing the leading
# half had not already caught. A date's LEFT neighbour is what says it was
# welded into a path token; its right neighbour is ordinary sentence punctuation.
_CODE_SPAN = re.compile(r"`[^`]*`")
_BARE_ISO_DATE = re.compile(r"(?<![\w/.-])\d{4}-\d{2}-\d{2}")

# (ii) The evidence vocabulary — SHOUTED, and deliberately short.
#
# 🔴 `claude/RULES.md`: "a guard on WORDS is walkable by REWORDING". True, and
# accepted here on purpose: this is an ADVISORY that costs a line of output when
# it is wrong and blocks nothing when it is missed, exactly the shape
# `subsystem_resolver._UNMARKED_ACTION` already documents as "a FLOOR, never a
# list". The structural half of rule (f) is the bucket a heading falls in, which
# no rewording touches.
#
# All-caps is load-bearing, not decoration. `decided`, `measured` and `ruled
# out` are ordinary English that turns up in ordinary status prose: over the
# same 2,626 lines this list matched CASE-INSENSITIVELY hits 58 (2.2%) against
# 7 (0.27%) shouted — a 8x widening of the single noisiest axis, on top of the
# dated-claim signal it would mostly duplicate.
#
# 🔴 THE LEADING CLASS EXCLUDES `-`, which is the SAME reasoning the date's
# leading boundary uses: a shouted word welded to its neighbour is a compound
# MODIFIER, not a declaration. Measured — `the loop-CLOSED reframing of the #1
# soak item` is an inventory line about a doc edit, and it was the only thing
# the guard removed: the existing verbs matched 7 lines with it and 7 without,
# so it costs no recall at all.
#
# REJECTED, each measured over the same 2,626 lines, and the split matters —
# a match on a line the OTHER signals already flag adds nothing but noise, so
# what is counted below is the INCREMENTAL half:
#
#   VERIFIED    7 matches, 3 already flagged, 4 incremental — and 3 of those 4
#       are ordinary status: `Both VERIFIED + switched`, a "what shipped" list
#       entry, and 🔴 `- **Deploy/verify status: DEPLOYED AND VERIFIED.**`. That
#       last one is decisive: `Deploy/verify status:` is a field the handoff
#       skill's own step-2 TEMPLATE prescribes, so on any session that deployed
#       successfully this net fires on the template's own status line. That is
#       the definition of the churn rule (f) must stay silent on.
#   CONFIRMED   0 matches. No corpus evidence, so it buys recall that cannot be
#       demonstrated and precision that cannot be defended — the same argument
#       `subsystem_resolver._UNMARKED_ACTION` makes for its own rejections.
#   negative-result phrasing   99 lines (3.8%) — `does not`, `did not`, `never`,
#       `no evidence`, `turned out`. Alone it is larger than the whole shipped
#       predicate: a block on nearly every run, which is the failure mode rule
#       (f) exists to avoid rather than a wider net.
#
# CLOSED was rejected with them in the first draft and that was WRONG, on a
# number that was never broken down. Measured properly: 8 matches, 2 already
# flagged, **6 incremental of which 4 are genuine durable closures** — `is
# **CLOSED and refuted**`, `the fail-open is CLOSED, deployed, and verified`,
# `**CLOSED by PR #185**`, `the close-the-loop thread is now **CLOSED**`. The
# two misses are attributive (`a CLOSED PR`, and the hyphen case above, which
# the leading class now takes). It is also the ONLY candidate that catches a
# closure-shaped finding, which is one of the three field cases this rule exists
# for and the one every other signal is silent on. Cost: 58 -> 63 lines
# (2.21% -> 2.40%), 38 -> 40 of 230 sections.
_EVIDENCE_VERB = re.compile(
    r"(?:^|[^A-Za-z-])"
    r"(MEASURED|RETRACTED|SUPERSEDED|SUPERSEDES|DISPROVED|RULED OUT|WONTFIX"
    r"|CORRECTION|DECIDED|CLOSED)"
    r"(?![a-z])"
)

DURABLE_DATED = "dated claim"
DURABLE_EVIDENCE = "evidence verb"


def durable_reason(line: str) -> str | None:
    """Why this ONE line looks durable, as a short reason, or None.

    The single home of rule (f)'s question. Consumers branch on truthiness and
    PRINT the reason, so a new signal becomes visible in the output rather than
    silently widening a boolean nobody can attribute.

    The openness reason is spelled `openness/<population>` and comes verbatim
    from `subsystem_resolver`, which is what makes the two call sites' agreement
    testable: anything that module calls other than `none` is durable here, so a
    population added upstream is durable by default. That direction is
    deliberate — a new population is a new kind of declared claim, and the
    fail-safe for a warning that cannot refuse is to say more, not less.
    """
    bullets = parse_journal_bullets(line)
    if bullets:
        population = bullets[0].openness_population
        if population != "none":
            return f"openness/{population}"
    prose = _CODE_SPAN.sub(" ", line)
    if _BARE_ISO_DATE.search(prose):
        return DURABLE_DATED
    if _EVIDENCE_VERB.search(prose):
        return DURABLE_EVIDENCE
    return None


def _fence_token(line: str) -> str | None:
    """The fence run opening/closing a code block on this line, if any."""
    m = _FENCE.match(line.strip())
    return m.group(1) if m else None


def split_sections(text: str) -> tuple[str, list[list[str]]]:
    """(preamble, [[heading_line, body], ...]) — FENCE AWARE, and lossless.

    `preamble + "".join(h + b for h, b in sections) == text` exactly, which is
    what lets an untouched section stay byte-identical through a merge rather
    than being re-rendered into a diff nobody asked to approve.

    Fence awareness is not decoration: a handoff doc's step-2 template is a
    fenced markdown block full of `## ` lines, and treating those as real
    headings would shred the doc.
    """
    pre: list[str] = []
    sections: list[list[str]] = []
    open_tok: str | None = None
    for line in text.splitlines(keepends=True):
        tok = _fence_token(line)
        was_open = open_tok
        if open_tok is None:
            if tok:
                open_tok = tok
        elif (
            tok
            and tok[0] == open_tok[0]
            and len(tok) >= len(open_tok)
            and line.strip() == tok
        ):
            open_tok = None
        is_fence_line = tok is not None and (was_open is None or open_tok is None)
        if was_open is None and not is_fence_line and _H2.match(line):
            sections.append([line, ""])
        elif sections:
            sections[-1][1] += line
        else:
            pre.append(line)
    return "".join(pre), sections


def heading_text(heading_line: str) -> str:
    """`## Open investigations — live` -> `Open investigations — live`."""
    return heading_line.lstrip("#").strip()


def append_bucket(heading_line: str) -> str | None:
    """The APPEND_PREFIXES bucket this heading falls in, or None (= replace)."""
    low = heading_text(heading_line).lower()
    for prefix in APPEND_PREFIXES:
        if low.startswith(prefix):
            return prefix
    return None


def _norm_heading(heading_line: str) -> str:
    return " ".join(heading_text(heading_line).lower().split())


BUCKET_APPEND = "APPEND"
BUCKET_REPLACE = "REPLACE"
BUCKET_NEW = "NEW"


class DroppedDurable(typing.NamedTuple):
    """One base line a REPLACE deletes that `durable_reason` flagged."""

    heading: str
    """The BASE heading's text — the one the line was written under."""
    line_no: int
    """1-based line number in the BASE doc, so it can be opened and moved."""
    line: str
    """The line, verbatim apart from trailing whitespace."""
    reason: str


class MergeReport(typing.NamedTuple):
    text: str
    dropped: tuple[DroppedDurable, ...]
    buckets: tuple[tuple[str, str], ...]
    """`(heading text, BUCKET_*)` for each section the update touched, in the
    update's own order — rule (g)."""


def merge(base_text: str, update_text: str) -> str:
    """Rule (c): replace current-state sections, APPEND diagnosis-state ones."""
    return merge_report(base_text, update_text).text


def merge_report(base_text: str, update_text: str) -> MergeReport:
    """`merge()`, plus what rules (f) and (g) need to say about it.

    🔴 ONE MATCHER. The dropped-line classification and the bucket line are
    computed HERE, inside the loop that decides each section's fate, rather than
    by a second pass that re-derives which section matched which. A second pass
    would be free to disagree with this one — and the disagreement would render
    as a warning naming a section the merge did not touch, or silence about one
    it did.

    A section present in the base and absent from the update is left ALONE —
    an update is a delta, not a replacement document, so omitting a section
    never deletes it. A section present only in the update is added at the end.

    FRONT MATTER IS CARRIED, not merged: the base's block survives unless the
    update supplies one of its own (an explicit re-statement wins, so a session
    that genuinely needs to change the recorded task can). See
    `split_front_matter` for why this cannot be left to the preamble rule.
    """
    base_fm, base_text = split_front_matter(base_text)
    upd_fm, update_text = split_front_matter(update_text)
    base_pre, base_secs = split_sections(base_text)
    upd_pre, upd_secs = split_sections(update_text)

    out_pre = upd_pre if upd_pre.strip() else base_pre
    out = [[h, b] for h, b in base_secs]

    by_bucket: dict[str, int] = {}
    by_heading: dict[str, int] = {}
    for i, (h, _b) in enumerate(out):
        bucket = append_bucket(h)
        if bucket is not None:
            by_bucket.setdefault(bucket, i)
        by_heading.setdefault(_norm_heading(h), i)

    # base_fm was stripped above and is still part of the file a reader opens —
    # see _body_start_lines. Dropping it here silently shifts every rule (f)
    # line number on any doc that records a clawgate task.
    body_starts = _body_start_lines(base_pre, base_secs, base_fm)
    dropped: list[DroppedDurable] = []
    # TWO WAYS THE FIELD LEAVES A DOCUMENT WITHOUT ANY SECTION BEING TOUCHED,
    # and the same function reports both — the asymmetry between them was a real
    # gap, not a design:
    #
    #  (a) the update brings its own FRONT MATTER, so `(upd_fm or base_fm)`
    #      discards the base's. "An explicit one wins" is the intended rule and
    #      stays — but a rule whose only statement is prose in a skill is not a
    #      guard, and a delta can claim front matter by ACCIDENT: a `---` used as
    #      a horizontal rule on line 1 with another `---` further down is a
    #      well-formed block to `split_front_matter`. Offset 0 because front
    #      matter starts at line 1 of the file.
    #  (b) the update brings its own PREAMBLE, which is where an UNTERMINATED
    #      block lives. Offset = the height of the real front matter above it.
    if upd_fm and upd_fm != base_fm:
        dropped.extend(_dropped_preamble_task(base_fm, upd_fm, 0, "(front matter)"))
    if out_pre != base_pre:
        dropped.extend(
            _dropped_preamble_task(base_pre, out_pre, base_fm.count("\n"))
        )
    buckets: list[tuple[str, str]] = []

    tail: list[list[str]] = []
    for h, b in upd_secs:
        bucket = append_bucket(h)
        if bucket is not None and bucket in by_bucket:
            i = by_bucket[bucket]
            out[i][1] = _append_body(out[i][1], b)
            buckets.append((heading_text(out[i][0]), BUCKET_APPEND))
        elif bucket is None and _norm_heading(h) in by_heading:
            i = by_heading[_norm_heading(h)]
            # 🔴 Classified BEFORE the body is overwritten — `out[i][1]` is the
            # outgoing text only until the next statement runs.
            dropped.extend(
                _durable_dropped(
                    heading_text(out[i][0]), out[i][1], b, body_starts[i]
                )
            )
            out[i][0] = h
            out[i][1] = _replace_body(out[i][1], b)
            buckets.append((heading_text(h), BUCKET_REPLACE))
        else:
            tail.append([h, b])
            buckets.append((heading_text(h), BUCKET_NEW))

    rendered = (upd_fm or base_fm) + out_pre + "".join(h + b for h, b in out + tail)
    return MergeReport(
        text=rendered.rstrip("\n") + "\n",
        dropped=tuple(dropped),
        buckets=tuple(buckets),
    )


def _body_start_lines(
    pre: str, sections: list[list[str]], front_matter: str = ""
) -> list[int]:
    """1-based line number of each section BODY's first line in the base doc.

    Derived from the same lossless split the merge walks, so a line number can
    never name a line from a different section: `split_sections` guarantees
    `pre + "".join(h + b)` reproduces the document byte-for-byte, which makes
    counting newlines an exact address rather than an estimate.

    🔴 `front_matter` IS PART OF THE FILE THE READER OPENS, so it counts, and it
    is a SEPARATE argument because `merge_report` strips it off `base_text`
    BEFORE `split_sections` ever sees it. Without it every rule (f) warning on a
    doc carrying `clawgate-task:` names a line short by the height of that block
    — and a line number is the whole value of that warning. Losslessness is a
    property of `pre + "".join(h + b)` against the STRIPPED text, so nothing
    else in the walk can notice the missing lines.
    """
    starts: list[int] = []
    # the first heading's own line number, in the WHOLE file
    cur = front_matter.count("\n") + pre.count("\n") + 1
    for _h, b in sections:
        starts.append(cur + 1)
        cur = cur + 1 + b.count("\n")
    return starts


def _unfenced(body: str) -> typing.Iterator[tuple[int, str]]:
    """`(0-based index within body, line)` for lines OUTSIDE code fences.

    Fence lines and their contents are skipped: a sample command or a pasted log
    inside a fence routinely carries a date, and 610 of the corpus's
    REPLACE-bucket lines sit inside one. Flagging those would put the block in
    front of a reader on runs where nothing durable moved at all.
    """
    open_tok: str | None = None
    for idx, line in enumerate(body.splitlines()):
        tok = _fence_token(line)
        if open_tok is None:
            if tok:
                open_tok = tok
                continue
            yield idx, line
        elif (
            tok
            and tok[0] == open_tok[0]
            and len(tok) >= len(open_tok)
            and line.strip() == tok
        ):
            open_tok = None


def _norm_line(line: str) -> str:
    """Whitespace-collapsed, for the carried-forward comparison only."""
    return " ".join(line.split())


def _durable_dropped(
    heading: str, old_body: str, new_body: str, first_line_no: int
) -> list[DroppedDurable]:
    """The durable-looking lines this replace deletes and does not carry forward.

    🔴 CARRIED FORWARD IS AN EXACT (whitespace-collapsed) LINE MATCH, and the
    looseness is deliberately in the LOUD direction: a durable line the author
    reworded while carrying it counts as dropped and gets named. That is a line
    of output on a line the author is already looking at. The other direction —
    treating a near-match as carried — would silence the exact case this rule
    exists for, since a status rewrite of a section naturally reuses much of its
    wording.
    """
    carried = {_norm_line(ln) for ln in new_body.splitlines() if ln.strip()}
    out: list[DroppedDurable] = []
    for idx, line in _unfenced(old_body):
        if not line.strip():
            continue
        reason = durable_reason(line)
        if reason is None or _norm_line(line) in carried:
            continue
        out.append(DroppedDurable(heading, first_line_no + idx, line.rstrip(), reason))
    return out


def _spacing(body: str) -> str:
    """The run of newlines a section body ends with (at least one).

    Preserved across both merge operations so that a section's SPACING is not a
    change the caller has to approve: a replace that silently ate a blank line
    would put whitespace into a diff a human is being asked to read, and would
    make the no-change verdict below unreachable for a genuinely no-op update.
    """
    tail = body[len(body.rstrip("\n")) :]
    return tail or "\n"


def _replace_body(base_body: str, new_body: str) -> str:
    """New content, base spacing."""
    return new_body.rstrip("\n") + _spacing(base_body)


def _append_body(base_body: str, new_body: str) -> str:
    """Base body VERBATIM, then a blank line, then the new material.

    Only trailing newlines are touched — everything a past session wrote comes
    through character-for-character, which is the whole point of rule (c).
    """
    kept = base_body.rstrip("\n")
    added = new_body.strip("\n")
    if not added:
        return base_body
    if not kept:
        return added + _spacing(base_body)
    return kept + "\n\n" + added + _spacing(base_body)


def _canon(text: str) -> str:
    """Whitespace-insensitive form, for the no-change verdict only."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def unified(base_text: str, merged_text: str, relpath: str) -> str:
    """A compact unified diff with git-shaped headers, so it can be compared
    line-for-line against what `git show` prints for the resulting commit."""
    return "".join(
        difflib.unified_diff(
            base_text.splitlines(keepends=True),
            merged_text.splitlines(keepends=True),
            fromfile=f"a/{relpath}",
            tofile=f"b/{relpath}",
            n=3,
        )
    )


# Rule (f), bounded. A doc can drop many lines and the block is printed ABOVE
# the diff, where an unbounded list would push the thing it is annotating off
# the top of the screen. Six is enough to see the shape; the count that follows
# is what stops "…" from reading as "and nothing else worth mentioning".
DROPPED_SHOWN_MAX = 6
DROPPED_LINE_MAX = 140

#: 🔴 The remedy for a dropped `clawgate-task:` — and it is the OPPOSITE of the
#: standing one. That field is read ONLY from a block whose `---` is line 1, so
#: "move it under an APPEND heading" would put it where nothing parses it and
#: /resume would report the doc as naming no task at all.
CLAWGATE_DROP_REMEDY = (
    "  RESTORE IT AT LINE 1, in a closed `---` block — that is the only place\n"
    "  /resume reads it from. Do NOT move it under a heading: a `clawgate-task:`\n"
    "  line anywhere else is invisible to every reader, so the doc would report\n"
    "  as naming no task at all.\n"
    "  This is a WARNING, not a refusal. Dropping it on purpose (the work moved "
    "to another task, or none) is a legitimate update — this only makes it a "
    "decision rather than an accident."
)

DROPPED_REMEDY = (
    "  Move them under an APPEND heading (open investigations / findings / "
    "gotchas) or carry them forward in this update.\n"
    "  This is a WARNING, not a refusal — replacing stale status is the "
    "ordinary case and nothing here blocks it. It is a FLOOR, so a silent run "
    "is not evidence that nothing durable was dropped."
)


def buckets_line(buckets: typing.Sequence[tuple[str, str]]) -> str:
    """Rule (g): which sections replaced, which appended, this run — one line."""
    if not buckets:
        return "buckets: (this update touched no section)"
    shown = " · ".join(f"{_clip(h, 44)} → {bucket}" for h, bucket in buckets)
    return f"buckets: {shown}"


def dropped_durable_report(dropped: typing.Sequence[DroppedDurable]) -> str:
    """Rule (f)'s block, or "" when nothing was flagged.

    Empty on the ordinary run BY DESIGN — the caller prints nothing rather than
    a reassuring "0 durable lines dropped", which would be a line on every run
    saying the same thing and would be read as a guarantee the predicate cannot
    make (see `durable_reason`: recall is unknown).
    """
    if not dropped:
        return ""
    # 🔴 THE FRONT-MATTER CLASS GETS ITS OWN HEADING AND ITS OWN REMEDY, because
    # the standing one SILENTLY DISABLES THE FEATURE. `clawgate-task:` is only
    # read out of a block whose `---` is line 1; an author who follows "move
    # them under an APPEND heading" moves the field somewhere no reader parses,
    # and /resume then prints `(no clawgate-task: field …)` — the exact silent
    # disable this whole change exists to prevent, arrived at by obeying the
    # tool. The ADDRESS was right either way; the label and the advice were not.
    fm_rows = [d for d in dropped if d.reason == DURABLE_CLAWGATE]
    other = [d for d in dropped if d.reason != DURABLE_CLAWGATE]
    blocks: list[str] = []
    if fm_rows:
        blocks.append("\n".join([
            f"🔴 This update DROPS the doc's recorded clawgate task "
            f"({len(fm_rows)} line(s), from the front matter or preamble):",
            *[f"  {_clip(d.heading, 44)}:{d.line_no}: "
              f"{_clip(d.line.strip(), DROPPED_LINE_MAX)}  [{d.reason}]"
              for d in fm_rows[:DROPPED_SHOWN_MAX]],
            CLAWGATE_DROP_REMEDY,
        ]))
    if other:
        rows = [
            f"  {_clip(d.heading, 44)}:{d.line_no}: "
            f"{_clip(d.line.strip(), DROPPED_LINE_MAX)}  [{d.reason}]"
            for d in other[:DROPPED_SHOWN_MAX]
        ]
        elided = len(other) - len(rows)
        if elided:
            rows.append(f"  … and {elided} more not shown (read the diff below).")
        blocks.append("\n".join([
            f"🔴 This replace DROPS {len(other)} line(s) that look DURABLE "
            f"(they sit under a REPLACE heading):",
            *rows,
            DROPPED_REMEDY,
        ]))
    return "\n".join(blocks)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- rule (h): is the BASE the document this update was written against? ------
#
# 🔴 THE INCIDENT. Pointed at a clone 313 commits behind its mainline, this tool
# resolved the base doc from that clone's working tree, found no section matching
# the update's `State now`, and reported `State now → NEW`. Confirming would have
# rebuilt an 891-line / 14-heading document from a 290-line / 9-heading base —
# discarding ~601 lines including a whole incident writeup — and exited 0 having
# said `status=written`. The ONLY tell was one token inside the `buckets:` line,
# which reads as a routine classification.
#
# 🔴 IT WARNS, EXCEPT IN THE ONE SHAPE THAT DESTROYS THE DOCUMENT. Rule (f)'s
# reasoning still holds for the ordinary case — a gate that can stop the write
# becomes a gate people learn to click through, and working on a
# deliberately-behind clone is legitimate — so behind-but-present stays a warning
# at exit 0, and 4 (`no-advance`) / 5 (`no-change`) keep their exact meanings.
# 🔴 But when there is no usable doc HERE and the mainline has one, warning was
# the wrong call: every section arrives NEW, the committed document is REPLACED,
# and the y/N that used to stand between that and a push was retired 2026-08-23.
# That shape exits 7 (`stale-base`) with an explicit override. Scoping the
# refusal to it is what keeps this from being the clicked-through gate above.
#
# 🔴 IT MUST BE SILENT ON THE ORDINARY RUN, and that is measured rather than
# hoped. Every one of the 49 real handoff-doc updates in this repo's history was
# REPLAYED through `merge_report` with the update delta reconstructed from the
# commit, and the tells below fire on 0 and 1 of them respectively:
#
#   canonical heading NEW, membership by CANONICAL PREFIX ..... 0/49  (0.0%)
#   canonical heading NEW, membership by FULL heading text .... 4/49  (8.2%)
#   base smaller than update, LINES only ..................... 10/49 (20.4%)
#   base smaller than update, lines AND sections .............. 1/49  (2.0%)
#
# The two rejected variants are why the rules are shaped the way they are.
# Membership is by prefix because an author re-glossing `## State now` to
# `## State now — THE STORE IS PUBLIC` makes it `NEW` against a base that plainly
# HAS a state-now section (4 real cases, all in one doc) — the heading moved, the
# skeleton did not. And the size tell needs BOTH dimensions because a handoff
# update routinely rewrites more lines than a short doc contains.

# The skeleton of a handoff doc, as PREFIXES — same matching discipline as
# `APPEND_PREFIXES` and for the same reason: the canonical spellings carry a
# trailing gloss an updating session will not reproduce. Occurrence across the
# 44 real handoff docs in this repo (2026-08-21): how-to-verify 27, next-steps
# 26, state-now 25, goal 24, gotchas 21, open-investigations 14, what-shipped 11.
CANONICAL_HEADING_PREFIXES: tuple[str, ...] = (
    "goal",
    "state now",
    "next steps",
    "how to verify",
    "what shipped",
    *APPEND_PREFIXES,
)

# A base with fewer sections than this is a stub, not an established handoff, and
# a canonical heading arriving NEW in one is ordinary growth. Of the 44 real docs
# exactly one has 3 sections and three have 4; the median is 6.
MIN_ESTABLISHED_SECTIONS = 4


def canonical_prefix(heading: str) -> str | None:
    """The skeleton section this heading TEXT belongs to, or None."""
    low = " ".join(heading.lower().split())
    for prefix in CANONICAL_HEADING_PREFIXES:
        if low.startswith(prefix):
            return prefix
    return None


class DocShape(typing.NamedTuple):
    """How big a document is, and which skeleton sections it carries."""

    sections: int
    lines: int
    canonical: frozenset[str]


def doc_shape(text: str) -> DocShape:
    """Measured with the SAME splitters the merge uses, so the numbers in the
    warning are the numbers the merge acted on — front matter stripped, fences
    respected."""
    _fm, body = split_front_matter(text)
    _pre, secs = split_sections(body)
    canon = {c for c in (canonical_prefix(heading_text(h)) for h, _ in secs) if c}
    return DocShape(len(secs), len(text.splitlines()), frozenset(canon))


def wrong_base_tells(
    base_text: str,
    update_text: str,
    buckets: typing.Sequence[tuple[str, str]],
) -> tuple[str, ...]:
    """Reasons to doubt the base, in the update's own order. Empty on an ordinary
    run — see the measurement above.

    Returns "" reasons, not a boolean, because the two tells are different
    evidence and a reader who is told which one fired can settle it in one
    command. An empty tuple is NOT a claim that the base is current; that
    question is `base_currency`'s, and it is the one with hard evidence.
    """
    if nothing_to_merge_into(base_text):
        # No base at all. Every heading is NEW by construction, so the tell
        # would fire on every genuine first write. `base_currency` still speaks:
        # a doc that is absent HERE and present on the mainline is the same bug.
        # 🔴 Shares the predicate rather than open-coding `.strip()` a third
        # time: a third copy is a third chance to drift, and drift between the
        # first two is what rounds 2 and 3 of this PR's audit were spent on.
        return ()
    base = doc_shape(base_text)
    upd = doc_shape(update_text)
    tells: list[str] = []
    if base.sections >= MIN_ESTABLISHED_SECTIONS and base.canonical:
        for heading, bucket in buckets:
            prefix = canonical_prefix(heading)
            if bucket != BUCKET_NEW or prefix is None or prefix in base.canonical:
                continue
            tells.append(
                f"`{_clip(heading, 56)}` came back {BUCKET_NEW} — but this base "
                f"has {base.sections} sections and NOT ONE of them is a "
                f"`{prefix}` section. On an established handoff doc that is a "
                f"skeleton section MISSING from the base, not a section being "
                f"introduced."
            )
    if upd.sections > base.sections and upd.lines > base.lines:
        tells.append(
            f"the incoming update is LARGER than the base it merges into "
            f"({upd.sections} sections / {upd.lines} lines against the base's "
            f"{base.sections} / {base.lines}). An update is a delta; a delta "
            f"bigger than the whole document is a base missing most of it."
        )
    return tuple(tells)


def nothing_to_merge_into(base_text: str) -> bool:
    """Is there no usable base document here — missing, empty, or whitespace?

    🔴 ONE predicate, FOUR consumers. `wrong_base_tells` asks it to raise a
    tell; `BaseCurrency.replaces_mainline_doc` builds the REFUSAL on it; and the
    WARNING reaches it through that same method rather than calling here
    directly — deliberately, because the warning must describe the refusal's
    set, not this one's. This function answers only "is there anything here to
    merge into"; whether that MATTERS additionally needs a mainline copy to
    lose AND that copy being itself non-blank, and `replaces_mainline_doc` is the
    only place those THREE facts are combined. 🔴 It is now FOUR consumers, not
    three: `base_currency` also runs it over the MAINLINE text, which is what
    makes both sides of the equivalence class one predicate instead of two.

    MEASURED: they disagreed for exactly one round. The refusal was widened to
    `.strip()` while the warning kept `if not local.lines:`, which is true only
    for a strictly 0-line file — so a `"\\n"` local doc got the weak
    `0 sections / 1 lines` line and never the loud one, in precisely the shape
    where every section merges as NEW. The two had been equivalent before the
    widening (`not base_text` ⟺ `splitlines() == []`), which is why the drift
    was silent.
    """
    return not base_text.strip()


class BaseCurrency(typing.NamedTuple):
    """Whether the base document is the newest COMMITTED copy this clone can see.

    🔴 `unmeasured` is never a 0 and never a clean bill. It carries the reason
    the question could not be answered, and the renderer prints it — but only
    beside a tell, so an ordinary run stays silent.
    """

    base_ref: str | None
    ladder: tuple[str, ...]
    doc_behind: int | None
    """Commits touching THIS doc that the mainline has and HEAD does not."""
    clone_behind: int | None
    """Commits the mainline has that HEAD does not, whole-repo. Context, not the
    trigger: a clone can be far behind on code with a perfectly current doc, and
    warning on that would fire on nearly every agent worktree."""
    mainline: DocShape | None
    """The mainline copy's shape — read ONLY when `doc_behind` is non-zero, so
    the ordinary run costs no extra `git show`."""
    unmeasured: str | None
    mainline_blank: bool | None = None
    """Is the mainline copy itself nothing-to-merge-into — missing, empty or
    whitespace? `None` means NOT MEASURED, never "it has content".

    🔴 A FLAG, not a shape-derived guess, and that is the whole point. Asking
    `DocShape` this question is what went wrong twice: `bool(lines)` calls a
    whitespace-only mainline "a document to lose" (`"\\n"` is 1 line), and
    `bool(sections)` fails OPEN on a real prose doc that happens to carry no
    `##` heading — MEASURED, `DocShape(sections=0, lines=6)`, which the whole
    guard exists to protect. Neither quantity answers it, so `base_currency`
    puts the mainline TEXT through `nothing_to_merge_into` — the same predicate
    the local side uses — at the point it already holds that text.
    """

    @property
    def stale(self) -> bool:
        return bool(self.doc_behind)

    def replaces_mainline_doc(self, base_text: str) -> bool:
        """🔴 The one destructive shape: NOTHING here to merge into, and a real
        document on the mainline. Every section then arrives NEW and the
        committed copy is replaced wholesale by this delta.

        Deliberately narrower than `stale`, in both directions:

        * a doc PRESENT here but behind is NOT this — the merge can still
          classify its sections, so updating a knowingly-behind clone stays
          legitimate and stays a warning;
        * a doc absent on BOTH sides is the ordinary NEW-doc case that the
          skill says step 5 owns, and refusing it would make first writes
          impossible.

        `mainline` is populated only when `doc_behind` is non-zero and the
        `git show` succeeded, so it carries both facts and an UNMEASURED
        currency can never satisfy this — an unanswered question must not
        become a refusal.

        🔴 `nothing_to_merge_into`, NOT a bare falsiness test. MEASURED against
        a mainline doc of 6 sections: `""` refused (rc 7), but `"\\n"` and
        `"   \\n\\n"` both exited 0 and REPLACED it. The merge treats all three
        identically — 0 sections, so every section arrives NEW — so a doc that is
        whitespace is exactly as absent as one that is missing, and the bare test
        let the guard be walked by a single newline.

        🔴 AND THE MAINLINE MUST HAVE SOMETHING TO LOSE — `is not None` is not
        that test. A committed but EMPTY mainline doc parses to
        `DocShape(0, 0, …)`, which is not None, so the refusal fired on it:
        MEASURED, an empty committed mainline copy plus no local doc exited 7
        `NOTHING WRITTEN` and printed "and <ref> has one (0 section(s) / 0
        line(s))" — a self-contradicting sentence, blocking a legitimate first
        write in a shape where NOTHING is destroyed. That is the exact mirror of
        the bug this guard exists to fix, and an earlier docstring here claimed
        the combination was already right. Both sides must be non-empty for a
        replacement to cost anything.
        """
        # 🔴 `mainline is not None` is REDUNDANT to the predicate and kept on
        # purpose: it is the type-narrowing contract `wrong_base_report` relies
        # on when it dereferences `currency.mainline.sections`. Both fields are
        # assigned in the SAME branch of `base_currency`, so `mainline_blank is
        # False` already implies it — which makes a mutant that deletes this
        # line alone EQUIVALENT, not a coverage gap. It is recorded as an
        # expected SURVIVE in the sweep rather than counted as coverage; the
        # alternative is deleting a line that documents an invariant two
        # functions apart.
        return (
            nothing_to_merge_into(base_text)
            and self.mainline is not None
            and self.mainline_blank is False
        )


def base_currency(repo: Path, relpath: str) -> BaseCurrency:
    """Is the base doc behind its mainline? READ-ONLY, and it does NOT fetch.

    🔴 The mainline ref is DERIVED (`git_mainline`), never a hardcoded `main`.
    The clone this incident happened in has mainline `trunk`; a hardcoded ladder
    would have answered "cannot measure" there and printed nothing at all — the
    silence being fixed, arrived at a second way.

    No fetch by design: this tool is invoked inside a confirm gate a human is
    waiting on, and a network round-trip is not something to add there. The
    counts are therefore a FLOOR against refs already fetched — never an
    overstatement, and a clone that has never fetched can be far worse.
    """
    base_ref, ladder = git_mainline.resolve_base_ref(repo)
    if base_ref is None:
        return BaseCurrency(
            None, ladder, None, None, None,
            f"no mainline ref resolves in this clone (tried {', '.join(ladder)})",
        )
    doc_behind = git_mainline.commits_behind(repo, base_ref, path=relpath)
    clone_behind = git_mainline.commits_behind(repo, base_ref)
    if doc_behind is None:
        return BaseCurrency(
            base_ref, ladder, None, clone_behind, None,
            f"git could not count commits to {relpath} in HEAD..{base_ref}",
        )
    mainline: DocShape | None = None
    mainline_blank: bool | None = None
    if doc_behind:
        shown = git_allow(repo, "show", f"{base_ref}:{relpath}")
        if shown.code == 0:
            mainline = doc_shape(shown.out)
            # 🔴 The SAME predicate the local side uses, on the mainline TEXT.
            # Measured here rather than inferred from `mainline` later, because
            # this is the only place the text exists.
            mainline_blank = nothing_to_merge_into(shown.out)
    return BaseCurrency(base_ref, ladder, doc_behind, clone_behind, mainline,
                        None, mainline_blank)


WRONG_BASE_REMEDY = (
    "  Settle it BEFORE confirming: read the mainline copy — `git -C {repo} "
    "show {ref}:{relpath}` — and re-run against a current clone if it is the "
    "fuller document.\n"
    "  Updating a deliberately-behind clone is legitimate, so on its own this is "
    "a WARNING and no exit code changed. It is a FLOOR: a silent run is NOT "
    "evidence that the base is current.\n"
    "  🔴 ONE shape refuses instead — no usable doc here while the mainline has "
    "one — and it refuses ONLY on `--confirm`, as `status=stale-base` (exit 9). "
    "A proposal run therefore NEVER prints that line whatever shape it is in, so "
    "its absence here is not evidence you are in the benign case: read the line "
    "above instead, which fires on exactly the shape that refuses."
)


def wrong_base_report(
    tells: typing.Sequence[str],
    currency: BaseCurrency,
    relpath: str,
    repo: Path,
    local: DocShape,
    replaces_mainline: bool,
) -> str:
    # 🔴 `replaces_mainline` MUST come from `currency.replaces_mainline_doc(...)`
    # — the assert below makes any other True illegal. The type is a bare
    # `bool`, so the signature cannot say so and this comment has to.
    """Rule (h)'s block, or "" when there is nothing to say.

    Printed when the currency check found the base STALE (hard evidence), or
    when a tell fired (soft evidence) — and in the soft case the currency
    verdict is printed WITH it, including the reason it could not be taken, so
    the reader is never handed a suspicion with no way to settle it.
    """
    if not currency.stale and not tells:
        return ""
    lines: list[str] = []
    if currency.stale:
        behind = currency.doc_behind
        lines.append(
            f"🔴 THE BASE DOCUMENT IS NOT THE NEWEST COMMITTED COPY — "
            f"{currency.base_ref} has {behind} commit(s) to {relpath} that this "
            f"checkout does not."
        )
        # 🔴 THE ABSENT CASE IS CHECKED FIRST, not folded into the size line. A
        # base of "0 sections / 0 lines" is technically the same fact and reads
        # as a formatting artefact; the reader needs to be told the document is
        # not here at all, because that is the case where EVERY section merges as
        # NEW and the committed doc is replaced wholesale by the delta.
        #
        # 🔴 THE WHOLE REFUSAL PREDICATE, not a half of it. Two rounds got this
        # wrong in two different ways, and the second was worse than the first:
        #   round 1 used `not local.lines` — true only for a strictly 0-line
        #     file, so a `"\n"` base got the mild branch;
        #   round 2 used the blank half ALONE — which fires when the mainline
        #     has DELETED this doc (a retirement, revert or rename), where
        #     nothing is replaced. It then printed "and <ref> has one … will be
        #     replaced by this delta" about a document that does not exist, and
        #     the remedy told the operator confirming would refuse. It does not:
        #     it exits 0 and writes.
        # So this takes `currency.replaces_mainline_doc(base_text)` itself. The
        # loud line and the refusal are now the SAME condition by construction
        # rather than by two expressions that have to be kept in step.
        if replaces_mainline:
            # `replaces_mainline` implies `mainline is not None` AND a
            # non-zero line count, so there is no absent-shape branch to write:
            # an `else ""` here would be dead code that reads as a handled case.
            assert currency.mainline is not None  # implied by replaces_mainline
            shape = (f" ({currency.mainline.sections} sections / "
                     f"{currency.mainline.lines} lines)")
            lines.append(
                f"  this checkout has no usable {relpath} — missing, empty or "
                f"whitespace, which the merge treats identically — and "
                f"{currency.base_ref} has one{shape} — every section will merge "
                f"as {BUCKET_NEW} and the committed document will be replaced by "
                f"this delta."
            )
        elif currency.mainline is not None:
            lines.append(
                f"  base being merged into: {local.sections} sections / "
                f"{local.lines} lines   ·   {currency.base_ref}: "
                f"{currency.mainline.sections} sections / "
                f"{currency.mainline.lines} lines"
            )
        if currency.clone_behind:
            lines.append(
                f"  (this clone is {currency.clone_behind} commit(s) behind "
                f"{currency.base_ref} overall — a floor: nothing here fetched.)"
            )
    if tells:
        lines.append(
            f"🔴 THIS MERGE LOOKS LIKE IT RESOLVED THE WRONG BASE "
            f"({len(tells)} tell(s)):"
        )
        lines.extend(f"  - {t}" for t in tells)
        if not currency.stale:
            if currency.unmeasured:
                lines.append(
                    f"  base currency UNCHECKED: {currency.unmeasured}. That is "
                    f"not a clean reading — check the base by hand."
                )
            else:
                lines.append(
                    f"  base currency: 0 commit(s) to {relpath} in "
                    f"HEAD..{currency.base_ref}"
                    + (
                        f", though the clone is {currency.clone_behind} behind "
                        f"overall"
                        if currency.clone_behind
                        else ""
                    )
                    + " — so the base is the newest copy this clone has FETCHED."
                )
    lines.append(
        WRONG_BASE_REMEDY.format(
            repo=repo, ref=currency.base_ref or "<mainline>", relpath=relpath
        )
    )
    return "\n".join(lines)


def advance_is_real(advanced: str | None) -> bool:
    """Rule (d), as a predicate — one place, so the CLI and the tests agree."""
    if advanced is None:
        return False
    return advanced.strip().lower() not in NO_ADVANCE_SENTINELS


class GitError(RuntimeError):
    pass


def resolve_branch(repo: Path, override: str | None) -> str:
    """The branch a push would land on. Resolved BEFORE the write, not after.

    Called UNCONDITIONALLY by `main()`, not only under `--push`: the not-pushed
    report below names the branch, and a local commit whose branch is not stated
    is a commit the next session cannot find. Under `--push` a failure here still
    refuses; without it a failure is only a missing NAME, never a refusal — see
    `main()`.
    """
    if override:
        return override
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch == "HEAD":
        raise GitError("detached HEAD and no --branch given; refusing to guess")
    return branch


# The branch names a converger fast-forwards. A FALLBACK, not the primary
# signal — see `branch_is_shared`.
SHARED_BRANCH_NAMES: frozenset[str] = frozenset({"main", "master", "trunk"})


def branch_is_shared(repo: Path, remote: str, branch: str) -> bool:
    """Is `branch` one other people push to — i.e. does an un-pushed commit on
    it become the `ship.sh`-skip hazard rather than merely unsaved work?

    It decides only WHICH REMEDY the not-pushed report names, and the two cases
    genuinely differ: on a feature branch `git push` is the answer, while several
    repos (devrc among them) forbid committing to their shared branch at all, so
    printing `push … HEAD:refs/heads/main` there would be a pasteable command the
    target repo's own rules refuse. A wrong pasteable command is worse than a
    descriptive one.

    TWO SIGNALS, UNIONED, because either alone is wrong somewhere:

      * the NAME list — right for the overwhelming majority, blind to a repo
        whose shared branch is called something else (this module's own history
        records a concurrent `git fetch origin stable`);
      * `refs/remotes/<remote>/HEAD` — structural and exact where it is set, but
        `git init` + `git remote add` never creates it, so it is simply absent in
        many checkouts (and in this suite's fixture repo).

    Unioned rather than layered so neither can VETO the other: a false `True`
    costs one extra line of prose, a false `False` costs the louder half of the
    warning on exactly the branch where it matters most. That is the fail-safe
    direction.

    Read-only: `symbolic-ref` on a remote-tracking ref reads a local ref file and
    writes nothing, so this adds no side effect to a path whose whole property is
    that it touches nothing it was not asked to.
    """
    if branch in SHARED_BRANCH_NAMES:
        return True
    head = git_allow(repo, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
    return head.code == 0 and head.out.strip() == f"refs/remotes/{remote}/{branch}"


# 🔴 The headline, and it is deliberately ONE line without alarm punctuation.
# `--confirm` without `--push` is a SUCCESS — this is information, not a refusal
# — so it must not read like `status=push-failed`, whose nine-line 🔴 block
# describes the IDENTICAL end state reached by a failure. What was missing was
# never the alarm; it was the fact.
NOT_PUSHED_HEADLINE = (
    "NOT PUSHED — the commit exists only in this checkout; push it or open a "
    "PR in THIS session."
)

# 🔴 MEASURED AT TWO POINTS, and it is why a COMMAND is named at all rather than
# a re-run. The obvious retry — the identical command plus `--push` — does not do
# what a caller expects, and WHICH way it fails depends on the delta's sections:
#
#   A. a delta that only REPLACES ("## State now") — the doc on disk now equals
#      the merge result, the no-change guard fires first: exit 5, remote unmoved.
#   B. a delta carrying an APPEND section ("## Findings") — rule (c) appends a
#      SECOND copy, so the run succeeds and pushes a doc with the update in it
#      twice, plus an extra commit. Silently. This is the worse half and it is
#      invisible from the exit code, which is 0.
#
# The first draft of this note asserted only case A, from a single measurement on
# a replace-only fixture. `test_the_retry_…` caught it by running case B.
NOT_PUSHED_RETRY_NOTE = (
    "  Do NOT retry by re-running this tool with --push — the doc on disk "
    "already carries this update, so a second run either exits 5 `no-change` (a "
    "delta that only replaces sections) or APPENDS your findings a second time "
    "and pushes the duplicate. Push the commit you already have."
)


def not_pushed_report(repo: Path, remote: str, branch: str | None) -> str:
    """What `--confirm` without `--push` owes its caller, in three or four lines.

    🔴 A COMMAND, NOT A DESCRIPTION — but only a command that is safe in its
    widest reading. `git push` is safe by construction: one that should not
    happen is REJECTED, so it cannot damage a tree the way the `behind` path's
    `merge --ff-only` can, which is why this report needs no dirty-tree check.
    On a shared branch the safe command is not a push at all (see
    `branch_is_shared`), and with no resolvable branch there is no push target,
    so both of those get the preserve-on-a-topic-branch route instead — the same
    one `status=push-failed` and this repo's diverged-host recipe already name.
    """
    topic = (
        f"    git -C {repo} branch <topic> HEAD && "
        f"git -C {repo} push -u {remote} <topic>"
    )
    if branch is None:
        why = (
            "  There is no branch to push from (detached HEAD, no --branch), so "
            "the commit is reachable only from HEAD. Give it a name first:"
        )
    elif branch_is_shared(repo, remote, branch):
        # 🔴 The `ship.sh` consequence is SCOPED, never asserted of every repo —
        # stating it flatly once taught a reader that a stranded commit in an
        # unrelated repo blocks it, and they repeated the claim.
        why = (
            f"  `{branch}` is a SHARED branch. In a devrc checkout an un-pushed "
            f"commit there is the state `ship.sh` skips over silently; elsewhere "
            f"it is a commit on a branch other people push to. Several repos "
            f"forbid committing to `{branch}` at all, so do NOT push from here — "
            f"preserve it on a topic branch and open a PR:"
        )
    else:
        return (
            f"{NOT_PUSHED_HEADLINE}\n"
            f"    git -C {repo} push -u {remote} HEAD:refs/heads/{branch}\n"
            f"{NOT_PUSHED_RETRY_NOTE}"
        )
    return f"{NOT_PUSHED_HEADLINE}\n{why}\n{topic}\n{NOT_PUSHED_RETRY_NOTE}"


COMMIT_LANDED_NOTE = (
    "\n🔴 THE COMMIT LANDED — a later step failed, so nothing was rolled back "
    "(rolling back here would DISCARD a committed change). This is the one "
    "`status=failed` that does NOT mean 'nothing happened': the commit exists "
    "locally and is un-pushed. Find it with `git log -1` and push it or open a "
    "PR; do not re-run this tool, which would append the update a second time."
)


def _undo_write(
    repo: Path, doc: Path, relpath: str, original: bytes | None
) -> str:
    """Undo the doc write + `git add` after a commit that never happened.

    🔴 PATH-LIMITED, exactly like the commit it is undoing. A blanket
    `git reset` would unstage a co-worker's staged files as a side effect of OUR
    failure — trading one shared-checkout defect for a worse one. `git restore
    --staged -- <path>` touches only the index entry for that path.

    Best-effort and non-raising: this runs on an error path, and a rollback that
    threw would replace the caller's real diagnosis with its own. Whatever it
    could not undo is RETURNED as text so the caller prints it — silence here
    would be the same defect one level down.
    """
    left: list[str] = []
    try:
        # Unstage first: if restoring the bytes fails we still want the index
        # clean, because a staged path is the half that another session's
        # `git commit` picks up.
        git(repo, "restore", "--staged", "--", relpath)
    except (GitError, OSError):
        # `git restore` predates nothing we support, but a very old git or a
        # path git no longer knows about can still refuse.
        try:
            git(repo, "reset", "--quiet", "HEAD", "--", relpath)
        except (GitError, OSError):
            left.append(f"still STAGED: {relpath}")
    try:
        if original is None:
            doc.unlink(missing_ok=True)
        else:
            doc.write_bytes(original)
    except OSError:
        left.append(f"still MODIFIED: {relpath}")
    if left:
        # 🔴 EMIT ADVICE ONLY FOR THE HALF THAT ACTUALLY FAILED. The two halves
        # fail independently, and printing both was measured to produce a message
        # that CONTRADICTS what happened: with only the index half failing, it
        # told the operator their content "was never committed … restore by hand"
        # while the bytes had in fact been restored and the content WAS in HEAD.
        # An operator who believes that hand-rewrites a doc they still have.
        #
        # 🔴 And do not name `restore --source=HEAD --worktree` in the worktree
        # arm: it discards UNCOMMITTED local edits — exactly what `original`
        # exists to preserve — and fails outright when the doc did not exist at
        # HEAD. Advice printed on an already-degraded path must not be the thing
        # that loses the work.
        fixes: list[str] = []
        if any(s.startswith("still STAGED") for s in left):
            # 🔴 CONTRACT: the index arm is ONE bare command line, no comment
            # lines. The worktree arm is comment lines in every wording it has
            # had, so "no comment lines present" is how the test proves the
            # worktree half was NOT advised — a check that survives rewording
            # AND reindenting. Adding a comment here will fail that test; that
            # is deliberate (a loud false failure beats a silent false pass),
            # so move any explanation into the message body above instead.
            fixes.append(f"    git -C {repo} restore --staged -- {relpath}")
        if any(s.startswith("still MODIFIED") for s in left):
            fixes.append(
                f"    # the doc did not exist before this run — delete it:\n"
                f"    rm -f -- {doc}"
                if original is None
                else
                "    # its previous content is the bytes this process read, and "
                "they are\n    # not necessarily in git. Run the unstage line "
                "above FIRST — until you do,\n    # the index still holds THIS "
                "run's merged text, so `git restore -- <path>`\n    # would "
                "restore that and look like it worked. Only after unstaging,\n"                "    # and only if the doc was clean at HEAD, is it safe."
            )
        return (
            "\n🔴 ROLLBACK INCOMPLETE — the commit did not happen, but this tree "
            "was left changed: " + "; ".join(left) + "\n"
            "  Fix it before doing anything else; in a shared checkout a staged "
            "path is one `git commit` away from being swept into someone else's "
            "commit.\n" + "\n".join(fixes)
        )
    # 🔴 Deliberately NOT "byte-identical": two measured exceptions. If the doc
    # was STAGED-modified before the run, `restore --staged` resets its index
    # entry to HEAD rather than to that staged content; and a `claudedocs/`
    # directory this run created is not removed. Both are harmless, and neither
    # is what the sentence would be claiming. A comment is a claim — say the
    # thing that is true, which is the thing the caller actually needs.
    return (
        "\n(rolled back: the doc was restored and unstaged, so nothing from this "
        "run is left staged or written — re-running is safe and will not append "
        "the update twice.)"
    )


def uncommitted_paths(repo: Path) -> list[str]:
    """Paths with uncommitted changes in `repo`'s working tree, staged or not.

    🔴 THIS DECIDES WHICH REMEDY THE `behind` MESSAGE OFFERS, and the hazard it
    measures is NOT "which repo is this". `merge --ff-only` into a tree holding
    someone else's uncommitted work either refuses or overwrites it, and in a
    shared clone that work is routinely not yours: measured in `$DATAPACKET`
    2026-08-19, **38 dirty paths** across at least three sessions while the clone
    sat **90 commits behind**. That repo's own rules forbid `commit`, `add`,
    `stash`, `checkout` and `switch` in the primary clone for exactly this reason.

    So the tool measures the tree instead of enumerating repos — an enumeration
    would be wrong for the next shared checkout nobody added to it.

    UNREADABLE ⇒ TREAT AS DIRTY. A tree we cannot inspect is one we must not
    recommend mutating; the fail-safe direction is the cautious remedy.

    🔴 `--no-optional-locks`, AND IT IS NOT OPTIONAL HERE. A plain `git status`
    REFRESHES THE INDEX and writes `.git/index` — on a shared gitdir that is a
    side effect on every other worktree, and this module already forbids exactly
    that. The existing no-write guard caught this the first time the check
    shipped, which is the guard doing its job. The flag makes the read take no
    lock and write nothing; the cost is that a stat-dirty file can be reported
    as modified when its content matches, which errs toward the cautious remedy
    and is the right direction for this decision.
    """
    try:
        out = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ["<could not read the working tree>"]
    if out.returncode != 0:
        return ["<could not read the working tree>"]
    return [ln[3:].strip() for ln in out.stdout.splitlines() if ln.strip()]


def remote_has_commits_we_lack(repo: Path, remote: str, branch: str) -> bool:
    """Would a push to `<remote>/<branch>` be rejected non-fast-forward?

    🔴 `ls-remote`, NOT `fetch` — three measured reasons, all found by audit after
    the fetch version shipped:

      1. **`fetch` REINTRODUCED THE BUG THIS GUARD EXISTS TO CLOSE.** It wrote
         `FETCH_HEAD` and a second process then read `HEAD..FETCH_HEAD`.
         `FETCH_HEAD` is shared mutable state: any other fetch in that checkout
         between the two wins. Measured on a checkout genuinely 1 behind — after
         another session's `git fetch origin stable`, the check returned **0**.
         A confident zero here means write → commit → push rejected → a stranded
         commit on a shared branch. `drift-check.sh` fetches on a systemd timer,
         so the racer is unattended.
      2. **`fetch` is NOT read-only, and the earlier comment saying so was false.**
         It writes `refs/remotes/<remote>/<branch>` in the COMMON gitdir — shared
         by every worktree — plus objects and reflogs. Two concurrent
         `git fetch --quiet origin main` produced `cannot lock ref` in **30 of 30**
         trials. Fail-safe, but a new transient refusal of the handoff.
      3. **`fetch <remote> <branch>` FAILS when the branch is not on the remote
         yet**, which made a first push impossible — an ordinary end-of-session
         state, hard-refused with no way past it.

    `ls-remote` writes NOTHING locally (measured: added/changed/removed all empty)
    and 12/12 concurrent runs exited 0.

    Returns False — pushable — when the branch does not exist on the remote (a
    first push cannot be rejected non-fast-forward) and when the remote tip is an
    ancestor of HEAD (ahead-only). True when the remote has anything HEAD lacks,
    which covers behind AND diverged.

    🔴 A LOOKUP THAT FAILS IS NOT "PUSHABLE". Network down, no such remote, auth
    expired, or a remote tip this repo has never fetched — each RAISES, and the
    caller refuses rather than guessing. Guessing pushable strands the commit in
    exactly the way this whole guard exists to prevent.
    """
    out = git_allow(repo, "ls-remote", "--exit-code", remote, f"refs/heads/{branch}")
    if out.code == 2:
        return False  # no such branch on the remote — a first push
    if out.code != 0:
        raise GitError(
            f"cannot read {remote}/{branch}: {out.err.strip() or f'git exited {out.code}'}"
        )
    tip = out.out.split()[0] if out.out.split() else ""
    if not tip:
        raise GitError(f"{remote}/{branch} returned no sha")
    # 🔴 One check, not two. A `cat-file -e` guard for "a tip this repo has
    # never fetched" was here and was REDUNDANT: `merge-base --is-ancestor` on an
    # unknown object exits non-zero too, which is already the refuse answer. Two
    # branches reaching one outcome cannot be told apart by any test — deleting
    # either left the suite green — and that is the dead-predicate shape. The
    # fail-safe is the non-zero, so state it once:
    #   ancestor  -> 0     -> ahead-only, pushable
    #   not       -> 1     -> behind or diverged, refuse
    #   unknown / any error-> refuse (this is the property, not an accident)
    return git_allow(repo, "merge-base", "--is-ancestor", tip, "HEAD").code != 0


class GitRun(typing.NamedTuple):
    code: int
    out: str
    err: str


def git_allow(repo: Path, *args: str) -> GitRun:
    """`git` that RETURNS its exit code. `ls-remote --exit-code` uses 2 to mean
    "no such ref", which is an answer, not a failure — raising on it is what made
    a first push impossible."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    return GitRun(proc.returncode, proc.stdout, proc.stderr)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc.stdout


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="handoff_doc.py",
        description="write or update a handoff doc behind a confirm gate — the "
        "doc's only writer, whether or not it already exists",
        # 🔴 argparse abbreviates long options by DEFAULT, which makes a guard
        # spelled as a long name walkable by shortening it. MEASURED: with the
        # default, `--al` was accepted as `--allow-replacing-mainline-doc` and
        # replaced the committed document — while that flag's own help text
        # claimed it was "deliberately long" so it could not be passed by
        # reflex. A prefix of a destructive override is exactly the reflex it
        # was named to prevent. No caller here passes an abbreviated flag.
        allow_abbrev=False,
    )
    p.add_argument("--repo", required=True, help="repo root the handoff lives in")
    p.add_argument("--topic", required=True, help="handoff topic slug")
    p.add_argument(
        "--update",
        required=True,
        help="file holding the proposed sections (## headings). A DELTA when the "
        "doc exists — omit a section and it is left alone. The WHOLE doc when it "
        "does not: with no base the file becomes the doc verbatim, front matter "
        "included, so its line 1 is the doc's line 1.",
    )
    p.add_argument(
        "--advanced",
        help="one line: what changed since the doc was written. Required — "
        "without it, or with a value that means nothing changed, no diff is "
        "offered and nothing is written (rule d).",
    )
    p.add_argument(
        "--new-effort",
        action="store_true",
        help="rule (i-b): assert that this topic is a genuinely NEW effort and "
        "not a second doc for one that already has one. Required only when the "
        "doc does not exist AND the repo already has handoff docs; the refusal "
        "lists them so the right one can be updated instead.",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="land it: write the doc and make exactly one commit of that path. "
        "Run this ONLY after a human answered y to the diff the default mode printed.",
    )
    p.add_argument(
        "--allow-replacing-mainline-doc",
        action="store_true",
        help="override the status=stale-base refusal: proceed even though this "
        "checkout has no copy of the doc while the mainline does, REPLACING the "
        "committed document with this delta. Deliberately long: the ordinary fix "
        "is to re-run against a current clone, and an override that reads like a "
        "routine flag is one that gets passed by reflex.",
    )
    p.add_argument(
        "--push",
        action="store_true",
        help="also push the commit. Requires --confirm; this is the half the gate exists for.",
    )
    p.add_argument("--remote", default="origin")
    p.add_argument("--branch", help="defaults to the repo's current branch")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return EXIT_FAIL
    if args.push and not args.confirm:
        print(
            "--push requires --confirm: the push is the half the gate exists "
            "for, so it never happens without the confirmed write.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    relpath = f"claudedocs/handoff-{args.topic}.md"
    doc = repo / relpath

    # ---- rule (i): is this the RIGHT DOCUMENT to be writing at all? ----------
    # 🔴 ASKED BEFORE RULE (d), and the order is deliberate: rule (d) asks
    # whether this doc's CONTENT advanced, which is only a meaningful question
    # once the doc is the right one. A dated topic that also went nowhere should
    # be told about the date — re-running it with a better `--advanced` would
    # otherwise "fix" it into creating the per-session doc.
    dated = topic_carries_a_date(args.topic)
    if dated:
        print(
            f"status=dated-topic\n"
            f"NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
            f"  `--topic {args.topic}` carries a date ({dated}), so it names a "
            f"PER-SESSION document: next session's date differs, so this doc can "
            f"never be updated in place and a second one gets created instead.\n"
            f"  Operator decision 2026-08-28: ONE handoff doc per effort, updated "
            f"in place. Drop the date — `--topic "
            f"{_TOPIC_DATE.sub('', args.topic).strip('-_') or '<effort>'}` — and "
            f"the existing doc for this effort will be found and updated.\n"
            f"  🔴 There is no flag to bypass this. MEASURED over the 123 real "
            f"handoff docs in devrc + homelab-talos: 55 (44%) carry a date, and "
            f"collapsing them by date exposes `remix-session` x8 and "
            f"`browser-bridge` x3 — the same effort, once per session.",
            file=sys.stderr,
        )
        return EXIT_DOC_PER_EFFORT

    if not doc.exists() and not args.new_effort:
        existing = existing_handoff_docs(repo)
        if existing:
            shown = existing[:EXISTING_SHOWN_MAX]
            elided = len(existing) - len(shown)
            print(
                "status=new-doc\n"
                "NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
                f"  {relpath} does not exist, and this repo already has "
                f"{len(existing)} handoff doc(s). Creating a second doc for an "
                f"effort that already has one is the thing the one-doc-per-effort "
                f"rule caps (operator decision 2026-08-28).\n"
                "  If one of these IS this effort, re-run with its topic — the "
                "update lands in place and nothing is lost:\n"
                + "\n".join(f"    {name}" for name in shown)
                + (f"\n    … and {elided} more." if elided else "")
                + "\n  If this really is a NEW effort, say so: re-run with "
                "`--new-effort`.\n"
                "  🔴 No similarity matching is done here on purpose — whether "
                "two slugs are the same effort is a judgement, and a heuristic "
                "guess would be wrong in both directions. This refusal exists to "
                "put the list in front of you, not to decide for you.",
                file=sys.stderr,
            )
            return EXIT_DOC_PER_EFFORT

    # ---- rule (d): the advance question, asked BEFORE anything is computed ---
    if not advance_is_real(args.advanced):
        print(
            "status=no-advance\n"
            "This session did not state what changed since the handoff was "
            "written, so no update is offered: no diff, no write, no commit.\n"
            "  If state DID advance, re-run with --advanced '<what changed>'.\n"
            "  If it did not, say so plainly and write nothing — a handoff that "
            "still describes reality is not stale.",
            file=sys.stderr,
        )
        return EXIT_NO_ADVANCE

    try:
        update_text = Path(args.update).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read --update: {exc}", file=sys.stderr)
        return EXIT_FAIL

    # ---- rule (j): every ranked next-step names a forcing function ----------
    # Read from the UPDATE, so legacy items already in the base are never
    # retroactively refused — see `ranked_items`. An update that brings no
    # `Next steps` section at all touches no ranks and is not asked the question.
    items = ranked_items(update_text)
    unforced = unforced_report(items)
    if unforced:
        print(unforced, file=sys.stderr)
        return EXIT_UNFORCED

    base_text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    if base_text:
        report = merge_report(base_text, update_text)
    else:
        # No base: the update simply becomes the doc. Nothing was replaced, so
        # rule (f) has nothing to classify and rule (g) has no bucket to state —
        # an empty report is the honest answer, not a missing one.
        report = MergeReport(update_text.rstrip("\n") + "\n", (), ())
    merged_text = report.text

    if _canon(merged_text) == _canon(base_text):
        print(
            "status=no-change\n"
            f"The merge of {args.update} into {relpath} changes nothing. "
            "No diff, no commit — an empty commit is not a handoff update.",
            file=sys.stderr,
        )
        return EXIT_NO_CHANGE

    diff = unified(base_text, merged_text, relpath)
    print(f"doc: {relpath}")
    print(f"advanced: {args.advanced.strip()}")
    # 🔴 BOTH BEFORE THE DIFF, and on the SAME stream. The diff is what the human
    # is being asked to approve, so the classification of what it deletes has to
    # arrive before it, not after several hundred lines of it. Neither line
    # carries a `status=` token: `status=` is the machine-readable verdict and
    # the skill's contract pins one per run.
    if base_text:
        print(buckets_line(report.buckets))
    # 🔴 RULE (h) FIRST, above rule (f) and above the diff. It is the only one of
    # the three that can invalidate the OTHER two: a wrong base makes the bucket
    # line describe a merge nobody wanted and makes "nothing durable dropped"
    # true of a document that is not the one being replaced. It runs even with no
    # base at all — a doc absent HERE and present on the mainline is the same bug
    # wearing its loudest disguise, every section arriving NEW.
    # Hoisted: the refusal below needs the SAME reading the warning was rendered
    # from. Computing it twice would let the two disagree across a concurrent
    # fetch, and the message would then name numbers the decision did not use.
    currency = base_currency(repo, relpath)
    wrong_base = wrong_base_report(
        wrong_base_tells(base_text, update_text, report.buckets),
        currency,
        relpath,
        repo,
        doc_shape(base_text),
        currency.replaces_mainline_doc(base_text),
    )
    if wrong_base:
        print(wrong_base)

    # 🔴 REFUSE, don't warn. The block above DETECTED this case and printed it
    # from the day it shipped — and exited 0. That was survivable while a human
    # answered a y/N here; that prompt was retired 2026-08-23, which left the
    # warning as the only thing between this diff and a pushed commit. The
    # governing rule (`subsystem-index` SKILL) is "blast radius earns a REFUSAL,
    # not a question", and replacing a committed document with a delta is blast
    # radius. `--push`'s `behind` check does NOT cover it: that one compares
    # against `<remote>/<push-branch>`, a DIFFERENT ref, so a feature branch
    # that is current with its own upstream passes it while the mainline copy
    # of the doc is still the one being destroyed.
    if currency.replaces_mainline_doc(base_text) and args.confirm and not (
        args.allow_replacing_mainline_doc
    ):
        assert currency.mainline is not None  # implied by the predicate
        print(
            f"status=stale-base ref={currency.base_ref} path={relpath}\n"
            f"NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
            f"  This checkout has no usable {relpath} — it is missing, empty or "
            f"whitespace, which the merge treats identically — and "
            f"{currency.base_ref} "
            f"has one ({currency.mainline.sections} section(s) / "
            f"{currency.mainline.lines} line(s)). Confirming would merge every "
            f"section as NEW and REPLACE that committed document with this "
            f"delta.\n"
            f"  This is usually a clone that never re-synced after work was "
            f"committed from a WORKTREE — the doc is real, it is just not "
            f"here.\n"
            f"  Read the mainline copy first:\n"
            f"    git -C {repo} show {currency.base_ref}:{relpath}\n"
            f"  Then re-run against a current clone — that is the fix, not the "
            f"override.\n"
            f"  🔴 The override (--allow-replacing-mainline-doc) CANNOT rescue "
            f"the common shape of this. Being stale-base against the branch you "
            f"are pushing to implies being BEHIND it, so on the mainline branch "
            f"`--push` then refuses with status=behind (exit 6) whatever you "
            f"pass here. It only lands on a branch whose own upstream is "
            f"current, or with --confirm and no --push. Re-syncing the clone is "
            f"the route that works in every case.",
            file=sys.stderr,
        )
        return EXIT_STALE_BASE

    warning = dropped_durable_report(report.dropped)
    if warning:
        print(warning)
    # Rule (j)'s advisory half, beside the other two and for the same reason:
    # above the diff, because it is a statement about what the diff is adding.
    self_generated = self_generated_report(items)
    if self_generated:
        print(self_generated)
    print(diff, end="" if diff.endswith("\n") else "\n")

    if not args.confirm:
        print("status=proposed")
        print(
            "NOTHING WRITTEN — not the doc, not a commit, not a ref. This run "
            "exists to put the diff above in the transcript.\n"
            "  land it -> re-run this exact command with --confirm --push\n"
            "  🔴 READ THE WARNINGS ABOVE THE DIFF FIRST. They were advisory "
            "while a human answered a y/N here; that prompt was retired "
            "2026-08-23 (operator decision — it was always answered y), so they "
            "are now the only thing between this diff and a pushed commit.\n"
            "  Declining is still normal: run nothing else and the tree stays "
            "byte-identical. Nothing about this run has to be undone."
        )
        return EXIT_OK

    # 🔴 Resolved for EVERY confirmed write, not only for `--push`, because the
    # not-pushed report names the branch. A failure is DEFERRED rather than
    # raised: `--confirm` without `--push` on a detached HEAD commits and exits 0
    # today and must keep doing so — this is information, not a new refusal — so
    # only the push path below re-raises it, which keeps that refusal byte-identical.
    push_branch = ""
    branch_error: GitError | None = None
    try:
        push_branch = resolve_branch(repo, args.branch)
    except GitError as exc:
        branch_error = exc

    if args.push:
        # 🔴 BEFORE the write, not after. See EXIT_BEHIND.
        try:
            if branch_error is not None:
                raise branch_error
            behind = remote_has_commits_we_lack(repo, args.remote, push_branch)
        except (GitError, ValueError) as exc:
            print(
                f"status=failed\ncannot determine whether {args.remote} has moved, "
                f"so refusing to commit something that may not be pushable: {exc}\n"
                f"  If you only want the doc updated LOCALLY, re-run without "
                f"`--push` — the remote being unreachable does not make the local "
                f"write wrong.",
                file=sys.stderr,
            )
            return EXIT_FAIL
        if behind:
            # 🔴 The RESOLVED branch in every line. An earlier version printed
            # `HEAD` and a literal `<branch>`, so the recovery could not be
            # pasted — and this message is the entire second half of the fix.
            dirty = uncommitted_paths(repo)
            ff = f"git -C {repo} merge --ff-only {args.remote}/{push_branch}"
            head = (
                f"status=behind remote={args.remote} branch={push_branch}\n"
                f"NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
                f"  {args.remote}/{push_branch} has commit(s) this checkout does "
                f"not, so the push would be rejected and the commit would be left "
                f"behind on a shared branch. In a devrc checkout that is the state "
                f"that silently blocks `ship.sh`; elsewhere it is a stranded commit "
                f"on a branch other people push to.\n"
            )
            if dirty:
                # 🔴 A DIRTY TREE CHANGES THE REMEDY, and this is the branch that
                # matters. `merge --ff-only` here either refuses or overwrites work
                # that is very often NOT the caller's: measured in a shared clone
                # 2026-08-19, 38 dirty paths across three sessions at 90 behind.
                # Repos with a shared primary clone forbid mutating it at all, so
                # the tool must not print that command as if it were the fix.
                shown = ", ".join(sorted(dirty)[:4])
                more = f" (+{len(dirty) - 4} more)" if len(dirty) > 4 else ""
                print(
                    f"{head}"
                    f"  🔴 THIS CHECKOUT IS DIRTY — {len(dirty)} uncommitted "
                    f"path(s): {shown}{more}\n"
                    f"  DO NOT fast-forward it. Some or all of that work is "
                    f"probably another session's, and `merge --ff-only` would "
                    f"either refuse or overwrite it. Several repos forbid "
                    f"committing in a shared primary clone for exactly this "
                    f"reason.\n"
                    f"  Commit and push from a THROWAWAY WORKTREE off the remote "
                    f"branch instead, leaving this tree untouched:\n"
                    f"    git -C {repo} worktree add /tmp/handoff-wt "
                    f"{args.remote}/{push_branch}\n"
                    f"    # write the doc there, commit it path-limited, then:\n"
                    f"    git -C /tmp/handoff-wt push {args.remote} "
                    f"HEAD:{push_branch}\n"
                    f"  🔴 Remove the worktree only AFTER the push succeeds — "
                    f"removing it after a failed push deletes the branch ref and "
                    f"orphans the commit.\n"
                    f"  Verify by CONTENT, never ancestry: a squash merge never "
                    f"makes your head an ancestor of {push_branch}.",
                    file=sys.stderr,
                )
                return EXIT_BEHIND
            print(
                f"{head}"
                f"  This checkout is CLEAN, so a fast-forward is safe. Run it, "
                f"then re-run this exact command:\n"
                f"    {ff}\n"
                f"  🔴 If `--branch {push_branch}` is not the branch you are ON, "
                f"do NOT run that merge — it would merge an unrelated branch into "
                f"your checkout. Push from a checkout of {push_branch} instead.\n"
                f"  If the merge refuses, this checkout has DIVERGED — preserve, "
                f"verify, then move the pointer, in that order:\n"
                f"    git -C {repo} branch <topic> HEAD && git -C {repo} push -u "
                f"{args.remote} <topic>\n"
                f"    git -C {repo} ls-remote --heads {args.remote} <topic>\n"
                f"    git -C {repo} reset --keep {args.remote}/{push_branch}",
                file=sys.stderr,
            )
            return EXIT_BEHIND

    # 🔴 A BLOCKED COMMIT IS NOT A NO-OP — capture enough to undo the write.
    # MEASURED 2026-08-21: a PreToolUse hook enforcing "never commit in the
    # primary clone" refused the commit below — correct behaviour — but the doc
    # had already been written AND `git add`ed, so `status=failed` left a
    # modified, STAGED file in a checkout shared with other sessions, where the
    # next person's `git commit` sweeps it in. The caller then re-ran the tool to
    # read the error and the merge appended the same block a SECOND time.
    # `status=failed` reads as "nothing happened"; it must therefore BE that.
    original: bytes | None = doc.read_bytes() if doc.exists() else None
    committed = False
    try:
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(merged_text, encoding="utf-8")
        git(repo, "add", "--", relpath)
        subject = f"docs(handoff): {args.advanced.strip().splitlines()[0]}"[:100]
        # Path-limited on purpose: exactly one commit, carrying exactly the
        # diff that was shown, even if the caller had other work staged.
        git(repo, "commit", "-m", subject, "--", relpath)
        committed = True
        sha = git(repo, "rev-parse", "HEAD").strip()
    except (GitError, OSError) as exc:
        # Only roll back what did NOT happen. If the commit landed and only
        # `rev-parse` failed, the tree is already correct and restoring the file
        # would DISCARD a committed change — the opposite of the fix.
        note = (
            COMMIT_LANDED_NOTE if committed
            else _undo_write(repo, doc, relpath, original)
        )
        print(f"status=failed\n{exc}{note}", file=sys.stderr)
        return EXIT_FAIL

    if args.push:
        # No `branch=` here: the line that follows on either push outcome already
        # names it (`status=pushed remote= branch=`, or the push-failed recovery
        # which spells it in every command). Adding it twice would also make this
        # change alter output on paths it has no business altering.
        print(f"status=written commit={sha}")
    else:
        # 🔴 The ONE outcome where the branch was never stated anywhere, and the
        # one that leaves a commit behind with no further line about its fate.
        print(f"status=written commit={sha} branch={push_branch or '<unresolved>'}")
        print(not_pushed_report(repo, args.remote, push_branch or None))
        return EXIT_OK

    try:
        git(repo, "push", args.remote, f"HEAD:refs/heads/{push_branch}")
    except GitError as exc:
        # The pre-check makes this rare, not impossible: the remote can move in
        # the window between them. The commit EXISTS at this point, so say so and
        # hand over the recovery — a caller who is not told is a caller who
        # leaves a shared branch diverged.
        print(
            f"status=push-failed\n{exc}\n"
            f"🔴 THE COMMIT {sha[:12]} EXISTS LOCALLY on `{push_branch}` "
            f"and is NOT on {args.remote}. On a shared branch that is the state "
            f"`ship.sh` skips over silently.\n"
            f"  Preserve, verify, then move the pointer — in that order:\n"
            f"    git -C {repo} branch <topic> HEAD && git -C {repo} push -u "
            f"{args.remote} <topic>\n"
            f"    git -C {repo} ls-remote --heads {args.remote} <topic>   # confirm it landed\n"
            f"    git -C {repo} reset --keep {args.remote}/{push_branch}   # --keep refuses rather than destroys",
            file=sys.stderr,
        )
        return EXIT_FAIL

    print(f"status=pushed remote={args.remote} branch={push_branch}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
