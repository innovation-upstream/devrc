#!/usr/bin/env python3
"""hook_telemetry — ONE structured row per Stop-hook DECISION, into the activity spool.

WHY THIS EXISTS — MEASURED, AND PAID FOR THREE TIMES IN ONE SESSION
-------------------------------------------------------------------
Seven Stop hooks fire ~20,700 times per six weeks on this host and NONE of them is
visible to the telemetry this repo already runs. `activity.events` records no hook
invocation at all: the `claude` source tails the same transcripts but keeps only
`prompt` / `command` / `session-summary`. Every question about what a hook decided
therefore has to be answered by INFERRING the decision from transcript prose, and in
one session that cost three wrong answers:

  * a compliance measurement returned a bogus **100.0%**, because compliance had to be
    inferred from transcript text and each guard's own re-fired message CONTAINS the
    strings that define compliance. The guard was grading itself;
  * a lift figure was wrong (+5.3pp against a correct +10.1pp) because the detector
    matched **any** task id rather than the BLOCKED one — there was no per-entity key
    to join on, so the entity had to be guessed;
  * a hypothesis could not be tested at all, because 85 of 92 relevant comment bodies
    were passed via `--body-file`/heredoc and never reached the transcript.

All three are facts the hook HELD at the moment it fired. Answering them took a 7.5 GB
transcript scan. This module makes them a query instead — one row, at the moment of
the decision, carrying the decision, the entity it was about, and whether the live read
it depended on actually succeeded.

🔴 WHAT A ROW IS FOR, AND WHY DENOMINATORS ARE EMITTED TOO
-----------------------------------------------------------
A wired hook emits a row on EVERY Stop it sees, not only when it acts. A row set
containing only fires cannot answer "out of how many?", which is precisely the shape
that produced the bogus 100.0%: a numerator with no denominator, dressed as a rate. So
`suppressed` and `armed` rows are as load-bearing as `fired` ones, and
`could-not-measure` is its OWN value — never folded into a clean result, because
"we looked and the answer was no" and "we could not look" are different facts that
share an observable.

CONTRACT — THREE HARD PROMISES
-------------------------------
  1. 🔴 FAIL-OPEN, ALWAYS, SILENTLY. These are Stop hooks: a raising, blocking or
     chatty emitter perturbs the operator's turn at the exact moment a session is
     trying to end. Every path here returns a value; nothing raises, nothing writes to
     stdout or stderr, nothing spawns a process, nothing opens a socket.
     `emit_decision` returns "" on any failure and call sites still wrap it as
     defence-in-depth.

     🔴 AND NOTHING BLOCKS ON A SPOOL FILE THAT IS NOT A REGULAR FILE — stated
     separately because "does not raise" does NOT imply "does not hang", and a hang is
     STRICTLY WORSE than this module not existing. MEASURED: with a FIFO at
     `<spool>/current.log` and no reader, `spool_emit.emit`'s `open(..., "a")` blocks
     forever and the hook never exits; the wired hook did not return within 12 s. So
     `emit_decision` stats the target first and refuses anything that is not a regular
     file (see `_spool_target_is_a_regular_file`).

     🔴 WHAT THAT DOES NOT COVER, NAMED RATHER THAN IMPLIED. The check is a `stat`
     before an `open`, so it is TOCTOU-racy against a writer that swaps the path in
     between, and it cannot bound an ordinary write to an ordinary file that stalls in
     the kernel (a hung NFS mount, a device that stopped responding). The honest claim
     is therefore bounded: no reachable NON-REGULAR spool target can block a hook, and
     a pathological FILESYSTEM still can. Bounding the latter needs a timeout around
     the write, which is `spool_emit`'s to own — it is the shared collector module with
     its own consumers, and this module must not fork its behaviour.

  2. 🔴 NO NETWORK ON THE HOOK PATH. The row is a single `O_APPEND` line to the local
     spool via `spool_emit` — the same v1 line the collector daemon already ships. The
     daemon does the shipping, out of band. If the daemon is not running the line
     accumulates locally and is never shipped, which is the graceful telemetry-off
     no-op. There is deliberately no HTTP client, no subprocess and no argv payload:
     a `payload build failed` argv trap already cost this repo a ~96%-dead hook.

  3. 🔴 NO CAPTURED TEXT — AND THE GUARANTEE IS TWO-PART, BECAUSE THIS MODULE ALONE
     CANNOT DELIVER IT. Read both halves; the first one on its own is the claim this
     file used to make, and it was FALSE.

     (a) HERE: every value that reaches the payload must be an ID, SLUG, ENUM, BOOLEAN
         or COUNT, and that is checked rather than promised — a string is admitted only
         if it matches `_SAFE_TOKEN`, a character class with NO SPACE in it. A value
         that fails is DROPPED WHOLE rather than truncated-and-shipped, and every drop
         is COUNTED (`dropped` in the payload) so it is visible rather than invisible.
         This is structural, not spelled: it does not look for words that indicate
         prose, it admits only shapes prose cannot take.

     (b) 🔴 AT THE CALL SITE: a caller must not LAUNDER. `_SAFE_TOKEN` rejects prose;
         it cannot reject prose that was already rewritten into token shape, and a
         sanitizer that maps every disallowed character to `_` does exactly that.
         MEASURED on this branch before the fix: `handoff-write-guard` keyed its ledger
         on `_sanitize(basename)`, and a `Read` of
         `claudedocs/handoff- <a sentence someone typed>.md` arrived here as one
         underscore-joined token that `_SAFE_TOKEN` admitted — up to 120 characters of
         operator- or model-chosen text, past a boundary advertised as
         construction-proof. So "prose cannot satisfy `_SAFE_TOKEN` by construction" is
         true of PROSE and false of LAUNDERED prose, and the second half of the promise
         is the call site's: pass a value you did not rewrite. `handoff-write-guard`
         now enforces that explicitly — it drops its entity when sanitizing the
         basename was not a no-op (`telemetry_entity` there), which is the only way the
         round trip can be known to have changed nothing.

WHY A SECOND EMITTER MODULE AND NOT `scripts/collector/invocation.py`
----------------------------------------------------------------------
`invocation.py` is the existing on-demand emitter and this module reuses its PATTERN
and its SPOOL PATH (`spool_emit`, the one definition of the v1 line). It does not
reuse the module, for two reasons that are facts about deployment and about the
consumer, not preferences:

  * LOCATION. A hook runs as `~/.claude/hooks/<name>.py`, so Python puts
    `~/.claude/hooks/` on `sys.path` — and `nix/home.nix` does NOT deploy
    `invocation.py` anywhere on this host (the collector ships `emit`, `collector.py`,
    `changed_paths.py`, `mention_scan.py`, `lib/`, `claude/`, `opencode/`, `keylog/`,
    `browser-ext/` and `i3/` — that list does not include it). `spool_emit` IS
    deployed, at `~/.config/activity-collector/keylog/`, which is why THAT is the
    dependency.
  * CONSUMER. `session-analysis/adoption-scan.py` reads `source='tool'
    kind='invocation'` as the adoption signal for shipped TOOLS. A Stop hook is not a
    tool the operator chose to run; folding thousands of hook decisions into that
    stream would change what every adoption number means.

So the row is `source='hook' kind='stop-decision'`, queryable on its own and joinable
to everything else on `session`.

ROW SHAPE
---------
    source=hook  kind=stop-decision  text=<hook name>  session=<session id>
    payload = {"hook", "decision", "entity", "entity_kind", "satisfier",
               "satisfied", "measured", "reason", ...extra, "dropped"?}

  * `text` carries the hook name as well as the payload, so a consumer can group by
    hook without parsing JSON (the same choice `invocation.build_fields` makes).
  * `satisfied` is a TRISTATE: True = the satisfying act was found, False = it was
    looked for and not found, **None = it was never evaluated**. A two-valued field
    here would report "not satisfied" for a decision that never got that far.
  * `measured` says whether the live read the decision rests on succeeded. A row with
    `measured=False` carries `decision="could-not-measure"`; the pair is redundant on
    purpose, so a consumer filtering on either one gets the same population.

🔴 THE DENOMINATOR UNIT IS NOT THE SAME FOR EVERY HOOK — SAY WHICH BEFORE DIVIDING
-----------------------------------------------------------------------------------
"A row on every Stop" is true of `next-step-nudge`, which reasons about the session
and emits exactly ONE row per Stop. It is NOT true of `handoff-write-guard`, which
reasons per tracked DOCUMENT and emits one row per DECISION — up to `MAX_DOCS` of them
for a single Stop. Counting guard rows therefore counts decisions, not Stops, and a
rate whose denominator is `count(*)` over that hook is wrong by however many documents
each session had open.

So the guard stamps every row of one Stop with the same `stop` extra — a per-process
random token — and the Stop count is `uniqExact(stop)`. The nudge does not carry one:
for a hook that emits one row per Stop the row IS the Stop, and a column that only ever
holds distinct values buys nothing. A consumer wanting one expression across both hooks
should use `coalesce(stop, session || '/' || toString(ts))`, or simply group the nudge
by row.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys

SOURCE = "hook"
KIND = "stop-decision"

# --------------------------------------------------------------------------- #
# THE DECISION VOCABULARY — closed, and pinned two-way against the wired hooks by
# `tests/test_hook_telemetry.py::test_the_decision_vocabulary_is_pinned_two_way`.
#
# 🔴 THE FOUR ARE NOT THREE PLUS A DEGENERATE CASE. `could-not-measure` exists
# because "we measured and nothing was owed" and "we could not measure" produce the
# SAME observable — silence — and picking whichever one you already believed is a coin
# flip that gets recorded as a finding. `armed` exists for the same reason one level
# in: a hook that was eligible to act and was withheld by its own BUDGET (a
# once-per-session claim, a spent escalation ladder) is not the same population as one
# whose gates said no work was owed, and a schema that cannot tell them apart makes
# every compliance rate off by the size of the budget.
# --------------------------------------------------------------------------- #
# 🔴 `fired` MEANS "THE HOOK ACTED ON A MEASURED FINDING", NOT "THE OPERATOR SAW
# SOMETHING". The two came apart in `handoff-write-guard`: a doc whose state could not
# be read spends its own rung of the escalation ladder and can emit an operator-visible
# NOTICE while the row correctly records `could-not-measure`, because what was
# unmeasurable does not become measured by being spoken about. So visibility is NOT
# derivable from `decision` — it is `rung` in `extra` (`silent` / `notice` / `block`)
# that says whether anything reached a human, and a consumer asking "how often did this
# hook speak?" must read that, never `decision = 'fired'`.
DECISION_FIRED = "fired"            # acted on a measured finding
DECISION_ARMED = "armed"            # eligible, but withheld by a budget/claim
DECISION_SUPPRESSED = "suppressed"  # a substantive gate said no action was owed
DECISION_UNMEASURED = "could-not-measure"   # the live read failed; NOT a clean result
DECISIONS = frozenset({DECISION_FIRED, DECISION_ARMED,
                       DECISION_SUPPRESSED, DECISION_UNMEASURED})

# 🔴 THE KILL SWITCH. Set it and this module is a no-op with no import cost beyond
# reading one environment variable — which is also what makes the added-latency claim
# measurable A/B on the IDENTICAL binary rather than against a hand-edited copy.
OFF_ENV = "HOOK_TELEMETRY_OFF"

# --------------------------------------------------------------------------- #
# The privacy boundary's FIRST HALF, as a shape rather than a word list.
#
# 🔴 THERE IS NO SPACE IN THIS CHARACTER CLASS, AND THAT IS HALF THE GUARD. Every value
# a wired hook passes is an id, a slug, an enum or a count; every form of captured text
# this repo forbids — a comment body, a prompt, an assistant message, a transcript
# excerpt — contains whitespace within the first few tokens. A truncating cap would
# SHIP the first 120 characters of a leaked message; this drops it whole. The cost is
# named rather than hidden: a legitimate value that happens to carry a space is dropped
# too, and the `dropped` counter is how that becomes visible instead of silent.
#
# 🔴 AND IT IS ONLY HALF, BECAUSE A CALLER CAN LAUNDER PAST IT. This regex decides
# whether a string LOOKS like a token; it cannot ask where the token came from. A call
# site that replaces every disallowed character with `_` hands it a compliant string
# built out of arbitrary text, and it is admitted — measured on this branch, see
# promise 3(b) in the module docstring. "Prose cannot satisfy this by construction" is
# therefore a claim about PROSE, not about every value that can reach here; the other
# half is the call site's obligation not to rewrite.
# --------------------------------------------------------------------------- #
_MAX_LEN = 120
_MAX_LIST = 8
_MAX_EXTRA = 8
_MAX_INT = 10 ** 12
# 🔴 `{0,…}`, NOT `{1,…}`: the EMPTY STRING IS A LEGITIMATE VALUE and rejecting it was
# a real defect, caught by the first end-to-end run. `reason` is `""` on the one
# decision that matters most — a fire — so a `{1,…}` bound dropped the field from
# every fired row and marked it in `dropped`, i.e. the guard reported a privacy
# rejection for the absence of a reason. An empty string carries no text to leak.
_SAFE_TOKEN = re.compile(r"\A[A-Za-z0-9_.:@/+#-]{0,%d}\Z" % _MAX_LEN)

# Where `spool_emit` can be, in priority order. Resolved at CALL time (so a test can
# redirect `$HOME`), and each candidate is a plain `isfile` check — no import is
# attempted against a directory that does not hold the module.
_SPOOL_EMIT_MODULE = "spool_emit.py"


def _candidate_dirs():
    """Directories that may hold `spool_emit.py`, most specific first.

    Two layouts, and both are real:
      * THE REPO — this file is `scripts/claude-hooks/hook_telemetry.py`, so the
        collector is `../collector/keylog` from here.
      * THE HOST — `nix/home.nix` deploys this file to `~/.claude/hooks/` (a symlink
        into `/nix/store`, whose `resolve()`d parent has no collector beside it) and
        the collector to `~/.config/activity-collector/`. The repo candidate simply
        does not exist there, which is why this is a LIST and not a branch on which
        layout we think we are in.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    out = [os.path.join(os.path.dirname(here), "collector", "keylog")]
    home = os.environ.get("HOME") or os.path.expanduser("~")
    out.append(os.path.join(home, ".config", "activity-collector", "keylog"))
    return out


