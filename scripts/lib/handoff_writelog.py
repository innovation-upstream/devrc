#!/usr/bin/env python3
"""handoff_writelog — ONE JSONL line per `handoff_doc.py` write ATTEMPT, plus the reader.

    python3 scripts/lib/handoff_writelog.py                       # rates, default log
    python3 scripts/lib/handoff_writelog.py --log <file.jsonl>     # a specific log
    python3 scripts/lib/handoff_writelog.py --json                 # machine-readable

WHY THIS EXISTS — THE MEASUREMENT IT MAKES POSSIBLE, STATED AS THE GAP IT CLOSES
--------------------------------------------------------------------------------
`handoff_doc.py` is the /handoff write gate and it had ZERO logging. The cost the
gate is believed to impose is a SECOND PASS: a session composes content, the write
is refused at rule (p) (`status=size-ratchet`, exit 14) or rule (q)/(r)
(`prune-refused` 15 / `prune-unconserved` 16), the session picks an exit flag
(`--prune` / `--archive-write` / `--autoevict` / `--override-size-ratchet`) and
re-runs. The re-run is the cost.

🔴 GIT CANNOT ANSWER WHETHER THAT COST FELL, AND THAT IS THE WHOLE ARGUMENT FOR A
LOG. `git log` records the committed END STATE, so it cannot distinguish

    "evicted BEFORE composing"  (the near-ceiling warning worked)
    "evicted AFTER a refusal"   (the warning did not, and a pass was paid)

and that ORDERING is the entire claim. An attempt that was REFUSED leaves no
commit at all, so the refusals — the signal — are exactly what git deletes. A
logger that recorded only successes would measure nothing; every status this
module records is therefore a status, including every refusal.

🔴 AND THE DENOMINATOR IS `allowance`, NEVER `handoff_budget.MAX_BYTES`. Many
docs sit at 3-5x `MAX_BYTES` legitimately, under a GRANDFATHERED entry, so
`MAX_BYTES` is the wrong denominator and produces a confidently wrong metric:
MEASURED over 38 git-derived "over the ceiling" shrinks, only 10 were real forced
evictions — the other 28 were ordinary edits to large grandfathered docs. This
module therefore records the doc's OWN ceiling, from `budget_position`, and the
reader below never mentions `MAX_BYTES` at all.

CONTRACT — FOUR HARD PROMISES
-----------------------------
  1. 🔴 FAIL-OPEN, ALWAYS, SILENTLY. A handoff write must never fail, change its
     status, or change its exit code because of this module. `record()` catches
     `Exception` around EVERYTHING — path resolution included, not only the write
     — and returns "" on any failure. A write gate that dies because a log path is
     unwritable is strictly worse than no logging, and `/handoff`'s write path is
     the only step that records a session at all.

     ⚠ WHAT THAT DOES NOT COVER, NAMED RATHER THAN IMPLIED. `except Exception`
     deliberately does NOT catch `KeyboardInterrupt` or `SystemExit`: those are the
     operator and the interpreter, and swallowing them here would make a Ctrl-C
     look like a successful log. It also cannot bound a write to an ordinary file
     that STALLS in the kernel (a hung network mount) — bounding that needs a
     timeout, which is a different mechanism; what is ruled out is a raise, not a
     hang. A non-regular target IS ruled out: see `_is_a_regular_file_or_absent`.

  2. 🔴 THE LOG NEVER LIVES INSIDE A REPO WORKING TREE, AND THAT IS CHECKED RATHER
     THAN PROMISED. devrc is a PUBLIC repository; a telemetry file that landed
     under a checkout is one `git add -A` from being published. `_outside_any_repo`
     walks the resolved path's ancestors and REFUSES (logs nothing) if any of them
     holds a `.git`. A refusal here is silent and costs only the log — the same
     fail-open direction as promise 1.

  3. 🔴 NO DOCUMENT CONTENT, EVER — sizes, statuses, paths and booleans only. Every
     value in a row is an integer, a boolean, `null`, a repo-RELATIVE doc path, a
     repo BASENAME, a `status=` token from a closed set, or a session id. No
     excerpt, no diff, no heading, no advance sentence. The row is a measurement,
     not a copy.

  4. 🔴 ONE `O_APPEND` WRITE OF ONE COMPLETE LINE, INCLUDING ITS NEWLINE. Several
     sessions and agents run this concurrently. A single `write(2)` to an
     `O_APPEND` fd is atomic for sub-`PIPE_BUF` lines, so interleaving cannot
     corrupt a record; nothing here reads the file to write it, so there is no
     read-modify-write window either. `json.dumps` of this field set is far under
     `PIPE_BUF` (4096 on Linux) — the paths are repo-relative and the rest are
     scalars.

WHERE THE FILE IS, AND THE PRECEDENT THAT CHOSE IT
--------------------------------------------------
`${XDG_STATE_HOME:-~/.local/state}/devrc/handoff-writes.jsonl`, mode 0600, in a
0700 directory. ⚠ SCOPE THAT SECOND CLAIM: `0700` is the mode this module asks
for when it CREATES `<state>/devrc`, and MEASURED, that is what it gets. It does
not re-`chmod` a directory that already exists, and `mkdir(parents=True)` applies
the mode only to the FINAL component — so an intermediate `<state>` this module
had to create carries the umask default. The FILE's `0600` is the guarantee that
does not depend on any of that. That is this repo's existing convention for durable per-user
state, not a new one: `scripts/drift-check.sh` (`DRIFT_STATE_DIR`),
`scripts/ship.sh` and `scripts/collector/keylog/spool_emit.py`
(`default_spool_dir`) all resolve `${XDG_STATE_HOME:-$HOME/.local/state}/<tool>`.
Honouring `XDG_STATE_HOME` also makes the suite hermetic for free:
`scripts/run-tests.sh` GUARD 8 exports it to a temp dir for every target, so a
test that forgets to override the path still cannot write to the operator's real
log.

⚠ `$HOME` ITSELF BEING A GIT REPO WOULD DISABLE LOGGING ENTIRELY, by promise 2.
That is the correct direction (silent, and nothing leaks) but it is SILENT, so it
is stated here and it is visible in the reader: a reader over a log with no rows
prints `rows=0`, which is the "could not measure" answer rather than a rate.

SCHEMA
------
`schema` is an integer on every row so a later reader can tell formats apart.
Version 1 is the field set in `Attempt`. 🔴 BUMP IT when a field changes MEANING
or disappears; adding a field does not need a bump, because a reader that does not
know the field ignores it and one that does uses `.get`.
"""
from __future__ import annotations

