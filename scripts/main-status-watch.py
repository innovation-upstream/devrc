#!/usr/bin/env python3
# main-status-watch — make the main-green deadman look SOONER, and nothing else.
#
# WHY THIS EXISTS. `scripts/main-green-check.sh` is the authoritative answer to
# "is origin/main actually green?" — it reproduces a red by running both nix
# sandbox tiers TWICE before it will alert. It is correct and it is slow, and it
# is driven by `OnUnitActiveSec=4h` from its own last activation, so with
# `TimeoutStartSec=5400` a healthy cycle is up to ~5.5h. MEASURED over the 100
# most recent commits to `main` (2026-09-08..09-10): every PR branched during a
# red window inherits the red, and a human then triages a failure their diff
# cannot reach.
#
# 🔴 THIS SCRIPT IS AN ACCELERATOR, NOT A DETECTOR, AND THE DISTINCTION IS THE
# WHOLE DESIGN. It never decides that `main` is broken and it never notifies a
# human. All it does is start `main-green-check.service` early, on evidence,
# instead of waiting for the 4-hourly timer. Every claim about main's health is
# still made by the deadman, by reproduction, exactly as before.
#
# Three consequences follow from that, and they are the reasons this shape was
# chosen over the alternatives:
#
#   1. A FLAKE REACHING THIS SCRIPT COSTS COMPUTE, NOT ATTENTION. The deadman
#      already separates a flake from a break by re-running (`VERDICT=flake` when
#      attempt 1 is red and attempt 2 is green), and only rc 10 — red on BOTH
#      attempts — reaches `notify-failure@`. So the hazard "a fast alert that
#      fires on a flake trains click-through" is answered by REPRODUCTION in the
#      deadman, not by name-matching here. See `screen_all_known_flakes` for why
#      name-matching could not carry that weight even if we wanted it to.
#   2. IF THIS SCRIPT BREAKS, COVERAGE REVERTS TO WHAT IT WAS YESTERDAY — the
#      4-hourly deadman is untouched. That is why it is wired with NO
#      `OnFailure=notify-failure@`: a transient GitHub outage must not take the
#      operator's attention to report that a latency optimisation is offline.
#      It still ladders (rc 12) so it cannot go blind in permanent silence.
#   3. IT MUST BE CHEAP. It makes a handful of API reads. The expensive thing —
#      ~20 minutes of both sandbox tiers on a box that routinely carries 20-40
#      concurrent test runs — stays event-driven, fired only on evidence.
#
# 🔴 WHY IT READS `/commits/{sha}/statuses` AND NOT `/commits/{sha}/status`.
# The second is the ROLL-UP endpoint, and its top-level `state` is a trap this
# investigation walked into: GitHub maps `error` onto `failure` there, and 79% of
# `tekton/devrc-main-*` rows are `error` meaning "superseded by a newer run" or
# "KILLED: the gate pod died". Reading the roll-up, 48 of 60 recent main pushes
# look RED. Reading the per-status rows, the real figure over 200 rows is 9.
# A watcher built on the roll-up would have fired on ~80% of pushes on day one —
# the permanently-red gate `claude/RULES.md` calls worse than no gate. The list
# endpoint carries no roll-up field at all, so that mistake is unavailable here
# rather than merely avoided.
#
# EXIT CODES
#   0   looked; nothing to do (no red verdict, or the deadman already has it)
#  10   TRIGGERED main-green-check on evidence of a red `main`
#  11   COULD NOT MEASURE (network/gh/repo) — quiet, recorded, laddered
#  12   BLIND: too many consecutive unmeasured runs; fails the unit, no toast
#   2   usage
#
# TEST SEAMS (none is read anywhere else). Every behavioural test drives the
# seams, so `scripts/tests/test_main_status_watch.py` pins BOTH production argvs
# textually AND drives their parsing directly — `git remote get-url origin` in
# `test_resolve_repo_parses_every_origin_url_shape`, and
# `systemctl … main-green-check` in
# `test_the_production_trigger_is_systemctl_start_main_green_check` — so a seam
# cannot drift away from what production does while the suite stays green.
#   MAIN_STATUS_WATCH_GH        argv0 for the API reader (default: `gh`)
#   MAIN_STATUS_WATCH_TRIGGER   full command to start the deadman
#   MAIN_STATUS_WATCH_CACHE     state directory
#   MAIN_STATUS_WATCH_REPO      owner/name, overriding the origin remote
#   MAIN_STATUS_WATCH_DEPTH     how many main commits to walk (default 20)
#   MAIN_STATUS_WATCH_BUDGET    total wall-clock seconds for all API reads
#   MAIN_STATUS_WATCH_BLIND_ESCALATE  consecutive unmeasured runs before rc 12
# `test_every_env_var_the_code_reads_is_documented_in_the_header` pins that list
# two-way — an undocumented knob is a knob nobody can find, and one named here
# but no longer read is a lie. BUDGET was read for a full commit before anything
# mentioned it; that is the shape this guard exists to stop recurring.
# ⚠ NOTHING ELSE IN THIS COMMENT MAY BEGIN A LINE WITH A KNOB NAME. That guard
# reads the LEDGER above by matching `^#  <NAME> `, so an ordinary sentence
# wrapping onto a line that happens to start with one would register as a ledger
# entry for a knob that has none — a two-way pin satisfied by prose. A draft of
# this very paragraph did it.
# ⚠ AND AN EARLIER WORDING OVERSTATED THE SEAM PINS above: it said the tests
# pinned that the production path "resolves the operator's OWN origin". They did
# not — every test set the repo override, so nothing exercised `resolve_repo`
# past its first line: TEN of its ELEVEN enumerated mutants survived, the one
# death being the override branch itself, which every test needs. No test reads
# the real remote; what is pinned is the argv and the URL parsing.
"""Start the main-green deadman early when main's own CI says main is red."""
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

