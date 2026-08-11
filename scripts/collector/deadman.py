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

🔴 THE COST OF THE PRESENCE ALLOWLIST — the blind spot is WIDER than it was.
Active time now advances only while a PRESENCE_SOURCES member emits, so the
blackout above no longer needs every source to die: losing ALL FIVE presence
sources at once is enough. An X-session crash is the realistic shape (it takes
`keys` and `i3` together, and `tmux`/`zsh` with the terminals) — after it, active
buckets stop accruing, every silence measures 0, and the check goes QUIET instead
of alarming. Under the old any-source rule an agent still emitting would have
kept the timeline moving and the presence sources would have alarmed. That trade
is deliberate: the alarms it removes were false (the operator was asleep), and
the alarm it gives up is one that only fires when the operator's whole desk is
gone — which `newest_event_age_minutes` shows.
Losing ONE presence source is still caught: the other four keep the timeline
advancing, which is the whole motivating case (workbench/keys, below).
Budgets are DENOMINATED in active buckets, so a shorter active timeline moves
BOTH the budget and the measured silence, and the net goes in different
directions for different sources. MEASURED 2026-08-11, both directions:
  MORE sensitive, for a source pinned on FLOOR_BUCKETS -- the floor is a fixed
  24 active buckets, so it now spans fewer WALL hours. laptop/opencode gained a
  dead-hour in the 97-point sweep for exactly this reason (0 -> 1).
  LESS sensitive, for an on-demand source whose budget scales with the same
  shrinking timeline. Seeding a 72h death on workbench/tool: the old rule
  measured 23.6 silent active hours against a 21.7h budget (DEAD); the allowlist
  measures 18.2 against 19.4 (not yet). Silence shrank faster than the budget
  because the buckets removed were disproportionately the overnight agent-only
  ones. It is a DELAY, not a blind spot -- the same seeded death at 120h is
  caught by both rules. Every other pair tested agreed at both 24h and 72h.
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
  ok              the table was read and at least one pair was evaluated

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
# MEASURED 2026-08-11, sweeping the evaluation point hourly over 97 points across
# the live 14-day table, denylist rule vs this allowlist (dead-hours per pair):
#   workbench/keys            6 -> 0      workbench/tmux            7 -> 0
#   workbench/browser-bridge 15 -> 2      laptop/browser-bridge    91 -> 88
#   laptop/claude            50 -> 50     laptop/tmux               3 -> 3
#   laptop/opencode           0 -> 1   (see the docstring's cost section: the
#                                       allowlist is not uniformly quieter)
# Positive control, same run: seeding a real death by dropping a pair's last 24h
# is still caught under the allowlist for all seven pairs tested, with no
# disagreement against the denylist rule. At a 72h drop one pair disagrees
# (workbench/tool: caught by the denylist, delayed until ~120h by the
# allowlist) -- quantified in the docstring's COST section.
#
# 🔴 ADDING A SOURCE THAT A HUMAN DRIVES DIRECTLY MEANS ADDING IT HERE — and
# adding an agent-driven one means NOT adding it. The cost of getting the first
# case wrong is quiet (the timeline just advances more slowly); the cost of the
# second is the overnight false alarm above. The test that catches an agent
# source sneaking in is
# test_an_unattended_agent_at_night_does_not_make_human_sources_look_dead — it
# uses a day/night fixture, because a fixture with no idle time is structurally
# blind to this entire class of bug.
PRESENCE_SOURCES = frozenset({"keys", "i3", "tmux", "zsh", "browser"})

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
# It stays exported because scripts/agent-ops imports BOTH this tuple and
# `cadence_predicate_sql` for its telemetry-freshness panel, where the concept is
# genuinely different: that panel wants to exclude MACHINE-GENERATED rows while
# still counting `claude`/`tool` as real USAGE of those sources. "Is this row
# machine-generated?" and "does this row prove a human is at the desk?" are
# different questions, and only the second one defines active time.
# Seam tests: scripts/tests/test_agent_ops.py.
#
# 🔴 ADDING A TIMER-DRIVEN EMITTER ANYWHERE IN THE PIPELINE MEANS ADDING IT HERE
# (for agent-ops' freshness panel) — and, separately, keeping it OUT of
# PRESENCE_SOURCES.
MACHINE_CADENCE = (("browser-bridge", "heartbeat"),)

DEFAULT_ENV_FILE = "~/.config/activity-collector/env"
DEFAULT_TIMEOUT = 8.0

