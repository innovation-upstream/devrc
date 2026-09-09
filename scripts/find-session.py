#!/usr/bin/env python3
"""Find past Claude Code sessions by keyword.

Searches ~/.claude/projects/**/*.jsonl (one file per session, named <sessionId>.jsonl)
for query terms across user-typed and assistant text, ranks matches by relevance +
recency, and prints each hit with its project, date, branch, genesis message, and the
best matching snippets — plus how to resume it.

The walk, the ranking and the snippet extraction live in `scripts/lib/transcript_search.py`
and are shared with `scripts/check-clickup-addressed/`. This file is the CLI only.

🔴 `--live` INVERTS THE INSTRUMENT, AND THE MEASUREMENT IS WHY
--------------------------------------------------------------------------
Re-measured 2026-09-08 on this host, term `find-session`: the UNWINDOWED
transcript-archive walk takes **42.96 s cold / 14.26 s warm**, against a LIVE
cross-host tmux scan of **1.10 s / 1.21 s** over two runs. Corpus at that
measurement: **924** local session transcripts (5,954 `*.jsonl` on disk, but
~5,030 are `subagents/agent-*.jsonl`, which the walk excludes by name), plus a
2.47 GB opencode DB, on each of two hosts.

The 2026-08-28 figures this docstring used to carry were 30.1 s and 1.82 s. Both
moved, and so did the RATIO. The numbers are dated on purpose: the argument for
live-first is the ratio, and it only ever gets stronger — the corpus grows and
the live fleet does not. Re-measure rather than quoting these.
The live rows already carry `task`, `label`, `hotkey`, `status`,
`waiting_probable`, `path` and `claude_session_id`.

🔴 AND THE ARCHIVE LEG IS NOW WINDOWED BY DEFAULT — see `DEFAULT_SINCE_DAYS`.
A bounded search that does not SAY it is bounded reads as an exhaustive one, so
the window is printed on every archive run, with the number of transcripts it
skipped unopened.

So for the question people actually ask — *"find that thing I lost track of, is
it still running, which window, where did it leave off"* — the archive walk is
the WRONG instrument whatever it costs: it answers a question about the past over a
corpus that cannot say whether anything is running now. `--live` runs the live
scan FIRST and falls back to the archive only when the live fleet matched
nothing (or `--deep` forces both).

`--tail N` closes the loop: it prints the scrollback of the resolved window, so
one call answers "where did it leave off" too. It REFUSES on an ambiguous match
rather than picking one: ambiguity is refused, not guessed.

🔴 AN UNREACHABLE HOST IS NEVER RENDERED AS "NOT RUNNING". Same rule the opencode
leg already follows: if the live scan fails, or a host did not answer, that is
said out loud and the empty LIVE section is labelled UNMEASURED. The one sentence
this tool must never emit is "it is not running" off a look that never happened.

Usage:
  find-session.py <term> [<term> ...] [--skill NAME] [--project SUBSTR]
                  [--since YYYY-MM-DD | --all-time] [--limit N] [--any] [--all]
                  [--claude-only | --opencode-only] [--json]
                  [--live [--deep] [--tail N]]

  Terms are ANDed by default (a session must match all). Pass --any to OR them.
  Quote a multi-word term to match it as a phrase: find-session.py "pr 235"

Examples:
  find-session.py redis vpn            # both redis AND vpn, last 12 days
  find-session.py "pr 235" --all-time  # the whole corpus, ~15s warm
  find-session.py minio --project talos --since 2026-05-01
  find-session.py widget-cache --live            # live first, archive fallback
  find-session.py widget-cache --live --tail 60  # ...and where it left off
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from transcript_search import (  # noqa: E402
    DEFAULT_ROOT, SURFACE_ALL, SURFACE_TEXT, canonical_skill_name, search,
    search_peers,
)
from opencode_search import search_opencode  # noqa: E402

# Reassigned by tests to point at a tmp corpus. Read at CALL time, never captured.
ROOT = DEFAULT_ROOT

# --------------------------------------------------------------------------- #
# THE LIVE LEG — one subprocess, and the MATCHING happens on the other side
# --------------------------------------------------------------------------- #
# 🔴 THE MATCH PREDICATE IS NOT RE-IMPLEMENTED HERE. `session-manager --match`
# owns it (`MATCH_FIELDS` / `row_matches` in that file), and this passes the
# terms through. A second copy here would be the one-rule-one-place failure with
# the worst possible symptom: the two tools would answer the same words with
# different sets and neither would say so. It also means the field list this
# prints comes from the payload (`filters.match_fields`), not from a literal
# that can drift away from what was actually searched.
SESSION_MANAGER = str(Path(__file__).resolve().parent / "session-manager")

# The live scan measures ~1.1 s (2026-09-08; it was 1.82 s on 2026-08-28 —
# the figure this file's docstring retires). The ceiling is generous because it makes an
# ssh round trip to the peer host; a timeout is reported as a FAILED scan, never
# as an empty fleet.
LIVE_TIMEOUT_SECS = 90

# --------------------------------------------------------------------------- #
# 🔴 THE DEFAULT ARCHIVE WINDOW — A BOUND THAT MUST NEVER BE SILENT
# --------------------------------------------------------------------------- #
# `search()` already carries an mtime PREFILTER for `--since`: a transcript
# whose mtime precedes the cutoff cannot pass and is skipped WITHOUT being
# opened. It was opt-in, so the default walk read the whole corpus to EOF on
# every query.
#
# ⚠ EVERY COUNT BELOW IS A DATED SNAPSHOT OF A GROWING CORPUS — re-measure
# rather than quoting it. The walked set was 924 on 2026-09-08 and passed 930
# within hours; the RATIOS are what the argument rests on, not the totals.
# 🔴 THE DENOMINATOR IS THE WALKED SET, NOT 5,954, AND AN EARLIER DRAFT GOT
# IT WRONG. `find ~/.claude/projects -name '*.jsonl'` counts 5,954 — but ~5,030
# of those are `<project>/<id>/subagents/agent-*.jsonl`, which `iter_transcripts`
# excludes BY NAME (a subagent transcript is not a resumable session). The walked
# set is 924. Every ratio below is quoted against the counter the WALK publishes
# (`stats["skipped_stale"]`), not against a `find` this code never performs.
#
# Measured 2026-09-08, warm cache, `--claude-only`, back to back:
#
#     --since  3 days   2.40 s     skipped 780 of 924     5.5x
#     --since 12 days   7.35 s     skipped 467 of 924     1.8x   <- the default
#     --since 30 days  13.35 s     skipped   0 of 924     1.0x
#     --all-time       13.09 s
#
# 🔴 A 30-DAY DEFAULT WOULD BE A NO-OP, AND THE COUNTER SAYS SO OUTRIGHT: it
# skips ZERO of 924 files, because nothing in this corpus is older than that.
# It would have capped the answer while buying nothing — the worst of both. 12
# days skips about half and is the operator's chosen point on that curve.
#
# 🔴 AND THAT ZERO IS STRUCTURAL, NOT THIS WEEK'S LUCK — which is what makes the
# argument outlive the measurement. The oldest walked transcript sits at a hard
# floor across many project dirs, because Claude Code PRUNES `~/.claude/projects`
# on a 30-day retention and no `cleanupPeriodDays` override is set. So the Claude
# corpus can never be deeper than ~30 days and any default at or above that is
# inert BY CONSTRUCTION. ⚠ It does not generalise to the OTHER corpus: the
# opencode store has no such pruning and reaches back much further (see
# `window_notice`), which is exactly why its cut is the larger one and why the
# notice must name it as unmeasured rather than imply the printed count is all.
#
# Whole-tool, both corpora: 7.67 / 7.99 s windowed against 14.26 / 17.06 s under
# `--all-time` on a warm cache, and 42.96 s on a COLD one. The user-facing
# strings quote the warm figure with the cold one named, because a number that
# only holds on a cold cache reads as the normal case and is not.
#
# 🔴 A BOUND NOBODY IS TOLD ABOUT IS THE SILENT CAP `claude/RULES.md` NAMES. The
# window is therefore printed on EVERY archive run, with the number of
# transcripts it skipped unopened — a measured size for the cap, not a claim
# that one was applied — and `--all-time` turns it off.
DEFAULT_SINCE_DAYS = 12

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_AMBIGUOUS = 3      # --tail could not resolve to exactly one window
EXIT_UNAVAILABLE = 4    # --tail only: something the tail needed was not measured

# --------------------------------------------------------------------------- #
# 🔴 THE EXIT CONTRACT, AS DATA — because the shipped doc got it wrong TWICE
# --------------------------------------------------------------------------- #
# `claude/skills/find-session/SKILL.md` is what an agent reads before branching
# on an exit code, and it shipped two false claims at once:
#
#   * "3 = --tail could not resolve to one window on a FULLY MEASURED fleet".
#     False. Two live matches with a host DOWN also exits 3, while the run
#     itself prints "this candidate list is INCOMPLETE". A wrapper reading
#     `rc == 3 => here are all the candidates` reports a complete list under
#     this fleet's documented common degraded state — the "real but wrong
#     window" failure this whole change exists to prevent, relocated to the
#     caller.
#   * "4 = the live scan failed or no host answered", with no `--tail`
#     qualifier. False. EVERY source of this code is inside the tail path;
#     without `--tail` a failed scan exits 0, because the LIVE section says so
#     in prose and the archive still ran.
#
# So the table is DATA here, one sentence per code, and a test pins the shipped
# doc against it AND pins each sentence against the behaviour it describes.
# Correcting the prose alone would have left the next drift undetected — which
# is what happened between the last two rounds.
#
# 🔴 THE SENTENCES SAY WHAT THE CODE MEANS, NOT WHAT THE CALLER SHOULD WANT.
# `3` and `4` are BOTH `--tail`-only, and neither carries any claim about fleet
# coverage: read `tail.coverage_complete` for that.
EXIT_CONTRACT = (
    (EXIT_OK, "the run completed. NOT a claim that anything matched — an empty "
              "LIVE section and an empty ARCHIVE section both exit 0. 🔴 NOR a "
              "claim about coverage: a `--tail` that resolved to ONE window "
              "exits 0 even when a host did not answer, so another window may "
              "match on the host that was never asked. This is the code a "
              "caller ACTS on — read `tail.coverage_complete` before treating "
              "the resolution as unique."),
    # 🔴 THE ENUMERATION IS THE CLAIM, AND IT WENT STALE THE FIRST TIME ANYONE
    # ADDED A USAGE ERROR. `main` had NINE `return EXIT_USAGE` sites when this
    # block was written and has more now — the live number is
    # `EXIT_USAGE_SITE_COUNT` in the contract test, which is asserted, and the
    # collector behind it is module-wide rather than `main`-scoped. Do not
    # restate either here: this very comment shipped a stale count one round
    # after the round that added the ratchet, inside a block whose whole
    # subject is enumerations going stale. This
    # sentence named five causes covering six of them, and the gate that
    # rebuilds it from a ledger only checks sentence -> code ("every cause named
    # is really an exit 2"), never code -> sentence. Its own comment says so.
    # So the two errors THIS change added and one that predates it were absent
    # from the table an agent is told to branch on, and the suite stayed green.
    # `test_every_EXIT_USAGE_SITE_is_a_cause_the_sentence_NAMES` closes the
    # other direction by TRACING each probe to the line it returns from.
    (EXIT_USAGE, "bad arguments: `--tail` without `--live`, `--tail` below 1, "
                 "`--limit` below 1, an unparseable `--since`, `--since` "
                 "together with `--all-time` (they name two different "
                 "windows), `--live` with no search terms (it matches a "
                 "window's task/label/codename, so `--skill` alone is an "
                 "ARCHIVE query), a query that names nothing (no terms and no "
                 "`--skill`, or a `--skill` that canonicalises to empty), "
                 "`--claude-only` with `--opencode-only` (between them they "
                 "search no corpus at all), `--skill` with `--opencode-only` — "
                 "that corpus carries no skill attribution, so the combination "
                 "has no answer rather than an empty one, or a malformed "
                 "command line rejected by argparse ITSELF before `main` runs "
                 "(an unknown flag, or a non-integer `--limit`/`--tail`), which "
                 "exits 2 from inside argparse and is the one cause this "
                 "module never returns."),
    (EXIT_AMBIGUOUS, "`--tail` ONLY: it could not resolve to exactly one live "
                     "window — several matched, or none did on a fleet where "
                     "every host answered. It carries NO claim about coverage; "
                     "the candidate list may be incomplete, and "
                     "`tail.coverage_complete` is the field that says so."),
    (EXIT_UNAVAILABLE, "`--tail` ONLY: something the tail needed was NOT "
                       "measured — the live scan failed or no host answered, "
                       "or `session-manager tail` itself failed (rc 2/4/5), or "
                       "nothing matched while a host was unreachable. Without "
                       "`--tail` a failed scan still exits 0 and says so in the "
                       "LIVE section."),
)


# --------------------------------------------------------------------------- #
# THE WINDOW, RESOLVED IN ONE PLACE AND DESCRIBED FROM THE SAME VALUE
# --------------------------------------------------------------------------- #
# 🔴 The cutoff and the sentence describing it are produced by ONE function, so
# the printed window can never name a cutoff the search did not use — DATE *and* time-of-day, via `window_stamp`. That is the
# whole failure mode of a silent cap: not that the bound exists, but that the
# output describes a wider search than the one that ran.
WINDOW_DEFAULT = "default"        # nothing asked; DEFAULT_SINCE_DAYS applied
WINDOW_EXPLICIT = "explicit"      # --since
WINDOW_ALL_TIME = "all-time"      # --all-time
WINDOW_SKILL_EXEMPT = "skill-exempt"   # --skill, unwindowed on purpose


def resolve_window(a, now=None):
    """`(since_or_None, source)` — the ONE place the archive cutoff is decided.

    `now` is injectable so a test pins the arithmetic instead of re-deriving it.
    Midnight-anchored, so the default window is a whole number of days and has
    the same shape as an explicit `--since YYYY-MM-DD`, which is the only form
    a caller can spell.

    🔴 `--skill` IS EXEMPT — see `DEFAULT_SINCE_DAYS`. An EXPLICIT `--since` is
    still honoured alongside `--skill`: the exemption removes a default nobody
    asked for, it does not override an instruction somebody gave.
    """
    if a.since:
        return datetime.fromisoformat(a.since), WINDOW_EXPLICIT
    if getattr(a, "all_time", False):
        return None, WINDOW_ALL_TIME
    if getattr(a, "skill", ""):
        return None, WINDOW_SKILL_EXEMPT
    base = now or datetime.now()
    cutoff = (base - timedelta(days=DEFAULT_SINCE_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return cutoff, WINDOW_DEFAULT


def window_stamp(since):
    """The ONE spelling of a cutoff — human notice and JSON field alike.

    🔴 FIXED IN TWO PLACES AND THEN ONLY ONE. A `--since` carrying a time
    (`--since 2026-09-01T18:30:00`) parses, and the walk uses the whole
    timestamp; both the notice and `archive.window.since` printed the bare
    DATE, naming a window up to a day WIDER than the one that ran — the single
    direction a disclosure must never err in. The previous round fixed the
    human string and left the machine-readable field behind, so one object then
    carried `since: "2026-09-01"` beside `message: "… since 2026-09-01
    18:30:00"`: two fields of one window disagreeing, which is worse than the
    bug it half-fixed. There is now one function and both call it.
    """
    if since is None:
        return None
    return (since.date().isoformat() if since.time() == time.min
            else since.isoformat(sep=" "))


def unmeasured_legs(a):
    """The windowed legs the printed count does NOT cover — and only those.

    🔴 A LEG THAT DID NOT RUN IS NOT AN UNCOUNTED LEG. The previous round
    appended "the opencode corpus and the peer hosts were windowed too" to
    every notice unconditionally, which is false exactly where it is loudest:
    under `--opencode-only` the opencode corpus is the ONLY thing searched and
    no peer is contacted, so the run told the operator that the corpus it just
    searched was excluded from the run. Under `--claude-only` it asserted an
    unreported cut in a corpus nobody opened. Derived from the same conditions
    `archive_search` branches on, so the two cannot drift.
    """
    legs = []
    if not a.opencode_only:
        legs.append("the peer hosts")
    if not a.claude_only and not a.opencode_only and not a.skill:
        legs.append("the opencode corpus")
    if a.opencode_only:
        legs.append("the opencode corpus")
    return tuple(legs)


def window_notice(since, source, skipped=None, examined=None, legs=(),
                  claude_leg_ran=True):
    """The line that stops a BOUNDED archive search reading as an exhaustive one.

    🔴 It states the cap AND its measured size. `skipped`/`examined` come from
    `search()`'s own counters — the same predicate that did the skipping — so
    this is a measurement, not a second implementation of the prefilter. When
    they were not measured (the caller replaced `archive_search`, or no local
    walk ran) it says so rather than printing a reassuring zero: a `0 skipped`
    from a counter wired to nothing is indistinguishable from a real one.
    """
    if source == WINDOW_ALL_TIME:
        return "ARCHIVE window: the WHOLE corpus (--all-time) — nothing was cut."
    if source == WINDOW_SKILL_EXEMPT:
        return ("ARCHIVE window: the WHOLE corpus — the "
                f"{DEFAULT_SINCE_DAYS}-day default is NOT applied to --skill, "
                "because 'has skill X ever been used' is a historical question "
                "and adoption-scan reads this answer. Pass --since to narrow.")
    stamp = window_stamp(since)
    if source == WINDOW_EXPLICIT:
        head = f"ARCHIVE window: since {stamp} (--since)."
    else:
        head = (f"ARCHIVE window: the last {DEFAULT_SINCE_DAYS} days (since "
                f"{stamp}) — DEFAULT, not a corpus-wide search. Anything older "
                "was NOT looked at; pass --all-time for the whole corpus "
                "(~15s warm, ~43s cold) or --since YYYY-MM-DD for a "
                "different window.")
    # 🔴 THE COUNT IS ONE LEG OF THREE, AND IT MUST SAY SO. `ARCHIVE_STATS` is
    # filled only by the LOCAL Claude walk; `search_peers` and `search_opencode`
    # are windowed by the same `since` and counted by nothing. Measured
    # 2026-09-08 on the live stores (a snapshot; the denominator grows hourly):
#           the local Claude corpus loses 466 of ~926
    # (50%) to the 12-day default, while the local OPENCODE corpus loses 487 of
    # 707 (69%) and appears in no number here — that store reaches back to
    # 2026-07-02, far deeper than the Claude corpus, whose oldest transcript is
    # a hard 30-day retention floor. So the unreported cut is both the larger
    # fraction and the longer reach.
    #
    # 🔴 AND "local" WAS THE WRONG WORD FOR IT. Everywhere else in this codebase
    # `local` means THIS HOST as opposed to a peer (`search_peers`,
    # `_query_db(label="local")`) — an opencode session on this machine IS a
    # local transcript, and was not in the count. Naming the corpus and the host
    # separately, and naming what is excluded, is the same correction this
    # change already made once to the DENOMINATOR: a partial measurement
    # presented without its scope reads as the whole thing.
    if not claude_leg_ran:
        counted = "the Claude corpus was NOT SEARCHED on this run"
    elif skipped is None:
        counted = ("Claude transcripts on THIS host skipped by the window: "
                   "NOT MEASURED")
    else:
        total = skipped + examined if examined is not None else None
        counted = ("Claude transcripts on THIS host skipped unopened: "
                   f"{skipped}" + (f" of {total}" if total is not None else ""))
    if legs:
        # 🔴 NO SUBJECT-VERB AGREEMENT TO GET WRONG. The first draft picked the
        # verb from the NUMBER OF LEGS rather than the grammatical number of the
        # phrase, and printed "the peer hosts was windowed too" whenever exactly
        # one leg was excluded — a sentence that reads as a bug in the tool. A
        # colon list agrees with nothing and cannot drift as legs are added.
        counted += " — also windowed, and NOT in this count: " + ", ".join(legs)
    return head + f" ({counted})"


def _default_run(argv, timeout=LIVE_TIMEOUT_SECS):
    """The ONE impure edge this file adds. Replaced wholesale by the tests."""
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# Reassigned by tests. Read at CALL time (`RUN(...)` resolves the module
# attribute), never captured as a default argument — the same reason
# `session-manager` resolves `ch_client_factory` at call time.
RUN = _default_run


def live_scan_argv(terms=()):
    """`session-manager scan --json --lean --no-ch [--match T]...`.

    `--lean` and `--no-ch` are not incidental: the lean view is the agent-shaped
    row projection (and the only one carrying `hotkey_display`), and ClickHouse
    answers session-history questions that this call is not asking.

    `sys.executable`, not the shebang: this must work from a nix devShell, a
    systemd unit and a bare cron alike, none of which agree about PATH.
    """
    argv = [sys.executable, SESSION_MANAGER, "scan", "--json", "--lean", "--no-ch"]
    for t in terms:
        argv += ["--match", str(t)]
    return argv


def live_scan(terms=()):
    """Run the live scan and return a STATUS-DISCRIMINATED result.

    `status` is `ok` / `unavailable` / `error`, and `rows` is only believable
    when it is `ok`:

      * `error`       — the subprocess did not run, or produced no JSON. Nothing
                        was measured.
      * `unavailable` — it ran and NO host answered. Still nothing measured; the
                        empty row list is not a fleet with no windows.
      * `ok`          — at least one host answered. `hosts_unreachable` may
                        still be non-empty, and a window living only on such a
                        host cannot appear in `rows` — which is why that list
                        travels with the verdict instead of being dropped.

    🔴 BOTH HOST LISTS ARE `None`, NEVER `[]`, ON THE `error` PATHS. The scan did
    not run, so "which hosts answered" was never measured — and an empty
    `hosts_unreachable` reads as the strongest possible claim, *every host
    answered*. It leaked straight into the payload as
    `archive.live_hosts_unreachable: []` for a scan that never happened, which is
    the null-never-`[]` rule this same change wrote into `payload-contract.md`.
    Fixed at the SOURCE rather than at the two publishers, so a third publisher
    cannot reintroduce it. `status == "unavailable"` is different: the scan DID
    run and every host really is unreachable, so both lists are real there.
    """
    out = {"status": "error", "rows": [], "hosts_reachable": None,
           "hosts_unreachable": None, "match_fields": None, "error": None,
           "rc": None, "terms": [str(t) for t in terms]}
    try:
        rc, stdout, stderr = RUN(live_scan_argv(terms))
    except Exception as e:  # noqa: BLE001 — a failed scan must degrade, not crash
        return dict(out, error=f"{type(e).__name__}: {e}")
    out["rc"] = rc
    try:
        report = json.loads(stdout)
    except Exception:  # noqa: BLE001
        detail = (stderr or stdout or "").strip()[:200]
        return dict(out, error=(f"session-manager exited {rc} and produced no "
                                f"JSON on stdout: {detail!r}"))
    # 🔴 VALID JSON IS NOT A REPORT. `json.loads("[]")` and `json.loads("null")`
    # both succeed and then `report.get` raises AttributeError OUTSIDE the try —
    # crashing a function whose entire contract is to return a status-
    # discriminated result rather than raise. A truncated pipe or a wrapper that
    # prints a bare array is enough.
    if not isinstance(report, dict):
        return dict(out, error=(f"session-manager exited {rc} and produced "
                                f"{type(report).__name__}, not a report "
                                "object, on stdout"))
    hosts = report.get("hosts") or {}
    # 🔴 SAME DEFECT, ONE LEVEL DOWN — and the guard above is what made it
    # invisible. Proving `report` is an object says nothing about `hosts`, so
    # this called `.items()` on whatever `hosts` was and `.get()` on each value.
    # MEASURED against this function, all three raising OUT of a function whose
    # entire contract is to return a status-discriminated result rather than
    # raise:
    #   {"hosts": [1, 2]}       -> AttributeError: 'list' has no 'items'
    #   {"hosts": {"wb": null}} -> AttributeError: 'NoneType' has no 'get'
    #   {"hosts": {"wb": []}}   -> AttributeError: 'list' has no 'get'
    # A session-manager version skew is enough to produce any of them, and the
    # crash surfaces as a traceback where the tool's whole purpose is to say
    # "the fleet was NOT measured". Discriminated at the SOURCE, like the two
    # host lists, so no publisher has to re-derive it.
    # 🔴 THE TWO SHAPES ARE REPORTED SEPARATELY, because one message for both
    # named the WRONG OPERAND: a bad host ENTRY reported "(got dict)" — true of
    # `hosts` and useless about the entry — which reads as self-contradictory in
    # 3 of the 4 shapes this guard exists for. The test now asserts the operand,
    # not just that the word "hosts" appears; a guard checked by a substring of
    # its own subject is satisfied by a wrong diagnostic.
    if not isinstance(hosts, dict):
        return dict(out, error=(f"session-manager exited {rc} and produced a "
                                f"report whose `hosts` is "
                                f"{type(hosts).__name__}, not an object"))
    bad = sorted(k for k, v in hosts.items() if not isinstance(v, dict))
    if bad:
        return dict(out, error=(
            f"session-manager exited {rc} and produced a report whose host "
            f"entr{'y' if len(bad) == 1 else 'ies'} "
            f"{', '.join(repr(k) for k in bad)} "
            f"{'is' if len(bad) == 1 else 'are'} not an object (got "
            + ", ".join(f"{k!r}={type(hosts[k]).__name__}" for k in bad) + ")"))
    out["hosts_reachable"] = sorted(k for k, v in hosts.items() if v.get("reachable"))
    out["hosts_unreachable"] = sorted(k for k, v in hosts.items()
                                      if not v.get("reachable"))
    out["rows"] = [r for name in sorted(hosts)
                   for r in (hosts[name].get("windows") or [])]
    out["match_fields"] = (report.get("filters") or {}).get("match_fields")
    # 🔴 The discriminant, and it is the whole reason this function exists: a
    # scan where no host answered has an empty `rows` that means NOTHING WAS
    # MEASURED. Reporting that as "not running" is the one claim this tool must
    # never make.
    out["status"] = "ok" if out["hosts_reachable"] else "unavailable"
    return out


def live_session_ids(res):
    """The set of `claude_session_id`s the live fleet is holding — or `None`.

    🔴 `None` MEANS UNMEASURED, and it is what stops an archive hit being
    labelled CLOSED on the strength of a scan that never ran. A caller must
    branch on it; `set()` is the measured "the fleet holds no session ids".

    ⚠ A NON-`None` RETURN IS NOT PROOF OF FULL COVERAGE. `status == "ok"` means
    at least ONE host answered, so this set can be built from a partial fleet.
    `live_coverage_complete` is the second half of the answer and a caller that
    wants to say CLOSED needs BOTH — see `live_state_of`.
    """
    if res.get("status") != "ok":
        return None
    return {r.get("claude_session_id") for r in res.get("rows") or []
            if r.get("claude_session_id")}


def live_coverage_complete(res):
    """Did EVERY host answer? Only then can a MISS mean anything.

    🔴 THE DEFECT THIS EXISTS FOR. `live_scan` sets `status: "ok"` when ANY host
    answers, and `hosts_unreachable` may be non-empty in that state. Off that
    set alone, every archive hit whose session lives on the DOWN host was
    stamped `CLOSED` — a confident "that session is finished" about a machine
    nobody talked to — with `live_ids_measured: true` and no warning printed.
    On this fleet the laptop is a secondary machine that is frequently asleep,
    so the partial fleet is the COMMON degraded state, not an exotic one.

    Per-host attribution is deliberately NOT attempted, and the reason is NOT
    "the hit's host is unknown" — an earlier draft of this docstring said that
    and it was wrong for half the corpus. A Claude transcript hit's host IS
    known: `~/.claude/projects` is read from LOCAL disk only, so every Claude hit
    came from this machine. What makes an inference from that unsound is
    `claude --resume`: a session recorded on one host can be resumed and running
    on the OTHER, so "this transcript is local, therefore its live window would
    be local" does not follow. (The opencode corpus is read from both hosts, so
    those hits genuinely carry no host.) Either way the coarse answer is the one
    that is true, and it is what this returns.
    """
    return res.get("status") == "ok" and not res.get("hosts_unreachable")


def live_coverage_state(res):
    """`True` / `False` / **`None`** — the PUBLISHABLE form of coverage.

    🔴 `live_coverage_complete` is a two-valued PREDICATE and is right for
    branching: on a scan that never ran, "was coverage complete?" is correctly
    False, because it certainly was not. But PUBLISHING that False says
    *measured, and incomplete* about a scan that produced no measurement at all
    — the exact shape removed from `hosts_unreachable` one field over, in the
    same commit that introduced this one.

    `None` for `status == "error"` only. `unavailable` is NOT unmeasured here:
    that scan RAN and every host was genuinely unreachable, so `False` is a real
    answer.
    """
    if res.get("status") == "error":
        return None
    return live_coverage_complete(res)


def live_state_of(session_id, live_ids, coverage_complete=True):
    """`LIVE` / `CLOSED` / `UNMEASURED` for one archive hit.

    🔴 A POSITIVE IS A MEASUREMENT WHATEVER THE COVERAGE; A NEGATIVE IS NOT.
    Finding the id on a host that answered proves the session is live, and no
    unreachable peer can make that false. Failing to find it proves nothing
    unless every host answered — so a miss under partial coverage is
    `UNMEASURED`, never `CLOSED`.

    `coverage_complete` defaults to True so the parameter cannot be forgotten
    into a silently WEAKER verdict; forgetting it yields the strict old
    behaviour, which is wrong loudly rather than wrong quietly.
    """
    if live_ids is None:
        return "UNMEASURED"
    if session_id in live_ids:
        return "LIVE"
    return "CLOSED" if coverage_complete else "UNMEASURED"


# --------------------------------------------------------------------------- #
# 🔴 THE ARCHIVE-ONLY FLAG LEDGER — A LIST, NOT A SENTENCE
# --------------------------------------------------------------------------- #
# An earlier revision hand-listed three flags here and closed the comment with
# "this diff already prints five notices of exactly this class; THESE ARE THE
# REST". That sentence was false when it was written: `--claude-only`,
# `--opencode-only` and `--all` were missing, and a completeness claim is worse
# than the omission it decorates, because it tells the next reader to stop
# looking. Measured on the live fleet:
#
#   $ find-session.py <term> --live --opencode-only
#   LIVE (3 matched; …)  ->  a tmux window running CLAUDE
#
# The caller selected the opencode corpus and got Claude windows — and because
# the live leg matched, `run_archive` stayed False, so the opencode corpus was
# never searched at all.
#
# So the set is DATA and a test pins it against the parser two-way: every
# argparse destination is either live-aware or in this ledger, and adding a flag
# without deciding which fails the suite. The sentence cannot drift from the
# list again because there is no sentence.
#
# `(dest, spelling, why)`.
ARCHIVE_ONLY_FLAGS = (
    ("any", "--any", "the live scan ANDs its terms; there is no OR mode"),
    ("project", "--project", "no cwd filter on the live leg — try `--match-path` "
                             "on session-manager"),
    ("since", "--since", "live rows carry an age, not a date"),
    ("all_time", "--all-time", "it lifts the ARCHIVE's default date window; "
                               "the live scan has no window to lift"),
    ("claude_only", "--claude-only", "CORPUS selection; live rows have a "
                                     "`runtime` but the scan has no corpus axis"),
    ("opencode_only", "--opencode-only", "CORPUS selection; same reason"),
    ("all", "--all", "widens the TRANSCRIPT search surface only"),
    ("skill", "--skill", "the live scan has NO skill-attribution axis — a "
                         "window's task/label/codename cannot say which skill "
                         "ran in it"),
)

# The destinations that DO reach the live leg (or steer both). Pinned beside the
# ledger so the two-way test has both halves of the partition in one place.
LIVE_AWARE_DESTS = frozenset({"terms", "live", "deep", "tail", "limit", "json"})

# Which archive-only flags additionally mean the archive result is the ONLY one
# that can answer the question — a corpus/surface selector. Named separately
# because their notice has to say more than "not filtered by them".
CORPUS_SELECTOR_DESTS = frozenset({"claude_only", "opencode_only", "all",
                                   "skill"})


def archive_only_notice(args):
    """The stderr line for archive-only flags passed alongside `--live`, or None.

    Derived from `ARCHIVE_ONLY_FLAGS`, so it cannot name a different set from the
    one the test pins.
    """
    named = [(dest, spelling, why) for dest, spelling, why in ARCHIVE_ONLY_FLAGS
             if getattr(args, dest, None)]
    if not named:
        return None
    line = ("(ARCHIVE-ONLY flags, ignored by the live scan: "
            + "; ".join(f"{spelling} ({why})" for _, spelling, why in named)
            + " — the LIVE section below is NOT filtered by them)")
    # 🔴 ...AND NOT WHEN `--deep` ALREADY FORCES THE ARCHIVE. The sentence exists
    # to say "the corpus you chose may go unsearched — pass --deep"; with
    # `--deep` already passed the archive DOES run, so it is advice to do the
    # thing the caller has done. That is the same "noise that trains the reader
    # to skip the line" this file refused to append to `--since`.
    if (any(dest in CORPUS_SELECTOR_DESTS for dest, _, _ in named)
            and not getattr(args, "deep", False)):
        # 🔴 The consequence, not just the fact. A corpus selector says WHICH
        # ARCHIVE to search, and the archive does not run at all when the live
        # leg matches — so the corpus the caller explicitly chose can go
        # unsearched while the run reports success.
        line += ("\n(...and a CORPUS selector only steers the ARCHIVE, which is "
                 "SKIPPED when the live scan matches: pass --deep to actually "
                 "search the corpus you selected)")
    return line


def fmt_age(secs):
    """Coarse human age. `None` is stated, never rendered as `0s`."""
    if secs is None:
        return "no age recorded"
    s = int(secs)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600:02d}h"


def live_resume_command(row):
    """How to re-enter this window's agent session, or None when it has no id."""
    sid = row.get("claude_session_id")
    if not sid:
        return None
    if row.get("runtime") == "opencode":
        return f"opencode --session {sid}"
    return f"claude --resume {sid}"


