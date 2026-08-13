#!/usr/bin/env python3
"""Stop nudge: when a turn ends WITHOUT naming a next step, block the stop once per
session and ask for one line saying what happens next.

Why this exists — measured, not assumed. Over 14 days of operator prompts in
`activity.events`:

    proceed 542 · yes 134 · recommend next actions 123 · recommend 76 ·
    recommend and proceed 17

`recommend*` = 216 occurrences, roughly 1 in every 3.5 approvals. The operator's reply
vocabulary is otherwise almost pure authorization (`proceed`, `yes`, `merge`,
`dispatch`), so each `recommend*` is a whole round trip that exists ONLY because the
assistant stopped without saying what it would do next. A turn that ends with a stated
next step converts that round trip into a one-word `proceed`. Concentration by project
over the same window — datapacket-talos 152, cli 33, devrc 22, homelab-talos 12 — is why
this is a global hook and not devrc guidance.

Prose was considered and rejected: `claude/RULES.md` is a few hundred bytes under its
enforced ceiling, loads every session on both hosts, and is concatenated into opencode's
`AGENTS.md` — paid twice. PRINCIPLES.md prefers deterministic/structural fixes over prose
instruction, and shell-env-nudge.py's own header records the precedent ("the CLAUDE.md
pointers documenting them are opt-in, and opt-in guidance has historically not stuck").

--------------------------------------------------------------------------------------
MECHANISM — exit 2, and why that is the only option

`hookSpecificOutput.additionalContext` is documented as NOT supported on Stop. The
documented way to reach the model from a Stop hook is exit code 2, whose row in the
hooks reference reads: `Stop | Yes | Prevents Claude from stopping, continues the
conversation`, with stderr fed back to the model. The JSON alternative `continue:false`
is the OPPOSITE of what is wanted — it halts processing entirely.

So firing means BLOCKING the stop. That is expensive and disruptive if wrong, which is
why the suppression below is the substance of this file and the firing condition is an
ABSENCE. The blast radius is bounded three ways: at most ONE block per session ever
(atomic claim); never a second consecutive block (`stop_hook_active`); and any error,
missing field or unreadable transcript exits 0 and lets the turn end.

🔴 BLOCKING A STOP RE-RUNS EVERY OTHER STOP HOOK — checked against each, one by one,
because "it only affects my hook" was not true. Stop is shared with three pre-existing
owners on this host. When this hook blocks, the turn continues and Stop fires AGAIN when
it really ends, so those three can see two Stop events for one turn. Bounded to at most
one extra per session by the claim below. What each does with the second event:

  * claude-notify.py — SAFE, twice over: it returns immediately when `stop_hook_active`
    is set, and it also consumes its turn-start marker on the first Stop, so the second
    finds no start file. Verified by reading it (`if data.get("stop_hook_active"):
    return`, then the `if event == "Stop"` cleanup).
  * ~/.config/tmux/task-hook.sh — its INLINE fallback guards on `stop_hook_active` and
    exits 0. Its primary path is `exec fuzzyclaw hook stop`, and the fuzzyclaw binary was
    NOT inspected — out of scope (it is being deprecated) and deliberately not entangled
    with here. Unverified, stated as such; worst case is one extra task-json write.
  * clawgate-stop-hook.sh — does NOT guard on `stop_hook_active` (grepped: no match), so
    it WILL POST to /api/suggest a second time on a session where this hook fires. One
    extra suggestion generation per session, at most. A real cost, named rather than
    hidden; it does not affect approval, which is the PermissionRequest hook, not this.

Registration appends this hook LAST so the other three have already observed the turn
before it can block.

🔴 FAIL-OPEN HERE MEANS SILENCE, which is the opposite of this hook's PostToolUse
siblings. shell-env-nudge/search-tool-nudge fail open by EMITTING (a duplicate nudge is
cheap, a swallowed one is invisible). This hook fails open by NOT emitting, because its
emission blocks the operator's turn. Every `except` below therefore exits 0, and every
gate that cannot be evaluated is treated as "do not fire".

--------------------------------------------------------------------------------------
DETECTION — an absence, in a positional window, over closed grammatical classes

The firing condition is: the TAIL of the final message contains no forward-looking
construction. Both halves matter.

  * POSITIONAL — and stated at the scope actually measured. The tail is the message's
    last paragraph blocks, taken from the end until at least TAIL_CHARS characters are
    covered, rather than the whole message. 🔴 On this corpus that narrowing changes
    NOTHING: sweeping TAIL_CHARS from 10 to 10^9 leaves the fire count at 565/11,215,
    unmoved, because a message that has any forward-looking construction essentially
    always has one in its final paragraph. So the window is a bound on a shape the real
    traffic does not contain — a next step stated up top and then buried under kilobytes
    of findings — and NOT a tuned parameter. It is kept because it is cheap and can only
    ever make the hook louder, never quieter; it is not evidence for anything, and
    test_next_step_buried_far_above_the_tail_does_not_suppress is labelled there as the
    invariant guard it is rather than counted as regression coverage.

  * ABSENCE, NOT PRESENCE. There is no "does the text contain 'next'" test anywhere;
    that is the spelled-guard failure this repo has been bitten by (a
    `Contains(body,"disabled")` check satisfied by an unrelated attribute on a live
    button). A firing condition that is the ABSENCE of every suppressor cannot be
    satisfied by an unrelated feature spelling a word — the only way to make this hook
    fire is to write a tail with no question, no commitment, no offer and no marked
    recommendation in it.

  * The suppressors ARE lexical, and that is deliberate and safe in this direction.
    A false suppressor costs one missed nudge (silent, cheap). A false FIRE costs a
    blocked turn and trains the operator to ignore the hook. So every suppressor is
    written GENEROUSLY: when in doubt, suppress. They are closed grammatical classes
    (pronoun + modal, sentence-final `?`, second-person hand-off) rather than topic
    vocabulary, so widening one cannot make the hook fire more.

MEASURED false-positive rate. Against 11,215 turn-final assistant messages taken from
this host's own transcripts, labelled by what the OPERATOR typed next — a behavioural
label, not my judgement about whether the turn was any good:

    turns answered with `recommend…`   (SHOULD fire):   39/268   = 14.6%
    turns answered with bare approval  (MUST NOT fire): 14/1464  =  1.0%
    all turns                                          565/11215 =  5.0%
    sessions firing at all (once-per-session dedupe):  190/620   = 30.6%

So ~15:1 in favour of firing on a turn that genuinely lacked a next step, and an
operator sees this at most once in roughly three sessions.

Bounding the 1.0%, honestly:
  * It is an UPPER bound on harm. Some of those 14 are turns that did name a next step
    somewhere the operator was happy with; none of them are turns where the nudge would
    have been welcome.
  * It is NOT an artefact of tuning on the same data. Split by session mtime, the newest
    25% of sessions — which the suppressors were never sampled from — give 1.1%
    (5/461) against 0.9% (9/1003) on the older 75%. Per project: 1.0%, 0.0%, 3.4%,
    0.0%, 0.0% (the 3.4% is 4/118, small n).
  * Recall is deliberately the sacrificed side. Every suppressor is written generously,
    and each widening traded recall for precision on purpose: an earlier revision sat at
    36.9%/4.6% and was made quieter, twice. Firing costs a blocked turn; staying silent
    costs one round trip the operator was going to pay anyway.

🔴 WHAT THIS DOES NOT MEASURE, stated plainly: the real-world nudge rate cannot be known
until this ships. The corpus is retrospective — it says how often the predicate WOULD
have fired on turns already written, not how the model's endings change once a hook is
nudging them, and not whether a nudged turn actually converts a `recommend` into a
`proceed`. The verifier for that is a re-run of the same prompt counts over
activity.events some weeks after deploy; nothing here establishes it.

--------------------------------------------------------------------------------------
"""
import sys, json, os, re