# 🔴 ONE RULE, ONE PLACE. `classify`, `newest_per_context`, `parse_failing_names`
# and `parse_failed_count` used to be open-coded HERE and again in
# `scripts/stale-base-triage.py`, and the copies had already DIVERGED. They now
# live in `scripts/lib/ci_status.py`; this file keeps only the POLICY — which
# context prefix may speak, which names are known flakes, what to do about a red.
#
# ⚠ IMPORTED BY PATH, not as a package, and this unit runs the file straight out
# of the CHECKOUT (`ExecStart=… %h/workspace/devrc/scripts/main-status-watch.py`),
# so a `git pull` is the whole deploy for both files and no home-manager switch
# is involved. A run that lands in the instant between the two files arriving
# fails to import and exits non-zero; this unit has no `OnFailure=` toast and the
# next poll is 10 minutes away, so the cost of that window is one skipped poll.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from ci_status import (classify, newest_per_context,  # noqa: E402
                       parse_failed_count, parse_failing_names)

RC_OK, RC_TRIGGERED, RC_UNMEASURED, RC_BLIND, RC_USAGE = 0, 10, 11, 12, 2

# The context prefix the main-push pipeline posts under. Derived nowhere: this
# is a literal contract with `devrc-ci-push-main`, and a typo here yields "no
# verdict found" forever — which is why `test_a_context_typo_is_not_silent`
# exists and why an empty walk is reported, never treated as green.
CONTEXT_PREFIX = "tekton/devrc-main-"

# 🔴 KNOWN FLAKES, AND READ `screen_all_known_flakes` BEFORE ADDING ONE.
# A name here can only ever SUPPRESS a confirmation run, never an alert, and it
# suppresses only when the description proves the named set is the COMPLETE set.
# `TestARefusedWriteIsIndistinguishableFromAnAbsentOne` is documented as
# explicitly unfixed at `scripts/tests/test_subsystem_store_api.py` — a
# `TimeoutError` inside `socket.recv_into` on an ESTABLISHED connection.
KNOWN_FLAKES = frozenset({
    "TestARefusedWriteIsIndistinguishableFromAnAbsentOne"
    ".test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_difference",
})


def say(msg):
    print(f"main-status-watch: {msg}", flush=True)


HEADER_SENTINEL = '"""Start the main-green deadman early'


def print_header():
    """Print the comment header — the part that documents the env knobs.

    Bounded by the module docstring's opening line rather than a hardcoded line
    number: a literal range silently truncates --help the moment the header
    grows, which main-green-check.sh records having happened to it already.
    """
    src = Path(__file__).read_text(encoding="utf-8").splitlines()
    end = next((i for i, ln in enumerate(src) if ln.startswith(HEADER_SENTINEL)), None)
    if end is None:
        say("cannot locate the end of the header (the docstring sentinel moved)")
        return False
    for line in src[1:end]:
        print(line[2:] if line.startswith("# ") else line.lstrip("#"))
    return True


# ── classification ────────────────────────────────────────────────────────────
# 🔴 `classify` AND `newest_per_context` NOW LIVE IN `scripts/lib/ci_status.py`,
# imported above. What they do and why is documented there; what stays HERE is
# the policy they are applied under.
#
# ⚠ `commit_verdict` BRANCHES ON `green` AND `red` ONLY, WHICH IS WHY THE FINER
# CLASSES NEED A TEST OF THEIR OWN. Downstream, `superseded`/`killed`/
# `no-gate-pod`/`error-other` are indistinguishable, so an end-to-end test can
# only ever see "not a verdict" — measured, 35 of `classify`'s 60 enumerated
# mutants survived a fully green 61-test suite, and the 25 that died were
# exactly the ones that moved an answer INTO green-or-red. Worse, the two
# fall-through `return "error-other"`s were reached by no fixture at all:
# turning either into `return "green"` survived. That is a state GitHub adds
# tomorrow folded into "main is green", closing a red episode. They are pinned
# by `test_classify_maps_a_row_to_its_documented_class`, which drives the shared
# function directly — the only way to see a distinction the caller cannot make.
# A tuple named NOT_A_VERDICT used to sit here enumerating the same classes;
# nothing read it, so it was deleted rather than left reading as a contract it
# could not enforce.
#
# 🔴 THE FOLD CHANGED WHEN IT MOVED, AND THE CHANGE IS DELIBERATE. This file
# used to keep the FIRST row per context, which is right only because GitHub
# returns the array newest-first — an assumption about someone else's response
# ordering, load-bearing and unstated. The shared `newest_per_context` folds on
# `max(created_at)` instead: it agrees with first-wins on the order GitHub uses
# today and stays right if that order ever changes. Pinned by
# `test_newest_per_context_is_ORDER_INDEPENDENT_which_first_wins_was_not`.
#
# 🔴 AND THE CONTEXT FILTER MOVED HERE, WHICH IS WHERE THE POLICY BELONGS. The
# shared fold returns every context it saw; deciding that only
# `tekton/devrc-main-*` may speak about main is this file's rule, not GitHub's.
# It must be applied BEFORE the emptiness check below — a commit carrying only a
# foreign pipeline's rows has NO verdict here, and reading one as green would
# close an open red episode off another pipeline's answer. Pinned by
# `test_a_commit_with_ONLY_FOREIGN_contexts_is_no_verdict_not_green`.


