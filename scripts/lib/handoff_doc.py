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
   committed document is REPLACED wholesale. That case alone exits 9
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
  9  stale-base      — no usable doc here and the mainline has one
 10  unevidenced     — rule (k): an elimination names no way it was eliminated
 11  undefined-done  — rule (m): the arc names no closing condition
 12  rank-growth     — rule (n): the queue's `forcing: none` half grew
 13  leak-refused    — rule (o): the repo's own leak scanner would not vouch for
     the delta, or could not be run at all; the write is rolled back
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import os
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

# 🔴 THE STORE-READER MODULES COME FROM THE PINNED `cairn` FLAKE INPUT, not
# from `scripts/lib/` — devrc deleted its forked copies when it consolidated
# onto the pin. `cairn_pin.ensure()` APPENDS the packaged `lib/` after the
# line above and raises, naming both resolution routes and the remedy, when
# the pin is not deployed. There is no local fallback.
import cairn_pin  # noqa: E402

cairn_pin.ensure()

from subsystem_resolver import parse_journal_bullets  # noqa: E402

# 🔴 ONE RULE, ONE PLACE — rule (h)'s "what is this repo's mainline?" half.
# `subsystem_touch` resolves the same question for its commit window and takes
# the same answer from the same module. A second derivation here would disagree
# with that one the first time a clone's `origin/HEAD` is dangling — a state
# measured in devrc itself — and rule (h) would then measure currency against a
# branch the rest of the toolchain does not consider mainline.
import git_mainline  # noqa: E402
import handoff_budget  # noqa: E402
import session_trailer  # noqa: E402

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

EXIT_UNEVIDENCED = 10
"""Rule (k). An elimination bullet names no way it was eliminated. Nothing written.

MEASURED 2026-08-30, and this rule exists for one bullet. An opencode session
running a weaker model wrote `**Ruled out:** Not a per-DIMM issue (all 4
identical)` into a handoff's open-investigations block. It had run `inxi -CmG`
and `inxi -dm`; NEITHER prints a part number, so the sentence was an elimination
its own data could not support — and it was false. One flag away (`inxi -max`)
sat two different part numbers, which killed the theory the doc went on to rank
FIRST, with a ⚠ and "free performance sitting on the table". The next session's
instruction was to reboot a 26-day-uptime workstation into its BIOS.

🔴 THE GATE IS ON PROVENANCE, NOT ON PLAUSIBILITY. Nothing here can tell a true
elimination from a false one, and it does not try. It makes the AUTHOR say which
kind of evidence closed the question, from a closed vocabulary — the same shape
as rule (j), and for the same reason: a blocklist of weasel phrases is walkable
by rewording, an allowlist the author must pick from is not.

🔴 AND `assumed` IS A MEMBER, ACCEPTED AND COUNTED. The failure was never that
the session reasoned rather than measured; it was that the doc did not SAY so,
so a later reader could not tell the two apart. `via: assumed` is the honest
label, it passes, and it is reported above the diff.
"""

EXIT_UNDEFINED_DONE = 11
"""Rule (m). The arc declares no closing condition. Nothing written.

Two arms share this code the way rule (i)'s two do: a NEW doc that never set a
finish line, and an update that DELETES one the document had. One class ("after
this run the arc has nothing that can end it"), two remedies, both printed.
"""

EXIT_RANK_GROWTH = 12
"""Rule (n). The ranked queue's `forcing: none` half grew. Nothing written.

🔴 NOT A QUEUE-LENGTH LIMIT, and reading it as one gets the design backwards.
Items with an EXTERNAL forcing kind are not counted at all — an incident, the
operator or a red gate can add as many ranks as they like. What is ratcheted is
the SELF-GENERATED half, which the 75-day arc study found growing 2–6 items a
round and reaching 54–84 in the worst arcs while under a third of the items
carried any forcing function at all.
"""

