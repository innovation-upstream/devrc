#!/usr/bin/env python3
"""The ROUND-0 ATTRIBUTION RATE — is `unattributed:` falling since the asks block shipped?

    scripts/round0-attribution-rate.py                      # full corpus, default cut
    scripts/round0-attribution-rate.py --since-sha 31033cdb
    scripts/round0-attribution-rate.py --cut-iso 2026-09-27T05:49:10Z   # no git at all

WHY THIS EXISTS
---------------
`claudedocs/handoff-audit-pr-operator-asks.md` closes on a count, and that count
had TWO defects as a CHECK. Both were measured on 2026-09-27, and this script is
the fix for both — the doc's `closing-condition` now names this command.

**Defect 1 — the baseline was not reproducible.** The doc quoted *1,649 round-0
ledger lines across 728 sessions in 7 repos*. Two defensible methods over the
same corpus (`~/.claude/projects/**/*.jsonl`) disagree, and NEITHER produces it:
counting only ASSISTANT-authored text blocks gives 493 lines in 236 sessions
(474 transcript FILES — see blind spot 6; an earlier version of this sentence
called the file count a session count, which is the same defect one level up) / 7
projects (re-measured 508 / 488 / 8 hours later the same day, the corpus being
live), counting every record of any role gives 1,977 / 707 / 8. A number quoted
without its method has no defined left-hand side, so this script STATES its
method in its own output: one round-0 ledger line in ONE assistant-authored text
block is one report, and nothing else is.

🔴 **Defect 2 — an absolute corpus-wide count can only GROW, so it can never be
"lower".** The old condition compared a post-ship count against a pre-ship
count over the SAME cumulative corpus: every pre-fix report stays in it forever
and each new one adds to it, so the left-hand side is monotonically increasing
and the condition is unfalsifiable in the direction it wanted. The evaluable
statistic is a RATE over the reports in each bucket — mean `unattributed` per
report, and `unattributed` as a share of `requirements` — which is what this
prints. **Do not reintroduce a bare count as the comparator.** A count here is
a denominator's size, never a result.

THE UNIT, AND THE PROVENANCE SEPARATION THAT MAKES IT A UNIT
------------------------------------------------------------
🔴 A NAIVE WALK OVER THE CORPUS COUNTS SKILL LOADS AND BRIEFS AS REPORTS. The
`/audit-pr` skill body and the dispatched brief are INJECTED into transcripts —
as `tool_result` content (a Read/Skill result) and as user-role text (the
dispatch prompt) — so the very sentence a report is recognised by also appears
in the material that ASKED for the report. `scripts/audit-rule-firing-sweep.py`
measured that shape on the same corpus: one skill sentence matched 1,022 files
while only the assistant-authored subset was the rule being applied. The
separation is PROVENANCE, read off the record rather than guessed from the text:

    assistant-role `text` block   -> the model REPORTING          = SIGNAL
    `tool_result` block           -> the skill body or the brief   = noise
    user-role text                -> the dispatch prompt           = noise
    `thinking` block              -> not the report                = noise

Measured consequence on this host, 2026-09-27: 508 assistant-authored ledger
lines against 1,068 occurrences across all provenances — so a walk without this
separation over-counts by ~2.1x, and the over-count is not noise but the
instrument reading its own input back.

THE IN-POPULATION TEST IS LOCAL AND STRUCTURAL — NO `gh`, NO NETWORK
--------------------------------------------------------------------
🔴 THE POPULATION IS "THE OPERATOR'S WORDS ACTUALLY REACHED THE AUDITOR" — NOT
"THE PR'S COMMITS CARRY A TRAILER". That second reading was inherited verbatim
from `#1887`'s closing condition and it is the WRONG FACT, which `#1901 round 0`
proved by measurement rather than argument. `render()` emits
`session transcript: N session(s) NAMED BY <Claude-Session-Id:> trailers`
whenever trailers named a session — guarded by `if session_ids:` alone — and
emits `! session transcript: UNKNOWN — …` beside it when nothing could be read
off those sessions. A rule that reads the first line as "answered" therefore
scores a report whose own block says

    🔴 **NO OPERATOR ASK COULD BE READ FOR THIS PR.**

as IN-population: zero asks reached that auditor, and it lands in the very
bucket the closing condition's left-hand side is computed over.
`operator_asks.py`'s own comment warns about this equivocation in terms —
"NAMED BY", not "resolved", because *named by a trailer* and *readable on this
host* are separate facts.

So the roles are separated, and only one of them is evidence of READING:

    selected       `session transcript: N session(s) NAMED BY …`
                   trailers SELECTED sessions. Says nothing about whether their
                   words arrived.
    answered       `### from the session transcript`
                   `_render_asks` emits this heading only when at least one Ask
                   whose source IS the session transcript was rendered into the
                   brief — i.e. the operator's words are IN the auditor's hands.
                   This is the only line that proves reading.
    unmeasured     `! session transcript: UNKNOWN — …`  a source consulted that
                   did not answer.
    informational  `session transcript: dropped N record(s) — …`  the classifier
                   dropped records; says nothing either way.

🔴 THREE DISPOSITIONS, NEVER TWO:

    in-population      an asks block rendered at least one ask FROM A SESSION
                       TRANSCRIPT. The words arrived.
    out-of-population  a READING that the words did not arrive: the source was
                       consulted and did not answer (`unmeasured`); or sessions
                       were named and nothing from them was rendered
                       (`selected` with no `answered`); or the only asks came
                       from another source (a PR comment); or there is no asks
                       block at all, i.e. a pre-fix report.
    UNKNOWN            THIS INSTRUMENT could not read the block — no Sources
                       block, or a Sources block with no session-transcript line
                       at all.

🔴 WHY `named-but-unreadable` IS `out` AND NOT `UNKNOWN`, decided deliberately.
UNKNOWN here means MY blindness, not the auditor's. A block that says its
session source did not answer is a successful READING of a real outcome: those
words did not reach that auditor, so the report is not a member of a population
defined by their arrival. `audit-dispatch.py` records that a missing transcript
is the ORDINARY case — the operator runs two hosts — so routing it to UNKNOWN
would put most of the corpus in a bucket that reports nothing and would make the
instrument's own blindness the modal answer. ⚠ This does NOT weaken the
handoff's rule that "a source that could not be read is not an absence of asks":
that rule governs what the AUDITOR must do (never raise a deletion candidate on
the grounds that nobody asked). It is about the auditor's inference; this is
about population membership. Two different claims, and conflating them is how
the first version of this file scored the negative case as `in`.

⚠ A session that dispatched SEVERAL audits carries several asks blocks, and a
ledger line cannot be tied to one of them by text alone. Precedence is
`answered` > any reading of non-arrival > `UNKNOWN`, i.e. one block whose asks
arrived makes that session's reports in-population. That is a stated choice, not
a measurement.

WHY THE RENDERER'S STRINGS ARE PINNED TWO-WAY
---------------------------------------------
🔴 THE WHOLE IN-POPULATION TEST IS A MATCH AGAINST STRINGS `scripts/lib/
operator_asks.py` EMITS. A one-sided matcher is the dangerous shape here: reword
that module and every report silently reclassifies as out-of-population, the
denominator goes to zero, and the run prints a clean NOT MEASURABLE that looks
exactly like today's honest answer. So `ANCHORS` below is a LEDGER, checked
against what `render()` actually emits on every run (exit 5 on disagreement) and
pinned TWO-WAY by `scripts/tests/test_round0_attribution_rate.py` — failing when
the emitted set GROWS as well as when it shrinks. `claude/RULES.md`: a guard on
WORDS is walkable by REWORDING, so each anchor is the narrowest stable fragment
that can carry its meaning and names the spelling it depends on.

🔴 AND THE PIN IS BEHAVIOURAL AT EVERY POLE, not just structural. A ledger of
strings cannot catch a wrong PRECEDENCE — the first version of this file had
every anchor correct and still scored the negative case as in-population — so
`check_behavioural_poles` classifies EVERY pole in `BEHAVIOURAL_POLES` on each
run — five today, one per branch of the `BRANCHES` precedence ledger — and refuses
unless each comes out as its ledger says, AND unless every branch is the one that
DECIDES some pole. ⚠ The count is not fixed: read `BEHAVIOURAL_POLES`, never a
number in prose. This paragraph said "two" after the set had grown to four, which
is the same count-in-prose defect `WalkFacts` below records.
The role set that can produce `in` is DERIVED from `BRANCHES` and is NOT a second
ledger — `IN_POPULATION_ROLES` is built at `:591-592`. So the edit that re-admits
`selected` is flipping its `BRANCHES` disposition to `"in"`, which is visible and
fails a test; ⚠ do not go hunting a literal tuple to change, because there is
none, and restoring one would reinstate the two-sources-of-truth defect the
derivation removed. This sentence claimed a "one-line ledger" three lines after
the paragraph above self-corrected a stale count — the same count-in-prose defect,
twice in one docstring. A NEW ask SOURCE added to
`operator_asks` also fails the pin, because every `### from the …` heading a live
render emits must match an anchor — nobody gets to decide by accident whether a
new source counts as the words arriving.

BLIND SPOTS, each at the width it actually holds
------------------------------------------------
1. **Claude Code only.** An `/audit-pr` run under opencode writes no
   `~/.claude/projects` transcript, so its reports are outside this corpus and
   are not counted in EITHER bucket. This is not a rate over all audits.
2. **ONE host only.** This reads THIS machine's disk. An absence here is not an
   absence in the fleet: the laptop's transcripts are not visible, and a report
   written there is missing from both buckets.
3. **A pruned transcript removes its reports from both buckets**, so the
   denominators are what is still on disk, not what was ever written.
4. **The pre-cut bucket is a MIXTURE, not a controlled baseline.** It spans
   every revision of the `/audit-pr` skill, every model, and every repo, so a
   difference between buckets is a difference between two uncontrolled
   populations. The verdict is a comparison of rates and never a causal claim
   about the asks block.
5. **The unit is a LEDGER LINE, not an audit.** A verbose round 0 that restates
   its ledger twice is two reports; a round 0 that omits the ledger line is
   none. The line is the only machine-readable handle the skill emits.
6. **THE UNIT OF CLASSIFICATION IS THE TRANSCRIPT FILE; THE UNIT OF THE FLOOR IS
   THE REAL SESSION.** Saying so is the fix for a sentence that used to call the
   first one "the session". A report and the brief it was written against coexist
   in ONE file, so that is what `disposition_of` reads — which means one audit CAN
   score `in` in the auditor subagent's transcript and `out` in the parent's, and
   neither reading is wrong about its own file. The floor instead counts distinct
   session ids derived from the path (`_session_id_of`), because a round-0 report
   is written by a subagent whose transcript is
   `<project>/<sid>/subagents/agent-*.jsonl` and several auditors of one session
   are several files again.
   🔴 **TWO DIFFERENT RATIOS, AND AN EARLIER VERSION OF THIS BULLET CONFLATED
   THEM INTO ONE "~2x".** They are not the same measurement:
     * the CORPUS ratio — every transcript on this host — is **~6.9x**
       (`_session_id_of` over the whole corpus, 2026-09-28T17:05Z: 6,739 files /
       973 sessions, 5,766 under `subagents/`);
     * the PRE-BUCKET ratio — files that carry a round-0 report — is **~2.0x**
       (474 files / 236 sessions), because a file with no report is not counted.
   🔴 EVERY FIGURE IN THAT FIRST BULLET DRIFTS AND IS STAMPED FOR THAT REASON —
   treat it exactly like the tables in the handoff marked "re-run, never quote".
   Four measurements across three days gave 6,717/973, 6,736/973, 6,739/973 — and
   a `974` this file published once that has not reproduced since. Read the
   method and the stamp, or re-run it; do not carry the literal.
   ⚠ AND AN INVARIANT THIS BULLET ASSERTED IS FALSIFIED, removed rather than
   repaired: it said "session ids cannot fall while files rise" as the reason the
   wrong figure was caught. A pruned transcript falsifies it outright, and the
   measurements above show sessions flat while files rose by 22. The sound claim
   is the NARROWER one — the same method cannot give both 1,563 and 973 — and
   that is what caught it.
   What does NOT drift is the mechanism, and it is the thing worth remembering: a
   "first path segment" derivation that does not strip `.jsonl` counts a session
   with both a top-level transcript and a `subagents/` directory TWICE (measured
   at the same instant: naive 1,570 against 973). That is exactly how a wrong
   `1,563` entered this file, and
   `test_the_naive_session_derivation_double_counts_and_ours_does_not` pins the
   MECHANISM against a fixture — the durable artifact here — so a future wrong
   number fails instead of reading fine. 🔴 Re-derive with the code's own
   function; do not trust a figure here that a reader cannot reproduce — this arc
   has shipped three of those.
   ⚠ Within a file, a session that dispatched one answered audit and one
   unanswered one has all of its reports counted in-population (the precedence
   note above), and one verbose round 0 emitting two ledger lines is two reports
   and ONE observation — measured on `#1901`'s own round 0.
7. **A PRE-CUT block written under an OLDER SPELLING is not read as such, on
   purpose** — a HISTORICAL spelling cannot be pinned against a live render, and
   an unpinnable anchor is the one shape this design refuses. MEASURED on this
   host: two blocks from the `#1887` development session (2026-09-26) say
   `N session(s) resolved from …` where round 1 of that ladder later wrote
   `NAMED BY`; that line is `selected` now, so its absence changes nothing about
   arrival. ⚠ **And the PRE-cut in-population row is therefore NOT structurally
   zero** — the same session rendered blocks whose asks DID arrive, from the
   feature's own branch before the squash. The run's own note says which case
   holds; an earlier version of this bullet and that row's label both asserted
   the zero, and the next measurement printed 2.
8. **The in-population test is a property of the BRIEF, never of the PR's
   commits — and the ERROR DIRECTION IS EXCLUSION, which is now true of the code
   and was not.** It reads what the asks block emitted; it never asks GitHub. So
   a PR whose commits DO carry trailers but whose transcript could not be read on
   the host that ran the audit is **out-of-population** here, while the prose the
   condition inherited from `#1887` ("PRs whose commits carry a
   `Claude-Session-Id:` trailer") would count it in. That is the intended
   direction: the population is the words ARRIVING, and a report whose asks block
   says they did not arrive is not evidence about the feature working.
   ⚠ **This bullet previously claimed exactly that behaviour while the code did
   the opposite** — the trailers-exist-but-unreadable case emitted `NAMED BY`,
   scored `answered`, and landed IN-population, so the described direction was
   unreachable. `#1901 round 0` found it. A doc naming the safe direction over
   code taking the unsafe one is worse than silence: it stops the next reader
   looking.

SELF-CHECKS — no verdict prints unless both pass
------------------------------------------------
* POSITIVE control: the walk must find a non-zero number of assistant-authored
  ledger lines somewhere in the corpus. Zero there cannot be distinguished from
  a walk wired to nothing, so it exits 4 rather than printing a reassuring `0`.
  (Measured 508 on this host, so a real run finds plenty.)
* NEGATIVE control: a sentinel that must appear NOWHERE. 🔴 It is COMPUTED from
  a seed, never written as a literal — the corpus IS `~/.claude/projects`, so
  any session that READS a file containing the literal writes it into a
  transcript and the control then fails permanently for everyone.
  `scripts/audit-rule-firing-sweep.py` records that incident: four transcripts
  carried its old literal and it had been refusing to print for longer than
  anyone had noticed.

🔴 THIS REPO IS PUBLIC AND THE CORPUS IS TRANSCRIPTS. Output is counts, rates and
ledger-line SHAPES only — no message bodies, no prompts, no quoted transcript
prose, and no transcript paths. ⚠ There is deliberately NO sample-quoting flag:
the first version carried `--samples`, whose own docstring had to say its output
must never be committed or posted. A flag whose only product comes with a
handling prohibition, and which nothing in the tree consumed, is one the deletion
pass removes rather than documents (`#1901 round 0`). The same pass removed
`--json`, which had zero consumers: the bucketing is a pure function
(`bucket_reports`) that tests call directly, which is a better seam than a file.

Env:
  ROUND0_RATE_CORPUS        corpus root (default `~/.claude/projects`)
  ROUND0_RATE_OPERATOR_ASKS path to the `operator_asks.py` the anchors are
                            checked against. Exists so the exit-5 pin can be
                            watched RED against a copy — a gate nothing can be
                            aimed at a broken input is a gate nobody has seen
                            fail.

Exit codes: 0 a verdict printed · 2 bad invocation — an unresolvable
`--since-sha`, an unparseable `--cut-iso`, or an unknown flag · 3 a self-control
failed · 4 nothing walked: a corpus path that is not a directory, or no
assistant-authored ledger line anywhere · 5 the pinned renderer strings disagree
with `operator_asks.py`, or a live render's POLE classifies wrongly · 6 NOT
MEASURABLE. 🔴 6 has TWO reasons and the message says which: the post-cut
in-population SESSION count is below `MIN_SESSIONS`, or the pre-cut bucket is
empty so the comparison has no left-hand side. They share one code because they
demand the same action — collect more observations, change nothing.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib"

# `scripts/lib` is not a package and this file sits in `scripts/`, so the path
# insert is how every sibling reaches these modules (`audit-dispatch.py` and
# `find-session.py` do the same thing on the same line). 🔴 AT MODULE LEVEL AND
# RESOLVED FROM `__file__`, NOT FROM THE CWD — both halves matter. The verify
# recipe in `handoff-audit-pr-operator-asks.md` calls this script by ABSOLUTE
# PATH from wherever the reader is standing, and an import buried inside a
# function only ever worked because `load_operator_asks()` happened to run
# first: an ordering side effect nobody had declared, invisible until a caller
# used the walk on its own.
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# A HARD import. ⚠ The first version wrapped this in `try/except ImportError`
# with a narrower fallback rule "for a partial deploy or a `cp -a` of one file" —
# deleted by `#1901 round 0`, because no mechanism produces that state: nothing in
# `nix/` deploys this script (`git grep round0-attribution -- nix/` is empty), it
# is run by absolute path from inside the repo, so `scripts/lib` is always beside
# it. The branch was `# pragma: no cover` by its own admission, i.e. a second,
# untested classification rule guarding against nothing.
import transcript_search  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONTROL = 3
EXIT_NOTHING_WALKED = 4
EXIT_PIN = 5
EXIT_NOT_MEASURABLE = 6

CORPUS_ENV = "ROUND0_RATE_CORPUS"
OPERATOR_ASKS_ENV = "ROUND0_RATE_OPERATOR_ASKS"

#: The commit the asks block shipped in (`#1887`, squash-merged). A PARAMETER
#: and not a literal in the logic: `--since-sha` moves the cut, and `--cut-iso`
#: bypasses git entirely so the bucketing is testable with no history.
DEFAULT_SINCE_SHA = "31033cdb"

#: 🔴 THE REFUSAL FLOOR, AND IT ENCODES A STANDING OPERATOR INSTRUCTION — *do
#: not re-tune the feature off n<10* (`handoff-audit-pr-operator-asks.md`, NEXT
#: #1). Below this many post-cut in-population SESSIONS the run prints its
#: numbers and then refuses to compare, so the instrument cannot be used to
#: justify a change the data cannot support.
#:
#: 🔴 DISTINCT SESSIONS, NOT REPORTS — renamed from `MIN_REPORTS` because a name
#: that says "reports" while the honest unit is observations is the same
#: label-wider-than-implementation defect this file exists to avoid. MEASURED on
#: `#1901`'s own round 0: it produced two in-population reports and BOTH came
#: from one session, so a report-counting floor let one verbose audit supply 20%
#: of it. The VERDICT line prints both numbers so nobody reads n=10 as ten
#: independent observations.
MIN_SESSIONS = 10

#: The round-0 ledger line the `/audit-pr` skill tells the auditor to emit
#: (`round 0 · requirements: N (unattributed: U)`). Only the two numbers are
#: read; the prose around them is that skill's to change.
LEDGER_RE = re.compile(r"requirements:\s*(\d+)\s*\(unattributed:\s*(\d+)\)")

#: ---------------------------------------------------------------------------
#: THE RENDERER ANCHOR LEDGER — pinned two-way (see the docstring).
#:
#: `text` is matched against DECODED transcript text, never raw JSON, so a
#: non-ASCII character is safe here (a writer using `ensure_ascii` stores `—` as
#: `—` in the file, and `json.loads` gives it back).
#: ---------------------------------------------------------------------------

#: Block presence. Depends on the spelling of `operator_asks.HEADING` up to the
#: em-dash clause, which is where that constant states its own subject; the
#: trailing "— attribution input for step 1" is deliberately NOT required, so a
#: reword of the explanatory half does not blind the instrument.
ANCHOR_BLOCK = "## THE OPERATOR'S OWN ASKS"

#: The Sources block. Depends on `render()`'s literal `**Sources read:**` line
#: AND on its two-space indent for every source line under it — both are read
#: back out of a live render by the two-way pin.
ANCHOR_SOURCES = "**Sources read:**"
SOURCE_LINE_INDENT = "  "

#: 🔴 A LINE START, OPTIONALLY BEHIND A `cat -n` LINE NUMBER — and this shape has
#: now been wrong in BOTH directions, so read the whole comment before narrowing
#: it again. I am the third writer of this justification.
#:
#: (1) A PLAIN SUBSTRING test is too wide: `operator_asks.py`'s own source
#:     carries `HEADING = "## THE OPERATOR'S OWN ASKS …"` and
#:     `lines += ["", "**Sources read:**"]`, so any session that merely READ that
#:     file scored as one that received a block.
#: (2) A BARE `^` ANCHOR is too narrow, and it was blind to the DOMINANT
#:     delivery path. `audit-dispatch.py`'s output is redirected to a scratchpad
#:     `.md` and the dispatch prompt tells the auditor to READ it, so the brief
#:     reaches the auditor as a Read `tool_result` in `cat -n` form: every line
#:     prefixed `<spaces><n>\t`. `^## THE OPERATOR'S OWN ASKS` cannot match that.
#:     MEASURED by `#1901 round 1` (blind) and reproduced: 13 of 20 post-cut
#:     reports were scored `out` with the reason "no asks block in this session
#:     (a pre-fix report)" while their numbered copy demonstrably carries
#:     `**Sources read:**`, and at least one carries
#:     `### from the session transcript`, i.e. was genuinely IN-population. The
#:     printed reason was affirmatively false, the contemporaneous control row
#:     was contaminated with sessions that DID receive a block, and `post_in` was
#:     deflated with `MIN_SESSIONS` unreachable by the dominant path.
#:
#: So the prefix is OPTIONAL and the two classes stay separated: a numbered real
#: render matches, and a numbered SOURCE READ still does not, because after the
#: line number comes `HEADING = "` or `lines += [`, not the heading itself. Both
#: poles are pinned by tests, in the numbered form as well as the plain one.
#:
#: ⚠ AND THE COMMENT THAT STOOD HERE IS RETRACTED, not patched. It read: "51
#: blocks contain the heading substring, of which only 12 carry it at the start
#: of a line, and every one of the other 39 is a source read or a discussion of
#: this feature." The last clause was FALSE: round 1 date-bucketed 20
#: line-numbered REAL renders in that residual, ≥11 of them predating the day it
#: was measured. The re-measurement is in `#1901`'s PR body and in the handoff;
#: what belongs here is the rule, not a census that goes stale in a day.
_LINENO_PREFIX = r"(?:[ \t]*\d+\t)?"

#: One place that prefix is removed from a line, so every per-line test below
#: (indent, block end) sees the same text the renderer emitted.
_LINENO_RE = re.compile(r"^[ \t]*\d+\t")


def strip_lineno(line: str) -> str:
    """A `cat -n` line number removed, if present. Otherwise unchanged."""
    return _LINENO_RE.sub("", line, count=1)


ANCHOR_BLOCK_RE = re.compile("^" + _LINENO_PREFIX + re.escape(ANCHOR_BLOCK), re.M)

#: 🔴 THE SAFETY NET'S pattern, deliberately WIDER than the matcher and narrower
#: than a substring: the heading at a line start modulo a `cat -n` number,
#: indentation, and markdown quote/bullet markers. A text that matches this but
#: NOT `ANCHOR_BLOCK_RE` is a block delivered in a form this instrument cannot
#: parse — MY blindness — and routes to UNKNOWN rather than to a false "no asks
#: block (a pre-fix report)". It still excludes the false-positive class
#: line-anchoring was added for: in `operator_asks.py`'s own source the heading
#: sits after `HEADING = "`, which is neither whitespace nor a quote marker, so a
#: source READ matches neither pattern.
_BLOCKLIKE_BLOCK_RE = re.compile(
    "^" + _LINENO_PREFIX + r"[ \t>*+-]*" + re.escape(ANCHOR_BLOCK), re.M)
ANCHOR_SOURCES_RE = re.compile("^" + _LINENO_PREFIX + re.escape(ANCHOR_SOURCES),
                               re.M)

#: The per-source ask headings `_render_asks` emits at column 0, above the
#: Sources block. These are the only lines that prove asks were READ — so this
#: pattern carries the same optional prefix, or the `answered` role would be
#: unreachable on exactly the delivery path that matters.
ASK_HEADING_RE = re.compile("^" + _LINENO_PREFIX + r"### from the .*$", re.M)

#: Every line of a live asks block this instrument reads, by the fragment that
#: identifies it, with the ROLE it carries for the in-population test.
#:
#: `where` says which region of the block the line lives in:
#:   `asks`    the rendered asks themselves (a `### from the <source>` heading)
#:   `sources` an indented line under `**Sources read:**`
#:
#: 🔴 TWO-WAY, AND THE REACH IS EXACTLY THIS — the sentence that stood here
#: claimed more than the code did, twice, both measured INERT by `#1901 round 1`,
#: on a file that exists to prevent that class of defect. It now describes what
#: `check_pins` does:
#:   GROWS   EVERY source line the probe matrix emits is matched, not only the
#:           session-transcript ones (the old call sites filtered through
#:           `session_source_lines` first, so an uncovered `PR comment:` line gave
#:           rc 4 with zero GROWS), and every `### from the <source>` heading is
#:           matched, with the probes DERIVED FROM `oa.SOURCE_ORDER` so a source
#:           added to `operator_asks` really does reach a probe (hand-built Asks
#:           meant no mutation of that module could trip the pin).
#:   SHRINKS an anchor that matches nothing a live render emits.
#: ⚠ CLASSIFICATION still reads session-transcript lines ONLY — that is narrower
#: than the pin on purpose, and the two must not be confused: the pin asks "do I
#: still understand this renderer", the classifier asks "did the words arrive".
#: `text` is matched against DECODED transcript text, so a non-ASCII character is
#: safe here.
#:
#: 🔴 `selected` IS NOT `answered`, AND THAT DISTINCTION IS THE WHOLE FIX. The
#: first version gave `session(s) NAMED BY` the `answered` role, so a block
#: saying "NO OPERATOR ASK COULD BE READ FOR THIS PR" — which emits that line
#: whenever trailers named a session, beside `! … UNKNOWN` — scored
#: in-population. Trailers naming a session is SELECTION; it is not evidence that
#: a single word arrived. Found by `#1901 round 0`.
ANCHORS = (
    dict(id="asks-read-session", where="asks", role="answered",
         text="### from the session transcript",
         spelling="`_render_asks`'s `### from the {source}` heading, with "
                  "`operator_asks.SOURCE_SESSION`. Emitted ONLY when at least "
                  "one Ask whose source is the session transcript was rendered, "
                  "which is exactly 'the operator's words reached the auditor'. "
                  "Depends on the `### from the ` prefix and on that source "
                  "label; both are read back out of a live render by the pin."),
    dict(id="asks-read-pr-comment", where="asks", role="other-source",
         text="### from the PR comment",
         spelling="the same heading for `operator_asks.SOURCE_PR_COMMENT`. In "
                  "the ledger so that ADDING A SOURCE to `operator_asks` fails "
                  "the GROWS direction instead of silently deciding, by "
                  "accident, whether that source counts as the words arriving. "
                  "PR-comment asks do NOT make a report in-population — the "
                  "route the fix shipped is the session transcript, and that "
                  "arc's own open investigation records agent-posted comments "
                  "being misattributed to the operator."),
    dict(id="selected", where="sources", role="selected",
         text="session(s) NAMED BY",
         spelling="`render()`'s trailer line — 'N session(s) NAMED BY "
                  "`Claude-Session-Id:` trailers'. NAMED BY was itself a round-1 "
                  "correction of 'resolved' for this exact reason, so the words "
                  "are load-bearing and the count/label tail is not matched."),
    dict(id="unmeasured", where="sources", role="unmeasured",
         text=": UNKNOWN — ",
         spelling="`render()`'s `! {source}: UNKNOWN — {reason}` line for an "
                  "`Unmeasured` source. The em-dash separator is part of it; the "
                  "reason text is not matched at all."),
    dict(id="dropped", where="sources", role="informational",
         text=": dropped ",
         spelling="`render()`'s `{source}: dropped N record(s) — {reason}` line. "
                  "INFORMATIONAL: the classifier dropped records, which says "
                  "nothing either way — in the ledger so GROWS stays meaningful."),
    # The PR-comment source lines. They carry NO role for the in-population test
    # (the classifier never sees them — it filters to session-transcript lines);
    # they are here so that GROWS covers every source line the renderer emits,
    # which is what the header above now claims and previously did not.
    dict(id="comments-examined", where="sources", role="informational-other",
         text="comment(s) examined",
         spelling="`render()`'s `{PR comment}: N comment(s) examined, M from the "
                  "operator` line, printed even at ZERO so 'consulted and empty' "
                  "is distinguishable from 'not consulted'."),
    dict(id="review-comment-caveat", where="sources", role="informational-other",
         text="ISSUE comments ONLY",
         spelling="`render()`'s `! {PR comment}: gh pr view --json comments "
                  "returns ISSUE comments ONLY …` caveat line."),
    # 🔴 FOUND BY THE WIDENED GROWS CHECK ITSELF, on its first run: this line
    # names no source, so the session-filtered call site never saw it, and a
    # block carrying it fell through to UNKNOWN. It is not blindness — the block
    # states plainly that nothing was consulted — so it is a reading of
    # non-arrival. `scope="block"` because it is about the whole assembly rather
    # than about one source, and the classifier therefore reads it even though it
    # is not a session-transcript line.
    # 🔴 FOUND BY `#1901 round 2`: `render()` emits this and NO probe did, so the
    # widened GROWS was still blind on one axis — the asks side is derived from
    # `SOURCE_ORDER`, but the sources side is a hand-enumerated kwarg list. A
    # probe now passes `comment_skips`, and
    # `test_every_render_KWARG_is_exercised_by_a_probe` makes the axis DERIVABLE:
    # a new keyword on `render()` fails the suite until a probe drives it.
    dict(id="comment-skipped", where="sources", role="informational-other",
         text=" skipped — ",
         spelling="`render()`'s `{PR comment}: N skipped — {why}` line, emitted "
                  "for each reason a comment was not counted as the operator's. "
                  "`audit-dispatch.py` passes `comment_skips`, so it is live."),
    dict(id="no-source-consulted", where="sources", role="none-consulted",
         scope="block", text="no source was consulted at all",
         spelling="`render()`'s `! no source was consulted at all — that is a "
                  "fact about this assembly, not about the operator.` line, "
                  "emitted when there are no session ids, no unmeasured sources "
                  "and no asks."),
)

#: 🔴 THE LEDGER OF WHAT MAKES A REPORT IN-POPULATION, DERIVED from `BRANCHES`
#: below rather than spelled twice. It was a hand-written tuple until `#1901
#: round 3` turned the precedence into data: two constants saying the same thing
#: is the one-rule-two-places hazard, and the version that is not consulted by
#: `disposition_of` is the one that goes quietly stale. Re-admitting `selected`
#: here is now impossible without editing `BRANCHES`, which
#: `test_only_the_answered_role_can_make_a_report_in_population` pins.

#: 🔴 THE PRECEDENCE, AS DATA IN ONE ORDERED LEDGER — not as an `if`/`elif` chain,
#: and the change is what makes the branches TESTABLE rather than merely present.
#: `#1901 round 3` found the `selected` branch STILL unguarded after round 2's fix:
#: two mutants (deleting it, and `and False` with the string left in place) both
#: survived a green 58-test suite, and the guard written to prevent exactly that
#: reported it as covered. It missed because it compared the union of roles
#: PRESENT in each pole against the branch list, while `unmeasured` precedes
#: `selected` — so no pole ever EXECUTED the selected branch. That is the
#: unreachable-guard shape: a mutation test passes when an earlier check always
#: wins.
#:
#: As a ledger, three things follow mechanically: the order IS the precedence and
#: is visible; `deciding_role` returns the branch actually TAKEN, so a test can
#: assert the branch rather than the roles; and a branch cannot be re-spelled out
#: of a regex-derived list, because nothing derives it from source text any more.
BRANCHES: dict = {
    "answered": ("in", "an ask from a session transcript was rendered into the "
                       "brief — the operator's words reached the auditor"),
    "unmeasured": ("out", "the asks block says its session-transcript source was "
                          "consulted and could not be read, so no ask arrived"),
    "selected": ("out", "trailers NAMED a session but no ask from it was "
                        "rendered — selection is not arrival"),
    "other-source": ("out", "the only asks came from another source (a PR "
                            "comment), not from a session transcript"),
    "none-consulted": ("out", "the asks block says no source was consulted at "
                              "all — a fact about that assembly, and no ask "
                              "arrived either way"),
}


#: Derived — see the comment above `BRANCHES`.
IN_POPULATION_ROLES = tuple(r for r, (disp, _why) in BRANCHES.items()
                            if disp == "in")


def deciding_role(roles):
    """The FIRST branch of `BRANCHES` these roles satisfy, or None.

    One definition of the precedence, read by `disposition_of` AND by the tests —
    so a test can assert which branch DECIDED a disposition instead of which
    roles happened to be present. Round 3's finding is precisely that those two
    are different, and that only the first one is coverage.
    """
    for role in BRANCHES:
        if role in roles:
            return role
    return None

#: 🔴 COMPUTED FROM A SEED, NEVER WRITTEN DOWN. The corpus is
#: `~/.claude/projects/**/*.jsonl`, so a sentinel spelled as a literal poisons
#: its own control: any session that READS this file writes the string into a
#: transcript, the negative control counts a hit, and the run exits 3 with no
#: verdict — permanently, and increasingly with every session that investigates
#: why. That has already happened once in this tree, to
#: `scripts/audit-rule-firing-sweep.py`; its comment carries the measurement.
#: Reading this source gives you the SEED and not the string.
#: Hex with no separators, because the value is used both as a substring probe
#: here and as literal corpus text by
#: `test_the_negative_control_refuses_when_the_sentinel_is_planted`.
#: ⚠ Still poisonable by PRINTING the computed value into a transcript. Rotate
#: by changing the seed.
_NEG_SEED = b"round0-attribution-rate negative control v1 2026-09-27"
NEG_CONTROL = "r0" + hashlib.sha256(_NEG_SEED).hexdigest()[:24]

#: Raw-line prefilter. 🔴 The corpus is 7.9 GB / 6,695 files on this host, so
#: `json.loads` on every line is not affordable; these substrings are ASCII and
#: need no JSON escaping, so a raw-line test is sound. The sentinel is in the
#: set deliberately — a prefilter that skipped it would make the negative
#: control a claim about the prefilter instead of about the corpus.
_PREFILTER = ("unattributed", "OWN ASKS", NEG_CONTROL)


# --------------------------------------------------------------------------- #
def fail(code: int, msg: str, err=None) -> NoReturn:
    """Refuse, with the reason on stderr.

    🔴 `sys.stderr` IS RESOLVED AT CALL TIME, NOT BOUND AS A DEFAULT. Bound at
    def time it holds the interpreter's original stream, so every refusal
    bypasses a caller that replaced `sys.stderr` — which made five guards for
    these refusals read the empty string and fail while the refusal itself was
    correct. A test that cannot see the message it asserts on is not a test of
    the message.
    """
    print(f"\nREFUSED: {msg}", file=err or sys.stderr)
    sys.exit(code)


def parse_ts(raw: str):
    """ISO-8601 -> aware datetime, or None.

    🔴 NOT a string compare. Transcript stamps end in `Z` while `git log %cI`
    carries a numeric offset, so comparing the two as strings is lexicographic
    nonsense and would mis-bucket every report near the cut.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def corpus_root(explicit=None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(os.environ.get(CORPUS_ENV) or (Path.home() / ".claude" / "projects"))


def load_operator_asks():
    """The module whose emitted strings the anchors are checked against.

    Loaded BY PATH: `scripts/lib` is not importable as a package and
    `operator_asks` imports three siblings from it. `OPERATOR_ASKS_ENV` points
    the check at a COPY so the exit-5 pin can be watched fail.

    🔴 AN UNIMPORTABLE RENDERER IS A PIN FAILURE, NOT A PASS. If this module
    cannot be loaded, nothing verified the strings the whole in-population test
    depends on, and proceeding would report a confident zero.
    """
    path = Path(os.environ.get(OPERATOR_ASKS_ENV) or (LIB / "operator_asks.py"))
    if not path.exists():
        fail(EXIT_PIN, f"the renderer under test does not exist: {path}")
    # `scripts/lib` is already on `sys.path` from the module-level insert above —
    # which is what lets a COPY under `/tmp` (the env door) import the three
    # siblings `operator_asks` needs.
    spec = importlib.util.spec_from_file_location("_r0rate_operator_asks", path)
    if not spec or not spec.loader:
        fail(EXIT_PIN, f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    # 🔴 REGISTERED BEFORE `exec_module`, and that is not tidiness: `@dataclass`
    # resolves annotations through `sys.modules[cls.__module__]`, so a module
    # holding a dataclass — `operator_asks.Ask` does — raises
    # `AttributeError: 'NoneType' object has no attribute '__dict__'` when
    # executed unregistered. The failure names dataclasses, not the loader.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:                                  # pragma: no cover
        fail(EXIT_PIN, f"{path} could not be imported "
                       f"({e.__class__.__name__}: {e}) — the anchors are "
                       "therefore UNVERIFIED, which is not a pass")
    return mod


#: The probes whose CLASSIFICATION is asserted on every run — one per branch of
#: `BRANCHES`, and read from here rather than counted in prose — with the
#: disposition each must produce. 🔴 THIS IS THE GUARD A STRING LEDGER CANNOT BE:
#: every anchor was already correct when the negative pole scored `in`, because
#: what was wrong was the PRECEDENCE. Both poles are live `render()` output.
BEHAVIOURAL_POLES = (
    # Sessions NAMED BY trailers, nothing readable, zero asks — the real shape of
    # `#1901`'s finding, and the one that must NOT be in-population.
    ("named-but-unreadable", "out"),
    # An ask actually rendered from a session transcript — the shape a live
    # `audit-dispatch.py 1901 --round 0` emits (`### from the session transcript`
    # above `**Sources read:**`, `1 session(s) NAMED BY`, no `!` on that source).
    ("answered", "in"),
    # 🔴 ADDED AFTER `#1901 round 2`: these two branches had NO runtime check, and
    # two mutants against the first of them survived a fully green suite. A pole
    # is verified on EVERY invocation, so the check does not depend on anyone
    # having written a test — which is the gap that let them live.
    ("no-source", "out"),
    ("pr-comment-only", "out"),
    # 🔴 ADDED AFTER `#1901 round 3`, which found the `selected` branch STILL
    # unguarded — two mutants survived — because every existing pole that CARRIES
    # `selected` also carries `unmeasured`, which precedes it, so no pole ever
    # executed that branch. The probe below is the selected-ONLY shape: trailers
    # named a session, the transcript WAS readable (hence no `Unmeasured`), and no
    # ask matched. `roles == {"selected"}` exactly.
    ("selected-only", "out"),
)


def renderer_probes(oa, projects_root) -> dict:
    """Name -> a live `render()` output, one per block shape this reads.

    The matrix, not one happy case: each entry makes ONE line shape appear, so
    the two-way pin can require that every emitted shape is covered and every
    anchor is reachable — and the `BEHAVIOURAL_POLES` subset is classified
    end-to-end, one probe per `BRANCHES` entry.

    `projects_root` is pointed at a directory that does not exist on purpose —
    `agent_side_reference` globs it, and the probe must not walk the real
    7.9 GB corpus (nor depend on what is in it).
    """
    sid = "a" * 8
    ask = oa.Ask(source=oa.SOURCE_SESSION, text="an ask", session_id=sid)
    comment_ask = oa.Ask(source=oa.SOURCE_PR_COMMENT, text="an ask", who="someone")
    # 🔴 DERIVED FROM `oa.SOURCE_ORDER`, NOT FROM TWO HAND-NAMED CONSTANTS, and
    # that is what makes the ANCHORS header's claim about a NEW SOURCE true. It
    # was FALSE while the probes were hand-built: `_render_asks` iterates
    # SOURCE_ORDER, so a third source added to `operator_asks` could never appear
    # in any probe, no mutation of that module could trip the pin (measured: rc 4,
    # not rc 5), and such a source would have classified silently as UNKNOWN.
    # `#1901 round 1` found the overclaim.
    every_source = [oa.Ask(source=s, text="an ask",
                           session_id=sid if s == oa.SOURCE_SESSION else "",
                           who="" if s == oa.SOURCE_SESSION else "someone")
                    for s in oa.SOURCE_ORDER]
    unmeasured = oa.Unmeasured(
        source=oa.SOURCE_SESSION,
        reason="those session ids resolved to no readable transcript on this host")
    kw = dict(projects_root=projects_root)
    return {
        # an ask FROM a session transcript was rendered -> the words arrived
        "answered": oa.render([ask], session_ids=(sid,), comments_examined=0, **kw),
        # EVERY source `operator_asks` declares, so adding one fails the pin
        "every-source": oa.render(every_source, session_ids=(sid,),
                                  comments_examined=1, **kw),
        # 🔴 THE NEGATIVE POLE: trailers named a session AND nothing could be read.
        # `render()` emits BOTH the NAMED BY line and the `! … UNKNOWN` line here.
        "named-but-unreadable": oa.render([], session_ids=(sid,),
                                         unmeasured=(unmeasured,),
                                         comments_examined=0, **kw),
        # 🔴 SELECTED-ONLY: trailers named a session, the transcript WAS readable
        # (so there is no `Unmeasured`), and no ask matched. The only shape whose
        # deciding branch is `selected` — every other probe carrying that role
        # also carries `unmeasured`, which precedes it, which is why two mutants
        # against that branch survived until `#1901 round 3`.
        "selected-only": oa.render([], session_ids=(sid,), comments_examined=0,
                                   **kw),
        # consulted and did not answer, with no trailers at all
        "unmeasured": oa.render([], unmeasured=(unmeasured,),
                                comments_examined=0, **kw),
        # the classifier dropped records -> an INFORMATIONAL session line
        "dropped": oa.render([ask], session_ids=(sid,),
                             dropped={"a reason": 3}, comments_examined=0, **kw),
        # asks arrived, but from the OTHER source — and this probe also drives
        # `comment_skips`, whose source line had NO probe until `#1901 round 2`
        # (see `comment-skipped`), and `dropped` — which already had one in the
        # `dropped` probe, so it is driven here for locality, not for coverage.
        # ⚠ An earlier wording called them "the two kwargs whose source lines had
        # no probe at all", which was false for `dropped`.
        "pr-comment-only": oa.render(
            [comment_ask], comments_examined=1,
            comment_skips={"written by someone else": 2},
            dropped={"a reason": 1}, **kw),
        # no source consulted at all -> a block with no session line
        "no-source": oa.render([], **kw),
    }


def block_regions(text: str) -> tuple[list[str], list[str], bool]:
    """-> (ask-heading lines, source lines, a Sources block was found).

    Two regions of the FIRST asks block in `text`, split at `**Sources read:**`:
    the ask headings ABOVE it (which prove reading) and the indented source lines
    BELOW it (which describe what was consulted). Structural, not positional.

    The third value distinguishes "no Sources block at all" — which is THIS
    instrument failing to read the block — from "a Sources block with nothing in
    it about the session transcript". Those are different findings and the first
    version could not tell them apart.
    """
    head = ANCHOR_BLOCK_RE.search(text)
    if not head:
        return [], [], False
    mark = ANCHOR_SOURCES_RE.search(text, head.start())
    if not mark:
        return ASK_HEADING_RE.findall(text[head.start():]), [], False
    asks = ASK_HEADING_RE.findall(text[head.start():mark.start()])
    sources: list[str] = []
    for raw in text[mark.start():].splitlines()[1:]:
        # 🔴 THE LINE NUMBER COMES OFF FIRST, or every per-line test below is
        # testing `cat -n`'s formatting instead of the renderer's. A numbered
        # source line reads `   128\t  session transcript: …`: unstripped it
        # fails the indent test, the loop breaks at the FIRST source line, and
        # the block reads as having no sources at all.
        line = strip_lineno(raw)
        if not line.strip():
            continue
        if not line.startswith(SOURCE_LINE_INDENT):
            break                       # in a real render, the `**Ledger:**` line
        sources.append(line)
    return asks, sources, True


def session_source_lines(session_label: str, lines) -> list[str]:
    """The subset of source lines that are about the SESSION-TRANSCRIPT source."""
    needle = f"{session_label}:"
    return [l for l in lines if needle in l]


#: An anchor's `scope`, defaulting to `session`:
#:   `session` counted only when it appears on a SESSION-TRANSCRIPT source line
#:   `block`   counted wherever it appears, because it is about the whole
#:             assembly rather than about one source
_DEFAULT_SCOPE = "session"


def match_anchors(lines, where: str, scopes=None) -> tuple[set, set, list]:
    """-> (roles matched, anchor ids matched, lines matching NO anchor).

    `where` selects the region's anchors (`asks` or `sources`), so a heading
    cannot be scored by a source-line anchor or the reverse. `scopes` narrows to
    anchors of those scopes; None means all of them, which is what the PIN uses
    — the classifier is narrower (see `roles_of_block`).

    The unmatched list is the GROWS half of the two-way pin (a line shape the
    ledger does not cover); the matched ids are the SHRINKS half (an anchor
    nothing emits any more).
    """
    roles: set = set()
    ids: set = set()
    unmatched: list[str] = []
    for line in lines:
        hit = [a for a in ANCHORS
               if a["where"] == where
               and (scopes is None or a.get("scope", _DEFAULT_SCOPE) in scopes)
               and a["text"] in line]
        if not hit:
            unmatched.append(line)
            continue
        for a in hit:
            roles.add(a["role"])
            ids.add(a["id"])
    return roles, ids, unmatched


def roles_of_block(text: str, session_label: str) -> tuple[set, bool, list]:
    """-> (roles this block carries, sources-block-found, unmatched lines).

    One place where a block's text becomes roles, shared by `disposition_of` and
    by the pin — so the pin's behavioural poles exercise the SAME path the corpus
    walk does, not a parallel re-implementation of it.

    ⚠ THE UNMATCHED LIST IS NARROWER THAN ITS NAME, and saying so is the point:
    it carries unmatched ASK headings and unmatched SESSION-TRANSCRIPT source
    lines only. A source line of ANOTHER source that no anchor covers is not
    reported here, because classification reads session-scoped anchors off session
    lines plus the block-scoped ones. `check_pins` is the wide reader
    (`scopes=None`, every source line) and is where GROWS is enforced; this return
    value is not a GROWS signal. (`#1901 round 2` noted the mismatch.)
    """
    asks, sources, found = block_regions(text)
    ask_roles, _ask_ids, ask_bad = match_anchors(asks, "asks")
    # Session-scoped anchors are read ONLY off session-transcript lines (a
    # `PR comment: … UNKNOWN` line must not say the transcript was unreadable);
    # block-scoped ones are read off any source line, because they are about the
    # whole assembly.
    src_roles, _src_ids, src_bad = match_anchors(
        session_source_lines(session_label, sources), "sources", {"session"})
    block_roles, _b_ids, _b_bad = match_anchors(sources, "sources", {"block"})
    return ask_roles | src_roles | block_roles, found, ask_bad + src_bad


def check_pins(oa, probes) -> None:
    """Refuse (exit 5) unless the anchors still describe what `render()` emits.

    Both DIRECTIONS (shrinks/grows) on every run — this is the STRUCTURAL half;
    `check_behavioural_poles` is the behavioural one:
      SHRINKS  an anchor that matches nothing a live render emits — the shape
               this instrument is blind to after a reword, and the one that
               silently empties the denominator.
      GROWS    ANY line of a rendered block the ledger does not cover — an ask
               heading OR a source line of ANY source, not only the
               session-transcript ones — i.e. a new shape whose meaning for the
               in-population test nobody has decided. ⚠ This sentence said
               "a session-source line" after the check had already been widened,
               which is the wording that invites the next maintainer to restore
               the filter; `#1901 round 2` found it.
    """
    if not oa.HEADING.startswith(ANCHOR_BLOCK):
        fail(EXIT_PIN,
             f"ANCHOR_BLOCK {ANCHOR_BLOCK!r} does not START operator_asks."
             f"HEADING {oa.HEADING!r} — the asks block can no longer be "
             "recognised at all, so every report would classify as "
             "out-of-population")
    seen_ids: set = set()
    unmatched: list[str] = []
    for name, text in probes.items():
        if not ANCHOR_BLOCK_RE.search(text):
            fail(EXIT_PIN, f"the {name!r} render does not put "
                           f"{ANCHOR_BLOCK!r} at the START of a line — "
                           "line-anchoring is what separates a real block from "
                           "a source read of operator_asks.py")
        if not ANCHOR_SOURCES_RE.search(text):
            fail(EXIT_PIN, f"the {name!r} render carries no line-initial "
                           f"{ANCHOR_SOURCES!r} — the Sources block cannot be "
                           "located")
        asks, sources, _found = block_regions(text)
        # 🔴 EVERY source line, not just the session-transcript ones. The old
        # call site filtered through `session_source_lines` first, so the header's
        # GROWS claim was INERT for any other source: an uncovered `PR comment:`
        # line gave rc 4 with zero GROWS (`#1901 round 1` measured it).
        # Classification stays narrower on purpose — see `roles_of_block`.
        ask_roles, ask_ids, ask_bad = match_anchors(asks, "asks")
        _src_roles, src_ids, src_bad = match_anchors(sources, "sources")
        seen_ids |= ask_ids | src_ids
        unmatched += ask_bad + src_bad
    missing = sorted({a["id"] for a in ANCHORS} - seen_ids)
    # 🔴 BOTH HALVES IN ONE REFUSAL, and that is not cosmetic: a REWORD trips
    # both at once — the old spelling matches nothing (SHRINKS) and the new line
    # is covered by nothing (GROWS) — so exiting on the first half printed a
    # diagnosis naming only one direction and reading like a NEW source line
    # rather than a renamed one. Measured on the two mutants in
    # `scripts/tests/test_round0_attribution_rate.py`.
    if not unmatched and not missing:
        return
    parts = []
    if missing:
        parts.append(
            "SHRINKS — these anchors match nothing operator_asks.render() "
            f"emits, so the in-population test is matching text that no longer "
            f"exists: {', '.join(missing)}")
    if unmatched:
        parts.append(
            # 🔴 "LINE(S) OF A RENDERED BLOCK", not "session-transcript source
            # line(s)". `#1901 round 2` triggered this refusal live and it named
            # a class the offending item did not belong to — the item was
            # `['### from the an invented source']`, an ASK HEADING — so a
            # maintainer debugging it looks in the wrong place.
            "GROWS — render() emits line(s) of the asks block that no anchor "
            "covers (an ask heading, or a source line of any source), so this "
            "instrument does not know what they mean for the in-population "
            f"test: {unmatched[:3]}")
    if missing and unmatched:
        parts.append("BOTH halves failed together, which is the signature of a "
                     "REWORD rather than of a new source line: update ANCHORS "
                     "in the same commit that reworded the renderer.")
    fail(EXIT_PIN, "the pinned renderer strings disagree with operator_asks.py. "
                   + "  ".join(parts))


def check_behavioural_poles(oa, probes) -> None:
    """Refuse (exit 5) unless EVERY pole still classifies the way it must, AND
    every branch of `BRANCHES` is the one that DECIDES some pole.

    🔴 THE GUARD A STRING LEDGER CANNOT BE, and the reason this function exists
    separately: when `#1901 round 0` found the negative case scoring
    in-population, every anchor in the ledger was CORRECT — the defect was the
    precedence between them. A structural pin cannot see that. So live `render()`
    outputs are classified end to end, through the same `disposition_of` the
    corpus walk uses, and a disagreement refuses the run.

    🔴 AND THE SECOND HALF IS `#1901 round 3`'s FINDING: it is not enough that a
    branch's ROLE appears somewhere among the poles. `selected` appeared — and no
    pole ever EXECUTED that branch, because `unmeasured` precedes it in every
    probe carrying both, so two mutants against it survived a green suite. The
    reachability check below asks which branch `deciding_role` actually TAKES, and
    refuses if any branch is decided by none of the poles. A branch no input can
    reach is not guarded by anything.
    """
    if not set(IN_POPULATION_ROLES) <= {a["role"] for a in ANCHORS}:
        fail(EXIT_PIN,
             f"IN_POPULATION_ROLES {IN_POPULATION_ROLES} names a role no anchor "
             "carries, so nothing could ever be in-population")
    decided: dict = {}
    for name, expected in BEHAVIOURAL_POLES:
        got, why = disposition_of([probes[name]], oa.SOURCE_SESSION)
        if got != expected:
            fail(EXIT_PIN,
                 f"the {name!r} pole of a LIVE render classifies as {got!r}, "
                 f"expected {expected!r} ({why}). This is the precedence defect "
                 "`#1901 round 0` found: `session(s) NAMED BY` is SELECTION, "
                 "never evidence that the operator's words arrived, so a block "
                 "saying no ask could be read must not be in-population.")
        role = deciding_role(roles_of_block(probes[name], oa.SOURCE_SESSION)[0])
        if role is not None:
            decided.setdefault(role, name)
    unreached = [r for r in BRANCHES if r not in decided]
    if unreached:
        fail(EXIT_PIN,
             "no pole's disposition is DECIDED by these branch(es), so nothing "
             f"exercises them and a mutation of them would survive: {unreached}. "
             "Add a pole whose roles reach the branch — not one that merely "
             "CARRIES the role, since an earlier branch may win (`#1901 round 3`)."
             f" Branches decided today: {decided}")


# --------------------------------------------------------------------------- #
@dataclass
class Report:
    project: str
    ts: str
    dt: object
    requirements: int
    unattributed: int
    #: The transcript FILE this report was read from — NEVER printed. The unit of
    #: CLASSIFICATION (a file is where a report and its own brief coexist).
    file: str = ""
    #: 🔴 THE REAL SESSION, which is NOT the file. A round-0 report is written by
    #: an auditor SUBAGENT, whose transcript is `<project>/<sid>/subagents/
    #: agent-*.jsonl` — a different file from the parent's, and several auditor
    #: subagents of one session are several files again. The PRE bucket was 474
    #: FILES but 236 real sessions (~2.0x), so a file-counting floor was
    #: satisfiable by about half the observations it claimed; corpus-wide the ratio
    #: is ~6.9x. Both numbers drift — blind spot 6 carries them with their date and
    #: the derivation that produces them. This is the unit the FLOOR counts.
    session_id: str = ""
    disposition: str = ""
    why: str = ""


def blocks_of(path: Path):
    """Yield (provenance, text, timestamp) for every text-bearing block.

    Provenance is read off the record, never guessed from the text — the
    separation the module docstring measures. `tool_use` blocks are skipped
    entirely: a Write/Edit tool call carries the file's content and is neither a
    report nor a brief.

    ⚠ NOT `transcript_search.load_records`: this applies a raw-line prefilter
    first (see `_PREFILTER`) and must see raw lines for the negative control, so
    it is a different contract rather than a second copy of the same one.
    """
    try:
        fh = path.open(errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            if not any(p in line for p in _PREFILTER):
                continue
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            ts = rec.get("timestamp") or ""
            content = msg.get("content")
            blocks = content if isinstance(content, list) else (
                [{"type": "text", "text": content}] if isinstance(content, str)
                else [])
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    txt = b.get("text") or ""
                    prov = "assistant" if role == "assistant" else "injected"
                elif bt == "tool_result":
                    cc = b.get("content")
                    txt = cc if isinstance(cc, str) else json.dumps(cc)
                    prov = "injected"
                elif bt == "thinking":
                    txt = b.get("thinking") or ""
                    prov = "thinking"
                else:
                    continue
                if txt:
                    yield prov, txt, ts


def disposition_of(texts, session_label: str) -> tuple[str, str]:
    """-> (disposition, why) for one session, from every asks block in it.

    `texts` is every block's text — the brief arrives INJECTED, so the
    provenance rule that defines a report deliberately does not apply here.

    🔴 THE ORDER OF THESE BRANCHES IS THE FIX FOR `#1901 round 0`'s 🔴, and each
    one names what it READ rather than what it assumed. Only `answered` — an
    `### from the session transcript` heading, i.e. an ask actually rendered from
    a transcript — makes a report in-population. `selected` (`session(s) NAMED
    BY`) is evidence that trailers picked a session, and `render()` emits it
    beside `! … UNKNOWN` in exactly the case where nothing could be read; scoring
    it as `answered` put reports whose own block says "NO OPERATOR ASK COULD BE
    READ FOR THIS PR" into the closing condition's left-hand side.

    Every non-arrival branch returns `out` rather than UNKNOWN because each is a
    successful READING of non-arrival. UNKNOWN is reserved for THIS instrument's
    blindness — see the module docstring for why that boundary is drawn there and
    why it does not weaken the handoff's "an unreadable source is not an absence
    of asks" rule (that rule is about the AUDITOR's inference, not membership).
    """
    roles: set = set()
    no_sources_block = 0
    blocks = 0
    block_like = 0
    for txt in texts:
        if not ANCHOR_BLOCK_RE.search(txt):
            # 🔴 THE SAFETY NET FOR A DELIVERY FORM I CANNOT PARSE, and it exists
            # because the bare `^` anchor missed the `cat -n` form and reported
            # "no asks block (a pre-fix report)" — an affirmatively FALSE reason
            # — for 13 of 20 post-cut reports. A block-like text the anchored
            # matcher cannot locate is THIS instrument's blindness, so it routes
            # to UNKNOWN rather than quietly inflating `out`.
            #
            # ⚠ ITS REACH IS EXACTLY ITS PATTERN, and an earlier version of this
            # comment said "any future transformation", which is wider than the
            # code. VERIFIED shape by shape: column-0 ✓, `cat -n` (`<n>\t`) ✓,
            # indented ✓, markdown-quoted (`> `) ✓ — and `grep -n`'s `<n>:` form
            # ✗, matched by NEITHER pattern.
            # MEASURED 2026-09-28 rather than assumed, because the first version of
            # this note asserted an impact of zero that was NOT what the corpus
            # said. RE-MEASURED 2026-09-28T17:05Z, because the first numbers I
            # wrote were arithmetically impossible (16 files holding 15 texts —
            # files cannot exceed texts): **15 texts in 14 files** carry the
            # heading behind a `<n>:` prefix and are matched by neither pattern
            # (17 in 16 before the anchored-match exclusion, which is the pair I
            # had mixed them with). **2** of those files also emit a round-0
            # ledger line, and
            # in BOTH the file ALSO carries an anchored block, so the disposition
            # is decided by that block and no report changes. Impact today is zero
            # by that path, not by the absence of the shape. Left unwidened
            # deliberately — a colon-delimited prefix would admit
            # `HEADING: "## …"`-shaped prose — and named here so the next reader
            # knows it is a KNOWN gap rather than a covered one.
            if _BLOCKLIKE_BLOCK_RE.search(txt):
                block_like += 1
            continue
        blocks += 1
        got, found, _bad = roles_of_block(txt, session_label)
        roles |= got
        if not found:
            no_sources_block += 1
    if not blocks:
        if block_like:
            return "UNKNOWN", (
                f"{block_like} text(s) carry the asks-block heading but not in a "
                "form this instrument can locate (a transformed delivery?) — "
                "that is MY blindness, not a pre-fix report")
        return "out", "no asks block in this session (a pre-fix report)"
    role = deciding_role(roles)
    if role is not None:
        disp, why = BRANCHES[role]
        return disp, why
    return "UNKNOWN", (
        "an asks block is present but this instrument could not read it: "
        f"{no_sources_block} of {blocks} carry no Sources block, and none names "
        "a session-transcript source — unreadable HERE, which is not a zero")


@dataclass
class WalkFacts:
    """What the walk saw, beside the reports themselves.

    A dataclass rather than a dict because the COUNT fields and the Counter have
    different types: a `dict(...)` of mixed value types type-checks as a union
    and every `+= 1` on it is unverifiable. (This said "two of these fields are
    COUNTS" while there were three — a count in prose is a claim, so it is now
    a description that cannot go stale.)
    """
    files: int = 0
    #: Ledger lines seen in an INJECTED block — the skill body or the brief
    #: reading itself back. Counted, not ignored: it is the over-count the
    #: provenance rule prevents, and the report prints it.
    injected_ledger_lines: int = 0
    sentinel_hits: int = 0
    dispositions: collections.Counter = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.dispositions is None:
            self.dispositions = collections.Counter()


def walk(corpus: Path, session_label: str,
         project_of=None) -> tuple[list, WalkFacts]:
    """-> (reports, walk facts). One pass; nothing is printed from here.

    `session_label` is `operator_asks.SOURCE_SESSION`, PASSED IN rather than
    re-spelled here — it is one of the pinned strings, so there is exactly one
    place it can be wrong.
    """
    if not corpus.is_dir():
        fail(EXIT_NOTHING_WALKED, f"corpus {corpus} is not a directory")
    project_of = project_of or (lambda p: _project_of(p, corpus))
    files = sorted(corpus.rglob("*.jsonl"))
    reports: list[Report] = []
    facts = WalkFacts(files=len(files))
    for path in files:
        proj = project_of(path)
        texts, found = [], []
        for prov, txt, ts in blocks_of(path):
            texts.append(txt)
            if NEG_CONTROL in txt:
                facts.sentinel_hits += 1
            hits = LEDGER_RE.findall(txt)
            if not hits:
                continue
            if prov != "assistant":
                facts.injected_ledger_lines += len(hits)
                continue
            for req, un in hits:
                found.append(Report(project=proj, ts=ts, dt=parse_ts(ts),
                                    requirements=int(req), unattributed=int(un),
                                    file=str(path),
                                    session_id=_session_id_of(path, corpus)))
        if not found:
            continue
        disp, why = disposition_of(texts, session_label)
        for r in found:
            r.disposition, r.why = disp, why
            facts.dispositions[disp] += 1
        reports += found
    return reports, facts


# --------------------------------------------------------------------------- #
def stats(rows) -> dict:
    n = len(rows)
    req = sum(r.requirements for r in rows)
    un = sum(r.unattributed for r in rows)
    return dict(n=n, requirements=req, unattributed=un,
                mean_unattributed=(un / n) if n else None,
                unattributed_share=(un / req) if req else None,
                # BOTH, because they DIFFER and only one is an observation count:
                # `files` is what a naive count gives, `sessions` is the real unit
                # (see `Report.session_id`). The ratio is a property of the rows,
                # so it is printed per bucket rather than asserted here.
                files=len({r.file for r in rows}),
                sessions=len({r.session_id for r in rows}))


def _fmt(v, nd=3) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


#: 🔴 THE COMMITTER DATE (`%cI`), NEVER THE AUTHOR DATE — AND THAT IS A FIXED
#: DEFECT, NOT A PREFERENCE. The condition this instrument serves is "reports
#: recorded AFTER `31033cdb`", i.e. after the change LANDED on `main`. For a
#: GitHub squash merge those are two different facts: the squash carries the PR
#: author's own date as `%aI` while `%cI` is the moment GitHub created the
#: commit on `main`. Reading `%aI` therefore classifies every report written in
#: that window as POST-cut while the fix was not yet on `main` and not yet
#: deployed — contaminating the post-cut bucket with PRE-fix reports, which is
#: precisely the misattribution this instrument exists to measure.
#:
#: MEASURED AT TWO POINTS on `origin/main`, because one is not a general claim:
#:   `31033cdb`  author == committer to the second (2026-09-27T00:49:10-05:00),
#:               so today's numbers do NOT move — which is exactly why the bug
#:               was invisible.
#:   `daa6fd65`  author 2026-08-22T13:20:35-05:00, committer 14:03:11-05:00 —
#:               **+2,556 s (42.6 min)** apart. Over the 1,200 newest `main`
#:               commits, 10 diverge and that is the widest.
#: `--since-sha` takes ANY sha, so the coincidence at one commit is not a
#: property of the tool.
CUT_CLOCK = "%cI"
CUT_CLOCK_LABEL = "committer date — the moment a GitHub squash landed on main"


def resolve_cut(sha: str, repo: Path) -> str:
    """The cut instant for `sha`, read off `CUT_CLOCK`."""
    out = subprocess.run(["git", "-C", str(repo), "log", "-1",
                          f"--format={CUT_CLOCK}", sha],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        fail(EXIT_USAGE,
             f"cannot resolve --since-sha {sha} in {repo} — the cut is what "
             "defines both buckets, so a run without it would report one "
             f"undivided population. git said: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def bucket_reports(reports, cut) -> dict:
    """Split reports into the buckets the report prints. A PURE function.

    The seam tests use instead of a `--json` file: `#1901 round 0`'s deletion
    pass removed that flag (zero consumers), and a function returning lists is a
    better seam than a file anyway — it cannot be stale and needs no IO.

    🔴 UNKNOWN is in NO denominator, in either bucket, and `undated` is in
    neither bucket at all.
    """
    undated = [r for r in reports if r.dt is None]
    dated = [r for r in reports if r.dt is not None]
    pre = [r for r in dated if r.dt < cut]
    post = [r for r in dated if r.dt >= cut]
    return dict(
        pre=pre, post=post, undated=undated,
        pre_in=[r for r in pre if r.disposition == "in"],
        post_in=[r for r in post if r.disposition == "in"],
        # F3's contemporaneous control: same skill revisions, models and repos as
        # `post_in`, differing only in whether the asks arrived.
        post_out=[r for r in post if r.disposition == "out"],
        post_unknown=[r for r in post if r.disposition == "UNKNOWN"],
    )


def render_report(cut_iso, cut_src, corpus, facts, b) -> tuple[str, int]:
    """The whole report, and the exit code it implies."""
    o: list[str] = []
    pre, pre_in = b["pre"], b["pre_in"]
    post, post_in, post_out = b["post"], b["post_in"], b["post_out"]
    post_unknown, undated = b["post_unknown"], b["undated"]
    o.append("round0-attribution-rate — run "
             f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    o.append(f"corpus {corpus}   files walked {facts.files}")
    o.append(f"cut {cut_iso}   ({cut_src})")
    o.append("unit: ONE round-0 ledger line in ONE assistant-authored text "
             "block. Injected copies (skill body, brief) are NOT reports: "
             f"{facts.injected_ledger_lines} such occurrence(s) were seen "
             "and excluded.")

    o.append("\nCONTROLS")
    pos = len(pre) + len(post) + len(undated)
    o.append(f"  POSITIVE (assistant-authored ledger lines anywhere): {pos}"
             f"   {'ok' if pos else 'FAILED'}")
    o.append(f"  NEGATIVE (computed sentinel, must be 0):             "
             f"{facts.sentinel_hits}   "
             f"{'ok' if not facts.sentinel_hits else 'FAILED'}")
    if not pos:
        return "\n".join(o), EXIT_NOTHING_WALKED
    if facts.sentinel_hits:
        return "\n".join(o), EXIT_CONTROL

    o.append("\nRATES — a RATE, never a count. A corpus-wide count only grows; "
             "see the docstring's defect 2.")
    o.append(f"{'bucket':38} {'reports':>7} {'sess':>5} {'files':>6} "
             f"{'reqs':>6} {'unattr':>7} {'mean/report':>11} {'share':>7}")
    # 🔴 THE PRE-CUT IN-POPULATION ROW'S NOTE IS COMPUTED, NOT ASSERTED — and that
    # is a correction of my own claim. It was labelled `[struct. 0]` with the words
    # "cannot be non-zero: no asks block existed pre-cut", and the very next
    # measurement printed 2 there: the feature's OWN development session rendered
    # real asks blocks from its branch before the squash landed. A static label
    # that the same run contradicts is the "comment is a claim too" defect, so the
    # note now describes whichever case actually holds.
    pre_in_note = (
        "0 as expected: the asks block did not exist before the cut"
        if not pre_in else
        f"NON-ZERO ({len(pre_in)} report(s) in "
        f"{len({r.session_id for r in pre_in})} session(s)) — the block was "
        "rendered from the feature's own BRANCH before the squash landed, i.e. "
        "pre-merge development use. Not a pre-fix measurement, and not the "
        "comparator either way")
    rows_to_print = [
        ("PRE-cut (all)", pre, ""),
        ("PRE-cut in-population [see note]", pre_in, ""),
        ("POST-cut (all)", post, ""),
        # F3's control, printed BESIDE the comparator: same skill revisions,
        # models and repo mix as `post_in`, differing only in whether the asks
        # arrived. The PRE-cut baseline is an UNSELECTED population; this one is
        # contemporaneous, so the two together bound the selection effect.
        ("POST-cut out-of-population [control]", post_out, ""),
        ("POST-cut in-population", post_in, ""),
    ]
    for label, rows, _ in rows_to_print:
        s = stats(rows)
        o.append(f"{label:38} {s['n']:7d} {s['sessions']:5d} {s['files']:6d} "
                 f"{s['requirements']:6d} {s['unattributed']:7d} "
                 f"{_fmt(s['mean_unattributed']):>11} "
                 f"{_fmt(s['unattributed_share']):>7}")
    # 🔴 THE RATIO IS COMPUTED FROM THE ROWS, not asserted from memory: an
    # earlier line said "~2x here" while conflating the PRE-bucket ratio with the
    # corpus-wide one (~6.9x), and a prose ratio is a number nobody re-derives.
    o.append("  `sess` is the REAL session count and the only observation count "
             "— several auditor subagents of one session are several FILES. "
             "files/sess by bucket: "
             + "  ".join(
                 f"{lbl}={(stats(rows)['files'] / stats(rows)['sessions']):.2f}x"
                 for lbl, rows in (("PRE", pre), ("POST", post))
                 if stats(rows)["sessions"]))
    o.append(f"  PRE-cut in-population: {pre_in_note}.")
    o.append("  [control] is the CONTEMPORANEOUS comparison — same skill "
             "revisions, models and repos as the in-population row, differing "
             "only in whether the operator's words arrived. The PRE-cut "
             "baseline is an unselected population; read both.")
    o.append(f"projects represented: PRE={len({r.project for r in pre})}  "
             f"POST={len({r.project for r in post})}")
    o.append("\nDISPOSITIONS (of every report, both buckets): "
             + "  ".join(f"{k}={v}" for k, v in
                         sorted(facts.dispositions.items())))
    reasons = collections.Counter(r.why for r in post)
    for why, n_why in reasons.most_common():
        # The classification is auditable from the output: every post-cut report
        # says WHY it landed where it did, in the words `disposition_of` used.
        o.append(f"  post-cut · {n_why:4d} × {why}")
    o.append(f"  UNKNOWN post-cut: {len(post_unknown)} — 🔴 NOT folded into "
             "in-population or out-of-population, and NOT in any denominator "
             "above. An unreadable source is a different finding from a zero.")
    o.append(f"  undated (no `timestamp`): {len(undated)} — reported, folded "
             "into NOTHING, so neither bucket claims them.")

    o.append("\nVERDICT")
    n_sessions = len({r.session_id for r in post_in})
    n_reports = len(post_in)
    n_files = len({r.file for r in post_in})
    unit = (f"n={n_sessions} distinct session(s) / {n_files} transcript file(s) "
            f"/ {n_reports} report(s) — the floor counts SESSIONS, because one "
            "verbose round 0 emits several ledger lines, and one session's "
            "auditor subagents write several FILES, while all of it is one "
            "observation")
    if n_sessions < MIN_SESSIONS:
        o.append(f"  VERDICT: NOT MEASURABLE ({unit}; floor {MIN_SESSIONS})")
        o.append("  The post-cut in-population bucket is too small to compare. "
                 "🔴 This is a standing operator instruction, not a formatting "
                 "choice: do NOT re-tune the feature off n<"
                 f"{MIN_SESSIONS}. Numbers above are printed so the population "
                 "can be watched grow; they are not a result.")
        return "\n".join(o), EXIT_NOT_MEASURABLE
    base = stats(pre)
    if base["n"] == 0 or base["mean_unattributed"] is None:
        o.append("  VERDICT: NOT MEASURABLE (the pre-cut bucket is empty, so "
                 "the comparison has no left-hand side)")
        return "\n".join(o), EXIT_NOT_MEASURABLE
    now = stats(post_in)
    if now["mean_unattributed"] is None:
        # Unreachable while the MIN_SESSIONS refusal above stands — which is
        # exactly why it is here: a mutation sweep that removed that refusal
        # killed this path with a `TypeError`, i.e. the guard's absence was
        # reported as a crash rather than as a refusal. A comparison with no
        # right-hand side is NOT MEASURABLE, in every route to it.
        o.append("  VERDICT: NOT MEASURABLE (the post-cut in-population bucket "
                 "has no reports, so there is nothing to compare)")
        return "\n".join(o), EXIT_NOT_MEASURABLE
    # 🔴 AN UNDEFINED SHARE IS NOT ZERO, and `or 0` made it one. `stats()`
    # returns None for the share whenever a bucket's summed `requirements` is 0 —
    # reachable whenever a round 0 legitimately reports zero requirements — and
    # the old `(x or 0)` then printed a FABRICATED delta ("share — vs 0.750,
    # Δ-0.750") inside the one line the closing condition is read from, plus the
    # words "LOWER on both statistics" over one statistic that does not exist.
    # The mean was guarded twice and the share not at all. `#1901 round 1`.
    if now["unattributed_share"] is None or base["unattributed_share"] is None:
        undefined = [name for name, s in (("post-cut in-population", now),
                                          ("pre-cut", base))
                     if s["unattributed_share"] is None]
        o.append("  VERDICT: NOT MEASURABLE (the `unattributed` SHARE is "
                 f"UNDEFINED for {', '.join(undefined)} — that bucket's summed "
                 "`requirements` is 0, so the share has no denominator. The "
                 "condition names TWO statistics and one of them does not "
                 f"exist; mean/report is {_fmt(now['mean_unattributed'])} vs "
                 f"{_fmt(base['mean_unattributed'])} and is NOT a verdict on "
                 "its own.)")
        return "\n".join(o), EXIT_NOT_MEASURABLE
    d_mean = now["mean_unattributed"] - base["mean_unattributed"]
    d_share = now["unattributed_share"] - base["unattributed_share"]
    if d_mean < 0 and d_share < 0:
        word = "LOWER on both statistics"
    elif d_mean > 0 and d_share > 0:
        word = "HIGHER on both statistics"
    else:
        word = "MIXED (the two statistics disagree)"
    o.append(f"  VERDICT: post-cut in-population is {word} "
             f"(mean/report {_fmt(now['mean_unattributed'])} vs "
             f"{_fmt(base['mean_unattributed'])}, Δ{d_mean:+.3f}; share "
             f"{_fmt(now['unattributed_share'])} vs "
             f"{_fmt(base['unattributed_share'])}, Δ{d_share:+.3f}), {unit}")
    ctrl = stats(post_out)
    o.append("  CONTROL (contemporaneous): POST-cut out-of-population "
             f"mean/report {_fmt(ctrl['mean_unattributed'])}, share "
             f"{_fmt(ctrl['unattributed_share'])} over {ctrl['n']} report(s) in "
             f"{ctrl['sessions']} session(s). 🔴 Read the verdict against THIS "
             "as well as against the PRE-cut baseline: a difference that also "
             "appears here is not the asks block.")
    o.append("  ⚠ A comparison of two UNCONTROLLED populations (blind spot 4), "
             "not a causal claim about the asks block. The remaining half of "
             "the closing condition — that no report raises a deletion "
             "candidate against a requirement the asks block quotes — is a "
             "JUDGEMENT this tool does not make.")
    return "\n".join(o), EXIT_OK


def main(argv=None, out_stream=sys.stdout) -> int:
    ap = argparse.ArgumentParser(
        description="the round-0 attribution RATE, pre-cut vs post-cut")
    ap.add_argument("--corpus", help=f"corpus root (or ${CORPUS_ENV})")
    ap.add_argument("--since-sha", default=DEFAULT_SINCE_SHA,
                    help=f"the cut, resolved via git (default {DEFAULT_SINCE_SHA})")
    ap.add_argument("--cut-iso",
                    help="use this instant as the cut and consult no git at all")
    ap.add_argument("--repo", default=str(REPO),
                    help="repo to resolve --since-sha in")
    args = ap.parse_args(argv)

    oa = load_operator_asks()
    corpus = corpus_root(args.corpus)
    # The probe root must not exist: `render()` globs it, and the pin must not
    # depend on — or walk — the real corpus.
    probes = renderer_probes(oa, corpus / "_r0rate_probe_no_such_dir")
    check_pins(oa, probes)
    # 🔴 AND the behavioural poles, on every run: the strings can all be right
    # while the PRECEDENCE between them is wrong, which is exactly the defect
    # `#1901 round 0` found.
    check_behavioural_poles(oa, probes)

    if args.cut_iso:
        cut_iso, cut_src = args.cut_iso, "--cut-iso, no git consulted"
    else:
        cut_iso = resolve_cut(args.since_sha, Path(args.repo))
        # The label NAMES THE CLOCK. A cut printed without saying which of a
        # commit's two dates it read is the same defect one level out: the
        # reader cannot tell whether the boundary is "authored" or "landed".
        cut_src = (f"{CUT_CLOCK} of {args.since_sha} in {args.repo} — "
                   f"{CUT_CLOCK_LABEL}")
    cut = parse_ts(cut_iso)
    if cut is None:
        fail(EXIT_USAGE, f"cannot parse the cut {cut_iso!r} as ISO-8601")

    reports, facts = walk(corpus, oa.SOURCE_SESSION)
    text, code = render_report(cut_iso, cut_src, corpus, facts,
                               bucket_reports(reports, cut))
    print(text, file=out_stream)
    return code


def _project_of(path: Path, corpus: Path) -> str:
    """The project a transcript belongs to.

    🔴 DELEGATES TO `transcript_search.project_dir_of` — `path.parent.name` is
    WRONG for a nested transcript: a real post-merge report of this arc lives at
    `<corpus>/<project>/<session>/subagents/agent-<id>.jsonl`, where
    `parent.name` is literally `subagents`. That function takes the FIRST path
    segment under the root, which is the project for both depths, and it is the
    ONE place that rule lives.

    ⚠ There is no fallback re-spelling of that rule any more: `#1901 round 0`'s
    deletion pass removed it (see the import at the top for why the state it
    guarded has no producing mechanism).
    """
    return transcript_search.project_dir_of(path, root=corpus)


def _session_id_of(path: Path, corpus: Path) -> str:
    """The REAL session a transcript belongs to — NOT the file.

    🔴 A FILE IS NOT A SESSION, and counting files was the same
    label-wider-than-the-unit defect the `MIN_SESSIONS` rename was written to
    close, relocated one level up. The corpus has two shapes:

        <corpus>/<project>/<sid>.jsonl                       the session itself
        <corpus>/<project>/<sid>/subagents/agent-<x>.jsonl   its subagents

    A round-0 report is written BY an auditor subagent, so it lives in the second
    shape — a different file from the parent's, and several auditors of one
    session are several files again (corpus-wide ~6.9x; blind spot 6 carries the
    figures with their date).

    🔴 STRIPPING `.jsonl` IS THE LOAD-BEARING LINE, not tidiness. Both shapes
    carry the session id in the SAME path segment, but in the first it carries the
    extension — so a derivation that takes the segment RAW counts a session with
    both a top-level transcript and a `subagents/` directory TWICE. That is not
    hypothetical: it is how a wrong corpus-session figure reached this file and
    three other places. `test_the_naive_session_derivation_double_counts_and_ours_does_not`
    pins the mechanism.

    ⚠ Falls back to the file's own stem for a shape neither branch describes,
    which over-counts sessions rather than merging two real ones — the safe
    direction for a FLOOR.
    """
    try:
        rel = path.relative_to(corpus)
    except ValueError:                                      # pragma: no cover
        return path.stem
    parts = rel.parts
    if len(parts) < 2:
        return path.stem
    head = parts[1]
    return head[:-len(".jsonl")] if head.endswith(".jsonl") else head


if __name__ == "__main__":
    sys.exit(main())