import datetime
import json
import os
import typing
from pathlib import Path

#: The row format's version. See the SCHEMA note in the module docstring.
SCHEMA = 1

#: Full path override. Set it to a path to redirect the log; set it to one of
#: `DISABLE_VALUES` to turn logging off. Tests use the first; an operator who
#: wants no telemetry uses the second.
LOG_PATH_ENV = "DEVRC_HANDOFF_WRITELOG"

#: Values of `LOG_PATH_ENV` that mean "write nothing". `""` is included because an
#: exported-but-empty variable is far likelier to be a shell accident than an
#: intent to log to the current directory.
DISABLE_VALUES = ("", "off", "OFF", "none", "/dev/null")

#: The XDG variable the default path is resolved from — named so a test can pin
#: that it is READ rather than reconstructing the layout and agreeing by luck.
STATE_HOME_ENV = "XDG_STATE_HOME"

STATE_SUBDIR = "devrc"
LOG_NAME = "handoff-writes.jsonl"

#: Statuses that mean "rule (p)/(q)/(r) refused this attempt" — the refusals the
#: second-pass measurement is about.
#:
#: 🔴 A CLOSED SET, AND IT IS THE READER'S DEFINITION OF A REFUSED PASS RATHER
#: THAN A SPELLING TEST ON THE WORD "refused". Exit codes 14/15/16 are the same
#: three rules; the tokens are kept here because a row carries the token the run
#: PRINTED, which is the thing a human reads in a transcript.
REFUSAL_STATUSES = ("size-ratchet", "prune-refused", "prune-unconserved")