# --------------------------------------------------------------------------- #
# Tunables. Each carries its own evidence below, INCLUDING where there is none:
# TAIL_CHARS is measurably inert on this corpus and says so rather than borrowing
# the others' credibility.
# --------------------------------------------------------------------------- #

# Below this, the message is a terse answer, not a piece of work that owes a next step.
# p10 of the "operator replied with approval-or-recommend" population is ~1,530 chars, so
# 600 sits well under the real working range and only excludes genuinely short replies.
MIN_MESSAGE_CHARS = 600

# The positional tail: last paragraph blocks covering at least this many characters.
# NOT a tuned number — swept 10 / 200 / 400 / 800 / 1600 / 3200 / 10^9 against the final
# suppressor set and the fire count was 565 at EVERY point. See the "POSITIONAL" note in
# the module docstring: this bounds a pathological shape, it does not tune anything.
TAIL_CHARS = 800

# How much of the transcript's end to read when establishing turn shape. A turn larger
# than this is, by construction, a turn that did work — see _turn_shape.
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024

# A short interrogative opener is a factual question; answering it owes no next step.
SHORT_QUESTION_CHARS = 160

STATE_TOKEN = "fired"


def _state_root():
    """Read HOME at call time, not import time, so a test can point it somewhere safe."""
    return os.path.join(os.path.expanduser("~"), ".cache", "claude-next-step-nudge", "s")