def commit_verdict(rows):
    """Fold one commit's status rows into (verdict, {context: description}).

    red beats green: a commit is red if ANY main leg failed. A commit has no
    verdict unless at least one leg produced green-or-red — a commit whose legs
    were all superseded tells us nothing at all, and must not read as green.
    """
    per_ctx = {ctx: row for ctx, row in newest_per_context(rows).items()
               if ctx.startswith(CONTEXT_PREFIX)}
    if not per_ctx:
        return "none", {}
    classes = {ctx: classify(r.get("state"), r.get("description")) for ctx, r in per_ctx.items()}
    reds = {ctx: per_ctx[ctx].get("description") or "" for ctx, c in classes.items() if c == "red"}
    if reds:
        return "red", reds
    if any(c == "green" for c in classes.values()):
        return "green", {}
    return "none", {}


# ── the flake screen ──────────────────────────────────────────────────────────
# 🔴 THIS SCREEN IS SOUND AND ALMOST NEVER SATISFIABLE, AND BOTH HALVES ARE THE
# POINT. GitHub caps a status description at 140 characters and the pipeline's
# `FAILING: a | b | c | TOTAL …` line overruns it constantly. MEASURED on real
# rows: one described `failed=7` while naming ONE test; one named NO test at all;
# and the row that named the known flake was cut MID-WORD
# (`…_CAN_see_the_dif`). So a description can prove "at least one test failed and
# here is its name" — it can NEVER prove "these are all of them".
#
# A screen that skipped on a name match would therefore skip real reds. This one
# skips only when the description PROVES the named set is complete: every failing
# test is named, the count agrees, and every name is a known flake. On the 100
# commits measured it would have fired ZERO times — including on the real flake
# row, which triggers a confirmation run because its truncation makes
# completeness unprovable. That is the correct answer, not a defect.
#
# It is kept because it costs a handful of lines and encodes WHY name-matching
# cannot carry the flake decision here, next to a ledger someone will otherwise
# be tempted to grow. The flake decision is made by the deadman, by re-running.
#
# 🔴 THE ZERO ABOVE IS NOT A PROPERTY OF THE IDEA — IT IS THIS SCREEN READING
# ONLY `failed=N`, WHICH IS THE FIELD THE 140-BYTE CAP EATS. `failed=` is LAST
# in the banner, so whether it survives is decided by the failing test's NAME
# LENGTH. `scripts/stale-base-triage.py` derives the same completeness proof a
# second way — `collected − passed − skipped` is an upper bound on `failed`, and
# those three fields PRECEDE it, so they survive — and on the rows measured that
# route turns 5/28 provably-complete into 18/28.
#
# ⚠ DELIBERATELY NOT ADOPTED HERE, AND THE REASON IS THE DIRECTION OF THE ERROR.
# There, proving completeness makes the tool SPEAK; here it makes this file stay
# SILENT about a red. A wider screen is a wider silence over the only automated
# detector `main` has, and the screen's own argument is that the flake decision
# belongs to the deadman's re-run rather than to a name match. Widening it is a
# change to make on purpose, with its own measurement — not a free win inherited
# from a sibling. (Handoff rank 6's open question, "is this screen inert?", now
# has half an answer: it is inert for a REASON, and the reason is fixable.)
# `parse_failing_names` / `parse_failed_count` are shared with that file via
# `scripts/lib/ci_status.py`; only the decision below is local.


def screen_all_known_flakes(descriptions):
    """True only when EVERY failure is proven to be a known flake.

    Returns False — i.e. confirm it — on any doubt whatsoever: no names, no
    count, a count that disagrees with the names, or one unrecognised name.
    """
    if not descriptions:
        return False
    for desc in descriptions:
        names = parse_failing_names(desc)
        if not names:
            return False           # named nothing: cannot screen
        count = parse_failed_count(desc)
        if count is None:
            return False           # truncated before the count: completeness unprovable
        if count != len(names):
            return False           # more failures than were named
        if any(n not in KNOWN_FLAKES for n in names):
            return False           # at least one real failure
    return True


