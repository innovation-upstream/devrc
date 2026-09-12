"""GitHub commit-status reading for this repo's `tekton/devrc-*` gate.

🔴 ONE RULE, ONE PLACE. Every predicate here was open-coded in BOTH
`scripts/main-status-watch.py` (live, every 10 minutes, on both hosts) and
`scripts/stale-base-triage.py`, and the copies had ALREADY DIVERGED before
anybody noticed — only the newer one stripped the `TOTAL` truncation fragment,
and only the newer one folded statuses by timestamp instead of by array
position. Consolidation is what made that disagreement audible; the divergence
was the finding, not the tidiness.

WHAT LIVES HERE, AND WHAT DELIBERATELY DOES NOT. This module owns the rules that
are about GITHUB'S DATA and about the GATE'S OWN OUTPUT FORMAT — how a status row
maps to a verdict class, how a commit's rows fold to a current answer, and how
the `FAILING: … | TOTAL collected=… ` banner parses. It owns no policy: which
CONTEXT to look at, whether a foreign context may speak, what to do about a red,
and whether a named set is provably complete are each the CALLER'S decision and
stay in the caller, because the two callers genuinely answer them differently.

🔴 AND EACH CALLER KEEPS ITS OWN ROW-VALIDITY POLICY, WHICH IS NOT THE SAME
POLICY. `main-status-watch.py` validates every decoded row is a dict in its walk
and names the commit when one is not — so it must NEVER silently drop a row,
because a dropped row could be the red it exists to catch. `stale-base-triage.py`
has no such walk and skips a malformed row. `newest_per_context` below therefore
skips non-dict rows: for the watcher that arm is UNREACHABLE (its caller already
proved every row is a dict), and for the triage tool it is the existing
behaviour. Neither caller's contract changes; see
`test_newest_per_context_skips_a_malformed_row_rather_than_raising`.
"""
import re

# ── status classification ─────────────────────────────────────────────────────
# 🔴 ONLY `state == "failure"` IS A RED. Every non-code outcome this pipeline
# reports — superseded, KILLED, NO GATE POD, clone rc 128 — arrives as
# `state == "error"`. Treating `error` as red is the `/commits/{sha}/status`
# roll-up mistake in a second spelling; treating it as GREEN would be worse
# still, so an unrecognised state is classified as NOT-A-VERDICT and the caller
# walks past it. That is the narrowest rule that can be wrong in one direction.
#
# ⚠ THE FINER CLASSES NEED A TEST THAT DRIVES THIS FUNCTION DIRECTLY. Both
# callers fold `superseded`/`killed`/`no-gate-pod`/`error-other` together, so an
# end-to-end test can only ever see "not a verdict" — measured, 35 of this
# function's 60 enumerated mutants survived a fully green 61-test suite, and the
# two fall-through `return "error-other"`s were reached by no fixture at all:
# turning either into `return "green"` survived. That is a state GitHub adds
# tomorrow folded into "main is green", closing a red episode.


def classify(state, description):
    """One status row -> a verdict class. Pure, and the tests drive it directly.

    🔴 UNRECOGNISED MEANS `error-other`, WHICH MEANS NOT-A-VERDICT — never
    `red`, and never `green`. Both fall-through returns below are the arm an
    unknown `state` lands on, and "absence reads as green" is the single failure
    the watcher that consumes this is built against.
    """
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
    """Fold status rows to the NEWEST row per context, by `created_at`.

    A commit accumulates a `pending` row and then its verdict under the SAME
    context, so folding by context is what turns the history into a current
    answer. Without it a stale `pending` from 20 minutes ago outvotes the
    verdict that replaced it.

    🔴 NOT first-wins AND NOT last-wins — BY TIMESTAMP, so the array's order is
    not an input at all. GitHub returns this list newest-first today, which
    makes first-wins accidentally correct and last-wins catastrophically wrong:
    an agent shipped last-wins and reported a known-green head as `pending`. The
    max-`created_at` fold agrees with first-wins on the order GitHub uses today
    AND stays right if that order ever changes, so the hazard is removed rather
    than avoided. `test_newest_per_context_is_order_independent` feeds both
    orders and pins that they agree.

    ⚠ WITH NO `created_at` ANYWHERE — a shape GitHub does not produce, but a
    fixture might — every stamp compares equal and the FIRST row per context
    wins, which is exactly the previous behaviour. The fallback is the old rule,
    never an arbitrary one.

    Returns EVERY context it saw. Filtering to the contexts a caller is allowed
    to believe is the caller's job, and `main-status-watch.py` does it in
    `commit_verdict`: a foreign pipeline's green must not read as this gate's.
    """
    best = {}
    for row in rows:
        # See the module docstring: skipping is the triage tool's existing
        # behaviour, and unreachable for the watcher, whose walk has already
        # proved every row is a dict and named the commit if one was not.
        if not isinstance(row, dict):
            continue
        ctx = row.get("context") or ""
        if not ctx:
            continue
        stamp = row.get("created_at") or ""
        if ctx not in best or stamp > best[ctx][0]:
            best[ctx] = (stamp, row)
    return {ctx: row for ctx, (_, row) in best.items()}