# --------------------------------------------------------------------------- #
# Suppressors — a tail matching ANY of these already names a next step or asks.
# Read them as: "this tail is already forward-looking."
#
# The numbering below is presentational; order does not affect the result, since
# suppressed_by() evaluates all of them. Measured load over the 11,215-turn corpus —
# tails matched, and in brackets the tails where that suppressor was the ONLY match, i.e.
# what would start firing if it were deleted:
#
#     commits 7774 [2751] · offers 4799 [330] · marked 3867 [606] · asks 1979 [32]
#     headed 886 [40] · done 391 [55] · labelled 38 [5]
#
# `labelled` is the weak one and is kept knowingly: 5 unique tails is real but thin, and
# it is the cheapest guard here. Every suppressor including that one is confirmed to move
# the corpus fire count in BOTH directions (neutralised -> louder, saturated -> silent),
# so none of them is decoration.
# --------------------------------------------------------------------------- #

# 1. ASKS — a sentence-final '?'. The strongest single signal in the corpus: present in
#    the tail of 70% of turns the operator answered with a bare approval, versus 26% of
#    the turns where they had to ask for a recommendation. Anchored to end-of-line so a
#    rhetorical '?' mid-sentence does not count.
ASKS = re.compile(r"\?(?=[\s\"'*`)\]]*$)", re.M)

# 2. COMMITS — a forward-looking clause. Two shapes, both closed classes:
#    (a) first person + modal/volitional head ("I'll", "I would", "we can", "I plan to")
#    (b) IMPERSONAL future ("will merge", "'ll deploy", "going to", "about to").
#    (b) exists because a real corpus miss was "there'll be one more delta re-audit …
#    #256 merges and deploys as 0.42.0" — a perfectly good next step stated with no
#    first-person pronoun anywhere. A suppressor that only understood "I will" fired on
#    it. Widening a suppressor can only make this hook quieter, never louder.
COMMITS = re.compile(
    r"\b(?:I|we)\s*['’](?:ll|d|m|ve)\b"
    r"|\b(?:I|we)\s+(?:will|shall|can|could|would|should|may|might|am|are|"
    r"plan|intend|propose|recommend|suggest|expect|next|start|begin|go|do|run|open|"
    r"take|pick|move|dispatch|merge|ship|write|add|fix|land|leave|hold|keep|need|want)\b"
    r"|\b(?:will|shall)\s+(?:not\s+|then\s+|now\s+)?[a-z]+\b"
    r"|['’]ll\b"
    r"|\b(?:going to|about to|due to run|queued to|set to)\b",
    re.I)

# 3. OFFERS — an explicit hand-off to the operator: second-person and possessive shapes.
#    A tail containing one of these has already put the ball in the operator's court,
#    which is exactly the state this hook exists to produce.
#    The `your <noun>` arm is an enumerated set, not `your \w+`: "your repo is dirty" is
#    a statement about the world, not a hand-off, and the open form would suppress it.
OFFERS = re.compile(
    r"\b(?:say the word|let me know|want me to|shall I|should I|do you want|"
    r"tell me|yours to|up to you|for you to|left (?:it |that |the \w+ )?to you|"
    r"your (?:call|review|go[- ]?ahead|choice|decision|say|word|go|court|shout|"
    r"move|turn|PR|approval|sign[- ]?off)|"
    r"(?:ready|over|up) for (?:your )?review|"
    r"if you(?:['’]d| would)? (?:like|want|prefer)|on your (?:say|word|go)|"
    r"awaiting|standing by|ready when you are|whenever you)\b",
    re.I)

