#!/usr/bin/env python3
"""The operator's own asks, as attribution input for `/audit-pr`'s ROUND 0.

WHAT PROBLEM THIS SOLVES. Round 0 step 1 says "Question every requirement, and
NAME its author of record: Zach (quote the ask), a prior audit round, a
`RULES.md`/`CLAUDE.md` bullet, or **unattributed**". The auditor is dispatched
READ-ONLY with a diff and a PR — it has never had any way to READ what the
operator asked for, so "quote the ask" was unreachable and every requirement
whose author was Zach landed on `unattributed`, which the section then treats as
a finding. The failure that produces is a round-0 deletion candidate aimed at
something the operator asked for in as many words.

🔴 THE CENTRAL HAZARD IS THE EMPTY CASE, NOT THE FULL ONE. Trailer coverage on
`main` is partial — `session_trailer.py` measures 47 of the last 100 and 67 of
the last 200 carrying one, and a re-measure here on 2026-09-26 over the 60
newest commits found 35 by a BODY scan against 17 by git's own trailer parser.
So "no asks could be read" is a COMMON outcome, and an empty block that reads as
"the operator asked for nothing" would actively LICENSE the deletion suggestion
this module exists to prevent. Every unreadable source therefore renders as a
named UNKNOWN with an explicit instruction, never as an absence. `render()` has
no code path that emits a quiet empty block.

🔴 READ THE COMMIT **BODY**, NEVER git's TRAILER PARSER. Measured 2026-09-26 on
`c0fd28e3`, the squash merge of devrc#1883: `git log -1
--format='%(trailers:key=Claude-Session-Id,valueonly)'` prints EMPTY while the
body holds 12 occurrences of `Claude-Session-Id: ad781c3f-…`. GitHub's squash
body concatenates every squashed commit message, so the ids do not sit in git's
final trailer block. That is why this module calls
`handoff_arc.trailer_ids(body)` and not `git --format=%(trailers:…)`: same
predicate, one definition, and the one that can see a squash.

🔴 THE HARD PART IS THE CLASSIFIER, NOT THE BUDGET — AND `kind: "typed"` IS NOT
THE OPERATOR. `extract_user_msgs.py` emits a row per user-ROLE transcript
record, but Claude Code injects several kinds of machine-generated content as
user-role records, and they dwarf the human. MEASURED 2026-09-26 over 981
sessions / 23,734 user-role text records / 139,541,944 B on this host:

    injected SKILL BODIES  83,065,007 B  59.5%   median 25,255 B   (n=3,039)
    <task-notification>    54,311,593 B  38.9%   median  5,446 B   (n=9,971)
    hook feedback             822,615 B   0.6%   median  1,030 B   (n=  631)
    <command-message/name/local-command-*>
                              161,000 B   0.1%
    THE OPERATOR            1,175,592 B   0.84%  median     18 B   (n=8,663)

**The operator is 0.84% of it, with a median message of 18 bytes.** A round-0
block built on `kind == "typed"` alone is therefore ~99% subagent results and
skill bodies — it would bury the ask it exists to surface, and an auditor
skimming it would attribute requirements to text the operator never wrote.
`non_operator_reason()` is the enumeration that removes them, and it names the
reason per record so the ledger can report what it dropped.

⚠ THE SKILL-BODY CLASS IS THE ONE THAT IS EASY TO MISS: it carries no wrapping
tag, so it looks exactly like prose until you notice it is 25 KB of `<topic>` /
`<repo>` / `<sha>` placeholders. It is identified by the literal header Claude
Code puts above an invoked skill, not by size — a long ask must survive.

THE CAP IS A BACKSTOP, NOT A BUDGET. With the classifier in place the operator's
own messages are tiny: p90 245 B, p99 1,254 B, max 16,404 B across all 8,663.
`PER_MESSAGE_CAP = 4000` clips 45 of 8,663 (0.5%). It exists so one pathological
paste cannot dominate a brief, NOT to control the corpus size — that problem was
an artefact of counting machine output as the operator.

🔴 AN EARLIER VERSION OF THIS MODULE GOT THIS WRONG AND THE NUMBERS ARE KEPT SO
NOBODY RE-DERIVES THEM. It measured 812 B - 239,589 B of "operator messages" per
PR and concluded the bulk was the operator PASTING tool output, sizing a
per-message cap against that. Both halves were false: the bulk was
`<task-notification>` and skill bodies, and the operator's real text for those
same PRs is a couple of KB. The live end-to-end run is what exposed it — the
unit tests all passed, because every fixture was a hand-written ask.

🔴 NEVER SUMMARISE AN ASK. A paraphrase of "the user directly asked for this" is
exactly the evidence that goes missing, so this module clips and elides but has
no summarising path at all. Clipping is reported in bytes and messages so the
auditor knows what it is NOT seeing.

WHAT THIS DELIBERATELY DOES NOT CARRY: any agent/assistant output. The operator
asked for their own messages only. The agent side is reachable and
`agent_side_reference()` prints how, per session, rather than leaving the
auditor to guess a path.
"""

