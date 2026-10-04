#!/usr/bin/env python3
"""Extract what the OPERATOR said and decided across a SCOPED set of sessions.

WHAT THIS IS FOR
----------------
Reading back what the operator actually asked for across one effort — the
kickoff, the corrections, the "no, do X instead" — without the model's replies,
the tool output, or the harness's injected boilerplate in the way.

🔴 AND WHAT THEY DECIDED, WHICH IS A SEPARATE CHANNEL THIS TOOL USED TO DROP
ENTIRELY. An `AskUserQuestion` answer is the operator resolving a fork, and it
arrives in a `tool_result` block — the one block shape `extract_from_content`
ignores, correctly, for every other tool. devrc#1955: so an audit asking "did
everything he chose actually ship?" read a PARTIAL decision channel while
presenting a complete-looking one, and nothing in the output said a channel was
missing. It produced a confident wrong all-clear on a real arc: an instruction
of the form "merge as correctness-only, then close the loop" was reported as
honoured with its second half undone, and an item the operator had explicitly
DECLINED was about to be recommended as top priority. Both decisions were in
the transcripts the tool had just read.

Four `kind`s now, all four in the DEFAULT output and all four enumerated in
`KINDS`: `typed`, `command`, `decision`, and `decision_unanswered` — the last
being an `AskUserQuestion` that was asked and never answered, emitted rather
than dropped, because a new channel that hides its own empty case has the
defect this one was built to remove.

🔴 THE SCOPE IS THE POINT, AND IT USED NOT TO EXIST. Until 2026-09-25 this
script took one argument (an output path), walked the WHOLE corpus, and wrote
records of `{project, kind, text}`. MEASURED on this host that same day: 974
transcripts, 13,768 records, **54.1 MiB**, 12 s. The records carried NO session
id, so the obvious compose — enumerate an arc's sessions with
`find-session.py --arc … --json`, extract corpus-wide, then grep the ids out —
could not be completed at all: there was nothing to grep on. The only available
filter was `project`, a cwd basename, which for devrc work selects the entire
devrc corpus. So the answer to "what did I ask for across this arc" was 54 MiB
of everything, hand-read.

The selectors below answer it directly. `--arc` resolves the same session chain
`find-session.py --arc` prints, and extracts only those sessions.

🔴 THE ARC RESOLVER IS IMPORTED, NEVER RE-IMPLEMENTED. `find-session.arc_report`
owns the four steps (find the repo holding the doc, walk the corpus for reader
sessions, `handoff_arc.resolve_arc`, then append the note naming the corpus that
walk did NOT search). Each is a claim about coverage; a second spelling here
would publish a chain that looks complete and is not — and the two tools would
then answer "which sessions worked this doc" with different sets and neither
would say so.

🔴 A ZERO IS NEVER REPORTED AS A ZERO. `--arc typo-in-the-name` and an arc that
genuinely has no sessions both produce "nothing extracted"; so do "the ids
resolved to no transcript", "no transcript could be opened at all" and "the
transcripts held no row of any kind". Different facts with different fixes, so
each gets its own exit code and a reason on stderr — and where one code covers
two ways in (exit 5 does), the stderr line says which. See `EXIT_CONTRACT`.

HOW THE CORPUS IS REACHED — ONE RULE, ONE PLACE
-----------------------------------------------
Both paths go through `scripts/lib/transcript_search.py`: selected sessions via
`find_transcript` (the by-id lookup), the unscoped walk via `iter_transcripts`.
Both apply `is_corpus_member`, so a `subagents/` transcript is excluded either
way — a subagent is not a session anybody typed into.

⚠ RETRACTED 2026-09-25, DO NOT RE-DERIVE: this file used to keep a PRIVATE glob
here "because the unscoped walk wants EVERY jsonl including `subagents/`". That
sentence was false in four places at once — here, on `corpus_paths`, in the
`JSONL_GLOB_SITES` ledger, and in the line the tool PRINTED on every unscoped
run — and the private walk had never included a single subagent transcript
(measured: 0 of 5,681). See `corpus_paths` for the mechanism.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from transcript_search import find_transcript, iter_transcripts  # noqa: E402
import handoff_index  # noqa: E402

#: `"$DEVRC/$HOMELAB/…"` — the repo handles `--arc` can resolve a doc in, DERIVED
#: from the one tuple that decides it. 🔴 An inline copy here enumerated FOUR
#: while `handoff_index.REPO_ENV_HANDLES` had grown to five, so the exit-3
#: sentence told an operator the search had covered every checkout it can see
#: when it had skipped `$CIVITAI_CLI` entirely. `find-session.py` owns the search
#: and renders the same list from the same tuple.
ARC_HANDLES_SPELLED = "/".join(f"${h}" for h in handoff_index.REPO_ENV_HANDLES)

ROOT = os.path.expanduser("~/.claude/projects")

# --------------------------------------------------------------------------- #
# EXIT CODES
# --------------------------------------------------------------------------- #
EXIT_OK = 0
EXIT_USAGE = 2
#: `--arc` named nothing measurable: the seed resolved to no handoff doc, or no
#: repo handle holds that doc. 🔴 NOT an empty arc.
EXIT_ARC_UNMEASURED = 3
#: `--arc` resolved a real doc and the arc WAS measured — and it has no member
#: sessions. A measured emptiness, which is a finding rather than a failure.
EXIT_ARC_EMPTY = 4
#: NOTHING WAS READ. Two ways in, and the message on stderr says which:
#:   (a) session ids were selected and none resolved to a transcript here — the
#:       ids may be from the other host, or pruned; every one is named.
#:   (b) the transcripts that WERE selected could not be opened, or the corpus
#:       itself is absent/empty (a fresh host, a different $HOME, a container,
#:       the nix sandbox). No id is named, because none was the problem.
#: 🔴 (b) arrived when exit 6 was fixed to stop claiming files were read that
#: were not, and every contract sentence here still said "ids were selected …
#: check the peer host" — which sends a reader of case (b) to the wrong fix.
EXIT_NO_TRANSCRIPTS = 5
#: Transcripts were read and yielded zero rows of ANY kind after filtering —
#: nothing typed, no slash command, and no `AskUserQuestion` decision. ⚠ This
#: used to read "zero user-typed messages", which was narrower than the
#: condition it reports from the moment the decision channel became the default:
#: a reader of exit 6 could have concluded the decisions were merely filtered.
EXIT_NO_MESSAGES = 6

#: 🔴 EXIT 5 MEANS SOMETHING ELSE IN `find-session.py`, AND THIS TOOL IMPORTS
#: THAT ONE. There, `EXIT_ARC_UNMEASURED = 5` — "the doc was named but NOT
#: measured". Here that fact is exit 3, and 5 means "ids resolved to no
#: transcript on this host". A script reading the number from the wrong tool
#: gets a confident, wrong answer, and the coupling this PR introduced makes
#: that MORE likely rather than less. Renumbering to match was rejected: 3/4
#: must be adjacent to read as a pair, and moving `find-session`'s established
#: 5 would break its own documented contract. So the divergence is DECLARED
#: rather than hidden, here and in the reference's exit table.
EXIT_CONTRACT = (
    (EXIT_OK, "messages were extracted"),
    (EXIT_USAGE,
     "bad invocation, OR the output could not be opened, written or closed. "
     "🔴 IT IS NOT A CLAIM THAT NOTHING HAPPENED. On an --ids-file error "
     "nothing was searched; but on an OUTPUT error the walk has already run "
     "and, for -o PATH, that file has been TRUNCATED AT OPEN and may hold a "
     "PARTIAL result — whatever was there before is GONE. Delete it or "
     "re-run; do not treat it as the previous content."),
    (EXIT_ARC_UNMEASURED,
     "`--arc` ONLY: the seed named no handoff doc, or no "
     + ARC_HANDLES_SPELLED
     + " checkout holds it. 🔴 NOTHING WAS MEASURED — this "
     "is not an empty arc, and a wrong name lands here, not on exit 4."),
    (EXIT_ARC_EMPTY,
     "`--arc` ONLY: the doc resolved and the arc was measured, and it has ZERO "
     "member sessions. A MEASURED empty arc — a real finding about the doc, "
     "not a typo in your argument."),
    (EXIT_NO_TRANSCRIPTS,
     "NOTHING WAS READ — the stderr line says which of the two: session ids "
     "were selected and none resolved (each is named; check the peer host "
     "before concluding they are gone), OR no transcript could be opened at "
     "all, which includes an absent or empty ~/.claude/projects."),
    (EXIT_NO_MESSAGES,
     "transcripts WERE read and held zero rows of ANY kind after filtering — "
     "no typed prose, no slash command, and no AskUserQuestion decision. The "
     "sessions exist and are empty of operator input (agent-driven, or every "
     "message was harness boilerplate)."),
)

# --------------------------------------------------------------------------- #
# NOISE FILTERING — what is user-TYPED and what is harness boilerplate
# --------------------------------------------------------------------------- #
# ⚠ `scripts/collector/claude/tailer.py` carries a PORT of these predicates on
# purpose (it deploys as a standalone copy with no `scripts/lib` beside it). If
# you change one, read the other.
SYS_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)

#: Prefixes the harness writes into a `user` record that nobody typed.
BOILERPLATE_PREFIXES = (
    "[Request interrupted",
    "Caveat: The messages below",
    "API Error",
    "API request failed",
)


def clean_text(t):
    t = SYS_REMINDER.sub("", t)
    t = COMMAND_STDOUT.sub("", t)
    return t.strip()


def extract_from_content(content):
    """Every raw text string a `user` record's content carries.

    Returns strings, not `(kind, text)` pairs: the kind is decided by
    `message_of` from the text itself, and a provisional kind here was a second
    place the same decision appeared to be made.
    """
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    # Only `text` blocks. ⚠ `tool_result` is skipped HERE and read in
    # `records_of` — an `AskUserQuestion` result is the operator, every other
    # tool's result is not, and the discriminator is the `tool_use_id`, which
    # this function cannot see. image/tool_use/thinking are skipped outright.
    return [b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"]


def message_of(raw):
    """`(kind, text)` for one raw content string, or None when it is not typed.

    Factored out of the walk so the filtering can be tested without a corpus —
    it is the whole reason this tool's output is readable, and it had no test of
    its own while it lived inline.
    """
    if not raw:
        return None
    cmd = COMMAND_NAME.search(raw)
    if cmd:
        cargs_m = COMMAND_ARGS.search(raw)
        text = (cmd.group(1).strip() + " " +
                (cargs_m.group(1).strip() if cargs_m else "")).strip()
        return ("command", text) if text else None
    txt = clean_text(raw)
    if not txt:
        return None
    # A bare tag left over after the substitutions above, not prose.
    if txt.startswith("<") and txt.endswith(">") and len(txt) < 80:
        return None
    if txt.startswith(BOILERPLATE_PREFIXES):
        return None
    return ("typed", txt)


#: `kind` for a DECISION the operator took at an `AskUserQuestion` prompt — the
#: question was asked and an answer came back. Named `decision` rather than
#: `answer` because that is the fact a reader of this channel is after: it is
#: how a fork got resolved, and devrc#1955's closing condition names this
#: spelling.
KIND_DECISION = "decision"

#: `kind` for an `AskUserQuestion` that was ISSUED and never answered — no
#: `tool_result` ever named its id (the operator aborted, interrupted, or the
#: session ended on the prompt). 🔴 ITS OWN KIND, NOT A DROPPED ROW AND NOT A
#: FIELD ON `decision`. Dropping it would give the new channel the exact
#: silent-omission property the old one had; folding it into `decision` would
#: make "7 decisions" count questions nobody answered. A separate `kind` is
#: also what makes a consumer BRANCH on the state — `operator_asks.py` takes
#: `decision` and refuses this one, because an unanswered question is the
#: AGENT speaking, not the operator. MEASURED on this host 2026-10-03 over 954
#: corpus transcripts: **4 of 1,554** non-sidechain `AskUserQuestion` calls have
#: no result, in 4 different sessions, and all 4 were invisible.
KIND_DECISION_UNANSWERED = "decision_unanswered"

#: 🔴 EVERY `kind` THIS TOOL CAN EMIT, IN ONE PLACE. Two-way pinned: against
#: `message_of`'s own returns by `test_message_of_only_ever_returns_a_DECLARED_kind`,
#: and against the reference doc's prose by
#: `test_the_reference_names_every_kind_the_module_emits`. A channel added with
#: no prose beside it is this issue's defect in a new shape — the output would
#: gain a row class that nothing documents and no reader expects.
KINDS = ("typed", "command", KIND_DECISION, KIND_DECISION_UNANSWERED)

#: The tool whose result IS the operator speaking. Matched STRUCTURALLY: a
#: `tool_result` block's `tool_use_id` against an assistant `tool_use` of this
#: name. Never by the text, because a `tool_result` block is also how every
#: Bash/Read/Grep result arrives and those are not the operator.
ASK_TOOL_NAME = "AskUserQuestion"

#: Opens the text of a `decision_unanswered` row. A reader grepping the decision
#: channel must be able to tell "he chose X" from "he was asked and never said",
#: in the TEXT and not only in a field they might not read.
UNANSWERED_PREFIX = (
    "UNANSWERED AskUserQuestion — asked, and no tool_result ever came back "
    "(aborted, interrupted, or the session ended on the prompt): ")

#: Opens a `decision` row whose MATCHED `tool_result` carried no text at all.
#: MEASURED 0 of the 1,550 answered calls on this host, so this is not a path
#: in use — it exists because the predecessor's `if text:` was a SILENT DROP of
#: exactly the class this channel was added to stop, and a guard that only
#: covers the shapes we happened to see is how the next shape goes missing.
NO_ANSWER_TEXT_PREFIX = (
    "DECISION with no answer text — the tool_result matched and carried "
    "nothing readable: ")


def question_summary(tool_input):
    """The question(s) asked and the option labels offered, out of a `tool_use`.

    🔴 PARSED STRUCTURALLY AND TOLERANTLY, because the transcript schema varies
    across Claude Code versions and this input is the version-sensitive half.
    MEASURED over this host's corpus 2026-10-03: 1,551 calls carry `questions`
    (a list of `{question, header, multiSelect, options:[{label, description}]}`)
    and 3 carry `__unparsedToolInput` INSTEAD — the harness could not parse the
    call at all. So every field here is optional, and a shape this function
    cannot read returns '' rather than raising; the caller still emits the row,
    falling back to the `tool_use` id. An unreadable question is a worse record
    than a readable one and a far better one than no record.
    """
    if not isinstance(tool_input, dict):
        return ""
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return ""
    parts = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or q.get("header") or "").strip()
        labels = []
        for opt in q.get("options") or []:
            if not isinstance(opt, dict):
                continue
            label = str(opt.get("label") or "").strip()
            if label:
                labels.append(label)
        if labels:
            text = (text + " — options offered: " + " / ".join(labels)).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def asks_in(record):
    """`[(tool_use_id, question_summary)]` for one ASSISTANT record.

    🔴 NO SECOND PASS, AND NO MATERIALISED FILE. A `tool_result` names a
    `tool_use` that appeared EARLIER in the transcript, so collecting the asks
    as the stream goes is sufficient — which is what lets this channel be ON by
    default. Its predecessor (`answer_ids`) took a list of every record in the
    file, so the opt-in flag that enabled it also forced `list(_parse(f))`;
    MEASURED on 984 transcripts / 2.79 GB that cost 1.6-2.4x wall and ~15% RSS.
    Making the channel default WITHOUT this would have made that the only path.

    🔴 NO `isSidechain` CHECK HERE — `records_of` OWNS IT, FOR EVERY ROLE, AND
    THE COPY THAT USED TO SIT ON THIS LINE WAS UNREACHABLE. `records_of` drops
    a sidechain record before dispatching on `type`, so a second test here could
    never execute, and a test written against it passed for the wrong reason:
    row `K6` of `mutation_battery_extract_user_msgs.py` disabled the LIVE guard
    and scored KILLED-WRONG-REASON — the sidechain decision test stayed GREEN
    because this dead copy still refused the record. One rule, one place; the
    live site is the one the battery can move.
    """
    if record.get("type") != "assistant":
        return []
    out = []
    for c in (record.get("message") or {}).get("content") or []:
        # 🔴 `isinstance(…, str)` ON THE ID, NOT JUST TRUTHINESS. The id becomes
        # a DICT KEY two lines down, so a non-string one raises
        # `TypeError: unhashable type` — and `records_of` is consumed inside a
        # bare `except OSError`, so that escapes as a traceback at rc 1 and
        # discards the WHOLE extraction. MEASURED: 1,554 of 1,554 ids on this
        # host are strings, so this is defensive and is not claimed as covering
        # an observed shape; it is here because the failure mode is a crash in
        # an audit tool whose entire contract is that a zero means something.
        if (isinstance(c, dict) and c.get("type") == "tool_use"
                and c.get("name") == ASK_TOOL_NAME
                and isinstance(c.get("id"), str) and c["id"]):
            out.append((c["id"], question_summary(c.get("input"))))
    return out


def answer_text(block):
    """The operator's answer text out of one `tool_result` block, or ''.

    Handles both shapes the schema has carried: `content` a plain string
    (1,550 of 1,550 measured on this host 2026-10-03) and `content` a list of
    typed blocks (0 measured here, but it is the shape every other tool's
    result uses, so it is parsed rather than assumed absent).
    """
    content = block.get("content")
    if isinstance(content, str):
        return clean_text(content)
    if isinstance(content, list):
        return clean_text("".join(
            x.get("text", "") for x in content
            if isinstance(x, dict) and x.get("type") == "text"))
    return ""


def records_of(path, session_id=None):
    """Yield `{session_id, project, ts, kind, text}` for one transcript file.

    🔴 THE DECISION CHANNEL IS EMITTED, AND IT IS NOT OPT-IN. devrc#1955:
    `extract_from_content` says "ignore tool_result" — correct for Bash and Read
    output, and wrong for one tool, because an `AskUserQuestion` answer is the
    OPERATOR arriving in a `tool_result` block. Dropping it made every operator
    DECISION invisible to an arc audit while the output looked complete, and
    that **already produced a wrong all-clear once**: an arc was reported as
    "nothing you asked for was dropped" while an instruction of the form
    "merge as correctness-only, then close the loop" sat half-done, and an item
    the operator had explicitly declined was about to be recommended as top
    priority. An audit answering "did everything he chose actually ship?" off
    the typed channel alone lies by omission.

    MEASURED over this host's corpus 2026-09-26: **1,491 answer records across
    594 sessions, 837,635 B** against 895,672 B for the entire `typed` operator
    corpus — 93.5% again on top of everything this tool could previously
    report. Median 479 B, max 2,037 B.

    🔴 IT WAS OPT-IN UNTIL #1955 AND THAT IS WHAT THE ISSUE OVERRULES. The
    argument for the flag was "no shipped consumer's output may move", and it
    is a real cost paid in the wrong direction: the channel's whole value is to
    an audit that does not know to ask for it. `--include-answers` is still
    ACCEPTED and now does nothing (see `build_parser`), so every caller that
    passes it keeps working. The dedup key is `(project, kind, text)` and `kind`
    is load-bearing there already, so the new kinds compose without changing
    which pre-existing rows survive.

    🔴 EMISSION ORDER: `decision_unanswered` rows come LAST, after the file is
    exhausted, because "no result ever came back" is only known at EOF. `main` sorts
    every row by `ts` before rendering, so the CLI output is still chronological
    — a direct caller of this generator gets them at the end and must sort if
    it cares.

    `project` is always derived from the path — it had a parameter that no
    caller and no test ever passed.
    """
    path = Path(path)
    session_id = session_id if session_id is not None else path.stem
    project = path.parent.name

    def _parse(fh):
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue

    # 🔴 THERE IS NOW EXACTLY ONE PATH AND IT STREAMS. The predecessor had two:
    # the default handed the consumer a generator, and `--include-answers`
    # materialised `list(_parse(f))` because `answer_ids` took every record in
    # the file. That list is gone — `asks_in` is applied per record as the stream
    # goes, which is sound because a `tool_result` always names a `tool_use` that
    # appeared EARLIER in the transcript. So the decision channel became the
    # DEFAULT without making the materialising path the only path.
    #
    # The consumer loop stays inside the `with` because that is what makes the
    # generator survive it. ⚠ THAT SENTENCE HAS BEEN WRONG TWICE, IN OPPOSITE
    # WAYS, AND THE SECOND TIME IT ASSERTED THE FIX IT DID NOT MAKE — kept
    # because a maintainer re-splitting this function will reach for the same
    # shape:
    #   * `90f5aa74` materialised UNCONDITIONALLY and said "the default path
    #     still streams nothing extra into memory beyond the list above":
    #     self-contradictory, the list WAS the whole file.
    #   * `9b61b26d` moved that list into an `else` branch, wrote "🔴 THE DEFAULT
    #     PATH STREAMS", and still built `list(_parse(f))` — because the consumer
    #     loop sat OUTSIDE the `with`, so a generator could not survive it. Round
    #     2 of the devrc#1887 ladder measured the default path unmoved and caught
    #     the sentence.
    #
    # 🔴 THE PERFORMANCE NUMBER WAS ALSO MIS-ATTRIBUTED TWICE, THE SECOND TIME BY
    # THE COMMENT CORRECTING THE FIRST. `84d91b19` re-attributed it to `697387c6`,
    # which STREAMS: measured per commit, `62b516a4` streams, `697387c6` streams,
    # `90f5aa74` materialises and carries the self-contradictory sentence,
    # `9b61b26d` materialises in an `else`. So that correction named a comparison
    # between two identical implementations. Round 3 found it. Do not re-derive the
    # pair from memory — the shas above were each read with `git show`.
    # The measurement — 984 transcripts / 2.79 GB, `--jsonl -o`, 4 interleaved
    # runs at load ~9: 10.0-11.3 s / 222 MB against 17.3-26.3 s / 256 MB — is
    # REAL, and it compares a streaming revision against the unconditional
    # materialise. 🔴 IT IS NOT A MEASUREMENT OF THIS REVISION: this one streams
    # AND does strictly more work per record, and nobody has re-timed it. Do not
    # quote the range as this file's cost.
    #
    #: tool_use_id -> (base, question_summary) for every AskUserQuestion still
    #: awaiting a result. Popped on match; whatever is LEFT at EOF is the
    #: unanswered population, and it is emitted rather than dropped.
    pending = {}
    with open(path, errors="replace") as f:
        for obj in _parse(f):
            # sidechain == a subagent's own transcript, not user-typed, on
            # EITHER role — an `AskUserQuestion` a subagent issued is not a
            # fork the operator resolved.
            if obj.get("isSidechain"):
                continue
            if obj.get("type") == "assistant":
                for tool_use_id, summary in asks_in(obj):
                    pending[tool_use_id] = (
                        {"session_id": session_id, "project": project,
                         "ts": obj.get("timestamp") or ""}, summary)
                continue
            if obj.get("type") != "user" or obj.get("isMeta"):
                continue
            # 🔴 A COMPACTION SUMMARY IS THE MODEL'S PROSE, NOT THE OPERATOR'S. It
            # arrives as a `user` record with `isCompactSummary: true` and opens
            # "This session is being continued from a previous conversation…".
            # MEASURED 2026-09-26: 16 such records on this host, ALL 16 previously
            # emitted as user-typed, carrying 211,362 B — **23.6% of the entire
            # operator corpus**, and 212x the largest harness class any downstream
            # filter removes. Round 1 of the devrc#1887 ladder found it. It also
            # matters for a PUBLIC repo: `CLAUDE.md` names "a model's summaries of
            # them" as captured text that must never be committed, and this tool's
            # output gets quoted into handoff docs.
            if obj.get("isCompactSummary"):
                continue
            msg = obj.get("message") or {}
            if msg.get("role") != "user":
                continue
            base = {"session_id": session_id, "project": project,
                    "ts": obj.get("timestamp") or ""}
            for raw in extract_from_content(msg.get("content")):
                got = message_of(raw)
                if got is None:
                    continue
                kind, text = got
                yield {**base, "kind": kind, "text": text}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                # 🔴 THE DISCRIMINATOR IS THE ID, NEVER THE TEXT. Every
                # Bash/Read/Grep result arrives in this same block shape and
                # none of them is the operator; `pending` holds only the ids of
                # `AskUserQuestion` calls, so membership IS the test.
                tool_use_id = block.get("tool_use_id")
                # `isinstance` FIRST: `{} in some_dict` raises
                # `TypeError: unhashable type`, which no handler here catches
                # — see `asks_in` for the same guard and the same reason.
                if (not isinstance(tool_use_id, str)
                        or tool_use_id not in pending):
                    continue
                _, summary = pending.pop(tool_use_id)
                text = answer_text(block)
                if not text:
                    # 🔴 NOT `if text:` — that was a SILENT DROP. See
                    # NO_ANSWER_TEXT_PREFIX: 0 of 1,550 on this host, kept
                    # because an unmeasured shape is the one that goes missing.
                    text = NO_ANSWER_TEXT_PREFIX + (summary or tool_use_id)
                yield {**base, "kind": KIND_DECISION, "text": text}
    # 🔴 EVERY ASK THAT NEVER CAME BACK IS EMITTED, AS ITS OWN KIND. devrc#1955
    # note 1: "a `tool_use` with no matching `tool_result` is a real state
    # (aborted/unanswered) and must be emitted as such, not dropped — otherwise
    # the new channel acquires the same silent-omission property as the old
    # one." The predecessor dropped all of them: it built a SET of ids and only
    # ever emitted on a match, so an operator who closed the prompt without
    # answering left no trace at all. MEASURED on this host 2026-10-03: 4 of
    # 1,554 `AskUserQuestion` calls, in 4 different sessions, invisible.
    #
    # `base` here is the ASSISTANT record's — the moment the question was asked,
    # which is the only timestamp this state has.
    for tool_use_id, (ask_base, summary) in pending.items():
        yield {**ask_base, "kind": KIND_DECISION_UNANSWERED,
               "text": UNANSWERED_PREFIX + (summary or tool_use_id)}


# --------------------------------------------------------------------------- #
# SELECTORS
# --------------------------------------------------------------------------- #
def _load_find_session():
    """Import `scripts/find-session.py` — hyphenated, so by path.

    Same mechanism `scripts/check-clickup-addressed/check-addressed.py` uses to
    reach its sibling, and for the same reason: the predicate is ONE rule and a
    copy is a second thing to drift.
    """
    spec = importlib.util.spec_from_file_location(
        "_find_session", SCRIPTS / "find-session.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ArcUnresolved(RuntimeError):
    """The `--arc` seed named nothing measurable. Carries the operator sentence."""


def arc_sessions(seed, root=None, find_session=None):
    """`(members, notes)` for an arc seed — imported resolution, never a copy.

    `members` is a list of `handoff_arc.ArcMember` in the order the chain runs.
    `notes` is every coverage caveat the resolver produced, which the caller
    MUST surface: a chain rendered without them reads as complete.

    Raises `ArcUnresolved` when nothing was measured. An EMPTY `members` from a
    doc that DID resolve is returned normally — that is a measured fact.
    """
    fs = find_session or _load_find_session()
    if root is not None:
        fs.ROOT = root
    basename = fs.arc_seed_to_doc(seed, root=root)
    if not basename:
        raise ArcUnresolved(
            f"--arc {seed!r} names no handoff doc. A slug/basename/path is "
            "taken directly; a SESSION ID is resolved by reading that "
            "session's opening message, and that message named no "
            "`claudedocs/handoff-*.md`. 🔴 Nothing was measured.")
    try:
        report = fs.arc_report(basename)
    except fs.ArcUnmeasured as exc:
        raise ArcUnresolved(f"--arc {seed!r}: {exc}") from exc
    notes = [f"arc {report.doc} (repo {report.repo or 'unknown'})",
             # 🔴 ALWAYS, INCLUDING WHEN IT IS ZERO — an absent coverage line is
             # indistinguishable from "nothing missing".
             fs.handoff_arc.coverage_line(report)]
    notes.extend(report.unmeasured_notes)
    return list(report.members), notes


def read_ids_file(path):
    """One session id per line; `-` is stdin. Blank lines and `#` comments skip.

    Raises `OSError` — the caller turns that into exit 2, because an ids file
    that could not be read must never degrade into "no ids selected".

    🔴 BOTH BRANCHES DECODE WITH `errors="replace"`. stdin used to decode
    strictly, so non-UTF-8 on the pipe raised `UnicodeDecodeError` — a
    `ValueError`, not an `OSError` — which escaped the caller's handler as a
    traceback at rc 1 while this docstring promised exit 2. A session id is
    ASCII in every runtime seen so far, so a replaced byte yields an id that
    simply resolves to nothing and is REPORTED as unresolved, which is the
    behaviour the file branch already had.
    """
    if path == "-":
        data = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else None
        text = (data.decode("utf-8", errors="replace") if data is not None
                else sys.stdin.read())
    else:
        text = Path(path).read_text(errors="replace")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def resolve_sessions(ids, root=None):
    """`(resolved, missing)` — `resolved` is `[(session_id, path)]`, order kept.

    🔴 `missing` IS RETURNED, NOT DROPPED. An id that names no transcript on
    this host is a fact about THIS host (the session may live on the peer, or
    have been pruned), and silently narrowing the set to what happened to
    resolve is how a partial extraction reads as a whole one.
    """
    resolved, missing, seen = [], [], set()
    for sid in ids:
        if sid in seen:
            continue
        seen.add(sid)
        path = find_transcript(sid, root=root if root is not None else ROOT)
        if path is None:
            missing.append(sid)
        else:
            resolved.append((sid, Path(path)))
    return resolved, missing


def corpus_paths(root=None):
    """Every session transcript in the corpus — the unscoped walk.

    🔴 THE SHARED ENUMERATOR, NOT A GLOB OF OUR OWN, AND THE PRIVATE ONE THIS
    REPLACED CARRIED A FALSE REASON FOR EXISTING. It open-coded exactly the two
    rules `transcript_search.is_corpus_member` owns — skip a `subagents` dir,
    skip a `wf_` prefix — while `JSONL_GLOB_SITES`, this function's docstring,
    the module docstring and the line the tool PRINTS on every unscoped run all
    said it kept its own walk because it "wants EVERY jsonl including
    subagents". MEASURED 2026-09-25: of 5,681 transcripts whose parent dir IS
    `subagents`, the private walk included **0**. It had never included one; a
    real subagent transcript sits at `<project>/<id>/subagents/agent-*.jsonl`,
    so its immediate parent is literally `subagents` and the very check that
    was supposed to let them through is what dropped them.

    Worse, the private copy was the NARROWER of the two: it tested only
    `path.parent.name`, where `is_corpus_member` tests every parent part. A
    transcript nested one level below a `subagents/` dir passed the private
    check and fails the shared one — so routing here both deletes a duplicated
    predicate and fixes the direction it was wrong in.

    The two-way `JSONL_GLOB_SITES` ledger entry and the `scripts/README.md`
    prose row went with it, which is what that ledger is for.
    """
    return list(iter_transcripts(root=root if root is not None else ROOT))


# --------------------------------------------------------------------------- #
# RENDERING
# --------------------------------------------------------------------------- #
def _longest_backtick_run(text):
    """The longest run of backticks in `text`, so a fence can outgrow it."""
    best = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        best = max(best, run)
    return best


def render_jsonl(rows, out):
    for r in rows:
        out.write(json.dumps(r, ensure_ascii=False) + "\n")


def render_markdown(rows, out, title, notes=(), roles=None):
    """Grouped by session, oldest message first.

    ⚠ This said "newest-typed-first WITHIN a session preserved" and the sort is
    ASCENDING — oldest first — so it described the opposite order. Rows sharing
    a timestamp keep file order; that is the only sense in which file order is
    "preserved".

    Markdown is the DEFAULT because the answer is read by a human or pasted
    into a context window; `--jsonl` is the canonical machine form and carries
    every key un-truncated. ⚠ It does NOT carry more than markdown overall —
    the coverage notes go to STDERR in both formats, which is what stops
    `--jsonl` publishing a chain with its gaps missing.
    """
    roles = roles or {}
    by_session = {}
    for r in rows:
        by_session.setdefault(r["session_id"], []).append(r)
    out.write(f"# {title}\n\n")
    out.write(f"{len(by_session)} session(s), {len(rows)} message(s)\n\n")
    for note in notes:
        out.write(f"> {note}\n")
    if notes:
        out.write("\n")
    for sid, group in by_session.items():
        bits = [sid]
        if roles.get(sid):
            bits.append(roles[sid])
        bits.append(f"{len(group)} message(s)")
        out.write(f"## {' · '.join(bits)}\n\n")
        for i, r in enumerate(group, 1):
            stamp = (r.get("ts") or "")[:19].replace("T", " ") or "time UNMEASURED"
            out.write(f"### {i}. {stamp} · {r['kind']}\n\n")
            # 🔴 FENCED, NOT RAW. The text is the operator's own prose and
            # this is a structured document an AGENT reads: a message beginning
            # `## `, `### ` or `> ` rendered raw becomes a session heading, a
            # message heading or a coverage note. Fencing makes the boundary
            # unambiguous in the one artifact whose whole job is to report what
            # was said without editorialising it.
            body = r["text"].rstrip()
            fence = "`" * max(3, _longest_backtick_run(body) + 1)
            out.write(f"{fence}\n{body}\n{fence}\n\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
EPILOG = """\
selectors (combinable; with NONE of them the WHOLE corpus is walked)

  --arc SEED            every session in a handoff doc's arc. SEED is a slug,
                        a basename, a path, or a SESSION ID whose opening
                        message names the doc.
      extract_user_msgs.py --arc handoff-cairn-phase3
      extract_user_msgs.py --arc claudedocs/handoff-cairn-phase3.md
      extract_user_msgs.py --arc 00000000-1111-4222-8333-444444444444

  --session ID          one session. Repeatable.
      extract_user_msgs.py --session 00000000-1111-4222-8333-444444444444
      extract_user_msgs.py --session <id-a> --session <id-b>

  --ids-file PATH       one id per line; `#` comments and blanks skipped.
                        `-` reads stdin.
      extract_user_msgs.py --ids-file ./ids.txt
      find-session.py --arc handoff-foo --json \\
        | jq -r '.members[].session_id' \\
        | extract_user_msgs.py --ids-file -