EXIT_LEAK_REFUSED = 13
"""Rule (o). The repo's OWN leak scanner would not vouch for the delta.

🔴 WHY THIS IS CODE RATHER THAN A SENTENCE. This module commits AND pushes in
ONE call under `--confirm --push`, so there is no window in which a human can
scan between the two. Four `denied-identifier` leak events have landed on
handoff deltas and ONE of them reached `main` of a PUBLIC repository. The
standing remedy had been written as a SENTENCE in the handoff document three
times, in three wordings, and no code ran it. Prose an agent can read and then
not follow is what this module exists to replace — the same argument the eight
rules above are made of.

🔴 ONE CLASS, TWO CAUSES, and both print `status=leak-refused` because the
verdict is the GATE's, not the scanner's: the scanner REFUSED the delta, or it
could not be run and therefore never read it. Nothing is written on either arm —
the doc is rolled back to the bytes this process found.

🔴 ZERO IS THE ONLY PASS, AND THAT IS THE NON-OBVIOUS HALF OF IT. Any non-zero
exit refuses; there is no `== 1` comparison here and there must not be one.
`tests/leakscan.py` exits **2** for "could not vouch" — one of its OWN controls
misbehaved — and its docstring says in as many words that 2 is not a clean
result. A reader who has not been told that is exactly the reader who narrows
the comparison to the code a scanner "normally" uses.

🔴 ONE OPERATOR OPT-IN, `--leak-pre-existing-approved`, FOR THE ALREADY-RED
TREE. The gate refuses on any non-zero exit and does NOT try to work out whether
this delta caused it — see `leak_gate` for why that attribution was a guess. The
target tree can be red for something this call did not cause, and the honest
answer to that is an operator who read the output and said so, recorded on the
run. It does not cover the arm where the scanner could not be RUN: there is no
verdict to have read.
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
# homelab-talos leads with it (`2026-07-18-remix-session`).
#
# 🔴 THE BARE-YEAR ARM `(?<!\d)(?:19|20)\d{2}(?!\d)` WAS DELETED 2026-08-31 —
# operator decision on #964 item 1, and the deletion is the MEASUREMENT, not a
# taste call. Over the 147 real handoff docs in devrc + homelab-talos that arm
# had ZERO hits IN BOTH DIRECTIONS: all 55 dated slugs carry a full ISO date, so
# it never fired on a real doc, and 0 of the 92 undated slugs tripped it. What it
# DID have was a measured false-positive surface no test could see, because every
# case is a plain 4-digit run a human reads as a NUMBER:
#
#   rfc-1918-addressing -> rfc--addressing      rtx-2080-passthrough -> rtx--passthrough
#   rsa-2048-keys       -> rsa--keys            viewport-1920x1080   -> viewport-x1080
#   nfs-2049-mount      -> nfs--mount           iso-2022-jp-encoding -> iso--jp-encoding
#   y2038-timestamps    -> y-timestamps         cve-2024-3094-xz     -> cve--3094-xz
#
# The right column is what the refusal HANDS BACK, and it is unreadable — so a
# false positive cost a real handoff its natural name with no flag to say "this
# is a number". `cve-YYYY-NNNN` in particular is a predictable slug here, since
# `security` is a first-class forcing kind.
#
# 🔴 WHAT THE DELETION COSTS, stated so nobody re-derives it as a regression: a
# YEAR-ONLY per-session slug is no longer refused — `q3-2026-cleanup` and
# `remix-2026-07-session` both pass now. Zero such docs exist in the 147-doc
# corpus, and rule (i-b)'s new-doc refusal still catches the second doc for an
# effort that already has one. `test_ordinary_numbers_in_a_slug_are_not_dates`
# is the regression side; `test_a_year_only_slug_is_a_KNOWN_gap` is the cost side,
# pinned so the gap is a recorded decision rather than an accident.
#
# 🔴 THE SECOND ARM IS NEW (#964 item 2) and it is the OTHER half of that trade.
# `\d{4}-\d{2}-\d{2}` needs the hyphens, so the likeliest AGENT-GENERATED
# spelling — `date +%Y%m%d`, i.e. `remix-20260801` — sailed straight through
# while `rfc-1918` was refused. Measured over the same corpus: 0 of the 92
# undated slugs carry a bare 8-digit `19xx`/`20xx` run, so this arm costs
# nothing. `(?!\d)` keeps it off a 10-digit epoch, which stays a KNOWN gap.
_TOPIC_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|(?<!\d)(?:19|20)\d{6}(?!\d)")

#: How many rows any of rules (i)/(j)'s listings show before eliding — the
#: existing-docs list, the unforced items and the self-generated items alike.
#: Each is an aid to recognising what to fix, not an inventory: devrc alone has
#: 92 handoff docs (2026-08-31; 77 when this was written), and 92 lines of
#: filenames would bury the remedy under itself. Same reasoning as
#: `DROPPED_SHOWN_MAX`, one number rather than three so the three blocks cannot
#: drift into different shapes.
#:
#: 🔴 AN ELIDING LIST MUST SAY HOW TO SEE THE REST — #964 item 4's amplifier.
#: At 92 docs this shows 12 and elides 80, and an effort last touched three
#: weeks ago is not in the newest-12 window, so "re-run with its topic" pointed
#: at a list that structurally could not contain the answer. Rule (i-b)'s
#: elision line now carries the command that enumerates all of them.
EXISTING_SHOWN_MAX = 12


def topic_carries_a_date(topic: str) -> str | None:
    """The date-looking token in this topic slug, or None. Rule (i-a).

    One place, so the CLI refusal and the tests ask the same question — the
    `advance_is_real` pattern.
    """
    m = _TOPIC_DATE.search(topic)
    return m.group(0) if m else None


def mainline_handoff_docs(repo: Path, base_ref: str | None) -> list[str]:
    """Every `claudedocs/handoff-*.md` on the MAINLINE ref, sorted by name.

    🔴 THE LIST MUST NOT BE BLIND TO THE MAINLINE (#964 item 4). Rule (i-b)'s
    refusal exists to say "one of these IS your effort — re-run with its topic",
    and it built that list from a `glob` of the WORKING TREE. In a clone that is
    behind — the 313-commits-behind incident this module exists for — the doc
    you are being told to name may only exist upstream, so the list it offers
    STRUCTURALLY CANNOT contain the answer while reading as if it were complete.

    Distinct from `base_currency`'s question and it does not subsume it: that one
    asks about THIS topic's doc and refuses (exit 9) when it would be replaced.
    This one asks what OTHER efforts exist, which is what makes the list usable.

    READ-ONLY and it does NOT fetch, for the same reason `base_currency` does
    not: a human is waiting on the confirm gate. A `base_ref` of None, an
    unfetched ref or a repo with no `claudedocs/` on the mainline all yield `[]`
    — 🔴 which is NOT a claim that the mainline has no docs. The caller must not
    render an empty list as "nothing upstream".
    """
    if base_ref is None:
        return []
    shown = git_allow(repo, "ls-tree", "--name-only", f"{base_ref}:claudedocs")
    if shown.code != 0:
        return []
    return sorted(
        name for name in shown.out.split("\n")
        if name.startswith("handoff-") and name.endswith(".md")
    )


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

# A numbered item LINE. `^ {0,3}` is CommonMark's own bound on how far a
# TOP-LEVEL block may be indented and still be top-level — it is NOT, on its own,
# what excludes a nested item, and this comment used to claim that it was
# (#964 item 5). MEASURED at every indent: 0,1,2,3 counted; 4,5,6 excluded. But
# **3 spaces is exactly the CommonMark content indent for `1. `** — the canonical
# column a sub-list under a top-level rank starts at — so at indent 3 the two
# readings COLLIDE and no regex over a single line can tell them apart. At
# `edbc596f` the collision resolved the wrong way: `1. parent` followed by
# `   1. child` returned TWO items, both rank `1`. The ranks are half a claim's
# identity (`claim-work --slug-for <doc> <rank>`), so that is the exact rank
# miscount the comment said it prevented, and it re-points live claims.
#
# 🔴 THE EXCLUSION IS THEREFORE CONTEXTUAL AND LIVES IN `_item_blocks`, which is
# the only place that knows which item is open. This pattern stays wide on
# purpose: narrowing it to `^ {0,2}` would drop a legitimately 3-space-indented
# top-level rank out of rule (j) entirely — silently ACCEPTING an untagged item,
# the fail-open direction, which `_item_blocks` already records as the worse one.
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


def _item_blocks(
    section_body: str,
    pattern: re.Pattern[str] = _RANKED_ITEM,
) -> list[tuple[re.Match[str], list[str], list[str]]]:
    """`(match, the item's own visible lines, the fenced lines inside it)`.

    🔴 `pattern` IS PARAMETERISED SO RULE (k) REUSES THIS WALK RATHER THAN
    COPYING IT. Everything below about where a block ENDS was learned from
    measured accept-and-refuse bugs; a second walker for elimination bullets
    would have regenerated every one of them, which `claude/RULES.md` names
    exactly ("a predicate open-coded at N sites is typically wrong at N−1 of
    them in the same direction"). The pattern must capture at least one group;
    callers read the groups they defined.

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
    # 🔴 A NUMBERED LINE INDENTED PAST THE OPEN ITEM IS ITS CHILD, NOT A RANK —
    # #964 item 5, and the only place the answer exists. `_RANKED_ITEM` matches
    # indents 0-3 because CommonMark lets a top-level block carry up to 3 spaces;
    # (`_ELIMINATION` shares that bound, so rule (k) inherits this too);
    # 3 is ALSO the content indent of a sub-list under `1. `, so the LINE is
    # ambiguous and only the enclosing item settles it. The first rank line in a
    # section fixes the queue's own column; anything deeper belongs to it.
    #
    # 🔴 RELATIVE, NEVER A LITERAL 0. A section that indents its whole queue
    # (all items at 2, say) must still have those items counted — pinning the
    # column at 0 would exempt every one of them from rule (j), which is the
    # fail-OPEN direction. MEASURED over the 147-doc corpus: all 547 real rank
    # lines sit at indent 0 and 0 are the child shape, so this changes no
    # committed document — it closes a latent miscount, and the corpus figure is
    # the control that says so.
    starts: list[int] = []
    top_indent: int | None = None
    for i in range(len(all_lines)):
        if i not in visible or not pattern.match(all_lines[i]):
            continue
        indent = len(all_lines[i]) - len(all_lines[i].lstrip(" "))
        if top_indent is not None and indent > top_indent:
            continue
        top_indent = indent
        starts.append(i)
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
        m = pattern.match(all_lines[start])
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
        # 🔴 THE ONE OWNER, never a second `.startswith` — see `canonical_prefix`
        # for the disagreement that spelling caused (#964 item 3).
        if not is_next_steps_heading(heading_text(heading)):
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


def declared_forcing_none_report(items: typing.Sequence[RankedItem]) -> str:
    """Rule (j)'s advisory block for `forcing: none` items, or "".

    Silent when there are none, for `dropped_durable_report`'s stated reason: a
    reassuring "0 declared `forcing: none` items" on every run is a line that
    gets skimmed and then read as a guarantee.

    🔴 THE NAME IS THE FIX, AND IT IS THE WHOLE CHANGE. This was
    `self_generated_report` while its body tested `kind == "none"` — a function
    named after `is_self_generated`'s predicate (`kind not in
    EXTERNAL_FORCING_KINDS`) while implementing a DIFFERENT one. The two
    disagree on exactly one population and it is the populous one: an UNTAGGED
    legacy item has `kind is None`, so it IS self-generated and is NOT a
    declared `forcing: none`.

    🔴 THEY MUST STAY DIFFERENT — see `is_self_generated`, where counting only
    the literal `none` was MEASURED to make an honest re-tagging of a legacy
    queue read as pure GROWTH and be refused by rule (n). So this is NOT a
    duplicate to consolidate; it is two predicates that needed two names.
    The consolidation is already caught by
    `test_an_UNTAGGED_legacy_base_item_counts_as_SELF_GENERATED`, which
    asserts the behavioural half end-to-end — no new guard was added here,
    deliberately: a second test for a mutant an existing test already kills is
    the duplicate-guard pattern `claude/RULES.md` warns about.
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

# --- rule (k): an elimination names HOW it was eliminated ---------------------
#
# 🔴 THE SAME SHAPE AS RULE (j), DELIBERATELY: a NAMED FIELD whose value comes
# from a CLOSED VOCABULARY. `claude/RULES.md` warns that "a guard on WORDS is
# walkable by REWORDING", and a content sniff is precisely that guard. MEASURED
# against the bullet this rule exists for — `Not a per-DIMM issue (all 4
# identical)` — every cheap heuristic ACCEPTS it: it carries a digit, it is
# fluent, it is the same length as bullets that do cite evidence. Nothing in its
# TEXT separates it from a real elimination, because what it lacks is not a word
# but a MEASUREMENT. So the author declares the kind instead.
#
# What each member asserts, and each names something outside the author's head:
#   command      a command was run and its output read
#   measurement  a number was measured — the ≥2-point kind RULES.md asks for
#   code         the source was read, at a place the bullet names
#   change       a diff, a commit or a PR was read
#   doc          an authoritative external spec or vendor document
#   assumed      🔴 NOT EVIDENCE. A declaration that this was reasoned, not
#                observed — ACCEPTED and counted, exactly like `forcing: none`.
#
# 🔴 THERE IS DELIBERATELY NO `obvious`, `known`, `checked`, `verified` OR
# `tested`. Those are the labels an unmeasured elimination reaches for, and
# their absence is what forces such a bullet onto `assumed`, where it is counted
# and printed rather than passing as though something had been observed.
ELIMINATION_KINDS: frozenset[str] = frozenset(
    {"command", "measurement", "code", "change", "doc", "assumed"}
)

#: The kinds asserting an OBSERVATION — `ELIMINATION_KINDS` minus the honest
#: opt-out. Derived, never a second literal list, for the reason
#: `EXTERNAL_FORCING_KINDS` states: adding a kind above and forgetting it here
#: is the drift that would silently stop counting `assumed`.
OBSERVED_ELIMINATION_KINDS: frozenset[str] = ELIMINATION_KINDS - {"assumed"}

# 🔴 `via`, NOT `evidence`, AND THE CHOICE IS A FALSE-REFUSAL ARGUMENT RATHER
# THAN TASTE. `evidence:` is the more self-documenting key and it is the WRONG
# one: `**Ruled out:** … evidence: the ACCESS_DENIED is positive evidence it is
# NOT` is a real shape in this corpus, and against that line the kind group
# captures `the`, so a bullet whose author DID cite a measurement is refused
# `[unknown kind: the]`. `via` cannot collide the same way — the pattern
# requires a COLON immediately after the key, and prose reaches for `via the`,
# `via a`, `via its`, never `via:`. MEASURED over both corpora (devrc 90 docs,
# 121 elimination bullets): `via:` followed by any member of the vocabulary
# occurs 0 times outside a deliberate tag.
ELIMINATION_KEY = "via"

#: Same grammar as `_FORCING`, and it must STAY the same: an author who has
#: learned one field's spelling should not have to learn a second. Bounded by
#: the closed vocabulary in exactly the same way.
_VIA = re.compile(
    rf"(?<![A-Za-z0-9]){ELIMINATION_KEY}{_MARKUP}\s*:\s*{_MARKUP}\s*([A-Za-z-]+)",
    re.IGNORECASE,
)

#: The FAIL-LOUD half, mirroring `_FORCING_ATTEMPT` for its measured reason: a
#: bullet that DID carry a tag being told `[no via: field]` is a refusal no
#: re-run can clear. Only ever consulted for a bullet already being refused, so
#: the worst case is a refusal naming a line the author is looking at.
_VIA_ATTEMPT = re.compile(
    rf"(?<![A-Za-z0-9]){ELIMINATION_KEY}(?![A-Za-z0-9])"
    rf"[^\n]{{0,40}}?"
    rf"(?<![A-Za-z0-9])(?:{'|'.join(sorted(ELIMINATION_KINDS))})(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# An elimination bullet: a top-level list item whose text opens with a ruled-out
# marker.
#
# ⚠ THE INDENT BOUND IS `{0,3}`, MATCHING `_RANKED_ITEM`, AND ITS LIMIT IS
# STATED HERE BECAUSE THE COMMENT USED TO OVERSTATE IT. It said a "nested bullet
# inside an item's sub-list is part of that item, not a claim of its own", which
# is FALSE at the 2- and 3-space nesting CommonMark actually produces: those
# match, and are treated as claims. Only 4-space-or-deeper nesting is excluded.
# The bound is kept rather than tightened, because it is measured: over all 303
# tracked `.md` the matched bullets sit at 0 spaces ×128 and at 3 spaces ×1 — so
# `{0,1}` would silently drop a real elimination to buy a nesting case the
# corpus does not contain.
#
# 🔴 NOTHING IS REQUIRED BETWEEN THE MARKER AND THE CLAIM, AND THAT WIDTH IS A
# FIX, NOT LAZINESS. The first version demanded a separator right after the
# marker (`ruled[ -]out` + markup + `[:—–-]`), which is what `Ruled out:`
# looks like — and MEASURED over this repo's 90 docs it MISSED 9 real bullets,
# every one of them the same shape: a QUALIFIER between the marker and the
# colon. `**Ruled out as writers:**`, `**Ruled out (structurally):**`,
# `**Ruled out, and still true:**`, `**Ruled out (carried forward):**`,
# `**Ruled out** (do NOT re-run these):` … Nine is not noise, it is a house
# style — and a gate blind to it is WALKABLE BY TYPING `Ruled out (obviously):`,
# which is exactly the "guard on WORDS defeated by REWORDING" that
# `claude/RULES.md` warns about. So the marker alone starts the bullet and the
# rest of the line is the claim; there is no punctuation to omit.
#
# 🔴 AND THE SAME CLASS EXISTS TO THE **LEFT** OF THE MARKER — measured after
# the qualifier fix, which only widened to its right. `_MARKUP` admits
# `[*_`~]` and nothing else, so any other decoration between the bullet and
# `Ruled out` defeated the pattern. Four real corpus bullets were invisible,
# all one shape:
#     - 🔴 **Ruled out — my own first diagnosis, which was WRONG.**
# plus `- ⚠ **Ruled out:**` and `- [x] **Ruled out:**`. So a leading run of
# NON-LETTER decoration (and an optional task-list checkbox) is skipped.
#
# 🔴 THE RUN IS NON-LETTER ON PURPOSE, NOT A GENERAL PREFIX: `- **NOT ruled
# out:**` is a real corpus line and asserts the OPPOSITE. Letters are what
# separate the two, so admitting a general prefix would silently start
# refusing bullets that say a candidate is still live.
_ELIMINATION = re.compile(
    rf"^ {{0,3}}[-*+]\s+"
    rf"(?:\[[ xX]\]\s*)?"
    rf"(?:[^A-Za-z\s]+\s*)*"
    rf"{_MARKUP}\s*ruled[ -]out\b(.*)$",
    re.IGNORECASE,
)

#: Leading emphasis and separator punctuation left over on a claim once the
#: marker is stripped — cosmetic only, so refusal rows read as the sentence the
#: author wrote rather than `:** the thing`.
_CLAIM_LEADER = re.compile(r"^[\s*_`~:—–-]+")


class EliminationBullet(typing.NamedTuple):
    """One `Ruled out:` bullet, and the evidence kind it declared (if any)."""

    text: str
    kind: str | None
    """Lowercased declared kind, or None when the bullet carries no field. A
    kind OUTSIDE `ELIMINATION_KINDS` is reported as declared — the author needs
    to see what they typed in order to fix it."""
    near_miss: str | None = None
    """The line that LOOKS like a tag `_VIA` could not parse, or None."""
    fenced: bool = False
    """True when the only `via:` field sits inside a fence, where it does not
    count."""

    @property
    def is_declared(self) -> bool:
        return self.kind in ELIMINATION_KINDS


def elimination_bullets(text: str) -> list[EliminationBullet]:
    """Every top-level `Ruled out:` bullet in `text`, with its declared kind.

    🔴 READS THE UPDATE, NEVER THE MERGED DOC — the single most load-bearing
    line in this rule. `open investigations` is an APPEND heading (see
    `APPEND_PREFIXES`), so the merged doc accumulates every elimination any
    session ever wrote. Checking the merge would refuse on all 151 legacy
    bullets across this repo's 45 docs that carry one, turning rule (k) into a
    permanently-red gate on the first run — which `claude/RULES.md` names as
    worse than no gate, because it trains everyone to click through. Checking
    the UPDATE gates what THIS session is adding and nothing else.

    🔴 SCOPED TO THE WHOLE BODY, NOT TO `## Open investigations`. A ruled-out
    bullet makes the identical claim wherever it is written, and the corpus puts
    them under `Gotchas` and `Findings` too. Narrowing to one heading would let
    the same unevidenced elimination through by moving it one section down —
    the "widest reading" the rules ask for.

    Fence-aware via `_item_blocks`, for that function's stated reason: a handoff
    routinely pastes a sample bullet inside a code block, and a sample is not a
    claim.
    """
    _fm, body = split_front_matter(text)
    out: list[EliminationBullet] = []
    for m, own, hidden in _item_blocks(body, _ELIMINATION):
        claim = _CLAIM_LEADER.sub("", m.group(1)).strip()
        block = "\n".join(own)
        found = _VIA.search(block)
        if found:
            out.append(EliminationBullet(claim, found.group(1).lower()))
            continue
        # Nothing parsed. Diagnose WHY, so the refusal says something the author
        # has not already done — `_VIA_ATTEMPT` cannot span a newline, so a
        # per-line walk sees what a block-wide search would and yields the LINE.
        near = next(
            (ln.strip() for ln in own if _VIA_ATTEMPT.search(ln)),
            None,
        )
        # 🔴 INDENTED hidden lines ONLY, and this is a FIX. `_item_blocks`
        # appends a fenced line to `hidden` BEFORE its boundary check, so a
        # COLUMN-0 fence appearing anywhere later in the update is absorbed into
        # this bullet's hidden set — its own docstring calls that "a KNOWN,
        # UNTESTED gap". Rule (j) never felt it because it walks one section;
        # rule (k) walks the whole body deliberately, so it does.
        #
        # MEASURED: an unrelated col-0 fence containing `via: command` turned an
        # untagged bullet's honest `[no via: field]` into a false `[fenced]`,
        # and the legend then told the author to unfence a block with no
        # relationship to the bullet. The likeliest such paste is THIS TOOL'S
        # OWN REFUSAL — exactly what sits in the scratch file of a session
        # re-running after rc 10. A fence that really belongs to the bullet is
        # indented under it, so indentation is the discriminator.
        fenced = near is None and any(
            _VIA.search(ln) for ln in hidden if ln[:1] in (" ", "\t")
        )
        out.append(EliminationBullet(claim, None, near, fenced))
    return out


ELIMINATION_VOCAB_LINE = (
    "  The field is `via: <kind>`, anywhere on the bullet's own lines — "
    "`<kind>` one of: " + ", ".join(sorted(OBSERVED_ELIMINATION_KINDS)) + ".\n"
    "  `via: assumed` is the honest opt-out for a candidate you reasoned away "
    "rather than measured. It is ACCEPTED and counted, not refused — but a "
    "reader is then told the elimination rests on reasoning, not on evidence."
)

MARK_NO_VIA = "[no via: field]"

#: 🔴 RULE (k)'s MARKER LEDGER, and it exists because the ONE it adds escaped
#: `REFUSAL_MARKERS` — which is guarded from both sides and still could not see
#: it, because that guard asserts a LENGTH of 4 against rule (j)'s tuple. So the
#: new marker is enumerated here and the skill legend is asserted against THIS
#: tuple, not against a number somebody has to remember to bump.
ELIMINATION_MARKERS: tuple[str, ...] = (
    MARK_NO_VIA,
    MARK_UNKNOWN_KIND,
    MARK_UNPARSED,
    MARK_FENCED,
)

#: 🔴 THREE REMEDIES, ONE PER CAUSE — NOT one trailer for all of them. Rule (j)
#: learned this twice, and `unforced_report`'s own standard is that "a refusal
#: which instructs a caller to do a thing the caller has already done is
#: unrecoverable": every re-run prints the identical text and the author has no
#: way forward. Rule (k) shipped with a single unconditional trailer and
#: reproduced all three shapes, so each arm now gets the remedy its cause needs.
MISSING_VIA_REMEDY = (
    f"  Tag each bullet marked {MARK_NO_VIA} above. A continuation line counts — "
    "the field does not have to sit on the bullet's first line, but it MUST be "
    "INDENTED: a flush-left line ENDS the bullet once a blank has intervened, so "
    "a `via:` at column 0 below one is outside the bullet and reads as absent."
)

NEAR_MISS_VIA_REMEDY = (
    f"  🔴 The bullet(s) marked {MARK_UNPARSED}] DO carry something — the quoted "
    "line is there and the check could not parse it. Spell the field as the "
    "literal key, a colon, then the kind: `via: command`. Emphasis around it is "
    "fine (`**via: command**`, `**via:** command`, `_via: command_`); a word "
    "between the key and the colon is not, and neither is any other separator "
    "(`via = command`, `via — command`)."
)

FENCED_VIA_REMEDY = (
    f"  🔴 The bullet(s) marked {MARK_FENCED} carry a `via:` INSIDE a code "
    "fence, where it does not count — a pasted sample is not a declaration. If "
    "it is YOUR declaration, move it onto one of the bullet's own lines, "
    "INDENTED — at column 0 it reads as absent. If it is quoted output, an "
    "example, or THIS TOOL'S OWN REFUSAL pasted back in, the bullet is genuinely "
    "untagged and needs a field of its own — do NOT promote the quote."
)

UNKNOWN_VIA_REMEDY = (
    f"  The bullet(s) marked {MARK_UNKNOWN_KIND} …] name a kind outside the "
    "vocabulary. Pick one of the listed kinds, or `via: assumed` if nothing was "
    "actually observed — there is deliberately no `obvious`/`checked`/`verified`."
)

ASSUMED_NOTE = (
    "  These are ACCEPTED and the write proceeds — declaring one honestly is "
    "the point. A later session must re-derive them before relying on them: an "
    "elimination nobody measured is a hypothesis wearing a conclusion's clothes."
)


def unevidenced_report(bullets: typing.Sequence[EliminationBullet]) -> str:
    """Rule (k)'s refusal block, or "" when every elimination declared a kind."""
    bad = [b for b in bullets if not b.is_declared]
    if not bad:
        return ""
    shown = bad[:EXISTING_SHOWN_MAX]
    rows: list[str] = []
    causes: set[str] = set()
    for b in shown:
        if b.fenced:
            mark, cause = MARK_FENCED, "fenced"
        elif b.near_miss is not None:
            mark, cause = f"{MARK_UNPARSED}: {_clip(b.near_miss, 48)}]", "near"
        elif b.kind is not None:
            mark, cause = f"{MARK_UNKNOWN_KIND}: {_clip(b.kind, 24)}]", "unknown"
        else:
            mark, cause = MARK_NO_VIA, "absent"
        causes.add(cause)
        rows.append(f"  {mark} {_clip(b.text, 88)}")
    elided = len(bad) - len(shown)
    if elided:
        # `unforced_report`'s line, for its reason: without it an author fixes
        # the visible rows, re-runs, and is refused again with no warning that
        # more were waiting.
        rows.append(f"  … and {elided} more not shown.")
    # 🔴 PER CAUSE, and only the causes actually present — a refusal that prints
    # all four remedies makes the reader find their own, which is the same
    # failure as printing none.
    remedies = [
        r for c, r in (
            ("absent", MISSING_VIA_REMEDY),
            ("near", NEAR_MISS_VIA_REMEDY),
            ("unknown", UNKNOWN_VIA_REMEDY),
            ("fenced", FENCED_VIA_REMEDY),
        ) if c in causes
    ]
    return "\n".join(
        [
            f"status=unevidenced\n"
            f"🔴 {len(bad)} of {len(bullets)} elimination bullet(s) name no way "
            f"the candidate was eliminated. An elimination is the claim a later "
            f"session trusts MOST and re-checks LEAST — it is what stops the "
            f"next reader looking.\n"
            f"NOTHING WRITTEN — not the doc, not a commit, not a ref. Fix your "
            f"scratch file and re-run; re-running after a fix is safe.",
            *rows,
            ELIMINATION_VOCAB_LINE,
            *remedies,
            "  🔴 This gate cannot tell a TRUE elimination from a false one and "
            "does not try. It makes you say which KIND of evidence closed the "
            "question, so a later reader can tell a measurement from a guess.",
        ]
    )


def assumed_report(bullets: typing.Sequence[EliminationBullet]) -> str:
    """Rule (k)'s advisory for `via: assumed` bullets, or "".

    Silent when there are none, for `declared_forcing_none_report`'s stated reason: a
    reassuring "0 assumed eliminations" on every run gets skimmed and then read
    as a guarantee.
    """
    assumed = [b for b in bullets if b.kind == "assumed"]
    if not assumed:
        return ""
    return "\n".join(
        [
            f"🔴 {len(assumed)} of {len(bullets)} elimination(s) declare "
            f"`{ELIMINATION_KEY}: assumed` — REASONED, NOT MEASURED:",
            *[f"  {_clip(b.text, 96)}" for b in assumed[:EXISTING_SHOWN_MAX]],
            ASSUMED_NOTE,
        ]
    )


# --- rule (l): a mid-diagnosis block declares WHEN it was written ------------
#
# 🔴 THE FAILURE THIS CLOSES, measured 2026-09-12. An `## Open investigations`
# block is written in the PRESENT TENSE by a session mid-diagnosis, and rule (c)
# APPENDS it forever — nothing ever retracts one. The doc's status header is
# visibly dated; a diagnosis block is not, so it reads as current for the life
# of the document. A session read one, adopted its framing, and the framing was
# wrong: a claim that fused two documents' measurements over two windows with
# two instruments. Refuting it cost a full re-measurement.
# `claudedocs/handoff-handoff-resume-skill-trace.md` carries the worked example.
#
# 🔴 PROSE HAS ALREADY FAILED AT THIS. The `resume` skill body warns about the
# class and cites two prior instances (2026-08-19, 2026-08-20). A warning that
# must be remembered is a warning that gets skipped, so the STAMP IS WRITTEN BY
# THE TOOL, not asked for in a checklist — the same reason rule (i) resolves the
# topic slug here rather than telling the author to think about it.
#
# WHAT IS STAMPED, AND WHAT IS NOT:
#   * only `### ` blocks inside the UPDATE's `## Open investigations` section —
#     the text THIS session is writing. The base document is never touched, so
#     this is not a retro-stamp of the corpus and cannot rewrite what a past
#     session wrote. (Nor does it need to: `scripts/resume-state.sh` dates an
#     unstamped block from the commit that introduced it, which answered for
#     478 of 478 blocks across this repo's 81 docs. The stamp is the most
#     PRECISE clock, never the only one.)
#   * an EXPLICIT stamp always wins and is never rewritten. A session recording
#     evidence gathered last week must be able to say so, and "the tool moved my
#     date" is exactly how an author learns to distrust the field.
#   * fence-aware, for `_unfenced`'s stated reason: a delta routinely pastes the
#     skill's own template — a sample block is not a claim.
#
# The grammar is deliberately rule (j)/(k)'s: key, colon, value, `_MARKUP`
# emphasis allowed. An author who has learned `forcing:` and `via:` should not
# have to learn a third spelling. The VALUE is an ISO date rather than a closed
# vocabulary, because the question is "when", not "which kind".
INVESTIGATION_STAMP_KEY = "as-of"

#: `## Open investigations` — matched on the PREFIX, exactly as `APPEND_PREFIXES`
#: matches it, because the canonical spelling carries a trailing gloss ("— live
#: diagnosis state") no updating session reproduces character-for-character.
_INVESTIGATION_SECTION = re.compile(r"^##\s+open investigations", re.IGNORECASE)

#: Any other `## ` heading closes the section. `### ` does not — that is a block.
_ANY_H2 = re.compile(r"^##\s+")

_INVESTIGATION_BLOCK = re.compile(r"^###\s+(.*\S)\s*$")

#: 🔴 THE TRAILING `(?![-\d])` IS LOAD-BEARING: without it `as-of: 2026-09-12-rev2`
#: parses as a valid date and the block is treated as stamped. It is not a
#: hypothetical — a date followed by a hyphenated suffix is a real habit in this
#: corpus's filenames. An unparseable value must read as ABSENT so the tool
#: stamps it, rather than as a stamp nobody can date.
_INVESTIGATION_STAMP = re.compile(
    rf"(?<![A-Za-z0-9]){INVESTIGATION_STAMP_KEY}{_MARKUP}\s*:\s*{_MARKUP}\s*"
    rf"(\d{{4}}-\d{{2}}-\d{{2}})(?![-\d])",
    re.IGNORECASE,
)


def _unfenced_flags(lines: typing.Sequence[str]) -> list[bool]:
    """`True` for each line that sits OUTSIDE a code fence (fences themselves
    are `False`).

    `_unfenced` yields only the surviving lines, which is what rule (f) needs;
    stamping has to REWRITE the text, so it needs the mask instead. Same fence
    grammar, taken from the same `_fence_token`, so the two cannot disagree
    about where a fence starts.
    """
    flags: list[bool] = []
    open_tok: str | None = None
    for line in lines:
        tok = _fence_token(line)
        if open_tok is None:
            if tok:
                open_tok = tok
                flags.append(False)
                continue
            flags.append(True)
        else:
            flags.append(False)
            if (
                tok
                and tok[0] == open_tok[0]
                and len(tok) >= len(open_tok)
                and line.strip() == tok
            ):
                open_tok = None
    return flags


class InvestigationBlock(typing.NamedTuple):
    """One `### ` block under `## Open investigations`, and its stamp."""

    heading: str
    """The heading text, without the `### ` marker."""
    stamp: str | None
    """The ISO date it declares, or None when it carries none."""
    index: int
    """0-based index of the heading line within the document's lines."""


def investigation_blocks(text: str) -> list[InvestigationBlock]:
    """Every mid-diagnosis block in `text`, with the date it declares.

    🔴 SCOPED TO `## Open investigations`, WHICH IS THE OPPOSITE OF RULE (k)'s
    CHOICE, and the difference is not an inconsistency. A `Ruled out:` bullet
    makes the same claim wherever it is written, so rule (k) walks the whole
    body. A *block* is a structural object that only exists under that heading —
    a `### ` under `Gotchas` is not a live diagnosis and stamping it would put a
    date on settled text, teaching the reader that the field means nothing.
    """
    lines = text.splitlines()
    flags = _unfenced_flags(lines)
    out: list[InvestigationBlock] = []
    in_section = False
    for i, line in enumerate(lines):
        if not flags[i]:
            continue
        if _INVESTIGATION_SECTION.match(line):
            in_section = True
            continue
        if _ANY_H2.match(line):
            in_section = False
            continue
        if not in_section:
            continue
        m = _INVESTIGATION_BLOCK.match(line)
        if m:
            out.append(InvestigationBlock(m.group(1), None, i))
            continue
        if out and out[-1].stamp is None:
            found = _INVESTIGATION_STAMP.search(line)
            if found:
                out[-1] = out[-1]._replace(stamp=found.group(1))
    return out


def stamp_investigations(text: str, today: str) -> tuple[str, list[str]]:
    """`(text with every unstamped investigation block stamped, headings done)`.

    The field is inserted as the block's FIRST bullet, immediately under its
    heading: a fixed position is what makes it findable by eye and by
    `resume-state.sh`, and the alternative — appending at the end of a block —
    puts it after whatever fenced evidence the author pasted, where the fence
    grammar decides its meaning.

    Idempotent by construction: a block that already declares a date is skipped,
    so re-running against an already-stamped scratch file changes nothing and
    the `no-change` verdict stays reachable.
    """
    blocks = [b for b in investigation_blocks(text) if b.stamp is None]
    if not blocks:
        return text, []
    lines = text.splitlines(keepends=True)
    ends_clean = text.endswith("\n") or not text
    for b in reversed(blocks):
        lines.insert(b.index + 1, f"- {INVESTIGATION_STAMP_KEY}: {today}\n")
    out = "".join(lines)
    if not ends_clean:
        out = out.rstrip("\n")
    return out, [b.heading for b in blocks]


def _today() -> str:
    """Today, LOCAL, as `YYYY-MM-DD`.

    Local rather than UTC on purpose: every other date a handoff carries is the
    operator's calendar date, and a stamp reading tomorrow (or yesterday) beside
    a `# Handoff: <topic> — <date>` line written the same minute would be read
    as a bug in the field, not as a timezone.

    🔴 NO ENVIRONMENT OVERRIDE, deliberately. A settable clock is a way for a
    run to declare its own text fresh, and this field exists precisely because
    nobody re-checks a date. Tests pass `today` to `stamp_investigations`
    directly; nothing else needs to.
    """
    return datetime.date.today().isoformat()


def stamped_report(headings: typing.Sequence[str], today: str) -> str:
    """Rule (l)'s advisory, or "" when nothing needed a stamp.

    An ADVISORY, never a refusal: the tool did the work, so there is nothing for
    the author to fix and a refusal would be unclearable. It is still printed,
    because a line this tool ADDED to the author's text must be visible above
    the diff rather than discovered inside it.
    """
    if not headings:
        return ""
    return "\n".join(
        [
            f"🔴 {len(headings)} investigation block(s) carried no "
            f"`{INVESTIGATION_STAMP_KEY}:` date, so this run stamped them "
            f"`{INVESTIGATION_STAMP_KEY}: {today}`:",
            *[f"  {_clip(h, 96)}" for h in headings[:EXISTING_SHOWN_MAX]],
            "  A diagnosis block is written in the PRESENT TENSE and nothing "
            "ever retracts it, so its age is the only thing that tells a later "
            "reader whether to trust it. `scripts/resume-state.sh` reads this "
            "field and reports a block that has aged out.",
            "  🔴 The date is TODAY. If the evidence in a block is older than "
            "that, say so: write the field yourself with the real date and this "
            "run will leave it alone.",
        ]
    )


# --- rule (m): the arc declares what ENDS it ----------------------------------
#
# 🔴 THE MEASUREMENT THIS RULE EXISTS FOR, and it is about handoff ARCS rather
# than about any one document. `<homelab-talos>/claudedocs/audit-arc-rabbit-holes-2026-09-13.md`
# in homelab-talos read 75 days of telemetry — 745 doc-linked kickoff sessions
# across 299 arcs — and deep-read the five longest. In ALL FIVE the round-1
# objective was satisfied within 1–7 rounds; the arcs ran 13–23. Nothing in
# those documents was wrong. What was missing was a written statement of what
# would END them, so every later round had a ranked list and no finish line, and
# "is this done?" had no object to be answered against.
#
# 🔴 SO THE GATE IS ON THE FIELD'S EXISTENCE, NOT ON ITS CONTENT — the same
# posture as rules (j) and (k), for the same reason. Nothing here can tell a real
# closing condition from a plausible sentence, and it does not try. It makes the
# author name one, from a closed vocabulary, at the moment the arc is created.
#
# What each kind asserts, and the split is `claude/RULES.md`'s own — the
# "out of scope ⇒ file it, but only once you can name the CLOSING CONDITION"
# paragraph, which already distinguishes exactly these two and refuses a third:
#   check       a MECHANICAL check: a merged PR, a cleared alert, a command
#               exiting 0 — something a later session can RUN
#   judgement   a NAMED human reading NAMED evidence ("X reads the transcript")
# 🔴 THERE IS DELIBERATELY NO `soon`, `done`, `tbd` OR `ongoing`, and no third
# kind for "someone will decide" — that phrase is the thing RULES.md names as
# NOT a work item at all.
CLOSING_KINDS: frozenset[str] = frozenset({"check", "judgement"})

#: The field key. A NAMED FIELD, in the spirit of `FORCING_KEY` — not a keyword
#: hunted for in prose, which `claude/RULES.md` says is walkable by rewording.
CLOSING_KEY = "closing-condition"

# The grammar is rule (j)'s, deliberately: key, colon, a member of a CLOSED
# vocabulary, then free text. `_MARKUP` and the `(?<![A-Za-z0-9])` lookbehind are
# shared with `_FORCING` for the measured reason recorded there — `\b` has no
# boundary to match against `_`, so `_closing-condition: check_` fell through the
# equivalent pattern and was refused with a remedy the author had carried out.
#
# 🔴 THE DETAIL IS CAPTURED RATHER THAN PATTERNED. `closing-condition: check`
# with nothing after it names no check, and a regex that demanded a separator
# would refuse `check: PR #123 merges` (a colon) while accepting `check —` with
# an empty tail. The emptiness test lives in `closing_condition`, where it can
# strip markup and separators first and say WHICH of the two it got.
#
# 🔴 AFTER THE COLON THE CLASS IS `[\s*_`~]*`, NOT `_MARKUP\s*`, AND THE
# WIDENING IS A MEASURED FIX RATHER THAN GENEROSITY. `_MARKUP\s*` admits ONE run
# of markup followed by whitespace — it cannot see markup, then whitespace, then
# markup again. The step-2 template writes exactly that:
#
#     - **closing-condition:** `check` — a command a later session can RUN
#                              ^^ ^      two runs, separated by a space
#
# so the pattern REFUSED the one spelling the skill teaches, while
# `resume-state.sh`'s awk (whose class is flat) accepted it. Caught by
# `TestTheTwoParsersAgree` in `test_resume_state_dod.py` and by nothing else:
# both parsers were green in isolation, and the disagreement was the writer
# refusing a document the reader would happily have shown a finish line from.
# `claude/RULES.md`: "the defect lives in the SEAM nobody owns."
#
# The widening is bounded by what FOLLOWS it — a member of a closed
# two-word vocabulary — so a longer markup run buys an author nothing.
_CLOSING = re.compile(
    rf"(?<![A-Za-z0-9]){CLOSING_KEY}{_MARKUP}\s*:[\s*_`~]*"
    rf"([A-Za-z]+)(?![A-Za-z0-9])(.*)$",
    re.IGNORECASE,
)

#: The FAIL-LOUD half, same idiom and same purpose as `_FORCING_ATTEMPT`: keep
#: the grammar strict and REPORT what it turns away, rather than loosening it
#: until a refusal fires on prose. Only ever consulted for a document already
#: being refused, so the worst case is a refusal naming a line the author is
#: looking at.
#:
#: It matches the KEY in the spellings a session actually reaches for — a space
#: or an underscore where the hyphen belongs — and nothing else. It does NOT
#: match a bare `check`/`judgement`: those are ordinary English, they occur in
#: every handoff, and naming one as a near-miss would send the author to a line
#: that was never trying to be this field.
_CLOSING_ATTEMPT = re.compile(
    r"(?<![A-Za-z0-9])closing[-_ ]?condition(?![A-Za-z0-9])", re.IGNORECASE
)

#: Leading markup and ONE separator between the kind and its free text. Stripped
#: before the emptiness test so `check — ` and `check` are one case, not two.
#:
#: 🔴 THE SEPARATOR MUST BE FOLLOWED BY SPACE OR END, AND THE TRAILING RUN IS
#: WHITESPACE-ONLY. Both bounds are there because the first draft mangled real
#: details: a greedy `[\s*_`~:\-–—]+` ate the opening backtick of
#: ``check — `gate.sh` exits 0`` (leaving ``gate.sh` exits 0``), and an
#: unanchored separator ate one dash of `check — --dry-run exits 0`. The detail
#: is not only an emptiness test — `resume-state.sh` PRINTS it every round — so
#: a character eaten here is a character wrong on screen forever.
_CLOSING_DETAIL_LEAD = re.compile(r"^[\s*_`~]*(?:[:\-–—](?=\s|$))?\s*")

GOAL_PREFIX = "goal"


class ClosingCondition(typing.NamedTuple):
    """Rule (m)'s field as the document declares it — parsed, not judged."""

    kind: str | None
    """Lowercased declared kind, or None when no field parsed at all. A kind
    OUTSIDE `CLOSING_KINDS` is reported AS DECLARED, because the author needs to
    see what they typed in order to fix it."""
    detail: str = ""
    """The free text after the kind, markup and separators stripped."""
    near_miss: str | None = None
    """A line that spells the key in a shape `_CLOSING` cannot parse, or None.
    Set only when `kind is None` — a field that parsed needs no diagnosis."""
    fenced: bool = False
    """True when the ONLY field found sits inside a code fence, where it does not
    count. Same reason as `RankedItem.fenced`: the author can see the field in
    their file, so `[no closing-condition: field]` reads as a lie. 🔴 THE STEP-2
    TEMPLATE IS ITSELF A FENCED BLOCK CARRYING THIS FIELD, so a session that
    pastes the template wholesale into its scratch file lands here — which is
    precisely the case that must not be told the field is absent."""
    heading: str | None = None
    """The heading whose section carried the field. `None` when nothing parsed.
    Set even for a field found OUTSIDE `## Goal`, which is how the refusal can
    say "move it" instead of "add one"."""
    in_goal: bool = False
    """Was the field found under a `## Goal` heading?

    🔴 PART OF `is_declared`, NOT DECORATION, and leaving it out was a real
    defect in this rule's first draft: a well-formed field under `## State now`
    satisfied the gate while `/resume` — which reads the Goal section — could
    not see it. That is `claude/RULES.md`'s spelled-guard shape exactly: the
    guard passes while the hazard exists in a different place."""

    @property
    def is_declared(self) -> bool:
        return self.kind in CLOSING_KINDS and bool(self.detail) and self.in_goal


def _closing_in(body: str) -> tuple[ClosingCondition | None, str | None, bool]:
    """`(parsed field, near-miss line, saw a fenced field)` for one section body.

    Split out from `closing_condition` so the whole-document walk can ask the
    same question of a `## Goal` section and of every other section without
    spelling the search twice — the wrong-section arm of the refusal depends on
    the two searches being identical, or it would report a "move it" for a field
    that the Goal search would have rejected anyway.
    """
    lines = body.splitlines()
    flags = _unfenced_flags(lines)
    near: str | None = None
    fenced = False
    for line, unfenced in zip(lines, flags):
        found = _CLOSING.search(line)
        if found and not unfenced:
            fenced = True
            continue
        if found:
            kind = found.group(1).lower()
            detail = _CLOSING_DETAIL_LEAD.sub("", found.group(2)).strip()
            return ClosingCondition(kind, detail), None, fenced
        if unfenced and near is None and _CLOSING_ATTEMPT.search(line):
            near = line.strip()
    return None, near, fenced


def closing_condition(text: str) -> ClosingCondition:
    """Rule (m)'s field, read from `text`'s `## Goal` section(s).

    🔴 SCOPED TO `## Goal`, AND THE SCOPE IS THE POINT rather than tidiness. The
    field states what ends the ARC, so it belongs beside the arc's objective —
    where `/resume` reads it on every round, and where a session choosing the
    round's work cannot miss it. `Goal` is a REPLACE-bucket heading, so an update
    that rewrites it rewrites the field, and one that omits it leaves the base's
    alone; both are the behaviour this rule wants.

    🔴 READ FROM THE MERGED DOCUMENT, WHICH IS THE OPPOSITE OF RULES (j)/(k).
    Those read the UPDATE because their sections are the update's own. This one
    must not: the overwhelmingly common delta omits `## Goal` entirely, so
    reading the update would find nothing on almost every run and the rule would
    be fail-open exactly where it is cheapest to satisfy. The caller supplies the
    merge; see `undefined_done_report` for the grandfathering that keeps a merged
    read from going permanently red on a corpus written before this rule.

    A field outside `## Goal` is still FOUND and still reported — with its
    heading — because "you wrote it under the wrong heading" and "you wrote no
    field" are different problems with different fixes, and only one of them is
    solved by writing the field again.
    """
    _pre, secs = split_sections(split_front_matter(text)[1])
    fenced_anywhere = False
    near_anywhere: str | None = None
    elsewhere: ClosingCondition | None = None
    for heading, body in secs:
        parsed, near, fenced = _closing_in(body)
        fenced_anywhere = fenced_anywhere or fenced
        if near and near_anywhere is None:
            near_anywhere = near
        if parsed is None:
            continue
        if canonical_prefix(heading_text(heading)) == GOAL_PREFIX:
            return parsed._replace(heading=heading_text(heading), in_goal=True)
        if elsewhere is None:
            elsewhere = parsed._replace(heading=heading_text(heading), in_goal=False)
    if elsewhere is not None:
        return elsewhere
    return ClosingCondition(None, "", near_anywhere, fenced_anywhere, None, False)


CLOSING_VOCAB_LINE = (
    f"  The field is `{CLOSING_KEY}: <kind> — <the thing itself>`, in the "
    f"document's `## Goal` section — `<kind>` one of: "
    + ", ".join(sorted(CLOSING_KINDS))
    + ".\n"
    "  `check` = something a later session can RUN (a merged PR, a cleared "
    "alert, a command exiting 0). `judgement` = a NAMED person reading NAMED "
    "evidence. There is no kind for `someone will decide`."
)

#: Why the rule exists, in the words of the thing that measured it. Printed on
#: BOTH the refusal and the advisory, because a gate whose reason is only in a
#: commit message is a gate people route around.
CLOSING_WHY = (
    "  🔴 An arc with no written finish line does not finish. MEASURED over 75 "
    "days / 299 arcs: the round-1 objective was met by round 1–7 in all five "
    "deep-read arcs, which then ran 13–23 rounds. The field is FROZEN at round "
    "1 — later audits and asks do NOT extend it, they open a NEW arc."
)


def undefined_done_report(
    found: ClosingCondition, base_had_one: bool, is_new_doc: bool
) -> str:
    """Rule (m)'s refusal, or "" when this document may proceed.

    🔴 THREE ARMS, AND THE GRANDFATHERING IS THE MIDDLE ONE. Refusing every
    document that lacks the field would go red on the first update to every
    handoff written before this rule — `claude/RULES.md` calls a
    permanently-red gate worse than no gate, and this module has already been
    bitten by exactly that shape (see `ranked_items` on why rule (j) reads the
    update).

      NEW DOC, no field        -> REFUSE. Round 1 is the only round at which the
                                  finish line can honestly be set, and a new doc
                                  has no history to grandfather.
      BASE HAD ONE, merge does not -> REFUSE. That is a DELETION of the arc's
                                  finish line, and it is the one way a document
                                  that once complied stops complying.
      NEITHER had one          -> "" here; `legacy_dod_report` advises instead.

    The `is_declared` half is checked before the arms, so a document carrying a
    field with an unknown kind or an empty detail is refused whichever arm it is
    in — it declared nothing, and it is the author's own line, so the refusal is
    clearable.
    """
    if found.is_declared:
        return ""
    if not (is_new_doc or base_had_one):
        return ""
    if base_had_one:
        cause = (
            f"  This document HAD a `{CLOSING_KEY}:` and the merged result does "
            f"not. Deleting the arc's finish line is the one way a compliant "
            f"document stops complying — restore it in your `## Goal` delta, or "
            f"omit `## Goal` from the delta and the base's own field survives "
            f"untouched."
        )
    else:
        cause = (
            f"  This is a NEW handoff doc, which makes this round 1 — the only "
            f"round at which the finish line can honestly be set."
        )
    if found.fenced:
        diag = (
            f"  [fenced] the only `{CLOSING_KEY}:` found sits inside a code "
            f"fence, where it does not count. If that is the step-2 TEMPLATE "
            f"you pasted, fill in your own field OUTSIDE the fence; if it is "
            f"yours, unfence it."
        )
    elif found.heading is not None and found.kind not in CLOSING_KINDS:
        diag = (
            f"  [unknown kind] `{found.kind}` under `## {_clip(found.heading, 60)}` "
            f"is not one of the two — pick from the vocabulary above."
        )
    elif found.heading is not None and not found.detail:
        diag = (
            f"  [empty] `{CLOSING_KEY}: {found.kind}` under "
            f"`## {_clip(found.heading, 60)}` names a KIND and no condition. Say "
            f"which check, or which person reads which evidence."
        )
    elif found.heading is not None and not found.in_goal:
        diag = (
            f"  [wrong section] the field is under `## "
            f"{_clip(found.heading, 60)}`, not `## Goal`. MOVE it — do not write "
            f"a second one. `/resume` reads the Goal section, so a field "
            f"anywhere else is invisible to every later round."
        )
    elif found.near_miss:
        diag = (
            f"  [unparsed] this line looks like the field and is not it: "
            f"{_clip(found.near_miss, 96)}\n"
            f"  Re-spell the key exactly `{CLOSING_KEY}:` — hyphen, no space."
        )
    else:
        diag = f"  [no {CLOSING_KEY}: field] add one."
    return (
        f"status=undefined-done\n"
        f"NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
        f"{cause}\n"
        f"{diag}\n"
        f"{CLOSING_VOCAB_LINE}\n"
        f"{CLOSING_WHY}"
    )


def legacy_dod_report(
    found: ClosingCondition, is_new_doc: bool, history_unknown: bool = False
) -> str:
    """Rule (m)'s advisory for a pre-rule document, or "".

    Silent when the field is there, and silent on a NEW doc — that case is a
    refusal, and printing an advisory beside it would read as a second, softer
    verdict on the same fact. Silent-when-clean for `declared_forcing_none_report`'s
    stated reason: a reassuring line on every run gets skimmed and then read as
    a guarantee.
    """
    if found.is_declared or is_new_doc:
        return ""
    # 🔴 "written before rule (m)" IS A CLAIM ABOUT HISTORY, so it may only be
    # made when the history was READ. Round 2 caught it asserted in a repo with
    # an unborn HEAD, where there is no history at all.
    lead = (
        # One sentence for both unknown-history shapes — the doc is in neither
        # HEAD nor the mainline, or HEAD could not be read. Saying "git could
        # not say" covered only the second and was false on the first, where
        # git answered clearly.
        f"⚠ This handoff declares no `{CLOSING_KEY}:`, and this checkout cannot "
        f"show that it PREDATES rule (m) — the doc is in neither HEAD nor the "
        f"mainline here, or HEAD could not be read. Grandfathered either way — "
        f"this run proceeds."
        if history_unknown else
        f"⚠ This handoff declares no `{CLOSING_KEY}:` — it was written "
        f"before rule (m) and is GRANDFATHERED, so this run proceeds."
    )
    return "\n".join(
        [
            lead,
            "  Adding one is a `## Goal` delta — a line if the doc has that "
            "section, the section plus a line if it does not (measured: 42 of "
            "183 handoff docs have no heading that resolves to `goal`). Until "
            "it has one, no round of this arc can answer "
            "“is it done?” against anything.",
            CLOSING_VOCAB_LINE,
            CLOSING_WHY,
        ]
    )


# --- rule (n): the rank queue does not GROW its unforced half -----------------
#
# 🔴 THE MECHANISM THIS RULE INTERRUPTS, measured in the same 75-day study: a
# SELF-EXTENDING RANK QUEUE. Each round's audits and close-checks minted 2–6 new
# ranked items — faster than rounds closed them — so the queue could not drain
# however much the arc shipped. The worst four arcs went 0→76, 9→84, 1→55 and
# 11→54 ranks. One of them recorded its own state as "54 items… Of the 29 live
# items, only nine carry a forcing function".
#
# 🔴 SO THE RATCHET IS ON THE `forcing: none` HALF ONLY, AND THAT IS THE WHOLE
# DESIGN. An item with an EXTERNAL forcing kind is answerable to something
# outside the loop — an incident, the operator, a red gate — and work like that
# must never be blocked by a queue-length rule. What compounds is the other half:
# rule (j) already makes a self-generated item DECLARE itself (`forcing: none`,
# "accepted and counted, and not eligible to be worked"), and this rule is what
# makes the count it was being counted for actually bind.
#
# It is an ANTI-REGROWTH RATCHET, the same idiom as the repo's byte gates: the
# number may fall freely and may not rise. Closing self-generated items is what
# buys room for new ones, which is the behaviour the finding asks for.
#
# 🔴 WHAT IT DELIBERATELY DOES NOT DO: it does not match items across rounds.
# Rank TEXT is rewritten between rounds and rank NUMBERS are re-pointed by
# re-ranking, so any identity test would be a guess, and a guess here mis-reports
# WHICH item is new — worse than reporting only that the count moved. The count
# is the claim; the report prints the update's own `forcing: none` items so the
# author can see the population, and says in its own words that it did not
# identify which of them is the addition.
UNFORCED_GROWTH_FLAG = "--rank-growth-approved"


def is_self_generated(item: RankedItem) -> bool:
    """Does this ranked item answer to nothing outside the loop?

    🔴 THE PREDICATE IS "NOT EXTERNAL", NOT "== none", AND THE DIFFERENCE IS
    WHAT KEEPS THIS RULE OFF THE PERMANENTLY-RED LIST. A legacy ranked item
    carries NO `forcing:` field at all — 384 of them across the corpus rule (j)
    was measured against, and rule (j) deliberately never refuses one, because
    it reads the update and legacy items live in the base. Counting only the
    literal `none` would therefore read every legacy base as ZERO
    self-generated items, so the first honest re-tagging of a legacy queue —
    the exact thing rule (j) is trying to produce — would look like pure GROWTH
    and be refused. MEASURED: it took `test_forcing_none_is_ACCEPTED_and_
    reported` red, on a base whose two items are untagged on purpose.

    An untagged item asserts nothing external, so it belongs in the same
    population as one that says so. ONE predicate over BOTH sides, for `claude/
    RULES.md`'s one-rule-one-place reason; the asymmetry a reader will notice —
    that an update can never contain an untagged item — is rule (j)'s doing, not
    a second rule here.
    """
    return item.kind not in EXTERNAL_FORCING_KINDS


def self_generated_rank_count(text: str) -> int:
    """How many of `text`'s ranked next-steps answer to nothing external."""
    return sum(1 for i in ranked_items(text) if is_self_generated(i))


def rank_ratchet_skipped_report(
    update_items: typing.Sequence[RankedItem], reason: str
) -> str:
    """Say that rule (n) did NOT run, or "" when there is nothing to disclose.

    🔴 A SKIP THAT NOBODY SEES IS A PASS, AND THIS RULE HAS THREE OF THEM. Rule
    (n) declines to judge when it cannot COUNT the base — git could not say
    whether the doc exists in HEAD, or this checkout holds no usable copy, or
    the ranked queue sits under a heading `ranked_items` does not recognise.
    All three are the right call: `claude/RULES.md` refuses a zero that was
    never measured. (It said TWO until round 3; the third arrived with the
    unborn-HEAD fix and three narrations kept the old count.) But silence makes "I could not check" and "it passed" the same
    observable, which is the shape that rule's own evidence is about.

    Round 1 of this PR's audit measured the consequence: with the base
    unreadable the queue went 3 -> 5 at exit 0 and nothing said so.

    Printed ONLY when the update actually carries self-generated ranks — there
    is nothing to disclose about a round that added none, and a line on every
    run is one nobody reads by the third.
    """
    none_items = [i for i in update_items if is_self_generated(i)]
    if not none_items:
        return ""
    return "\n".join(
        [
            f"\u26a0 RULE (n) DID NOT RUN, so this update's "
            f"{len(none_items)} self-generated rank(s) were NOT ratcheted "
            f"against the document: {reason}.",
            "  This is NOT a pass — the comparison was not made. If you are "
            "adding to the queue, the count you would have been held to is the "
            "one in the document you cannot currently read.",
        ]
    )


def rank_growth_report(
    base_count: int, update_items: typing.Sequence[RankedItem], is_new_doc: bool
) -> str:
    """Rule (n)'s refusal, or "" when the queue did not grow its unforced half.

    🔴 SILENT ON A NEW DOC, and this is the grandfathering rather than an
    oversight. Round 1 legitimately opens with self-generated work — the finding
    is about what happens AFTER round 1 ("ranks may only be added by operator
    opt-in"), and a new document has no round 1 to have grown since.
    `declared_forcing_none_report` still counts them, on every run, new doc included.
    """
    if is_new_doc:
        return ""
    none_items = [i for i in update_items if is_self_generated(i)]
    if len(none_items) <= base_count:
        return ""
    return "\n".join(
        [
            "status=rank-growth",
            "NOTHING WRITTEN — not the doc, not a commit, not a ref.",
            f"  The ranked queue's SELF-GENERATED half grows: {base_count} "
            f"item(s) in the document answer to nothing external, "
            f"{len(none_items)} in this update.",
            *[
                f"  {i.rank}. {_clip(i.text, 96)}"
                for i in none_items[:EXISTING_SHOWN_MAX]
            ],
            "  ⚠ WHICH of these is the addition is NOT identified — rank text is "
            "rewritten and rank numbers are re-pointed between rounds, so any "
            "match across rounds would be a guess. The COUNT is the finding.",
            "  Three ways forward, and the first is usually the right one:",
            "    1. an audit finding is a DEFECT, not a rank — put it under a "
            "`## Defects (batched)` heading and fix the batch in one round. A "
            "defect list drains; a rank queue that grows by 2–6 a round does "
            "not.",
            "    2. if something outside this loop really is asking for it, say "
            "so: `forcing: incident|user|gate|deadline|regression|security` is "
            "not counted here at all.",
            "    3. close one. The ratchet falls freely — closing a "
            "`forcing: none` item buys room for a new one.",
            f"  Operator opt-in overrides: {UNFORCED_GROWTH_FLAG}.",
            "  🔴 MEASURED over 75 days / 299 arcs: rounds minted 2–6 new ranks "
            "each, and the worst arcs reached 54–84 items with under a third "
            "carrying any forcing function. The queue is why the arcs never "
            "closed, not the work.",
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



# --- rule (j): would this update put the document OVER its size budget? -------
#
# 🔴 WHY THIS EXISTS. `scripts/tests/test_handoff_doc_size.py` caps every
# `claudedocs/**/handoff-*.md`, and it has no feedback loop: `/handoff` appends by
# design, nothing in that loop says you are approaching a ceiling, and the author
# finds out when an UNRELATED PR goes red. MEASURED 2026-09-13, the evening the
# gate landed: THREE different documents went over in one session and every one
# was discovered that way.
#
# 🔴 IT WARNS AND NEVER REFUSES, and that is forced rather than preferred. A
# blocking check DEADLOCKS against `~/.claude/hooks/handoff-write-guard.py`,
# which blocks Stop until a handoff is written: a session working on one of the
# documents already grandfathered OVER the base ceiling could then neither record
# its work nor end its turn. The write guard's own measurement — 22 of 253
# sessions never recorded, ZERO of them because a gate correctly declined — says
# the unrecorded session is the more expensive failure.
#
# ⚠ IT IS NOT THE AUTHORITY. The gate reads the file on disk; this reads the text
# about to be written. They agree today and the test is what fails.
BUDGET_NEAR_BYTES = 4_096


# The ceiling gate's population is the tree that CONTAINS it: its own
# `REPO_ROOT` is `Path(__file__).resolve().parent.parent.parent`, so it walks
# whatever checkout it ships in — the base clone, a worktree, any future clone.
# Hence the predicate below is DERIVED from that fact rather than hardcoding a
# path: a repo is gated iff it ships the gate.
#: Re-exported, NOT re-declared: `handoff_budget` owns it beside the ceiling it
#: gates, because `handoff-audit.py` must ask the same question of a DIFFERENT
#: root and two copies are how the two tools come to disagree. Name kept so every
#: existing reference (and its tests) keeps working.
BUDGET_GATE_RELPATH = handoff_budget.GATE_RELPATH


def gate_enforces_budget(repo: Path) -> bool:
    """Does `test_no_handoff_doc_exceeds_its_budget` actually READ this repo?

    🔴 IT DOES NOT READ EVERY REPO, AND SAYING OTHERWISE COST A REAL SESSION
    REAL WORK. The gate enumerates `claudedocs/` under its OWN root, so a
    handoff doc in any repo that does not ship it is enforced by NOTHING. A
    warning that nevertheless announced "will go RED on `main`, and it fails for
    EVERYONE" drove, in `civitai/cli` on 2026-09-14/15: five evictions in two
    days, 35,517 B moved to `refs/`, and a heading-delimited slice that removed
    27,991 B — the whole ranked list — one step before a commit. None of it was
    required by any gate. See `civitai/cli#618`.

    Path-equality against a known root would be wrong: in a worktree the gate's
    own `REPO_ROOT` is the WORKTREE, not the base clone.
    """
    try:
        return (repo / BUDGET_GATE_RELPATH).is_file()
    except OSError:
        # An unreadable repo path is not evidence of a gate. Fail toward the
        # weaker claim: we never invent a gate we could not see.
        return False


#: Where each `audit_text` bucket keeps its (start, end) LINE RANGE. The four
#: buckets have different tuple shapes and there is no positional rule that
#: covers all of them — `retracted` is `(s, e, b)`, `resolved`/`dated` are
#: `(title, s, e, b)`, `done` is `(rank, s, e, b, is_done)`, so "the last three"
#: works for every bucket except `done`, whose last element is a bool. Enumerated
#: rather than derived, and pinned two-way by a test that re-derives each bucket's
#: own byte count from the range these indices select.
AUDIT_SPAN_INDEX = {
    "resolved": (1, 2),
    "done": (1, 2),
}

#: Sibling auditor. Loaded LAZILY and DEFENSIVELY — see `evictable_note`.
_AUDITOR = Path(__file__).resolve().parent.parent / "handoff-audit.py"


def evictable_note(merged_text: str, over_by: int) -> str:
    """What has CLOSED in THIS doc, in bytes, or "".

    🔴 IT REPORTS NUMBERS AND GIVES NO ADVICE — that is the design, not an
    omission. `test_handoff_doc_size.py` owns the eviction ladder: the steps,
    the MOVE-not-delete rule, and the prohibition on deleting a gotcha or a
    ruled-out theory. An earlier version copied that advice into this output and
    then spent four audit rounds keeping the copy consistent with the original —
    including a tension the playbook itself carries (retracted reasoning is
    demotable AND gotchas stay). MEASURED across those rounds: 7 of 10 findings
    were caused by the advice surface, 3 by the numbers. One rule, one place;
    this function supplies only what nothing else did — how much of THIS
    document has already closed.

    🔴 WHY THIS EXISTS. The ladder in `test_handoff_doc_size.py` ranks EVICT WHAT
    HAS CLOSED first and calls it "usually the whole answer" — but it says that
    to every author about every document, and nothing told them how much "here"
    holds for the doc in front of them. MEASURED 2026-09-20 corpus-wide: 468,110 B
    (14.1%) is already evictable — 284,262 B of resolved investigations alone —
    while 28 docs sit over the hard cap. The detection was never the gap.

    🔴 IT REUSES `handoff-audit.py`'S DETECTORS AND KEEPS NO COPY. A second
    implementation of "is this investigation resolved" is how the two answers
    drift, and that auditor's own comments record the corrections its matchers
    have already absorbed (a `retracted` bucket over-counted by 30% until bullets
    crossing a heading were clipped). One rule, one place.

    🔴 NEVER RAISES — this runs inside the WRITE PATH. An exception here would take
    down `/handoff`'s only landing step and cost a session its record, to decorate
    a warning. That reason alone carries the guard. Any failure ⇒ "" and the
    warning prints exactly as it did before.

    ⚠ An earlier draft added "another repo using this module has no such file,
    which is the ordinary case". Round 0 of #1815 checked and found neither
    `handoff_doc.py` nor `handoff-audit.py` vendored anywhere outside devrc
    clones, so that clause named a configuration nobody has. Removed rather than
    replaced with a better-sounding one: the write-path argument is sufficient,
    and reaching for a second justification is how a wrong one gets written.
    """
    if not _AUDITOR.is_file():
        return ""
    try:
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("_handoff_audit",
                                                      str(_AUDITOR))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        a = mod.audit_text(merged_text)
        # 🔴 ONLY THE TWO COUNTED BUCKETS. `retracted` and `dated` are the
        # playbook's step 2, and reporting them here is what this function used
        # to do — see the header for why that whole surface is gone.
        rows = [
            ("resolved investigations", a["resolved_b"], len(a["resolved"]), "block"),
            ("completed ranked items", a["done_b"], len(a["done"]), "item"),
        ]
        rows = [r for r in rows if r[1] > 0]
        if not rows:
            return ""
        out = ["  Evictable in THIS doc, measured:"]
        for label, b, n, unit in rows:
            out.append(f"    {label:<24}{b:>9,} B  ({n} {unit}{'' if n == 1 else 's'})")

        # 🔴 UNION, NOT SUM — `audit_text`'s `gross` adds the four buckets without
        # unioning their line ranges, and one block can land in two of them (an
        # H3 matching both RESOLVED_HEAD and WORK_STATUS). Measured: 7 of 851
        # handoff docs under ~/workspace overlap, worst overstatement 42.6%. That
        # is tolerable in a REPORT and not here, because this line promises
        # "CLEARS the N B you are over by" — an author who evicts everything named
        # and is still red got the one outcome worse than saying nothing.
        # Round 1 of #1815, F5. `audit_text`'s own `gross` is left alone: it is a
        # report number with its own tests, and this is the consumer that makes a
        # promise out of it.
        # 🔴 THE BUCKETS DO NOT SHARE A TUPLE SHAPE, and assuming they did was a
        # defect in the FIRST draft of this union: `retracted` is a 3-tuple
        # `(start, end, bytes)` while `resolved`/`dated` are `(title, start, end,
        # bytes)` and `done` is `(rank, start, end, bytes, is_done)`. Blindly
        # indexing [1],[2] read `retracted`'s END and BYTES as a line range, and
        # because this whole body is wrapped in a `except (Exception, SystemExit)`
        # the resulting IndexError would have DELETED THE NOTE SILENTLY rather
        # than failing loudly. Caught by a control that re-derived each bucket's
        # byte count from its own range. Indices are explicit and pinned by
        # `test_the_union_indices_match_each_buckets_tuple_shape`.
        # 🔴 A TRUE INTERVAL UNION, AND THE THIRD ATTEMPT AT THIS NUMBER. The
        # history is kept because each wrong version looked right:
        #   round 1 (F5) added a union over all four buckets — correct, because
        #     `resolved` and `dated` can hold the SAME block.
        #   round 2 narrowed the COUNTED set to step 1 = {resolved, done}, which
        #     left the union guarding a pair that (then) never overlapped.
        #   round 3 DELETED the union on the claim that those two are
        #     "structurally disjoint — `resolved` is blocks under
        #     `## Open investigations`, `done` is items under `## Next steps`".
        # 🔴 THAT CLAIM IS FALSE, and round 4 produced the counterexample.
        # `NEXT_STEPS` and `INVESTIGATIONS` are two REGEXES tested with `if` /
        # `if` over the SAME heading list (`handoff-audit.py`), not two headings —
        # so ONE H2 can match both. `## Open investigations / next steps` does
        # exactly that and exists in this corpus
        # (`claudedocs/archive/handoff-browser-bridge-gates-and-deploys-2026-08-02.md`).
        # A resolved `### ` block holding a done ranked item is then booked in
        # BOTH step-1 buckets, and summing them promised 1,746 B out of a 1,048 B
        # document — round 1's F5 outcome verbatim: evict everything named and
        # still be red. Reproduced, and pinned by
        # `test_a_heading_matching_BOTH_step1_detectors_is_not_double_counted`.
        # ⚠ The re-add trigger is NOT "a new bucket joins step 1" (round 3's
        # wording); it is a DOCUMENT putting investigations and ranked items under
        # one heading, which needs no code change at all.
        lines = merged_text.splitlines(keepends=True)
        spans = sorted((x[AUDIT_SPAN_INDEX[k][0]], x[AUDIT_SPAN_INDEX[k][1]])
                       for k in ("resolved", "done") for x in a[k])
        merged_spans: list[list[int]] = []
        for s_, e_ in spans:
            if merged_spans and s_ <= merged_spans[-1][1]:
                merged_spans[-1][1] = max(merged_spans[-1][1], e_)
            else:
                merged_spans.append([s_, e_])
        counted_b = sum(len("".join(lines[s_:e_]).encode()) for s_, e_ in merged_spans)
        net = max(0, counted_b - mod.RESUME_COST * len(a["done"]))
        # 🔴 NET, and the shortfall is stated rather than implied. Quoting a gross
        # number that does not actually clear the overage sends an author cutting
        # and leaves them still red — the one outcome worse than saying nothing.
        # 🔴 SCOPED TO THE `over_by > 0` CALLERS, which since #1826 means the
        # GATED over-budget arm alone. The two `over_by=0` callers — the near
        # arm, and the UNGATED over-budget arm — deliberately take the `else`
        # branch and state no relation to any overage. For the near arm there
        # is no overage to relate to. For the ungated arm it is a choice: no
        # gate will go red there, so "still red" cannot happen, and the arm's
        # own comment explains why it declines to assert a deficit against a
        # ceiling nothing enforces. ⚠ The COST is real and is recorded there:
        # 12 of the 13 docs that arm newly reaches have `net < over_by`, and
        # it is the reader who must subtract. Round 1 of #1826 found this
        # comment asserting, unscoped, a rule that its newest caller breaks.
        # 🔴 NO POINTER TO THE PLAYBOOK OR THE LADDER ON THESE LINES, and their
        # removal is what let this note reach the UNGATED arms at all. Both were
        # decoration on a number — "steps 2-4 of the playbook cover the rest" on
        # the shortfall case, "before you need the ladder at all" on the zero
        # case — and both named a remediation ladder that lives in a test only
        # devrc ships. In a repo without it they are the civitai/cli#618 failure
        # in miniature: an authoritative-sounding step nobody's gate requires.
        # In a repo WITH it they were a second copy of what the over-budget arm
        # already prints in full, three lines above. One rule, one place: the
        # ladder belongs to `test_handoff_doc_size.py`, this note to the numbers.
        if net >= over_by > 0:
            out.append(f"    → {net:,} B net, which CLEARS the {over_by:,} B you are over by.")
        elif over_by > 0:
            out.append(f"    → {net:,} B net, which does NOT clear the {over_by:,} B "
                       f"you are over by.")
        else:
            out.append(f"    → {net:,} B net already closed in this document.")
        # 🔴 CONDITIONAL, and the unconditional version was a real defect (round 0,
        # F4): it explained a charge that had not been applied, on a note whose
        # whole argument is that a line printing every time is a line nobody
        # reads. `net` is only charged for completed RANKS, so the sentence
        # belongs only when that row is present.
        if any(label == "completed ranked items" for label, *_ in rows):
            out.append("    Net of 200 B per evicted rank: the NUMBER must stay (it is "
                       "half a claim-work slug).")
        # Reported, never promised: step 2 is a MOVE to `refs/` that must leave a
        # pointer, so these bytes are not recovered at face value — and the
        # playbook keeps gotchas in the doc. Counting them toward "CLEARS" is the
        # defect this split fixes; naming them is still useful.
        return "\n".join(out)
    except (Exception, SystemExit):
        # 🔴 `SystemExit` IS NOT AN `Exception` — it derives from BaseException,
        # and `handoff-audit.py`'s own `_load_sibling()` raises exactly that, at
        # MODULE level, when `scripts/skill-audit.py` is missing. So a bare
        # `except Exception` left the one import failure this function is most
        # likely to meet uncaught, on the WRITE PATH, where it kills the write
        # `budget_warning` is only decorating. Round 1 of #1815, F3.
        # KeyboardInterrupt is deliberately NOT caught: a human interrupting the
        # write must still interrupt it.
        return ""


def budget_warning(relpath: str, merged_text: str, base_text: str, *,
                   gated: bool) -> str:
    """A one-block warning, or "" when there is nothing worth saying.

    Silent by default: a doc with room says nothing, because a line that prints
    on every run is a line nobody reads by the third one.

    🔴 `gated` IS REQUIRED AND HAS NO DEFAULT, deliberately. A default is how the
    false claim survived: every caller got the devrc answer whether or not it was
    writing to devrc. Pass `gate_enforces_budget(repo)`.
    """
    if not relpath.startswith("claudedocs/") or "/handoff-" not in "/" + relpath:
        return ""
    after = len(merged_text.encode("utf-8"))
    before = len(base_text.encode("utf-8"))
    allowance = handoff_budget.GRANDFATHERED.get(relpath, handoff_budget.MAX_BYTES)
    grandfathered = relpath in handoff_budget.GRANDFATHERED
    delta = after - before
    sign = "+" if delta >= 0 else ""

    if after > allowance and not gated:
        # 🔴 REPORT THE NUMBER, PRESCRIBE NOTHING. The remediation ladder is what
        # actually cost bytes in civitai/cli#618 — 35,517 B went to
        # `claudedocs/refs/` because a step told someone to put it there — not the
        # word "RED". Outside devrc that ladder has no authority: it cites a
        # playbook in a test the repo does not ship. The SIZE still transfers
        # (measured across the other repos' corpora), so the number stays.
        #
        # 🔴 THE NOTE PRINTS HERE, BY THE OPERATOR'S REVERSAL — but with
        # `over_by=0`, so that THE NOTE names no threshold. Scope that claim
        # to the NOTE and nothing wider: the head line in this arm's own
        # `return` states the overage outright (`over by {N} B`), deliberately and
        # unchanged, and `test_EVERY_branch_that_names_the_gate_is_repo_aware`
        # pins it (`assert "over by 1 B" in ungated_over`). An earlier
        # wording of THIS comment said the arm names no threshold, which that
        # test falsifies — round 1 of #1826. (No line number here: this
        # ladder has now produced three non-reproducing counts, so the test
        # is named and nothing else is claimed about where it sits.)
        # What `over_by=0` buys is that the note does not ADD a second,
        # louder deficit ("does NOT clear the N B you are over by") on top of
        # it: that is the civitai/cli#618 pressure shape with the
        # prescription removed and the false-consequence framing kept.
        # ⚠ `gate_enforces_budget` above and the paragraph above DISAGREE
        # about which half of #618 did the damage, and #618's own body
        # settles neither. This arm assumes the worse case.
        # ⚠ CONSEQUENCE, STATED BECAUSE IT IS A REAL COST: with `0` the note
        # reports what has closed without relating it to the overage, and on
        # the corpus this change was justified by, 12 of the 13 docs that now
        # print a note have `net < over_by`. The reader must subtract the two
        # numbers themselves. See the NET comment above `evictable_note`'s
        # three-way branch, which is scoped to the `over_by > 0` callers for
        # exactly this reason.
        which = ("its grandfathered allowance" if grandfathered
                 else "the handoff-document ceiling")
        note = evictable_note(merged_text, 0)
        return "\n".join([
            f"⚠ SIZE ONLY, NO GATE: {after:,} B against {which} of "
            f"{allowance:,} B, over by {after - allowance:,} B "
            f"({sign}{delta:,} B this update).",
            f"  This repo ships no `{BUDGET_GATE_RELPATH}`, and that test reads "
            "only the tree it lives in — nothing will go red and nothing is "
            "inherited by anyone. Treat it as JUDGEMENT about what the next "
            "session has to read, not as a build to fix.",
            *([note] if note else []),
        ])

    if after > allowance:
        which = ("its grandfathered allowance" if grandfathered
                 else "the handoff-document ceiling")
        # Computed ONCE: `evictable_note` execs the auditor module, so calling it
        # twice to test-then-use would pay that twice on the write path.
        note = evictable_note(merged_text, after - allowance)
        return "\n".join([
            f"🔴 THIS UPDATE PUTS THE DOC OVER ITS SIZE BUDGET: {after:,} B "
            f"against {which} of {allowance:,} B, over by {after - allowance:,} B "
            f"({sign}{delta:,} B this update).",
            "  `test_no_handoff_doc_exceeds_its_budget` will go RED on `main`, and it "
            "fails for EVERYONE — the next unrelated PR inherits it.",
            "  Fix in the order that test's own playbook prescribes, and raising a "
            "number is LAST: evict what has CLOSED (usually the whole answer), then "
            "demote dated evidence to `claudedocs/refs/<topic>.md` leaving a pointer, "
            "then split by initiative.",
            *([note] if note else []),
            "  🔴 Do NOT satisfy it by deleting an open investigation, a gotcha or a "
            "ruled-out theory — those are the sections whose whole value is that a "
            "future session does not repeat the work.",
            "  This is a WARNING, not a refusal: a blocking check here would deadlock "
            "against the write-back guard, and an unrecorded session costs more.",
        ])

    if grandfathered and after <= handoff_budget.MAX_BYTES and gated:
        # Gated only: the ledger it tells you to edit lives in THIS repo. An
        # ungated repo reaching here did so by a relpath COLLISION with devrc's
        # ledger — which also silently handed it an allowance as large as the
        # biggest entry in handoff_budget.GRANDFATHERED. No figure is quoted:
        # that ledger is edited by the same commits that shrink the docs, so a
        # number here is stale the moment one entry ratchets down.
        return (f"✅ This doc is now {after:,} B, back under the "
                f"{handoff_budget.MAX_BYTES:,} B ceiling — DELETE its "
                f"`GRANDFATHERED` entry in scripts/lib/handoff_budget.py in this "
                f"same commit. The ledger is a ratchet; an entry left behind "
                f"licenses the regrowth it was installed to catch.")

    headroom = allowance - after
    if headroom < BUDGET_NEAR_BYTES:
        tail = ("evicting what has CLOSED now is cheaper than doing it under a "
                "red `main`." if gated else
                "no gate enforces it here, so this is a note about readability, "
                "not a deadline.")
        head = (f"⚠ Size: {after:,} B of {allowance:,} B "
                f"({sign}{delta:,} B this update) — {headroom:,} B left. The next "
                f"update or two will go over; {tail}")
        # 🔴 UNCONDITIONAL, by the operator's reversal. Already `over_by=0`
        # here, so THE NOTE has always named no threshold — which is why it
        # needed no change when the ungated OVER arm was corrected to match
        # it. ⚠ Scoped to the note: this arm's own head line does state one
        # (`{after} B of {allowance} B … {headroom} B left`), and an earlier
        # wording of this comment claimed the arm rendered "numbers without a
        # threshold" outright, which that line falsifies — round 1 of #1826.
        # The gated/ungated split survives where it is still about a gate:
        # `tail` just above, and the GRANDFATHERED arm ABOVE this one.
        # ⚠ NO PROHIBITION HERE. One was added in round 4 and is removed by the
        # /the-algorithm pass: the eviction ladder — including "do NOT satisfy
        # this by deleting an open investigation, a gotcha or a ruled-out
        # theory" — belongs to `test_handoff_doc_size.py`, which the over-budget
        # arm already cites. Restating it here is the second copy that made this
        # advice wrong four times; this arm carried none before the note existed.
        note = evictable_note(merged_text, 0)
        return f"{head}\n{note}" if note else head
    return ""


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
# That shape exits 9 (`stale-base`) with an explicit override. Scoping the
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
    NEXT_STEPS_PREFIX,
    "how to verify",
    "what shipped",
    *APPEND_PREFIXES,
)

# A base with fewer sections than this is a stub, not an established handoff, and
# a canonical heading arriving NEW in one is ordinary growth. Of the 44 real docs
# exactly one has 3 sections and three have 4; the median is 6.
MIN_ESTABLISHED_SECTIONS = 4


#: Leading punctuation, emoji or arrows a session hangs on a canonical heading.
#: STRIPPED, because `▶ Next steps (ranked)` is the SAME section as
#: `Next steps (ranked)` wearing decoration — not a reworded heading. 🔴 THE
#: BOUND IS DELIBERATE: only LEADING non-alphanumerics, so this can never turn a
#: SYNONYM into a match. `Ranked next steps` and `Backlog — …` still classify as
#: None, and that is not an oversight — see `is_next_steps_heading`.
#: MEASURED over all 1,051 headings in the 147-doc corpus: exactly 2 change
#: classification, `▶ Next steps (ranked)` -> `next steps` and
#: `⚠️ Gotchas / standing notes` -> `gotchas`, both of which plainly ARE those
#: sections. Everything else is untouched.
_HEADING_DECORATION = re.compile(r"^[^0-9A-Za-z]+")


def canonical_prefix(heading: str) -> str | None:
    """The skeleton section this heading TEXT belongs to, or None.

    🔴 THE SOLE OWNER of "is this heading canonical?", and that is a fix rather
    than a description (#964 item 3). `ranked_items` used to re-derive rule
    (j)'s half with a raw `heading_text(h).lower().startswith("next steps")`,
    which skips BOTH normalisations this does — so the two disagreed, measurably:
    `## Next  steps` (two spaces) was canonical here and NOT a ranked section
    there, silently exempting every item under it from rule (j). One rule, two
    places, wrong at one.
    """
    low = _HEADING_DECORATION.sub("", " ".join(heading.lower().split()))
    for prefix in CANONICAL_HEADING_PREFIXES:
        if low.startswith(prefix):
            return prefix
    return None


def is_next_steps_heading(heading: str) -> bool:
    """Is this heading TEXT the ranked queue rule (j) gates? Via the one owner.

    🔴 WHAT THIS DELIBERATELY DOES NOT DO, stated because the gap is MEASURED
    and is a decision rather than a miss: it does not recognise a REWORDED
    heading. Across the 147-doc corpus, 20 sections carrying 96 items are read
    by a human as the ranked queue and are exempt here — `Ranked next steps`,
    `Open items, ranked`, `Backlog — the highest-ROI UNBUILT items (ranked)`,
    `Resume next session`, `Next session — priority order`, and 15 more.

    Widening by SYNONYM is refused on purpose: `claude/RULES.md` says a guard on
    WORDS is walkable by REWORDING, and a synonym list makes rule (j)'s coverage
    unpredictable at the moment a session is trying to obey it — the same
    argument `_TOPIC_DATE` and `FORCING_KINDS` are already built on. What IS
    normalised is the two ways of spelling the SAME heading: whitespace, and
    leading decoration. `## Next steps` is what the template mandates, so a
    compliant session is covered and a deviating one is not gated.
    """
    return canonical_prefix(heading) == NEXT_STEPS_PREFIX


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


def doc_tracked_at_head(repo: Path, relpath: str) -> bool | None:
    """Is this doc in HEAD's tree? `None` when git could not answer.

    🔴 THE WORKING COPY IS NOT THE DOCUMENT, AND `not base_text` CANNOT TELL THE
    TWO APART. `: > claudedocs/handoff-<topic>.md` leaves a tracked document with
    an empty working copy, which every text-only predicate reads as "there is no
    document here" — so rule (n)'s ratchet switched OFF and rule (m) asserted
    "This is a NEW handoff doc" about a file with a full history. Found by round
    1 of this PR's own audit (F3), reproduced end to end: a queue went 3 -> 5
    `forcing: none` at exit 0 and the merge replaced the committed document
    wholesale.

    ⚠ `None` is NOT `False`, and the caller must not collapse them. git failing
    to answer is not evidence the doc is new; treating it as such would refuse a
    grandfathered document on a repo this tool merely could not read.
    """
    # 🔴 `ls-tree`, NOT `cat-file -e`, AND THE FIRST DRAFT USED THE WRONG ONE.
    # MEASURED: `git cat-file -e HEAD:<absent>` exits **128**, the same code a
    # broken or absent HEAD gives — so the two cases this function exists to
    # separate are indistinguishable through it. Read as "could not answer",
    # that made EVERY genuinely-new doc look like it might exist, which
    # switched rule (m)'s whole REFUSE arm off; the module's own suite caught
    # it (8 tests) the moment the fix landed.
    #
    # `ls-tree` separates them cleanly: **rc 0 with empty output** is a real
    # "absent from that tree", and rc 128 is reserved for "no such tree".
    if git_allow(repo, "rev-parse", "--verify", "-q", "HEAD").code != 0:
        return None  # no HEAD to ask about — not evidence either way
    got = git_allow(repo, "ls-tree", "--name-only", "HEAD", "--", relpath)
    if got.code != 0:
        return None
    return bool(got.out.strip())


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


def restore_doc_bytes(doc: Path, original: bytes | None) -> bool:
    """Put `doc` back to `original`, `None` meaning "it did not exist". True if
    that worked.

    🔴 THE `None`-MEANS-UNLINK CONVENTION IS WHY THIS IS A NAMED FUNCTION. A
    copy that wrote `b""` instead of unlinking would leave behind a file the run
    created, which is exactly the "a failure writes nothing" property this
    module is built on. ⚠ It has ONE caller again — `_undo_write`, after a
    commit that never happened. It had two while `leak_gate` un-wrote the doc to
    take a baseline scan; that scan is gone. Kept named rather than inlined
    because the convention, not the call count, is the thing worth stating once.

    Non-raising for `_undo_write`'s reason: it runs on error paths, where a
    rollback that threw would replace the caller's diagnosis with its own.
    """
    try:
        if original is None:
            doc.unlink(missing_ok=True)
        else:
            doc.write_bytes(original)
    except OSError:
        return False
    return True


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
    if not restore_doc_bytes(doc, original):
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


# --- rule (o): the repo's OWN leak scanner reads the delta before the commit --
#
# 🔴 THE TARGET REPO'S SCANNER, NEVER A SCANNER THIS TOOL SHIPS. This module
# writes handoff docs into many repositories, and what counts as sensitive is a
# property of the repository, not of the tool: one repo's denied-identifier set
# is another repo's ordinary vocabulary. So the gate RESOLVES a scanner out of
# the repo the doc lives in, and a repo that has none PASSES. Refusing there
# would make the tool unusable in most repos and would be the permanently-red
# gate `claude/RULES.md` says trains everyone to route around.
#
# 🔴 THE CLOSED SET IS A LOOKUP, NOT A SEARCH, and it is now ONE path. A glob
# for `*leak*` would pick up a fixture, a README or another tool's helper and
# then run it as this repo's gate — and the hazard is not only a glob's. The set
# declared three paths; MEASURED across 175 checkouts, `scripts/leakscan.py` and
# a ROOT `leakscan.py` existed in ZERO of them, so they bought no repository any
# coverage, while a root-level `leakscan.py` is exactly where a FIXTURE or an
# EXAMPLE sits — a smaller version of the thing the paragraph above rejects a
# glob for. The survivor is `.py` by construction, which is why the scanner is
# launched with `sys.executable`. Adding a candidate is adding a program this
# tool will EXECUTE in someone else's repository; measure that it exists first.
LEAKSCAN_CANDIDATES = ("tests/leakscan.py",)

#: Wall-clock ceiling on one scan. A scanner that hangs must not hang a handoff
#: — and it must not PASS one either: a timeout raises `ScannerUnusable`, which
#: is a refusal.
#:
#: ⚠ 300 IS UNATTRIBUTED — nobody derived it, and this line says so rather than
#: inventing a derivation. The only measurement beside it is the ~2.0s the real
#: scanner takes on the tree this was built against. It is kept because a
#: generous ceiling fails in the safe, loud direction (on a hang, not on a slow
#: machine), which is a reason to keep the number, not a reason it is the right
#: one.
LEAKSCAN_TIMEOUT_SECONDS = 300

#: How many of the scanner's own lines a refusal prints before eliding. Same
#: reasoning as `DROPPED_SHOWN_MAX`: the block is an aid to recognising what to
#: fix, not an inventory, and the elision line carries the command that shows
#: the rest.
LEAKSCAN_SHOWN_MAX = 20

#: 🔴 THE OPERATOR OPT-IN FOR AN ALREADY-RED TREE, and it is rule (n)'s shape
#: rather than a second spelling of the same idea: a deliberately long
#: `--…-approved` flag held in a constant, declared `store_true`, and named by
#: the refusal it overrides. `claude/RULES.md`: one rule, one place — two
#: override conventions in one parser is how the next one gets invented too.
LEAK_PRE_EXISTING_FLAG = "--leak-pre-existing-approved"


class ScanRun(typing.NamedTuple):
    """One scanner invocation: its exit code and everything it printed.

    🔴 STDOUT AND STDERR ARE CONCATENATED, NOT INTERLEAVED, and that is fine for
    the only thing read off `output`: display. Nothing here parses the format —
    `claude/RULES.md`: parsing a tool's output makes its FORMAT a dependency you
    did not pin, and this gate has to work against a scanner it has never seen.
    """

    code: int
    output: str


class ScannerUnusable(RuntimeError):
    """The scanner could not be RUN, so it never read the delta at all.

    Distinct from "the scanner refused": a refusal is an answer. This is the
    absence of one, and it is still a refusal on THIS gate — a gate that cannot
    read is not a pass. Kept separate from `GitError` so `main`'s `status=failed`
    handler cannot swallow it and report a git problem.
    """


def find_leak_scanner(repo: Path) -> Path | None:
    """The target repo's own leak scanner, or None if it has none."""
    for rel in LEAKSCAN_CANDIDATES:
        candidate = repo / rel
        if candidate.is_file():
            return candidate
    return None


def run_leak_scanner(scanner: Path, repo: Path) -> ScanRun:
    """Run `scanner` over `repo` as it stands. Raises `ScannerUnusable`.

    No arguments are passed. A repo-local scanner's argument surface is its own
    business — `tests/leakscan.py` takes `--self-test`/`--quiet` and neither is
    ours to choose — and an unrecognised flag would make argparse exit 2, which
    this gate reads as a refusal. The bare invocation is the one every candidate
    is guaranteed to understand.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(scanner)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=LEAKSCAN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ScannerUnusable(
            f"it did not finish within {LEAKSCAN_TIMEOUT_SECONDS}s"
        ) from None
    except OSError as exc:
        raise ScannerUnusable(f"it could not be started: {exc}") from None
    return ScanRun(proc.returncode, proc.stdout + proc.stderr)


class LeakVerdict(typing.NamedTuple):
    """`refusal` is stderr and means NOTHING may be written; `notes` is stdout
    on a run that proceeds. Exactly one of them is non-empty."""

    refusal: str
    notes: str


def leak_absent_note(repo: Path) -> str:
    """🔴 A PASS BY ABSENCE IS NOT A CLEAN RESULT, AND IT SAYS SO.

    Most repositories have no scanner, so this is the ordinary line. It exists
    because `claude/RULES.md`'s reassuring-zero rule applies to a gate that
    silently did not run exactly as much as to one wired to nothing: a run that
    printed nothing here would be indistinguishable from a scanned, clean delta.
    """
    return (
        f"leakscan: NO SCANNER FOUND in {repo} — looked for "
        + ", ".join(LEAKSCAN_CANDIDATES)
        + ".\n  This delta was NOT scanned for sensitive content. That is a PASS "
        "BY ABSENCE, not a clean result."
    )


def leak_clean_note(scanner: Path, repo: Path, relpath: str) -> str:
    return (
        f"leakscan: {_scanner_rel(scanner, repo)} exited 0 with this delta "
        f"written into {relpath} — the repo's OWN gate vouches for it."
    )


def _scanner_rel(scanner: Path, repo: Path) -> str:
    try:
        return str(scanner.relative_to(repo))
    except ValueError:  # pragma: no cover — `find_leak_scanner` builds it from repo
        return str(scanner)


def _rerun_hint(scanner: Path, repo: Path) -> str:
    return f"{sys.executable} {_scanner_rel(scanner, repo)}   # from {repo}"


def _scanner_tail(run: ScanRun) -> tuple[list[str], int]:
    """The last `LEAKSCAN_SHOWN_MAX` lines of a scan, and how many were dropped.

    The TAIL rather than the head: every scanner here ends with its verdict, and
    a head-first elision cuts exactly that off. One place, because both the
    refusal and the approved-through note print the same thing for the same
    reason — the operator has to see what was refused.
    """
    lines = run.output.splitlines()
    shown = lines[-LEAKSCAN_SHOWN_MAX:]
    return shown, len(lines) - len(shown)


def leak_refusal_report(
    scanner: Path, repo: Path, relpath: str, run: ScanRun
) -> str:
    """The scanner would not vouch for the tree with this delta in it.

    🔴 THE SCANNER'S OWN LINES ARE REPRODUCED, and the trade is deliberate: a
    refusal that does not say which line and which rule is one the operator
    cannot act on. It does re-state the flagged text into this transcript — but
    that text is already in the operator's own scratch file and was about to be
    committed, so the transcript is not where it becomes public; the commit is,
    and that is the thing this refusal stops.

    🔴 IT CLAIMS THE SCANNER REFUSED, NOT THAT THIS DELTA CAUSED IT. The gate
    makes no attribution, so the message offers BOTH remedies and does not
    pretend to know which one applies: fix the scratch file, or — if the tree
    was already red — say so explicitly with the flag.
    """
    shown, elided = _scanner_tail(run)
    rel = _scanner_rel(scanner, repo)
    return (
        f"status=leak-refused scanner={rel} exit={run.code}\n"
        f"NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
        f"  {rel} exits {run.code} on this tree with the delta written into "
        f"{relpath}, so the repo's OWN gate will not vouch for what was about "
        f"to be committed and pushed.\n"
        f"  🔴 ZERO IS THE ONLY PASS. A scanner that exits 2 is saying `could "
        f"not vouch` — a control of its own misbehaved — which is not a clean "
        f"result either.\n"
        f"  The last {len(shown)} line(s) it printed:\n"
        + "".join(f"    {line}\n" for line in shown)
        + (
            f"    … and {elided} earlier line(s) — see them all with:\n"
            f"      {_rerun_hint(scanner, repo)}\n"
            if elided
            else ""
        )
        + f"  Fix the SCRATCH FILE (--update), not {relpath}, and re-run: the "
        f"doc was rolled back to the bytes this run found, so nothing has to be "
        f"undone first.\n"
        f"  🔴 IF THIS TREE WAS ALREADY RED for something this handoff did not "
        f"cause, that is an OPERATOR DECISION and not a guess this tool may "
        f"make for you: read the lines above, then re-run with "
        f"{LEAK_PRE_EXISTING_FLAG}, which records on the run that it was "
        f"approved through. A handoff delta is the exact path four leak events "
        f"took, one of them onto a PUBLIC repository's mainline."
    )


def leak_approved_note(
    scanner: Path, repo: Path, relpath: str, run: ScanRun
) -> str:
    """The scanner refused and the OPERATOR approved it through. WRITTEN.

    🔴 AN APPROVED-THROUGH RUN MUST NOT READ LIKE A CLEAN ONE, and that is the
    whole reason this is a flag rather than an attribution heuristic. Both end
    `status=written`; only this one carries the flag's own name, the scanner's
    exit code and its output, so a reader of the transcript afterwards can tell
    which decision was made and re-take it.
    """
    shown, elided = _scanner_tail(run)
    rel = _scanner_rel(scanner, repo)
    return (
        f"🔴 LEAK GATE APPROVED THROUGH by {LEAK_PRE_EXISTING_FLAG} — {rel} "
        f"exited {run.code} with this delta written into {relpath}, and the "
        f"write went ahead anyway.\n"
        f"  This is an OPERATOR DECISION, not a clean result: the repo's own "
        f"gate refused, and the flag asserts a human read that refusal and "
        f"judged it PRE-EXISTING in this tree rather than caused by this "
        f"delta.\n"
        f"  ⚠ NOTHING HERE CHECKED THAT. The gate does not compare scans, so "
        f"what was approved is everything below, whatever produced it.\n"
        f"  The last {len(shown)} line(s) {rel} printed"
        + (f" ({elided} earlier line(s) elided)" if elided else "")
        + ":\n"
        + "".join(f"    {line}\n" for line in shown)
        + f"  Full output: {_rerun_hint(scanner, repo)}"
    )


def leak_unscannable_report(
    scanner: Path, repo: Path, relpath: str, reason: str
) -> str:
    """The scanner could not be run, so it never read the delta."""
    rel = _scanner_rel(scanner, repo)
    return (
        f"status=leak-refused scanner={rel}\n"
        f"NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
        f"  {rel} exists, so this repository HAS a leak gate — but this run "
        f"could not get a verdict out of it: {reason}.\n"
        f"  🔴 A GATE THAT CANNOT READ IS NOT A PASS. `could not vouch` and "
        f"`clean` are different answers, and only one of them lets a delta onto "
        f"a shared branch.\n"
        f"  The delta was rolled back. Fix the scanner — it is the same gate "
        f"this repo's CI runs — then re-run:\n"
        f"      {_rerun_hint(scanner, repo)}"
    )


def leak_gate(
    repo: Path, relpath: str, scanner: Path | None, approved: bool
) -> LeakVerdict:
    """Rule (o). Run the repo's own scanner over the tree with the delta already
    written into it, and refuse unless it exits 0. This gate does not touch the
    file — the CALLER owns both the write and the rollback.

    🔴 FLAT REFUSE ON ANY NON-ZERO EXIT, AND NO ATTRIBUTION. That is a DECISION,
    not a simplification, and the shape it replaced is why. A whole-tree scanner
    cannot be asked about one file — `tests/leakscan.py` takes no paths at all,
    it enumerates the repo from its own location — so "did THIS delta cause it?"
    can only be a comparison between two scans, and that comparison is a guess:
    a concurrent writer in a shared checkout, a scanner whose rule set grew
    between the runs, or a new finding whose line is byte-identical to one the
    scan was already printing each make it the WRONG guess. The previous shape
    took that guess and, when it could not decide, printed a banner and
    committed AND pushed anyway. Never ship a delta while the scanner refuses.

    🔴 THE ALREADY-RED TREE IS AN OPERATOR DECISION, WHICH IS WHAT KEEPS THIS
    OFF THE PERMANENTLY-RED LIST. A target tree can be red for something this
    call did not cause; with no way past, the gate would be unclearable and
    `claude/RULES.md` says such a gate trains everyone to route around it.
    `--leak-pre-existing-approved` is that way past — explicit, and recorded in
    the run's own output by `leak_approved_note`, so an approved-through run is
    distinguishable afterwards from a clean one.

    ⚠ THE OPT-IN DOES NOT COVER THE UNRUNNABLE ARM, deliberately. A scanner that
    hung or could not be started never produced a verdict, so there is nothing
    for an operator to have read and approved — and approving an absence is the
    reassuring zero this gate exists to refuse to print.
    """
    if scanner is None:
        return LeakVerdict("", leak_absent_note(repo))
    try:
        run = run_leak_scanner(scanner, repo)
    except ScannerUnusable as exc:
        return LeakVerdict(
            leak_unscannable_report(scanner, repo, relpath, str(exc)), ""
        )
    if run.code == 0:
        return LeakVerdict("", leak_clean_note(scanner, repo, relpath))
    if approved:
        return LeakVerdict("", leak_approved_note(scanner, repo, relpath, run))
    return LeakVerdict(leak_refusal_report(scanner, repo, relpath, run), "")


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
        UNFORCED_GROWTH_FLAG,
        action="store_true",
        help="override the status=rank-growth refusal: let this update add a "
        "self-generated (`forcing: none`) ranked item the document did not have. "
        "This is the OPERATOR OPT-IN the arc study asks for — the default path "
        "for an audit finding is a `## Defects (batched)` entry, not a new rank.",
    )
    p.add_argument(
        LEAK_PRE_EXISTING_FLAG,
        action="store_true",
        help="override the status=leak-refused refusal: proceed even though the "
        "TARGET repo's own leak scanner exits non-zero, asserting that a human "
        "read its output and judged the finding PRE-EXISTING in that tree rather "
        "than caused by this delta. The run RECORDS that it was approved "
        "through, so it does not read afterwards as a clean scan. Does NOT apply "
        "when the scanner could not be RUN at all — there is no verdict to "
        "approve.",
    )
    p.add_argument(
        "--push",
        action="store_true",
        help="also push the commit. Requires --confirm; this is the half the gate exists for.",
    )
    p.add_argument("--remote", default="origin")
    p.add_argument("--branch", help="defaults to the repo's current branch")
    return p


#: 🔴 THE SAME PRECEDENCE `scripts/lib/clawgate_handoff.sh` USES, AND THE ORDER
#: IS NOT COSMETIC. Inside opencode, an `OPENCODE_SESSION_ID` is per-call while a
#: `CLAUDE_CODE_SESSION_ID` in scope may be one INHERITED from an ancestor Claude
#: Code session — reading the claude var first therefore attributes an opencode
#: commit to whichever session happened to spawn it. There is no
#: `CLAUDE_SESSION_ID`; that name does not exist and reading it yields nothing.
SESSION_ID_ENV_ORDER = ("OPENCODE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")


def resolve_session_id(env: typing.Mapping[str, str] | None = None) -> str:
    """This session's id for the commit trailer, or '' when none is resolvable.

    Environment first (this tool runs INSIDE the session, so the vars are right
    there), then `session_trailer.lookup()`, which walks /proc for a Claude
    ancestor and is what the `prepare-commit-msg` hook uses when there is no env
    to read.

    🔴 RETURNS '' RATHER THAN A PLACEHOLDER. An unresolvable id must produce NO
    trailer at all: a trailer carrying `unknown` is worse than none, because the
    arc reader would count the commit as stamped and then resolve every such
    commit across every repo to one imaginary session.
    """
    src = os.environ if env is None else env
    for name in SESSION_ID_ENV_ORDER:
        value = (src.get(name) or "").strip()
        if value and session_trailer.valid_id(value):
            return value
    try:
        return (session_trailer.lookup() or "").strip()
    except Exception:
        # Best-effort by design: attribution must never break the write the
        # operator actually asked for.
        return ""


def commit_message(subject: str, session_id: str | None = None) -> str:
    """`subject`, carrying exactly one `Claude-Session-Id:` trailer when known.

    🔴 THE TRAILER IS WHAT MAKES A HANDOFF DOC'S ARC RECONSTRUCTIBLE — it is the
    WRITER half `scripts/lib/handoff_arc.py` reads, and the half that includes
    the ORIGINATING session, which by definition never resumed from the doc it
    created and so appears in no transcript search.

    🔴 AND IT DOES NOT DOUBLE-STAMP. MEASURED 2026-09-18: a `--confirm --push`
    run from a worktree of `~/workspace/devrc` produced a commit ALREADY carrying
    this trailer, because `install-session-stamp.sh` had armed
    `prepare-commit-msg` in that clone's COMMON git dir, which every worktree
    shares. So the hook-present case is the DEFAULT on a developed clone, not an
    edge case, and a naive append here would have emitted two trailers on its
    very first run. `session_trailer.append_trailer` is idempotent for the same
    id and corrective for a different one, so reusing it — rather than writing a
    second appender here — is what makes the two writers compose. One rule, one
    place.

    The clones this function exists FOR are the ones with no hook: this arc's own
    originating commit is unstamped for exactly that reason.
    """
    sid = resolve_session_id() if session_id is None else session_id
    if not sid:
        return subject
    return session_trailer.append_trailer(subject, sid)


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
            f"  🔴 There is no FLAG to bypass this — a SPELLING still can, and "
            f"reference/write-gate.md §C names the three that do. RE-MEASURED "
            f"2026-08-31 over the 147 real handoff docs in devrc + "
            f"homelab-talos: 55 (37%) carry a date, and collapsing them by date "
            f"exposes `remix-session` x8 and `browser-bridge` x3 — the same "
            f"effort, once per session.",
            file=sys.stderr,
        )
        return EXIT_DOC_PER_EFFORT

    # 🔴 READ AT RULE (i), NOT AT THE MERGE. Rule (i) asks "is this a new
    # effort?" and a copy of this doc on the MAINLINE answers it: no — this is a
    # stale checkout. Without that, rule (i) fires first in every realistic repo
    # (it needs only "doc absent + repo has handoff docs") and the stale-base
    # refusal below becomes UNREACHABLE in the exact shape it exists for.
    # MEASURED on the merge that introduced this: realistic repo, doc absent
    # here and present on the mainline -> rc 7 `status=new-doc`, telling the
    # operator to re-run with `--new-effort` for a doc that already exists
    # upstream. Neither rule is wrong alone and git reported NO conflict.
    currency = base_currency(repo, relpath)

    if (not doc.exists() and not args.new_effort
            and not currency.replaces_mainline_doc("")):
        local = existing_handoff_docs(repo)
        # 🔴 THE TRIGGER IS THE LOCAL LIST; THE MAINLINE ONLY COMPLETES IT.
        # `if local`, deliberately NOT `if local or upstream`, and that is a
        # MEASURED boundary rather than caution: gating on the union made rule
        # (i-b) fire in a repo whose working tree has no handoff docs at all
        # while the mainline has some, taking FOUR pre-existing tests red at
        # once — `test_a_GENUINELY_NEW_doc_is_untouched_by_this` among them,
        # i.e. the exact exit-0 contract #1046 landed to protect. #964 item 4's
        # complaint was that the LIST is blind to the mainline, never that the
        # rule should refuse more often. Computing `upstream` inside this branch
        # also keeps the extra `git ls-tree` off the bootstrap path.
        if local:
            # 🔴 THE MAINLINE'S DOCS TOO. Appended rather than merged into the
            # mtime order, because a blob on a ref HAS no mtime: inventing a
            # position for it would make `existing_handoff_docs`' "NEWEST FIRST"
            # contract a lie for half the rows. Only the ones ABSENT here are
            # shown, and they are LABELLED, so the reader can tell "you have
            # this" from "you would have to fetch this".
            upstream = [
                name for name in mainline_handoff_docs(repo, currency.base_ref)
                if name not in set(local)
            ]
            existing = local + upstream
            shown = existing[:EXISTING_SHOWN_MAX]
            elided = len(existing) - len(shown)
            upstream_set = set(upstream)
            ref = currency.base_ref or "the mainline"
            print(
                "status=new-doc\n"
                "NOTHING WRITTEN — not the doc, not a commit, not a ref.\n"
                f"  {relpath} does not exist, and this WORKING TREE already has "
                f"{len(local)} handoff doc(s)"
                + (f" ({len(upstream)} more on {ref})" if upstream else "")
                + ". Creating a second doc for an "
                f"effort that already has one is the thing the one-doc-per-effort "
                f"rule caps (operator decision 2026-08-28).\n"
                "  If one of these IS this effort, re-run with its topic — the "
                "update lands in place and nothing is lost:\n"
                + "\n".join(
                    f"    {name}" + (f"    (on {ref} only)" if name in upstream_set else "")
                    for name in shown
                )
                # 🔴 THE COMMAND MUST NAME THE SOURCE THAT WAS ELIDED. A single
                # `ls-tree` line would be wrong whenever the tail is local, and
                # a single `ls` wrong whenever it is upstream — the two lists
                # have different sources and no one command enumerates the
                # union. So the local half is always offered and the ref half
                # only when there ARE upstream rows to see.
                + (
                    f"\n    … and {elided} more — `ls {repo}/claudedocs/"
                    f"handoff-*.md` lists this tree's"
                    + (
                        f", `git -C {repo} ls-tree --name-only {ref}:claudedocs`"
                        f" lists {ref}'s" if upstream else ""
                    )
                    + "."
                    if elided else ""
                )
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

    # ---- rule (l): every NEW investigation block declares its date ----------
    # 🔴 FIRST, and against the UPDATE. First because every rule below reads
    # `update_text` and they must all see the text that will be written — a
    # stamp added after the merge would be a line in the committed doc that the
    # diff on screen never showed. Against the UPDATE for rule (k)'s reason,
    # sharpened: `open investigations` APPENDS, so stamping the merge would
    # rewrite blocks past sessions wrote, dating them TODAY — the exact false
    # freshness this rule exists to prevent, manufactured by the rule itself.
    update_text, stamped = stamp_investigations(update_text, _today())
    stamped_advisory = stamped_report(stamped, _today())

    # ---- rule (j): every ranked next-step names a forcing function ----------
    # Read from the UPDATE, so legacy items already in the base are never
    # retroactively refused — see `ranked_items`. An update that brings no
    # `Next steps` section at all touches no ranks and is not asked the question.
    items = ranked_items(update_text)
    unforced = unforced_report(items)
    if unforced:
        print(unforced, file=sys.stderr)
        return EXIT_UNFORCED

    # ---- rule (k): every elimination names how it was eliminated ------------
    # Read from the UPDATE for a reason rule (j) does NOT share and which is
    # sharper here: `open investigations` is an APPEND heading, so the merged
    # doc carries every elimination ever written. Checking the merge would
    # refuse on 151 legacy bullets in this repo alone — permanently red on run
    # one. See `elimination_bullets`. An update with no elimination bullet is
    # not asked the question.
    bullets = elimination_bullets(update_text)
    unevidenced = unevidenced_report(bullets)
    if unevidenced:
        print(unevidenced, file=sys.stderr)
        return EXIT_UNEVIDENCED

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

    # 🔴 THE ARC RULES NEED A THREE-WAY ANSWER, AND TWO-WAY WAS WRONG TWICE.
    # "Is `base_text` empty?" conflates three different documents:
    #
    #   NEW ARC          nothing here, nothing on the mainline, nothing in HEAD.
    #                    Rule (m) may demand a finish line; rule (n) may not
    #                    ratchet (round 1 legitimately opens with its own work).
    #   BASE UNREADABLE  a document exists — on the mainline, or in HEAD, or
    #                    both — and this working copy is not it. NEITHER rule may
    #                    compare against it: every count would be about a
    #                    document nobody is editing.
    #   BASE READABLE    the ordinary case. Both rules apply.
    #
    # Two MEASURED false positives, both found by round 1 of this PR's own audit,
    # both on the middle row that did not exist before:
    #   F1 rule (h)'s stale-base REFUSAL is gated on `--confirm`, so the PROPOSAL
    #      run — the default first half of every `/handoff` — fell straight
    #      through to rule (n), which reported "0 item(s) in the document" about a
    #      mainline doc carrying 3, and printed three remedies none of which could
    #      be carried out ("close one" is impossible at a floor of 0).
    #   F3 `: > claudedocs/handoff-<topic>.md` emptied the working copy of a
    #      TRACKED doc, which read as a new arc: the ratchet switched off, a queue
    #      went 3 -> 5 at exit 0, and rule (m) asserted "This is a NEW handoff
    #      doc" about a file with a full history.
    #
    # So the mainline reading alone is not enough — it is populated only when the
    # mainline is AHEAD on this doc — and HEAD has to be asked too.
    tracked_at_head = doc_tracked_at_head(repo, relpath)
    # 🔴 `None` IS ITS OWN CASE, NOT A QUIET MEMBER OF "a doc may exist".
    # Round 2 measured the cost of folding it in: in a repo with an UNBORN HEAD
    # (a fresh `git init`, or `git checkout --orphan`) `doc_tracked_at_head`
    # answers `None`, so a genuinely new arc was GRANDFATHERED — rule (m)'s
    # refuse arm silently off — and the run printed two statements that were
    # false about the document: that it "was written before rule (m)", and that
    # the ratchet was skipped because "this checkout does not hold a readable
    # copy of the doc … the count you would have been held to is the one in the
    # document you cannot currently read". There is no document, anywhere.
    #
    # Folding is still the right FAIL DIRECTION — an unreadable repo must
    # grandfather rather than refuse — so what changes is that the run SAYS
    # which case it is in instead of borrowing the other one's words.
    git_could_not_answer = tracked_at_head is None
    doc_exists_elsewhere = (
        currency.replaces_mainline_doc(base_text) or tracked_at_head is True
    ) or git_could_not_answer
    # 🔴 `bool(base_text.strip())` ALONE — the `replaces_mainline_doc` conjunct
    # was PROVABLY DEAD and round 2 killed it by mutation. `nothing_to_merge_into`
    # IS `not text.strip()`, and `replaces_mainline_doc` requires it, so the
    # second operand can never change the result. It survived the full suite
    # because no input can distinguish the two readings — the shape this module
    # refuses elsewhere. Keeping it would also keep the false narrative that
    # rule (m)'s fix lives here; the real fix is `is_new_doc` below.
    base_readable = bool(base_text.strip())
    # ⚠ `None` (git could not answer) is folded in with "a document exists", so
    # an unreadable repo GRANDFATHERS a doc rather than refusing one. Round 3
    # corrected this comment twice over: it quoted `tracked_at_head is not
    # False`, an expression the same round deleted, and it called grandfathering
    # "FAIL-CLOSED" ten lines above a comment calling it "grandfather rather
    # than refuse". One name for one direction: this is the PERMISSIVE choice,
    # taken because refusing on a repo we could not read is the worse error.
    is_new_doc = not base_text.strip() and not doc_exists_elsewhere

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
    # `currency` is read once, up at rule (i) — the refusal here and the warning
    # below must use the SAME reading, or a concurrent fetch lets them disagree
    # and the message names numbers the decision did not use.
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

    # 🔴 THE TWO ARC RULES SIT BELOW rule (h), NOT BESIDE (j)/(k) ABOVE, AND THE
    # ORDER IS THE FIX FOR A MEASURED COLLISION. Both read the BASE, and rule (h)
    # is the one that decides whether this checkout's base is the real document
    # at all: a wrong base makes "this arc has no finish line" and "the queue
    # grew" statements about a document nobody is editing. Same precedence
    # argument the comment above rule (h) makes for rule (f) and the bucket line.
    # They still refuse BEFORE the diff is printed, like every other refusal.

    # ---- rule (n): the queue's self-generated half does not grow -------------
    # Counted on the UPDATE (rule (j)'s reason: `Next steps` REPLACES, so the
    # update's items ARE the doc's) against the BASE (which is what "grew" means).
    # An update with no `Next steps` section has 0 items and cannot grow anything.
    #
    # 🔴 THREE SILENT SKIPS, AND EACH IS "I CANNOT MEASURE THIS", NOT "IT PASSED".
    # This rule is the only one here that compares a count on BOTH sides, so it
    # is the only one that can be wrong about the document rather than about the
    # update — and a count presented as 0 when it was never taken is exactly the
    # reassuring zero `claude/RULES.md` says to refuse to print.
    #   (a) git could not answer whether the doc is in HEAD.
    #   (b) `base_readable` — see the classification block above (audit F1/F3).
    #   (c) the base must actually CARRY a canonical `## Next steps`. `ranked_items`
    #       recognises only the heading `is_next_steps_heading` names, which is
    #       deliberate and harmless for rule (j) because that rule reads ONLY the
    #       update. Reading BOTH sides turns the same gap into a false GROWTH:
    #       so MIGRATING such a queue onto the canonical heading, even while
    #       SHRINKING it, was refused, permanently, and only
    #       `--rank-growth-approved` cleared it. Audit F2.
    #       🔴 EVERY POPULATION FIGURE FOR THIS RULE LIVES IN `write-gate.md`
    #       §F AND NOWHERE ELSE — including the ones that retract earlier ones.
    #       A round wrote a figure into both files, retracted it in the skill,
    #       and left it standing here; the round that fixed that then restated
    #       four MORE figures under a banner saying it was not restating them.
    #       A second copy is a second thing to re-measure, whichever direction
    #       it points. Read §F.
    base_carries_a_ranked_queue = NEXT_STEPS_PREFIX in doc_shape(base_text).canonical
    ratchet_skip = ""
    if is_new_doc or args.rank_growth_approved:
        pass  # round 1, or the operator opted in — both are DECISIONS, not gaps
    elif git_could_not_answer and not base_text.strip():
        # The THIRD reason. Distinct from the one below:
        # there may be no document at all, so telling the author to go and read
        # a count "in the document you cannot currently read" would be false.
        ratchet_skip = (
            "git could not say whether this doc exists in HEAD, so whether there "
            "is a document to ratchet against is UNKNOWN"
        )
    elif not base_readable:
        ratchet_skip = "this checkout does not hold a readable copy of the doc"
    elif not base_carries_a_ranked_queue:
        ratchet_skip = (
            "the document's ranked queue is under a heading this tool does not "
            "recognise, so its count could not be taken"
        )
    growth = (
        ""
        if args.rank_growth_approved or ratchet_skip
        else rank_growth_report(self_generated_rank_count(base_text), items, is_new_doc)
    )
    if growth:
        print(growth, file=sys.stderr)
        return EXIT_RANK_GROWTH

    # ---- rule (m): the arc declares what ENDS it ----------------------------
    # Read from the MERGE, not the update — see `closing_condition`. The base is
    # read too, so a document that HAD a finish line and would lose it is told
    # that, rather than being told to add one it can see in its own file.
    #
    # 🔴 THE DELETION ARM NEEDS A READABLE BASE FOR THE SAME REASON rule (n) does:
    # `base_had_one` off an unreadable base is False, which silently downgrades
    # "you are DELETING the finish line" to the grandfathered advisory. The
    # refusal arm is unaffected — `is_new_doc` is now false for a stale or
    # emptied copy, so such a run is grandfathered rather than told it is round 1.
    closing = closing_condition(merged_text)
    undefined_done = undefined_done_report(
        closing,
        # `closing_condition("").is_declared` is already False for every
        # blank base, so a `base_readable and …` conjunct here was dead too
        # (round 2, mutation-proven). One predicate, stated once.
        base_had_one=closing_condition(base_text).is_declared,
        is_new_doc=is_new_doc,
    )
    if undefined_done:
        print(undefined_done, file=sys.stderr)
        return EXIT_UNDEFINED_DONE

    # 🔴 #1648's size-budget warning, MOVED BELOW THE REFUSALS (audit F4). It
    # still sits above the diff, which is where its own tests pin it and where it
    # belongs — it is a fact about the text a human is about to approve. What it
    # must NOT do is arrive on a run that then refuses: it asserts
    # "`test_no_handoff_doc_exceeds_its_budget` WILL GO RED on `main`, and it
    # fails for EVERYONE" and "this is a WARNING, not a refusal", and BOTH are
    # false of a run whose stderr says `NOTHING WRITTEN`. The class is inherited
    # (rule (h)'s stale-base refusal already sat below it), but rules (m) and (n)
    # fire on ORDINARY rounds rather than on a rare stale clone, so this PR is
    # what makes the contradiction common rather than theoretical.
    budget_note = budget_warning(relpath, merged_text, base_text,
                                 gated=gate_enforces_budget(repo))
    if budget_note:
        print(budget_note)
    warning = dropped_durable_report(report.dropped)
    if warning:
        print(warning)
    # Rule (j)'s advisory half, beside the other two and for the same reason:
    # above the diff, because it is a statement about what the diff is adding.
    declared_none = declared_forcing_none_report(items)
    if declared_none:
        print(declared_none)
    # Rule (n)'s SKIP disclosure, beside the advisories and for their reason:
    # it is a statement about what this run did NOT check on the text below.
    skipped = rank_ratchet_skipped_report(items, ratchet_skip) if ratchet_skip else ""
    if skipped:
        print(skipped)
    # Rule (k)'s advisory half, for the identical reason: an elimination the
    # author reasoned rather than measured is a statement about what the diff
    # is adding, so it belongs above the diff and not after it.
    assumed = assumed_report(bullets)
    if assumed:
        print(assumed)
    # Rule (m)'s advisory half — the grandfathered arm. A document written before
    # this rule is not refused (that would be red-by-construction on every legacy
    # handoff), so the ONLY thing that ever surfaces its missing finish line is
    # this line, above the diff, on every update until someone adds one.
    # 🔴 `tracked_at_head is not True`, NOT `git_could_not_answer`. The
    # sentence this gates is a claim about THIS DOC's history, and "absent from
    # HEAD" is no-history just as much as "HEAD unreadable" is. Round 3 measured
    # the commoner case: an ordinary repo with commits, a non-empty handoff doc
    # present but UNTRACKED, no field -> "it was written before rule (m) and is
    # GRANDFATHERED" about a document created seconds earlier. A hand-written
    # doc, a template paste, a `git add` not yet committed, a copy into a fresh
    # worktree — all of them land here, and an unborn HEAD is the RARE one.
    # 🔴 "HAS HISTORY" IS THE PREDICATE, and round 4 caught the previous
    # widening overshooting into its own mirror error. `tracked_at_head is not
    # True` is also true when the doc is absent HERE but present on the MAINLINE
    # — so a run that had just printed "origin/main has 1 commit(s) to this doc
    # that this checkout does not" went on to say its history was unknown. Two
    # adjacent lines of one transcript contradicting each other, and the same
    # sin `legacy_dod_report`'s docstring names: a claim about what git could
    # see, made on a run where git saw it.
    #
    # History is KNOWN when the doc is in HEAD, or when the mainline carries a
    # copy. Everything else — absent from both, or HEAD unreadable — is the
    # honest "this checkout cannot show it predates the rule".
    history_known = tracked_at_head is True or currency.replaces_mainline_doc(base_text)
    legacy_dod = legacy_dod_report(closing, is_new_doc, not history_known)
    if legacy_dod:
        print(legacy_dod)
    # Rule (l)'s advisory. Same slot, and it is the one of the three that names
    # a line the TOOL wrote rather than one the author did — which is exactly
    # why it must be on screen above the diff rather than left to be noticed in
    # it. Computed before the refusals so the text it describes is the text
    # every rule below saw; printed here so a run that refuses does not claim to
    # have stamped a document it never wrote.
    if stamped_advisory:
        print(stamped_advisory)
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
    # 🔴 RESOLVED BEFORE THE WRITE so a repo with no scanner costs nothing but a
    # `Path.is_file()`, and so the refusal below cannot be a surprise about where
    # the scanner was expected to be.
    scanner = find_leak_scanner(repo)
    try:
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(merged_text, encoding="utf-8")
        # ---- rule (o): the repo's own leak scanner reads the delta ----------
        # 🔴 AFTER THE WRITE AND BEFORE THE `git add`, because the scanner reads
        # the WORKING TREE — `tests/leakscan.py` enumerates `--cached --others`
        # and then reads the bytes on disk, so the delta is only visible to it
        # once it is written. Staging first would add nothing and would leave a
        # staged path behind on the refusal, which is the shape
        # `TestBlockedCommitLeavesNoTrace` exists for.
        verdict = leak_gate(
            repo, relpath, scanner, args.leak_pre_existing_approved
        )
        if verdict.refusal:
            print(
                f"{verdict.refusal}{_undo_write(repo, doc, relpath, original)}",
                file=sys.stderr,
            )
            return EXIT_LEAK_REFUSED
        print(verdict.notes)
        git(repo, "add", "--", relpath)
        subject = f"docs(handoff): {args.advanced.strip().splitlines()[0]}"[:100]
        # 🔴 THE TRUNCATION IS ON THE SUBJECT, AND THE TRAILER IS ADDED AFTER IT.
        # `[:100]` bounds the summary line; applying it to the whole message
        # would have cut the trailer off exactly when the summary was longest,
        # i.e. silently and on the busiest commits.
        message = commit_message(subject)
        # Path-limited on purpose: exactly one commit, carrying exactly the
        # diff that was shown, even if the caller had other work staged.
        git(repo, "commit", "-m", message, "--", relpath)
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