STATE_OK = "ok"
STATE_NO_DATA = "no-data"
STATE_UNREACHABLE = "unreachable"
STATE_QUERY_FAILED = "query-failed"
STATE_NOT_CONFIGURED = "not-configured"

# Everything that is NOT a measured verdict. The whole point of the module is
# that these are distinguishable from `ok`, so they are enumerated once here and
# every consumer asks this set rather than re-deriving the list.
UNKNOWN_STATES = frozenset(
    {STATE_NO_DATA, STATE_UNREACHABLE, STATE_QUERY_FAILED, STATE_NOT_CONFIGURED}
)


# --------------------------------------------------------------------------- #
# Query
# --------------------------------------------------------------------------- #
def cadence_predicate_sql(cadence=MACHINE_CADENCE) -> str:
    """A ClickHouse boolean matching any MACHINE_CADENCE pair; "" when empty.

    🔴 THE ONE PLACE this predicate is RENDERED, not just the one place the pairs
    are declared. Exported because scripts/agent-ops needs the same predicate for
    its freshness panel: a second copy of the `" OR ".join` immediately grew the
    same latent bug (joining with AND makes `NOT (A AND B)` exclude NOTHING),
    which is only reachable once a second pair exists -- i.e. it would have gone
    unnoticed until exactly the moment MACHINE_CADENCE's own note tells a
    maintainer to add one. Single-sourcing the DATA was not enough.
    """
    return " OR ".join("(source = '%s' AND kind = '%s')" % (s, k)
                       for s, k in cadence)


def presence_predicate_sql(presence=PRESENCE_SOURCES) -> str:
    """A ClickHouse boolean matching any PRESENCE_SOURCES member; "" when empty.

    Sorted so the rendered SQL is stable (PRESENCE_SOURCES is a set) and so a
    test can pin the literal string.
    """
    if not presence:
        return ""
    return "source IN (%s)" % ", ".join("'%s'" % s for s in sorted(presence))