output

  (default)             markdown, grouped by session, to stdout
  --jsonl               one canonical record per line:
                        {session_id, project, arc_role, ts, kind, text}
  -o PATH               write to PATH instead of stdout
  --include-answers     ACCEPTED AND IGNORED. The decision channel it used to
                        gate is ON by default since devrc#1955; the flag is
                        kept so a caller that passes it — audit-dispatch, the
                        re-read command /audit-pr prints — keeps working, and
                        so it still enables the channel against an older
                        extractor deployed on another host.

kinds in the output

  typed                 prose the operator wrote
  command               a slash command they invoked
  decision              a DECISION they took at an AskUserQuestion prompt —
                        the question and the option they chose. Arrives in a
                        tool_result block; MEASURED 1,491 records / 837,635 B
                        on this host against 895,672 B for the whole typed
                        corpus, so it nearly DOUBLES what this tool reports
  decision_unanswered   an AskUserQuestion that was ASKED and never answered
                        (aborted, interrupted, or the session ended on it).
                        Its own kind, never a dropped row: 4 of 1,554 measured

exit codes — a zero cannot distinguish a wrong name from an empty arc, so
each reason has its own code and prints on stderr:

  0  messages were extracted
  2  bad invocation, or the output could not be opened/written/closed.
     NOT a claim that nothing happened: on an output error the walk has
     already run and `-o PATH` holds a PARTIAL file — it was TRUNCATED
     at open, so its previous content is gone
  3  --arc: NOTHING WAS MEASURED (the seed names no doc, or no checkout
     holds it) — a typo lands here, not on 4
  4  --arc: the arc WAS measured and has zero member sessions
  5  NOTHING WAS READ — ids resolved to no transcript, or none could be
     opened at all (an absent or empty corpus lands here)
  6  transcripts WERE read and held zero rows of ANY kind — nothing typed,
     no slash command, and no AskUserQuestion decision