# 4. MARKED — a recommendation marked as such. Covers the "explicit numbered ask with a
#    recommendation marked" ending, which is a legitimate way to end a turn: the operator
#    can answer it with one word even though it is a question, not a commitment.
#    The `worth <gerund>` / `worth a <noun>` arm is the operator-directed suggestion
#    shape — "worth folding into the skill", "worth a glance before merging" — which the
#    corpus produced repeatedly and an earlier revision fired on.
MARKED = re.compile(
    r"\b(?:recommend(?:ed|ation|ations|ing|s)?|suggest(?:ed|ion|ions|s)?|"
    r"I['’]?d\s+(?:go|start|pick|take|do|lean|leave|say)|"
    r"my (?:pick|call|vote|recommendation|preference)|"
    r"default(?:s|ing)? to|lean(?:s|ing)? toward|"
    r"worth (?:a |an )?(?:glance|look|check|[a-z]+ing)|"
    r"the (?:one|first) (?:I['’]?d|to do))\b",
    re.I)

# 5. HEADED — a heading or bold lead-in that opens a forward-looking section. Structural:
#    it must be at the START of a line and carry heading markup ('#' or '**'), so a
#    mention of the word "plan" in running prose does not match. Up to two leading
#    modifier words are allowed inside the markup — a real corpus miss was
#    "**Ranked next steps** put the stale-node sweep ahead of …", a perfectly good
#    next-step section that an anchored-at-the-first-word pattern walked straight past.
HEADED = re.compile(
    r"(?:^|\n)\s*(?:#{1,6}\s*|\*\*|\d+[.)]\s+\*\*)\s*(?:[A-Za-z-]+ ){0,2}"
    r"(?:next|next steps?|what['’]?s next|recommend(?:ed|ation)s?|"
    r"plan|proposed|proposal|remaining|open items?|to ?do|follow[- ]ups?)\b",
    re.I)

# 6. LABELLED — the same forward-looking section opener as HEADED but with NO markdown
#    markup, which is how it is written about as often: a line that BEGINS with a label
#    word and is immediately followed by a colon or dash. Structural in both position
#    (line-initial) and punctuation (the label separator) — "the next thing I checked
#    was" in running prose does not match, because it is neither line-initial nor
#    followed by a separator. Split out from HEADED rather than folded in so a mutation
#    to either is attributable to exactly one test.
LABELLED = re.compile(
    r"(?:^|\n)\s*(?:next(?: steps?| up)?|then|after (?:that|this)|from here|"
    r"remaining|to ?do|follow[- ]ups?|the (?:sequence|plan|order|next step))"
    r"\s*[:—–-]",
    re.I)

# 7. DONE — an explicit statement that nothing follows. This is a next step: the whole
#    point of the nudge is to remove the operator's round trip, and "nothing further,
#    this is done" removes it just as well as naming an action.
#
#    🔴 SELF-CONSISTENCY, and it is load-bearing: NUDGE below tells the model that
#    "nothing further; this is done" is a valid ending. Without this suppressor the hook
#    would fire on a turn that had followed its own instruction — the exact shape that
#    teaches an operator the hook is noise. Pinned by a test that feeds NUDGE's own
#    prescribed endings back through the predicate.
DONE = re.compile(
    r"\bnothing (?:further|else|more|left|remaining|outstanding|to do|follows)\b"
    r"|\bno (?:further|remaining|outstanding|other) (?:steps?|work|actions?|items?|"
    r"follow[- ]ups?|changes?)\b"
    r"|\b(?:all|now) done\b|\bthat['’]?s (?:it|all|everything)\b"
    r"|\bthis (?:is|task is|work is) (?:done|complete|finished)\b"
    r"|\b(?:work|it) is (?:complete|finished)\b",
    re.I)

SUPPRESSORS = (
    ("asks", ASKS),
    ("commits", COMMITS),
    ("offers", OFFERS),
    ("marked", MARKED),
    ("headed", HEADED),
    ("labelled", LABELLED),
    ("done", DONE),
)

# A user message that ENDS the exchange. The turn that answers one of these owes no next
# step — the operator has already said they are done. Anchored whole-message (the whole
# prompt is the word), never a substring: "stop the timer and then …" is not terminal.
TERMINAL_PROMPT = re.compile(
    r"^\s*(?:/(?:clear|compact|exit|quit|resume)\b.*"
    r"|(?:thanks?|thank you|ty|thx|stop|halt|cancel|abort|nvm|never ?mind|done|"
    r"bye|goodnight|gn|ok|okay|k|cool|nice|great|perfect|got it|understood|"
    r"no|nope|nothing|that['’]?s all|all good)\W*)$",
    re.I | re.S)

