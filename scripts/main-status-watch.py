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
# TEST SEAMS (none is read anywhere else, and `scripts/tests/
# test_main_status_watch.py` pins that the production path still resolves the
# operator's own origin and really invokes systemctl):
#   MAIN_STATUS_WATCH_GH        argv0 for the API reader (default: `gh`)
#   MAIN_STATUS_WATCH_TRIGGER   full command to start the deadman
#   MAIN_STATUS_WATCH_CACHE     state directory
#   MAIN_STATUS_WATCH_REPO      owner/name, overriding the origin remote
#   MAIN_STATUS_WATCH_DEPTH     how many main commits to walk (default 20)
#   MAIN_STATUS_WATCH_BLIND_ESCALATE  consecutive unmeasured runs before rc 12
"""Start the main-green deadman early when main's own CI says main is red."""
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

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


# ── classification ────────────────────────────────────────────────────────────
# 🔴 ONLY `state == "failure"` IS A RED. Every non-code outcome the pipeline
# reports — superseded, KILLED, NO GATE POD — arrives as `state == "error"`, and
# each says "Not a code failure." in its own description. Treating `error` as red
# is the roll-up mistake in a second spelling; treating it as GREEN would be
# worse still, so it is classified as NOT-A-VERDICT and the walk continues past
# it. That is the narrowest rule that can be wrong in only one direction.
NOT_A_VERDICT = ("superseded", "killed", "no-gate-pod", "error-other", "pending")


def classify(state, description):
    """One status row -> a verdict class. Pure; the tests drive it directly."""
    desc = (description or "").strip()
    if state == "success":
        return "green"
    if state == "failure":
        return "red"
    if state == "pending":
        return "pending"
    if state == "error":
        if desc.startswith("superseded"):
            return "superseded"
        if desc.startswith("KILLED"):
            return "killed"
        if desc.startswith("NO GATE POD"):
            return "no-gate-pod"
        return "error-other"
    return "error-other"


def newest_per_context(rows):
    """GitHub returns statuses newest-first; keep the first row per context.

    A commit accumulates a `pending` row and then its verdict under the SAME
    context, so folding by context is what turns the history into a current
    answer. Without it a stale `pending` from 20 minutes ago outvotes the
    verdict that replaced it.
    """
    seen = {}
    for row in rows:
        ctx = row.get("context", "")
        if not ctx.startswith(CONTEXT_PREFIX):
            continue
        if ctx not in seen:
            seen[ctx] = row
    return seen


def commit_verdict(rows):
    """Fold one commit's status rows into (verdict, {context: description}).

    red beats green: a commit is red if ANY main leg failed. A commit has no
    verdict unless at least one leg produced green-or-red — a commit whose legs
    were all superseded tells us nothing at all, and must not read as green.
    """
    per_ctx = newest_per_context(rows)
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
# It is kept because it costs 15 lines and encodes WHY name-matching cannot carry
# the flake decision here, next to a ledger someone will otherwise be tempted to
# grow. The flake decision is made by the deadman, by re-running.
_FAILING_RE = re.compile(r"FAILING:\s*(.+?)(?:\s*\|\s*TOTAL\b|$)")
_FAILED_COUNT_RE = re.compile(r"\bfailed=(\d+)\b")


def parse_failing_names(description):
    m = _FAILING_RE.search(description or "")
    if not m:
        return []
    return [n.strip() for n in m.group(1).split("|") if n.strip()]


def parse_failed_count(description):
    m = _FAILED_COUNT_RE.search(description or "")
    return int(m.group(1)) if m else None


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
        self.triggered = self.root / "last-triggered"
        self.streak = self.root / "blind-streak"

    def mkdir(self):
        self.root.mkdir(parents=True, exist_ok=True)

    def _read(self, path):
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def last_triggered(self):
        return self._read(self.triggered)

    def record_trigger(self, sha):
        self.triggered.write_text(sha + "\n", encoding="utf-8")

    def read_streak(self):
        raw = self._read(self.streak)
        return int(raw) if raw.isdigit() else 0

    def bump_streak(self):
        n = self.read_streak() + 1
        self.streak.write_text(f"{n}\n", encoding="utf-8")
        return n

    def reset_streak(self):
        try:
            self.streak.write_text("0\n", encoding="utf-8")
        except OSError:
            pass


# ── IO ────────────────────────────────────────────────────────────────────────
class Unmeasured(Exception):
    """Raised for every reason the world could not be read. Never a verdict."""