#: Statuses that mean "bytes landed in the document". `written` is a confirmed
#: write that was not pushed (or whose push outcome follows on another line);
#: `pushed` is a confirmed write that reached the remote.
#:
#: ⚠ `push-failed` is NOT a member and that is deliberate rather than an
#: oversight: the COMMIT exists on that path, so the document did change — but the
#: run ended in a recovery the operator has to carry out, and counting it as a
#: clean landing would make a second-pass rate read as resolved when the arc is
#: still open. It is reported in its own bucket by the reader.
SUCCESS_STATUSES = ("written", "pushed")


class Attempt:
    """One write attempt's row, filled in as the run learns each fact.

    🔴 MUTABLE AND PRE-POPULATED WITH `None`, deliberately. `handoff_doc`'s write
    gate returns from thirty-nine places (MEASURED on the tree this landed on, and
    no count is restated anywhere else), and most of them are reached before the
    document has been read — so "not applicable" and "not yet known" are the NORMAL
    states for half these fields — a constructor that demanded them all would have to be fed
    invented values, which is the shape `handoff_doc.resolve_session_id` refuses
    for the session id. `None` serialises to JSON `null` and a reader can tell it
    from `0`.
    """

    __slots__ = (
        "session", "repo", "doc", "status", "exit_code",
        "bytes_before", "bytes_after", "allowance", "grandfathered",
        "band_warning_fired", "confirmed", "prune_requested", "exit_flags",
    )

    def __init__(
        self,
        *,
        session: str | None = None,
        repo: str | None = None,
        doc: str | None = None,
        confirmed: bool = False,
        exit_flags: typing.Sequence[str] = (),
    ) -> None:
        self.session = session or None
        self.repo = repo
        self.doc = doc
        #: The exact `status=` token the run PRINTED, or `None` when it printed
        #: none. 🔴 `None` IS A REAL VALUE WITH A NAMED POPULATION, not a gap: the
        #: argument-validation arms (exit 2) and the three environment arms (exit
        #: 3 — not a git repo, unreadable `--update`, unreadable `--prune`) print
        #: no verdict token at all, because the complaint is about an ARGUMENT or
        #: the ENVIRONMENT rather than a verdict about a document. A derived token
        #: here would read as something the transcript contains and does not.
        #: `exit_code` is what discriminates those arms.
        self.status: str | None = None
        self.exit_code: int | None = None
        self.bytes_before: int | None = None
        self.bytes_after: int | None = None
        self.allowance: int | None = None
        self.grandfathered: bool | None = None
        self.band_warning_fired = False
        self.confirmed = bool(confirmed)
        self.exit_flags = tuple(exit_flags)
        #: The coarse boolean, DERIVED from `exit_flags` rather than passed, so the
        #: two can never disagree about whether this run carried an exit flag.
        self.prune_requested = bool(exit_flags)

    def row(self) -> dict[str, object]:
        """This attempt as the dict that becomes one JSONL line.

        Key order is FIXED — identity, then verdict, then measurement — so a human
        reading `tail -1` sees the same shape every time. It is not alphabetical and
        nothing depends on the order: `json.loads` gives a dict, and the reader
        addresses every field by name.
        """
        return {
            "schema": SCHEMA,
            "ts": _now_iso(),
            "session": self.session,
            "repo": self.repo,
            "doc": self.doc,
            "status": self.status,
            "exit_code": self.exit_code,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "allowance": self.allowance,
            "grandfathered": self.grandfathered,
            "band_warning_fired": bool(self.band_warning_fired),
            "confirmed": bool(self.confirmed),
            "prune_requested": bool(self.prune_requested),
            "exit_flags": list(self.exit_flags),
        }