# A short interrogative opener — "who did the semver commit", "which pod is OOMing?".
# Wh-initial OR question-marked, AND short. Both halves are needed: a 2 KB prompt that
# happens to end in '?' is a task, not a lookup.
WH_INITIAL = re.compile(
    r"^\s*(?:who|what|which|when|where|why|how|is|are|was|were|does|do|did|can|could|"
    r"should|would|has|have|had|any)\b",
    re.I)


def tail_of(text, nchars=TAIL_CHARS):
    """The message's final paragraph blocks, taken from the end until >= nchars covered.

    Positional by construction: blocks are split on blank lines and consumed backwards,
    so the window always starts at a paragraph boundary. A single huge final paragraph
    yields itself whole — deliberately, since truncating mid-paragraph could cut a
    commitment in half and turn a suppressed turn into a firing one.
    """
    if not text:
        return ""
    blocks = re.split(r"\n\s*\n", text.rstrip())
    out, total = [], 0
    for b in reversed(blocks):
        out.append(b)
        total += len(b)
        if total >= nchars:
            break
    return "\n\n".join(reversed(out))


def suppressed_by(text):
    """Names of every suppressor matching the tail of `text`. Empty => the turn named
    no next step. Returned as a list (not a bool) so a test can pin WHICH guard fired —
    a mutation that breaks one suppressor must not be masked by another still matching.
    """
    t = tail_of(text)
    return [name for name, rx in SUPPRESSORS if rx.search(t)]


def names_next_step(text):
    """True if the turn already names a next step, asks, or offers a marked choice."""
    return bool(suppressed_by(text))


def is_terminal_prompt(prompt):
    return bool(prompt) and bool(TERMINAL_PROMPT.match(prompt))


def is_short_question(prompt):
    """A brief factual lookup: short AND interrogative in shape."""
    if not prompt:
        return False
    p = prompt.strip()
    if len(p) > SHORT_QUESTION_CHARS:
        return False
    return bool(WH_INITIAL.match(p)) or p.endswith("?")


# --------------------------------------------------------------------------- #
# Turn shape, from the transcript tail
# --------------------------------------------------------------------------- #
def _is_real_user(rec):
    """A genuine operator prompt, not a tool_result the harness wrote back as `user`."""
    if rec.get("type") != "user" or rec.get("isSidechain"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result"
                       for b in content)
    return False