_UNRESOLVED = object()
_SE = _UNRESOLVED


def spool_emit_module():
    """`spool_emit`, or None. Memoised — including a memoised FAILURE.

    🔴 LAZY, and measured rather than stylistic: `spool_emit` drags in `base64`,
    `datetime` and `socket`, none of which a hook otherwise loads. `mention-open.py`
    measured that same import at a median 3.7 ms on this host. A hook process emits at
    most a handful of rows, so the import is paid once or not at all.
    """
    global _SE
    if _SE is not _UNRESOLVED:
        return _SE
    _SE = None
    for d in _candidate_dirs():
        try:
            if not os.path.isfile(os.path.join(d, _SPOOL_EMIT_MODULE)):
                continue
            if d not in sys.path:
                sys.path.insert(0, d)
            import spool_emit  # noqa: PLC0415 — lazy by design, see the docstring
            _SE = spool_emit
            break
        except Exception:  # noqa: BLE001 — a broken candidate is a no-op, not an error
            continue
    return _SE


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #
_DROP = object()


def coerce(value):
    """A payload value, or `_DROP`. Pure, total, and never raises.

    Admitted: bools, None (an explicit "not evaluated"), bounded ints/floats, strings
    matching `_SAFE_TOKEN`, and short lists whose members are themselves admitted
    strings. Everything else — an object, a huge number, a string with a space in it —
    is dropped. `str()` is never called on an arbitrary object: a `__str__` that raises
    is one more way a telemetry helper can take a hook down with it, and the way to not
    have that failure mode is to not have the call.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if -_MAX_INT < value < _MAX_INT else _DROP
    if isinstance(value, float):
        # NaN/inf do not survive JSON round-tripping through ClickHouse cleanly.
        return value if -_MAX_INT < value < _MAX_INT else _DROP
    if isinstance(value, str):
        return value if _SAFE_TOKEN.match(value) else _DROP
    if isinstance(value, (list, tuple)):
        out = [v for v in (coerce(x) for x in list(value)[:_MAX_LIST]) if v is not _DROP]
        return out
    return _DROP


def build_payload(hook, decision, entity=None, entity_kind=None, satisfier=None,
                  satisfied=None, measured=None, reason=None, extra=None):
    """The JSON payload dict for one decision row. Pure.

    Every field goes through `coerce`, INCLUDING the ones this module names itself:
    a hook that passes a malformed decision must not be the one call site that bypasses
    the privacy boundary. Dropped keys are absent from the payload and counted in
    `dropped`, which is present ONLY when something was dropped — so a consumer can
    filter on its existence rather than on a zero that every row carries.
    """
    fields = [
        ("hook", hook),
        ("decision", decision),
        ("entity", entity),
        ("entity_kind", entity_kind),
        ("satisfier", satisfier),
        ("satisfied", satisfied),
        ("measured", measured),
        ("reason", reason),
    ]
    # 🔴 `extra` MAY NOT OVERWRITE A RESERVED KEY. Nothing stops a future call site
    # passing `extra={"decision": …}`, and because extras are appended AFTER the fixed
    # fields it would silently win — a row whose `decision` column no longer means what
    # every consumer reads it as. Rejected and counted like any other bad value, so the
    # collision is visible in `dropped` rather than being a schema corruption nobody
    # can see. Enumerated from the fixed list itself, so a new fixed field is covered
    # without a second list to keep in step.
    #
    # 🔴 THE `[:_MAX_EXTRA]` TRUNCATION IS COUNTED TOO. It used to be the ONE place this
    # module dropped a value silently, which contradicted the only promise it makes
    # about drops — that they are visible in `dropped` rather than invisible. A row over
    # the cap looked identical to a caller that never passed those keys.
    reserved = {name for name, _ in fields}
    dropped_collisions = 0
    if isinstance(extra, dict):
        extras = [(k, v) for k, v in extra.items() if k not in reserved]
        dropped_collisions = len(extra) - len(extras)
        dropped_collisions += max(0, len(extras) - _MAX_EXTRA)
        fields.extend(extras[:_MAX_EXTRA])
    payload, dropped = {}, dropped_collisions
    for key, raw in fields:
        k = coerce(key)
        if k is _DROP or not isinstance(k, str):
            dropped += 1
            continue
        v = coerce(raw)
        if v is _DROP:
            dropped += 1
            continue
        payload[k] = v
    if dropped:
        payload["dropped"] = dropped
    return payload


def build_fields(hook, decision, session=None, **kw):
    """The spool field dict for one decision row. Pure.

    `source`/`kind`/`text`/`session` are the v1 line's own columns; everything else
    lives in the JSON `payload`. `session` is emitted as a top-level column rather than
    inside the payload because that is the join key against `source='claude'` rows —
    the per-entity key whose absence produced the wrong lift figure this module exists
    for.

    🔴 THERE IS NO `duration_ms` PARAMETER. One was written here and had no call site
    anywhere in the tree — a v1 column this module does not measure, whose coercion
    branch no test could reach and no mutation could kill. Deleted rather than kept as
    a shape a reader would mistake for a supported field. `spool_emit` writes it as a
    plain scalar if a future caller genuinely measures one, so adding it back is a
    two-line change made at the point there is something to put in it.
    """
    name = coerce(hook)
    fields = {
        "source": SOURCE,
        "kind": KIND,
        "text": name if isinstance(name, str) else "",
        "payload": json.dumps(build_payload(hook, decision, **kw),
                              ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")),
    }
    sess = coerce(session)
    if isinstance(sess, str) and sess:
        fields["session"] = sess
    return fields


# --------------------------------------------------------------------------- #
# Emission
# --------------------------------------------------------------------------- #
def _spool_target_is_a_regular_file(se, spool_dir):
    """False ONLY when `<spool>/current.log` exists and is not a regular file.

    🔴 THIS IS THE ANTI-HANG CHECK, AND A HANG IS WORSE THAN A CRASH. `spool_emit.emit`
    opens the log with a blocking `open(..., "a")`. On a FIFO with no reader that call
    never returns, so the Stop hook never exits and the operator's turn never ends —
    MEASURED: the wired hook did not come back within 12 s. `spool_emit` swallows
    `OSError`, which covers every way the write can FAIL and none of the ways it can
    WAIT, so this is the only place the wait can be refused without changing a shared
    collector module out from under its other consumers.

    🔴 ABSENT MEANS PROCEED, and that is the common case rather than an edge: the log
    does not exist until the first emit creates it, and `stat` on a missing path raises
    `FileNotFoundError`. Every other `OSError` (a permission-denied parent, ENOTDIR
    because the spool dir is itself a file) is also proceed — those are states
    `spool_emit`'s own handler already turns into a silent no-op, and refusing them
    here would be a second copy of a decision already made correctly one layer down.

    ⚠ TOCTOU: this stats, then `emit` opens. A writer that replaces the path in the gap
    defeats it. Named rather than hidden — the threat model is a misconfigured
    `ACTIVITY_SPOOL_DIR`, not an adversary racing a Stop hook.
    """
    try:
        d = spool_dir if spool_dir is not None else se.default_spool_dir()
        st = os.stat(os.path.join(str(d), se.CURRENT_NAME))
    except OSError:
        return True
    return stat.S_ISREG(st.st_mode)


def emit_decision(hook, decision, spool_dir=None, **kw):
    """Emit ONE decision row, best-effort. Returns the written line, or "".

    🔴 NEVER RAISES, NEVER PRINTS AND NEVER BLOCKS ON A NON-REGULAR SPOOL TARGET.
    `except Exception` rather than `except BaseException` is deliberate: `SystemExit`
    and `KeyboardInterrupt` must sail through — swallowing the first would let a
    telemetry call cancel a hook's own exit, which is the opposite of not perturbing
    the turn. The blocking half is `_spool_target_is_a_regular_file`; read its
    docstring for what that promise does NOT cover.
    """
    try:
        if os.environ.get(OFF_ENV):
            return ""
        se = spool_emit_module()
        if se is None:
            return ""
        if not _spool_target_is_a_regular_file(se, spool_dir):
            return ""
        return se.emit(build_fields(hook, decision, **kw), spool_dir=spool_dir)
    except Exception:  # noqa: BLE001 — telemetry never costs its caller anything
        return ""


def emit_rows(rows, spool_dir=None):
    """Emit a list of `{hook, decision, ...}` dicts. Returns the number ACCEPTED.

    Per-row isolation: one malformed row does not cost the rows after it. The return
    value is a COUNT rather than a bool so a caller — or a positive control in a test —
    can watch the number move.

    🔴 "ACCEPTED", NOT "ON DISK", AND THE DIFFERENCE IS NOT PEDANTRY. `spool_emit.emit`
    swallows its own `OSError` and STILL returns the line, so a row counted here may
    have reached nothing: an unwritable spool dir, a full disk and a `current.log` that
    is a directory all produce a non-empty return. What this number does establish is
    that the row was built, passed the privacy boundary and was handed to the emitter —
    which is what a positive control here needs, because the alternative it is
    distinguishing is a caller wired to nothing. A control that must prove BYTES landed
    has to read the spool file back; the suites here do exactly that (`rows(spool)`),
    and this count is deliberately not offered as a substitute for it.
    """
    n = 0
    try:
        items = list(rows or ())
    except Exception:  # noqa: BLE001
        return 0
    for row in items:
        try:
            if not isinstance(row, dict):
                continue
            kw = dict(row)
            hook = kw.pop("hook", None)
            decision = kw.pop("decision", None)
            if emit_decision(hook, decision, spool_dir=spool_dir, **kw):
                n += 1
        except Exception:  # noqa: BLE001
            continue
    return n