from __future__ import annotations

import glob as _glob
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import handoff_arc
import session_trailer

# Bytes of each message kept, clipping the TAIL. A BACKSTOP against one
# pathological paste, not a budget: measured over 8,663 real operator messages
# this clips 45 of them (0.5%). See the module docstring.
PER_MESSAGE_CAP = 4000

# Backstop on the whole rendered ask corpus. Did not fire on any of the 7 PRs
# measured; it exists so brief size cannot be unbounded in a population those 7
# did not contain.
BLOCK_CEILING = 49152

# What a clipped message says. Counted in the ledger so the elision is a number,
# not a vibe.
CLIP_MARK = "  […  clipped {withheld:,} B of {full:,} — read the full text, below …]"

SOURCE_SESSION = "session transcript"
SOURCE_PR_BODY = "PR description"
SOURCE_PR_COMMENT = "PR comment"

# Ordered most- to least-authoritative. A session transcript is what the
# operator actually typed while the work happened; a PR description is often
# written BY the agent about the work, so it is read but ranked below and
# labelled, never merged into the session rows.
SOURCE_ORDER = (SOURCE_SESSION, SOURCE_PR_BODY, SOURCE_PR_COMMENT)

HEADING = "## THE OPERATOR'S OWN ASKS — attribution input for step 1"

# The instruction that makes an UNKNOWN safe. It is a STRING CONSTANT because
# two tests pin it: one that it appears whenever any source is unreadable, and
# one that it appears even when SOME asks were read (a partial read is still a
# partial read, and the sources that failed are the ones a deletion candidate
# would be wrong about).
UNKNOWN_DIRECTIVE = (
    "🔴 **A SOURCE THAT COULD NOT BE READ IS NOT AN ABSENCE OF ASKS.** Where a "
    "source below says UNKNOWN, you have no evidence either way about what the "
    "operator asked for — so record such a requirement as "
    "`UNATTRIBUTED-UNKNOWN`, which is NOT the same as `unattributed`, and do "
    "**not** raise a deletion candidate whose whole case is that nobody asked "
    "for it. Say the source was unreadable instead."
)

# Bots that comment on devrc PRs. A bot comment is not an ask, and letting one
# through would attribute a requirement to the operator that they never made.
# An enumeration, not a pattern: an unknown author is treated as the OPERATOR
# (the fail-safe direction here is to over-include an ask, never to drop one),
# and the ledger prints who was skipped so the enumeration is visible.
KNOWN_BOT_LOGINS = frozenset({
    "github-actions", "github-actions[bot]", "dependabot", "dependabot[bot]",
    "codecov", "codecov[bot]", "claude", "claude[bot]", "copilot",
    "copilot-pull-request-reviewer[bot]",
})