def live_tail_argv(row, lines):
    """🔴 `--host` IS PASSED EXPLICITLY. `session-manager tail` resolves
    `--host all` to the LOCAL host, so a tail of a laptop row without it
    searches the workbench and reports the window missing."""
    return [sys.executable, SESSION_MANAGER, "tail",
            f"{row.get('session')}:{row.get('window_index')}",
            "--host", str(row.get("host")), "--plain", "--lines", str(int(lines))]


# 🔴 THE TAIL EXIT CODES THAT ARE NOT FAILURES. `session-manager tail` returns
# 0 for a scrollback and 3 (EXIT_EMPTY) for a window whose scrollback really is
# empty — a MEASURED empty. Everything else (2 no-such-window, 4 host
# unreachable, 5 no tmux server) means the scrollback was NOT obtained, and
# rendering that as "(empty scrollback)" over exit 0 is the silent-zero failure
# this whole tool exists to refuse. The window closing between the scan and the
# tail is an ordinary race, not an exotic one.
TAIL_MEASURED_RCS = (0, 3)


def live_tail(row, lines):
    """One window's scrollback, with `ok` DISCRIMINATED from an empty string.

    `ok` is False whenever the scrollback was not obtained; `rc` travels with
    it so a caller never has to infer the reason from an empty `text`.
    """
    try:
        rc, stdout, stderr = RUN(live_tail_argv(row, lines))
    except Exception as e:  # noqa: BLE001
        return {"rc": None, "ok": False, "text": "",
                "error": f"{type(e).__name__}: {e}"}
    return {"rc": rc, "ok": rc in TAIL_MEASURED_RCS, "text": stdout,
            "error": (stderr or "").strip() or None}