# ── state ─────────────────────────────────────────────────────────────────────
class State:
    """Two independent facts in two files, deliberately.

    "I could not look" must not overwrite "I last triggered on sha X", for the
    same reason `main-green-check.sh` keeps its blind streak out of its verdict
    file: they are different facts and the second must survive the first.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.streak = self.root / "blind-streak"
        self.episode = self.root / "red-episode"

    def mkdir(self):
        self.root.mkdir(parents=True, exist_ok=True)

    def _read(self, path):
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # ── the RED EPISODE, not the verdict sha ──────────────────────────────────
    # 🔴 KEYED ON THE EPISODE BECAUSE THE VERDICT SHA IS THE WRONG UNIT. Only
    # ~22% of main commits ever get an authoritative verdict, so the newest
    # VERDICTED sha changes repeatedly inside one sustained red window. Keying
    # the debounce on it re-triggers for every newly-verdicted red commit —
    # measured at the 1.43h median verdict gap against ~40 min per deadman run,
    # roughly a 47% duty cycle of the most expensive job on this box, during
    # exactly the sustained-red scenario this whole unit exists for.
    #
    # Worse, it costs ATTENTION, not just compute: main-green-check carries
    # `OnFailure = notify-failure@` and its memo's red branch exits RC_RED
    # WITHOUT re-running anything, so a commit flipping pending -> failure inside
    # one interval fires the DND-defeating toast again having measured nothing.
    # "A flake costs compute, not attention" is true of ONE isolated flake and
    # false of a sustained red — which would rebuild the click-through hazard
    # this unit was built to remove.
    #
    # So: ONE trigger per red episode. An episode opens at the first non-flake
    # red and closes only when a GREEN verdict is observed. While it is open the
    # operator has already been told, and re-telling them adds no information.
    def episode_open(self):
        """-> (sha, age_seconds) for an open episode, else None.

        🔴 AN EPISODE CARRIES ITS AGE BECAUSE AN UNBOUNDED ONE SILENTLY DISARMS
        THIS UNIT. `close_episode` is reachable from exactly two places — a green
        verdict and a failed trigger — and nothing else clears it. Measured
        paths where it would otherwise stay open forever: main never gets a GREEN
        verdict inside the walk (a renamed context, a disabled pipeline, or a red
        window longer than 20 commits); a crash or reboot between the record and
        the trigger; a second distinct breakage with no green between. In every
        one, later reds returned rc 0 with no start and no signal at all — the
        `none` arm even resets the streak, so nothing accumulates.

        That is the same fault this file's F1 comment names: an arm that can
        never advance a ladder and never fails the unit is permanently silent.
        The bound is EPISODE_MAX_AGE_S; see it for why that number.
        """
        raw = self._read(self.episode)
        if not raw:
            return None
        parts = raw.split()
        sha = parts[0]
        try:
            ts = int(parts[1])
        except (IndexError, ValueError):
            # Written by an older version, or truncated. Treat as ANCIENT rather
            # than as fresh: an unreadable age must not grant an unbounded pass.
            return sha, EPISODE_MAX_AGE_S + 1
        return sha, max(0, int(time.time()) - ts)

    def open_episode(self, sha):
        """Record the episode BEFORE triggering. Returns False if it cannot.

        🔴 THE ORDER IS THE GUARD. Writing this AFTER a successful trigger means
        a failed write leaves the deadman started and no record — so the next run
        sees the same red as new and starts it again, every interval, forever.
        The realistic cause is ENOSPC on ~/.cache, which shares the root
        filesystem with the nix store: the disk pressure that makes the write
        fail is produced by the very builds you least want re-kicked. That is
        the one failure mode that would be WORSE than not having this unit, so
        it fails CLOSED — no record, no trigger.
        """
        try:
            self.episode.write_text(f"{sha} {int(time.time())}\n", encoding="utf-8")
            return True
        except OSError:
            return False

    def close_episode(self):
        """Returns False if the episode could NOT be cleared.

        🔴 IT USED TO SWALLOW THE OSError, so a green verdict printed "closing
        the red episode" while the file was still there — a log line asserting an
        action that did not happen, and an accelerator left permanently disarmed
        because every later red then saw an open episode. Never claim a state
        change you did not achieve.
        """
        try:
            self.episode.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def read_streak(self):
        raw = self._read(self.streak)
        first = raw.split()[0] if raw.split() else ""
        return int(first) if first.isdigit() else 0

    def bump_streak(self):
        """Returns (count, persisted). The SECOND value is load-bearing.

        🔴 IT USED TO SWALLOW ITS OWN OSError AND RETURN A COUNT NOBODY SAVED,
        which re-created the exact bug F1 removed, in the exact scenario F2 was
        written for: under ENOSPC every write in this directory fails, so `n` was
        recomputed as 1 on every run and `n >= escalate` was never true. Measured
        with escalate=2 over six polls: rc 11 six times, `streak 1/2` six times —
        byte-for-byte the sequence the F1 commit cites as the bug it removed, and
        rc 11 is a systemd SUCCESS, so the unit stayed green forever.

        A ladder that cannot persist is not a ladder. The caller must treat
        `persisted=False` as "this arm can never become loud later", which is
        precisely the condition F3 already answers with rc 12.
        """
        n = self.read_streak() + 1
        try:
            self.streak.write_text(f"{n}\n", encoding="utf-8")
            return n, True
        except OSError:
            return n, False

    def reset_streak(self):
        # ⚠ ITS RESULT IS DELIBERATELY NOT CONSULTED, unlike every other writer
        # on this class — and that asymmetry is a decision, not an oversight a
        # mutation sweep should re-file. `open_episode`, `close_episode` and
        # `bump_streak` each return a flag the caller MUST branch on, because
        # each of their failures is silent and dangerous. This one fails NOISY,
        # not silent — and that is the whole distinction: a reset that does not
        # land leaves the streak HIGH, so a later unmeasured run escalates
        # SOONER than it should. That is a quiet false alarm (rc 12 fails the
        # unit and does not toast), never a missed one. Flipping this return
        # value is therefore an equivalent mutant, and the sweep says so.
        try:
            self.streak.write_text("0\n", encoding="utf-8")
            return True
        except OSError:
            return False


# ── IO ────────────────────────────────────────────────────────────────────────
BLIND_ESCALATE_DEFAULT = 12


def blind_escalate():
    """How many consecutive unmeasured runs before rc 12 — ONE PLACE.

    🔴 This was open-coded at BOTH ladder sites, literal `12` and parsing guard
    duplicated. `claude/RULES.md`: one rule, one place — a predicate duplicated
    across call sites regenerates the same bug at every site. The two copies
    happened to agree; the hazard is the next edit touching one of them, and the
    ladder is exactly the mechanism whose failure is SILENCE, so a divergence
    would not announce itself.

    Non-numeric or absent falls back to the default rather than raising: this is
    called on the failure path, and a bad env var must not turn "could not
    measure" into a traceback.
    """
    raw = os.environ.get("MAIN_STATUS_WATCH_BLIND_ESCALATE")
    return int(raw) if (raw or "").isdigit() else BLIND_ESCALATE_DEFAULT


def cache_root():
    """Where the two state files live — ONE PLACE.

    🔴 THIS WAS OPEN-CODED AT TWO SITES, and the second one is the one that runs
    when the first has already failed: `main` derives it, and so did the
    outermost exception net. Two copies drifting apart would send the blind
    streak to a different directory than the next run reads, which silently
    un-ladders the net — and the ladder's failure mode is SILENCE, so the
    divergence would not announce itself. Same rule, same reason, as
    `blind_escalate()` directly above.
    """
    return Path(os.environ.get("MAIN_STATUS_WATCH_CACHE") or
                Path.home() / ".cache" / "main-status-watch")


def ladder_exit(state, escalate, reason):
    """The ONE place that turns "could not measure" into an exit code.

    🔴 THIS WAS OPEN-CODED AT THREE SITES AND THE RULE CHANGED UNDER ALL THREE.
    Each site bumped the streak and compared it to `escalate`; none of them could
    see that a bump which FAILED TO PERSIST makes the comparison meaningless.
    Consolidating is what made the missing case audible — a predicate open-coded
    at N sites is typically wrong at N-1 of them in the same direction, and here
    it was wrong at all three.

    🔴 UN-PERSISTABLE MEANS LOUD NOW, NOT QUIET FOREVER. If the streak cannot be
    written, this arm can never become loud later, and an arm that never advances
    a ladder and never fails the unit is permanently silent — the exact shape
    this file's F1 comment condemns. F3 already answers that condition with
    rc 12; this applies the same answer to the same condition wherever it arises,
    rather than only where the directory was missing outright.

    ⚠ That corrects a second thing: F3's comment called the cause "structural,
    not transient". ENOSPC — the cause `open_episode` itself names — is neither
    permanent nor rare. The rc is not chosen because the fault cannot heal; it is
    chosen because the ALARM cannot count.
    """
    n, persisted = state.bump_streak()
    if not persisted:
        say(f"COULD NOT MEASURE — {reason}")
        say("  🔴 …and the blind streak could not be written either, so this arm")
        say("  can never escalate on its own. Failing the unit NOW rather than")
        say("  reporting success forever. (ENOSPC on the cache filesystem does")
        say("  exactly this: it breaks the alarm and the thing the alarm watches.)")
        return RC_BLIND
    say(f"COULD NOT MEASURE — {reason} (streak {n}/{escalate})")
    if n >= escalate:
        say(f"  BLIND — {n} consecutive unmeasured runs. Failing the unit (no toast).")
        return RC_BLIND
    return RC_UNMEASURED


class Unmeasured(Exception):
    """Raised for every reason the world could not be read. Never a verdict."""


# 🔴 A TOTAL BUDGET, NOT JUST A PER-CALL ONE. The walk makes up to DEPTH+1 API
# reads; at 45s each that is ~15 minutes worst case, well past the unit's
# `TimeoutStartSec=180`. Being SIGTERM'd by systemd would fail the unit with no
# message and no streak entry — the script would never get to say COULD NOT
# MEASURE. So it owns its own deadline, set BELOW the unit's, and exits through
# its normal unmeasured path instead.
# `test_the_total_budget_PLUS_the_trigger_timeout_is_under_the_units_timeout`
# pins the ordering of those numbers so tightening one cannot silently invert
# them. ⚠ That name is the THIRD-number version; this comment cited the
# two-number name the same commit had already renamed, so the citation read as
# authority and resolved to nothing. `test_every_test_this_script_names_
# actually_exists` now fails on a dangling one. (A name wrapped across a comment
# line-break is exactly what hid it from every grep, so that guard re-joins the
# halves before looking — and this comment is deliberately wrapped that way.)
TOTAL_BUDGET_S = 120
# 🔴 A THIRD NUMBER, and the pin used to name only two. `trigger_deadman`'s
# subprocess timeout is NOT charged against _DEADLINE — the budget covers the
# API walk only — so the real worst case is BUDGET + TRIGGER, not BUDGET.
# Measured at a 10s budget: a hanging trigger ran 30s. 120+30=150 < 180 today,
# but any edit raising the budget into [150, 179] would keep a
# `budget < TimeoutStartSec` assertion green while making SIGTERM reachable —
# the exact outcome the constant exists to prevent.
TRIGGER_TIMEOUT_S = 30
UNIT_TIMEOUT_START_SEC = 180

# 🔴 AN OPEN EPISODE EXPIRES, AND THE NUMBER IS AN ARGUMENT, NOT A TASTE.
# The episode suppresses DUPLICATE confirmations inside one red window. Left
# unbounded it also suppresses every future red once it gets stuck open, which
# disarms this unit with no signal — so it needs a ceiling. 4h is chosen to
# match main-green-check's own `OnUnitActiveSec=4h`: past that point the deadman
# re-runs against the tip ANYWAY, so re-triggering adds no work that was not
# already going to happen, and the duty cycle this unit can add is bounded above
# by the deadman's existing cadence rather than by the verdict rate. Shorter
# would re-introduce the toast amplification F5 removed; longer would leave a
# stuck episode masking reds for more than one deadman cycle.
EPISODE_MAX_AGE_S = 4 * 60 * 60
_DEADLINE = None


def _remaining():
    if _DEADLINE is None:
        return 45.0
    return _DEADLINE - time.monotonic()


def gh_json(path, timeout=45):
    gh = os.environ.get("MAIN_STATUS_WATCH_GH", "gh")
    left = _remaining()
    if left <= 0:
        raise Unmeasured(
            f"ran out of its {TOTAL_BUDGET_S}s budget before reading {path}"
        )
    timeout = min(timeout, left)
    try:
        proc = subprocess.run(
            [gh, "api", path], capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise Unmeasured(f"{gh} is not on PATH")
    except subprocess.TimeoutExpired:
        raise Unmeasured(f"gh api {path} timed out after {timeout}s")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise Unmeasured(f"gh api {path} exited {proc.returncode}: {err[0] if err else '(no stderr)'}")
    try:
        return json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        raise Unmeasured(f"gh api {path} returned unparseable JSON: {exc}")


def resolve_repo():
    override = os.environ.get("MAIN_STATUS_WATCH_REPO", "").strip()
    if override:
        return override
    here = Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            ["git", "-C", str(here), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unmeasured(f"cannot read origin remote: {exc}")
    if proc.returncode != 0:
        raise Unmeasured("cannot read origin remote (not a git checkout?)")
    url = proc.stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise Unmeasured(f"cannot parse owner/name out of origin url: {url!r}")
    return m.group(1)


def trigger_deadman():
    override = os.environ.get("MAIN_STATUS_WATCH_TRIGGER", "").strip()
    cmd = shlex.split(override) if override else [
        # 🔴 NO `--force`. The deadman memoises its verdict on the sha, so if it
        # has ALREADY adjudicated this tip the trigger costs nothing and its
        # answer stands. Forcing would let this script overrule a reproduction
        # with a status row — exactly the authority inversion it exists to avoid.
        "systemctl", "--user", "start", "--no-block", "main-green-check.service",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=TRIGGER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unmeasured(f"cannot start main-green-check: {exc}")
    if proc.returncode != 0:
        raise Unmeasured(
            f"starting main-green-check exited {proc.returncode}: "
            f"{(proc.stderr or '').strip().splitlines()[:1]}"
        )
    return " ".join(cmd)


# ── the walk ──────────────────────────────────────────────────────────────────
def find_newest_verdict(repo, depth):
    """Newest main commit carrying an authoritative verdict.

    🔴 THE WALK IS WHAT MAKES THIS WORK AT ALL. Reading only the tip would
    almost always find `pending` or `superseded`: MEASURED, only 21 of 100 main
    commits carried an authoritative verdict, because a newer push cancels the
    older run. The one that DOES get run is the last of a burst, so the newest
    verdict in a short window is the current answer about `main`.
    """
    commits = gh_json(f"/repos/{repo}/commits?sha=main&per_page={depth}")
    if not isinstance(commits, list) or not commits:
        raise Unmeasured(f"no commits returned for {repo}")
    walked = 0
    for c in commits:
        # 🔴 `isinstance` BEFORE `.get`, ON EVERY DECODED ELEMENT. `json.loads`
        # will happily hand back a list of strings — an error envelope, a proxy
        # page, a future API change — and `.get` on one raises AttributeError.
        # In a timer that is a bare traceback: the unit fails with no message,
        # the blind streak is never touched, and nothing says which commit it
        # choked on. Every unreadable shape must reach the SAME unmeasured path
        # as a network failure.
        if not isinstance(c, dict):
            raise Unmeasured(f"commit entry {walked} was {type(c).__name__}, not an object")
        sha = c.get("sha") or ""
        if not sha:
            continue
        walked += 1
        rows = gh_json(f"/repos/{repo}/commits/{sha}/statuses")
        if not isinstance(rows, list):
            raise Unmeasured(f"statuses for {sha[:8]} were not a list")
        if not all(isinstance(r, dict) for r in rows):
            raise Unmeasured(f"statuses for {sha[:8]} contained a non-object row")
        verdict, reds = commit_verdict(rows)
        if verdict in ("red", "green"):
            return sha, verdict, reds, walked
    # 🔴 "" AND NOT None, DELIBERATELY. The callers below format `sha[:8]`, and
    # they are correct only because verdict=="none" is returned together with an
    # unusable sha — an INVARIANT nothing enforced. A later return path pairing a
    # None sha with "red" would crash on a timer, silently. An empty string keeps
    # the pairing honest AND makes the slice total, so a violated invariant
    # prints a useless sha instead of killing the run.
    return "", "none", {}, walked


def main(argv):
    global _DEADLINE
    budget = os.environ.get("MAIN_STATUS_WATCH_BUDGET")
    budget = float(budget) if (budget or "").replace(".", "", 1).isdigit() else TOTAL_BUDGET_S
    _DEADLINE = time.monotonic() + budget
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        # 🔴 THE HEADER, NOT `__doc__`. `__doc__` is one line and names ZERO of
        # the seven MAIN_STATUS_WATCH_* knobs, so "the header IS the --help
        # text" — the rationale the env-var guard was filed under — was simply
        # FALSE. `main-green-check.sh` already prints its own header for
        # --help; making that claim true is the better fix than deleting it.
        #
        # 🔴 AND THE RETURN VALUE IS CONSUMED. It used to be discarded, so a
        # moved sentinel printed ONE diagnostic line and still exited 0 — a
        # --help that reports success having printed no help, the reassuring
        # zero this file argues against. `main-green-check.sh` calls `die`
        # (exit 2) in the same situation; matching it makes the comparison in
        # this comment true rather than aspirational.
        # ⚠ ONE RULE, ONE PLACE: this used to be TWO paragraphs stating the
        # same rule, and the first described the pre-fix behaviour as
        # "silently prints zero lines", which is not what it did. The arm is
        # covered by `test_a_MOVED_docstring_sentinel_REFUSES_rather_than_
        # dumping_the_file`; before that it had none, and three separate
        # mutants of it survived a fully green suite.
        return RC_OK if print_header() else RC_USAGE
    if len(argv) > 1:
        say(f"unknown argument: {argv[1]}")
        return RC_USAGE

    state = State(cache_root())
    try:
        state.mkdir()
    except OSError as exc:
        # 🔴 rc 12, NOT rc 11 — AND THE ASYMMETRY IS THE POINT. Every other
        # unmeasured arm ladders, because a network blip self-heals and should
        # not fail the unit on its first occurrence. This one cannot ladder at
        # all: the streak file lives INSIDE the directory that could not be
        # created, so there is nowhere to count. An arm that can never advance a
        # ladder and never fails the unit is permanently silent — which is the
        # reassuring zero ("I could not create my state dir" reading as "looked,
        # nothing to do") this whole file is built against. A structural failure
        # that cannot self-heal by retrying is exactly what the ladder would be
        # waiting for anyway, so it reports it immediately.
        say(f"COULD NOT MEASURE — cannot create the state dir: {exc}")
        say("  🔴 This is NOT 'main is green'. Nothing was read.")
        # ⚠ The rc is NOT chosen because the fault is permanent — an earlier
        # version said "structural, not transient", and ENOSPC (the cause
        # `open_episode` names) is neither. It is chosen because there is
        # nowhere to record a streak, so this arm can never escalate on its own;
        # `ladder_exit` applies the same rule wherever that condition arises.
        say("  There is nowhere to record a streak, so this arm can never")
        say("  escalate on its own. Failing the unit now beats reporting success")
        say("  forever.")
        return RC_BLIND

    try:
        depth = int(os.environ.get("MAIN_STATUS_WATCH_DEPTH") or 20)
    except ValueError:
        depth = 20
    escalate = blind_escalate()

    try:
        repo = resolve_repo()
        sha, verdict, reds, walked = find_newest_verdict(repo, depth)
    except Unmeasured as exc:
        rc = ladder_exit(state, escalate, str(exc))
        say("  🔴 This is NOT 'main is green'. Nothing was read.")
        say("  The 4-hourly main-green-check deadman is unaffected; only the")
        say("  early trigger is offline, so coverage is what it was before this unit.")
        return rc

    # 🔴 NO `reset_streak()` HERE. It used to sit exactly here, and it made the
    # trigger-failure ladder STRUCTURALLY UNREACHABLE: the walk succeeding reset
    # the streak to 0, so the bump in the trigger handler below could only ever
    # return 1 and `n >= escalate` was never true. Measured with escalate=2:
    # six consecutive trigger failures all reported `streak 1/2`, rc 11 — a
    # systemd success — so an unloaded main-green-check.service (a renamed unit,
    # a switch without daemon-reload, a DBus hiccup) left this accelerator inert
    # and the unit reporting healthy forever. That is precisely the shape
    # drift-check's rc 18 exists to prevent, in the one arm where the trigger IS
    # the whole job. The reset now happens on each TERMINAL SUCCESS path only.

    if verdict == "none":
        # 🔴 NOT GREEN. An empty walk means every recent commit was superseded,
        # killed, or still pending — the exact reassuring zero this repo keeps
        # getting bitten by. Say which it is, and take no action either way.
        say(f"no authoritative {CONTEXT_PREFIX}* verdict in the newest {walked} commits of {repo}.")
        say("  Nothing is claimed about main. The 4-hourly deadman still covers it.")
        say("  An open red episode (if any) is left open: absence is not a fix.")
        state.reset_streak()
        return RC_OK

    if verdict == "green":
        # A green verdict is the ordinary way a red episode ends — a red window
        # closes when main is observed good again, not when it stops being
        # re-verdicted. (The age bound below is the BACKSTOP for when no green
        # verdict ever arrives, not the normal path.)
        was_open = state.episode_open()
        closed = state.close_episode()
        if was_open and closed:
            say(f"main is GREEN again at {sha[:8]} — closed the red episode.")
        elif was_open:
            # 🔴 NEVER CLAIM THE STATE CHANGE YOU DID NOT ACHIEVE. This branch
            # used to print "closing the red episode" unconditionally while the
            # unlink had failed and the file was still there — a log line
            # asserting an action that did not happen, leaving the accelerator
            # disarmed for every later red.
            say(f"main is GREEN again at {sha[:8]} but the episode file could NOT")
            say("  be removed — later reds will be suppressed until it is. Check"
                f" {state.episode}")
            # 🔴 NO `reset_streak()` HERE — a first draft of this very branch had
            # one, and it rebuilt F1 exactly: resetting immediately before
            # `ladder_exit` pins the count at 1 forever, so the arm can never
            # escalate. Its own test caught it (got 11,11; wanted 11,12). This is
            # NOT a success path — it is an unmeasured one that happens to have
            # read a green verdict, so it must ladder like every other.
            return ladder_exit(state, escalate, "cannot clear the red episode")
        else:
            say(f"newest main verdict: GREEN at {sha[:8]} (walked {walked}) — nothing to do.")
        state.reset_streak()
        return RC_OK

    names = sorted({n for d in reds.values() for n in parse_failing_names(d)})
    say(f"🔴 newest main verdict: RED at {sha[:8]} (walked {walked})")
    for ctx, desc in sorted(reds.items()):
        say(f"  {ctx}: {desc}")

    open_at = state.episode_open()
    if open_at and open_at[1] <= EPISODE_MAX_AGE_S:
        prev_sha, age = open_at
        say(f"  red episode already open (since {prev_sha[:8]}, {age // 60}m ago)"
            " — not re-triggering.")
        say("  The operator has already been told main is red; a second start")
        say("  would re-run the same tip, hit the deadman's memo, exit RC_RED and")
        say("  fire the toast again having measured nothing.")
        say(f"  It expires after {EPISODE_MAX_AGE_S // 3600}h so a stuck episode")
        say("  cannot disarm this unit indefinitely.")
        state.reset_streak()
        return RC_OK
    if open_at:
        say(f"  red episode from {open_at[0][:8]} is {open_at[1] // 3600}h old"
            f" (bound {EPISODE_MAX_AGE_S // 3600}h) — treating this as a new one.")
        say("  Past that age the deadman's own 4-hourly timer re-runs against the")
        say("  tip anyway, so this adds no work that was not already going to")
        say("  happen — and it is what stops a stuck episode masking reds forever.")

    if screen_all_known_flakes(list(reds.values())):
        say(f"  every failure is a PROVEN-COMPLETE known flake ({', '.join(names)}) —")
        say("  not spending a confirmation run. No claim about main is made here.")
        # Deliberately does NOT open an episode: a later, non-flake red in the
        # same window must still be able to trigger.
        state.reset_streak()
        return RC_OK

    # 🔴 RECORD FIRST, THEN TRIGGER — see State.open_episode. A failed write
    # must mean NO trigger, or an unwritable cache re-starts the deadman every
    # interval forever.
    if not state.open_episode(sha):
        rc = ladder_exit(state, escalate, "cannot record the red episode")
        say("  🔴 NOT triggering: without a record this would re-start the")
        say("  deadman every interval. Failing closed is the cheaper mistake.")
        return rc

    try:
        cmd = trigger_deadman()
    except Unmeasured as exc:
        rc = ladder_exit(state, escalate, str(exc))
        # 🔴 A TIMEOUT IS NOT A FAILURE TO START. `systemctl start --no-block`
        # can time out AFTER the job is queued, so closing the episode there
        # would let the next poll trigger a run that is already under way — the
        # one path that broke the "one extra deadman run per window" bound. Any
        # OTHER error means nothing was queued, so the episode is dropped and a
        # retry is possible once whatever broke the trigger is fixed.
        if "timed out" in str(exc):
            say("  episode LEFT OPEN: a timeout may have queued the job anyway.")
        elif not state.close_episode():
            say("  ⚠ could not drop the episode; the next poll will not retry.")
        return rc

    state.reset_streak()
    say(f"  TRIGGERED the authoritative check early: {cmd}")
    say("  🔴 That check — not this one — decides whether main is broken. It runs")
    say("     both sandbox tiers twice and alerts only if the red REPRODUCES.")
    return RC_TRIGGERED


def _guarded_main(argv):
    """🔴 AN UNATTENDED TIMER MUST NOT DIE BY TRACEBACK.

    `main` converts every failure it ANTICIPATED into rc 11 plus a streak entry.
    This converts the ones it did not: a shape from the API nobody predicted, a
    library raising something new. Without it those exit 1 with a traceback in
    the journal, no streak entry, and — because rc 1 is not in this unit's
    `SuccessExitStatus` — a failed unit carrying no explanation of what broke.

    🔴 THIS IS NOT SWALLOWING THE ERROR, AND THE DIFFERENCE IS THE WHOLE POINT.
    The exception type and message are PRINTED, the traceback goes to stderr for
    the journal, and the blind ladder counts it — so a bug that recurs escalates
    to rc 12 exactly like a persistent outage. A run that cannot look is
    reported as a run that could not look, never as 'main is green'.

    🔴 ONE ARM, NOT TWO — AND THE SECOND ONE WAS F1's SHAPE FOR THE THIRD TIME.
    There used to be a dedicated `except Unmeasured` here that printed COULD NOT
    MEASURE and returned rc 11 WITHOUT bumping the streak. rc 11 is a systemd
    SUCCESS, so that arm could never advance a ladder and never fail the unit:
    permanently silent, the exact fault this file's F1/F3 comments condemn and
    that `ladder_exit` says it answers "wherever that condition arises". The
    paragraph above was describing the net as a whole and was therefore FALSE
    for the arm nearest to it — a coverage claim wider than its implementation.
    `Unmeasured` is an `Exception`, so deleting the special case is the fix:
    the general arm already reports it AND counts it.
    """
    try:
        return main(argv)
    except Exception as exc:  # noqa: BLE001 — deliberate; see the docstring
        import traceback
        say(f"COULD NOT MEASURE — UNEXPECTED {type(exc).__name__}: {exc}")
        say("  🔴 This is a BUG in main-status-watch, not a verdict about main.")
        say("  Nothing is claimed about main; the 4-hourly deadman is unaffected.")
        traceback.print_exc()
        try:
            st = State(cache_root())
            st.mkdir()
            n, persisted = st.bump_streak()
            escalate = blind_escalate()
            if not persisted:
                say("  the blind ladder could NOT be written — failing the unit now")
                say("  rather than reporting success forever.")
                return RC_BLIND
            say(f"  recorded on the blind ladder (streak {n}/{escalate})")
            if n >= escalate:
                return RC_BLIND
        except Exception:  # noqa: BLE001 — the ladder must never mask the report
            say("  (could not record it on the blind ladder either)")
        return RC_UNMEASURED


if __name__ == "__main__":
    sys.exit(_guarded_main(sys.argv))
