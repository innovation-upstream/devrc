#!/usr/bin/env python3
"""Merge an UPDATE into an existing session-handoff doc, behind a real gate.

The write half of `/handoff` when a handoff doc for this topic ALREADY EXISTS —
step 2 of the skill writes a doc from scratch, and this is what runs the second
and every later time. (A missing base is handled rather than refused: the update
simply becomes the whole doc, so nothing here breaks if the skill ever routes a
first write through it too.) It exists because of a measured incident:

  A session re-entered work from a handoff, did ten minutes of genuinely
  valuable analysis — it answered the doc's open question AND corrected a prior
  misreading — and then wrote and PUSHED an updated handoff to a shared
  branch with no confirm gate at all. The operator never approved it.

Both skills involved were correct on their own terms. `/resume` is read-only by
contract and followed it. `/handoff` gates its *index* write ("Write only on
explicit confirm, diff first … on decline, discard"). The gap was underneath:
the handoff DOC's own write+push carried no equivalent gate, and a session
running after a resume inherited no constraint at all.

SEVEN RULES, and this module is what makes six of them structural rather than
prose an agent can read and then not follow:

a. UPDATING IS NOT FORBIDDEN. The incident's update was correct and valuable;
   suppressing it costs the next session the ten minutes again. Optimising for
   doc stability over state accuracy is backwards. So this tool exists to make
   the update SAFE, not to make it rare — there is no "don't update" path here.

b. THE GATE IS ON THE PUSH, and it is the SAME gate shape `/handoff` already
   specifies for the index write: one compact unified diff, a single y/N, and
   on decline DISCARD. Structurally: the default mode writes NOTHING — not the
   doc, not a commit, not a ref — it only prints the diff a human is being
   asked to approve. Landing it takes a SECOND invocation carrying `--confirm`
   (and `--push`), which is the action that happens after the `y`. A decline is
   therefore not a code path that has to behave; it is the absence of one, and
   `TestDeclineWritesNothing` hashes the whole repo tree either side of a
   default-mode run to keep it that way.

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

EXIT CODES
  0  proposed (diff shown, nothing written) — or written/pushed under --confirm.
     `written` WITHOUT `--push` also reports the branch and that it is not pushed
  2  usage
  3  operational failure (unreadable input, git refused) — nothing written
  4  no-advance      — rule (d), no diff printed
  5  no-change       — merge is a no-op, no diff printed, no empty commit
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
    """
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

    body_starts = _body_start_lines(base_pre, base_secs)
    dropped: list[DroppedDurable] = []
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

    rendered = out_pre + "".join(h + b for h, b in out + tail)
    return MergeReport(
        text=rendered.rstrip("\n") + "\n",
        dropped=tuple(dropped),
        buckets=tuple(buckets),
    )


def _body_start_lines(pre: str, sections: list[list[str]]) -> list[int]:
    """1-based line number of each section BODY's first line in the base doc.

    Derived from the same lossless split the merge walks, so a line number can
    never name a line from a different section: `split_sections` guarantees
    `pre + "".join(h + b)` reproduces the document byte-for-byte, which makes
    counting newlines an exact address rather than an estimate.
    """
    starts: list[int] = []
    cur = pre.count("\n") + 1  # the first heading's own line number
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
    head = (
        f"🔴 This replace DROPS {len(dropped)} line(s) that look DURABLE "
        f"(they sit under a REPLACE heading):"
    )
    rows = [
        f"  {_clip(d.heading, 44)}:{d.line_no}: "
        f"{_clip(d.line.strip(), DROPPED_LINE_MAX)}  [{d.reason}]"
        for d in dropped[:DROPPED_SHOWN_MAX]
    ]
    elided = len(dropped) - len(rows)
    if elided:
        rows.append(f"  … and {elided} more not shown (read the diff below).")
    return "\n".join([head, *rows, DROPPED_REMEDY])


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
        description="merge an update into an existing handoff doc, behind a confirm gate",
    )
    p.add_argument("--repo", required=True, help="repo root the handoff lives in")
    p.add_argument("--topic", required=True, help="handoff topic slug")
    p.add_argument(
        "--update",
        required=True,
        help="file holding the proposed sections (## headings, a delta not a whole doc)",
    )
    p.add_argument(
        "--advanced",
        help="one line: what changed since the doc was written. Required — "
        "without it, or with a value that means nothing changed, no diff is "
        "offered and nothing is written (rule d).",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="land it: write the doc and make exactly one commit of that path. "
        "Run this ONLY after a human answered y to the diff the default mode printed.",
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
    warning = dropped_durable_report(report.dropped)
    if warning:
        print(warning)
    print(diff, end="" if diff.endswith("\n") else "\n")

    if not args.confirm:
        print("status=proposed")
        print(
            "NOTHING WRITTEN — not the doc, not a commit, not a ref. Ask exactly "
            "one `update the handoff doc and push it? (y/N)`.\n"
            "  y -> re-run this exact command with --confirm (add --push to land "
            "it on the shared branch, which is what the question asked about)\n"
            "  n -> discard: write nothing and run nothing else. The tree is "
            "already byte-identical."
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

    try:
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(merged_text, encoding="utf-8")
        git(repo, "add", "--", relpath)
        subject = f"docs(handoff): {args.advanced.strip().splitlines()[0]}"[:100]
        # Path-limited on purpose: exactly one commit, carrying exactly the
        # diff that was shown, even if the caller had other work staged.
        git(repo, "commit", "-m", subject, "--", relpath)
        sha = git(repo, "rev-parse", "HEAD").strip()
    except (GitError, OSError) as exc:
        print(f"status=failed\n{exc}", file=sys.stderr)
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