def render_live(res, limit=None):
    """The LIVE section. Returns a list of lines, ALWAYS non-empty."""
    out = []
    if res["status"] == "error":
        out.append(f"LIVE: SCAN FAILED — {res['error']}")
        out.append("  🔴 NOT 'nothing is running': the live fleet was not "
                   "measured at all.")
        return out
    if res["status"] == "unavailable":
        out.append("LIVE: NO HOST ANSWERED — the live fleet was NOT measured.")
        out.append("  unreachable: " +
                   (", ".join(res["hosts_unreachable"]) or "unknown"))
        out.append("  🔴 An empty result here is UNMEASURED, not 'nothing is "
                   "running'.")
        return out
    rows = res["rows"]
    head = (f"LIVE ({len(rows)} matched"
            f"; searched: {', '.join(res['hosts_reachable'])}")
    if res["hosts_unreachable"]:
        head += f"; NOT searched: {', '.join(res['hosts_unreachable'])}"
    head += (f"; fields: {', '.join(res['match_fields'] or ['<all rows>'])})")
    out.append(head)
    if res["hosts_unreachable"]:
        out.append("  ⚠ " + ", ".join(res["hosts_unreachable"]) +
                   " did not answer — a window living only there cannot appear "
                   "below, so this is not a measured absence on that host.")
    if not rows:
        out.append("  (no live window matched these terms on the hosts that "
                   "answered)")
        return out
    # 🔴 `--limit` BOUNDS THE DISPLAY, NOT THE MEASUREMENT. The header above
    # already printed the full match count, and `_tail_outcome` is handed the
    # UNSLICED list — capping the ambiguity check at the display limit would
    # turn "several matched, I refuse" into "one is showing, I will tail that
    # one", which is guessing with extra steps.
    #
    # 🔴 THE SLICE IS THE ARCHIVE LEG'S SLICE, EXACTLY. It used to read
    # `limit <= 0` as "unbounded", so `--limit 0` showed the whole fleet here and
    # nothing at all in the ARCHIVE section of the SAME run. `main` now rejects
    # `--limit < 1` outright, so the only values reaching this are >= 1 (or None
    # from a direct call) — and the expression no longer carries a second,
    # contradictory meaning for anything else.
    shown = rows if limit is None else rows[:limit]
    if len(shown) < len(rows):
        out.append(f"  (showing {len(shown)} of {len(rows)} — raise --limit "
                   "to see the rest)")
    for i, r in enumerate(shown, 1):
        # 🔴 `hotkey_display` is READ, never derived here. `M-v` and `M-V` are
        # different sessions; the one writer of that spelling is
        # `session-manager.hotkey_display`, and re-deriving it in this renderer
        # is exactly the mistake it exists to remove.
        chord = r.get("hotkey_display") or "no hotkey"
        out.append(f"{i}. {r.get('host')}  "
                   f"{r.get('session')}:{r.get('window_index')}  "
                   f"{r.get('label')} [{chord}]  "
                   f"{r.get('status')} · {fmt_age(r.get('age_secs'))} "
                   f"(age from {r.get('age_source') or 'no writer'})")
        # Tri-state, dumped as JSON so `null` is visibly not `false`.
        out.append(f"   waiting_probable: {json.dumps(r.get('waiting_probable'))}"
                   f"   [{r.get('waiting_status')}]")
        out.append(f"   waiting_signals:  {json.dumps(r.get('waiting_signals'))}")
        out.append(f"   task: {r.get('task') or '—'}")
        out.append(f"   path: {r.get('path') or '—'}")
        resume = live_resume_command(r)
        out.append(f"   resume: {resume}" if resume else
                   "   resume: no agent session id on this row — "
                   f"attach with `tmux attach -t {r.get('session')}:"
                   f"{r.get('window_index')}` on {r.get('host')}")
        out.append("   tail:   " + " ".join(live_tail_argv(r, 100)[1:]))
    return out


