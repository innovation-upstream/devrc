#!/usr/bin/env python3
"""Deadman check for the activity-telemetry pipeline: which (host, source) pairs
have STOPPED emitting into `activity.events`?

WHY THIS EXISTS
---------------
2026-08-02: devrc PR #298 added `export const _internals = {...}` to
`activity-plugin.js`. opencode's plugin loader rejects the WHOLE module if any
named export is not a function, so every opencode telemetry hook died on both
hosts for ~11 hours. `emitEvent` swallows every error by design, so nothing
reported it — it was found because someone happened to run a manual check.
Two more of the same shape were sitting in the data unnoticed: the laptop had
ZERO `kind=tool-call` rows for its entire existence (a hand-run deploy script was
never run there), and `kind=session-create` has never emitted a single row ever.

A dead source is SILENT by construction. Nothing else in the pipeline can notice
it, because "no rows" is exactly what a healthy-but-idle source looks like too.

WHAT "DEAD" MEANS HERE
----------------------
Wall-clock staleness cannot work: `keys`/`i3`/`tmux` emit continuously while
`opencode`/`tool` fire only when the operator happens to run them. One threshold
either misses the outage or cries wolf, and alert fatigue is the failure mode
this repo's own bar design (hide-at-zero) is built around.

So silence is measured in ACTIVE TIME, not wall time, and the budget is
MEASURED per (host, source) rather than declared:

  1. Bucket every event into 5-minute buckets, per host and source
     (one GROUP BY — ~11k rows for 14 days across both hosts, measured).
  2. A host's ACTIVE buckets = the buckets in which a HUMAN-PRESENCE source on
     that host emitted (PRESENCE_SOURCES). Overnight and away-from-desk time
     simply is not in this set, so an on-demand source is not punished for the
     operator being asleep.
     🔴 This is an ALLOWLIST, and that is the load-bearing part. It used to be a
     denylist ("anything that is not a timer") which defaulted every source —
     including every AGENT-driven one — to "counts as the operator being
     present", so an unattended overnight agent run marked the night ACTIVE and
     the operator's own sources read DEAD by morning. Measured, with numbers, at
     PRESENCE_SOURCES.
  3. For each (host, source), the historical gap between consecutive emitting
     buckets is counted IN ACTIVE BUCKETS. Its Nth percentile (default p99) is
     that source's own normal worst-case silence.
  4. budget = clamp(K * p99_gap, FLOOR, CAP), and the pair is DEAD when the
     active-time silence since its last event exceeds that budget.

This is what "an on-demand source needs last-seen tracking, not a fixed window"
cashes out to: `tool` is allowed ~31 active hours of silence on the workbench and
`keys` only 2, and neither number was typed by hand.

EXPECTED-PRESENT IS ALSO MEASURED, NOT DECLARED
-----------------------------------------------
A pair is evaluated iff it cleared MIN_BASELINE_BUCKETS in the window. A source
that legitimately does not exist on a host (measured 2026-08-03: `browser` has
0 rows on the workbench — the Brave activity extension is laptop-only) is simply
absent from the evaluated set and can never alarm. A source that WAS emitting and
stopped keeps its baseline for the whole window and does alarm. No table of
per-host expectations is maintained anywhere, so no table can go stale — and one
had: the `activity` SKILL.md claimed keylog/i3/browser were "GUI-only -> laptop
only; the workbench is headless", while the workbench was in fact emitting 41,001
`i3` and 37,376 `keys` rows.

🔴 SCOPE — THE BLIND SPOT, STATED PLAINLY
-----------------------------------------
This is a RELATIVE check: it detects a source dying WHILE ITS PEERS KEEP
EMITTING. A simultaneous outage of every source on every host produces zero
active buckets after the blackout starts, so every silence measures as 0 and the
check reports "0 dead" — which is also exactly what the operator being asleep
looks like. The two are not distinguishable from this table, so the check does
not pretend to distinguish them; `newest_event_age_minutes` is reported so the
operator can see it, and it is deliberately NOT an alarm.
The motivating outage IS in scope: opencode died on both hosts while zsh/tmux/
keys/i3/claude kept emitting.

🔴 THE COST OF THE PRESENCE ALLOWLIST — the blind spot is WIDER, and it needed
its own detector.
Active time now advances only while a PRESENCE_SOURCES member emits, so the
blackout above no longer needs every source to die: losing ALL the presence
sources at once is enough. An X-session crash is the realistic shape (it takes
`keys` and `i3` together, and `tmux`/`zsh` with the terminals). After it, active
buckets stop accruing, so EVERY source on that host measures 0 silence and reads
not-dead — not just the presence sources. Measured against the real table with a
seeded workbench presence blackout 96h old plus `workbench/claude` genuinely
silent 74h: the old rule reported 5 dead pairs, the allowlist reported ZERO.
🔴 `newest_event_age_minutes` does NOT mitigate this, and an earlier version of
this paragraph claimed it did. It is `max` over the WHOLE result — every host and
every source — so surviving agent rows pin it at ~1 minute while a host's human
timeline is days stale. It cannot see a per-host presence blackout at all.
The real mitigation is STATE_PRESENCE_STALLED (see PRESENCE_STALL_HOURS): a host
that is still emitting while its presence timeline has been frozen past any
plausible away-from-desk period is reported as CANNOT-TELL, with its remaining
pairs marked un-evaluated, rather than as a silent `ok`. A pair that had ALREADY
blown its budget before the stall began keeps its DEAD verdict — that silence was
measured against real active buckets, so "cannot tell about the host" must not
erase "this source is dead".
Losing ONE presence source is still caught: the others keep the timeline
advancing, which is the whole motivating case (workbench/keys, below).

🔴 AND IT COSTS DETECTION LATENCY. Budgets are denominated in ACTIVE buckets, so
a slower-advancing active timeline means a real death takes LONGER in wall time
to convict. MEASURED 2026-08-11 over the live 14-day table -- the smallest seeded
drop at which each pair reads DEAD, old rule -> allowlist:
  workbench/keys 6h->24h · workbench/i3 6h->24h · workbench/tmux 6h->24h ·
  workbench/zsh 12h->24h · workbench/claude 12h->24h · laptop/keys 12h->24h ·
  laptop/i3 12h->24h · laptop/browser 12h->24h · workbench/tool 36h->96h ·
  workbench/browser-bridge 12h->24h -- which ERODES the 15-minute heartbeat #388
  added one commit earlier specifically to make that source detectable.
NO pair got faster, in any run. Ten of seventeen got slower; the rest were
unchanged. The individual pair figures DRIFT between sweeps (an earlier run of
the same probe measured workbench/keys 6h->12h and laptop/zsh 24h->72h) because
the table advances -- the robust claim is the DIRECTION and the count, not any
one number.
🔴 There is NO clean per-class rule here, and an earlier version of this
paragraph asserted one ("floor-pinned sources tighten, on-demand sources
loosen"). Both directions were wrong: the FLOOR is a fixed 24 active buckets, so
it spans MORE wall hours once active time advances more slowly, and the on-demand
laptop/tool got NOISIER in the sweep (27 -> 28 dead-hours), not quieter. Silence
and budget both shrink; which shrinks faster depends on where a pair's own gaps
sit relative to the removed agent-only buckets, which is a property of the pair,
not of its class. Measure it; do not reason about it.
The trade this change makes is therefore: FEWER FALSE ALARMS, SLOWER TRUE ONES.
Baselines re-form over the 14-day window.

🔴 "CANNOT TELL" IS NOT "HEALTHY"
---------------------------------
A health check whose reassuring answer is a zero is precisely the shape that can
be wired to nothing and report green forever. So every non-evaluable condition
gets its OWN state and none of them is `ok`:

  not-configured  no CLICKHOUSE_URL/USER/PASSWORD available
  unreachable     the HTTP request to ClickHouse failed
  query-failed    ClickHouse answered, but the response would not parse
  no-data         the query returned zero rows, or no pair cleared the baseline
                  -- i.e. we cannot prove we read the table at all
  misconfigured   PRESENCE_SOURCES is empty, so "active time" has no definition
                  and nothing can be judged (see presence_count_expr)
  presence-stalled a host is still emitting while its HUMAN timeline is frozen
                  past PRESENCE_STALL_HOURS -- that host's remaining sources
                  cannot be judged, and reading `ok` there would be the invisible
                  green this whole section exists to prevent. It does NOT
                  suppress a pair that was already past its budget when the
                  stall began: that death is measured, and it stays in `dead`,
                  in `count` and by NAME in `detail`.
  ok              the table was read and at least one pair was evaluated

A host that has emitted NO human-driven row in the whole window is a different
case and gets no state of its own: there is no normal to compare against, so its
pairs are marked un-evaluated (`reason="no-presence-baseline"`) exactly like an
insufficient baseline, and the fleet verdict is unaffected. See the split in
`evaluate` for why merging the two made a permanently-red gate.

`ok` therefore carries its own positive control: it is unreachable unless rows
came back AND at least one pair was actually measured. `evaluated` is in the
payload for the same reason -- a count that must be non-zero for the verdict to
mean anything.

CLI
---
    python3 deadman.py --json                 # live query (env or --env-file)
    python3 deadman.py --tsv FILE [--now N]   # evaluate a captured/seeded TSV
                                              #   (the control harness: seed a
                                              #   stale source and watch the
                                              #   count move off zero)
Exit code is 0 for ok-and-clean, 1 when something is dead, 2 for any unknown
state. Never raises.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------- #
# Tunables. Every default below is derived from a MEASUREMENT, named inline.
# --------------------------------------------------------------------------- #
BUCKET_SECONDS = 300          # 5-minute buckets
BASELINE_DAYS = 14

# A pair needs this many emitting buckets in the window before it is judged at
# all. Below it the gap statistics are noise, and a brand-new source would alarm
# before it had a normal to compare against. Measured 2026-08-03: the thinnest
# LIVE pair was laptop/tool at 45 buckets, so 20 leaves real headroom while still
# excluding a source that fired twice.
MIN_BASELINE_BUCKETS = 20

# p99 rather than p95: p95 tracks the typical lull, p99 tracks the worst NORMAL
# lull, which is the number a budget has to clear to avoid crying wolf.
GAP_QUANTILE = 0.99

# Slack on top of the measured worst normal lull.
BUDGET_K = 2.0

# Floor: 24 buckets = 2 ACTIVE hours. Applies to the continuous sources, whose
# p99 gap is 1-6 buckets -- without a floor they would alarm on a coffee break.
FLOOR_BUCKETS = 24

# Cap: 576 buckets = 48 ACTIVE hours (~5-6 wall days at the measured ~8.3 active
# hours/day). Chosen to sit well ABOVE the largest gap observed anywhere in the
# 14-day window (224 buckets, workbench/tool) so it cannot manufacture a false
# alarm, while still bounding a pathological budget.
CAP_BUCKETS = 576

# --------------------------------------------------------------------------- #
# HUMAN-PRESENCE SOURCES — the ALLOWLIST that defines ACTIVE time.
#
# A bucket joins its host's ACTIVE set iff one of THESE emitted in it. Everything
# else (`claude`, `tool`, `opencode`, `browser-bridge`, and anything added later)
# proves only that a MACHINE was doing something.
#
# 🔴 WHY AN ALLOWLIST AND NOT A DENYLIST. This used to be `NOT MACHINE_CADENCE`
# — exclude the timer-driven emitters, count everything else as the operator
# being present. A denylist defaults every NEW source to "the human is here",
# and the agent sources are exactly the ones where that is false. The asymmetry
# is total: when the operator really is at the machine, `keys`/`tmux`/`i3` have
# already marked those buckets active, so an agent row adds nothing; agent rows
# add buckets ONLY when the operator is away, which is precisely when adding
# them manufactures a false alarm.
#
# THE MOTIVATING INCIDENT (2026-08-11): an unattended overnight agent session
# emitted `claude`/`tool` rows for hours while the human slept. That marked the
# night ACTIVE, so `workbench/keys` and `workbench/tmux` blew their 2-ACTIVE-HOUR
# floor and read DEAD — the same failure shape the heartbeat denylist was added
# to fix, from a source nobody thought to deny.
#
# MEASURED 2026-08-11, sweeping the evaluation point hourly over 169 points
# (7 days) across the live 14-day table, denylist rule vs this allowlist
# (dead-hours per pair). 🔴 The table ADVANCES between runs, so repeated sweeps
# differ by 1-2 dead-hours per pair; these are the final run against the code as
# it stands, not a stable constant:
#   workbench/tmux            7 -> 0      workbench/keys           14 -> 7
#   workbench/browser-bridge 18 -> 8      laptop/browser-bridge   106 -> 91
#   laptop/tmux               3 -> 3      laptop/claude            50 -> 50
#   laptop/tool              27 -> 28  <- NOISIER: the allowlist is NOT uniformly
#                                         quieter and there is no per-class rule
# 🔴 `laptop/tool` is the ONLY noisier pair that REPRODUCES. Earlier revisions of
# this comment also named laptop/claude and laptop/opencode as noisier; across
# four independent sweeps (three mine, one an auditor's) laptop/claude measured
# 50->50, 49->50, 50->50 and 53->52 (quieter), and laptop/opencode was absent
# from three of the four. They were drift, not signal, and are dropped. Trust the
# DIRECTION-plus-example, never a specific dead-hour count.
# 🔴 workbench/keys does NOT go to zero, and an earlier version of this comment
# said it did off a 4-day window. Its surviving 7 dead-hours are a TRUE POSITIVE:
# 2026-08-05 15:00-21:00, `keys` silent 2.8-8.1 ACTIVE hours against a 2.0h
# budget while `i3`, `tmux` and `zsh` on the same host kept emitting. That is
# precisely the case this check exists for. The 7 hours the allowlist REMOVED
# were the overnight ones.
# Positive control, same run: seeding a real death by dropping a pair's last N
# hours is still caught for every pair, at a LONGER latency -- the numbers are in
# the docstring's COST section.
#
# 🔴 WHY `browser` IS NOT HERE, though the operator drives it. The browser-bridge
# agent drives the SAME Brave profile the activity extension instruments, so
# agent navigation emits `browser` rows -- the one remaining path by which the
# incident above could recur. MEASURED 2026-08-11 on the live table: of 777
# laptop `browser` buckets, 76 co-occur with a `browser-bridge` command bucket,
# and `browser` is the SOLE presence source in exactly ONE. Dropping it changed
# the 169-point sweep for ZERO pairs and improved one seeded-death latency
# (laptop/zsh). It bought nothing and carried a contamination path, so it is out.
# (`browser` is laptop-only -- the workbench has never emitted a `browser` row.)
#
# 🔴 ADDING A SOURCE THAT A HUMAN DRIVES DIRECTLY MEANS ADDING IT HERE — and
# adding an agent-driven one, or one an agent can DRIVE INDIRECTLY, means NOT
# adding it. The cost of getting the first case wrong is quiet (the timeline just
# advances more slowly); the cost of the second is the overnight false alarm
# above. The test that catches an agent source sneaking in is
# test_an_unattended_agent_at_night_does_not_make_human_sources_look_dead — it
# uses a day/night fixture, because a fixture with no idle time is structurally
# blind to this entire class of bug.
PRESENCE_SOURCES = frozenset({"keys", "i3", "tmux", "zsh"})

# --------------------------------------------------------------------------- #
# PRESENCE-STALL DETECTION — the cost of the allowlist, made observable.
#
# A host whose presence timeline is frozen while it KEEPS EMITTING cannot be
# judged at all: its active set stops growing, so every source on it measures 0
# silence and reads not-dead. Reporting `ok` there is exactly the invisible green
# the module's "CANNOT TELL IS NOT HEALTHY" section forbids, so it gets its own
# state instead.
#
# The predicate is `last_event - last_presence_event > PRESENCE_STALL_SECONDS`,
# per host, and it is deliberately NOT `now - last_presence`.
#
# 🔴 The mechanism is FREEZE, not "the two timestamps move together" — an earlier
# comment said the latter and it is not what the data does. Both timestamps stop
# advancing at power-off, so the gap holds whatever value it had at that instant.
# Measured on the live table: the workbench froze at a 38.75h gap and held that
# exact value for the 4.6h it stayed down. That is still the right behaviour for
# the common case (a laptop shut at the desk has a small gap, so it stays quiet
# instead of screaming every weekend, which `now - last_presence` would do), but
# the honest consequence is: a host powered off while ALREADY past the threshold
# stays `presence-stalled` for the rest of the 14-day window. That is not a false
# positive — it really was unjudgeable when it went down, and it still is — but
# it does mean the condition can outlive the machine.
#
# MEASURED 2026-08-11 over the full live 14-day window, hourly, restricted to
# points where the host was still emitting: the laptop's worst presence stall was
# 8.9h (0 points over 24h), the workbench's was 39.8h (2 points over 24h, 2 over
# 36h, ZERO over 48h) — an operator away for a weekend while agents ran. A second
# sweep over the most recent 7 days agreed: 39.0h worst, zero points over 48h.
# 72h is therefore 1.8x the worst NORMAL stall observed anywhere, which is the
# same "clear the worst normal, do not cry wolf" sizing FLOOR_BUCKETS uses. It
# fires zero times on the real table and fires on the seeded 96h blackout above.
PRESENCE_STALL_HOURS = 72

# --------------------------------------------------------------------------- #
# MACHINE-CADENCE EMITTERS — rows that prove a SOURCE is alive but do NOT prove
# the OPERATOR is present: `browser-bridge` emits a `heartbeat` every 900s so its
# own liveness is measurable (see the heartbeat contract in the tests).
#
# 🔴 THIS IS NO LONGER USED FOR ACTIVE TIME, AND IT IS STILL HERE ON PURPOSE.
# PRESENCE_SOURCES subsumes it for this module — `browser-bridge` is not a
# presence source, so its heartbeat cannot mark a bucket active no matter what
# `kind` it carries — and the two are deliberately NOT both applied to the active
# set, because layering a denylist under an allowlist gives two places to edit
# for one rule.
# It stayed exported because scripts/agent-ops imported BOTH this tuple and
# `cadence_predicate_sql` for its telemetry-freshness panel, where the concept is
# genuinely different: that panel wants to exclude MACHINE-GENERATED rows while
# still counting `claude`/`tool` as real USAGE of those sources. "Is this row
# machine-generated?" and "does this row prove a human is at the desk?" are
# different questions, and only the second one defines active time.
# Seam tests: scripts/collector/tests/test_deadman.py (the agent-ops-side
# ones went with that TUI when it was retired).
#
# 🔴 ADDING A TIMER-DRIVEN EMITTER ANYWHERE IN THE PIPELINE MEANS ADDING IT HERE
# (for agent-ops' since-retired freshness panel) — and, separately, keeping it OUT of
# PRESENCE_SOURCES.
MACHINE_CADENCE = (("browser-bridge", "heartbeat"),)

DEFAULT_ENV_FILE = "~/.config/activity-collector/env"
DEFAULT_TIMEOUT = 8.0

STATE_OK = "ok"
STATE_NO_DATA = "no-data"
STATE_UNREACHABLE = "unreachable"
STATE_QUERY_FAILED = "query-failed"
STATE_NOT_CONFIGURED = "not-configured"
STATE_MISCONFIGURED = "misconfigured"
STATE_PRESENCE_STALLED = "presence-stalled"

# Everything that is NOT a measured verdict. The whole point of the module is
# that these are distinguishable from `ok`, so they are enumerated once here and
# every consumer asks this set rather than re-deriving the list.
# 🔴 scripts/bar-status-poll carries a hardcoded FALLBACK copy of this set for
# the case where importing this module fails. Adding a state here means adding it
# there too, or the poller renders the new "cannot tell" as a healthy green.
# Pinned by scripts/tests/test_bar_status.py.
UNKNOWN_STATES = frozenset(
    {STATE_NO_DATA, STATE_UNREACHABLE, STATE_QUERY_FAILED, STATE_NOT_CONFIGURED,
     STATE_MISCONFIGURED, STATE_PRESENCE_STALLED}
)


# --------------------------------------------------------------------------- #
# Query
# --------------------------------------------------------------------------- #
def cadence_predicate_sql(cadence=MACHINE_CADENCE) -> str:
    """A ClickHouse boolean matching any MACHINE_CADENCE pair; "" when empty.

    🔴 THE ONE PLACE this predicate is RENDERED, not just the one place the pairs
    are declared. Exported because scripts/agent-ops needed the same predicate for
    its freshness panel: a second copy of the `" OR ".join` immediately grew the
    same latent bug (joining with AND makes `NOT (A AND B)` exclude NOTHING),
    which is only reachable once a second pair exists -- i.e. it would have gone
    unnoticed until exactly the moment MACHINE_CADENCE's own note tells a
    maintainer to add one. Single-sourcing the DATA was not enough.
    """
    return " OR ".join("(source = '%s' AND kind = '%s')" % (s, k)
                       for s, k in cadence)


def presence_predicate_sql(presence=PRESENCE_SOURCES) -> str:
    """A ClickHouse boolean matching any PRESENCE_SOURCES member.

    Sorted so the rendered SQL is stable (PRESENCE_SOURCES is a set) and so a
    test can pin the literal string.

    🔴 RAISES on an empty allowlist rather than rendering anything. An earlier
    version returned "" and the caller degraded to `0 AS n_presence`, which is
    VALID ClickHouse: run live it produced `state=ok evaluated=13 count=0` — a
    confident green with nothing actually judged, and `evaluated` stayed non-zero
    so evaluate's own positive control could not catch it. There is no safe SQL
    for "active time is undefined"; the only honest answer is to refuse.
    """
    if not presence:
        raise ValueError("PRESENCE_SOURCES is empty: active time has no "
                         "definition, so no bucket can be active and no pair "
                         "can be judged — refusing to render a query")
    return "source IN (%s)" % ", ".join("'%s'" % s for s in sorted(presence))


def presence_count_expr(presence=PRESENCE_SOURCES) -> str:
    """`countIf(...)` counting only HUMAN-PRESENCE events in the bucket.

    Built from PRESENCE_SOURCES so the SQL and the Python cannot drift apart.
    Raises on an empty allowlist — see presence_predicate_sql.
    """
    return "countIf(%s)" % presence_predicate_sql(presence)


# The header `FORMAT TSVWithNames` emits, and the ONE place its column names are
# spelled. `parse_buckets` compares against this exact string, so the query and
# the parser cannot disagree about the format -- which is the whole reason the
# header exists (see bucket_query).
TSV_COLUMNS = ("host", "source", "b", "n", "n_presence")
TSV_HEADER = "\t".join(TSV_COLUMNS)


def bucket_query(days: int = BASELINE_DAYS, bucket_seconds: int = BUCKET_SECONDS,
                 database: str = "activity", table: str = "events",
                 presence=PRESENCE_SOURCES) -> str:
    """The ONE query the check runs: per host/source/bucket event counts.

    Deliberately a plain GROUP BY rather than a window function -- all of the
    gap/percentile work happens in `evaluate`, which is pure and therefore
    testable against a fixture with no ClickHouse anywhere near it.

    FIVE columns, and the fifth is the whole point: `n` counts everything the
    pair emitted (so an agent-driven row, or a heartbeat, still proves ITS source
    alive), while `n_presence` counts only human-presence events (so neither a
    heartbeat nor an overnight agent run marks the bucket ACTIVE for every other
    source on the host). See PRESENCE_SOURCES.

    🔴 `FORMAT TSVWithNames`, not `FORMAT TSV`, and the header is load-bearing.
    The PREVIOUS version of this query was ALSO five columns wide, but its fifth
    was `n_op` (operator-driven = anything not machine-cadence). A capture taken
    in that window replays through `parse_buckets` with n_op ~= n for every
    source, which silently reinstates the exact overnight false-alarm bug this
    module was changed to remove — and a headerless TSV carries nothing that
    could tell the two apart. The header makes the format self-describing, so a
    stale capture is an ERROR instead of a wrong answer.
    """
    return (
        "SELECT host, source, "
        "toUnixTimestamp(toStartOfInterval(ts, INTERVAL %d SECOND)) AS b, "
        "count() AS n, %s AS n_presence "
        "FROM %s.%s WHERE ts > now() - INTERVAL %d DAY "
        "GROUP BY host, source, b ORDER BY host, source, b FORMAT TSVWithNames"
        % (int(bucket_seconds), presence_count_expr(presence), database, table,
           int(days))
    )


def parse_buckets(text: str, presence=PRESENCE_SOURCES):
    """TSV -> [(host, source, bucket_epoch, n, n_presence)]. ValueError on junk.

    THREE accepted shapes, decided by the FIRST non-empty line:

      TSV_HEADER then 5-field rows   the current query (FORMAT TSVWithNames).
                                     n_presence is AUTHORITATIVE — the server
                                     computed it from PRESENCE_SOURCES.
      4-field rows, no header        a hand-written fixture or a pre-heartbeat
                                     capture, which carries no presence column
                                     at all -> RECONSTRUCTED here as
                                     `n if source in PRESENCE_SOURCES else 0`.
      any other header               ERROR, naming what was seen.

    🔴 A 5-FIELD FILE WITHOUT A HEADER IS REFUSED, and that rejection is the
    point of the header. The PREVIOUS query was also five columns wide, but its
    fifth was `n_op` (operator-driven = not machine-cadence), which is ~= `n` for
    every source. Replaying such a capture would silently reinstate the exact
    overnight false-alarm bug this module was changed to remove, and nothing in a
    headerless TSV can tell the two formats apart — the widths are equal and the
    values are plausible. So the ambiguous case is an error, not a guess.

    🔴 The 4-field reconstruction, by contrast, is exact rather than a guess, and
    that is why it is allowed. Presence is a property of the SOURCE alone and the
    source column is right there — unlike the machine-cadence rule, which also
    needed `kind`. Both alternatives are wrong: carrying `n` across reinstates
    "every agent row means the human is here"; hard-zeroing makes a replay unable
    to judge anything and report a reassuring "0 dead".

    A file may not MIX widths: a width that changes mid-file is a format change
    or a corrupted capture, and inferring per-line would be the silent-drop
    failure this parser exists to avoid.

    Strict on purpose otherwise. Silently dropping unparseable lines would let a
    changed output format read as "fewer rows" and, at the limit, as "no
    staleness" -- the exact way a parsed-output harness lies (RULES.md: a tool's
    output format is a dependency you did not pin).
    """
    rows = []
    width = None
    header_seen = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if width is None:
            # A header line is identified by its first two column NAMES, not by
            # a heuristic on the numeric fields -- `notanumber` in the bucket
            # column must stay a non-integer error, not become "unknown header".
            if parts[:2] == ["host", "source"]:
                if line.rstrip("\r") != TSV_HEADER:
                    hint = ""
                    if len(parts) == 5 and parts[4].strip() == "n_op":
                        hint = (" — this is a PRE-PRESENCE capture whose 5th "
                                "column counts operator-driven events, not "
                                "human-presence ones; replaying it would "
                                "reinstate the bug that column was replaced to "
                                "fix. Re-capture it.")
                    raise ValueError("line %d: unexpected header %r (expected "
                                     "%r)%s" % (lineno, line, TSV_HEADER, hint))
                header_seen = True
                width = 5
                continue
            if len(parts) == 5:
                raise ValueError(
                    "line %d: 5 columns with no header — ambiguous. The "
                    "pre-presence query was also 5 columns wide with a "
                    "DIFFERENT 5th column (n_op), and nothing here can tell "
                    "them apart. Re-capture with FORMAT TSVWithNames, or "
                    "prepend %r." % (lineno, TSV_HEADER))
            if len(parts) != 4:
                raise ValueError("line %d: expected 4 tab-separated fields (or a "
                                 "%r header followed by 5), got %d"
                                 % (lineno, TSV_HEADER, len(parts)))
            width = 4
        elif len(parts) != width:
            raise ValueError("line %d: field count changed mid-file (%d, expected "
                             "%d) — refusing to guess" % (lineno, len(parts), width))
        host, source, b, n = parts[:4]
        if header_seen:
            n_presence = parts[4]
        else:
            n_presence = n if source in presence else "0"
        try:
            rows.append((host, source, int(b), int(n), int(n_presence)))
        except ValueError:
            raise ValueError("line %d: non-integer bucket/count: %r" % (lineno, line))
    return rows


def fetch_buckets(url: str, user: str, password: str, query: str,
                  timeout: float = DEFAULT_TIMEOUT) -> str:
    """POST the query to ClickHouse and return the raw TSV body. Raises."""
    req = urllib.request.Request(url.rstrip("/") + "/",
                                 data=query.encode("utf-8"), method="POST")
    if user:
        import base64
        token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Pure evaluation
# --------------------------------------------------------------------------- #
def percentile(values, p: float) -> float:
    """Linear-interpolated percentile over a list. [] -> 0.0."""
    if not values:
        return 0.0
    v = sorted(values)
    if len(v) == 1:
        return float(v[0])
    k = (len(v) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(v[int(k)])
    return float(v[lo]) + (float(v[hi]) - float(v[lo])) * (k - lo)


def budget_for(gap_p: float, k: float = BUDGET_K, floor: int = FLOOR_BUCKETS,
               cap: int = CAP_BUCKETS) -> int:
    """clamp(k * gap_p, floor, cap), in active buckets."""
    return int(min(cap, max(floor, round(k * gap_p))))


def evaluate(rows, now=None, *, bucket_seconds: int = BUCKET_SECONDS,
             min_baseline: int = MIN_BASELINE_BUCKETS,
             quantile: float = GAP_QUANTILE, k: float = BUDGET_K,
             floor: int = FLOOR_BUCKETS, cap: int = CAP_BUCKETS,
             presence=PRESENCE_SOURCES,
             stall_hours: float = PRESENCE_STALL_HOURS) -> dict:
    """Pure: bucket rows -> verdict dict. No I/O, no clock unless `now` is None.

    Returns {"state", "rows", "evaluated", "dead", "count", "sources",
             "presence_stalled", "newest_event_age_minutes"}.
    `state` is STATE_OK only when rows came back, at least one pair was actually
    measured, AND no host's human timeline is stalled -- see the module docstring
    on why the zero needs all three.
    """
    now = int(time.time()) if now is None else int(now)
    if not presence:
        # No allowlist means active time is undefined, which means nothing can be
        # judged. Loud, not quiet: see presence_predicate_sql.
        return {"state": STATE_MISCONFIGURED, "rows": len(rows), "evaluated": 0,
                "dead": [], "count": 0, "sources": [], "presence_stalled": [],
                "newest_event_age_minutes": None,
                "detail": "PRESENCE_SOURCES is empty — active time has no "
                          "definition, so nothing can be judged"}
    if not rows:
        return {"state": STATE_NO_DATA, "rows": 0, "evaluated": 0,
                "dead": [], "count": 0, "sources": [], "presence_stalled": [],
                "newest_event_age_minutes": None,
                "detail": "query returned no rows — cannot tell"}

    # ACTIVE time is HUMAN-PRESENCE time. A bucket joins the host's active set
    # only if a PRESENCE_SOURCES member emitted in it -- an agent-driven source
    # (or a timer-driven one) proves its own source alive and nothing more.
    # Without this, an unattended overnight agent run makes every idle source on
    # the host accrue active silence while the operator sleeps and read DEAD by
    # morning; see PRESENCE_SOURCES for the measurement.
    #
    # The presence count arrives in the row: the live query computes it (a 5-wide
    # TSV). A 4-tuple -- a hand-built fixture that never went through
    # `parse_buckets` -- gets the SAME reconstruction the parser applies, so a
    # 4-wide row means one thing regardless of which door it came in by. A test
    # that wants an agent source to count as presence (the negative control that
    # reproduces the old rule) must say so with an explicit 5th field.
    active_by_host = collections.defaultdict(set)
    buckets_by_pair = collections.defaultdict(set)
    last_event_by_host = {}
    for row in rows:
        host, src, b, n = row[0], row[1], row[2], row[3]
        n_presence = row[4] if len(row) > 4 else (n if src in presence else 0)
        buckets_by_pair[(host, src)].add(b)
        if b > last_event_by_host.get(host, -1):
            last_event_by_host[host] = b
        if n_presence > 0:
            active_by_host[host].add(b)

    newest = max(r[2] for r in rows)

    # PRESENCE-STALL: a host that kept emitting for longer than `stall_hours`
    # after its last human-driven event cannot be FULLY judged — its active set
    # stopped growing at `last_presence`, so no pair on it can accrue any further
    # active silence. See PRESENCE_STALL_HOURS for the threshold's measurement
    # and for why the predicate compares the host's own two timestamps.
    #
    # 🔴 TWO DIFFERENT CONDITIONS, deliberately NOT merged (they were once, and
    # the merged version was a permanently-red gate — see below):
    #
    #   stalled            the host HAD a human timeline in this window and lost
    #                      it. That is a CHANGE, which is exactly what a deadman
    #                      detects, so it raises the host-level cannot-tell.
    #   no-presence-       the host has NEVER emitted a human-driven row in the
    #   baseline           window at all. That is not a change and there is no
    #                      normal to compare against, so it follows the module's
    #                      existing doctrine for "expected-present is MEASURED,
    #                      not declared": the pairs are marked un-evaluated (never
    #                      silently `ok`) and the host raises NOTHING.
    #
    # 🔴 Why the split is load-bearing. Merged, the `None` arm had no threshold
    # and no minimum-evidence requirement, so ONE row from any host that emits
    # agent traffic but never presence traffic — an agent pod, a container, a
    # mis-set ACTIVITY_HOST, a single manual `emit` — flipped the WHOLE fleet
    # verdict to cannot-tell and zeroed the other hosts' real dead count, for the
    # full 14 days until that row rolled out of the window. Measured: one
    # synthetic ("agentpod", "claude") row added to the otherwise-real table took
    # the fleet from `state=ok count=1` to `state=presence-stalled count=1`, and
    # through the bar from `tlm 1` Critical to `tlm ?` Warning with the toast
    # suppressed. A permanently-red gate is worse than no gate.
    stall_seconds = float(stall_hours) * 3600.0
    stalled = []
    no_presence_hosts = set()
    for host, last_event in sorted(last_event_by_host.items()):
        act = active_by_host.get(host)
        if not act:
            no_presence_hosts.add(host)
            continue
        last_presence = max(act)
        if last_event - last_presence > stall_seconds:
            stalled.append({
                "host": host,
                "last_presence_epoch": last_presence,
                "last_event_epoch": last_event,
                "stall_hours": round((last_event - last_presence) / 3600.0, 2),
            })
    stalled_hosts = {s["host"] for s in stalled}

    sources = []
    for (host, src), bset in sorted(buckets_by_pair.items()):
        active = sorted(active_by_host[host])
        ev = sorted(bset)
        last = ev[-1]
        # Active buckets strictly AFTER the pair's last emitting bucket. The
        # pair's own buckets are in `active`, so "strictly after" is what makes
        # a live source measure 0 rather than 1.
        silent = len(active) - bisect.bisect_right(active, last)
        rec = {
            "host": host,
            "source": src,
            "baseline_buckets": len(ev),
            "last_event_epoch": last,
            "wall_silent_minutes": max(0, (now - last) // 60),
            "silent_active_buckets": silent,
            "silent_active_hours": round(silent * bucket_seconds / 3600.0, 2),
        }
        if host in no_presence_hosts:
            # No human timeline on this host in the whole window, so its active
            # set is empty and every pair measures 0 silence. Same shape as
            # insufficient-baseline: not judged, never alarmed, and VISIBLE in
            # the table rather than a confident `ok`.
            rec.update({"evaluated": False, "reason": "no-presence-baseline",
                        "gap_p": None, "budget_buckets": None,
                        "budget_active_hours": None, "dead": False})
            sources.append(rec)
            continue
        if len(ev) < min_baseline:
            # Not enough history to have a normal. Never alarms — this is also
            # what makes a legitimately-absent source (a source with 0 rows on a
            # host) silent rather than noisy: it is not in `rows` at all.
            rec.update({"evaluated": False, "reason": "insufficient-baseline",
                        "gap_p": None, "budget_buckets": None,
                        "budget_active_hours": None, "dead": False})
            sources.append(rec)
            continue
        gaps = []
        for i in range(len(ev) - 1):
            lo = bisect.bisect_right(active, ev[i])
            hi = bisect.bisect_left(active, ev[i + 1])
            gaps.append(hi - lo)
        gap_p = percentile(gaps, quantile)
        budget = budget_for(gap_p, k=k, floor=floor, cap=cap)
        rec.update({
            "evaluated": True,
            "reason": None,
            "gap_p": round(gap_p, 2),
            "budget_buckets": budget,
            "budget_active_hours": round(budget * bucket_seconds / 3600.0, 2),
            "dead": silent > budget,
        })
        if host in stalled_hosts and not rec["dead"]:
            # 🔴 PER-PAIR, not per-host. An earlier version dropped EVERY pair on
            # a stalled host, on the false premise that "silence is 0 by
            # construction for all of them". That holds only for a pair whose
            # last event lands at or after the host's last presence bucket. A
            # pair that died BEFORE the stall began accrued its silence against
            # REAL active buckets, and that measurement is genuine and already
            # complete — measured on the live table (96h blackout,
            # workbench/claude dead 200h): 54.58 silent ACTIVE hours against a
            # 2.0h budget, 27x over, discarded. That is the very scenario this
            # detector was written for, and dropping it made the check QUIETER
            # than before the detector existed.
            #
            # So a pair already past its budget stays DEAD and stays named. A
            # pair still under budget becomes un-evaluated instead of `ok`,
            # because its silence can never grow while the timeline is frozen:
            # "not dead" is the claim we genuinely cannot make, and "dead" is the
            # one we already made.
            rec.update({"evaluated": False, "reason": "presence-stalled",
                        "dead": False})
        sources.append(rec)

    evaluated = [s for s in sources if s["evaluated"]]
    dead = [s for s in evaluated if s["dead"]]
    base = {
        "rows": len(rows),
        "evaluated": len(evaluated),
        "dead": dead,
        "count": len(dead),
        "sources": sources,
        "presence_stalled": stalled,
        "newest_event_age_minutes": max(0, (now - newest) // 60),
    }
    if stalled:
        # 🔴 A stalled host WINS over both `ok` and `no-data` for the STATE, and
        # it wins even when a real dead pair is present — because the alternative
        # is a verdict that reads healthy for a host whose remaining sources are
        # unjudgeable. It does NOT win over the death itself: `dead` and `count`
        # stay populated and `describe_stalled` puts the dead pair's NAME first.
        #
        # The bar follows suit rather than flattening this: an unknown state
        # carrying count>0 renders `tlm N` Critical with the toast firing, and
        # only a count of 0 renders `tlm ?` (bar-status-poll: parse_telemetry,
        # toast_suppressed). An earlier version of this comment claimed the bar
        # masked the count here — it did, and that was the regression this
        # sentence now documents as CLOSED. Do not "re-fix" the trade.
        # Measured on 14 days of real data this predicate fires ZERO times (see
        # PRESENCE_STALL_HOURS).
        return dict(base, state=STATE_PRESENCE_STALLED,
                    detail=describe_stalled(stalled, dead, len(evaluated)))
    if not evaluated:
        return dict(base, state=STATE_NO_DATA, evaluated=0, dead=[], count=0,
                    detail="no (host, source) pair cleared the baseline — "
                           "cannot tell")
    return dict(base, state=STATE_OK, detail=describe(dead, len(evaluated)))


def describe_stalled(stalled, dead, evaluated: int) -> str:
    """One-line summary for a presence-stalled verdict.

    🔴 A MEASURED DEATH LEADS. A stalled host does not suppress a pair that was
    already past its budget when the stall began, and the operator must be able
    to read the source's NAME here — an earlier version buried the whole verdict
    behind "N other pair(s) still measured" and the dead source's identity
    appeared nowhere at all.
    """
    hosts = "; ".join(
        "%s: human timeline frozen %.1fh before its newest row"
        % (s["host"], s["stall_hours"]) for s in stalled)
    stall_note = ("presence stalled (%s) — those hosts' remaining sources are "
                  "unjudgeable" % hosts)
    if dead:
        return "%s — AND CANNOT TELL: %s" % (describe(dead, evaluated),
                                             stall_note)
    tail = "; %d other pair(s) fresh" % evaluated if evaluated else ""
    return "CANNOT TELL — %s%s" % (stall_note, tail)


def describe(dead, evaluated: int) -> str:
    """One-line human summary — the toast body and the block's popup text."""
    if not dead:
        return "%d source(s) fresh" % evaluated
    parts = ["%s/%s silent %.1fh active (budget %.1fh)"
             % (d["host"], d["source"], d["silent_active_hours"],
                d["budget_active_hours"]) for d in dead[:4]]
    more = "" if len(dead) <= 4 else " (+%d more)" % (len(dead) - 4)
    return "; ".join(parts) + more