# ── the gate's own banner ─────────────────────────────────────────────────────
# The line `run-tests.sh` prints and the pipeline posts:
#   FAILED: pytests — FAILING: a | b | TOTAL collected=N  passed=N  skipped=N
#                                     failed=N  (floor: …)
# GitHub caps a status description at 140 BYTES, so most real rows arrive cut.
FAILING_RE = re.compile(r"FAILING:\s*(.+?)(?:\s*\|\s*TOTAL\b|$)")
FAILED_COUNT_RE = re.compile(r"\bfailed=(\d+)\b")

# The only fragments a cut can leave behind where `TOTAL` was starting.
# Enumerated rather than pattern-matched: a `startswith` test would also eat a
# real name, and every test in this repo begins `test_` or `Test`, neither of
# which is a prefix of `TOTAL`.
TOTAL_FRAGMENTS = ("T", "TO", "TOT", "TOTA")


def parse_failing_names(description):
    """The test names the description NAMES. May be INCOMPLETE — see the callers.

    A description cut inside ` | TOTAL` leaves a trailing fragment of the word
    TOTAL sitting where a name would be. Left in, it resolves to no file and the
    PR is reported as a real red on the strength of a truncation artefact.

    ⚠ THE FRAGMENT STRIP IS INERT FOR A COMPLETENESS SCREEN AND LOAD-BEARING FOR
    A NAME->FILE LOOKUP, which is why the two copies could diverge unnoticed: a
    row cut at `| TOTA` was necessarily cut before `failed=N` too, so a screen
    that also demands the count refuses either way. Only a caller that RESOLVES
    the names can be hurt by the fragment.
    """
    m = FAILING_RE.search(description or "")
    if not m:
        return []
    names = [n.strip() for n in m.group(1).split("|") if n.strip()]
    if names and names[-1] in TOTAL_FRAGMENTS:
        names.pop()
    return names


def parse_failed_count(description):
    """The banner's own `failed=N`, or None when the cap ate it.

    It is the LAST field in the banner, so whether it survives is decided by the
    failing test's NAME LENGTH. `derived_failure_upper_bound` is the route that
    does not depend on that.
    """
    m = FAILED_COUNT_RE.search(description or "")
    return int(m.group(1)) if m else None


# 🔴 THE THREE FIELDS ARE MATCHED AS ONE ADJACENT, ORDERED GROUP, NOT SEPARATELY,
# AND THAT IS A SOUNDNESS REQUIREMENT RATHER THAN TIDINESS. The hazard is a
# number the 140-byte cap cut SHORT — `collected=22204` arriving as
# `collected=2220` would read as a perfectly well-formed integer and make the
# derived bound far too SMALL, which is the one direction that can certify a
# completeness a row does not have. Requiring `passed=` and then `skipped=` to
# follow proves `collected=` and `passed=` are complete numbers: the cap
# truncates a SUFFIX, so a field with more banner text after it was not cut.
# Only `skipped=` — the last of the three — can itself be short, and a short
# `skipped` SUBTRACTS LESS and so inflates the bound, i.e. errs toward refusing.
TOTALS_RE = re.compile(r"\bcollected=(\d+)\s+passed=(\d+)\s+skipped=(\d+)")


def derived_failure_upper_bound(description):
    """An UPPER bound on the banner's own `failed=`, or None if underivable.

    `scripts/run-tests.sh` GUARD 4 computes, per target,

        collected = passed + skipped + failed + errors + xfailed + xpassed
        TOT_FAILED += failed + errors          (the banner's `failed=` is f+e)

    and accumulates each term into the TOTAL banner this description quotes. So

        collected − passed − skipped == failed + xfailed + xpassed >= failed

    🔴 THE INEQUALITY ONLY EVER POINTS ONE WAY. `xfailed`/`xpassed` are
    non-negative, so this can equal `failed=` but never fall below it. That is
    what makes it usable as a completeness proof: a caller comparing it against
    a count of NAMES is comparing against a ceiling, and a ceiling that happens
    to equal the number of names squeezes `failed` to that same number. An
    under-count would instead certify completeness on a row that had more
    failures than it named — the single error a triage tool must not make.

    The coupling to `run-tests.sh` is pinned by
    `test_the_run_tests_collected_arithmetic_this_derivation_rests_on_is_pinned`.
    """
    m = TOTALS_RE.search(description or "")
    if not m:
        return None
    collected, passed, skipped = (int(g) for g in m.groups())
    derived = collected - passed - skipped
    # Negative is arithmetically impossible under the definition above, so it
    # means the banner is not the banner this derivation was written against.
    # Refusing to answer is the only safe reading of a row we cannot parse.
    return derived if derived >= 0 else None