def build_parser():
    """The parser, EXPOSED — so the archive-only partition can be checked
    against the parser's own ACTIONS rather than against a parsed namespace.

    🔴 A namespace is not the flag set. `set(vars(parse_args([...])))` misses any
    flag declared `default=argparse.SUPPRESS`, which is absent from the namespace
    entirely — so such a flag was in neither half of the partition and the
    equality still held. Measured: a `--newest-first` with `SUPPRESS`, classified
    nowhere, left the whole suite green while the gate's own comment claimed
    "adding a flag without deciding which fails the suite". Reading `_actions`
    sees every declared flag whatever its default.
    """
    p = argparse.ArgumentParser(add_help=True, description="Find past Claude Code and opencode sessions by keyword.")
    p.add_argument("terms", nargs="*", help="search terms (ANDed unless --any)")
    p.add_argument("--skill", default=None,
                   help="only sessions that USED this skill (exact canonical "
                        "identity). Reads the per-record skill attribution, an "
                        "explicit `Skill` tool call, and a typed /name — so it "
                        "sees a skill that AUTO-FIRED, which no keyword search "
                        "can distinguish from prose. May be used alone, with no "
                        "search terms. Counts SESSIONS: a skill used only inside "
                        "a dispatched SUBAGENT is not counted. The opencode "
                        "corpus has no such attribution, so that leg is SKIPPED "
                        "and the omission is printed.")
    p.add_argument("--project", default="", help="only sessions whose cwd/project contains this substring")
    p.add_argument("--since", default="",
                   help=f"only sessions on/after this date (YYYY-MM-DD). "
                        f"Overrides the default {DEFAULT_SINCE_DAYS}-day "
                        f"window; cannot be combined with --all-time")
    p.add_argument("--all-time", action="store_true",
                   help=f"search the WHOLE transcript corpus instead of the "
                        f"default last-{DEFAULT_SINCE_DAYS}-days window "
                        f"(~15s warm vs ~8s). --skill is already unwindowed")
    p.add_argument("--limit", type=int, default=10, help="max sessions to show (default 10)")
    p.add_argument("--any", action="store_true", help="match ANY term instead of all")
    p.add_argument("--all", action="store_true",
                   help="widen the search surface to tool inputs AND tool output (noisier)")
    p.add_argument("--claude-only", action="store_true",
                   help="search only Claude Code transcripts (skip opencode)")
    p.add_argument("--opencode-only", action="store_true",
                   help="search only opencode sessions (skip Claude Code)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of human text")
    p.add_argument("--live", action="store_true",
                   help="scan the LIVE cross-host tmux fleet FIRST (~1s) and "
                        "only fall back to the much slower transcript walk "
                        "when nothing live matched. Matches "
                        "task/label/codename — NOT path.")
    p.add_argument("--deep", action="store_true",
                   help="with --live: run the transcript walk TOO, even when "
                        "live windows matched")
    p.add_argument("--tail", type=int, default=None, metavar="N",
                   help="with --live: print the last N scrollback lines of the "
                        "matched window. REFUSES on an ambiguous match rather "
                        "than guessing (exit 3), and lists the candidates.")
    return p


