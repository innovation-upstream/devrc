#!/usr/bin/env python3
"""The ROUND-0 ATTRIBUTION RATE — is `unattributed:` falling since the asks block shipped?

    scripts/round0-attribution-rate.py                      # full corpus, default cut
    scripts/round0-attribution-rate.py --since-sha 31033cdb
    scripts/round0-attribution-rate.py --cut-iso 2026-09-27T05:49:10Z   # no git at all
    scripts/round0-attribution-rate.py --samples 5          # per-report SHAPES, opt-in
    scripts/round0-attribution-rate.py --json out.json

WHY THIS EXISTS
---------------
`claudedocs/handoff-audit-pr-operator-asks.md` closes on a count, and that count
had TWO defects as a CHECK. Both were measured on 2026-09-27, and this script is
the fix for both — the doc's `closing-condition` now names this command.

**Defect 1 — the baseline was not reproducible.** The doc quoted *1,649 round-0
ledger lines across 728 sessions in 7 repos*. Two defensible methods over the
same corpus (`~/.claude/projects/**/*.jsonl`) disagree, and NEITHER produces it:
counting only ASSISTANT-authored text blocks gives 493 lines / 474 sessions / 7
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
A report belongs to the population the closing condition is about iff its own
session's brief carried the asks block AND that block's SESSION-TRANSCRIPT
source ANSWERED. The discriminator is the asks block's own emitted text, found
in the same transcript (the brief arrives injected, which is exactly why the
provenance rule above applies to the REPORT and not to this test). Verbatim
shape of the OUT case, from a real brief (cairn #134, 2026-09-27T06:16Z):

    ## THE OPERATOR'S OWN ASKS — attribution input for step 1
    ! session transcript: UNKNOWN — none of this PR's 1 commit(s) carries a
      `Claude-Session-Id:` trailer — coverage is partial by nature …
    **Ledger:** operator asks: 0 (0 B)

🔴 THREE DISPOSITIONS, NEVER TWO:

    in-population      the asks block is present and its session-transcript
                       source ANSWERED (sessions were named by trailers).
    out-of-population  the asks block is present and says no commit carries a
                       trailer — or there is no asks block at all, i.e. a
                       pre-fix report.
    UNKNOWN            an asks block is present but its Sources block could not
                       be parsed, or carries no session-transcript line at all.

🔴 UNKNOWN IS NEVER FOLDED INTO EITHER OTHER BUCKET AND NEVER INTO THE RATE'S
DENOMINATOR. A reassuring zero and an unreadable source are different findings;
folding them makes an instrument that cannot report its own blindness.

⚠ A session that dispatched SEVERAL audits carries several asks blocks, and a
ledger line cannot be tied to one of them by text alone. Precedence is
`answered` > `unmeasured` > `unparseable`, i.e. one answered block makes the
session's reports in-population. That is a stated choice, not a measurement.

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
6. **A report's in-population test is a property of its SESSION**, not of the
   PR. A session that dispatched one answered audit and one unanswered one has
   all of its reports counted in-population (see the precedence note above).
7. **A PRE-CUT block written under an OLDER SPELLING reads as UNKNOWN, on
   purpose.** MEASURED on this host: two blocks from the `#1887` development
   session (2026-09-26) say `N session(s) resolved from `Claude-Session-Id:`
   trailers` — the wording round 1 of that ladder replaced with `NAMED BY`. They
   are `answered` lines this instrument cannot read, so they land in UNKNOWN
   rather than in-population. That is deliberate: a HISTORICAL spelling cannot
   be pinned against a live render, and an unpinnable anchor is the one shape
   this design refuses. It costs nothing the closing condition needs — the
   comparator is POST-cut in-population against PRE-cut ALL.
8. **The in-population test is a property of the BRIEF, never of the PR's
   commits.** It reads what the asks block SAID about its sources; it does not
   check GitHub, so a PR whose trailers exist but whose transcript was pruned is
   out-of-population here even though the condition's prose ("PRs whose commits
   carry a `Claude-Session-Id:` trailer") would include it.

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

🔴 THIS REPO IS PUBLIC AND THE CORPUS IS TRANSCRIPTS. Default output is counts,
rates and ledger-line SHAPES — no message bodies, no prompts, no quoted
transcript prose. `--samples` is opt-in, capped, and prints only
project/date/numbers/disposition per report; even that is per-session metadata
about the operator's work, so **its output must never be committed or posted**.

Env:
  ROUND0_RATE_CORPUS        corpus root (default `~/.claude/projects`)
  ROUND0_RATE_OPERATOR_ASKS path to the `operator_asks.py` the anchors are
                            checked against. Exists so the exit-5 pin can be
                            watched RED against a copy — a gate nothing can be
                            aimed at a broken input is a gate nobody has seen
                            fail.

Exit codes: 0 a verdict printed · 2 bad invocation (an unresolvable `--since-sha`
or an unreadable corpus path) · 3 a self-control failed · 4 nothing walked, or no
assistant-authored ledger line anywhere · 5 the pinned renderer strings disagree
with `operator_asks.py` · 6 NOT MEASURABLE. 🔴 6 has TWO reasons and the message
says which: the post-cut in-population count is below `MIN_REPORTS`, or the
pre-cut bucket is empty so the comparison has no left-hand side. They share one
code because they demand the same action — collect more reports, change nothing.
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

# 🔴 IMPORTED SOFTLY — its absence must not be a traceback — BUT THE FALLBACK IS
# NOT SILENT: `_project_of` names the derivation it used, because the fallback is
# a NARROWER rule than the shared one and a reader must not mistake the two. The
# only thing taken from here is "which project does this transcript belong to",
# which this script must not re-derive: `path.parent.name` is literally
# `subagents` for a nested transcript.
try:  # noqa: E402
    import transcript_search
except ImportError as _e:  # pragma: no cover - exercised via the fallback path
    transcript_search = None
    _TRANSCRIPT_SEARCH_IMPORT_ERROR = str(_e)
else:
    _TRANSCRIPT_SEARCH_IMPORT_ERROR = ""

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
#: #1). Below this many post-cut in-population reports the run prints its
#: numbers and then refuses to compare, so the instrument cannot be used to
#: justify a change the data cannot support. Today the real answer is n=0, and a
#: run must say NOT MEASURABLE rather than "improved".
MIN_REPORTS = 10

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

#: 🔴 MATCHED AT LINE START, AND THAT IS A MEASURED BUG FIX RATHER THAN RIGOUR
#: FOR ITS OWN SAKE. A plain substring test says "this session received an asks
#: block" for any session that merely READ `operator_asks.py`: that file's own
#: source carries `HEADING = "## THE OPERATOR'S OWN ASKS …"` and
#: `lines += ["", "**Sources read:**"]`, so a Read/Grep tool_result contains both
#: anchors. MEASURED on this host 2026-09-27 over the whole corpus: 51 blocks
#: contain the heading substring, of which only **12** carry it at the start of a
#: line, and every one of the other 39 is a source read or a discussion of this
#: feature. Those 39 were landing in UNKNOWN — an instrument reporting 11
#: unreadable sources it had invented for itself.
#: `render()` emits both at column 0 (it builds `lines` and joins them), so
#: line-anchoring costs nothing real; the pin below asserts that, so a renderer
#: that ever indents its own block fails loudly instead of going invisible.
ANCHOR_BLOCK_RE = re.compile("^" + re.escape(ANCHOR_BLOCK), re.M)
ANCHOR_SOURCES_RE = re.compile("^" + re.escape(ANCHOR_SOURCES), re.M)

#: Every session-transcript source line `render()` can emit, by the fragment
#: that identifies it. `role` is what the line means for the in-population test.
#: 🔴 TWO-WAY: a line shape `render()` gains and this does not cover is a
#: failure (GROWS), and an anchor that matches nothing a live render emits is a
#: failure (SHRINKS).
ANCHORS = (
    dict(id="answered", role="answered", text="session(s) NAMED BY",
         spelling="`render()`'s trailer line — 'N session(s) NAMED BY "
                  "`Claude-Session-Id:` trailers'. The word NAMED BY was itself "
                  "a round-1 correction of 'resolved', so it is the load-bearing "
                  "half and the count/label tail is not matched."),
    dict(id="unmeasured", role="unmeasured", text=": UNKNOWN — ",
         spelling="`render()`'s `! {source}: UNKNOWN — {reason}` line for an "
                  "`Unmeasured` source. The em-dash separator is part of it; the "
                  "reason text is not matched at all."),
    dict(id="dropped", role="informational", text=": dropped ",
         spelling="`render()`'s `{source}: dropped N record(s) — {reason}` line. "
                  "INFORMATIONAL: records were dropped by the classifier, which "
                  "says nothing about whether the source answered — it is in the "
                  "ledger so the GROWS direction stays meaningful."),
)

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

    🔴 NOT a string compare. Transcript stamps end in `Z` while `git log %aI`
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


def renderer_probes(oa, projects_root) -> dict:
    """Name -> a live `render()` output, one per source-line shape it can emit.

    The matrix, not one happy case: each entry is chosen to make ONE
    session-transcript line shape appear, so the two-way pin can require that
    every emitted shape is covered and every anchor is reachable.

    `projects_root` is pointed at a directory that does not exist on purpose —
    `agent_side_reference` globs it, and the probe must not walk the real
    7.9 GB corpus (nor depend on what is in it).
    """
    sid = "a" * 8
    ask = oa.Ask(source=oa.SOURCE_SESSION, text="an ask", session_id=sid)
    unmeasured = oa.Unmeasured(
        source=oa.SOURCE_SESSION,
        reason="none of this PR's commits carries a trailer")
    kw = dict(projects_root=projects_root)
    return {
        # sessions were named by trailers -> the source ANSWERED
        "answered": oa.render([ask], session_ids=(sid,), comments_examined=0, **kw),
        # consulted and did not answer -> the OUT case, verbatim shape
        "unmeasured": oa.render([], unmeasured=(unmeasured,),
                                comments_examined=0, **kw),
        # the classifier dropped records -> an INFORMATIONAL session line
        "dropped": oa.render([ask], session_ids=(sid,),
                             dropped={"a reason": 3}, comments_examined=0, **kw),
        # no source consulted at all -> a block with no session line
        "no-source": oa.render([], **kw),
    }


def sources_block_lines(text: str) -> list[str]:
    """The source lines of the FIRST asks block in `text`, or [].

    Structural, not positional: from `ANCHOR_SOURCES` take every following
    indented line, stopping at the first non-empty line that is NOT indented
    (in a real render that is the `**Ledger:**` line). Returns [] when the block
    is present but its Sources marker is not — which the caller reports as
    UNKNOWN rather than as an absence of sources.
    """
    head = ANCHOR_BLOCK_RE.search(text)
    if not head:
        return []
    mark = ANCHOR_SOURCES_RE.search(text, head.start())
    if not mark:
        return []
    out: list[str] = []
    for line in text[mark.start():].splitlines()[1:]:
        if not line.strip():
            continue
        if not line.startswith(SOURCE_LINE_INDENT):
            break
        out.append(line)
    return out


def session_source_lines(session_label: str, lines) -> list[str]:
    """The subset of source lines that are about the SESSION-TRANSCRIPT source."""
    needle = f"{session_label}:"
    return [l for l in lines if needle in l]


def match_anchors(lines) -> tuple[set, set, list]:
    """-> (roles matched, anchor ids matched, lines matching NO anchor).

    The unmatched list is the GROWS half of the two-way pin (a session-source
    line shape the ledger does not cover); the matched ids are the SHRINKS half
    (an anchor nothing emits any more).
    """
    roles: set = set()
    ids: set = set()
    unmatched: list[str] = []
    for line in lines:
        hit = [a for a in ANCHORS if a["text"] in line]
        if not hit:
            unmatched.append(line)
            continue
        for a in hit:
            roles.add(a["role"])
            ids.add(a["id"])
    return roles, ids, unmatched


def check_pins(oa, probes) -> None:
    """Refuse (exit 5) unless the anchors still describe what `render()` emits.

    Both directions, on every run:
      SHRINKS  an anchor that matches nothing a live render emits — the shape
               this instrument is blind to after a reword, and the one that
               silently empties the denominator.
      GROWS    a session-source line the ledger does not cover — a new shape
               whose meaning for the in-population test nobody has decided.
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
        lines = session_source_lines(oa.SOURCE_SESSION,
                                     sources_block_lines(text))
        _roles, ids, bad = match_anchors(lines)
        seen_ids |= ids
        unmatched += bad
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
            "GROWS — render() emits session-transcript source line(s) no anchor "
            "covers, so this instrument does not know what they mean for the "
            f"in-population test: {unmatched[:3]}")
    if missing and unmatched:
        parts.append("BOTH halves failed together, which is the signature of a "
                     "REWORD rather than of a new source line: update ANCHORS "
                     "in the same commit that reworded the renderer.")
    fail(EXIT_PIN, "the pinned renderer strings disagree with operator_asks.py. "
                   + "  ".join(parts))