def _now_iso() -> str:
    """UTC, ISO-8601, seconds resolution, with an explicit `Z`.

    `datetime.timezone.utc` rather than `astimezone()`: a row's timestamp is the
    join key across sessions on two hosts, and a local-time stamp would make the
    ordering the reader depends on wrong across a DST boundary.
    """
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def default_log_path(env: typing.Mapping[str, str] | None = None) -> Path:
    """`${XDG_STATE_HOME:-~/.local/state}/devrc/handoff-writes.jsonl`.

    Resolved at CALL time, never at import: `scripts/run-tests.sh` exports
    `XDG_STATE_HOME` for the whole run, and a module-level constant captured at
    import would be read before a test's `monkeypatch.setenv` could move it.
    """
    src = os.environ if env is None else env
    state = (src.get(STATE_HOME_ENV) or "").strip()
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / STATE_SUBDIR / LOG_NAME


def resolve_log_path(env: typing.Mapping[str, str] | None = None) -> Path | None:
    """Where this process should log, or `None` for "do not log".

    Two ways to get `None`, and they are different facts that share an observable:
    the override names a `DISABLE_VALUES` member (deliberately off), or the
    resolved path is inside a git working tree (promise 2 refused it). Neither is
    reported — this is the silent half of fail-open — so a caller who wants to
    know which one it was reads `log_refusal` instead.
    """
    path, _ = _resolve_with_reason(env)
    return path


def log_refusal(env: typing.Mapping[str, str] | None = None) -> str:
    """"" when logging is on, else WHY it is off. For a diagnostic, never a gate.

    🔴 SEPARATE FROM `resolve_log_path` SO THE SILENT PATH STAYS SILENT. The write
    path must print nothing; a human asking "why is my log empty?" needs a
    sentence. One resolution, two readers — not two resolutions that can disagree.
    """
    _, reason = _resolve_with_reason(env)
    return reason


def _resolve_with_reason(
    env: typing.Mapping[str, str] | None = None,
) -> tuple[Path | None, str]:
    src = os.environ if env is None else env
    override = src.get(LOG_PATH_ENV)
    if override is not None and override.strip() in DISABLE_VALUES:
        return None, (
            f"{LOG_PATH_ENV}={override!r} is one of {DISABLE_VALUES!r}, so "
            f"handoff write telemetry is deliberately OFF."
        )
    path = (
        Path(override.strip()).expanduser()
        if override is not None and override.strip()
        else default_log_path(src)
    )
    path = path.resolve()
    inside = _repo_root_above(path)
    if inside is not None:
        return None, (
            f"refusing to log: {path} is inside the git working tree at {inside}. "
            f"A telemetry file under a checkout is one `git add -A` from being "
            f"published, and devrc is PUBLIC. Point {LOG_PATH_ENV} somewhere "
            f"outside a repo, or leave it unset for {default_log_path(src)}."
        )
    return path, ""


def _repo_root_above(path: Path) -> Path | None:
    """The nearest ancestor of `path` holding a `.git`, or `None`.

    🔴 THE PATH ITSELF IS NOT A CANDIDATE AND ITS PARENTS ALL ARE. A log file is
    never a repo root, and stopping at the immediate parent would admit
    `<repo>/claudedocs/handoff-writes.jsonl` — the exact shape promise 2 exists
    for, since that directory is where the documents live.

    ⚠ `.git` IS TESTED WITH `exists()`, NOT `is_dir()`: a worktree's `.git` is a
    FILE holding `gitdir: …`, and a worktree is the common case for this repo's
    agents. An `is_dir()` spelling would have admitted every worktree.
    """
    for parent in path.parents:
        if (parent / ".git").exists():
            return parent
    return None


def _is_a_regular_file_or_absent(path: Path) -> bool:
    """True when `path` is a regular file or does not exist yet.

    🔴 A FIFO AT THE TARGET WOULD BLOCK THE WRITE FOREVER with no reader, and a
    hang is STRICTLY WORSE than this module not existing — "does not raise" does
    not imply "does not hang". `hook_telemetry` learned this the measured way
    (a wired Stop hook that never returned within 12 s). TOCTOU-racy against a
    writer that swaps the path between this `stat` and the `open`, which is
    stated rather than claimed away.
    """
    try:
        return (not path.exists()) or path.is_file()
    except OSError:
        return False