def parser_dests():
    """Every destination the parser DECLARES, `help` excluded.

    Read off `_actions`, not off a parsed namespace — see `build_parser`.
    """
    return {a.dest for a in build_parser()._actions if a.dest != "help"}


def parse_args(argv=None):
    return build_parser().parse_args(argv)


def render(r):
    """The JSON document. Datetimes are dropped; every field here is a string or a number."""
    d = {
        "session_id": r["session_id"],
        "project": os.path.basename(r["cwd"]) or r["project_dir"],
        "cwd": r["cwd"],
        "branch": r["branch"],
        "first": r["first"],
        "last": r["last"],
        "genesis": r["genesis"],
        "matched_terms": r["matched_terms"],
        "total_hits": r["total_hits"],
        "snippets": r["snippets"],
        "path": r["path"],
    }
    if r.get("source"):
        d["source"] = r["source"]
    return d


# 🔴 THE WALK'S OWN COUNTERS, PUBLISHED THROUGH A MODULE SEAM RATHER THAN A
# RETURN VALUE. `archive_search(a, since)` is REPLACED WHOLESALE by the tests at
# exactly that two-positional signature (`fake_archive(a, since)`), so widening
# it would break the seam that keeps 152 tests off a 43-second walk. A caller
# that replaces the function therefore leaves this empty — and `window_notice`
# renders that as NOT MEASURED rather than as a zero, which is the point: a
# `0 skipped` from a counter nobody wired is indistinguishable from a real one.
# Reset by `main` before every call so a stale count cannot describe a new run.
ARCHIVE_STATS = {}