# --------------------------------------------------------------------------- #
# Config + the live check
# --------------------------------------------------------------------------- #
def load_env_file(path: str = DEFAULT_ENV_FILE) -> dict:
    """Read KEY=VALUE lines from the collector's 0600 env file. Never raises.

    This is the same file the collector itself uses, so the check reads exactly
    the credentials that are known to work on this host, and no new secret is
    introduced anywhere (nothing lands in the nix store).
    """
    out = {}
    try:
        with open(os.path.expanduser(path)) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                out[key.strip()] = val.strip()
    except Exception:
        return {}
    return out


def resolve_config(env=None, env_file: str = DEFAULT_ENV_FILE) -> dict:
    """Process env wins; the collector env file fills the gaps."""
    env = os.environ if env is None else env
    fromfile = load_env_file(env_file)

    def pick(name, default=""):
        v = env.get(name)
        if v:
            return v
        return fromfile.get(name, default)

    return {
        "url": pick("CLICKHOUSE_URL"),
        "user": pick("CLICKHOUSE_USER"),
        "password": pick("CLICKHOUSE_PASSWORD"),
        "database": pick("CLICKHOUSE_DATABASE", "activity"),
        "table": pick("CLICKHOUSE_TABLE", "events"),
    }


def check(env=None, env_file: str = DEFAULT_ENV_FILE, now=None,
          timeout: float = DEFAULT_TIMEOUT, fetch=None,
          presence=PRESENCE_SOURCES, **kw) -> dict:
    """Full live check. Returns a verdict dict; NEVER raises.

    `fetch(url, user, password, query, timeout) -> str` is injectable so the
    whole path (including every failure branch) is testable offline.

    🔴 `presence` is threaded through ALL THREE stages that consume it — the
    query, the parse and the evaluation — because it was once an explicit
    parameter on the last two only, which made `check(presence=X)` a SILENT
    NO-OP: the SQL was built from the module default, so the server computed the
    default's presence column, and the parser's own copy never fired because live
    rows are 5-wide. A maintainer previewing the effect of adding a source got an
    unchanged answer and no error. A parameter that quietly does nothing is worse
    than no parameter, so this one either works everywhere or the call fails.

    🔴 The forward to `evaluate` is DEFENCE IN DEPTH and is currently MOOT, said
    plainly because a mutant deleting it survives every behavioural test: by then
    `parse_buckets` has applied the allowlist and every row is 5-wide, so
    evaluate's own membership test cannot fire, and an empty allowlist has already
    been refused by `bucket_query`. It is forwarded anyway so the three stages
    cannot disagree about what "presence" means, and it is pinned by a structural
    test that MEASURES the equivalence and fails the day it stops holding
    (test_check_forwards_presence_to_evaluate_even_though_it_is_currently_MOOT).
    """
    empty = {"rows": 0, "evaluated": 0, "dead": [], "count": 0, "sources": [],
             "presence_stalled": [], "newest_event_age_minutes": None}
    cfg = resolve_config(env=env, env_file=env_file)
    if not cfg["url"]:
        return dict(empty, state=STATE_NOT_CONFIGURED,
                    detail="no CLICKHOUSE_URL — telemetry not configured here")
    try:
        query = bucket_query(database=cfg["database"], table=cfg["table"],
                             presence=presence)
    except ValueError as e:
        return dict(empty, state=STATE_MISCONFIGURED,
                    detail="cannot build the query: %s" % str(e)[:160])
    fetch = fetch or fetch_buckets
    try:
        body = fetch(cfg["url"], cfg["user"], cfg["password"], query, timeout)
    except Exception as e:  # noqa: BLE001 — unreachable must not look healthy
        return dict(empty, state=STATE_UNREACHABLE,
                    detail="ClickHouse unreachable: %s" % str(e)[:160])
    try:
        rows = parse_buckets(body, presence=presence)
    except Exception as e:  # noqa: BLE001
        return dict(empty, state=STATE_QUERY_FAILED,
                    detail="unparseable ClickHouse response: %s" % str(e)[:160])
    return evaluate(rows, now=now, presence=presence, **kw)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _render_text(v: dict) -> str:
    lines = ["state: %s   rows=%d evaluated=%d dead=%d"
             % (v["state"], v.get("rows", 0), v.get("evaluated", 0),
                v.get("count", 0)),
             "detail: %s" % v.get("detail", "")]
    if v.get("newest_event_age_minutes") is not None:
        lines.append("newest event: %d min ago (informational, and GLOBAL across "
                     "hosts+sources — it cannot see a per-host presence "
                     "blackout; see module docstring)"
                     % v["newest_event_age_minutes"])
    for s in v.get("presence_stalled") or []:
        lines.append("PRESENCE STALLED: %s — %s; every source on it is "
                     "unjudgeable"
                     % (s["host"],
                        "no human-driven events in the window at all"
                        if s["stall_hours"] is None
                        else "human timeline frozen %.1fh before its newest row"
                             % s["stall_hours"]))
    if v.get("sources"):
        lines.append("")
        lines.append("%-10s %-15s %8s %8s %9s %9s  %s"
                     % ("host", "source", "baseline", "p99gap", "budget_h",
                        "silent_h", "verdict"))
        for s in v["sources"]:
            lines.append("%-10s %-15s %8d %8s %9s %9s  %s"
                         % (s["host"], s["source"], s["baseline_buckets"],
                            "-" if s["gap_p"] is None else "%.0f" % s["gap_p"],
                            "-" if s["budget_active_hours"] is None
                            else "%.1f" % s["budget_active_hours"],
                            "%.1f" % s["silent_active_hours"],
                            "DEAD" if s["dead"] else
                            ("skip:%s" % s["reason"] if not s["evaluated"] else "ok")))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="activity-telemetry deadman check")
    ap.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    ap.add_argument("--tsv", metavar="FILE",
                    help="evaluate this captured/seeded bucket TSV instead of "
                         "querying ClickHouse (the control harness)")
    ap.add_argument("--now", type=int, help="override 'now' (unix seconds)")
    ap.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    ap.add_argument("--print-query", action="store_true",
                    help="print the SQL and exit")
    args = ap.parse_args(argv)

    if args.print_query:
        sys.stdout.write(bucket_query() + "\n")
        return 0

    if args.tsv:
        try:
            with open(args.tsv) as f:
                rows = parse_buckets(f.read())
            verdict = evaluate(rows, now=args.now)
        except Exception as e:  # noqa: BLE001
            verdict = {"state": STATE_QUERY_FAILED, "rows": 0, "evaluated": 0,
                       "dead": [], "count": 0, "sources": [],
                       "presence_stalled": [],
                       "newest_event_age_minutes": None,
                       "detail": "unreadable TSV: %s" % str(e)[:160]}
    else:
        verdict = check(env_file=args.env_file, now=args.now)

    sys.stdout.write((json.dumps(verdict, indent=2) if args.json
                      else _render_text(verdict)) + "\n")
    if verdict["state"] in UNKNOWN_STATES:
        return 2
    return 1 if verdict["count"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("deadman: %s\n" % e)
        sys.exit(2)
