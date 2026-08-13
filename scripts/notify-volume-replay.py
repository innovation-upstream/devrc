#!/usr/bin/env python3
"""Replay claude-notify's historical log through the CROSS-SESSION coalescing
gate and report before/after desktop-toast volume per day.

WHY THIS EXISTS: PR #409 measured the notification stream but shipped no
reduction, partly because its first counting method (dunst's "icon not found"
warnings) was structurally incapable of seeing the largest producer — only
cpu-monitor sets an icon, so that census counted one producer and called it a
distribution. This replays the PRODUCER'S OWN log instead, and it drives the
REAL policy function imported from claude-notify.py rather than a paraphrase of
it, so the projection cannot drift away from the shipped behaviour.

WHY THE REPLAY IS EXACT (for the gate as shipped, not for a threshold change):
`claude-notify.log` records one line per turn that passed ALL pre-existing gates
(threshold + per-session cooldown) and reached dispatch, with `desktop=True` when
the desktop toast was the path taken. The new gate is applied strictly AFTER
those gates and only on the desktop path — so the set of `desktop=True` log lines
IS, exactly, the input stream to the new gate. Replaying it is not a model of the
change; it is the change, run on recorded input.

BLIND SPOTS, stated so the numbers carry their own scope:
  1. Dispatch, not delivery. The log records that dunstify was invoked; its exit
     code was never checked. A toast dunst dropped (DND, fullscreen_suppress)
     still appears here. So "before" is an upper bound on what Zach actually saw
     — but it is the correct measure of the load ON dunst, which is the thing
     being reduced.
  2. Only claude-notify. cpu-monitor and earlyoom are counted elsewhere; this
     tool says nothing about them.
  3. Exact for the global gate ONLY. If CLAUDE_NOTIFY_MIN_SECONDS were also
     raised, dropping an event could un-suppress a later same-session event that
     the per-session cooldown had hidden — and hidden events are not in the log,
     so the replay would UNDER-count. That is why this change does not touch the
     threshold. `--threshold` exists to size that option and its output is
     explicitly labelled a lower bound.
  4. Per-host. Run it once per host; the gate state is per-host.

Usage:
    notify-volume-replay.py ~/.claude/claude-notify.log [--window 600] [...]
    notify-volume-replay.py --self-test      # positive + negative controls
"""
import os
import re
import sys
import argparse
import datetime
import collections

# The REAL policy function, loaded from the shipped hook by path (its filename
# has a hyphen, so it is not importable as a module name). Not a copy — if the
# shipped gate changes this projection changes with it, and the tests that pin
# both fail together.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "claude_notify",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "claude-hooks", "claude-notify.py"))
claude_notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claude_notify)

LINE = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] notify '
    r'event=(\S+) project=(\S*) elapsed=(.+?) desktop=(\S+) clawgate=(\S+)'
)


def parse_elapsed(s):
    m = re.match(r'(?:(\d+)m )?(\d+)s\s*$', s.strip())
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2))


def load(path):
    """Parsed `notify` lines. Also returns how many lines did NOT parse — a
    silent parse failure would show up as a reassuring low 'before' count."""
    events, unparsed = [], 0
    with open(path) as fh:
        for ln in fh:
            ln = ln.rstrip("\n")
            if not ln.strip():
                continue
            m = LINE.match(ln)
            if not m:
                unparsed += 1
                continue
            day, tod, event, project, el, desk, claw = m.groups()
            ts = datetime.datetime.strptime(day + " " + tod, "%Y-%m-%d %H:%M:%S")
            events.append(dict(ts=ts, day=day, event=event, project=project,
                               elapsed=parse_elapsed(el),
                               desktop=(desk == "True"),
                               clawgate=(claw == "True")))
    return events, unparsed


def replay(events, window, threshold=None):
    """Drive the REAL coalesce_decide() over the recorded desktop stream.

    Returns (before_per_day, after_per_day, held_total, orphaned) where
    `orphaned` is the count still pending at the end of the log — turns that
    were held and whose summarising toast had not yet fired. That number is the
    honest cost of coalescing: it is delayed information, not lost (it still
    reaches tmux / the agent-ops dashboard / clawgate), but it is delayed
    indefinitely if the stream simply stops.
    """
    before = collections.Counter()
    after = collections.Counter()
    state = {}
    held_total = 0
    for e in events:
        if not e["desktop"]:
            continue
        if threshold is not None and (e["elapsed"] or 0) < threshold:
            continue
        before[e["day"]] += 1
        now = e["ts"].timestamp()
        emit, state, (held, _projects) = claude_notify.coalesce_decide(
            state, now, e["project"], window)
        if emit:
            after[e["day"]] += 1
            held_total += held
    orphaned = int(state.get("held") or 0)
    return before, after, held_total, orphaned