def record(
    attempt: Attempt, env: typing.Mapping[str, str] | None = None
) -> str:
    """Append `attempt` as one JSONL line. Returns the line, or "" on any failure.

    🔴 THE WHOLE BODY IS INSIDE ONE `try`, INCLUDING PATH RESOLUTION AND
    SERIALISATION. Promise 1 is not "the write is wrapped" — it is "nothing this
    module does can reach the caller". A `TypeError` from a non-serialisable field,
    a `RuntimeError` from a `Path.home()` with no `$HOME`, and an `OSError` from a
    full disk are all the same fact from the write gate's point of view: the log
    did not happen, and the handoff write continues unchanged.

    🔴 THE LINE IS RETURNED EVEN THOUGH NO CALLER USES IT, so a test can assert
    the row a run would have written WITHOUT reading the file — which is what lets
    the fail-open test assert on the gate's own behaviour instead of on an absence.
    """
    try:
        path = resolve_log_path(env)
        if path is None:
            return ""
        if not _is_a_regular_file_or_absent(path):
            return ""
        line = json.dumps(attempt.row(), separators=(",", ":"), sort_keys=False)
        # 0700 on the directory and 0600 on the file: this is per-user state, and
        # the mode is set at CREATE time by `os.open`/`mkdir` rather than by a
        # follow-up `chmod`, so there is no window in which the file exists with a
        # wider mode. An ALREADY-existing file keeps whatever mode it has — a
        # `chmod` on every run would fight an operator who widened it on purpose.
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            # ONE write of ONE complete line INCLUDING its newline — promise 4.
            # Never two writes, and never `print`/buffered I/O, both of which can
            # split a record across two `write(2)` calls and interleave.
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return line
    except Exception:
        # Promise 1. Deliberately bare of any reporting: a handoff write's stdout
        # and stderr are a contract (`tests/parity`-style byte comparisons exist
        # over them), so a complaint here would change output on a path that has
        # nothing to do with telemetry.
        return ""


# ---------------------------------------------------------------------------
# the reader — a log nobody can query is not a measurement
# ---------------------------------------------------------------------------


class Report(typing.NamedTuple):
    """The two rates, each with the denominator it is a rate OVER.

    🔴 NEVER BARE COUNTS. #2001 widened the near-ceiling band from 4,096 B to
    16,384 B, which moved docs-warned from 10 to 24 — so a raw warning COUNT rises
    by construction and a report quoting one would read as a regression. Every
    number here is published beside its denominator, and `render` prints them as
    `n/d`.
    """

    rows: int
    malformed: int
    #: second-pass: confirmed rows only (see `second_pass`)
    second_pass_hits: int
    second_pass_total: int
    second_pass_unattributable: int
    #: warning-acted-upon
    warned_shrank: int
    warned_grew: int
    warned_pending: int
    warned_unclassifiable: int
    warned_docs: int
    #: context
    by_status: dict[str, int]


def read_rows(path: Path) -> tuple[list[dict], int]:
    """Every well-formed row in `path`, plus a count of the lines that were not.

    🔴 THE MALFORMED COUNT IS RETURNED RATHER THAN SWALLOWED. A truncated final
    line is the expected artefact of reading a log a concurrent writer is
    appending to, and silently dropping it would make a shrinking denominator look
    like a falling rate. It is reported.
    """
    rows: list[dict] = []
    malformed = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            malformed += 1
    return rows, malformed


def _ordered(rows: typing.Sequence[dict]) -> list[dict]:
    """Rows in timestamp order, ties broken by their position in the file.

    🔴 A STABLE SECONDARY KEY IS LOAD-BEARING. `ts` is seconds-resolution, so a
    proposal run and its confirm run land in the SAME second routinely — and the
    `confirmed` measurement below depends on which came first. File order is the
    append order, which is the truth.
    """
    return [
        r for _, r in sorted(
            enumerate(rows), key=lambda pair: (str(pair[1].get("ts") or ""), pair[0])
        )
    ]


