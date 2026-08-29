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
Measured 2026-08-28 on this host: the transcript-archive walk above takes
**30.1 s**; `session-manager --json --lean --no-ch` — the LIVE cross-host tmux
scan — takes **1.82 s** and its rows already carry `task`, `label`, `hotkey`,
`status`, `waiting_probable`, `path` and `claude_session_id`, with `task`
populated on 66 of 72 rows.

So for the question people actually ask — *"find that thing I lost track of, is
it still running, which window, where did it leave off"* — the 30-second archive
search is the WRONG instrument: it answers a question about the past over a
corpus that cannot say whether anything is running now. `--live` runs the live
scan FIRST and falls back to the archive only when the live fleet matched
nothing (or `--deep` forces both).

`--tail N` closes the loop: it prints the scrollback of the resolved window, so
one call answers "where did it leave off" too. It REFUSES on an ambiguous match
rather than picking one — see `window-triage` §7, "Ambiguity is refused, not
guessed".

🔴 AN UNREACHABLE HOST IS NEVER RENDERED AS "NOT RUNNING". Same rule the opencode
leg already follows: if the live scan fails, or a host did not answer, that is
said out loud and the empty LIVE section is labelled UNMEASURED. The one sentence
this tool must never emit is "it is not running" off a look that never happened.

Usage:
  find-session.py <term> [<term> ...] [--project SUBSTR] [--since YYYY-MM-DD]
                  [--limit N] [--all] [--json]
                  [--live [--deep] [--tail N]]

  Terms are ANDed by default (a session must match all). Pass --any to OR them.
  Quote a multi-word term to match it as a phrase: find-session.py "pr 235"

Examples:
  find-session.py redis vpn            # sessions mentioning both redis AND vpn
  find-session.py "pr 235"             # the session where PR 235 was worked
  find-session.py minio --project talos --since 2026-05-01
  find-session.py widget-cache --live            # live first, archive fallback
  find-session.py widget-cache --live --tail 60  # ...and where it left off
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from transcript_search import (  # noqa: E402
    DEFAULT_ROOT, SURFACE_ALL, SURFACE_TEXT, search,
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

# The live scan measured 1.82 s. The ceiling is generous because it makes an
# ssh round trip to the peer host; a timeout is reported as a FAILED scan, never
# as an empty fleet.
LIVE_TIMEOUT_SECS = 90

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_AMBIGUOUS = 3      # --tail could not resolve to exactly one window
EXIT_UNAVAILABLE = 4    # the live fleet was not measured — same code as the sibling


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
    ("claude_only", "--claude-only", "CORPUS selection; live rows have a "
                                     "`runtime` but the scan has no corpus axis"),
    ("opencode_only", "--opencode-only", "CORPUS selection; same reason"),
    ("all", "--all", "widens the TRANSCRIPT search surface only"),
)

# The destinations that DO reach the live leg (or steer both). Pinned beside the
# ledger so the two-way test has both halves of the partition in one place.
LIVE_AWARE_DESTS = frozenset({"terms", "live", "deep", "tail", "limit", "json"})

# Which archive-only flags additionally mean the archive result is the ONLY one
# that can answer the question — a corpus/surface selector. Named separately
# because their notice has to say more than "not filtered by them".
CORPUS_SELECTOR_DESTS = frozenset({"claude_only", "opencode_only", "all"})


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
    if any(dest in CORPUS_SELECTOR_DESTS for dest, _, _ in named):
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


def parse_args(argv=None):
    p = argparse.ArgumentParser(add_help=True, description="Find past Claude Code and opencode sessions by keyword.")
    p.add_argument("terms", nargs="+", help="search terms (ANDed unless --any)")
    p.add_argument("--project", default="", help="only sessions whose cwd/project contains this substring")
    p.add_argument("--since", default="", help="only sessions on/after this date (YYYY-MM-DD)")
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
                   help="scan the LIVE cross-host tmux fleet FIRST (1.8s) and "
                        "only fall back to the 30s transcript walk when nothing "
                        "live matched. Matches task/label/codename — NOT path.")
    p.add_argument("--deep", action="store_true",
                   help="with --live: run the transcript walk TOO, even when "
                        "live windows matched")
    p.add_argument("--tail", type=int, default=None, metavar="N",
                   help="with --live: print the last N scrollback lines of the "
                        "matched window. REFUSES on an ambiguous match rather "
                        "than guessing (exit 3), and lists the candidates.")
    return p.parse_args(argv)


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


def archive_search(a, since):
    """The 30 s two-corpus transcript walk. Unchanged; factored out so the
    `--live` path can decide WHETHER to pay for it."""
    # 🔴 `--all` used to be INERT. Its handler sat behind `if not a.all and typ not in
    # ("user", "assistant")`, twenty lines after an unconditional `if typ not in
    # ("user", "assistant"): continue` had already skipped everything it could have
    # admitted — so the flag the SKILL.md advertises for "tool output" widened nothing.
    # It now selects the search surface, which is the only thing it ever meant.
    surface = SURFACE_ALL if a.all else SURFACE_TEXT

    results = []

    # Search Claude Code transcripts (default)
    if not a.opencode_only:
        cc_results = search(a.terms, root=ROOT, match_any=a.any, since=since,
                            project=a.project, surface=surface, limit=None)
        results.extend(cc_results)

    # Search opencode sessions (default)
    if not a.claude_only:
        try:
            oc_results = search_opencode(a.terms, match_any=a.any, since=since,
                                         project=a.project, limit=None)
            results.extend(oc_results)
        except Exception as e:
            print(f"WARN: opencode search failed: {e}", file=sys.stderr)

    # Re-rank the merged set by the same criteria
    results.sort(key=lambda r: (len(r["matched_terms"]), r["total_hits"], r["last_local"]),
                 reverse=True)
    return results


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
    match is ambiguous — `window-triage` §7, "Ambiguity is refused, not
    guessed": a scrollback printed from the wrong window is an answer that reads
    as correct, which is strictly worse than no answer.

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


def main(argv=None):
    a = parse_args(argv)
    since = None
    if a.since:
        try:
            since = datetime.fromisoformat(a.since)
        except ValueError:
            print(f"bad --since date: {a.since!r} (want YYYY-MM-DD)", file=sys.stderr)
            sys.exit(2)

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
    # 🔴 LIVE FIRST. 1.8 s against the archive walk's 30.1 s, and the live rows
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
        "--deep" if a.deep
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
                "coverage_complete": live_coverage_complete(live),
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
        print(f"ARCHIVE: skipped — the live fleet answered. Pass --deep to "
              f"search the {len(a.terms)}-term transcript walk too (~30s).")
    else:
        print(f"ARCHIVE ({len(results)} matched; ran because: {archive_reason})")
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
            print(f"  No sessions matched: {' '.join(a.terms)}")
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