def report(path, window, threshold=None):
    events, unparsed = load(path)
    before, after, held_total, orphaned = replay(events, window, threshold)
    tot_b, tot_a = sum(before.values()), sum(after.values())
    days = len(before) or 1
    print("=" * 72)
    print("%s  window=%ds%s" % (path, window,
                                "" if threshold is None else
                                "  threshold=%ds (LOWER BOUND, see blind spot 3)" % threshold))
    print("parsed=%d  unparsed=%d  desktop=%d  clawgate-only=%d"
          % (len(events), unparsed,
             sum(1 for e in events if e["desktop"]),
             sum(1 for e in events if not e["desktop"])))
    if unparsed:
        print("  !! %d unparsed lines — 'before' is an UNDER-count" % unparsed)
    print("\n day          before   after   reduction")
    for d in sorted(before):
        b, a = before[d], after[d]
        print("  %s   %5d   %5d   %5.1f%%" % (d, b, a, 100.0 * (b - a) / b if b else 0.0))
    print("\n TOTAL        %5d   %5d   %5.1f%%"
          % (tot_b, tot_a, 100.0 * (tot_b - tot_a) / tot_b if tot_b else 0.0))
    print(" per day      %5.1f   %5.1f" % (tot_b / days, tot_a / days))
    print(" turns folded into a surviving toast: %d" % held_total)
    print(" still pending at end of log (delayed, not lost): %d" % orphaned)
    print(" accounting check: before == after + folded + pending -> %d == %d  [%s]"
          % (tot_b, tot_a + held_total + orphaned,
             "OK" if tot_b == tot_a + held_total + orphaned else "MISMATCH"))
    return tot_b, tot_a


def _synth(gaps, project="p"):
    """Build a fake event stream from a list of inter-arrival gaps."""
    t0 = datetime.datetime(2026, 1, 1, 0, 0, 0)
    events, t = [], t0
    for i, g in enumerate(gaps):
        t = t + datetime.timedelta(seconds=g)
        events.append(dict(ts=t, day="2026-01-01", event="Stop",
                           project="%s%d" % (project, i), elapsed=120,
                           desktop=True, clawgate=False))
    return events


def self_test():
    """Controls for the counter ITSELF. A replay tool that reports a comforting
    reduction because it silently counted nothing is exactly the failure mode
    this repo has already shipped twice, so both directions are proven here.
    """
    ok = True

    # POSITIVE CONTROL — the counter must be able to observe a NON-ZERO 'after'.
    # 40 events spaced 1200s apart, window 600 -> every one survives.
    b, a, held, orph = replay(_synth([1200] * 40), window=600)
    print("positive control (gaps 1200s > window 600s): before=%d after=%d held=%d"
          % (sum(b.values()), sum(a.values()), held))
    ok &= (sum(b.values()) == 40 and sum(a.values()) == 40 and held == 0)

    # NEGATIVE CONTROL — it must be able to report a reduction, i.e. go 'red'.
    # 40 events 10s apart, window 600 -> only the first survives; 39 folded.
    b, a, held, orph = replay(_synth([10] * 40), window=600)
    print("negative control (gaps 10s < window 600s):  before=%d after=%d held=%d pending=%d"
          % (sum(b.values()), sum(a.values()), held, orph))
    ok &= (sum(b.values()) == 40 and sum(a.values()) == 1 and orph == 39)

    # DISABLED CONTROL — window 0 must be a strict no-op (the revert path).
    b, a, held, orph = replay(_synth([10] * 40), window=0)
    print("disabled control (window=0):                before=%d after=%d"
          % (sum(b.values()), sum(a.values())))
    ok &= (sum(a.values()) == 40)

    # CONSERVATION — nothing may be invented or vanish.
    b, a, held, orph = replay(_synth([10, 10, 5000, 10, 10, 10]), window=600)
    ok &= (sum(b.values()) == sum(a.values()) + held + orph)
    print("conservation: before=%d == after=%d + folded=%d + pending=%d"
          % (sum(b.values()), sum(a.values()), held, orph))

    print("SELF-TEST: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="*", help="claude-notify.log path(s)")
    ap.add_argument("--window", type=int, default=600,
                    help="global cooldown seconds (default 600; 0 = gate off)")
    ap.add_argument("--threshold", type=int, default=None,
                    help="also simulate a raised CLAUDE_NOTIFY_MIN_SECONDS "
                         "(result is a LOWER bound — see blind spot 3)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the counter's own positive/negative controls")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.logs:
        ap.error("give at least one log path, or --self-test")
    for p in args.logs:
        report(p, args.window, args.threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