def archive_search(a, since):
    """The two-corpus transcript walk. Factored out so the `--live` path can
    decide WHETHER to pay for it, and so `main` can bound it with a window."""
    # 🔴 `--all` used to be INERT. Its handler sat behind `if not a.all and typ not in
    # ("user", "assistant")`, twenty lines after an unconditional `if typ not in
    # ("user", "assistant"): continue` had already skipped everything it could have
    # admitted — so the flag the SKILL.md advertises for "tool output" widened nothing.
    # It now selects the search surface, which is the only thing it ever meant.
    surface = SURFACE_ALL if a.all else SURFACE_TEXT

    results = []

    # Search Claude Code transcripts (default)
    if not a.opencode_only:
        # `stats` is what makes the window's size a MEASUREMENT: `skipped_stale`
        # is counted by the prefilter itself, not re-derived here.
        stats = {}
        cc_results = search(a.terms, root=ROOT, match_any=a.any, since=since,
                            project=a.project, surface=surface, limit=None,
                            skill=a.skill, stats=stats)
        ARCHIVE_STATS.update(stats)
        results.extend(cc_results)
        # 🔴 The OTHER hosts' Claude corpora. Without this the local walk was the
        # whole Claude answer while the description promised "both hosts" — the
        # exact gap that made a workbench run report a laptop-only skill as
        # never used. Peers that cannot be reached warn on stderr.
        results.extend(search_peers(a.terms, match_any=a.any, since=since,
                                    project=a.project, surface=surface,
                                    skill=a.skill))

    # Search opencode sessions (default)
    # 🔴 SKIPPED under `--skill`, and the omission is PRINTED (stderr, so a
    # `--json` stdout stays parseable). That corpus has no per-record skill
    # attribution — there a skill invocation is a tool CALL, a different shape
    # this search does not read. Running it unfiltered would fold in sessions
    # selected on TERMS alone, quietly answering a different question; skipping
    # it silently would hand back a partial count that reads as the whole fleet.
    if not a.claude_only and not a.skill:
        try:
            oc_results = search_opencode(a.terms, match_any=a.any, since=since,
                                         project=a.project, limit=None)
            results.extend(oc_results)
        except Exception as e:
            print(f"WARN: opencode search failed: {e}", file=sys.stderr)
    elif a.skill and not a.claude_only:
        print("NOT searched: the opencode corpus (--skill has no attribution "
              "there). Claude transcripts WERE searched on every reachable host; "
              "any peer that could not answer is named on its own line above.",
              file=sys.stderr)

    # Re-rank the merged set by the same criteria
    results.sort(key=lambda r: (len(r["matched_terms"]), r["total_hits"], r["last_local"]),
                 reverse=True)
    return results


def _query_label(a):
    """What the run actually searched for, for the human-facing lines.

    `--skill` can carry the whole query, so `' '.join(a.terms)` alone prints an
    empty string and reads as "matched nothing" rather than "matched no session
    that used this skill".
    """
    label = " ".join(a.terms)
    if getattr(a, "skill", ""):
        label = (f"skill={a.skill}" + (f" + {label}" if label else "")).strip()
    return label


def render_archive_hit(i, r, state=None):
    """One archive hit, optionally annotated with its LIVE/CLOSED state."""
    out = []
    date = (r["last"] or r["first"])[:16].replace("T", " ")
    project = os.path.basename(r["cwd"]) or r["project_dir"]
    source_tag = f"  [{r['source']}]" if r.get("source") else ""
    # 🔴 The annotation is a JOIN on `claude_session_id`, and `UNMEASURED` is a
    # real third value — an archive hit is only CLOSED when a live scan actually
    # ran and did not hold its id.
    tag = f"  <{state}>" if state else ""
    out.append(f"{i}. [{date}] {project}  ({r['branch'] or 'no-branch'})"
               f"{source_tag}{tag}  ·  {r['total_hits']} hits")
    if r["genesis"]:
        out.append(f"   opened: {r['genesis'][:120]!r}")
    for term, (role, snip) in r["snippets"].items():
        out.append(f"   {term} → ({role}) …{snip[:120]}…")
    if r.get("source") == "opencode":
        out.append(f"   resume: opencode --session {r['session_id']}")
    else:
        out.append(f"   resume: claude --resume {r['session_id']}")
    out.append(f"   file:   {r['path']}")
    out.append("")
    return out


def _tail_outcome(a, live):
    """Resolve `--tail` to ONE window, or REFUSE and say why.

    Returns `(row_or_None, exit_code, lines)`. 🔴 It never picks a row when the
    match is ambiguous — ambiguity is refused, not guessed: a scrollback printed
    from the wrong window is an answer that reads as correct, which is strictly
    worse than no answer.

    🔴 PARTIAL COVERAGE IS ITS OWN CLAIM AND IS MADE HERE, not inherited. The
    reason the ARCHIVE block got its own PARTIAL line — "the LIVE section's
    caveat refers to the live row list, not to these annotations, which are a
    different claim under a different heading" — applies verbatim to the TAIL
    block, and was not applied there for one revision. Under a partial fleet:

      * ZERO rows is NOT "there is nothing to tail" (exit 3). The window may be
        on the host that did not answer, so this is UNMEASURED — exit 4, the
        same code the fleet-not-measured branch uses, because it is the same
        fact about a narrower question.
      * ONE row still tails, but the resolution is DISCLOSED as possibly
        non-unique. Refusing here would make `--tail` useless whenever the
        laptop is asleep, which is a permanently-red gate — but claiming "this
        is the one" is the guess the whole function exists to refuse.
      * SEVERAL rows already refuse; the candidate list is simply also
        incomplete, and says so.
    """
    if live["status"] != "ok":
        return None, EXIT_UNAVAILABLE, [
            "TAIL: REFUSED — the live fleet was not measured, so there is no "
            "window to tail. See the LIVE section above."]
    complete = live_coverage_complete(live)
    # SUBSCRIPT, not `.get` — the scan result is read by subscript throughout
    # these four functions on purpose, so `test_the_live_row_field_ledger_...`'s
    # AST sweep for `<row>.get("field")` cannot pick up a non-row key.
    missing = ", ".join(live["hosts_unreachable"] or []) or "a host"
    rows = live["rows"]
    if len(rows) == 1:
        if complete:
            return rows[0], EXIT_OK, []
        return rows[0], EXIT_OK, [
            f"⚠ TAIL: resolved on PARTIAL coverage — {missing} did not answer, "
            "so this is the only match ON THE HOSTS THAT DID. Another window "
            "may match there; the scrollback below is real either way."]
    if not rows:
        if not complete:
            return None, EXIT_UNAVAILABLE, [
                f"TAIL: REFUSED — no live window matched, but {missing} did not "
                "answer, so this is NOT 'there is nothing to tail'. The window "
                "may be there and UNMEASURED. Use --deep for the archive."]
        return None, EXIT_AMBIGUOUS, [
            "TAIL: REFUSED — no live window matched, so there is nothing to "
            "tail. Narrow or widen the terms, or use --deep for the archive."]
    lines = [f"TAIL: REFUSED — {len(rows)} live windows matched and this tool "
             "does not guess which one you meant. Re-run with a narrower term, "
             "or tail one directly:"]
    for r in rows:
        lines.append("  " + " ".join(live_tail_argv(r, a.tail)[1:])
                     + f"    # {r.get('label')} "
                       f"[{r.get('hotkey_display') or 'no hotkey'}] — "
                       f"{r.get('task') or 'no task'}")
    if not complete:
        lines.append(f"  ⚠ {missing} did not answer — this candidate list is "
                     "INCOMPLETE, so a narrower term may still be ambiguous.")
    return None, EXIT_AMBIGUOUS, lines


def _window_line(a, source, since):
    """`window_notice` fed from the counters the walk that JUST ran published,
    and from the legs that run under THESE flags."""
    return window_notice(since, source,
                         skipped=ARCHIVE_STATS.get("skipped_stale"),
                         examined=ARCHIVE_STATS.get("sessions_examined"),
                         legs=unmeasured_legs(a),
                         claude_leg_ran=not a.opencode_only)