@dataclass(frozen=True)
class Ask:
    """One thing the operator said, and where it was read from."""

    source: str
    text: str
    who: str = ""
    session_id: str = ""
    role: str = ""

    @property
    def full_len(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class Unmeasured:
    """A source that was consulted and did not answer. NEVER a zero."""

    source: str
    reason: str


def session_ids_from_bodies(bodies: Iterable[str]) -> tuple[str, ...]:
    """Distinct session ids across several commit bodies, first-appearance order.

    Delegates the per-body scan to `handoff_arc.trailer_ids`, which is the one
    definition of "a safe session-id trailer in a commit body" and the one that
    sees a squash. De-duping ACROSS bodies is this function's only addition: a
    squash body plus the branch's own commits name the same session repeatedly.
    """
    out: list[str] = []
    for body in bodies:
        for sid in handoff_arc.trailer_ids(body or ""):
            if sid not in out:
                out.append(sid)
    return tuple(out)


def clip(text: str, cap: int = PER_MESSAGE_CAP) -> tuple[str, int]:
    """-> (kept, withheld_bytes). Clips the TAIL; the head is the instruction.

    Returns the text unchanged and 0 when it fits, so a caller can report
    "clipped" off the second element alone without re-comparing lengths.

    A non-positive cap keeps nothing and reports the whole message withheld,
    rather than raising: a misconfigured cap must degrade into a loud ledger
    line, not into a traceback in the middle of assembling a brief.
    """
    if cap <= 0:
        return "", len(text)
    if len(text) <= cap:
        return text, 0
    return text[:cap], len(text) - cap


def safe_label(sid: str, width: int = 8) -> str:
    """A short, PRINTABLE handle for a session id.

    Every id this module renders goes through here. `session_trailer.valid_id`
    is the safety gate — an id it refuses renders as `<unsafe-id>` rather than
    reaching a terminal, because the brief is printed to a tty and an escape
    sequence in a commit body is a real observed hazard (`handoff_arc`'s own
    comment records `\\x1b[2J\\x1b]0;PWNED\\x07` reaching terminals raw from
    commit bodies in four repos).

    The truncation is COSMETIC and carries no shape claim: ids from other
    runtimes are not uuids and a short prefix is still a usable label.
    """
    if not session_trailer.valid_id(sid):
        return "<unsafe-id>"
    return sid[:width]


def is_bot(login: str) -> bool:
    """Is this comment author a known bot?

    Case-folded because GitHub logins are case-insensitive and the ledger
    prints what it skipped. An UNKNOWN login is NOT a bot: over-including one
    ask is recoverable, dropping the operator's own ask is the failure this
    module exists to prevent.
    """
    return (login or "").strip().lower() in KNOWN_BOT_LOGINS


def asks_from_comments(comments: Sequence[dict]) -> tuple[list[Ask], int]:
    """-> (operator asks, bots_skipped). `comments` is `gh pr view --json comments`."""
    out: list[Ask] = []
    skipped = 0
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        login = ((c.get("author") or {}) or {}).get("login") or ""
        body = (c.get("body") or "").strip()
        if not body:
            continue
        if is_bot(login):
            skipped += 1
            continue
        out.append(Ask(source=SOURCE_PR_COMMENT, text=body, who=login))
    return out, skipped


#: Machine-generated content Claude Code injects as a user-ROLE record, keyed by
#: the LEADING tag of the record. An ENUMERATION, derived by walking 981
#: sessions rather than guessed — see the module docstring for the byte shares.
#: An unlisted tag is treated as the OPERATOR, which is the safe direction here:
#: over-including one record is recoverable, dropping the ask is the defect.
INJECTED_LEADING_TAGS = (
    "task-notification",    # a SUBAGENT's result — 38.9% of all user-role bytes
    "command-message",      # a slash command expanding
    "command-name",
    "local-command-stdout",
    "local-command-caveat",
    "system-reminder",
)

#: The literal header Claude Code writes above an invoked skill's body. THE
#: HARDEST CLASS TO SPOT: a skill body carries no wrapping tag, so it reads as
#: prose until you notice it is 25 KB of `<topic>`/`<repo>` placeholders. 59.5%
#: of all user-role bytes. Matched on this HEADER and never on size, because a
#: long ask must survive.
SKILL_BODY_HEADER = "Base directory for this skill:"

#: A blocking hook's message, replayed to the model as a user record.
HOOK_FEEDBACK_MARKERS = ("Stop hook feedback:", "PostToolUse:", "PreToolUse:")

#: Harness-authored notes that sit in a user-role record with no wrapping tag
#: and no header. Found by the LIVE run, not by any fixture — which is the
#: argument for keeping this an open enumeration rather than claiming it closed.
#: `PREFIXES` is anchored at the start; `CONTAINS` is not, because the agent
#: name in the middle varies.
INTERRUPT_PREFIX = "[Request interrupted"
HARNESS_NOTE_PATTERNS = (
    'was stopped by the user.',      # "Background agent \"...\" was stopped by the user."
    'Caveat: The messages below were generated',
)

#: How far into a record the non-tag markers are looked for. Bounded so a record
#: that merely QUOTES one of these strings further down — an ask ABOUT a hook, or
#: about this very module — is not misclassified as machine output.
_MARKER_WINDOW = 400


def non_operator_reason(text: str) -> str:
    """Why this user-role record is NOT the operator speaking, or "".

    🔴 RETURNS A REASON, NOT A BOOLEAN, so the ledger can say WHAT it dropped
    and how much. A count of "records skipped" with no reason is the kind of
    silent filter that hides a classifier bug for weeks.
    """
    t = (text or "").lstrip()
    if not t:
        return "empty"
    for tag in INJECTED_LEADING_TAGS:
        if t.startswith(f"<{tag}>"):
            return f"<{tag}> — machine-generated, not typed by the operator"
    head = t[:_MARKER_WINDOW]
    if SKILL_BODY_HEADER in head:
        return "an injected SKILL BODY — instructions the harness delivered"
    for m in HOOK_FEEDBACK_MARKERS:
        if m in head:
            return f"hook feedback ({m.rstrip(':')}) — a guard speaking, not the operator"
    if t.startswith(INTERRUPT_PREFIX):
        return "an interrupt marker written by the harness"
    for m in HARNESS_NOTE_PATTERNS:
        if m in head:
            return "a harness-authored note, not typed by the operator"
    return ""


def parse_typed_rows(jsonl_text: str) -> tuple[list[Ask], dict]:
    """-> (operator asks, {reason: count}) from `extract_user_msgs.py --jsonl`.

    🔴 `kind == "typed"` IS NOT THE OPERATOR — it is "this was a user-role
    record", and 99.16% of those bytes on this host are subagent results and
    injected skill bodies. So the row kind is a necessary filter and nowhere
    near a sufficient one; `non_operator_reason` is the rest of it.

    The `command` kind is dropped too — a bare `/slash` (measured at 8 B) is an
    invocation, not an ask.
    """
    out: list[Ask] = []
    dropped: dict[str, int] = {}
    for line in (jsonl_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # A single unparseable line is not a reason to lose the rest. It is
            # not silently dropped either: it is counted as a drop REASON, so a
            # corrupt stream shows up in the ledger instead of as a short block.
            dropped["an unparseable --jsonl line"] = (
                dropped.get("an unparseable --jsonl line", 0) + 1)
            continue
        if not isinstance(row, dict):
            continue
        if row.get("kind") != "typed":
            continue
        text = (row.get("text") or "").strip()
        reason = non_operator_reason(text)
        if reason:
            if reason != "empty":
                dropped[reason] = dropped.get(reason, 0) + 1
            continue
        out.append(Ask(
            source=SOURCE_SESSION,
            text=text,
            session_id=row.get("session_id") or "",
            role=row.get("arc_role") or "",
        ))
    return out, dropped


# `extract_user_msgs.py`'s own exit vocabulary, so a non-zero becomes a REASON a
# human can act on instead of "the command failed". Imported as prose rather
# than by `from … import` because that script is not an importable module path
# (a hyphenated directory sibling), and a second copy of the NUMBERS would be a
# ledger to keep in step — so the numbers live here once, keyed to the reason
# strings that script prints, and `test_the_extractor_exit_vocabulary_is_pinned`
# reads them back OUT of the script's own `--help`.
EXTRACTOR_REASONS = {
    2: "bad invocation of the extractor, or its output could not be written",
    3: "the arc seed named no handoff doc, or no checkout holds it (NOTHING MEASURED)",
    4: "the arc was measured and has zero member sessions",
    5: "those session ids resolved to no readable transcript on this host",
    6: "the transcripts were read and held no operator-typed message",
}


def extractor_reason(rc: int, stderr: str = "") -> str:
    """A human reason for a non-zero extractor exit. Never 'no asks'."""
    base = EXTRACTOR_REASONS.get(rc, f"the extractor exited {rc}")
    tail = (stderr or "").strip().splitlines()
    if tail:
        return f"{base} — it said: {tail[-1].strip()}"
    return base


def transcript_paths(ids: Sequence[str], projects_root=None) -> list[str]:
    """Every on-disk transcript for these session ids, for the agent-side route.

    Globbed rather than constructed: a session's project directory is derived
    from its cwd and this module has no business re-deriving that mangling. A
    session with no file on THIS host simply contributes nothing, which
    `agent_side_reference` reports as a count rather than implying the session
    does not exist.

    🔴 TWO SEPARATE GUARDS, because they cover different characters and
    `handoff_arc`'s own comment is explicit that a copy of one is not the other.
    `session_trailer.valid_id` is the WRITER'S OWN safety predicate — it rejects
    every C0 control, which is what could reach a terminal raw — and it is
    called, never re-spelled. It does **not** reject glob metacharacters, which
    are not controls, so `glob.escape` covers the second hazard: an id holding
    `*` or `[` would otherwise match transcripts belonging to other sessions and
    the brief would attribute their asks to this PR. Neither guard inspects the
    id's SHAPE — a `ses_…` token from another runtime is a legitimate value and
    an opaque one.
    """
    root = Path(projects_root) if projects_root else Path.home() / ".claude" / "projects"
    out: list[str] = []
    for sid in ids:
        if not session_trailer.valid_id(sid):
            continue
        for p in sorted(root.glob(f"*/{_glob.escape(sid)}.jsonl")):
            out.append(str(p))
    return out


def agent_side_reference(ids: Sequence[str], projects_root=None) -> list[str]:
    """The block saying this carries the operator ONLY, and how to get the rest.

    Requested explicitly by the operator: user messages only, with a reference
    for pulling the agent side "if needed". It is a REFERENCE and not an
    inlined extraction because agent output is the bulk of a transcript and
    round 0 is about what was ASKED, not what was answered.
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


def _render_asks(asks: Sequence[Ask], cap: int, ceiling: int) -> tuple[list[str], dict]:
    """The verbatim ask bodies, grouped by source then session. -> (lines, ledger)."""
    lines: list[str] = []
    led = {"messages": 0, "clipped": 0, "withheld": 0, "inlined": 0,
           "dropped_to_ceiling": 0}
    spent = 0
    ceiling_hit = False

    by_source: dict[str, list[Ask]] = {}
    for a in asks:
        by_source.setdefault(a.source, []).append(a)

    for source in SOURCE_ORDER:
        group = by_source.get(source)
        if not group:
            continue
        lines += ["", f"### from the {source}"]
        if source == SOURCE_PR_BODY:
            # 🔴 SAY WHOSE WORDS THESE ARE. A PR description in this repo is
            # usually written BY THE AGENT about the work, so filing it under a
            # heading that says "the operator's own asks" would manufacture an
            # author of record. It is read because it is the ONLY source on a PR
            # whose commits carry no trailer — measured, 25 of the 60 newest
            # `main` commits — not because it is authoritative.
            lines += [
                "⚠ A PR description here is usually written **by the agent** "
                "about the work, not typed by the operator. Treat it as "
                "context, and do NOT record it as `Zach (quote the ask)` unless "
                "the wording is plainly his.",
            ]
        last_key = None
        for a in group:
            key = (a.session_id, a.who)
            if key != last_key and (a.session_id or a.who):
                label = safe_label(a.session_id) if a.session_id else a.who
                role = f" ({a.role})" if a.role else ""
                lines += ["", f"**{label}{role}**"]
                last_key = key
            if ceiling_hit:
                led["dropped_to_ceiling"] += 1
                continue
            kept, withheld = clip(a.text, cap)
            if spent + len(kept) > ceiling:
                ceiling_hit = True
                led["dropped_to_ceiling"] += 1
                continue
            spent += len(kept)
            led["messages"] += 1
            led["inlined"] += len(kept)
            if withheld:
                led["clipped"] += 1
                led["withheld"] += withheld
            for ln in kept.splitlines() or [""]:
                lines.append(f"> {ln}")
            if withheld:
                lines.append(CLIP_MARK.format(withheld=withheld, full=a.full_len))
            lines.append("")
    return lines, led


def render(asks: Sequence[Ask], unmeasured: Sequence[Unmeasured] = (),
           session_ids: Sequence[str] = (), bots_skipped: int = 0,
           dropped: dict | None = None,
           cap: int = PER_MESSAGE_CAP, ceiling: int = BLOCK_CEILING,
           projects_root=None) -> str:
    """The round-0 asks block. NEVER returns a quiet empty block.

    🔴 There is no path through this function that renders nothing. With no
    asks AND no unmeasured sources — which means the caller consulted no source
    at all — it says exactly that, because "we looked nowhere" and "the
    operator asked for nothing" are different facts and only one of them
    licenses a deletion candidate.
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

    body, led = _render_asks(asks, cap, ceiling)
    lines += body

    lines += ["", "**Sources read:**"]
    if session_ids:
        lines.append(
            f"  {SOURCE_SESSION}: {len(session_ids)} session(s) resolved from "
            f"`Claude-Session-Id:` trailers in the PR's commit BODIES — "
            + ", ".join(safe_label(s) for s in session_ids)
        )
    for reason, n in sorted((dropped or {}).items(), key=lambda kv: -kv[1]):
        # 🔴 REASONS, NOT A BARE COUNT. 99.16% of user-role bytes on this host
        # are machine-generated, so this filter does nearly all the work and a
        # silent one would hide a classifier bug indefinitely. A reader can see
        # here whether the thing that was dropped SHOULD have been.
        lines.append(f"  {SOURCE_SESSION}: dropped {n} record(s) — {reason}")
    if bots_skipped:
        lines.append(
            f"  {SOURCE_PR_COMMENT}: {bots_skipped} bot comment(s) skipped "
            "(an enumerated login list; an UNKNOWN author is kept as the "
            "operator, never dropped)"
        )
    for u in unmeasured:
        lines.append(f"  ! {u.source}: UNKNOWN — {u.reason}")
    if not session_ids and not unmeasured and not asks:
        lines.append(
            "  ! no source was consulted at all — that is a fact about this "
            "assembly, not about the operator."
        )

    lines += [
        "",
        f"**Ledger:** operator asks: {led['messages']} inlined "
        f"({led['inlined']:,} B) · clipped: {led['clipped']} "
        f"({led['withheld']:,} B withheld) · dropped to the block ceiling: "
        f"{led['dropped_to_ceiling']}",
    ]
    if led["clipped"] or led["dropped_to_ceiling"]:
        lines.append(
            "  Clipping keeps each message's HEAD, which is where the "
            "instruction is; the tail is usually pasted output. Withheld text "
            "is NOT absent ask — pull the full set with:"
        )
        if session_ids:
            # 🔴 QUOTED AT THE RENDER POINT. `handoff_arc`'s own comment says
            # the hazard lives here and not in the safety filter: this string is
            # printed for a human to paste into a shell, so the id is quoted
            # rather than interpolated bare. Safety-filtered too — an id that
            # `valid_id` refuses is dropped from the command instead of being
            # quoted into it, because quoting does not neutralise an escape
            # sequence written to a tty.
            cmd = " ".join(
                f"--session {shlex.quote(s)}"
                for s in session_ids if session_trailer.valid_id(s)
            )
            if cmd:
                lines.append(
                    "  python3 $DEVRC/scripts/session-analysis/"
                    f"extract_user_msgs.py {cmd}"
                )

    if unmeasured or not asks:
        lines += ["", UNKNOWN_DIRECTIVE]

    lines += [""] + agent_side_reference(session_ids, projects_root=projects_root)
    return "\n".join(lines)