def _user_text(rec):
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _turn_shape(path):
    """(opening_prompt, tool_use_count) for the turn that just ended, or None.

    None means "could not establish", and every caller treats that as do-not-fire.

    Reads only the last TRANSCRIPT_TAIL_BYTES so cost is bounded on a multi-MB
    transcript, and drops the first (probably partial) line. If no operator prompt
    appears in that window the turn is larger than the window, which is itself proof it
    did work — reported as (None, large) so the work gate passes and the prompt-shaped
    gates are skipped rather than guessed at.

    🔴 Only the transcript's OLDER records are read here. The final assistant message is
    documented to lag the transcript, which is exactly why `last_assistant_message` from
    the payload is used for the text instead of anything found in this file.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TRANSCRIPT_TAIL_BYTES:
                fh.seek(size - TRANSCRIPT_TAIL_BYTES)
                fh.readline()  # discard the partial line
            raw_lines = fh.readlines()
    except Exception:
        return None

    recs = []
    for raw in raw_lines:
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if isinstance(rec, dict):
            recs.append(rec)

    tools = 0
    prompt = None
    seen_user = False
    for rec in reversed(recs):
        if _is_real_user(rec):
            prompt = _user_text(rec)
            seen_user = True
            break
        if rec.get("type") == "assistant" and not rec.get("isSidechain"):
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):
                tools += sum(1 for b in content
                             if isinstance(b, dict) and b.get("type") == "tool_use")

    if not seen_user:
        # 🔴 "No operator prompt in the window" has TWO causes and the observable is the
        # same for both: (a) the turn is larger than the window — real, and proof it did
        # work; (b) the file is corrupt, truncated, or is not a transcript at all — from
        # which nothing at all follows. Picking (a) because it was the case I had in mind
        # is a coin flip, and it resolves toward FIRING, which is the expensive direction.
        #
        # The upstream signal the two disagree about is whether ANY tool_use record
        # parsed: an oversized turn's window is full of them, a corrupt file yields none.
        # So require that evidence, and return "could not establish" without it. Found by
        # test_fail_open_on_a_binary_transcript, which fired the hook on 4 KB of
        # /dev/urandom before this branch existed.
        return (None, tools) if tools >= 1 else None
    return (prompt, tools)


# --------------------------------------------------------------------------- #
# Once-per-session claim. Same atomic O_EXCL test-and-set as search-tool-nudge.py,
# with the fail direction REVERSED: an unwritable state dir means we cannot promise
# once-per-session, and an unbounded Stop-blocker is the one outcome worse than a
# missed nudge — so an unclaimable token means DO NOT FIRE.
# --------------------------------------------------------------------------- #
def _sanitize(part):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", part)[:120]


def _state_dir(data):
    session = data.get("session_id")
    if not isinstance(session, str) or not session:
        return None
    return os.path.join(_state_root(), _sanitize(session))


def already_fired(state_dir):
    if not state_dir:
        return True
    try:
        return STATE_TOKEN in os.listdir(state_dir)
    except FileNotFoundError:
        return False
    except Exception:
        return True


def claim(state_dir):
    """True iff THIS process created the session's single token. Fails CLOSED.

    O_CREAT|O_EXCL is one atomic test-and-set, so of N concurrent processes exactly one
    gets True. There is no lock, so this can never hang a Stop.

    🔴 ONE handler, deliberately, and this is where it diverges from
    search-tool-nudge.py's claim(): that one splits makedirs and open into two handlers
    because its two failure modes have DIFFERENT answers (an unwritable state dir must
    fail OPEN and emit; only a lost race may return False). Here both answers are False —
    FileExistsError, an unwritable directory and a state root that is a regular file all
    mean "this token is not provably ours", and the only safe response to that is
    silence. Two handlers returning the same value would be decoration: a mutation sweep
    deleted the first one and no test could tell, which is what prompted this collapse.
    """
    if not state_dir:
        return False
    try:
        os.makedirs(state_dir, exist_ok=True)
        os.close(os.open(os.path.join(state_dir, STATE_TOKEN),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except Exception:
        return False


NUDGE = (
    "next-step: this turn ended without saying what happens next, so answering it "
    "costs the operator an extra round trip just to ask.\n"
    "Add ONE short closing line before you stop — either:\n"
    "  • the action you would take next, stated so the reply can be a single "
    "\"proceed\"; or\n"
    "  • a numbered choice with YOUR recommendation marked.\n"
    "If the work is genuinely finished and nothing follows, say that explicitly — "
    "\"nothing further; this is done\" is a valid next step.\n"
    "Do not redo any work, re-run any command, or restate the summary: append the one "
    "line and stop. (Fires at most once per session.)"
)


def should_nudge(data, transcript_reader=_turn_shape):
    """Pure decision, no side effects. True => this turn should be nudged.

    Split out from main() so the gates can be tested directly AND mutation-tested one at
    a time; main() adds only the claim and the exit-2 emission.
    """
    if not isinstance(data, dict):
        return False
    if data.get("hook_event_name") not in (None, "Stop"):
        return False          # SubagentStop and friends: a subagent's turn never
    if data.get("agent_id"):  # reaches the operator, so it owes them no next step.
        return False
    if data.get("stop_hook_active"):
        return False          # never block twice in a row
    stop_reason = data.get("stop_reason")
    if stop_reason not in (None, "", "end_turn"):
        return False          # max_tokens/tool_use is truncation, not a considered end

    msg = data.get("last_assistant_message")
    if not isinstance(msg, str) or len(msg) < MIN_MESSAGE_CHARS:
        return False

    if names_next_step(msg):
        return False

    path = data.get("transcript_path")
    if not isinstance(path, str) or not path:
        return False
    shape = transcript_reader(path)
    if shape is None:
        return False          # cannot establish the turn's shape -> stay silent
    prompt, tools = shape
    if tools < 1:
        return False          # no work in this turn -> nothing to have a next step for
    if prompt is not None:
        if is_terminal_prompt(prompt):
            return False
        if is_short_question(prompt):
            return False
    return True


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        if not should_nudge(data):
            sys.exit(0)
        state_dir = _state_dir(data)
        if already_fired(state_dir):
            sys.exit(0)
        if not claim(state_dir):
            sys.exit(0)
        sys.stderr.write(NUDGE + "\n")
        sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