# 🔴 A TOTAL BUDGET, NOT JUST A PER-CALL ONE. The walk makes up to DEPTH+1 API
# reads; at 45s each that is ~15 minutes worst case, well past the unit's
# `TimeoutStartSec=180`. Being SIGTERM'd by systemd would fail the unit with no
# message and no streak entry — the script would never get to say COULD NOT
# MEASURE. So it owns its own deadline, set BELOW the unit's, and exits through
# its normal unmeasured path instead. `test_the_total_budget_is_under_the_units_
# timeout` pins the ordering of the two numbers so tightening one cannot silently
# invert them.
TOTAL_BUDGET_S = 120
UNIT_TIMEOUT_START_SEC = 180
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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
        sha = c.get("sha") or ""
        if not sha:
            continue
        walked += 1
        rows = gh_json(f"/repos/{repo}/commits/{sha}/statuses")
        if not isinstance(rows, list):
            raise Unmeasured(f"statuses for {sha[:8]} were not a list")
        verdict, reds = commit_verdict(rows)
        if verdict in ("red", "green"):
            return sha, verdict, reds, walked
    return None, "none", {}, walked


def main(argv):
    global _DEADLINE
    budget = os.environ.get("MAIN_STATUS_WATCH_BUDGET")
    budget = float(budget) if (budget or "").replace(".", "", 1).isdigit() else TOTAL_BUDGET_S
    _DEADLINE = time.monotonic() + budget
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return RC_OK
    if len(argv) > 1:
        say(f"unknown argument: {argv[1]}")
        return RC_USAGE

    state = State(os.environ.get("MAIN_STATUS_WATCH_CACHE") or
                  Path.home() / ".cache" / "main-status-watch")
    try:
        state.mkdir()
    except OSError as exc:
        say(f"COULD NOT MEASURE — cannot create the state dir: {exc}")
        return RC_UNMEASURED

    try:
        depth = int(os.environ.get("MAIN_STATUS_WATCH_DEPTH") or 20)
    except ValueError:
        depth = 20
    escalate = os.environ.get("MAIN_STATUS_WATCH_BLIND_ESCALATE")
    escalate = int(escalate) if (escalate or "").isdigit() else 12

    try:
        repo = resolve_repo()
        sha, verdict, reds, walked = find_newest_verdict(repo, depth)
    except Unmeasured as exc:
        n = state.bump_streak()
        say(f"COULD NOT MEASURE — {exc}")
        say(f"  🔴 This is NOT 'main is green'. Nothing was read. (streak {n}/{escalate})")
        say("  The 4-hourly main-green-check deadman is unaffected; only the")
        say("  early trigger is offline, so coverage is what it was before this unit.")
        if n >= escalate:
            say(f"  BLIND — {n} consecutive unmeasured runs. Failing the unit (no toast).")
            return RC_BLIND
        return RC_UNMEASURED

    state.reset_streak()

    if verdict == "none":
        # 🔴 NOT GREEN. An empty walk means every recent commit was superseded,
        # killed, or still pending — the exact reassuring zero this repo keeps
        # getting bitten by. Say which it is, and take no action either way.
        say(f"no authoritative {CONTEXT_PREFIX}* verdict in the newest {walked} commits of {repo}.")
        say("  Nothing is claimed about main. The 4-hourly deadman still covers it.")
        return RC_OK

    if verdict == "green":
        say(f"newest main verdict: GREEN at {sha[:8]} (walked {walked}) — nothing to do.")
        return RC_OK

    names = sorted({n for d in reds.values() for n in parse_failing_names(d)})
    say(f"🔴 newest main verdict: RED at {sha[:8]} (walked {walked})")
    for ctx, desc in sorted(reds.items()):
        say(f"  {ctx}: {desc}")

    if state.last_triggered() == sha:
        say(f"  already handed {sha[:8]} to main-green-check — not re-triggering.")
        return RC_OK

    if screen_all_known_flakes(list(reds.values())):
        say(f"  every failure is a PROVEN-COMPLETE known flake ({', '.join(names)}) —")
        say("  not spending a confirmation run. No claim about main is made here.")
        state.record_trigger(sha)
        return RC_OK

    try:
        cmd = trigger_deadman()
    except Unmeasured as exc:
        n = state.bump_streak()
        say(f"COULD NOT MEASURE — {exc} (streak {n}/{escalate})")
        return RC_BLIND if n >= escalate else RC_UNMEASURED

    state.record_trigger(sha)
    say(f"  TRIGGERED the authoritative check early: {cmd}")
    say("  🔴 That check — not this one — decides whether main is broken. It runs")
    say("     both sandbox tiers twice and alerts only if the red REPRODUCES.")
    return RC_TRIGGERED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