def presence_count_expr(presence=PRESENCE_SOURCES) -> str:
    """`countIf(...)` counting only HUMAN-PRESENCE events in the bucket.

    Built from PRESENCE_SOURCES so the SQL and the Python cannot drift apart.
    An empty allowlist renders the constant 0 -- nothing is presence, so no
    bucket is active. That is the QUIET degradation (nothing can ever be judged
    dead), never the loud one: an empty allowlist must not silently fall back to
    counting everything, which is the denylist behaviour this replaced.
    """
    pred = presence_predicate_sql(presence)
    if not pred:
        return "0"
    return "countIf(%s)" % pred


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
    """
    return (
        "SELECT host, source, "
        "toUnixTimestamp(toStartOfInterval(ts, INTERVAL %d SECOND)) AS b, "
        "count() AS n, %s AS n_presence "
        "FROM %s.%s WHERE ts > now() - INTERVAL %d DAY "
        "GROUP BY host, source, b ORDER BY host, source, b FORMAT TSV"
        % (int(bucket_seconds), presence_count_expr(presence), database, table,
           int(days))
    )


def parse_buckets(text: str, presence=PRESENCE_SOURCES):
    """TSV -> [(host, source, bucket_epoch, n, n_presence)]. ValueError on junk.

    Accepts BOTH widths, and the width is decided by the FIRST non-empty line and
    then enforced for the whole file:
      5 fields  the current query (n_presence = human-presence events in the
                bucket, computed server-side from PRESENCE_SOURCES).
      4 fields  a legacy capture or a hand-written fixture, which carries no
                presence column -> it is RECONSTRUCTED here as
                `n if source in PRESENCE_SOURCES else 0`.

    🔴 That reconstruction is exact, not a guess, and that is why it is the
    default. Presence is a property of the SOURCE alone, and the source column is
    present in a 4-field row — unlike the previous machine-cadence rule, which
    also needed `kind` and therefore genuinely could not be recovered. The two
    alternatives are both wrong: carrying `n` across (the old behaviour) reinstates
    "every agent row means the human is here" on every replayed capture, i.e. the
    exact bug this module was changed to remove; hard-zeroing the column would
    make a replayed capture unable to judge anything at all and report a
    reassuring "0 dead". Deriving from the source name fails safe in both
    directions because it reproduces what the live query would have computed.

    A file may not MIX the two widths: a width that changes mid-file is a format
    change or a corrupted capture, and inferring per-line would be the silent-drop
    failure this parser exists to avoid.

    Strict on purpose otherwise. Silently dropping unparseable lines would let a
    changed output format read as "fewer rows" and, at the limit, as "no
    staleness" -- the exact way a parsed-output harness lies (RULES.md: a tool's
    output format is a dependency you did not pin).
    """
    rows = []
    width = None
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if width is None:
            if len(parts) not in (4, 5):
                raise ValueError("line %d: expected 4 or 5 tab-separated fields, "
                                 "got %d" % (lineno, len(parts)))
            width = len(parts)
        elif len(parts) != width:
            raise ValueError("line %d: field count changed mid-file (%d, expected "
                             "%d) — refusing to guess" % (lineno, len(parts), width))
        host, source, b, n = parts[:4]
        if width == 5:
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
             presence=PRESENCE_SOURCES) -> dict:
    """Pure: bucket rows -> verdict dict. No I/O, no clock unless `now` is None.

    Returns {"state", "rows", "evaluated", "dead", "count", "sources",
             "newest_event_age_minutes"}.
    `state` is STATE_OK only when rows came back AND at least one pair was
    actually measured -- see the module docstring on why the zero needs that.
    """
    now = int(time.time()) if now is None else int(now)
    if not rows:
        return {"state": STATE_NO_DATA, "rows": 0, "evaluated": 0,
                "dead": [], "count": 0, "sources": [],
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
    for row in rows:
        host, src, b, n = row[0], row[1], row[2], row[3]
        n_presence = row[4] if len(row) > 4 else (n if src in presence else 0)
        buckets_by_pair[(host, src)].add(b)
        if n_presence > 0:
            active_by_host[host].add(b)

    newest = max(r[2] for r in rows)

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
        if len(ev) < min_baseline:
            # Not enough history to have a normal. Never alarms — this is also
            # what makes a legitimately-absent source (workbench/browser: 0 rows)
            # silent rather than noisy: it is not in `rows` at all.
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
        sources.append(rec)

    evaluated = [s for s in sources if s["evaluated"]]
    dead = [s for s in evaluated if s["dead"]]
    if not evaluated:
        return {"state": STATE_NO_DATA, "rows": len(rows), "evaluated": 0,
                "dead": [], "count": 0, "sources": sources,
                "newest_event_age_minutes": max(0, (now - newest) // 60),
                "detail": "no (host, source) pair cleared the baseline — cannot tell"}

    return {
        "state": STATE_OK,
        "rows": len(rows),
        "evaluated": len(evaluated),
        "dead": dead,
        "count": len(dead),
        "sources": sources,
        "newest_event_age_minutes": max(0, (now - newest) // 60),
        "detail": describe(dead, len(evaluated)),
    }


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
          timeout: float = DEFAULT_TIMEOUT, fetch=None, **kw) -> dict:
    """Full live check. Returns a verdict dict; NEVER raises.

    `fetch(url, user, password, query, timeout) -> str` is injectable so the
    whole path (including every failure branch) is testable offline.
    """
    cfg = resolve_config(env=env, env_file=env_file)
    if not cfg["url"]:
        return {"state": STATE_NOT_CONFIGURED, "rows": 0, "evaluated": 0,
                "dead": [], "count": 0, "sources": [],
                "newest_event_age_minutes": None,
                "detail": "no CLICKHOUSE_URL — telemetry not configured here"}
    query = bucket_query(database=cfg["database"], table=cfg["table"])
    fetch = fetch or fetch_buckets
    try:
        body = fetch(cfg["url"], cfg["user"], cfg["password"], query, timeout)
    except Exception as e:  # noqa: BLE001 — unreachable must not look healthy
        return {"state": STATE_UNREACHABLE, "rows": 0, "evaluated": 0,
                "dead": [], "count": 0, "sources": [],
                "newest_event_age_minutes": None,
                "detail": "ClickHouse unreachable: %s" % str(e)[:160]}
    try:
        rows = parse_buckets(body)
    except Exception as e:  # noqa: BLE001
        return {"state": STATE_QUERY_FAILED, "rows": 0, "evaluated": 0,
                "dead": [], "count": 0, "sources": [],
                "newest_event_age_minutes": None,
                "detail": "unparseable ClickHouse response: %s" % str(e)[:160]}
    return evaluate(rows, now=now, **kw)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _render_text(v: dict) -> str:
    lines = ["state: %s   rows=%d evaluated=%d dead=%d"
             % (v["state"], v.get("rows", 0), v.get("evaluated", 0),
                v.get("count", 0)),
             "detail: %s" % v.get("detail", "")]
    if v.get("newest_event_age_minutes") is not None:
        lines.append("newest event: %d min ago (informational — a total blackout "
                     "is NOT an alarm, see module docstring)"
                     % v["newest_event_age_minutes"])
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