"""


def build_parser():
    p = argparse.ArgumentParser(
        prog="extract_user_msgs.py",
        description="Extract the user-TYPED messages from a scoped set of "
                    "Claude Code sessions (an arc, named sessions, or ids on "
                    "stdin).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--arc", metavar="SEED",
                   help="every session in a handoff doc's arc (slug, path, or "
                        "a session id that opened with the doc)")
    p.add_argument("--session", action="append", default=[], metavar="ID",
                   help="one session id; repeatable")
    p.add_argument("--ids-file", metavar="PATH",
                   help="file of session ids, one per line; `-` reads stdin")
    p.add_argument("--jsonl", action="store_true",
                   help="canonical JSONL instead of markdown")
    p.add_argument("-o", "--out", metavar="PATH",
                   help="write here instead of stdout")
    p.add_argument("--no-dedup", action="store_true",
                   help="keep every repeat; by default an identical message "
                        "seen twice in the selection is emitted once and the "
                        "suppressed count is reported")
    # 🔴 A NO-OP ON PURPOSE, AND SAYING SO IS THE POINT. devrc#1955 made the
    # decision channel the default, which retires this flag's job. It is kept
    # ACCEPTED rather than removed for two reasons: `audit-dispatch.py` passes
    # it and `operator_asks.render` PRINTS it in the re-read command an auditor
    # copies, and either could be run against an older extractor deployed on
    # the peer host, where the flag is still load-bearing. Pinned by
    # `test_the_retired_flag_is_a_NO_OP_not_a_second_mode` — accepting a flag
    # that silently did something different would be worse than removing it.
    p.add_argument("--include-answers", action="store_true",
                   help="ACCEPTED AND IGNORED — the decision channel it used "
                        "to gate is ON by default since devrc#1955. Kept so "
                        "callers that pass it keep working, and so it still "
                        "enables the channel against an older deployed copy.")
    p.add_argument("--root", default=None,
                   help=argparse.SUPPRESS)   # tests point this at a fixture
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    err = sys.stderr
    root = a.root

    ids, roles, notes = [], {}, []
    scoped = bool(a.arc or a.session or a.ids_file)

    # 🔴 ONE EMITTER, CALLED BEFORE EVERY RETURN THAT HAS NOTES. Two defects
    # made this necessary and it fixes both. (a) The notes loop used to sit
    # AFTER the `if not rows: return`, so on exit 5 and exit 6 the UNSCOPED
    # banner, the arc coverage line and `unmeasured_notes` were emitted
    # NOWHERE — and under `--arc`, exit 6 is precisely the measured-zero whose
    # coverage line decides whether the zero is real. (b) Notes were ALSO
    # printed inline where they were appended, so every one reached stderr
    # twice on the success path and a consumer counting `!` lines double-counted
    # the gap. Appending is now the only way to raise a note; this is the only
    # way one is printed; `_emitted` makes a second call a no-op.
    _emitted = []

    def flush_notes():
        if _emitted:
            return
        _emitted.append(True)
        for note in notes:
            print(f"! {note}", file=err)

    if a.arc:
        try:
            members, arc_notes = arc_sessions(a.arc, root=root)
        except ArcUnresolved as exc:
            print(str(exc), file=err)
            return EXIT_ARC_UNMEASURED
        notes.extend(arc_notes)
        if not members:
            flush_notes()
            print(f"--arc {a.arc!r}: the doc resolved and its arc WAS measured "
                  "— it has zero member sessions. 🔴 This is a MEASURED empty "
                  "arc, not a typo in your argument (that is exit "
                  f"{EXIT_ARC_UNMEASURED}).", file=err)
            return EXIT_ARC_EMPTY
        for m in members:
            ids.append(m.session_id)
            roles[m.session_id] = m.role

    ids.extend(a.session)
    if a.ids_file:
        try:
            ids.extend(read_ids_file(a.ids_file))
        except OSError as exc:
            flush_notes()
            print(f"--ids-file {a.ids_file!r}: {exc}", file=err)
            return EXIT_USAGE
        if not ids:
            flush_notes()
            print(f"--ids-file {a.ids_file!r} held no session ids. Nothing was "
                  "searched.", file=err)
            return EXIT_USAGE

    if scoped:
        resolved, missing = resolve_sessions(ids, root=root)
        if missing:
            # Raised as a NOTE, not printed here — see `flush_notes`. It still
            # reaches the success path; a partial extraction that does not say
            # so reads as a whole one.
            notes.append(
                f"{len(missing)} of {len(ids)} selected session(s) have NO "
                "transcript on this host (peer host? pruned?): "
                + " ".join(missing))
        if not resolved:
            flush_notes()
            print(f"none of the {len(ids)} selected session id(s) resolved to a "
                  "transcript in ~/.claude/projects on this host. 🔴 Nothing "
                  "was read — this is not 'the sessions are empty' (that is "
                  f"exit {EXIT_NO_MESSAGES}).", file=err)
            return EXIT_NO_TRANSCRIPTS
        sources = resolved
        title = (f"user messages — arc {a.arc}" if a.arc
                 else f"user messages — {len(resolved)} selected session(s)")
    else:
        sources = [(p.stem, p) for p in corpus_paths(root=root)]
        title = f"user messages — WHOLE corpus ({len(sources)} transcripts)"
        notes.append(f"UNSCOPED: every session transcript ({len(sources)}) — "
                     "subagent transcripts are NOT included. Pass "
                     "--arc/--session/--ids-file to scope it.")

    # 🔴 `opened` COUNTS TRANSCRIPTS ACTUALLY READ, NOT THE SELECTION SIZE. The
    # exit-6 sentence asserts "the transcripts WERE read", and deriving it from
    # `len(sources)` made that assertion false on two reachable inputs: an
    # absent or empty `~/.claude/projects` (a fresh host, a different $HOME, a
    # container, the nix sandbox) reported `read 0 transcript(s)` while
    # affirming they were read, and a selection whose every file is unreadable
    # reported `read 1` having opened none. A caller branching on 6 then
    # concludes the exact opposite of the truth — which is the confusion the
    # four-code contract exists to remove, on the one code with no other witness.
    rows, seen, unreadable, opened = [], set(), [], 0
    #: sid -> how many of its records dedup suppressed. A session whose EVERY
    #: record is suppressed vanishes from the report entirely, and that must be
    #: announced rather than inferred from a total.
    suppressed_by = {}
    for sid, path in sources:
        # 🔴 MATERIALISED INSIDE THE `try`, ON PURPOSE. `records_of` is a
        # generator, so its `open()` runs on the first `next()`, not at the
        # call. Consuming it here puts every read inside one handler, and makes
        # `opened` true of files that were read and held nothing, which is
        # exactly the population exit 6 is about.
        #
        # ⚠ `records_of` STREAMS, so a mid-file read error surfaces at a LATER
        # `next()` than one at open. The arm holds either way, because `list(...)`
        # is inside this `try` — that is the only claim this comment needs, and
        # two earlier spellings tried to say more and were wrong:
        #   * `9b61b26d` wrote "both now surface at the same `next()`", true only
        #     while it materialised unconditionally.
        #   * `84d91b19` restored streaming on the default path and left that
        #     sentence standing, re-falsifying it. Round 3 found it.
        # A maintainer restructuring this `try` on the strength of the old
        # sentence would have been reasoning from an invalidated claim. There is
        # no longer a flag that changes it: devrc#1955 removed the second path.
        try:
            recs = list(records_of(path, session_id=sid))
        except OSError as exc:
            unreadable.append(f"{sid}: {exc}")
            continue
        opened += 1
        for rec in recs:
            if not a.no_dedup:
                # 🔴 `kind` IS PART OF THE IDENTITY. A `/handoff` typed as
                # prose and a `/handoff` slash command are two different
                # events with the same text; a key that omits `kind` keeps
                # whichever the walk reached first and silently drops the
                # other. MEASURED over a frozen 974-transcript list: 7 rows
                # the previous implementation kept vanished this way.
                key = hashlib.md5(
                    "\x1f".join((rec["project"], rec["kind"],
                                 rec["text"])).encode()).hexdigest()
                if key in seen:
                    suppressed_by[sid] = suppressed_by.get(sid, 0) + 1
                    continue
                seen.add(key)
            rec["arc_role"] = roles.get(sid)
            rows.append(rec)

    suppressed = sum(suppressed_by.values())
    if unreadable:
        notes.append(f"{len(unreadable)} of {len(sources)} transcript(s) could "
                     "NOT be read: " + "; ".join(unreadable))

    # 🔴 A SESSION DEDUP ERASED COMPLETELY IS A GAP, NOT A TIDY-UP. Cross-session
    # dedup is the point under `--arc` — the same kickoff pasted into every
    # resumed session is noise — but a session whose every record was a repeat
    # disappears from the report, and a reader then concludes it typed nothing.
    # The module already holds itself to this standard for unresolved ids
    # ("the markdown header MUST carry the gap too — stderr is routinely
    # discarded by a caller redirecting stdout"); suppression is the same class.
    emptied = sorted(sid for sid, n in suppressed_by.items()
                     if not any(r["session_id"] == sid for r in rows))
    if emptied:
        notes.append(
            f"{len(emptied)} session(s) are ABSENT from this report because "
            "dedup suppressed every one of their messages as a repeat of "
            "another session's — they are not empty: " + " ".join(emptied)
            + ". Pass --no-dedup to see them.")

    if not rows:
        # 🔴 BEFORE THE RETURN, NOT AFTER IT. Under `--arc`, exit 6 IS the
        # measured zero whose coverage line decides whether the zero is real.
        flush_notes()
        if not opened:
            # NOT exit 6: nothing was read, so nothing was measured.
            if not sources:
                print("the corpus holds NO session transcripts at all "
                      f"(looked under {root or ROOT!r}) — 0 were read. 🔴 This "
                      "is NOT 'the sessions are empty': check $HOME and that "
                      "this host has a ~/.claude/projects.", file=err)
            else:
                print(f"none of the {len(sources)} selected transcript(s) could "
                      "be opened — 0 were read. 🔴 This is NOT 'the sessions "
                      "are empty'; nothing was measured.", file=err)
            return EXIT_NO_TRANSCRIPTS
        print(f"read {opened} transcript(s) and found zero rows of ANY kind "
              "after filtering — no typed prose, no slash command, and no "
              "AskUserQuestion decision. 🔴 The transcripts WERE read — this "
              "is a measured emptiness, not an unresolved selector.", file=err)
        return EXIT_NO_MESSAGES

    # Oldest first across sessions; rows sharing a timestamp keep file order.
    rows.sort(key=lambda r: (r["ts"] == "", r["ts"]))

    flush_notes()

    try:
        out = open(a.out, "w") if a.out else sys.stdout
    except OSError as exc:
        # Guarded because the extraction has already RUN by this point — an
        # unwritable path used to surface as a traceback at rc 1, discarding
        # the whole result, from the `--jsonl -o <path>` shape this repo's own
        # reproduce recipe uses.
        print(f"-o {a.out!r}: {exc}", file=err)
        return EXIT_USAGE
    try:
        if a.jsonl:
            render_jsonl(rows, out)
        else:
            render_markdown(rows, out, title, notes=notes, roles=roles)
    except BrokenPipeError:
        # 🔴 `… | head` / `… | jq | head` closes stdout mid-write, and exiting
        # quietly is the Unix contract — this tool's own --help and its
        # reference doc BOTH show piped usage, so the traceback was reachable
        # from the documented invocation.
        #
        # TWO failures, one symptom. This `except` handles the failed WRITE and
        # is pinned behaviourally by `TestPipingToHead`. The `dup2` handles
        # CPython retrying the flush AT SHUTDOWN, which prints
        # "Exception ignored … BrokenPipeError" to stderr.
        #
        # 🔴 DO NOT WRITE A KILL RATE FOR THE `dup2` HERE. FOUR HAVE BEEN
        # MEASURED, NO TWO AGREE, AND TWO SHIPPED AS FACTS; IF YOU ARE ABOUT TO
        # ADD A FIFTH, THAT IS THE MISTAKE. The four, in order: SURVIVED — 0 red
        # of 1 draw, and that GREEN draw is why this history exists; then 20/20
        # red; then an independent audit's 22/40; then 10/40. Same mutant, same
        # test, controls clean every time (0/40). It is LOAD-DEPENDENT: whether
        # the shutdown flush still holds data depends on TextIOWrapper buffer
        # state at the moment the pipe closes, which varies with how far `head`
        # got before exiting. There is no rate to find. Do not go looking for
        # one — reaching for a better number is what regenerated this error
        # twice.
        #
        # So the line is pinned STRUCTURALLY instead, by
        # `test_the_shutdown_flush_is_silenced_by_redirecting_fd_1`: it asserts
        # the redirect HAPPENS, which is deterministic. That is a weaker claim
        # than "the stderr noise is gone" and is deliberately not dressed up as
        # the stronger one — the observable consequence is real (seen at
        # `--jsonl | head -5`) but cannot be asserted reliably.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        if a.out:
            out.close()
        return EXIT_OK
    except OSError as exc:
        # 🔴 THE WRITE, NOT JUST THE OPEN. The first version of this guard
        # wrapped `open()` only, and writes are BUFFERED — so for a path you
        # can create but cannot fill (a full disk, a quota, an I/O error) the
        # failure surfaced at `close()`, outside the handler, as exactly the
        # "traceback at rc 1 discarding the whole result" the guard was added
        # to remove. MEASURED: `--jsonl -o /dev/full` → rc 1, `OSError: [Errno
        # 28] No space left on device` from `out.close()`.
        if a.out:
            try:
                out.close()
            except OSError:
                pass            # the original failure is the one to report
        where = repr(a.out) if a.out else "stdout"
        print(f"writing {where}: {exc} — the output is INCOMPLETE"
              + (f"; {a.out!r} was truncated at open, so its previous content "
                 "is GONE" if a.out else ""), file=err)
        return EXIT_USAGE

    # 🔴 CLOSED EXPLICITLY, INSIDE A GUARD — not in a `finally`. A buffered
    # write is only durable once close() succeeds, so a close that raises must
    # reach the same handler as a failed write rather than escaping it.
    if a.out:
        try:
            out.close()
        except OSError as exc:
            print(f"writing {a.out!r}: {exc} — the output is INCOMPLETE; "
                  "its previous content was truncated at open and is GONE",
                  file=err)
            return EXIT_USAGE

    # `sessions=` counts what was READ, not what was selected — the same basis
    # exit 6 uses, and for the same reason: a file that could not be opened is
    # named in a note, and counting it here would contradict that note.
    print(f"sessions={opened} msgs={len(rows)} "
          f"deduped={suppressed} out={a.out or '-'}", file=err)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