def second_pass(rows: typing.Sequence[dict]) -> tuple[int, int, int]:
    """`(hits, total, unattributable)` for the second-pass rate.

    A HIT is a row whose `status` is in `REFUSAL_STATUSES` and which is followed,
    LATER in the same `(session, doc)`, by a row whose `status` is in
    `SUCCESS_STATUSES`. The TOTAL is every confirmed attempt.

    🔴 CONFIRMED RUNS ONLY, AND WITHOUT THAT FILTER THE RATE IS INFLATED ~2x BY
    CONSTRUCTION. `handoff_doc` has a deliberate two-run shape: a PROPOSAL run
    that writes nothing, then an identical run with `--confirm --push`. Counting
    both makes every ordinary write look like two attempts, so a "second pass"
    would be the normal case and the metric would measure the tool's own design
    rather than a cost. The refusals are confirmed-run events too — rule (p) fires
    on the proposal run as well, but the pass that was PAID is the confirmed one.

    🔴 A `null` SESSION IS EXCLUDED FROM THE NUMERATOR AND COUNTED SEPARATELY,
    never grouped. `session` is `null` when no id was resolvable, so grouping on it
    would merge every unattributed row from every session into one bucket and
    manufacture refusal->success pairs that never happened in one session.
    """
    confirmed = [r for r in _ordered(rows) if r.get("confirmed") is True]
    total = len(confirmed)
    unattributable = sum(1 for r in confirmed if not r.get("session"))
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in confirmed:
        session, doc = r.get("session"), r.get("doc")
        if not session or not doc:
            continue
        groups.setdefault((str(session), str(doc)), []).append(r)
    hits = 0
    for seq in groups.values():
        for i, r in enumerate(seq):
            if r.get("status") not in REFUSAL_STATUSES:
                continue
            if any(later.get("status") in SUCCESS_STATUSES for later in seq[i + 1:]):
                hits += 1
    return hits, total, unattributable


def warning_acted_upon(
    rows: typing.Sequence[dict],
) -> tuple[int, int, int, int, int]:
    """`(shrank, grew, pending, unclassifiable, docs_warned)`.

    For every row where `band_warning_fired`, find the FIRST successful write to
    the SAME doc strictly after it (excluding the row itself), and classify that
    write by its own delta: `bytes_after < bytes_before` is SHRANK, otherwise GREW.

    🔴 THIS IS THE METRIC THAT MATTERS, AND IT EXISTS BECAUSE A WARNING THAT FIRES
    IS NOT EVIDENCE. `handoff_doc`'s own `EXIT_SIZE_RATCHET` docstring records that
    the pre-write warning "has been printed on every over-budget write since #1648
    and the mechanism it names went on regardless". Only the ACTION is evidence, so
    this measures the next write rather than the warning.

    🔴 FOUR BUCKETS, NOT TWO, AND THE SPLIT IS WHAT KEEPS THE RATE HONEST.
    PENDING (warned, no later write yet) is excluded from the denominator: folding
    it into GREW would score an open arc as a failure, and folding it into SHRANK
    would score it as a success. UNCLASSIFIABLE is a later write with no
    `bytes_before` — a doc that did not exist, so there is no direction to read —
    and it is excluded for the same reason.

    `docs_warned` is published alongside because the band widening moved it 10 ->
    24: a reader needs the distinct-document count to tell a rising warning COUNT
    (expected, by construction) from a rising warned POPULATION.
    """
    ordered = _ordered(rows)
    writes: dict[str, list[tuple[int, dict]]] = {}
    for i, r in enumerate(ordered):
        if r.get("status") in SUCCESS_STATUSES and r.get("doc"):
            writes.setdefault(str(r["doc"]), []).append((i, r))
    shrank = grew = pending = unclassifiable = 0
    docs: set[str] = set()
    for i, r in enumerate(ordered):
        if not r.get("band_warning_fired") or not r.get("doc"):
            continue
        doc = str(r["doc"])
        docs.add(doc)
        later = [w for j, w in writes.get(doc, ()) if j > i]
        if not later:
            pending += 1
            continue
        nxt = later[0]
        before, after = nxt.get("bytes_before"), nxt.get("bytes_after")
        if not isinstance(before, int) or not isinstance(after, int):
            unclassifiable += 1
        elif after < before:
            shrank += 1
        else:
            grew += 1
    return shrank, grew, pending, unclassifiable, len(docs)