def main(argv=None):
    a = parse_args(argv)
    # 🔴 RESET HERE, NOT BESIDE THE WALK. Both reset sites used to sit INSIDE an
    # `if run_archive:` / classic-path branch, so a `--live` run that the live
    # fleet answered left the PREVIOUS call's counters in place and
    # `archive.window.message` published `"… skipped unopened: 0 of 2"` for a
    # walk that never happened — a measured-looking count beside
    # `archive.ran: false`, exactly the null-vs-0 laundering the design forbids.
    # `skipped_stale` and `sessions_examined` escaped it only because they are
    # separately guarded by `run_archive`; `message` was not, and the comment on
    # `ARCHIVE_STATS` claiming "reset by `main` before every call" was false for
    # that branch. It is true now: one reset, unconditional, before either leg.
    # (Not reachable from a shell today — `main` runs once per process — so this
    # was a latent defect plus a comment the code contradicted.)
    ARCHIVE_STATS.clear()
    if a.since:
        try:
            datetime.fromisoformat(a.since)
        except ValueError:
            print(f"bad --since date: {a.since!r} (want YYYY-MM-DD)", file=sys.stderr)
            # 🔴 `return EXIT_USAGE`, not `sys.exit(2)`. The literal was correct
            # and `EXIT_CONTRACT` names this path in the exit-2 sentence — but a
            # renumbering of `EXIT_USAGE` would have moved the constant and left
            # this literal behind, splitting a documented pair silently. It also
            # makes the path testable in-process like every other exit.
            return EXIT_USAGE
    # 🔴 TWO WINDOWS NAMED AT ONCE IS REFUSED, NOT RESOLVED. `--since` says
    # "from here" and `--all-time` says "from the beginning"; picking a winner
    # would make one of the two flags silently inert, which is the shape
    # `--limit < 1` is refused for. The caller asked for two different searches.
    if a.since and a.all_time:
        print("--since and --all-time name two different windows; pass one. "
              f"(--all-time lifts the default {DEFAULT_SINCE_DAYS}-day window; "
              "--since replaces it.)", file=sys.stderr)
        return EXIT_USAGE

    # 🔴 A `--skill` that was GIVEN but names nothing is REFUSED, never silently
    # dropped. Canonicalised with the same rule the corpus is keyed by, so
    # `--skill apps/web:deploy` matches the recorded `deploy`; `--skill /` and
    # `--skill ""` name nothing and would otherwise slip the guard below and run
    # an UNFILTERED keyword search at exit 0, answering a different question.
    raw_skill, a.skill = a.skill, (canonical_skill_name(a.skill) or "")
    if raw_skill is not None and not a.skill:
        print(f"--skill {raw_skill!r} names no skill", file=sys.stderr)
        return EXIT_USAGE
    if not a.terms and not a.skill:
        print("nothing to search for: give at least one term, or --skill NAME",
              file=sys.stderr)
        return EXIT_USAGE
    # 🔴 TWO CORPUS SELECTORS THAT BETWEEN THEM SELECT NOTHING. `archive_search`
    # skips opencode under `--claude-only` and skips the local Claude walk AND
    # `search_peers` under `--opencode-only`, so the pair searches NO corpus and
    # returns a clean "No sessions matched" at exit 0 — a false corpus-wide
    # absence, for a term with hundreds of real hits. That half PRE-DATES this
    # change; what this change added was a notice that then described the
    # unsearched opencode corpus as "windowed too and NOT in this count", i.e.
    # as searched. Refused rather than half-disclosed, the same call `--since`
    # with `--all-time` gets: the caller asked for two things that cannot both
    # hold. It also makes the `[--claude-only | --opencode-only]` in this file's
    # usage synopsis true — argparse never enforced it.
    if a.claude_only and a.opencode_only:
        print("--claude-only and --opencode-only select opposite corpora; "
              "between them they search NOTHING, so the run would report "
              "'no sessions matched' for a query it never ran. Pass one.",
              file=sys.stderr)
        return EXIT_USAGE
    if a.skill and a.opencode_only:
        print("--skill cannot be answered from the opencode corpus (no "
              "per-record skill attribution there); drop --opencode-only",
              file=sys.stderr)
        return EXIT_USAGE
    if a.live and not a.terms:
        print("--live matches on a window's task/label/codename, so it needs at "
              "least one term; --skill alone is an ARCHIVE query", file=sys.stderr)
        return EXIT_USAGE

    # 🔴 RESOLVED HERE, AFTER `--skill` HAS BEEN CANONICALISED — the exemption
    # keys on the canonical name, so resolving the window any earlier would read
    # a `--skill` that had not yet been rejected or normalised.
    since, window_source = resolve_window(a)

    if a.tail is not None and not a.live:
        print("--tail requires --live: it prints the scrollback of a LIVE tmux "
              "window, which the transcript archive cannot supply.",
              file=sys.stderr)
        return EXIT_USAGE
    if a.deep and not a.live:
        print("(--deep only means something with --live: without it the "
              "transcript walk always runs)", file=sys.stderr)
    # 🔴 `--limit` BELOW 1 MEANT TWO OPPOSITE THINGS IN ONE RUN: the live leg
    # read `<= 0` as "unbounded, show everything" and the archive leg took
    # `results[:0]` and showed nothing. One flag, one number, contradictory
    # halves — and neither reading is useful. Rejected outright instead of
    # picking a winner, because a degenerate input deserves a message rather
    # than a silent choice. This is a behaviour change on the classic path for
    # `--limit < 1` only, and it is deliberate.
    if a.limit < 1:
        print(f"--limit must be at least 1 (got {a.limit}). Below 1 the two "
              "legs disagreed: the live section showed everything and the "
              "archive section showed nothing.", file=sys.stderr)
        return EXIT_USAGE
    # 🔴 THE SAME DEGENERATE INPUT ONE FLAG OVER, AND IT WENT UNGUARDED. MEASURED
    # before this check existed: `--tail 0` and `--tail -5` were both accepted
    # and both exited 0, printing a scrollback block headed `(last 0 lines)` and
    # `(last -5 lines)` — a header stating a line count nothing honoured, over a
    # value `session-manager tail` had silently clamped to zero. Output line
    # deltas over one window, four values: 0 -> +0, -5 -> +0, 5 -> +5, 40 -> +40.
    # Refused rather than clamped, for the reason `--limit` is refused: a
    # degenerate input deserves a message, not a silent choice made for you.
    if a.tail is not None and a.tail < 1:
        print(f"--tail must be at least 1 (got {a.tail}). Below 1 the "
              "scrollback silently clamped to zero lines while the header "
              f"still claimed 'last {a.tail} lines'.", file=sys.stderr)
        return EXIT_USAGE
    # 🔴 A FLAG THAT REACHES ONLY ONE LEG MUST SAY SO — see `ARCHIVE_ONLY_FLAGS`
    # for the ledger and for why this is data rather than an inline list closed
    # by a completeness sentence.
    if a.live:
        notice = archive_only_notice(a)
        if notice:
            print(notice, file=sys.stderr)

    # ------------------------------------------------------------------ #
    # THE CLASSIC PATH — unchanged, byte for byte, including `--json`'s
    # bare-list shape. `--live` is opt-in precisely so no existing caller's
    # output moves.
    # ------------------------------------------------------------------ #
    if not a.live:
        results = archive_search(a, since)
        # 🔴 STDOUT ON THE HUMAN PATH, STDERR UNDER `--json` — and the earlier
        # revision sent BOTH to stderr, which put the disclosure where the most
        # common invocation never looks. The justification it carried ("stdout
        # on this path is the bare JSON array every existing caller parses") is
        # true only under `--json`; on the human branch stdout is prose with no
        # parse contract to protect, and `--live`'s human branch already prints
        # the window to stdout. So `find-session.py redis 2>/dev/null` used to
        # print `No sessions matched: redis` and nothing at all about the bound.
        # ⚠ KNOWN AND DELIBERATE: `--json` WITHOUT `--live` still emits the bare
        # array, so its window is on stderr ONLY — there is no machine-readable
        # window on that path. Adding a key would change a shape callers parse;
        # `--live --json` carries `archive.window`. Said out loud in SKILL.md
        # rather than left for a consumer to discover.
        print(_window_line(a, window_source, since),
              file=sys.stderr if a.json else sys.stdout)
        shown = results[: a.limit]
        if a.json:
            print(json.dumps([render(r) for r in shown], indent=2))
            return EXIT_OK
        if not results:
            print(f"No sessions matched: {' '.join(a.terms)}")
            return EXIT_OK
        print(f"{len(results)} session(s) matched {' '.join(a.terms)!r}"
              + (f" (showing {len(shown)})" if len(shown) < len(results) else "")
              + "\n")
        for i, r in enumerate(shown, 1):
            print("\n".join(render_archive_hit(i, r)))
        return EXIT_OK

    # ------------------------------------------------------------------ #
    # 🔴 LIVE FIRST. ~1.1 s against the archive walk's much larger cost, and the live rows
    # carry the fields the question is actually about.
    # ------------------------------------------------------------------ #
    live = live_scan(a.terms)
    live_lines = render_live(live, limit=a.limit)

    # 🔴 ONE PREDICATE, ONE PLACE — `run_archive` is DERIVED from the reason
    # rather than computed beside it. The two were open-coded separately for one
    # revision and already disagreed about `--deep`'s precedence, which is the
    # "wrong at N−1 of N sites, in the same direction" shape `claude/RULES.md`
    # names. Consolidating also made the UNMEASURED branch observable: a
    # mutation sweep scored its removal SURVIVED while the two were independent,
    # because a failed scan carries zero rows anyway and the `not rows` clause
    # picked up the slack silently. Now dropping it changes the printed reason.
    #
    # The order is the point: "we could not look" must never launder into "we
    # looked and there is nothing".
    archive_reason = (
        # 🔴 FIRST, and unconditional. `--skill` is answerable ONLY by the
        # archive (see ARCHIVE_ONLY_FLAGS), so if the live leg matched and this
        # clause were absent, `run_archive` would stay False and the skill
        # filter would never run — returning live rows chosen on TERMS ALONE
        # under a heading the caller reads as a skill answer.
        "--skill (answerable only from the transcript corpus)" if a.skill
        else "--deep" if a.deep
        else "the live scan was UNMEASURED" if live["status"] != "ok"
        else "no live match" if not live["rows"]
        else None)
    run_archive = archive_reason is not None

    results, live_ids = [], None
    # 🔴 `complete` GATES ONLY THE **CLOSED** VERDICT — see `live_state_of`. It
    # starts True so that a run which never builds an id set (`live_ids is None`)
    # still reports UNMEASURED through the `live_ids` branch, rather than
    # depending on this flag at all.
    coverage_complete = True
    if run_archive:
        results = archive_search(a, since)
        # No print here: the human branch says it in the ARCHIVE block below and
        # `--json` carries `archive.window`. Printing it to stderr as well made
        # `--live --deep` announce the same window TWICE, once per stream.

        # 🔴 A SECOND, UNFILTERED scan, and it is not waste. The first scan was
        # NARROWED by the terms, so a session that IS live but whose window
        # title no longer says those words is absent from it — annotating an
        # archive hit CLOSED off that set would state a measured absence about a
        # window the filter removed. Only on this path, which already costs 30 s,
        # so the fast path never pays for it.
        unfiltered = live_scan()
        live_ids = live_session_ids(unfiltered)
        # ...and the COVERAGE of that scan, which is a separate fact from
        # whether it produced a set at all. A fleet where one host was asleep
        # yields a perfectly real id set that cannot support a single CLOSED.
        coverage_complete = live_coverage_complete(unfiltered)

    def _state(sid):
        return live_state_of(sid, live_ids, coverage_complete)

    shown = results[: a.limit]
    tail_row, tail_code, tail_lines, tail_res = None, EXIT_OK, [], None
    if a.tail is not None:
        tail_row, tail_code, tail_lines = _tail_outcome(a, live)
        if tail_row is not None:
            tail_res = live_tail(tail_row, a.tail)
            # 🔴 A TAIL THAT DID NOT RUN IS NOT AN EMPTY WINDOW. `rc` 2/4/5 mean
            # the window vanished between the scan and the tail, the host went
            # away, or there is no tmux server — none of which is "the pane is
            # blank". Nothing branched on `rc` before, so all three printed
            # "(empty scrollback)" and exited 0.
            if not tail_res["ok"]:
                tail_code = EXIT_UNAVAILABLE
                tail_lines = [
                    f"TAIL: FAILED — `session-manager tail` exited "
                    f"{tail_res['rc']} for "
                    f"{tail_row.get('session')}:{tail_row.get('window_index')} "
                    f"on {tail_row.get('host')}: "
                    f"{tail_res.get('error') or 'no stderr'}",
                    "  🔴 The scrollback was NOT read. This is not an empty "
                    "window — the window may have closed between the scan and "
                    "the tail, or the host may have gone away.",
                ]

    if a.json:
        # 🔴 A NEW ENVELOPE, not a widened list. `--json` without `--live` still
        # emits the bare archive array every existing caller parses; adding
        # `live` keys to that array's elements would have changed a shape nobody
        # asked to change.
        print(json.dumps({
            "live": {k: live[k] for k in
                     ("status", "rows", "hosts_reachable", "hosts_unreachable",
                      "match_fields", "error", "rc", "terms")},
            "archive": {
                "ran": run_archive,
                "reason": archive_reason or "live matched",
                "total": len(results),
                # 🔴 THE BOUND TRAVELS WITH THE RESULT. `total` is a count under
                # a window, and a caller that cannot see the window reads it as
                # a corpus-wide one. `since` is null for an unwindowed run, and
                # `skipped_stale` is null when nothing measured it — never 0.
                "window": {
                    "source": window_source,
                    "since": window_stamp(since),
                    "default_days": DEFAULT_SINCE_DAYS,
                    "skipped_stale": (ARCHIVE_STATS.get("skipped_stale")
                                      if run_archive else None),
                    "sessions_examined": (ARCHIVE_STATS.get("sessions_examined")
                                          if run_archive else None),
                    "message": _window_line(a, window_source, since),
                },
                # `live_state` is UNMEASURED, not CLOSED, when no live scan
                # could supply the id set — OR when the scan that supplied it
                # did not cover every host.
                "results": [dict(render(r), live_state=_state(r["session_id"]))
                            for r in shown],
                "live_ids_measured": live_ids is not None,
                # 🔴 THE SECOND HALF, PUBLISHED SEPARATELY, because it is a
                # different fact. `live_ids_measured: true` with
                # `live_coverage_complete: false` is the state in which a MISS
                # proves nothing — and it used to be reported as CLOSED.
                "live_coverage_complete": (coverage_complete
                                           if live_ids is not None else None),
                # 🔴 `None`, NEVER `[]`, FOR A SCAN THAT NEVER RAN. `live_scan`
                # now seeds both host lists `None` on its error paths (see its
                # docstring), so this passes the discriminated value straight
                # through instead of laundering an unmeasured scan into "every
                # host answered". `run_archive` False means no second scan was
                # made at all, which is also not a measurement.
                "live_hosts_unreachable": (unfiltered["hosts_unreachable"]
                                           if run_archive else None),
            },
            "tail": None if a.tail is None else {
                "requested_lines": a.tail,
                "resolved": None if tail_row is None else {
                    "host": tail_row.get("host"),
                    "target": f"{tail_row.get('session')}:"
                              f"{tail_row.get('window_index')}",
                },
                "refused": tail_row is None,
                # 🔴 THE TAIL BLOCK CARRIES ITS OWN COVERAGE. A `refused: true`
                # with zero matches under a partial fleet is UNMEASURED, not
                # "there is nothing to tail", and a `resolved` row under one is
                # the only match ON THE HOSTS THAT ANSWERED. Neither fact is
                # readable from `archive.*`, which describes a different scan
                # (the unfiltered one) and is absent entirely on the fast path.
                # 🔴 `live_coverage_state`, NOT `live_coverage_complete`: the
                # predicate is two-valued and publishing its `False` for a scan
                # that never ran claims *measured, and incomplete*. `SKILL.md`
                # points a branching caller at THIS field, so it is the one
                # field that must be able to say "unmeasured".
                "coverage_complete": live_coverage_state(live),
                "hosts_unreachable": live["hosts_unreachable"],
                "message": "\n".join(tail_lines) or None,
                # 🔴 `rc` and `ok` TRAVEL WITH THE TEXT. An empty `text` beside
                # `ok: false` is "the scrollback was not read"; beside
                # `ok: true` it is a measured empty pane. Publishing `error`
                # alone left those indistinguishable whenever stderr was quiet.
                "rc": (tail_res or {}).get("rc"),
                "ok": (tail_res or {}).get("ok"),
                "text": (tail_res or {}).get("text"),
                "error": (tail_res or {}).get("error"),
            },
        }, indent=2, default=str))
        return tail_code

    print("\n".join(live_lines))
    print()
    if not run_archive:
        # The `--all-time` hint is CONDITIONAL: suggesting it to a caller who
        # already passed it, on a run where it did nothing, is the noise that
        # trains a reader to skip the line.
        hint = (f" (last {DEFAULT_SINCE_DAYS} days by default; add --all-time "
                f"for the whole corpus, ~15s warm)"
                if window_source == WINDOW_DEFAULT else
                f" (window: {window_source})")
        print(f"ARCHIVE: skipped — the live fleet answered. Pass --deep to "
              f"search the {len(a.terms)}-term transcript walk too{hint}.")
    else:
        print(f"ARCHIVE ({len(results)} matched; ran because: {archive_reason})")
        print("  " + _window_line(a, window_source, since))
        if live_ids is None:
            print("  ⚠ live/closed state is UNMEASURED — the live scan did not "
                  "answer, so no hit below can be called CLOSED.")
        elif not coverage_complete:
            # 🔴 ITS OWN LINE, in the ARCHIVE block. The LIVE section's caveat
            # says an absence "cannot appear BELOW" and refers to the live row
            # list; it says nothing about these annotations, which are a
            # different claim printed under a different heading.
            print("  ⚠ live/closed state is PARTIAL — "
                  + ", ".join(unfiltered["hosts_unreachable"])
                  + " did not answer, so a hit that is NOT marked <LIVE> is "
                    "UNMEASURED rather than CLOSED.")
        if not results:
            print(f"  No sessions matched: {_query_label(a)}")
        else:
            if len(shown) < len(results):
                print(f"  (showing {len(shown)})")
            print()
            for i, r in enumerate(shown, 1):
                print("\n".join(render_archive_hit(
                    i, r, _state(r["session_id"]))))
    if a.tail is not None:
        print()
        if tail_lines:
            print("\n".join(tail_lines))
        # 🔴 Only a MEASURED tail prints a scrollback block. A failed one has
        # already printed its FAILED lines above; falling through would append
        # "(empty scrollback)" under a header claiming to show the last N lines.
        if tail_res is not None and tail_res["ok"]:
            print(f"TAIL {tail_row.get('host')} "
                  f"{tail_row.get('session')}:{tail_row.get('window_index')} "
                  f"(last {a.tail} lines)")
            if tail_res.get("error"):
                print(f"  tail reported: {tail_res['error']}")
            sys.stdout.write(tail_res.get("text")
                             or "  (empty scrollback — MEASURED, the pane "
                                "really is blank)\n")
    return tail_code


if __name__ == "__main__":
    sys.exit(main())