# --------------------------------------------------------------------------- #
@dataclass
class Report:
    project: str
    ts: str
    dt: object
    requirements: int
    unattributed: int
    session: str = ""          # the transcript path — NEVER printed by default
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
    """
    roles: set = set()
    unparseable = 0
    blocks = 0
    for txt in texts:
        if not ANCHOR_BLOCK_RE.search(txt):
            continue
        blocks += 1
        lines = session_source_lines(session_label, sources_block_lines(txt))
        if not lines:
            unparseable += 1
            continue
        got, _ids, _bad = match_anchors(lines)
        roles |= got
    if not blocks:
        return "out", "no asks block in this session (a pre-fix report)"
    if "answered" in roles:
        return "in", "the asks block's session-transcript source answered"
    if "unmeasured" in roles:
        return "out", ("the asks block says the session-transcript source could "
                       "not be read")
    return "UNKNOWN", (
        f"an asks block is present but its Sources block could not be parsed "
        f"({unparseable} of {blocks}) or names no session-transcript source — "
        "that is unreadable, NOT a zero")


@dataclass
class WalkFacts:
    """What the walk saw, beside the reports themselves.

    A dataclass rather than a dict because two of these fields are COUNTS and
    one is a Counter: a `dict(...)` of mixed value types type-checks as a union
    and every `+= 1` on it is unverifiable.
    """
    files: int = 0
    sessions_with_reports: int = 0
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
                                    session=str(path)))
        if not found:
            continue
        facts.sessions_with_reports += 1
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
                sessions=len({r.session for r in rows}))


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


def render_report(cut_iso, cut_src, corpus, facts, buckets, undated,
                  samples, args_samples) -> tuple[str, int]:
    """The whole report, and the exit code it implies."""
    o: list[str] = []
    pre, pre_in, post, post_in, post_unknown = buckets
    o.append("round0-attribution-rate — run "
             f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    o.append(f"corpus {corpus}   files walked {facts.files}")
    if transcript_search is None:
        # Never silent: the fallback project rule is NARROWER than the shared
        # one, so a reader must know which was used.
        o.append("⚠ `scripts/lib/transcript_search` could not be imported "
                 f"({_TRANSCRIPT_SEARCH_IMPORT_ERROR}) — the project of each "
                 "transcript came from this script's own fallback rule, not "
                 "from the shared one")
    o.append(f"cut {cut_iso}   ({cut_src})")
    o.append("unit: ONE round-0 ledger line in ONE assistant-authored text "
             "block. Injected copies (skill body, brief) are NOT reports: "
             f"{facts.injected_ledger_lines} such occurrence(s) were seen "
             "and excluded.")

    o.append("\nCONTROLS")
    pos = len(pre) + len(post) + undated["n"]
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
    o.append(f"{'bucket':34} {'reports':>7} {'sess':>5} {'reqs':>6} "
             f"{'unattr':>7} {'mean/report':>11} {'share':>7}")
    for label, rows in (("PRE-cut (all)", pre),
                        ("PRE-cut in-population", pre_in),
                        ("POST-cut (all)", post),
                        ("POST-cut in-population", post_in)):
        s = stats(rows)
        o.append(f"{label:34} {s['n']:7d} {s['sessions']:5d} "
                 f"{s['requirements']:6d} {s['unattributed']:7d} "
                 f"{_fmt(s['mean_unattributed']):>11} "
                 f"{_fmt(s['unattributed_share']):>7}")
    o.append(f"projects represented: PRE={len({r.project for r in pre})}  "
             f"POST={len({r.project for r in post})}")
    o.append(f"\nDISPOSITIONS (of every report, both buckets): "
             + "  ".join(f"{k}={v}" for k, v in
                         sorted(facts.dispositions.items())))
    o.append(f"  UNKNOWN post-cut: {len(post_unknown)} — 🔴 NOT folded into "
             "in-population or out-of-population, and NOT in any denominator "
             "above. An unreadable source is a different finding from a zero.")
    o.append(f"  undated (no `timestamp`): {undated['n']} — reported, folded "
             "into NOTHING, so neither bucket claims them.")

    if args_samples:
        o.append("\nSAMPLES (shapes only — no transcript prose; do NOT commit "
                 "or post this section)")
        for label, rows in samples:
            for r in rows:
                o.append(f"  [{label}] {r.project} {r.ts[:19]} "
                         f"requirements={r.requirements} "
                         f"unattributed={r.unattributed} {r.disposition}")

    o.append("\nVERDICT")
    n = len(post_in)
    if n < MIN_REPORTS:
        o.append(f"  VERDICT: NOT MEASURABLE (n={n} < {MIN_REPORTS})")
        o.append("  The post-cut in-population bucket is too small to compare. "
                 "🔴 This is a standing operator instruction, not a formatting "
                 "choice: do NOT re-tune the feature off n<"
                 f"{MIN_REPORTS}. Numbers above are printed so the population "
                 "can be watched grow; they are not a result.")
        return "\n".join(o), EXIT_NOT_MEASURABLE
    base = stats(pre)
    if base["n"] == 0 or base["mean_unattributed"] is None:
        o.append("  VERDICT: NOT MEASURABLE (the pre-cut bucket is empty, so "
                 "the comparison has no left-hand side)")
        return "\n".join(o), EXIT_NOT_MEASURABLE
    now = stats(post_in)
    if now["mean_unattributed"] is None:
        # Unreachable while the MIN_REPORTS refusal above stands — which is
        # exactly why it is here: a mutation sweep that removed that refusal
        # killed this path with a `TypeError`, i.e. the guard's absence was
        # reported as a crash rather than as a refusal. A comparison with no
        # right-hand side is NOT MEASURABLE, in every route to it.
        o.append("  VERDICT: NOT MEASURABLE (the post-cut in-population bucket "
                 "has no reports, so there is nothing to compare)")
        return "\n".join(o), EXIT_NOT_MEASURABLE
    d_mean = now["mean_unattributed"] - base["mean_unattributed"]
    d_share = ((now["unattributed_share"] or 0)
               - (base["unattributed_share"] or 0))
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
             f"{_fmt(base['unattributed_share'])}, Δ{d_share:+.3f}), n={n}")
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
    ap.add_argument("--samples", type=int, default=0,
                    help="print up to N report SHAPES per bucket (no prose). "
                         "Per-session metadata: never commit or post it.")
    ap.add_argument("--json", dest="json_out", help="also write the rows as JSON")
    args = ap.parse_args(argv)

    oa = load_operator_asks()
    corpus = corpus_root(args.corpus)
    # The probe root must not exist: `render()` globs it, and the pin must not
    # depend on — or walk — the real corpus.
    check_pins(oa, renderer_probes(oa, corpus / "_r0rate_probe_no_such_dir"))

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

    undated = dict(n=len([r for r in reports if r.dt is None]))
    dated = [r for r in reports if r.dt is not None]
    pre = [r for r in dated if r.dt < cut]
    post = [r for r in dated if r.dt >= cut]
    # 🔴 UNKNOWN is excluded from every denominator, in BOTH buckets.
    pre_in = [r for r in pre if r.disposition == "in"]
    post_in = [r for r in post if r.disposition == "in"]
    post_unknown = [r for r in post if r.disposition == "UNKNOWN"]
    samples = [(lbl, rows[:args.samples]) for lbl, rows in
               (("pre", pre), ("post", post))] if args.samples else []

    text, code = render_report(cut_iso, cut_src, corpus, facts,
                               (pre, pre_in, post, post_in, post_unknown),
                               undated, samples, args.samples)
    print(text, file=out_stream)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(dict(
            run=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            corpus=str(corpus), cut=cut_iso, cut_source=cut_src,
            files=facts.files, injected_ledger_lines=facts.injected_ledger_lines,
            undated=undated["n"], min_reports=MIN_REPORTS,
            dispositions=dict(facts.dispositions),
            buckets={k: stats(v) for k, v in
                     (("pre", pre), ("pre_in", pre_in), ("post", post),
                      ("post_in", post_in))},
            post_unknown=len(post_unknown), exit=code), indent=2))
        print(f"\nwrote {args.json_out}", file=out_stream)
    return code


def _project_of(path: Path, corpus: Path) -> str:
    """The project a transcript belongs to.

    🔴 DELEGATES TO `transcript_search.project_dir_of` — `path.parent.name` is
    WRONG for a nested transcript: a real post-merge report of this arc lives at
    `<corpus>/<project>/<session>/subagents/agent-<id>.jsonl`, where
    `parent.name` is literally `subagents`. That function takes the FIRST path
    segment under the root, which is the project for both depths, and it is the
    ONE place that rule lives.

    ⚠ The fallback is the same rule re-spelled, for a tree with no
    `scripts/lib` beside it (a partial deploy, a `cp -a` of one file). It is
    reached only when the module-level import failed, and `main()` prints that
    it was used — a narrower rule applied silently would be the worse failure.
    """
    if transcript_search is not None:
        return transcript_search.project_dir_of(path, root=corpus)
    try:
        rel = path.relative_to(corpus)
    except ValueError:                                      # pragma: no cover
        return "?"
    return rel.parts[0] if len(rel.parts) > 1 else ""


if __name__ == "__main__":
    sys.exit(main())