def build_report(rows: typing.Sequence[dict], malformed: int = 0) -> Report:
    hits, total, unattributable = second_pass(rows)
    shrank, grew, pending, unclassifiable, docs = warning_acted_upon(rows)
    by_status: dict[str, int] = {}
    for r in rows:
        status = r.get("status")
        # 🔴 `null` GETS ITS OWN LABEL RATHER THAN BEING DROPPED. Those rows are
        # the argument/environment arms, and a status table that silently omitted
        # them would make the row count disagree with the sum of the buckets —
        # which is how a reader concludes the log is losing lines.
        key = "<no status= token>" if status is None else str(status)
        by_status[key] = by_status.get(key, 0) + 1
    return Report(
        rows=len(rows),
        malformed=malformed,
        second_pass_hits=hits,
        second_pass_total=total,
        second_pass_unattributable=unattributable,
        warned_shrank=shrank,
        warned_grew=grew,
        warned_pending=pending,
        warned_unclassifiable=unclassifiable,
        warned_docs=docs,
        by_status=by_status,
    )


def _rate(n: int, d: int) -> str:
    """`n/d (p%)`, or `n/0 (no denominator)`.

    🔴 NEVER A BARE PERCENTAGE AND NEVER A DIVIDE BY ZERO. A rate over an empty
    denominator is not 0% — it is "could not measure", and the two must not share
    a rendering, which is the reassuring-zero shape `claude/RULES.md` refuses.
    """
    if d <= 0:
        return f"{n}/0 (no denominator — nothing measured)"
    return f"{n}/{d} ({100.0 * n / d:.1f}%)"


def render(report: Report, path: Path | None, refusal: str = "") -> str:
    lines = [
        "handoff write-attempt telemetry",
        f"  log:  {path if path is not None else '<disabled>'}",
        f"  rows: {report.rows}" + (
            f"  (+{report.malformed} malformed line(s))" if report.malformed else ""
        ),
    ]
    if refusal:
        lines.append(f"  ⚠ {refusal}")
    if not report.rows:
        lines.append(
            "  NOTHING MEASURED. A zero here is 'the log is empty', never 'the "
            "rate is zero' — run a handoff write, then re-run this."
        )
        return "\n".join(lines) + "\n"
    lines += [
        "",
        "SECOND-PASS RATE  (a refusal on a doc, then a later successful write to "
        "that doc in the same session)",
        f"  {_rate(report.second_pass_hits, report.second_pass_total)}"
        f"  — confirmed runs ONLY; proposal runs are excluded from BOTH sides",
        f"  {report.second_pass_unattributable} confirmed row(s) carry no session "
        f"id and are excluded from the numerator (they cannot be grouped)",
        "",
        "WARNING-ACTED-UPON RATE  (of runs where the near-ceiling warning fired, "
        "did the NEXT write to that doc shrink it?)",
        f"  shrank: {_rate(report.warned_shrank, report.warned_shrank + report.warned_grew)}",
        f"  grew:   {_rate(report.warned_grew, report.warned_shrank + report.warned_grew)}",
        f"  excluded: {report.warned_pending} pending (no later write yet), "
        f"{report.warned_unclassifiable} unclassifiable (the later write had no "
        f"bytes_before)",
        f"  distinct documents warned: {report.warned_docs}",
        "",
        "ATTEMPTS BY STATUS",
    ]
    for status, count in sorted(report.by_status.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {count:>6}  {status}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Rates over the handoff write-attempt log. Reports every rate with "
            "its denominator; a zero row count is 'could not measure', not 0%."
        )
    )
    parser.add_argument(
        "--log", help="the JSONL log to read (default: the resolved state path)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    refusal = ""
    if args.log:
        path: Path | None = Path(args.log).expanduser()
    else:
        path = resolve_log_path()
        refusal = log_refusal()
    rows, malformed = read_rows(path) if path is not None else ([], 0)
    report = build_report(rows, malformed)
    if args.json:
        print(json.dumps(report._asdict(), indent=2, sort_keys=True))
    else:
        print(render(report, path, refusal), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
