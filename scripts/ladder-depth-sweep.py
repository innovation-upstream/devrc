#!/usr/bin/env python3
"""How deep do adversarial-audit ladders actually run? — the measurement behind
the attribution gate in `claude/skills/audit-pr/SKILL.md`.

WHY THIS EXISTS
---------------
That skill's stop rule is keyed to FINDINGS, and its attribution gate is keyed
to whether a round's FIXES changed payload. `scripts/tests/test_audit_ladder_
stop_rule.py` pins the instructions but says plainly that it cannot observe
BEHAVIOUR: nothing in the suite proves any session actually stopped. This script
is the other half — the only thing that can tell you whether the rule changed
what sessions do.

It answers one question: over the sessions on this host, how deep did numbered
delta re-audit ladders run? Run it before and after a change to the rule (or on
two date windows) and compare the distributions.

    scripts/ladder-depth-sweep.py                     # all sessions
    scripts/ladder-depth-sweep.py --since 2026-08-27  # one window
    scripts/ladder-depth-sweep.py --detail            # one line per session

🔴 A ZERO HERE IS REFUSED, NOT REPORTED. The first run of this sweep during
devrc #900 returned a confident **0 sessions** because it filtered on tool name
`Task` while the harness emits `Agent`. A count of zero is exactly what a
correct sweep of a quiet host and a sweep wired to nothing both produce, so this
exits non-zero and says so rather than letting a zero be quoted. Same reason the
skill it serves says "a failed command is not zero".

🔴 THE WINDOW MOVES. Sessions accumulate, so this is a measurement with a
timestamp and not a constant — a figure from it read 25 and then 24 two hours
later during #900, which is why the skill body carries the qualitative claim and
the reference file carries the dated numbers. The header prints the run date;
quote it with the date or not at all.

WHAT IT COUNTS
--------------
A "round" is a subagent dispatch whose description or prompt names a numbered
delta re-audit — `Delta re-audit round 4 of PR 900`, `round 3 delta re-audit`,
and so on. A session's DEPTH is the highest round number it dispatched. First
audits (unnumbered) are not counted as rounds, so depth 3 means at least four
audits ran. `subagents/` transcripts are excluded — by
`scripts/lib/transcript_search.py`, which owns that rule; this script does not
walk the corpus itself.

Blind spots, so the number is not read wider than it is:

* A ladder whose rounds were never NUMBERED is invisible, and so is one run by a
  different runtime. Both make this an UNDER-count of ladder work.
* IMPLIED is an UPPER BOUND. It assumes a ladder that reached round N ran N
  rounds; where numbering skips it over-counts — measured 2026-08-27: 3 sessions,
  8 round-numbers, 3.3% of the corpus-wide OBSERVED/IMPLIED gap.
* 🔴 MUCH OF THE REST IS NOT THE UNNUMBERED FIRST AUDIT EITHER, though an
  earlier version of this comment said all of it was. Measured 2026-08-27: 231 of
  239 missing round-numbers are LEADING absences, 128 sessions have a minimum
  round of 2 or more, and exactly ONE session in the corpus ever dispatched a
  "round 1". One unnumbered first audit accounts for at most one missing number
  per session, so **at least 103 of the 231 (45%)** must be UNNUMBERED DELTA
  ROUNDS — real rounds dispatched without a number in the label. 45% is the floor
  the measurement supports, not a share; an earlier "97% / most" over-stated it.
  Either way it is blind spot #1 above, and it means true ladder depth is HIGHER
  than IMPLIED, not lower. Do not read "depth 3" as "exactly four audits ran".
* 🔴 COMPARING TWO WINDOWS OF DIFFERENT LENGTH IS BIASED. Depth is computed from
  in-window rounds only, so a narrower window truncates ladders that started
  before it and mechanically RAISES mean depth. Measured on one unchanged
  corpus: 4.38 all-time, 4.42 since 08-01, 4.97 since 08-20, 6.47 since 08-27.
  Compare equal-length windows, or the artifact reads as an effect.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

# 🔴 THE CORPUS WALK IS NOT HAND-ROLLED HERE, and the first version of this
# script got that wrong. `scripts/lib/transcript_search.py` is the one
# enumerator, `scripts/tests/test_transcript_search.py` pins the full set of
# walk sites repo-wide, and a new `**/*.jsonl` walk fails that suite BY DEFAULT
# -- which is exactly what happened, and is the guard working. Going through the
# shared lib also inherits two decisions this script would otherwise have had to
# re-make and could have re-made wrongly: which files are corpus members
# (`subagents/` excluded), and skipping a MALFORMED LINE rather than the whole
# file, since a truncated tail is the normal shape of a transcript still being
# written.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import transcript_search as ts  # noqa: E402

# Both spellings, because the tool name is `Agent` in some transcripts and
# `Task` in others -- filtering on one of them is the bug that produced the
# refused zero this script now guards against.
DISPATCH_TOOLS = {"Agent", "Task"}

ROUND = re.compile(
    r"(?:delta\s+)?re-?audit(?:ing)?[^.\n]{0,40}?round\s*(\d+)"
    r"|round\s*(\d+)[^.\n]{0,30}(?:delta\s+)?re-?audit",
    re.I,
)


def dispatch_texts(row: dict) -> list[str]:
    """Description + prompt of every subagent dispatch in one transcript row."""
    content = (row.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use" or block.get("name") not in DISPATCH_TOOLS:
            continue
        inp = block.get("input") or {}
        out.append(f"{inp.get('description', '')} {str(inp.get('prompt', ''))[:400]}")
    return out


def sweep(since: str | None) -> tuple[dict, int, int]:
    """-> (sessions, dispatches_corpus_wide, dispatches_in_window)

    The two counts are separate because the first is the POSITIVE CONTROL and
    must not be narrowed by `--since` (a filtered zero is a measurement, not a
    broken filter), while the second is what a reader comparing two windows
    actually wants. Printing only the first under a window label was off by 21x
    on one measured pair.
    """
    sessions: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"rounds": set(), "date": ""}
    )
    dispatches = 0
    in_window = 0
    for path in ts.iter_transcripts():
        key = (path.parent.name, path.stem)
        for row in ts.load_records(path):
            stamp = (row.get("timestamp") or "")[:10]
            texts = dispatch_texts(row)
            # 🔴 The positive control counts dispatches BEFORE the window filter.
            # Counting after it made a legitimately EMPTY window exit 2 with
            # "indistinguishable from a wrong tool-name filter" -- naming a cause
            # that was not the real one, and making the honest "this zero is a
            # measurement" branch unreachable for the commonest way to get a
            # zero.
            dispatches += len(texts)
            if since and stamp and stamp < since:
                continue
            in_window += len(texts)
            for text in texts:
                hit = ROUND.search(text)
                if not hit:
                    continue
                rec = sessions[key]
                rec["rounds"].add(int(hit.group(1) or hit.group(2)))
                rec["date"] = max(rec["date"], stamp)
    return sessions, dispatches, in_window


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--since", help="only rows on/after this ISO date")
    ap.add_argument("--detail", action="store_true", help="one line per session")
    args = ap.parse_args()

    sessions, dispatches, in_window = sweep(args.since)

    # 🔴 The positive control. `dispatches` counts EVERY subagent dispatch the
    # walk saw, matched or not: if that is zero the walk itself found nothing,
    # which is a broken filter or an empty projects dir -- not a quiet host.
    if dispatches == 0:
        print(
            "🔴 REFUSING TO REPORT: the walk saw ZERO subagent dispatches under\n"
            f"   {ts.DEFAULT_ROOT}. That is indistinguishable from a wrong tool-name\n"
            "   filter or a wrong path, so it is not reported as a result.\n"
            "   Check DISPATCH_TOOLS and the projects directory before trusting\n"
            "   any number from this script.",
            file=sys.stderr,
        )
        return 2

    ladders = {k: v for k, v in sessions.items() if v["rounds"]}
    print(f"# ladder-depth sweep — run {date.today().isoformat()}"
          + (f", window from {args.since}" if args.since else ", all sessions"))
    print(f"# {dispatches:,} dispatch(es) walked corpus-wide (the positive "
          f"control) · {in_window:,} inside the window")
    if not ladders:
        print("\n0 sessions ran a NUMBERED re-audit ladder in this window.")
        print("(The walk found dispatches, so this zero is a measurement, not a "
              "broken filter.)")
        return 0

    depths = sorted(max(v["rounds"]) for v in ladders.values())
    observed = sum(len(v["rounds"]) for v in ladders.values())
    implied = sum(depths)
    deep = sum(1 for d in depths if d >= 5)
    # 🔴 TWO ROUND COUNTS, BOTH PRINTED, BECAUSE THEY ARE NOT THE SAME NUMBER
    # and one of them was published without saying which it was. OBSERVED counts
    # the distinct numbered rounds this walk actually matched; IMPLIED sums each
    # session's deepest round, on the reasoning that a ladder that reached round
    # 8 ran eight of them. Implied is always the larger. Quote one, name it.
    print(f"\nsessions with a numbered ladder : {len(ladders):,}")
    print(f"rounds OBSERVED (matched)       : {observed:,}")
    print(f"rounds IMPLIED (sum of depths)  : {implied:,}")
    print(f"mean deepest round              : {sum(depths)/len(depths):.2f}")
    print(f"ran 5 or more rounds            : {deep} ({100*deep/len(depths):.0f}%)")
    print("\ndepth  sessions")
    hist: dict[int, int] = defaultdict(int)
    for d in depths:
        hist[d] += 1
    for d in sorted(hist):
        print(f"{d:>5}  {'#' * hist[d]} {hist[d]}")

    if args.detail:
        print("\ndate        depth  project / session")
        for (project, session), rec in sorted(
            ladders.items(), key=lambda kv: (kv[1]["date"], max(kv[1]["rounds"])),
            reverse=True,
        ):
            proj = project.replace("-home-zach-workspace-", "")
            print(f"{rec['date']}  {max(rec['rounds']):>5}  {proj} {session[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
