#!/usr/bin/env python3
"""The operator's own asks, as attribution input for `/audit-pr`'s ROUND 0.

WHAT PROBLEM THIS SOLVES. Round 0 step 1 says "Question every requirement, and
NAME its author of record: Zach (quote the ask), a prior audit round, a
`RULES.md`/`CLAUDE.md` bullet, or **unattributed**". The auditor is dispatched
READ-ONLY with a diff and a PR, so "quote the ask" had no source: requirements
the operator stated in as many words landed on `unattributed`, which the section
treats as a finding, and the deletion candidate that produces is aimed at
something he requested.

🔴 THE CENTRAL HAZARD IS THE EMPTY CASE, NOT THE FULL ONE. Trailer coverage is
partial — measured 2026-09-26 over the 60 newest `main` commits, 35 carry a
`Claude-Session-Id:` by a BODY scan and 17 by git's own trailer parser. So "no
ask could be read" is a COMMON outcome, and an empty block reading as "the
operator asked for nothing" would actively LICENSE the deletion suggestion this
module exists to prevent. Every unreadable source renders as a named UNKNOWN
with an explicit directive, never as an absence; `render()` has no code path
that emits a quiet empty block.

🔴 READ THE COMMIT **BODY**, NEVER git's TRAILER PARSER. Measured on `c0fd28e3`,
the squash merge of devrc#1883: `git log -1
--format='%(trailers:key=Claude-Session-Id,valueonly)'` prints EMPTY while the
body holds 12 occurrences of `Claude-Session-Id: ad781c3f-…`, because a GitHub
squash body concatenates every squashed message and the ids do not land in git's
final trailer block. So this module calls `handoff_arc.trailer_ids(body)`: same
predicate, one definition, and the one that can see a squash.

🔴 `kind: "typed"` IS NOT THE OPERATOR — BUT ALMOST ALL OF THE DIFFERENCE IS ONE
CLASS, AND THE UPSTREAM TOOL ALREADY REMOVES THE REST. Measured 2026-09-26 over
**the population `parse_typed_rows` actually receives** — 18,494 `typed` rows /
55,551,545 B, i.e. what `extract_user_msgs.records_of` EMITS, not what the raw
transcripts hold:

    <task-notification>   10,007 rows   54,654,875 B   98.39%   (subagent results)
    a harness-authored note    13 rows          998 B    0.00%
    THE OPERATOR            8,474 rows      895,672 B    1.61%   median 17 B

🔴 AND THAT TABLE REPLACES A WRONG ONE — DO NOT RE-DERIVE THE OLD SHAPE. An
earlier version of this module classified SIX leading tags plus injected SKILL
BODIES, hook feedback and an interrupt prefix, and its docstring called skill
bodies "THE HARDEST CLASS TO SPOT … 59.5% of all user-role bytes". **Every one of
those eight families fires ZERO times in production**, because they are removed
UPSTREAM by the only producer this module consumes from
(`scripts/session-analysis/extract_user_msgs.py`):

  * `records_of` drops `isMeta` records, and an injected skill body IS
    `isMeta: true` — measured, 1,156 of 1,166 such records in the first 400
    transcripts, and 0 could ever reach here;
  * `COMMAND_NAME` routes `<command-name>`/`<command-message>` to
    `kind == "command"`, which `parse_typed_rows` drops a line earlier;
  * `clean_text` strips `<system-reminder>` and `<local-command-stdout>`;
  * `BOILERPLATE_PREFIXES` there already holds `"[Request interrupted"` and
    `"Caveat: The messages below"` — so re-spelling them here was the same
    predicate at two sites, which `claude/RULES.md` forbids.

The 59.5% figure was real but measured on the WRONG POPULATION: raw user-role
transcript records, which this module never sees. Found by round 0 of the audit
ladder on devrc#1887 and reproduced independently. The lesson worth keeping: a
filter's population is whatever its PRODUCER hands it, and measuring the corpus
instead is how eight guards got written for classes that cannot arrive.

🔴 NO CAP, NO CEILING — AND BOTH WERE DELETED AS MEASURED DEAD WEIGHT. An earlier
version carried `PER_MESSAGE_CAP` (clipping each message's tail) and
`BLOCK_CEILING` (a whole-block backstop) with a clip marker, byte accounting and
a withheld-text ledger. Measured: the cap clipped **20 of 8,474 operator
messages (0.24%)** and the ceiling fired **never** — the largest per-session ask
corpus on this host is 10,016 B against a 49,152 B ceiling. The operator's own
answer, recovered from the very transcript this feature reads, was *"user
messages only, my messages are never that big"*. Operator sizes: median 17 B,
p90 235 B, p99 907 B, max 16,404 B.

🔴 NEVER SUMMARISE AN ASK. A paraphrase of "the user directly asked for this" is
exactly the evidence that goes missing, so this module has no summarising path.

WHAT THIS DELIBERATELY DOES NOT CARRY: any agent/assistant output. The operator
asked for his own messages only — and for a way to reach the agent side "if
needed", which `agent_side_reference()` prints rather than inlining.

⚠ NOT A SOURCE, DELIBERATELY: the PR DESCRIPTION. An earlier version read it,
captioned "probably written by the agent". Measured over the 60 newest merged
devrc PRs, **1** body names an ask and **4** contain any blockquote — so on ~98%
of PRs it contributed agent prose under a heading saying it was the operator's,
and on devrc#1887 itself it was 83% of the block. Operator's call, 2026-09-26:
dropped. PR COMMENTS stay — measured 115 across the 120 newest devrc PRs.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import handoff_arc
import session_trailer
import transcript_search

SOURCE_SESSION = "session transcript"
SOURCE_PR_COMMENT = "PR comment"

#: Rejected before an id reaches `find_transcript`, which globs it unescaped.
_GLOB_METACHARACTERS = "*?[]"

#: Most- to least-authoritative. A session transcript is what the operator typed
#: while the work happened; a PR comment is still his words but written after.
SOURCE_ORDER = (SOURCE_SESSION, SOURCE_PR_COMMENT)

HEADING = "## THE OPERATOR'S OWN ASKS — attribution input for step 1"

#: The instruction that makes an UNKNOWN safe. A STRING CONSTANT because tests
#: pin it: it must appear whenever ANY source is unreadable, including when other
#: sources DID answer — a partial read is still partial, and the source that
#: failed is the one a deletion candidate would be wrong about.
UNKNOWN_DIRECTIVE = (
    "🔴 **A SOURCE THAT COULD NOT BE READ IS NOT AN ABSENCE OF ASKS.** Where a "
    "source below says UNKNOWN, you have no evidence either way about what the "
    "operator asked for — so record such a requirement as "
    "`UNATTRIBUTED-UNKNOWN`, which is NOT the same as `unattributed`, and do "
    "**not** raise a deletion candidate whose whole case is that nobody asked "
    "for it. Say the source was unreadable instead."
)

#: 🔴 THE OPERATOR IS `viewerDidAuthor`, AND NOTHING ELSE WILL DO. This predicate
#: has now been wrong TWICE in the same place, both times because it answered a
#: NEARBY question instead of the one the heading asks.
#:
#:  1. A nine-login BOT DENYLIST. Measured: filtered **0** of 115 comments across
#:     the 120 newest devrc PRs, and missed `civitai-deploy` — 130 of 177
#:     comments (74%) on the 60 newest `civitai/civitai` PRs. Nine declarations,
#:     zero instances (`claude/RULES.md` → "declarations-vs-instances").
#:  2. `authorAssociation in {OWNER, MEMBER, COLLABORATOR}`. That is REPO
#:     MEMBERSHIP, which is not operator identity. Measured: `devrc` has **11
#:     collaborators, 10 of them other people**, all org members ⇒ `MEMBER`; and
#:     `civitai-deploy` — the very bot fix 1 missed — is ALSO `MEMBER`. So the
#:     replacement re-admitted the exact comment it was written to exclude, AND
#:     newly admitted ten teammates, under a heading reading "THE OPERATOR'S OWN
#:     ASKS". Round 1 of the devrc#1887 ladder found it.
#:
#: `viewerDidAuthor` is on every comment row `gh` already returns and means "the
#: authenticated user wrote this" — `gh` runs AS the operator, so it is the
#: identity question itself rather than a proxy for it. Verified live: the
#: operator's own comment on #1887 is `viewerDidAuthor: true`, and
#: `civitai-deploy`'s are `false`.
#:
#: ⚠ THE LESSON, because two wrong answers in one spot is the pattern: when a
#: predicate's docstring asks a different question from the heading its result is
#: printed under, the heading is what readers believe. Do not replace this with
#: another proxy — a login table, an association set, an owner comparison — and
#: if `viewerDidAuthor` is ever absent, report UNKNOWN rather than guessing.
VIEWER_FIELD = "viewerDidAuthor"


@dataclass(frozen=True)
class Ask:
    """One thing the operator said, and where it was read from."""

    source: str
    text: str
    who: str = ""
    session_id: str = ""
    role: str = ""
    kind: str = ""


@dataclass(frozen=True)
class Unmeasured:
    """A source that was consulted and did not answer. NEVER a zero."""

    source: str
    reason: str


def session_ids_from_bodies(bodies: Iterable[str]) -> tuple[str, ...]:
    """Distinct session ids across several commit bodies, first-appearance order.

    Delegates the per-body scan to `handoff_arc.trailer_ids` — the one
    definition of "a safe session-id trailer in a commit body", and the one that
    sees a squash. De-duping ACROSS bodies is the only addition: a squash body
    plus the branch's own commits name the same session repeatedly.
    """
    out: list[str] = []
    for body in bodies:
        for sid in handoff_arc.trailer_ids(body or ""):
            if sid not in out:
                out.append(sid)
    return tuple(out)


def safe_label(sid: str, width: int = 8) -> str:
    """A short, PRINTABLE handle for a session id.

    Every id this module renders goes through here. `session_trailer.valid_id`
    is the safety gate — an id it refuses renders as `<unsafe-id>` rather than
    reaching a terminal, because the brief is printed to a tty and
    `handoff_arc`'s own comment records `\\x1b[2J\\x1b]0;PWNED\\x07` reaching
    terminals raw out of commit bodies in four repos.

    The truncation is COSMETIC and carries no shape claim: ids from other
    runtimes are not uuids and a short prefix is still a usable label.
    """
    if not session_trailer.valid_id(sid):
        return "<unsafe-id>"
    return sid[:width]


def is_the_operator(comment: object) -> tuple[bool, str]:
    """-> (is the operator, reason-if-not). The identity question, asked directly.

    `viewerDidAuthor` is authoritative: `gh` authenticates AS the operator, so
    the field means "the person this tool is running for wrote this". A MISSING
    field is NOT a yes and NOT a silent no — it returns a reason, so the caller
    can report UNKNOWN instead of guessing from a proxy. See `VIEWER_FIELD`'s
    comment for the two proxies that were wrong here before.
    """
    # 🔴 THE ONLY isinstance CHECK FOR A COMMENT ROW LIVES HERE. `asks_from_comments`
    # used to repeat it and then call this, which made this branch unreachable from
    # the only caller — a guard that reads as coverage and provides none, which is
    # exactly the eight-dead-guards defect round 0 found in this same module.
    # Pyright flagged it; the annotation is `object` so the check is real to the
    # type checker too.
    if not isinstance(comment, dict):
        return False, "a malformed comment row"
    v = comment.get(VIEWER_FIELD)
    if v is True:
        return True, ""
    if v is False:
        login = ((comment.get("author") or {}) or {}).get("login") or "someone else"
        # 🔴 WORDED AS THE FIELD'S ACTUAL CLAIM, not as an identity verdict.
        # `viewerDidAuthor` is about whoever `gh` is AUTHENTICATED AS. On the
        # operator's own host that is the operator, which is the whole basis for
        # using it — but under a CI token or a shared credential the operator's
        # OWN comment returns false, and a reason reading "not the operator" would
        # then be flatly untrue while the ask was dropped. Round 2 of the
        # devrc#1887 ladder found the wording.
        return False, (
            f"written by {login}, which is not the account `gh` is "
            "authenticated as"
        )
    return False, (
        f"`{VIEWER_FIELD}` was absent, so authorship could not be established — "
        "NOT a claim that the operator did not write it"
    )


def asks_from_comments(comments: Sequence[dict]) -> tuple[list[Ask], dict, int]:
    """-> (operator asks, {reason: count} for the rest, comments examined).

    🔴 RETURNS THE EXAMINED COUNT, so *consulted and empty* is distinguishable
    from *not consulted*. An earlier revision returned only a skip tally, so a PR
    with zero comments produced no "Sources read" line at all and the two states
    read identically — round 1 of the devrc#1887 ladder found that too.
    """
    out: list[Ask] = []
    skipped: dict[str, int] = {}
    examined = 0
    for c in comments or []:
        ok, why = is_the_operator(c)
        if not isinstance(c, dict):
            # A malformed row is not a comment anybody wrote; it is not counted
            # as examined, and it is reported rather than swallowed.
            skipped[why] = skipped.get(why, 0) + 1
            continue
        body = (c.get("body") or "").strip()
        if not body:
            continue
        examined += 1
        if not ok:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        login = ((c.get("author") or {}) or {}).get("login") or ""
        out.append(Ask(source=SOURCE_PR_COMMENT, text=body, who=login))
    return out, skipped, examined


#: The two `kind`s of `extract_user_msgs.py` row that are the operator speaking.
#: `decision` is a DECISION he took at an `AskUserQuestion` prompt — 93.5% again
#: on top of `typed`, see that script's own docstring for the measurement. It
#: was named `answer` and gated behind `--include-answers` until devrc#1955 made
#: the channel the default and renamed the kind.
#:
#: 🔴 `decision_unanswered` IS DELIBERATELY ABSENT, AND THE OMISSION IS THE
#: GUARD. That row means "a question was asked and never answered", so its text
#: is the AGENT's question and its option labels — not one word of it is the
#: operator. Admitting it here would quote an agent's own proposal back to the
#: auditor as a requirement the operator stated, which is the exact attribution
#: error round 0 exists to prevent, pointed the other way. Pinned by
#: `test_an_UNANSWERED_question_is_never_quoted_as_an_operator_ask` — and that
#: test is what makes this a branch rather than a comment.
#:
#: 🔴 ONE SPELLING, PINNED TO THE PRODUCER. `extract_user_msgs.KIND_DECISION` is
#: the definition; this module cannot import it (it lives under
#: `scripts/session-analysis/`, not `scripts/lib/`), so it is re-spelled here
#: ONCE and `test_the_decision_kind_is_pinned_to_the_producers_constant` reads
#: the producer's value back out. A kind renamed upstream with this left behind
#: drops every decision silently — the #1955 defect, restored.
KIND_DECISION = "decision"
OPERATOR_KINDS = ("typed", KIND_DECISION)

#: The only machine-generated class that reaches this module. Everything else is
#: removed upstream — see the module docstring for the eight families that were
#: deleted for firing zero times, and why.
TASK_NOTIFICATION_TAG = "<task-notification>"

#: A harness-authored note with no wrapping tag and no header. 13 records / 998 B
#: on this host — kept because it DID fire, and because a subagent-stopped notice
#: reads exactly like the operator speaking.
HARNESS_NOTE_PATTERNS = ("was stopped by the user.",)

#: How far in the note markers are looked for, so an ask ABOUT one survives.
_MARKER_WINDOW = 400

#: 🔴 A `decision` ROW ARRIVES WRAPPED IN AGENT TEXT, AND THAT IS THE SAME CLASS
#: THIS MODULE DELETED THE PR DESCRIPTION FOR. Measured on devrc#1887: two answer
#: rows totalling 1,124 B carried ~440 B of the operator's free-text notes; the
#: rest was the harness's framing sentences, the agent's own question text, its
#: option labels, and — worst — its `selected preview:` block, which is the
#: AGENT'S OWN PLAN. An auditor quoting that attributes the agent's plan to the
#: operator, the exact inversion round 0 exists to catch. Round 1 found it.
#:
#: These are the harness's fixed sentences, stripped so what remains is the
#: question and the answer. The question STAYS — an answer without it is
#: unreadable — and it is labelled in the render as a question the session asked.
ANSWER_FRAMING = (
    "The user answered: ",
    "Your questions have been answered: ",
    "Read the answers carefully — they may request clarification, changes, or "
    "that you not proceed — and follow what they actually say.",
    "You can now continue with these answers in mind.",
)

#: Everything from here on in an answer row is the AGENT's rendering of its own
#: option, not the operator's words.
ANSWER_PREVIEW_MARKER = "selected preview:"


def strip_answer_framing(text: str) -> str:
    """Remove the harness's fixed sentences and the agent's own preview block.

    🔴 ORDER IS LOAD-BEARING: framing FIRST, then the preview cut. The fragments
    end in a space, and cutting the preview first `rstrip`s that space away — so
    a row that was ONLY framing came back as the bare `"The user answered:"`
    instead of empty, and was emitted as an operator ask. Caught by
    `test_an_answer_that_is_ONLY_framing_is_dropped_with_a_reason` on its first
    run, which is the whole reason that test asserts the empty case rather than
    just the happy one.
    """
    out = text or ""
    for frag in ANSWER_FRAMING:
        out = out.replace(frag, "")
    cut = out.find(ANSWER_PREVIEW_MARKER)
    if cut != -1:
        out = out[:cut]
    return out.strip()


def non_operator_reason(text: str) -> str:
    """Why this row is NOT the operator speaking, or "".

    🔴 RETURNS A REASON, NOT A BOOLEAN, so the ledger can name WHAT it dropped
    and how much. This filter decides 98.39% of the bytes; a silent one would
    hide a classifier bug indefinitely.
    """
    t = (text or "").lstrip()
    if not t:
        return "empty"
    if t.startswith(TASK_NOTIFICATION_TAG):
        return f"{TASK_NOTIFICATION_TAG} — a subagent's result, not the operator"
    head = t[:_MARKER_WINDOW]
    for m in HARNESS_NOTE_PATTERNS:
        if m in head:
            return "a harness-authored note, not typed by the operator"
    return ""


def parse_rows(jsonl_text: str) -> tuple[list[Ask], dict]:
    """-> (operator asks, {reason: count}) from `extract_user_msgs.py --jsonl`."""
    out: list[Ask] = []
    dropped: dict[str, int] = {}
    for line in (jsonl_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # Not silently lost: counted as a drop REASON, so a corrupt stream
            # shows in the ledger rather than as a short block.
            dropped["an unparseable --jsonl line"] = (
                dropped.get("an unparseable --jsonl line", 0) + 1)
            continue
        if not isinstance(row, dict):
            continue
        if row.get("kind") not in OPERATOR_KINDS:
            continue
        text = (row.get("text") or "").strip()
        reason = non_operator_reason(text)
        if reason:
            if reason != "empty":
                dropped[reason] = dropped.get(reason, 0) + 1
            continue
        kind = row.get("kind") or ""
        if kind == KIND_DECISION:
            text = strip_answer_framing(text)
            if not text:
                dropped["an answer row that was entirely harness framing"] = (
                    dropped.get("an answer row that was entirely harness framing", 0) + 1)
                continue
        out.append(Ask(
            source=SOURCE_SESSION,
            text=text,
            session_id=row.get("session_id") or "",
            role=row.get("arc_role") or "",
            kind=kind,
        ))
    return out, dropped


#: `extract_user_msgs.py`'s own exit vocabulary, so a non-zero becomes a REASON
#: rather than "the command failed". The numbers live here once and
#: `test_the_extractor_exit_vocabulary_is_pinned_to_what_the_script_documents`
#: reads them back OUT of that script's `--help`, two-way.
EXTRACTOR_REASONS = {
    2: "bad invocation of the extractor, or its output could not be written",
    3: "the arc seed named no handoff doc, or no checkout holds it (NOTHING MEASURED)",
    4: "the arc was measured and has zero member sessions",
    5: "those session ids resolved to no readable transcript on this host",
    6: "the transcripts were read and held no operator-typed message",
}


#: Lines the extractor writes to stderr while STILL EXITING 0. They are prefixed
#: `!` by that tool (`print(f"! {note}", file=err)`), which is the marker this
#: reads — the prefix is the contract, the wording is that tool's to change.
#: 🔴 A rc-0 RUN WITH ONE OF THESE IS USUALLY A PARTIAL READ, and treating it as
#: complete is what suppressed the UNKNOWN directive on a half-resolved set.
_COVERAGE_NOTE_PREFIX = "!"

#: 🔴 …EXCEPT ONE FAMILY, WHICH IS INFORMATION AND NOT A GAP. The extractor also
#: emits, with the same `!`, "N session(s) are ABSENT from this report because
#: dedup suppressed every one of their messages as a repeat of another session's
#: — **they are not empty**". Those messages WERE read and ARE in the output,
#: attributed to the other session. Reading it as a gap puts a false UNKNOWN and
#: the directive on a COMPLETE read — and dedup is on by default, so it fires on
#: any PR whose trailers name two sessions of one arc (the extractor's own comment
#: calls that the ordinary case: "the same kickoff pasted into every resumed
#: session"). A permanent UNKNOWN is a permanently-red gate: it teaches the reader
#: to skip the line that matters. Round 2 of the devrc#1887 ladder found it.
#:
#: ⚠ THIS IS A WORDING DEPENDENCY, WHICH THE PREFIX RULE OTHERWISE AVOIDS — so it
#: is pinned TWO-WAY against the extractor's own source by
#: `test_the_dedup_note_wording_is_pinned_to_the_extractor` — which asserts the
#: tuple is NON-EMPTY first, because `for marker in ()` executes zero assertions
#: and would pass vacuously. If that tool rewords
#: the note the test fails LOUDLY rather than this silently reverting to a false
#: UNKNOWN. And the fail-safe direction is preserved: an `!` line this does not
#: recognise is treated as a GAP, so a reword over-reports rather than hiding one.
NOT_A_GAP_MARKERS = ("they are not empty",)


def coverage_notes(stderr: str) -> list[str]:
    """The extractor's rc-0 partial-coverage notes, as reasons for `Unmeasured`.

    Reads the `!`-prefixed lines that tool emits. The prefix is the contract and
    the sentences are that tool's to change — with ONE exception, which this
    function does depend on the wording of: `NOT_A_GAP_MARKERS` excludes the dedup
    note, and its comment explains why and how it is pinned. ⚠ This docstring used
    to say "rather than matching their wording" flatly, four lines above a body
    that matches wording — so a reader editing the extractor's note and checking
    only here would conclude they were safe. An
    empty result means it reported full coverage — which is a READING, not an
    assumption, because a rc-0 run with no notes read everything it selected.
    """
    out: list[str] = []
    for line in (stderr or "").splitlines():
        t = line.strip()
        if not (t.startswith(_COVERAGE_NOTE_PREFIX) and len(t) > 1):
            continue
        note = t.lstrip("! ").strip()
        if any(m in note for m in NOT_A_GAP_MARKERS):
            continue        # information, not a gap — see NOT_A_GAP_MARKERS
        out.append(note)
    return out


def extractor_reason(rc: int, stderr: str = "") -> str:
    """A human reason for a non-zero extractor exit. Never 'no asks'."""
    base = EXTRACTOR_REASONS.get(rc, f"the extractor exited {rc}")
    tail = (stderr or "").strip().splitlines()
    if tail:
        return f"{base} — it said: {tail[-1].strip()}"
    return base


def transcript_paths(ids: Sequence[str], projects_root=None) -> list[str]:
    """Every on-disk transcript for these session ids, for the agent-side route.

    🔴 DELEGATES TO `transcript_search.find_transcript` — THE CANONICAL BY-ID
    LOOKUP — AND MUST NOT GLOB ITSELF. An earlier revision globbed
    `*/<id>.jsonl` here, which failed
    `test_the_jsonl_glob_site_ledger_is_pinned_two_way`: that ledger pins every
    `*.jsonl` walk in the tree two-way, so a private one is a failure by
    construction. **The PR head was RED from its first commit** with CI saying so
    on every push, and three rounds missed it because `test_transcript_search.py`
    never contains the string `operator_asks` — no name-based selection can reach
    it (`claude/RULES.md`'s isolation-seam class, again).

    ⚠ AND THE FIRST JUSTIFICATION WRITTEN HERE FOR DELEGATING WAS FALSE. Round 3
    argued the private glob "could resolve a `subagents/` or `wf_` transcript that
    `find_transcript` reports as absent". MEASURED: an excluded transcript lives at
    `<project>/<session-id>/subagents/<id>.jsonl`, three levels down, and the old
    pattern was `*/` — one level. It matched 965 files on this host, **0** of them
    in an excluded directory, so that divergence was unreachable. Delegation is
    right for the ledger and for one-rule-one-place; it was never a live
    corpus-membership bug. 🔴 Note the direction of the change: `find_transcript`
    globs `**/`, so delegating WIDENS the search to depths the old code could not
    see — and is safe only because `is_corpus_member` filters them. That filter is
    now load-bearing here, which it was not before.

    🔴 THE GLOB-METACHARACTER GUARD STAYS, AND IS NOW A PRECONDITION ON THE
    CALLEE. `find_transcript` globs the id UNESCAPED, so an id holding `*` or `[`
    would match another session's transcript there just as it did here.
    `session_trailer.valid_id` does not reject those — they are not control
    characters — so they are rejected explicitly before delegating. Neither guard
    inspects the id's SHAPE: a `ses_…` token from another runtime is legitimate
    and opaque.
    """
    out: list[str] = []
    for sid in ids:
        if not session_trailer.valid_id(sid):
            continue
        if any(c in sid for c in _GLOB_METACHARACTERS):
            continue
        found = transcript_search.find_transcript(sid, root=projects_root)
        if found:
            out.append(str(found))
    return out


def agent_side_reference(ids: Sequence[str], projects_root=None) -> list[str]:
    """Says this carries the operator ONLY, and how to reach the rest.

    Requested explicitly by the operator — *"dont include agent responses, but
    include a reference on how to pull the agent responses if needed"* — so both
    halves of that sentence are implemented here, and neither is invented.
    """
    lines = [
        "**This block is the operator's own words ONLY — no agent or tool "
        "output, by design.** If you need the agent side of one of these "
        "sessions to judge a requirement, it is not in this brief; read it "
        "yourself:",
    ]
    paths = transcript_paths(ids, projects_root=projects_root)
    if not paths:
        lines.append(
            "  ⚠ no transcript file for these ids on this host, so the agent "
            "side is NOT reachable from here — say so rather than treating its "
            "absence as agreement."
        )
        return lines
    for p in paths:
        lines.append(f"  {p}")
    lines.append(
        "  jq -r 'select(.type==\"assistant\") | "
        "(.message.content[]? | select(.type==\"text\") | .text)' <path>"
    )
    return lines


def asks_by_source(asks: Sequence[Ask], source: str) -> list[Ask]:
    """The asks from one source. Public so the ledger can count without
    re-deriving the grouping `_render_asks` already does."""
    return [a for a in asks if a.source == source]


def _render_asks(asks: Sequence[Ask]) -> tuple[list[str], dict]:
    """The verbatim ask bodies, grouped by source then session."""
    lines: list[str] = []
    led = {"messages": 0, "bytes": 0, "answers": 0}

    by_source: dict[str, list[Ask]] = {}
    for a in asks:
        by_source.setdefault(a.source, []).append(a)

    for source in SOURCE_ORDER:
        group = by_source.get(source)
        if not group:
            continue
        lines += ["", f"### from the {source}"]
        last_key = None
        for a in group:
            key = (a.session_id, a.who)
            if key != last_key and (a.session_id or a.who):
                label = safe_label(a.session_id) if a.session_id else a.who
                role = f" ({a.role})" if a.role else ""
                lines += ["", f"**{label}{role}**"]
                last_key = key
            led["messages"] += 1
            led["bytes"] += len(a.text)
            if a.kind == KIND_DECISION:
                led["answers"] += 1
                # Labelled because it is a DECISION rather than a free-text
                # ask: the operator picked an option, sometimes with a note.
                lines.append("_(an answer to a question this session asked)_")
            for ln in a.text.splitlines() or [""]:
                lines.append(f"> {ln}")
            lines.append("")
    return lines, led


def render(asks: Sequence[Ask], unmeasured: Sequence[Unmeasured] = (),
           session_ids: Sequence[str] = (), comment_skips: dict | None = None,
           comments_examined: int | None = None,
           dropped: dict | None = None, projects_root=None) -> str:
    """The round-0 asks block. NEVER returns a quiet empty block.

    🔴 No path through this function renders nothing. With no asks AND no
    unmeasured sources — meaning the caller consulted no source at all — it says
    exactly that, because "we looked nowhere" and "the operator asked for
    nothing" are different facts and only one of them licenses a deletion
    candidate.
    """
    lines = [HEADING, ""]

    if asks:
        lines += [
            "Verbatim, for step 1's **author of record**. Where a requirement "
            "in the diff answers one of these, its author is the operator — "
            "**quote it** and do not propose deleting it on the grounds that "
            "nobody asked. These are asks, not a specification: an ask can "
            "still be questioned (that is step 1's job), but it must be "
            "questioned AS the operator's, out loud.",
        ]
    else:
        lines += [
            "🔴 **NO OPERATOR ASK COULD BE READ FOR THIS PR.** Every source is "
            "listed below with the reason it did not answer.",
        ]

    body, led = _render_asks(asks)
    lines += body

    lines += ["", "**Sources read:**"]
    if session_ids:
        # 🔴 "NAMED BY", NOT "RESOLVED". An earlier revision said "resolved",
        # which reads as "resolved to a transcript" — they were only read out of
        # trailers, and whether each has a readable transcript on THIS host is a
        # separate fact the `!` lines below carry. Round 1 found the wording.
        lines.append(
            f"  {SOURCE_SESSION}: {len(session_ids)} session(s) NAMED BY "
            f"`Claude-Session-Id:` trailers in the PR's commit BODIES — "
            + ", ".join(safe_label(s) for s in session_ids)
        )
    for reason, n in sorted((dropped or {}).items(), key=lambda kv: -kv[1]):
        # 🔴 REASONS, NOT A BARE COUNT. This filter decides 98.39% of the bytes,
        # so a reader must be able to see whether what it dropped SHOULD have
        # been dropped.
        lines.append(f"  {SOURCE_SESSION}: dropped {n} record(s) — {reason}")
    if comments_examined is not None:
        # Printed even at ZERO, so "consulted and empty" is distinguishable from
        # "not consulted" — they read identically without this line.
        lines.append(
            f"  {SOURCE_PR_COMMENT}: {comments_examined} comment(s) examined, "
            f"{len(asks_by_source(asks, SOURCE_PR_COMMENT))} from the operator"
        )
        lines.append(
            f"  ! {SOURCE_PR_COMMENT}: `gh pr view --json comments` returns "
            "ISSUE comments ONLY — an ask left as a REVIEW comment, inside a "
            "review thread, or in a review body is NOT visible here and its "
            "absence is UNKNOWN, not zero."
        )
    for why, n in sorted((comment_skips or {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"  {SOURCE_PR_COMMENT}: {n} skipped — {why}")
    for u in unmeasured:
        lines.append(f"  ! {u.source}: UNKNOWN — {u.reason}")
    if not session_ids and not unmeasured and not asks:
        lines.append(
            "  ! no source was consulted at all — that is a fact about this "
            "assembly, not about the operator."
        )

    answers = f" · of which answers to a question: {led['answers']}" if led["answers"] else ""
    lines += [
        "",
        f"**Ledger:** operator asks: {led['messages']} ({led['bytes']:,} B)"
        f"{answers}",
    ]
    if session_ids:
        # Always printed, not only on a truncation: the auditor may want the
        # operator's words from a session the trailers named even when this
        # block already inlined them, and there is no clipping any more.
        cmd = " ".join(
            f"--session {shlex.quote(s)}"
            for s in session_ids if session_trailer.valid_id(s)
        )
        if cmd:
            # ⚠ `--include-answers` IS A NO-OP AGAINST THE CURRENT EXTRACTOR —
            # devrc#1955 made the decision channel the default. It is still
            # printed because this line is copied and run by a human, possibly
            # on a host whose deployed copy predates that change, where the flag
            # is the difference between seeing his decisions and not. Pinned by
            # `test_the_reread_command_carries_include_answers`.
            lines.append(
                "  Re-read them yourself: python3 $DEVRC/scripts/"
                f"session-analysis/extract_user_msgs.py --include-answers {cmd}"
            )

    if unmeasured or not asks:
        lines += ["", UNKNOWN_DIRECTIVE]

    lines += [
        "",
        "🔴 **DO NOT PUBLISH A QUOTED ASK — ON ANY SURFACE THAT LEAVES THIS "
        "MACHINE.** That includes a tracked file, a commit message, **a PR "
        "comment or review**, and anything posted to an external service. This "
        "repo is PUBLIC and `CLAUDE.md` forbids committing captured text — "
        "message bodies, prompts, transcript content — however it arrives; no "
        "gate covers `.md` or a PR comment, so it is yours to hold. Quote an ask "
        "only in what you hand back to the operator; paraphrase or elide it "
        "everywhere else. ⚠ An earlier wording NAMED PR comments as a "
        "destination and then scoped the rule to files, which permitted the "
        "exposure its own heading forbids.",
    ]
    lines += [""] + agent_side_reference(session_ids, projects_root=projects_root)
    return "\n".join(lines)
