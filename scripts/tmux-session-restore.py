#!/usr/bin/env python3
"""tmux-session-restore — snapshot the live claude/tmux workspace, resume it post-reboot.

The gap: tmux-resurrect/continuum (already on: `@continuum-restore on`) restores every
scratchpad session's windows + working dirs on reboot — but it relaunches a bare shell,
NOT the `claude` conversation that was in each window. This captures which claude session
was where and, after reboot, relaunches `claude --resume <id>` in the right window.

Binding a window to its EXACT session id has TWO sources, and they are not equals:

  1. THE LEDGER (`~/.cache/agent-ledger/claude-p<N>.json`) — a RECORD. Claude Code's
     `agent-ledger-hook.py` is handed the real `session_id` and `transcript_path` by
     the harness and keys the file on its own `$TMUX_PANE`, so a validated record is
     ground truth for that pane: one O(1) file read.
  2. PANE-CONTENT MATCHING (`unique_match_sids`) — an INFERENCE, and the fallback.
     It greps a pane's on-screen text across every transcript in that cwd's project
     dir. Measured on the workbench 2026-09-04 over ONE snapshot of 44 live claude
     panes, against 145 competing transcripts in one project dir: the grep took
     176.6s and bound 34; the ledger took 0.002s and bound 34, agreeing on all 27
     panes both answered. They miss DIFFERENT panes, so together they bind 41.

So the ledger is consulted FIRST and CLAIMS FIRST — a certain binding must never lose
a session id to a guess — and the grep runs only for panes the ledger cannot answer.
The cheat-sheet prints each binding with its source and summary line so you can
eyeball / correct before running restore.

Usage:
  tmux-session-restore.py save      # BEFORE reboot — writes the plan + cheat-sheet
  tmux-session-restore.py restore   # AFTER reboot   — relaunches claude per window
  tmux-session-restore.py show      # print the last saved cheat-sheet

Restore flags:
  --dry-run / -n          show what would happen without sending keys
  --plan PATH             use a custom plan file instead of the default. THIS IS
                          THE RECOVERY PATH: point it at any file in
                          ~/.config/initiatives/restore-plans/ to resume from an
                          older generation after a bad save. `save` prints the
                          exact command whenever a save drops bound session ids.
  --best                  takes no argument. Restore from a RECENT generation
                          that is strictly richer than the current plan, when
                          one exists — i.e. when a save repointed the plan at a
                          workspace that had lost conversations. `restore`
                          reports that situation and names the file either way;
                          this just saves you retyping it.
  --staleness-check [H]   refuse to restore unless the plan is BOTH in step with
                          the saved layout AND produced within H hours of running
                          time (default: 2h). NOT wall-clock age — powered-off
                          time does not count. See `plan_staleness_hours`.

State: ~/.config/initiatives/restore-plan.json  (+ restore-cheatsheet.md), each a
symlink onto the newest file in restore-plans/ — see `cmd_save`.
Scratchpad codenames come from the canonical scripts/tmux-scratch-slots.sh.
"""
from __future__ import annotations

import calendar
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.config/initiatives"))
PLAN = STATE_DIR / "restore-plan.json"
CHEAT = STATE_DIR / "restore-cheatsheet.md"
# Where the immutable per-save generations live, and how many are kept.
#
# 🔴 THE NAME IS A CONSTANT; THE DIRECTORY IS DERIVED FROM `PLAN.parent`, NOT
# FROM `STATE_DIR`, and never bound at import time. Two reasons, both measured
# rather than stylistic:
#   * the generations dir MUST sit beside the pointer, because the pointer is a
#     RELATIVE symlink into it. A module-level `STATE_DIR / …` would keep
#     pointing at the real `~/.config/initiatives` in any test that repoints
#     only `PLAN`, and that test would then write into the operator's LIVE
#     recovery state — the exact thing this change exists to protect.
#   * one derivation means the two can never disagree.
GENERATIONS_DIRNAME = "restore-plans"
# 🔴 RETENTION IS A COUNT, NOT AN AGE, and 672 is 7 DAYS at continuum's
# 15-minute save interval (`nix/programs/tmux/default.nix` -> `tmux-post-save.sh`).
#
# WHY A COUNT. The writer is hook-driven, so an age bound gives NO bound on disk
# at all — turn the save interval down and an "keep 7 days" rule keeps unboundedly
# many files. A count bounds disk deterministically whatever the cadence does.
# The price is that the SPAN is cadence-dependent, and that is stated rather
# than hidden: at 15 min it is 7 days, at 1 min it is 11.2h, and on a host where
# tmux is rarely up it is months.
#
# WHY 7 DAYS, AND WHY 48h (192) WAS NOT ENOUGH. The recovery window has to
# outlast the interval between a bad save and a human NOTICING it. The 48h
# argument was built on a worst case of "a Friday-night crash noticed Sunday"
# (~40h). That is the wrong worst case: on 2026-09-11 the tmux server died twice
# in 24h, and the recovery for the second one leaned on generations from BEFORE
# the first. A window that can be consumed by a single bad weekend leaves no
# margin for a second incident inside it, and the failure mode of being too
# small is total loss of the thing this file exists to preserve. The operator
# asked for 7 days on 2026-09-11.
#
# ⚠ WHAT THIS IS NOT. Storing every save rather than only the most recent is
# ALREADY SHIPPED — that is #1383, which introduced generations at all. This
# constant is PURELY the retention window; nothing about what gets written
# changes.
#
# WHAT IT COSTS. Disk, and it was MEASURED at the new bound rather than
# extrapolated. 2026-09-12, synthesising 672 generations of the live 53-entry
# shape (~20 KB of plan JSON + ~15 KB of cheat-sheet, ~34 KiB each):
#   672 generations = 22.6 MiB on disk   (192 = 6.5 MiB; the live dir today is
#                                         146 generations / 4 MiB)
# The scan cost was measured at the same three points, because a retention bound
# is also a bound on how much `richer_generation` walks on every `restore`:
#   n=138   richer_generation  5–8 ms
#   n=192                      7–8 ms
#   n=672                     25–38 ms   (two runs, host at load ~78)
# `prune_generations` is unaffected in the steady state — one save prunes exactly
# one generation whatever the cap is — and a no-op prune at 672 measured 0.6–0.9 ms.
# ⚠ Those figures are WARM: the directory had just been written. The previously
# recorded 0.26s at 138 generations was a genuinely cold page cache, and the
# ratio measured here (~3–7x from 138 to 672) puts a cold scan at 672 in the
# region of ~1–2s. That is an extrapolation from a measured ratio, not a measured
# number, and it is a once-per-restore cost on a path that already waits up to
# 30s for a tmux server.
KEEP_GENERATIONS = 672
# `restore-plan_20260907T221535.json` — resurrect's own stamp format, so the two
# sets of generations sort and read alike. Lexicographic order IS chronological
# order for this format, which is what lets pruning sort on the NAME rather than
# on an mtime any `cp`/`rsync` could rewrite.
_GEN_STAMP_FMT = "%Y%m%dT%H%M%S"
_GEN_PLAN_RE = re.compile(r"^restore-plan_(\d{8}T\d{6})\.json$")
# The layout `restore` is racing: resurrect's newest state file, via its `last`
# symlink. `plan_staleness_hours` measures the plan against THIS rather than
# against the wall clock — see that docstring for why.
RESURRECT_LAST = Path(os.path.expanduser("~/.tmux/resurrect/last"))
PROJECTS = Path(os.path.expanduser("~/.claude/projects"))
SLOTS_FILE = Path(__file__).resolve().parent / "tmux-scratch-slots.sh"
_SLOT_RE = re.compile(r'"([^":]+):([^":]+):(#[0-9a-fA-F]{6}):([^":]+)"')
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# 🔴 EVERY symbol this file reads off the ledger module — a tuple, not one name,
# because the half-borrow is the hazard. Both of these are the WRITER's rules (the
# file key and the directory it writes into), so restating either here is the
# duplicated predicate that makes this reader look somewhere nobody writes. Add a
# name the moment this file reads a new attribute off `_AL`; a test pins this
# tuple two-way against the source, because the loader guards only what it names.
_BORROWED = ("pane_filename", "LEDGER_DIR")


def _load_agent_ledger(path: Path | None = None):
    """`scripts/lib/agent_ledger.py`, imported by path — or None if unusable.

    This file is a standalone script (run from the working tree by
    `tmux-post-save.sh` and by the `tmux-session-restore` user unit), so there is
    no package to import from; the ledger hook reaches its own copy the same way.

    🔴 We borrow `_BORROWED` rather than restating any of it. If the module cannot
    be USED there is deliberately NO fallback spelling — the ledger simply reports
    nothing and every pane falls through to the grep.

    🔴 "Cannot be used" is TWO cases, and the `_BORROWED` check is what makes the
    promise true for the second. An absent or syntactically broken file raises on
    import and is caught; a module that imports fine while lacking a borrowed
    symbol would be returned as usable. The two names then fail DIFFERENTLY, and
    the quieter failure is the one that made this check a tuple:

      * without `pane_filename`, the unguarded `_AL.pane_filename(...)` in
        `ledger_binding` raises out of `build_plan` and kills `cmd_save` — which
        runs unattended every ~15 min from `tmux-post-save.sh` into a log nobody
        reads, silently freezing the restore plan. Loud, once you read the log.
      * without `LEDGER_DIR`, NOTHING raises. This reader would look in a
        directory of its own invention, every pane would answer `no-record`, and
        the `cmd_save` tally would announce a broken deploy using the one token
        its own legend calls "nothing to fix". Rejecting the module instead makes
        the tally say `no-ledger-module`, which is a token an operator acts on.

    Degrading is the whole point; half-degrading is worse than not trying.

    `path` exists so a test can hand this loader a deliberately-broken module;
    production always takes the default.
    """
    p = Path(path) if path is not None else (
        Path(__file__).resolve().parent / "lib" / "agent_ledger.py")
    try:
        spec = importlib.util.spec_from_file_location("_tsr_agent_ledger", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001 — absent/broken lib: fall back to the grep
        return None
    if any(not hasattr(mod, sym) for sym in _BORROWED):
        return None
    return mod


_AL = _load_agent_ledger()
# No `or "<literal>"` default here, deliberately: see `_BORROWED`. `_AL` is None
# in exactly the cases where the directory cannot be borrowed, and `ledger_binding`
# returns `no-ledger-module` before it ever reads this.
LEDGER_DIR = Path(_AL.LEDGER_DIR) if _AL is not None else None


def run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def codenames() -> dict[str, str]:
    """{session: codename} from the canonical slot table; {} if unreadable."""
    try:
        text = SLOTS_FILE.read_text()
    except OSError:
        return {}
    return {sess: name for sess, _k, _c, name in _SLOT_RE.findall(text)}


def display_session(session: str, codes: dict[str, str]) -> str:
    """Codename for a scratchpad, else `main:<session>` (mirrors initiative-scan)."""
    return codes.get(session, f"main:{session}")


def project_dir_for(cwd: str) -> Path:
    """~/.claude/projects encodes a cwd by replacing every '/' with '-'."""
    return PROJECTS / cwd.replace("/", "-")


def tmux_server_pid() -> str:
    """This tmux server's pid — the ledger's generation key. "" if unmeasured."""
    return run(["tmux", "display-message", "-p", "#{pid}"]).strip()


def ledger_binding(pane_id: str, cwd: str, server_pid: str,
                   directory: Path | None = None) -> tuple[str, str]:
    """The session id the LEDGER records for this pane: `(session_id, reason)`.

    `("", <reason>)` whenever no record survives validation, and the reason token
    names WHICH check rejected it — the tests assert on those tokens, so a broken
    guard fails with its own name rather than with a generic empty string. The
    token also LEAVES this function: `build_plan` records it per pane and
    `cmd_save` prints the tally, because `0 ledger` alone cannot tell an operator
    apart `no-ledger-module` (a deploy problem) from `generation-mismatch` (the
    server restarted) from `no-record` (nothing to fix).

    THE FOUR VALIDATIONS, and what each one is for:

      * `no-session-id` — a record with an empty/absent `session_id` binds nothing.
      * `transcript-missing` — the transcript named by the record must exist on
        disk. `claude --resume <id>` against a deleted transcript fails, and a
        failed resume in the right window is worse than the picker.
      * `generation-mismatch` / `generation-unmeasured` — tmux pane ids restart at
        `%0` when the SERVER does, so yesterday's `%61` record and today's `%61`
        pane collide after exactly the reboot this tool exists for. `tmux_pid` is
        the server pid, constant across a server's windows, so equality rejects
        every record whose recorded pid differs from the live one — which is every
        record EXCEPT those from a server that drew this same pid.
        ⚠ That exception is the gap, and it sits in the very event this guard
        exists for: pids are reused, and a restart resets the pane counter, so a
        record left by a PREVIOUS server that happened to draw today's pid would
        pass all four checks and resume the wrong conversation in a window that
        looks right. How likely that is, is UNMEASURED — and the datum nearest to
        hand does not answer it. (MEASURED 2026-09-04 on the workbench: the live
        server's pid was `4025325` of a `pid_max` of `4194304`, but that server
        started 21.2h AFTER boot, so its pid says nothing about what a server
        started at login draws.) Closing the gap needs a value the WRITER does not
        record today (a boot id, or the server's `/proc` start time), so the
        residual is ACCEPTED — accepted without a rate, not shown to be small.
        🔴 An UNMEASURED live pid rejects too: being unable to check a generation
        is not the same as having checked it.
      * `project-mismatch` — 🔴 THE CROSS-REPO GUARD. The transcript's parent
        directory is the encoded cwd (`project_dir_for`). A record whose transcript
        lives under a DIFFERENT repo's project dir would resume the wrong
        conversation in a window that looks right, which is the single worst
        outcome available here. Compared as the encoded NAME, because that is what
        `project_dir_for` derives from the pane's cwd.

    ⚠ `last_activity_ts` deliberately does NOT gate. Within one tmux server pane
    ids are never reused, and the hook writes on `SessionStart`, so a live claude
    pane's record names the session running in it however long ago it last spoke;
    across servers the pid check already rejects. Any age threshold would
    therefore reject only CORRECT bindings — and it would reject them hardest for
    long-idle windows, which are precisely the ones worth restoring.

    🔴 But the WRITE side already applies one, so `no-record` has a permanent
    FLOOR rather than shrinking to nothing: `agent_ledger.DEFAULT_MAX_AGE` is 7
    days. `write_record` itself does NOT prune, and the two writers do not agree
    on when they do: `agent-ledger-hook.py` prunes on SESSION BOUNDARIES only
    (`PRUNE_EVENTS` is `SessionStart`/`Stop`, a subset of the four events that
    write), while `opencode/plugin/ledger.js` has no boundary hook at all and
    passes `--prune` on EVERY write, throttled to once per SESSION per 30 s
    (`ledger.js`'s `lastWrite` is a Map keyed by sessionID, so N concurrent
    sessions issue up to N prunes in a window, not one). But a prune
    sweeps the WHOLE directory whoever triggers it, so it is OTHER sessions'
    prunes that delete an idle pane's record. Either way a live pane idle
    longer than that ends up with
    no record at all and reports `no-record` forever. Prune keeps re-opening that set
    for exactly the long-idle windows a read-side age gate would also have thrown
    away — which is why the argument above still holds, and why "the unbound set
    shrinks on its own" is true only of the panes that predate the hook.
    """
    if _AL is None:
        return "", "no-ledger-module"
    if not pane_id:
        return "", "no-pane-id"
    d = Path(directory) if directory is not None else LEDGER_DIR
    path = d / _AL.pane_filename("claude", pane_id)
    try:
        rec = json.loads(path.read_text().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return "", "no-record"
    if not isinstance(rec, dict):
        return "", "no-record"
    sid = str(rec.get("session_id") or "").strip()
    if not sid:
        return "", "no-session-id"
    transcript = str(rec.get("transcript_path") or "").strip()
    if not transcript or not Path(transcript).exists():
        return "", "transcript-missing"
    rec_pid = str(rec.get("tmux_pid") or "").strip()
    live_pid = str(server_pid or "").strip()
    if not rec_pid or not live_pid:
        return "", "generation-unmeasured"
    if rec_pid != live_pid:
        return "", "generation-mismatch"
    if Path(transcript).parent.name != project_dir_for(cwd).name:
        return "", "project-mismatch"
    return sid, "ok"


def jsonls_by_recency(cwd: str) -> list[Path]:
    """A cwd's project-dir jsonl paths, newest first."""
    d = project_dir_for(cwd)
    if not d.is_dir():
        return []
    files = [f for f in d.glob("*.jsonl")]
    files.sort(key=lambda f: f.stat().st_mtime if f.exists() else 0, reverse=True)
    return files


def unique_match_sids(target: str, cwd: str) -> list[str]:
    """Session ids a pane's on-screen content matches UNIQUELY, best (longest) first.

    claude appends-and-closes its jsonl (no held fd) and the session summary isn't
    stored, so the reliable bind is content: capture the pane, take distinctive lines,
    and keep only fragments that appear in EXACTLY ONE jsonl — those pin a session with
    certainty (a pane shows its own conversation, which is logged in its own jsonl). A
    fragment hitting several files is ambiguous (shared handoff text, boilerplate) and
    dropped. Returns [] when nothing is certain — the caller then leaves that window to
    the interactive `claude --resume` picker rather than guessing wrong.
    """
    files = jsonls_by_recency(cwd)
    if not files:
        return []
    cap = _ANSI.sub("", run(["tmux", "capture-pane", "-t", target, "-p", "-S", "-200"]))
    frags = sorted(
        {ln.strip() for ln in cap.splitlines()
         if len(ln.strip()) >= 40 and sum(c.isalnum() for c in ln) >= 25},
        key=len, reverse=True)[:20]
    paths = [str(f) for f in files]
    seen: set[str] = set()
    out: list[str] = []
    for frag in frags:
        hits = run(["grep", "-lF", "--", frag, *paths]).split()
        if len(hits) == 1:
            sid = Path(hits[0]).stem
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
    return out


def first_user_line(session_id: str, cwd: str) -> str:
    """A short human hint for a session — its first real user message (for the sheet)."""
    f = project_dir_for(cwd) / f"{session_id}.jsonl"
    try:
        with open(f, errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("type") != "user":
                    continue
                msg = o.get("message") or {}
                c = msg.get("content")
                txt = c if isinstance(c, str) else (
                    next((b.get("text", "") for b in c
                          if isinstance(b, dict) and b.get("type") == "text"), "")
                    if isinstance(c, list) else "")
                txt = " ".join(txt.split())
                if txt and not txt.startswith(("<", "Caveat:", "[Request")):
                    return txt[:70]
    except OSError:
        pass
    return ""


def live_claude_panes() -> list[dict]:
    """Live claude panes: [{pane_id, session, window, cwd, title}], stable order.

    `#{pane_id}` leads the format because it is the ledger's file key; `#{pane_title}`
    stays last because it is the one field whose content is arbitrary.
    """
    out = run(["tmux", "list-panes", "-a", "-F",
               "#{pane_id}\t#{session_name}\t#{window_index}\t#{pane_current_path}"
               "\t#{pane_current_command}\t#{pane_title}"])
    panes = []
    for ln in out.splitlines():
        p = ln.split("\t")
        if len(p) < 6 or p[4] != "claude":
            continue
        panes.append({"pane_id": p[0], "session": p[1], "window": p[2],
                      "cwd": p[3], "title": p[5]})
    return panes


def build_plan() -> list[dict]:
    """Bind each live claude window to the EXACT session it runs.

    TWO PASSES, AND THE ORDER IS THE POINT. Pass 1 takes each pane's ledger record
    (a RECORD, see `ledger_binding`); pass 2 runs the pane-content grep only for the
    panes pass 1 could not answer. That ordering buys two things at once:

      * 🔴 CORRECTNESS — a certain binding claims its session id BEFORE any guess
        can. Interleaved, a fuzzy match on pane B could claim the very id the ledger
        knows belongs to pane A, and A would then fall through to the picker while B
        resumed A's conversation. Ledger-first makes that unreachable.
      * SPEED — the grep is never even called for a ledger-bound pane. That is the
        whole performance claim, and it is pinned behaviourally by a test that
        injects a matcher which raises.

    Consequence: for one pane the ledger and the grep can never disagree, because on
    a valid record the grep does not run. A session once claimed is never reused, so
    two windows can't collapse onto one conversation; a window with no certain,
    unclaimed binding gets an empty id and the interactive picker at restore time.
    """
    codes = codenames()
    panes = live_claude_panes()
    server_pid = tmux_server_pid()
    bound: dict[int, tuple[str, str]] = {}
    reasons: dict[int, str] = {}
    claimed: set[str] = set()

    # Pass 1 — the ledger. Certain, so it claims first.
    for i, p in enumerate(panes):
        sid, reason = ledger_binding(p.get("pane_id", ""), p["cwd"], server_pid)
        reasons[i] = reason
        if sid and sid not in claimed:
            claimed.add(sid)
            bound[i] = (sid, "ledger")

    # Pass 2 — the grep, for whatever is left.
    for i, p in enumerate(panes):
        if i in bound:
            continue
        cands = unique_match_sids(f"{p['session']}:{p['window']}", p["cwd"])
        sid = next((s for s in cands if s not in claimed), "")
        if sid:
            claimed.add(sid)
            bound[i] = (sid, "fuzzy")

    plan = []
    for i, p in enumerate(panes):
        sid, source = bound.get(i, ("", ""))
        plan.append({
            "session": p["session"],
            "window": p["window"],
            "codename": display_session(p["session"], codes),
            "cwd": p["cwd"],
            "session_id": sid,
            "bind_source": source,
            # Why the LEDGER did or did not answer for this pane — carried out so
            # `cmd_save` can print a tally an operator can act on. Independent of
            # `bind_source`: a pane can read `ok` here and still be `fuzzy`/unbound
            # if another pane claimed that session id first.
            "ledger_reason": reasons.get(i, ""),
            "title": (p["title"] or "").strip(),
            "hint": first_user_line(sid, p["cwd"]) if sid else "",
        })
    plan.sort(key=lambda e: (e["codename"], int(e["window"]) if e["window"].isdigit() else 0))
    return plan


def cheat_sheet(plan: list[dict]) -> str:
    lines = ["# Session restore cheat-sheet",
             "",
             "tmux-continuum restores your sessions/windows/cwds on reboot; this maps each",
             "window back to its claude conversation. Run `tmux-session-restore.py restore`",
             "to auto-resume, or resume by hand with the commands below.",
             ""]
    for e in plan:
        loc = f"{e['codename']}:{e['window']}"
        lines.append(f"## {loc}  —  {e['title'] or '(untitled)'}")
        lines.append(f"- cwd: `{e['cwd']}`")
        if e["session_id"]:
            src = e.get("bind_source") or "fuzzy"
            lines.append(f"- resume: `cd {e['cwd']} && claude --resume {e['session_id']}`"
                         f"  ({src})")
            if e["hint"]:
                lines.append(f"- first msg: _{e['hint']}_")
        else:
            lines.append(f"- resume: `cd {e['cwd']} && claude --resume`  (no session guess — pick from the list)")
        lines.append("")
    return "\n".join(lines)


def generations_dir() -> Path:
    """The directory holding the immutable per-save generations.

    Derived from `PLAN.parent` on every call — see `GENERATIONS_DIRNAME`.
    """
    return PLAN.parent / GENERATIONS_DIRNAME


def generation_stamp(when: float | None = None) -> str:
    """`20260907T221535` for a POSIX timestamp (now, if none). **UTC.**

    🔴 UTC, NOT LOCAL TIME, AND THAT IS LOAD-BEARING — NOT A STYLE CHOICE.
    Every ordering guarantee in this file rests on the stamp being monotonic,
    because `list_generations` sorts on the NAME and pruning deletes from the
    older end. Local time is NOT monotonic: it repeats an hour at every DST
    fall-back and can step backwards whenever the zone changes.

    MEASURED on this host's own zone (`America/Winnipeg`), 4 of 4 fixtures, in
    the production call sequence: with a local-time stamp,
    `free_generation_stamp`'s anchor landed BEHIND `now` inside the repeated
    hour, `stamp > newest` could then never be satisfied, and the fall-through
    returned an OCCUPIED stamp. A bound session id and its cheat-sheet were
    destroyed, rc 0, nothing printed. That is the mutable-file defect this whole
    file exists to remove, reappearing once a year in the mechanism built to
    remove it.

    The resurrect-style `%Y%m%dT%H%M%S` shape is kept because it is what the
    surrounding tooling reads; only the CLOCK changed. Nothing parses these
    names as local time — `list_generations` compares them as strings and the
    only parse is the anchor below, which now uses `calendar.timegm` to match.
    """
    return time.strftime(_GEN_STAMP_FMT,
                         time.gmtime(time.time() if when is None else when))


def generation_paths(stamp: str) -> tuple[Path, Path]:
    """(plan, cheat-sheet) paths for one generation stamp."""
    d = generations_dir()
    return (d / f"restore-plan_{stamp}.json",
            d / f"restore-cheatsheet_{stamp}.md")


def free_generation_stamp(when: float | None = None, limit: int = 60) -> str:
    """A stamp that is FREE and STRICTLY NEWER than every generation present.

    🔴 THE STAMP HAS ONE-SECOND RESOLUTION AND THE INCIDENT WAS A SAME-SECOND
    WRITE. Two saves inside one second — a manual `save` racing the 15-minute
    continuum hook — would otherwise land on the SAME stamp, and the second
    would overwrite the first: the mutable-file defect, reintroduced inside the
    mechanism built to remove it, and worse than the original because the shrink
    report would then name a "previous generation" that is the file just
    clobbered.

    🔴 FREE IS NOT ENOUGH — IT MUST ALSO BE THE NEWEST, AND THAT IS A MEASURED
    BUG, NOT A HYPOTHETICAL. A first draft returned the first UNOCCUPIED stamp,
    and `test_pruning_keeps_exactly_the_newest_generations` went red: pruning
    had just freed the oldest slot, this function handed that freed slot back,
    the fresh generation therefore sorted OLDEST, `prune_generations` selected
    it as doomed and `protect` (rightly) refused — so the run reported `4 kept
    (max 3), 0 pruned` and the retention cap silently stopped being enforced.
    Any backwards clock step reproduces it without the same-second race. A
    strictly-increasing name is the invariant `list_generations`'s name-sort and
    all of pruning rest on, so it is established HERE rather than repaired
    downstream.

    Cost of stepping forward: the generation is misdated by at most `limit`
    seconds.

    🔴 PAST `limit` THIS RAISES. It used to return the last candidate, which
    silently OVERWROTE an existing generation — measured destroying a bound
    session id and its cheat-sheet at rc 0 with nothing printed, while
    `prune_generations` reported `0 pruned`. The docstring called that "a
    bounded, VISIBLE loss"; it was not visible by any means. A save that fails
    loudly costs one save; a save that clobbers costs the bound plan that save
    existed to protect, which is the entire subject of this file. So the trade
    is inverted deliberately: raise, and let the caller's failure be seen.

    🔴 THE FREE-CHECK AND THE CLAIM ARE ONE ATOMIC OPERATION (`O_CREAT|O_EXCL`),
    NOT `exists()` THEN WRITE. `scripts/tmux-post-save.sh` launches `save`
    BACKGROUNDED AND DISOWNED WITH NO LOCK, so a manual save genuinely races the
    15-minute continuum hook — the race this function's first paragraph names.
    An `exists()` test cannot close it: both processes see the slot free, both
    return the same stamp, and then they interleave over fixed temp names.
    Measured with a two-process interleaving: `FileNotFoundError` out of
    `os.replace`, a generation holding one process's bytes under the other's
    rename, and `FileExistsError` out of `os.symlink`. Creating the plan file
    exclusively makes the winner unambiguous and the loser step to the next
    second.
    """
    base = time.time() if when is None else when
    present = list_generations()
    newest = present[-1] if present else ""
    # 🔴 ANCHOR ON THE NEWEST GENERATION, DO NOT STEP TOWARDS IT. Stepping alone
    # covers a base that is a few seconds behind and NOTHING else: a clock five
    # months behind (a battery-flat RTC, a restored backup, a container without
    # NTP) exhausts `limit` and falls through still older than the newest
    # generation. Measured — `test_a_new_stamp_is_never_older_than_an_existing_
    # generation` caught exactly that with a step-only implementation. Jumping
    # the base past `newest` makes the invariant independent of how far behind
    # the clock is; the loop below then only has to resolve occupancy.
    if newest:
        try:
            # `calendar.timegm`, not `time.mktime` — the stamp is UTC now, and
            # `mktime` would reinterpret it as local, shifting the anchor by the
            # UTC offset and (inside a DST fall-back) landing it BEHIND `now`.
            base = max(base, calendar.timegm(time.strptime(newest, _GEN_STAMP_FMT)) + 1)
        except ValueError:
            # An unparseable name cannot have come from `generation_stamp`, and
            # `_GEN_PLAN_RE` already rejects the wrong SHAPE — so this is a
            # well-shaped impossible date. Leave the base alone and let the
            # step loop do what it can rather than crash the save.
            pass
    generations_dir().mkdir(parents=True, exist_ok=True)
    for i in range(limit + 1):
        stamp = generation_stamp(base + i)
        if stamp <= newest:
            continue
        try:
            # The CLAIM. Succeeds for exactly one racer; the other gets EEXIST
            # and steps. The empty file it leaves is overwritten by the caller's
            # `_write_atomic` a moment later.
            fd = os.open(generation_paths(stamp)[0],
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        os.close(fd)
        return stamp
    raise RuntimeError(
        f"could not claim a free generation stamp newer than {newest!r} within "
        f"{limit}s of {generation_stamp(base)!r}. REFUSING rather than "
        f"overwriting an existing generation — the previous plan and its bound "
        f"session ids are intact. Check {generations_dir()} for a clock jump or "
        f"a stuck concurrent save.")


def list_generations() -> list[str]:
    """Every generation stamp present, OLDEST FIRST.

    Sorted on the NAME, which for `_GEN_STAMP_FMT` is chronological — an mtime
    sort would reorder the whole set after any `cp`/`rsync` that did not
    preserve stamps, and pruning would then delete the wrong end.

    Keyed on the PLAN file only. A cheat-sheet with no plan beside it is not a
    generation you can restore from, so it is not counted as one; `prune_
    generations` still unlinks it when its stamp is pruned.
    """
    try:
        names = os.listdir(generations_dir())
    except OSError:
        return []
    return sorted(m.group(1) for m in
                  (_GEN_PLAN_RE.match(n) for n in names) if m)


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` via a temp file + rename.

    🔴 `write_text` TRUNCATES FIRST. A save killed between the truncate and the
    write leaves a zero-byte plan — a second, smaller shape of the same
    data-loss defect this file's generations exist to close, and one that would
    otherwise apply to every generation as it is created. `os.replace` is atomic
    within a directory, so a reader sees either the old file or the whole new
    one, never a half.

    🔴 THE TEMP NAME CARRIES THE PID. A fixed `.tmp` is shared state between two
    concurrent saves — and they DO run concurrently, because
    `scripts/tmux-post-save.sh` backgrounds and disowns `save` with no lock.
    Measured with a two-process interleaving on a fixed name: the second
    `os.replace` raised `FileNotFoundError` (the first had already renamed the
    shared temp away), and the surviving generation held one process's bytes
    under the other's rename. Per-process names make the two writes independent.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except BaseException:
        # Do not leave this run's temp behind on failure: it does not match
        # `_GEN_PLAN_RE`, so `prune_generations` would never reap it.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _point_at(link: Path, target: Path) -> None:
    """Atomically make `link` a RELATIVE symlink to `target`.

    Relative so the whole state dir can be copied or moved as a unit.

    `os.replace` onto the link path is what makes this survive interruption AND
    what performs the one-time migration: the destination may be a symlink (the
    ordinary case) or a REGULAR FILE (a host that last saved with the
    pre-generations writer), and rename replaces either without a window in
    which the pointer is missing.
    """
    # 🔴 PER-PROCESS, for the same reason as `_write_atomic`: with a fixed
    # `.new` name a concurrent save raised `FileExistsError` out of `os.symlink`
    # — measured. The unlink-then-symlink pair is not atomic, so two processes
    # sharing the name interleave between them.
    tmp = link.with_name(f"{link.name}.{os.getpid()}.new")
    if os.path.lexists(tmp):        # lexists: a DANGLING leftover link is still there
        os.unlink(tmp)
    os.symlink(os.path.relpath(target, link.parent), tmp)
    os.replace(tmp, link)


def adopt_pre_generation_files() -> Path | None:
    """Preserve a REGULAR-FILE plan/cheat-sheet as a generation. Returns its plan path.

    🔴 THE DEPLOY OF THIS CHANGE MUST NOT ITSELF BE THE BAD SAVE. On a host that
    has been running the old writer, `restore-plan.json` is a real file holding
    the last plan — possibly the only good one. The first `cmd_save` under the
    new writer would repoint that path at a fresh generation and unlink the old
    inode, losing exactly what generations exist to keep. So copy it in first,
    stamped from its OWN mtime so it sorts into place chronologically.

    A no-op once the pointer is a symlink, so it is safe to call every save.
    """
    if os.path.islink(PLAN) or not PLAN.exists():
        return None
    generations_dir().mkdir(parents=True, exist_ok=True)
    stamp = free_generation_stamp(PLAN.stat().st_mtime)
    gplan, gcheat = generation_paths(stamp)
    _write_atomic(gplan, PLAN.read_text())
    # The cheat-sheet is the SAME defect with the same writer — carry it too, but
    # only if it is likewise a real file, and never invent one from a plan whose
    # cheat-sheet is already gone.
    if not os.path.islink(CHEAT) and CHEAT.exists():
        _write_atomic(gcheat, CHEAT.read_text())
    return gplan


def prune_generations(keep: int | None = None,
                      protect: tuple[str, ...] = ()) -> list[str]:
    """Delete all but the newest `keep` generations. Returns the stamps removed.

    🔴 `protect` NAMES THE GENERATION THE POINTER IS ON. Pruning is the only
    code here that deletes, so it is the only code that can recreate the defect:
    a `keep` of 0, a clock that jumped backwards so the new stamp sorts oldest,
    or a future caller reordering the write and the prune would each unlink the
    file `restore-plan.json` points at, leaving a DANGLING pointer and no
    current plan. Refusing to delete a protected stamp makes that unreachable
    regardless of how the ordering argument is disturbed.

    🔴 A STAMP IS REPORTED REMOVED ONLY IF ITS PLAN FILE ACTUALLY WENT. The
    unlink `OSError` used to be swallowed and the stamp appended regardless, so
    the line `cmd_save` prints was internally contradictory — measured
    `2 kept (max 1), 1 pruned` while NOTHING had been pruned. Worse than a wrong
    number: a PERSISTENT unlink failure (a read-only remount, an immutable flag,
    a permissions change) gives unbounded growth reported as healthy retention on
    every single save — a reassuring count from a pruner wired to nothing.
    A missing cheat-sheet is NOT a failure: `list_generations` keys on the plan
    file, so a stamp with no cheat-sheet beside it is already half-gone.
    """
    keep = KEEP_GENERATIONS if keep is None else keep
    stamps = list_generations()
    doomed = stamps[:max(0, len(stamps) - keep)]
    removed = []
    for stamp in doomed:
        if stamp in protect:
            continue
        gplan, gcheat = generation_paths(stamp)
        try:
            gplan.unlink()
        except FileNotFoundError:
            pass                    # already gone: the outcome we wanted
        except OSError:
            continue                # still there — do NOT claim it was pruned
        try:
            gcheat.unlink()
        except OSError:
            pass                    # not what `list_generations` counts
        removed.append(stamp)
    return removed


def read_plan(path: Path) -> list[dict] | None:
    """A saved plan as a list, or None if it is absent / unreadable / not a list.

    Used to compare a new save against the one it replaces. Every failure is one
    answer — "there is nothing to compare against" — because the comparison is
    advisory: an unreadable previous plan must never stop the new one being
    written.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def bound_ids(plan: list[dict]) -> set[str]:
    """The session ids a plan can actually resume — empty ids are not bindings."""
    return {e.get("session_id") for e in plan if e.get("session_id")}


def cmd_save() -> int:
    """Write a NEW generation and repoint `restore-plan.json` at it.

    🔴 THIS IS THE FIX FOR THE MEASURED LOSS OF 2026-09-06. The old writer
    overwrote one mutable file in place:

        21:47:17  a good plan — 47 entries, 46 carrying a bound session id
        21:54:21  the tmux server died, taking 47 claude conversations
        22:09:40  a continuum autosave fired on the DEGRADED post-crash
                  workspace and this function overwrote the plan with 10
                  entries. The cheat-sheet went in the same second.
                  NO BACKUP EXISTED.

    The conversations were recovered only because tmux-resurrect keeps its saves
    TIMESTAMPED and each pane line happens to carry a full `claude --resume
    <id>`. THAT ASYMMETRY WAS THE BUG: a bad save cost the layout nothing and
    the bindings everything. This mirrors resurrect — an immutable, timestamped
    generation per save plus a pointer at the path every reader already knows.

    🔴 WHAT THIS DELIBERATELY DOES **NOT** DO: refuse a shrinking save. The
    operator closing windows, or genuinely working in fewer, is ordinary use, so
    a guard that refused on a falling entry count would fire on ordinary use and
    train everyone to bypass it. The degraded save of 22:09:40 is written here
    too — it is simply no longer the only copy. What the shrink gets is a
    WARNING naming the previous generation and the exact command to restore from
    it, which is the thing the operator had to reconstruct by hand.
    """
    plan = build_plan()
    if not plan:
        # An empty plan was already never written, and that stays: a save with
        # no live panes must not become a generation, or a single tmux-less
        # moment would push the real ones toward the retention cliff.
        print("no live claude panes found — nothing to snapshot", file=sys.stderr)
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    generations_dir().mkdir(parents=True, exist_ok=True)

    # Read BOTH facts about the outgoing plan before anything moves.
    previous = read_plan(PLAN)
    previous_gen = Path(os.path.realpath(PLAN)) if PLAN.exists() else None
    adopted = adopt_pre_generation_files()
    if adopted is not None:
        # `realpath` of a regular file is the file itself, and that inode is
        # about to be replaced by the pointer — name the copy instead.
        previous_gen = adopted

    stamp = free_generation_stamp()
    gplan, gcheat = generation_paths(stamp)
    _write_atomic(gplan, json.dumps(plan, indent=2))
    _write_atomic(gcheat, cheat_sheet(plan))
    _point_at(PLAN, gplan)
    _point_at(CHEAT, gcheat)
    # 🔴 PROTECT THE PREVIOUS GENERATION TOO, NOT JUST THE NEW ONE. The shrink
    # report below names `previous_gen` as the thing to restore from, and with
    # `protect=(stamp,)` this very call could delete it first — measured at
    # KEEP_GENERATIONS=1: the report named a path whose `exists()` was False.
    # This file's own rule is that a warning pointing at the wrong file is worse
    # than no warning, and lowering the cap is the natural response to disk
    # pressure, so the reachable-today argument is not a defence.
    protected = (stamp,)
    if previous_gen is not None:
        m = _GEN_PLAN_RE.match(previous_gen.name)
        if m:
            protected += (m.group(1),)
    pruned = prune_generations(protect=protected)

    n_ledger = sum(1 for e in plan if e.get("bind_source") == "ledger")
    n_fuzzy = sum(1 for e in plan if e.get("bind_source") == "fuzzy")
    print(f"saved {len(plan)} windows → {gplan}")
    print(f"current → {PLAN} (→ {gplan.name})")
    print(f"generations: {len(list_generations())} kept "
          f"(max {KEEP_GENERATIONS}), {len(pruned)} pruned")
    print(f"bound: {n_ledger} ledger, {n_fuzzy} pane-content, "
          f"{len(plan) - n_ledger - n_fuzzy} unbound (picker at restore)")
    # 🔴 THE SHRINK REPORT — A WARNING, NEVER A REFUSAL. The plan is already
    # written by the time this runs, on purpose; see this function's docstring
    # for why refusing a shrink is the wrong shape. Keyed on BOUND SESSION IDS
    # rather than on the entry count, because ids are the payload a bad save
    # actually costs you: a save that drops five UNBOUND windows lost nothing
    # resumable and must stay quiet, or the line becomes noise and stops being
    # read. The recovery command is spelled out because reconstructing it by
    # hand under pressure is exactly what the 2026-09-06 incident cost.
    dropped = bound_ids(previous or []) - bound_ids(plan)
    if dropped and previous_gen is not None:
        print(f"🔴 this save DROPS {len(dropped)} bound session id(s) the previous "
              f"plan carried ({len(previous)} entries → {len(plan)}).",
              file=sys.stderr)
        print("   NOTHING IS LOST — the previous generation is retained. "
              "To resume from it instead:", file=sys.stderr)
        print(f"     tmux-session-restore.py restore --plan {previous_gen}",
              file=sys.stderr)
    # The counts above cannot tell `0 ledger` apart between a missing module, a
    # restarted server and simply no records — and the first two are what an
    # operator would act on. Sorted by token so the line's shape is stable.
    #
    # `unrecorded` is UNREACHABLE today and stays on purpose: `build_plan` assigns
    # every index and `cmd_save` always builds the plan rather than reading one off
    # disk, so a mutant deleting this default survives. Keeping it costs one `or`
    # and buys the right failure shape — this runs unattended every ~15 min into a
    # log nobody reads, so a future refactor that stops assigning reasons must
    # degrade to a token that is VISIBLY none of `ledger_binding`'s own
    # (`unrecorded=44` reads as broken) rather than raise a KeyError that freezes
    # the restore plan, or print `None=44`. The same argument covers
    # `reasons.get(i, "")` in `build_plan`.
    tally = Counter(e.get("ledger_reason") or "unrecorded" for e in plan)
    print("ledger reasons: "
          + ", ".join(f"{tok}={n}" for tok, n in sorted(tally.items())))
    print(f"cheat-sheet → {CHEAT}\n")
    print(cheat_sheet(plan))
    return 0


def cmd_show() -> int:
    if not CHEAT.exists():
        print("no saved snapshot — run `save` first", file=sys.stderr)
        return 1
    print(CHEAT.read_text())
    return 0


def tmux_session_exists(name: str) -> bool:
    """Does a session named EXACTLY `name` exist?

    🔴 `tmux has-session -t <name>` IS NOT AN EXACT TEST. tmux resolves the
    target by prefix and fnmatch, so it answers rc 0 for a session that merely
    STARTS WITH the name. MEASURED on a private socket holding only `sctest`:

        has-session -t sct    -> rc 0      (prefix)
        has-session -t 'sc*'  -> rc 0      (fnmatch)
        has-session -t zzznope -> rc 1

    On this operator's own plan that is live, not theoretical: `scratch2` and
    `scratch20` both exist. If `scratch20` is restored and `scratch2` is not,
    this returned True for `scratch2`, `new-session` was skipped, and every
    `scratch2` conversation was silently dropped while `new-window -t scratch2:5`
    created a window inside `scratch20`.

    `list-sessions -F '#{session_name}'` enumerates instead of resolving, so the
    comparison can be exact.
    """
    out = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return False
    return name in out.stdout.split("\n")


def window_state(target: str) -> tuple[bool, str]:
    """(window exists?, its pane_current_command) for a `session:window` target.

    🔴 `tmux display-message -t <session>:<MISSING-INDEX>` DOES NOT FAIL. IT
    ANSWERS ABOUT THE SESSION'S CURRENT WINDOW AND EXITS 0. That is what this
    function used, and it made the predicate report `(True, 'zsh')` for a window
    that does not exist.

    MEASURED 2026-09-11 on the live server, with a control — `scratch3` had
    exactly one window, index 1:

        tmux display-message -p -t scratch3:1  -> '1:zsh'  rc=0
        tmux display-message -p -t scratch3:87 -> '1:zsh'  rc=0   <- the lie
        tmux display-message -p -t scratch3:99 -> '1:zsh'  rc=0   <- the lie

    THE COST, measured the same night. A tmux server died with 52 bound
    conversations. continuum restored the SESSIONS but only ONE WINDOW each, so
    33 of the plan's windows were absent. `cmd_restore` asked this predicate,
    was told every window existed, therefore never called `new-window`, and
    `send-keys -t <session>:<missing>` ALSO resolves to the current window — so
    50 resumes piled into the ~18 windows that did exist, each overwriting the
    last. Result: `relaunched 50 windows` and **one** conversation running.
    `_verify_sends` then re-read through this same lying predicate.

    `list-windows` ENUMERATES a session's windows instead of resolving an index
    against it, so the INDEX half of the question becomes exact.

    🔴 BUT `-t <session>` IS STILL A TMUX TARGET, AND TMUX PREFIX- AND
    FNMATCH-MATCHES SESSION NAMES. An earlier version of this docstring claimed
    "there is nothing for tmux to be helpful about", and that was false for the
    session component. MEASURED on a private socket where only `sctest` existed:

        list-windows -t sct    -> sctest|1   rc=0     (prefix match)
        list-windows -t 'sc*'  -> sctest|1   rc=0     (fnmatch)
        has-session  -t sct    -> rc=0                (so new-session is skipped)

    That is reachable on this operator's own plan, which holds BOTH `scratch2`
    (windows 1-9) and `scratch20` (windows 1-3). In a partial continuum restore
    where `scratch20` came back and `scratch2` did not, asking about
    `scratch2:1` enumerates SCRATCH20's windows and answers `(True, ...)` — a
    false PRESENT of exactly the class this function exists to remove, one level
    up. `scratch2` is then never created and its conversations are silently
    skipped, while `new-window -t scratch2:5` creates a window inside
    `scratch20`.

    So the session name is compared EXACTLY too, against `#{session_name}` from
    the same output. tmux tells us which session it actually chose; we simply
    stop believing it chose ours. Do not drop that field, and do not
    "simplify" this back to `display-message` — a target-resolving command can
    never answer an existence question.
    """
    sess, _, win = target.partition(":")
    if not win:
        return (False, "")
    # `-F` keeps the triple on one line so a window whose command contains
    # whitespace cannot shift the parse; the session name is carried so the
    # caller's name can be verified rather than assumed.
    out = run(["tmux", "list-windows", "-t", sess,
               "-F", "#{session_name}\t#{window_index}\t#{pane_current_command}"])
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        got_sess, idx, cmd = parts[0], parts[1], parts[2]
        if got_sess == sess and idx == win:
            return (True, cmd.strip())
    return (False, "")


def claude_command() -> str:
    """The `claude` binary to send, resolved to an ABSOLUTE path when possible.

    🔴 A BARE `claude` IS RESOLVED BY THE TARGET PANE'S PATH, NOT BY OURS, AND
    THAT PATH IS ROUTINELY BROKEN IN EXACTLY THE PANES THIS SCRIPT WRITES TO.
    MEASURED 2026-09-11: after a server death, every pane tmux-continuum had
    restored answered

        claude: command not found
        hostname: command not found

    — a restored pane does not re-run the login shell's profile, so it can come
    back with a PATH that predates the current home-manager generation (the
    `~/.nix-profile` blanking this repo's MEMORY.md documents is the same
    family). The sends were correct; the panes could not run them.

    Resolving here fixes that for every send, because THIS process is started by
    the systemd unit with a known-good PATH. `shutil.which` follows that PATH and
    returns a `/nix/store/...` path that does not depend on the pane at all.

    🔴 `shutil.which` ALONE IS NOT ENOUGH, AND RELYING ON IT SHIPPED THIS FIX
    INERT IN THE ONE CONTEXT THAT MATTERS. The systemd unit pins its own PATH —
    `nix/home.nix`: `makeBinPath [ python312 tmux coreutils ]` — and `claude` is
    not on it. MEASURED under exactly that PATH:

        env -i PATH=<the unit's three entries> python3 -c 'shutil.which("claude")'
        -> None

    So under the unit — which is how a restore runs after a crash or a boot —
    `which` finds nothing and the bare name goes back on the wire, which is the
    very failure this function exists to remove.

    🔴 THE PROFILE SYMLINK IS RESOLVED TO ITS STORE PATH, NOT SENT AS-IS.
    `~/.nix-profile/bin/claude` is correct but MUTABLE: this repo's MEMORY.md
    records that a home-manager switch writes two generations and the
    intermediate one drops every `home.packages` binary for ~1s, so a command
    naming the profile path can miss. `realpath` pins the immutable
    `/nix/store/...` target at the moment we build the line.

    Order: PATH first (honours an override and a dev shell), then the profile,
    then the bare name — because a bare `claude` is what shipped for months and
    works wherever PATH is intact, so an unresolvable lookup must not turn a
    working restore into no restore.
    """
    found = shutil.which("claude")
    if found:
        # 🔴 realpath HERE TOO. On this host `which` returns
        # /home/zach/.nix-profile/bin/claude — the MUTABLE profile symlink this
        # function's own docstring warns about. Protecting only the fallback
        # branch left the branch that actually fires interactively unprotected.
        return os.path.realpath(found)
    profile = Path(os.path.expanduser("~/.nix-profile/bin/claude"))
    if profile.exists():
        # realpath: pin the store path, not the mutable profile symlink.
        return os.path.realpath(profile)
    return "claude"


def no_tmux_server_to_restore_into() -> bool:
    """True when there is no tmux server this process may safely restore into.

    🔴 THIS IS THE GUARD FOR THE MEASURED, SILENT, TOTAL LOSS OF 2026-09-06.
    The mechanism, confirmed from the journal and then by an isolated
    experiment — NOT inferred from the empty scrollback, which cannot tell the
    two candidate stories apart:

      On a cold boot nothing else has started tmux. `cmd_restore` below would
      call `tmux new-session -d` itself, so the server was born INSIDE this
      unit's cgroup. The 43 sends were delivered *successfully* into it. Then
      ExecStart returned, and with `Type=oneshot`, `RemainAfterExit=no` and
      `KillMode=control-group` systemd tore the cgroup down, taking the server
      and every claude process with it. The unit reported `Result=success`.

    The journal is what separates the stories: `Started tmux child pane N
    launched by process <pid>` arrived in TWO cohorts — 17 lines at the unit's
    own timestamp naming the pid the unit started, then 56 lines 24s later
    naming a DIFFERENT pid. Two servers, not one unready one. The isolated
    experiment (private `-L` socket, `systemd-run --user -p Type=oneshot`)
    confirmed it: without `RemainAfterExit` the server is gone once ExecStart
    returns; with it, the server survives.

    So the fix is not to wait longer and not to tune the timer — a duration was
    never the variable. It is to REFUSE, because a server this process creates
    cannot outlive it, and starting one is what destroys the workspace it was
    meant to restore.

    Uses `tmux has-session` with no target: it succeeds only when a server is
    running AND holds at least one session. A server with zero sessions reads
    as "no server" here, deliberately — that is the conservative direction,
    because a server with no sessions is one this process could still end up
    populating and owning.

    🔴 BUT "A STATE THE OPERATOR'S WORKSPACE NEVER SITS IN" — which this
    docstring asserted until round 2 of the audit — IS FALSE, and it was the
    same mistake as the one at the refusal in `cmd_restore`. EVERY tmux server
    has zero sessions between `server_start()` creating the socket and the
    first session being created after the config is sourced: MEASURED
    2026-09-07 at 0.098–0.112s with continuum EXCLUDED. That window is not
    exotic; it is precisely when `tmux-session-restore.path` fires, because the
    path unit triggers on the socket. `wait_for_tmux_server()` exists because
    of it. The predicate is unchanged and still correct — what was wrong was
    the claim about how often it answers "absent".
    """
    return subprocess.run(["tmux", "has-session"],
                          capture_output=True).returncode != 0


# 🔴 THE EXIT CODE FOR A MID-SESSION NO-SERVER REFUSAL, AND WHY IT IS NOT 0.
#
# The refusal itself is unchanged and still correct — see
# `no_tmux_server_to_restore_into`. What changed is that the refusal used to be
# INDISTINGUISHABLE from a cold boot, so it exited 0 in both cases and nothing
# ever told the operator. MEASURED 2026-09-11: the tmux server died at 02:36:44
# taking ~52 live conversations, this unit refused at 02:37:14 with rc 0
# (`Result=success`, no `OnFailure`, no retry — the socket fires once), and the
# workspace sat dead for 8.5 HOURS with 53 recoverable bindings on disk.
#
# 🔴 WHY A NON-ZERO CODE IS THE RIGHT CHANNEL, AGAINST THIS REPO'S OWN WARNING.
# `nix/home.nix` wires `OnFailure=notify-failure@%n.service` onto this unit, and
# that toast DEFEATS do-not-disturb (`zz_notify_failure_bypass`,
# `override_pause_level = 100`). That block's rule is precise about what abuses
# it: "any unit that can fail on a STANDING condition breaches it again". A cold
# boot IS such a standing condition — nothing else has started tmux yet, so the
# refusal is guaranteed — and that is exactly why the cold-boot arm below still
# exits 0, unchanged.
#
# A MID-SESSION death is the opposite of a standing condition on every axis that
# the rule is about:
#   * it is an EVENT, not a state — it requires a plan written during THIS boot,
#     which a cold boot cannot produce (see `no_server_is_mid_session`);
#   * it is SELF-CLEARING and fires at most once per death — `PathChanged=` is an
#     event on one watched name, so a socket nobody touches produces no second
#     trigger, and the next trigger comes only when a server is being started,
#     which is the case that RESTORES rather than refusing;
#   * it is precisely the class the bypass exists for. That block's own
#     justification is that the toast is "the only signal that an important user
#     unit died"; here what died is the operator's entire workspace, and the cost
#     of the toast not arriving was measured at 8.5 hours and 53 conversations.
#
# 🔴 THE ALTERNATIVES WERE CONSIDERED AND ARE WEAKER, not merely unchosen. A
# distinct journal line alone is what `tmux-restore-observe.sh` already reads, and
# that is a reader an operator has to RUN — the exact thing that did not happen
# for 8.5 hours. A marker file has the same defect. Sending a toast from this
# process would need `notify-send` and a session bus that the unit's pinned PATH
# (`python312 tmux coreutils`) does not carry, and would duplicate a mechanism
# this repo already declares. The push channel that exists is `OnFailure=`, and
# the only way to reach it is the exit code.
#
# WHY 75 AND NOT 1. `1` is already this file's code for three unrelated
# conditions (no plan, too stale, sends that never reached claude), so
# `ExecMainStatus=1` cannot tell an instrument which one happened —
# `tmux-restore-observe.sh` records that field. 75 is `EX_TEMPFAIL`, whose
# meaning ("the thing you needed was not available; try again") is what this is,
# and it is outside systemd's own 200+ range.
EXIT_SERVER_DIED_MIDSESSION = 75


def no_server_is_mid_session(now: float | None = None) -> tuple[bool, str]:
    """Did a claude workspace exist during THIS BOOT? `(mid_session?, reason)`.

    🔴 THE DISCRIMINATOR FOR THE 8.5-HOUR SILENCE OF 2026-09-11. A no-server
    refusal is CORRECT in both cases and must stay; what it could not do is say
    whether it was refusing the expected cold boot (nothing has started tmux yet
    — stay quiet) or an incident (a server was up, it died, and nothing on this
    host will start another — be loud). See `EXIT_SERVER_DIED_MIDSESSION`.

    THE SIGNAL: **the restore plan's own mtime, compared against uptime.**
    `cmd_save` writes a generation only when `build_plan()` found live claude
    panes — an empty workspace is explicitly refused with "no live claude panes
    found — nothing to snapshot" — so the pointer's mtime is the time a tmux
    server with live claude panes last existed. If that is INSIDE this boot, one
    existed during this boot and does not now.

    🔴 WHY THAT SIGNAL AND NOT THE OBVIOUS TWO.
      * UPTIME ALONE cannot do it. "High uptime" is a fact about the host, not
        about the workspace: a host up for weeks with tmux never started all
        session reads identical to one whose server just died.
      * `~/.tmux/resurrect/last`'s mtime is the WRONG WITNESS, and it is the
        same trap that broke the staleness gate in the same incident
        (`plan_staleness_hours`): a post-crash autosave REFRESHES it from the
        degraded workspace, so the crash's own damage looks like liveness. The
        plan is immune to that by construction — the crash makes `save` refuse
        rather than write. MEASURED on the incident's preserved evidence
        (`~/.cache/tmux-crash2-20260911T110407/`): plan mtime 02:23:15, crash
        02:36:44, refusal 11:00:02 — the plan was 8.6h old against an uptime of
        113h, i.e. unambiguously inside this boot.

    THE FOUR QUIET ANSWERS, each named rather than collapsed into one `False`:
      * `uptime-unmeasured` — `/proc/uptime` unreadable, so "inside this boot" is
        not a question that can be answered. The claim is withheld rather than
        guessed: guessing True here would fire a DND-bypassing toast off a
        measurement that did not happen.
      * `no-plan` — nothing was ever saved, so no workspace is known to have
        existed. This is also the genuine first-boot shape.
      * `plan-mtime-in-the-future` — a restored backup, `touch -d`, an rsync
        preserving a bad stamp, or a backwards clock step. A future mtime is not
        evidence the plan was written this boot; say what was observed rather
        than pick a cause, the same way `plan_staleness_hours`'s `skew` does.
      * `plan-predates-this-boot` — THE COLD BOOT. Exactly the standing
        condition the exit-0 argument is about.

    ⚠ THE RESIDUAL, stated rather than hidden: a host whose tmux has been dead
    for days but whose boot is older still answers `plan-written-this-boot`, so
    touching the socket once (starting and stopping a server) toasts once. That
    is a late alarm, not a false one — the conversations really are unrestored —
    and it is bounded at one toast per socket event.
    """
    up = uptime_hours()
    if up == float("inf"):
        return False, "uptime-unmeasured"
    if not PLAN.exists():
        return False, "no-plan"
    age_h = ((time.time() if now is None else now) - PLAN.stat().st_mtime) / 3600
    if age_h < 0:
        return False, "plan-mtime-in-the-future"
    if age_h >= up:
        return False, "plan-predates-this-boot"
    return True, "plan-written-this-boot"


# 🔴 HOW LONG TO WAIT FOR THE SESSION THE TRIGGER DOES NOT PROMISE.
#
# The path unit fires on the SOCKET FILE appearing. `no_tmux_server_to_restore_into`
# asks `tmux has-session`, which needs a SESSION. tmux creates the socket in
# `server_start()` BEFORE it sources its config, and the first session is queued
# behind the whole config — three blocking `run-shell` plugin loads and
# continuum's replay. So the trigger's observable is strictly EARLIER than this
# script's precondition, and the gap is real, not theoretical.
#
# MEASURED 2026-09-07 on this host, with continuum EXCLUDED (so every number is
# a LOWER BOUND — production is slower, with a colder page cache and ~45 panes
# to replay):
#   * socket appears at t0+0.009s;
#   * `has-session` returns rc=1 in 8ms, so it does NOT block behind the config
#     queue — the refusal is reached, it does not hang;
#   * the first session exists at t_sock+0.098–0.112s;
#   * the path-triggered ExecStart reached `has-session` at t_sock+0.065s in one
#     run and t_sock+0.288s in another.
# The outcome FLIPPED between those two runs. On a cold boot both variables move
# the wrong way at once: an idler box makes ExecStart faster, a colder cache and
# a full workspace make the config slower.
#
# 🔴 WHY 30s, AND NOT A NUMBER CLOSER TO THE MEASUREMENT. The two errors are not
# symmetric. Waiting too long costs LATENCY on a path that then does nothing.
# Waiting too little costs a SILENT NO-RESTORE — refusal, exit 0,
# `Result=success`, no `OnFailure`, and no retry, because the socket is created
# once and no second event ever comes. That is the exact outcome this unit
# exists to prevent, so the bound is biased long on purpose.
#
# 30s is ~270x the measured 0.11s lower bound, which leaves room for the
# continuum-replay term that measurement deliberately excluded and never
# quantified. As an upper anchor: the retired `OnActiveSec=45s` timer is evidence
# that a delay of that ORDER was tolerable on this host's boot path, and 30s
# stays inside it. That is an anchor, not a derivation — the replay term is
# unmeasured, and if a future boot is observed refusing after a full 30s wait the
# right response is to raise this number, not to shorten it.
#
# WHEN THE BOUND IS EXCEEDED: control falls through to the refusal in
# `cmd_restore`, which exits 0 and says how long it waited. The wait is reported
# so an operator can tell "no server ever came" apart from "a socket was there
# and nothing answered for the whole bound" — two different faults that the bare
# refusal reads identically for.
TMUX_SERVER_WAIT_SECONDS = 30.0


def tmux_socket_path() -> Path:
    """Where `tmux` will look for its socket, by tmux's own rule.

    `$TMUX_TMPDIR/tmux-$UID/default`, falling back to tmux's compiled-in `/tmp`
    when the variable is unset. This is the SAME formula the path unit's
    `PathChanged=%t/tmux-%U/default` spells in systemd specifiers, and
    `scripts/tests/test_tmux_restore_trigger.py` compares the two — that
    cross-artifact comparison is the only thing that can see the trigger and the
    query drifting apart, because each side is individually plausible.
    """
    root = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return Path(root) / f"tmux-{os.getuid()}" / "default"


def wait_for_tmux_server(timeout: float = TMUX_SERVER_WAIT_SECONDS,
                         step: float = 0.25,
                         sleep=time.sleep,
                         socket: Path | None = None) -> tuple[bool, float, str]:
    """Poll until a tmux server holds a session. Returns (found?, waited, why).

    🔴 THIS CLOSES THE GAP BETWEEN THE TRIGGER'S OBSERVABLE AND THIS SCRIPT'S
    PRECONDITION — see `TMUX_SERVER_WAIT_SECONDS` above for the measurements and
    for why the bound is what it is.

    🔴 IT MUST NOT TURN "THERE IS GENUINELY NO SERVER" INTO A 30-SECOND HANG,
    and the discriminator is free: the SOCKET FILE. It is the thing the path
    unit triggers on, so its presence is exactly the evidence that a server is
    starting. No socket => nothing is coming => return at once. That is the same
    shape as `wait_for_workspace_to_settle`'s `no_server_after` bail, and it
    exists for the same reason: #1351 shipped a revision that burned a full
    120s timeout in the nix build sandbox — which has no tmux server and no
    socket — and turned an empty-plan restore into a failure.

    `why` is one of:
      * `already-running` — a session existed on the first probe, no wait at all;
      * `appeared`        — a session appeared during the wait;
      * `no-socket`       — bailed immediately; nothing is starting;
      * `timeout`         — the socket is there and nothing answered in `timeout`.

    `sleep` and `socket` are injected so tests never sleep and never depend on
    the host having a tmux server. The probe is looked up on the MODULE at call
    time (not bound at import) so `monkeypatch.setattr(tsr,
    "no_tmux_server_to_restore_into", …)` reaches it — every existing test in
    this area patches exactly that name.
    """
    def _absent() -> bool:
        return globals()["no_tmux_server_to_restore_into"]()

    if not _absent():
        return True, 0.0, "already-running"
    sock = tmux_socket_path() if socket is None else socket
    if not sock.exists():
        return False, 0.0, "no-socket"
    waited = 0.0
    while waited < timeout:
        sleep(step)
        waited += step
        if not _absent():
            return True, waited, "appeared"
    return False, waited, "timeout"


def pane_fingerprint() -> str:
    """A stable identity for every live pane: its pid and current command.

    While tmux-resurrect is still restoring, panes are being created and
    RESPAWNED, so this string keeps changing. Once it holds still, the
    workspace has stopped moving underneath us.
    """
    return run(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{pane_current_command}"])


def wait_for_workspace_to_settle(settle: float = 5.0, timeout: float = 120.0,
                                 no_server_after: float = 10.0,
                                 sleep=time.sleep) -> tuple[bool, float]:
    """Block until the pane set stops changing. Returns (settled?, seconds waited).

    A SECONDARY guard, not the fix for the 2026-09-06 loss. That failure was
    `no_tmux_server_to_restore_into()` below — the unit manufactured its own
    tmux server and systemd killed it — and waiting longer could never have
    helped. Do not re-describe this function as the boot-race fix; an earlier
    revision of it did, and the claim was refuted by the journal (see that
    function's docstring for the discriminating evidence).

    What it IS for: once a server DOES exist, tmux-resurrect may still be
    replaying into it, creating and RESPAWNING panes. Sending into a pane that
    is mid-respawn is a real (if unmeasured here) way to lose a keystroke, so
    this waits for the OBSERVABLE — the pane set holding still — rather than
    guessing a duration.

    Returns rather than raising: a workspace that never settles is still worth
    a best-effort restore, but the caller must SAY the wait timed out rather
    than reporting a clean run.
    """
    waited = 0.0
    last = pane_fingerprint()
    stable_for = 0.0
    empty_for = 0.0
    step = 1.0
    while waited < timeout:
        sleep(step)
        waited += step
        now = pane_fingerprint()
        if not now.strip():
            # NO PANES AT ALL is not "not settled yet" — there is no workspace
            # to wait for, and burning the whole timeout would block a boot for
            # two minutes to learn nothing. Bail early, still UNSETTLED, so the
            # caller reports honestly rather than waiting.
            #
            # DEFENCE IN DEPTH, not the primary path: `cmd_restore` now refuses
            # outright when no server is running, so in production this branch
            # is only reachable if the server DIES mid-wait. It also keeps the
            # function honest when called directly (tests, and the nix build
            # sandbox, which has no tmux server at all).
            empty_for += step
            if empty_for >= no_server_after:
                return False, waited
            stable_for = 0.0
            last = now
            continue
        empty_for = 0.0
        if now == last:
            stable_for += step
            if stable_for >= settle:
                return True, waited
        else:
            stable_for = 0.0
        last = now
    return False, waited


def resurrect_last_path() -> Path:
    """`<resurrect-dir>/last`, asking tmux rather than assuming the default.

    🔴 `@resurrect-dir` IS CONFIGURABLE and the plugin honours it —
    `helpers.sh:resurrect_dir()` is `get_tmux_option @resurrect-dir
    "$HOME/.tmux/resurrect"`. Hardcoding the default is not merely incomplete:
    on a host that moved the directory, `~/.tmux/resurrect/last` FREEZES at the
    switchover instant, so every later run compares against a layout nobody
    writes, refuses permanently, and says `basis=layout` while doing it — a
    confident claim about a file that is no longer the layout being restored.
    Failing closed with a misdirecting message is worse than failing open.

    Falls back to the module default when tmux cannot be reached (no server, or
    `restore` running before one exists), which is also what the plugin's own
    `get_tmux_option` default does.
    """
    configured = run(["tmux", "show-options", "-gqv", "@resurrect-dir"]).strip()
    if configured:
        # 🔴 Match the PLUGIN'S grammar, not Python's. `helpers.sh:resurrect_dir()`
        # expands `$HOME`, `$HOSTNAME` and `~` ANYWHERE in the value via a global
        # sed; `expanduser` handles only a LEADING `~`. `$HOME/state/$HOSTNAME/
        # resurrect` is the documented multi-host idiom, and leaving it literal
        # makes the path un-stat-able -> `wall` basis -> the powered-off flaw
        # silently reintroduced, refusing a perfectly fresh plan at boot.
        expanded = (configured
                    .replace("$HOME", os.path.expanduser("~"))
                    .replace("$HOSTNAME", platform.node()))
        return Path(os.path.expanduser(expanded)) / "last"
    return RESURRECT_LAST


def resurrect_state_mtime() -> float | None:
    """mtime of the resurrect state file `restore` is racing, or None if unreadable.

    `<resurrect-dir>/last` is a symlink to the newest `tmux_resurrect_*.txt`.
    🔴 `stat()` FOLLOWS IT AND `lstat()` MUST NOT BE SUBSTITUTED: the target's
    mtime is when that layout was captured, while the symlink's own mtime is
    when it was last repointed. They differ, and on a DANGLING `last` the
    difference decides correctness — `stat` raises and we fall back to `wall`,
    whereas `lstat` would happily report `basis=layout` for a layout that no
    longer exists.

    `OSError` and not `FileNotFoundError`: a `last` that exists but is
    unreadable, or an ELOOP symlink chain, must degrade to the fallback rather
    than escape and crash the systemd unit.
    """
    try:
        return resurrect_last_path().stat().st_mtime
    except OSError:
        return None


def newest_pane_ledger_activity(directory: Path | None = None) -> float | None:
    """When an agent PANE last wrote to the ledger, as an mtime. None if unknown.

    🔴 THE WITNESS THE CRASH CANNOT REFRESH. `plan_staleness_hours` needs to know
    whether work CONTINUED after the plan stopped being written, and every other
    artefact in this chain is written by something a crash restarts: resurrect's
    `last` is refreshed by the post-crash autosave (that is the 2026-09-11 bug),
    and the plan is the thing under test. The agent ledger is written by the
    claude/opencode PROCESSES themselves, so when the tmux server dies they all
    stop writing at once and the newest record freezes at the moment of death.
    MEASURED on the incident's preserved ledger snapshot
    (`~/.cache/tmux-crash2-20260911T110407/agent-ledger`, a `cp -a`): the newest
    pane record was `claude-p28.json` at 02:36:40, 4 seconds before the crash and
    13.4 minutes after the plan — while resurrect's `last` had moved on to 10:59.

    🔴 PANE RECORDS ONLY, AND THAT FILTER IS LOAD-BEARING. The same directory
    holds SESSION-keyed records (`claude-s-<id>.json`) written by agents with no
    `$TMUX_PANE` — a claude run in a bare terminal, which is exactly what an
    operator or an investigating agent does WHILE the tmux workspace is dead.
    Counting those would let the investigation of the outage look like evidence
    that the outage was not happening. MEASURED in the same snapshot: 125 pane
    records against 105 session-keyed ones.

    🔴 THE PANE SPELLING IS DERIVED FROM THE WRITER, NOT RESTATED. `_BORROWED`
    already carries `pane_filename`; probing it with sentinels recovers the
    separator and suffix it actually uses, so a rename in `agent_ledger.py`
    cannot leave this reader silently matching nothing. A literal `"-p"` here
    would be the duplicated predicate that whole module's docstring warns about.

    ⚠ Residual, stated: a session id that itself contains the separator would be
    misclassified as a pane record, and a claude started INSIDE a pane of the new
    post-crash server does refresh this legitimately. The first is unreachable
    for the hex/base62 ids both writers produce; the second is a human actively
    working, which is not a case this gate should override.
    """
    d = Path(directory) if directory is not None else LEDGER_DIR
    if _AL is None or d is None:
        return None
    # Sentinels that `_clean` leaves alone, so the parts around them are exactly
    # the writer's literal separator and suffix.
    probe = _AL.pane_filename("RUNTIME", "PANE")
    head, _, rest = probe.partition("RUNTIME")
    mid, _, tail = rest.partition("PANE")
    if not mid or not tail:
        return None             # the spelling changed shape: measure nothing
    newest: float | None = None
    try:
        names = os.listdir(d)
    except OSError:
        return None
    for name in names:
        if not name.startswith(head) or not name.endswith(tail):
            continue
        core = name[len(head):len(name) - len(tail)]
        runtime, sep, pane = core.partition(mid)
        if not (sep and runtime and pane):
            continue
        try:
            mtime = (d / name).stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def pane_activity_after(when: float) -> float | None:
    """Hours that agent PANES kept writing past `when`. None if unmeasurable.

    Zero when the newest pane record PREDATES `when` — the panes stopped before
    the plan did, which is the crash shape and the opposite of a stale plan.
    """
    newest = newest_pane_ledger_activity()
    if newest is None:
        return None
    return max(0.0, (newest - when) / 3600)


def plan_staleness_hours() -> tuple[float, str] | None:
    """How stale the plan is RELATIVE TO THE LAYOUT IT DESCRIBES. None if no plan.

    🔴 NOT WALL-CLOCK AGE, AND THAT IS THE WHOLE POINT. The wall-clock measure
    counted time the machine spent POWERED OFF against the plan, so the gate
    refused in exactly the situation it exists to serve: shut down overnight,
    boot, and `restore --staleness-check 2` exited 1 with "plan is 8.0h old"
    — the identical rc 1 that the dead-hook outage produced (56c68cc7,
    cc409f82), from a different cause. MEASURED 2026-09-05: a plan written
    0.02h before a reboot reads as 8h/24h stale purely from being switched off,
    while nothing about it changed. Being powered off cannot make a plan
    diverge from reality; it is the one interval in which reality is frozen.

    So compare two ARTEFACTS instead of comparing one to the clock. The plan and
    the resurrect state file are written by the same chain — resurrect saves the
    layout, its `post-save-all` hook runs `tmux-post-save.sh`, which runs `save`
    — so under a working autosave their mtimes are seconds apart, forever,
    however long the host is then switched off. The gap between them is
    therefore a direct measure of the thing the gate actually guards: does this
    plan describe the layout continuum is restoring?

    It stays sharp in BOTH directions, which is why `abs()`:
      * plan much OLDER than the layout — the plan stopped being refreshed while
        the layout kept saving. This is the real 2026-08-05 outage: the plan sat
        at Jul 5 while resurrect ran to Jul 29. Restoring it would relaunch a
        month-old workspace. REFUSE.
      * layout much older than the plan — continuum stopped saving while `save`
        kept running, so the layout being restored is not the one the plan
        describes. REFUSE.

    🔴 CONTEMPORANEITY IS NOT LIVENESS, AND THE GATE NEEDS BOTH. The plan and
    the layout are written by ONE driver, so when that driver dies they freeze
    TOGETHER and their gap stays constant forever. A contemporaneity-only
    measure then reports "fresh" for a plan of any age: MEASURED, a plan and
    layout both frozen 1400h ago 30s apart returned a 0.008h gap and restored a
    58-day-old workspace across 44 windows. That is not hypothetical — it is
    the OTHER outage `nix/programs/tmux/default.nix` records: on 2026-08-05
    continuum's `status-right` interpolation was clobbered and resurrect
    stopped saving at all, freezing both artefacts at the same instant. The
    wall-clock measure caught that mode as a side effect; dropping it without
    replacement traded a LOUD failure for a silent, permissive one.

    So two numbers are computed and the WORSE is returned:

      * CONTEMPORANEITY `abs(state - plan)` — does this plan describe the
        layout being restored? Catches a ONE-SIDED freeze in either direction
        (the hook-name outage: plan stuck at Jul 5, resurrect live to Jul 29).
      * LIVENESS `min(now - newest_artefact, uptime)` — has the chain produced
        anything lately? Catches a TOTAL freeze, which contemporaneity cannot
        see. Capping at uptime is what keeps powered-off time out: at boot+45s
        the newest artefact is from before shutdown, so the cap makes it 45s
        rather than the whole night.

    Worked through the cases — INCLUDING the one this does not close:
      running normally   gap ~1min, live ~15min      -> passes
      boot+45s after 24h off  gap ~1min, live 45s    -> passes  (the bug fixed)
      31d uptime, chain dead 1400h  gap 30s, live 753h -> REFUSES
      plan frozen, layout live      gap huge         -> REFUSES
      🔴 boot+45s, chain dead 1400h  gap 30s, live 45s -> PASSES  (NOT CLOSED)
      🔴 chain dead 100h + a future mtime  gap 30s, skew -> PASSES  (see below:
         liveness is genuinely unmeasurable there, and the bounded-damage
         argument covers it — but a reader consults this list, so it is here)

    🔴 THE LIVENESS TERM IS ARITHMETICALLY INERT WHENEVER `uptime <= limit`, AND
    THAT INCLUDES THE BOOT THE UNIT RUNS ON. (The same is true of the SKEW
    branch below and of anything else routed through this cap — the ceiling
    applies to the term, not to one reason for it.) `live = min(since, uptime) <=
    uptime`, and refusing needs `> limit` — so at boot+45s with a 2h limit
    (`nix/home.nix`), `live <= 0.0125h`, 160x under, and this degrades exactly
    to the contemporaneity-only behaviour. A guard that is BREAKABLE at a
    fixture constant (the test pins `uptime_h=753.0`, 376x the limit) is not
    thereby REACHABLE at the call site's real value; those are different claims
    and only the second one protects a boot.

    WHY IT IS NOT CLOSED HERE RATHER THAN LEFT UNSAID. At boot, mtimes alone
    cannot separate "chain healthy, host off 24h" from "chain dead 58d, host
    off 24h" — after the cap both read `since ~= uptime`. The discriminator is
    the PREVIOUS BOOT'S END (`prev_shutdown - max(state, mt)`), and it is not
    reliably available: MEASURED on this host 2026-09-05, `journalctl
    --list-boots` reports TWO boots whose ranges do not abut (boot -1 ends
    2026-07-14, boot 0 begins 2026-08-18) while `uptime -s` says 2026-08-04 —
    the journal had rotated the intervening boots away. A detector built on
    that would be least trustworthy on exactly the long-lived host where a
    frozen chain is most likely.

    WHAT BOUNDS THE DAMAGE. A chain that froze froze BOTH artefacts, so
    resurrect's layout is equally stale and continuum restores that old layout
    whatever this gate decides. Resuming the conversations that match it is
    coherent; the gate cannot prevent the stale workspace, only decide whether
    to populate it. What IS lost is the diagnostic — the old wall-clock refusal
    is how the 2026-08-05 freeze was noticed at all — so if you are reading
    this because a restore looked wrong, check whether the chain is alive
    (`ls -t ~/.tmux/resurrect/*.txt | head`) before suspecting the plan.

    Returns `(hours, basis)` where basis is `"layout"`, `"ledger"`,
    `"liveness"`, `"skew"` or `"wall"` — FIVE, and the `why` dict in
    `cmd_restore` is the only consumer that knows it. A second consumer built
    from a shorter contract KeyErrors in the refusal path, which is the crash
    this line exists to prevent. The wall fallback (no readable state file) still carries the
    powered-off flaw by construction, so the basis is part of the answer and
    the refusal message NAMES it — the same number means different things.
    """
    if not PLAN.exists():
        return None
    import time
    mt = PLAN.stat().st_mtime
    state = resurrect_state_mtime()
    if state is None:
        # `max(0.0, …)`: under the same backward skew this goes negative, and a
        # negative age both prints as nonsense and compares as fresh. There is
        # no second measure to fall back to on this basis, so clamp and let the
        # value stand at "not stale", which is what a negative already meant.
        return (max(0.0, (time.time() - mt) / 3600), "wall")
    gap = abs(state - mt) / 3600
    basis_gap = "layout"
    # 🔴 A POST-CRASH LAYOUT SAVE IS THE CRASH'S OWN DAMAGE, NOT EVIDENCE THE
    # PLAN WENT STALE. MEASURED 2026-09-11: the tmux server died at 02:36:44 with
    # 53 bound conversations; a new, EMPTY server came up; continuum autosaved
    # that degraded layout at 10:59; and this gate then refused the good 02:23
    # plan with `out of step with the saved layout by 8.6h (limit 2.0h,
    # basis=layout)`. The guard against restoring a stale plan blocked recovery at
    # exactly the moment it was needed — the same inverted-basis shape #1317 fixed
    # once already for powered-off time.
    #
    # THE ARM IS NARROWED, NOT REMOVED, and only in the one direction where the
    # confusion is possible. `state > mt` is the "the plan stopped being
    # refreshed while the layout kept saving" story (the real 2026-08-05 outage).
    # That story has a testable consequence: work CONTINUED after the plan
    # stopped. `pane_activity_after` measures that from the agent ledger, which a
    # crash freezes and a post-crash autosave cannot refresh — see
    # `newest_pane_ledger_activity`. Where the panes stopped WITH the plan, the
    # layout's extra hours were bought by a dead workspace and must not be counted.
    #
    # Worked through the four cases that matter — the middle two are the ones the
    # richness-comparison alternative gets wrong, which is why the witness is the
    # ledger and not the layout's window count:
    #   2026-09-11 crash   plan 02:23, layout 10:59, panes froze 02:36:40
    #                      -> gap 8.6h, worked 0.22h -> 0.22h, PASSES (the fix)
    #   2026-08-05 outage  plan Jul 5, layout Jul 29, panes running throughout
    #                      -> gap 576h, worked ~576h -> REFUSES (preserved)
    #   same, mid-session  identical, and uptime does not enter -> REFUSES
    #   layout older       `state < mt` -> untouched, still REFUSES
    #
    # ⚠ Unmeasurable (no ledger module, no directory, no pane records — including
    # the case where a 7-day prune has emptied it) leaves `gap` exactly as it was.
    # Falling back to the previous behaviour is the safe direction: it can only
    # refuse more, never less.
    if state > mt:
        worked = pane_activity_after(mt)
        if worked is not None and worked < gap:
            gap, basis_gap = worked, "ledger"
    # Liveness: time since the chain last produced ANYTHING, but never counting
    # more than this boot has been up — powered-off time is the one interval in
    # which nothing can have gone stale.
    since = (time.time() - max(state, mt)) / 3600
    if since < 0:
        # 🔴 The clock moved BACKWARDS past an artefact's mtime, so LIVENESS IS
        # UNMEASURABLE — and both obvious answers fabricate a number.
        #
        #   `since = 0`   claims the chain just wrote. Silently disables the
        #                 F1 guard, which is what round 2 flagged.
        #   `since = inf` claims it never did. Looks safe and is worse: `live =
        #                 min(inf, uptime)` collapses to UPTIME, so a ONE-SECOND
        #                 step back on a long-uptime host refuses a HEALTHY chain
        #                 and reports it "silent for 753.0h". Measured. That is
        #                 the misdirecting-refusal failure `resurrect_last_path`
        #                 calls worse than failing open — and it was inert at
        #                 early boot anyway, the very case its comment named,
        #                 because the uptime cap neuters it there.
        #
        # CONTEMPORANEITY NEVER READS `now`, so it is immune to skew and stays
        # valid. Fall back to it and NAME the fact that liveness was not
        # evaluated, rather than inventing a liveness number in either
        # direction. Reporting "unmeasured" is the honest third option.
        return (gap, "skew")
    live = min(since, uptime_hours())
    return (gap, basis_gap) if gap >= live else (live, "liveness")


def uptime_hours() -> float:
    """Hours this boot has been up; +inf if unreadable, so the cap cannot HIDE staleness.

    An unreadable `/proc/uptime` must not silently turn the liveness check off —
    failing to the uncapped wall measure is the safe direction (it can only
    refuse more, never less).
    """
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0]) / 3600
    except (OSError, ValueError, IndexError):
        return float("inf")


# 🔴 HOW MANY LOST BOUND IDS MAKE A SHRINK AN INCIDENT RATHER THAN CHURN.
# Derived from this host's own 140-generation history, not chosen: the loss-size
# histogram across 138 transitions is {1: 13, 2: 4, 3: 1, 4: 1, 15: 1}. Ordinary
# churn — a conversation ending — loses one or two. The 2026-09-11 incident lost
# FIFTEEN. There is a clean gap, and 5 sits in it: at this threshold the rule
# fires on the incident and on nothing else in the whole retained chain.
RECOVERY_ALERT_MIN_LOST = 5

# 🔴 HOW FAR BACK TO LOOK, AND WHY IT IS BOUNDED AT ALL. Scanning the WHOLE
# retained chain sounds safer and is not: an ancestor from a day ago legitimately
# held conversations that have since ended, so an unbounded scan finds a
# "richer" plan almost always — measured 134 of 140 pointer positions, i.e.
# worse than the permanently-red predicate it replaced.
#
# MEASURED by replaying every pointer position against ONLY the generations that
# existed AT THAT TIME. (Replaying against the full chain is the trap: it lets
# the scan see the future, and that is how the 134/140 figure was produced.)
#     window= 4 -> fires   4/140 (3%)    catches the incident
#     window= 8 -> fires  11/140 (8%)    catches the incident
#     window=16 -> fires  20/140 (14%)   catches the incident
# Four is ~1h at the 15-minute autosave — long enough to span a recovery, short
# enough that ordinary churn has not accumulated. It also fixes the visibility
# problem the round-2 audit found in the consecutive-only predicate: the
# incident fires at BOTH 052312 and 052314 (2s apart) and goes quiet at 053811,
# the recovery save, instead of being visible for two seconds.
RECOVERY_ALERT_WINDOW = 4


def richer_generation(current: Path) -> tuple[Path, set[str]] | None:
    """A generation that is STRICTLY RICHER than the current plan by a lot.

    🔴 THE POINTER CAN MOVE TO A WORSE PLAN *WHILE YOU ARE RECOVERING*. MEASURED
    2026-09-11: the pre-crash plan held 52 bound conversations; partway through
    the recovery the 15-minute continuum autosave fired, saw the half-restored
    workspace, and repointed `restore-plan.json` at a fresh 37-entry generation.
    A restore after that moment recovers 37 of 52 and reports success. The 15 ids
    existed only in the older generation. Generations make that recoverable
    (#1383) — but only if somebody looks. This is the looking.

    🔴 TWO EARLIER PREDICATES WERE BOTH WRONG, IN OPPOSITE DIRECTIONS, AND BOTH
    WERE MEASURED WRONG ON THIS HOST'S REAL DATA RATHER THAN ARGUED:

      (a) "ANY generation holds an id you lack" — TRUE FOREVER. Ordinary churn
          leaves every older generation holding retired ids: **137 of 137**
          generations satisfied it, so the warning fired on every single run.
          A permanently-red gate trains everyone to click through.

      (b) "the IMMEDIATELY PRECEDING generation was richer" — fires, but for
          almost no time. Replaying the real chain, the incident was visible at
          exactly ONE pointer position and the next save landed **2 seconds**
          later, after which it was silent for the ~15 minutes that mattered.
          The unit runs `restore` at BOOT, long after any such window, so in the
          one automated caller it would essentially never fire.

    The signature is neither "different" nor "adjacent". It is **a large drop in
    bound ids that the current plan has not recovered**, wherever it sits in the
    retained chain. So: scan every generation, keep those that are STRICTLY
    RICHER than the current plan, and report the one that recovers the most —
    provided it recovers at least `RECOVERY_ALERT_MIN_LOST`.

    🔴 "STRICTLY RICHER" IS LOAD-BEARING AND ITS ABSENCE WAS A DEPLOY-BLOCKER.
    Without it this returned any generation holding *some* id the current lacks,
    including ones with FEWER conversations overall — so at the moment the
    operator had just recovered to 52 windows, `--best` would have restored the
    degraded **37**-entry plan and reported success. Measured on the real chain:
    2 of 20 fire positions picked a poorer plan. Comparing totals is what makes
    the remedy safe to follow.

    Returns `(generation, ids_it_has_that_current_lacks)` or None.
    """
    cur_ids = bound_ids(read_plan(current) or [])
    try:
        cur_real = current.resolve()
    except OSError:
        cur_real = None
    stamps = list_generations()
    # Where does the pointer sit? Everything before it is an ancestor; only the
    # RECENT ones are candidates (see RECOVERY_ALERT_WINDOW).
    idx = len(stamps)
    for i, st in enumerate(stamps):
        try:
            if cur_real is not None and generation_paths(st)[0].resolve() == cur_real:
                idx = i
                break
        except OSError:
            continue
    best: tuple[Path, set[str]] | None = None
    for stamp in stamps[max(0, idx - RECOVERY_ALERT_WINDOW):idx]:
        gplan = generation_paths(stamp)[0]
        try:
            gids = bound_ids(read_plan(gplan) or [])
        except (AttributeError, TypeError):
            continue            # a malformed generation must not fail a restore
        # STRICTLY richer overall — never offer a plan with fewer conversations.
        if len(gids) <= len(cur_ids):
            continue
        missing = gids - cur_ids
        if len(missing) < RECOVERY_ALERT_MIN_LOST:
            continue            # churn, not an incident
        if best is None or len(missing) > len(best[1]):
            best = (gplan, missing)
    return best


def cmd_restore(dry_run: bool = False, plan_path: Path | None = None,
                 staleness_hours: float | None = None,
                 prefer_best: bool = False) -> int:
    src = plan_path or PLAN
    if not src.exists():
        print(f"no restore plan at {src} — run `save` before rebooting", file=sys.stderr)
        return 1
    # 🔴 WARN ALWAYS, SWITCH ONLY ON `--best`. A restore that silently used a
    # different plan than the one the pointer names would be a worse surprise
    # than the problem it solves — but a restore that says NOTHING while 15
    # conversations sit in a generation one command away is how the 2026-09-11
    # recovery nearly stopped at 37 of 52. Skipped when `--plan` was given: the
    # caller has already chosen, explicitly.
    if plan_path is None:
        richer = richer_generation(src)
        if richer is not None:
            gplan, missing = richer
            if prefer_best:
                print(f"🔴 --best: using {gplan.name} — it carries {len(missing)} bound "
                      f"session id(s) the current plan has LOST.", file=sys.stderr)
                src = gplan
                # 🔴 The staleness gate below measures PLAN, not `src`. Having
                # explicitly chosen an OLDER generation, refusing it for being
                # old is incoherent — and measuring the pointer would let a
                # fresh pointer wave through an arbitrarily stale choice. Treat
                # --best like --plan: the caller has chosen.
                staleness_hours = None
            else:
                print(f"🔴 A NEWER-BUT-POORER PLAN IS IN EFFECT. {gplan.name} carries "
                      f"{len(missing)} bound session id(s) this plan does not.",
                      file=sys.stderr)
                print("   A save can repoint mid-recovery; the ids are not lost, "
                      "they are in that generation. To use it:", file=sys.stderr)
                print(f"     tmux-session-restore.py restore --plan {gplan}", file=sys.stderr)
                print("   …or re-run with --best to pick it automatically.", file=sys.stderr)
    if staleness_hours is not None and plan_path is None:
        measured = plan_staleness_hours()
        if measured is not None:
            gap, basis = measured
            if gap > staleness_hours:
                # Name the BASIS: "8.0h" means two different things depending on
                # which one produced it, and the wall fallback carries the
                # powered-off flaw the layout basis exists to remove.
                why = {
                    "layout": "out of step with the saved layout by",
                    # The layout gap was DISCOUNTED because the agent panes
                    # stopped when the plan did (see `plan_staleness_hours`);
                    # what is left is how long work outlived the plan. Naming
                    # the layout here would point the reader back at the number
                    # this basis exists to reject.
                    "ledger": ("older than the last live agent pane by"),
                    # NOT wall clock: this is uptime-capped running time since
                    # the chain last produced anything. Saying "wall clock"
                    # here points the reader at the powered-off bug, when the
                    # cause is the opposite — the save chain stopped.
                    "liveness": "produced by a chain that has been silent for",
                    "wall": "older than (wall clock, no layout to compare)",
                    # Liveness was NOT evaluated; say so rather than let the
                    # reader assume both terms were checked.
                    # NOT "the clock moved backwards": `since < 0` fires on
                    # ANY future mtime — a restored backup, `touch -d`, an
                    # rsync preserving a bad stamp — and this code cannot tell
                    # those apart. Name the OBSERVATION, not a cause it never
                    # measured; that is the principle this branch exists for.
                    "skew": ("out of step with the saved layout by (liveness "
                             "NOT evaluated — an artefact's mtime is in the "
                             "future)"),
                }[basis]
                print(f"restore plan is {why} {gap:.1f}h "
                      f"(limit {staleness_hours}h, basis={basis}) — too stale, "
                      f"skipping. Run `save` first.", file=sys.stderr)
                return 1
    plan = json.loads(src.read_text())
    tag = "[dry-run] would " if dry_run else ""
    sent = skipped = 0
    targets_sent: list[tuple[str, str]] = []
    settled = True
    # 🔴 REFUSE RATHER THAN MANUFACTURE A SERVER THAT CANNOT SURVIVE US.
    # See `no_tmux_server_to_restore_into` for the confirmed mechanism. This
    # must run BEFORE the settle wait and before the send loop: the loop's own
    # `tmux new-session` is the destructive step.
    if not dry_run and plan:
        found, server_waited, why = wait_for_tmux_server()
    else:
        found, server_waited, why = True, 0.0, "not-checked"
    if not dry_run and plan and not found:
        # 🔴 EXIT 0, NOT 1, AND THAT IS A DELIBERATE CHOICE — NOT AN OVERSIGHT.
        # The unit is `OnFailure=notify-failure@%n`, and that toast bypasses
        # DND (`nix/home.nix` — "any unit that can fail on a STANDING condition
        # breaches it again").
        #
        # 🔴 THIS REASON HAS NOW BEEN WRONG TWICE. Round 1 said "no server is
        # the normal COLD-BOOT state, because the unit fires on OnActiveSec=45s"
        # — that trigger is gone. Round 2 replaced it with "what is left is a
        # rare race: a stale socket from a SIGKILLed server, or a server with
        # zero sessions", and the FREQUENCY half of that was false: EVERY tmux
        # server has zero sessions for its first ~100ms, and that is precisely
        # the window `PathChanged=` fires in. So the two examples it gave as
        # exotic were preceded by a state every tmux server passes through, in
        # the exact window the trigger fires in. (Which of the reachable states
        # was the MOST common was never measured either — do not add that claim
        # back in a different spelling.)
        #
        # WHAT IS TRUE, stated at the scope it was measured:
        #   * `PathChanged=` fires on the watched socket being DELETED as well
        #     as created (MEASURED). Socket deletion is the operator's tmux
        #     server exiting — routine. The unit's `ConditionPathExists=`
        #     catches the ordinary shape of that before ExecStart.
        #   * The socket-appears case used to reach here whenever ExecStart won
        #     the race against tmux's config queue — MEASURED at 2 of 2 sampled
        #     runs landing on OPPOSITE sides of it. `wait_for_tmux_server()`
        #     above is what now covers that window.
        #   * 🔴 THE POST-FIX FREQUENCY IS UNMEASURED, and saying otherwise is
        #     how this comment got it wrong twice. Establishing it needs reboots
        #     this change has not had. What is left after the poll is: no socket
        #     at all (`why=no-socket` — the server went away between systemd's
        #     condition check and this process starting), or a socket with
        #     nothing answering for the full bound (`why=timeout`). Neither
        #     frequency is known.
        #
        # 🔴 THE CONCLUSION DOES NOT DEPEND ON THE FREQUENCY, which is why it
        # survives the correction. `OnFailure=` here bypasses DND. A branch that
        # can be reached by a race must not raise an alarm indistinguishable
        # from a real failure, however often the race happens — an alarm the
        # operator cannot tell from a real one is an alarm they learn to
        # dismiss, and this one bypasses DND to reach them. A skip the operator
        # can read in the log is the honest report. Do NOT replace this with a
        # third plausible-sounding reason; if you need the frequency, measure it.
        #
        # It is READABLE, not merely logged: `tmux-restore-observe.sh` counts
        # `REFUSING to restore` in this unit's journal and reports a refused
        # boot as its own verdict (RC_REFUSED). That arm exists because the
        # resume comparison is gated on `sends != 0`, and a refused run logs
        # ZERO sends — so before it, a boot where nothing was resumed read CLEAN.
        why_line = {
            "no-socket": ("no socket at %s either — the server was gone before "
                          "this process started" % tmux_socket_path()),
            "timeout": ("the socket at %s exists but no server answered in "
                        "%.1fs" % (tmux_socket_path(), server_waited)),
        }.get(why, "reason=%s" % why)
        print("no tmux server is running — REFUSING to restore.", file=sys.stderr)
        print(f"  waited {server_waited:.1f}s for one ({why_line}).", file=sys.stderr)
        print("  Starting one here would put it inside this unit's cgroup, and "
              "systemd would kill it the moment this process exits, taking every "
              "resumed conversation with it (measured 2026-09-06: 43 lost).",
              file=sys.stderr)
        print("  Nothing was changed. Once you have a tmux server, re-run: "
              "tmux-session-restore.py restore", file=sys.stderr)
        # 🔴 THE REFUSAL IS THE SAME EITHER WAY; ONLY THE VOLUME CHANGES. Every
        # line above has already been printed and nothing has been sent, so this
        # branch decides one thing: does the operator find out NOW, or only when
        # they next look at a terminal? MEASURED 2026-09-11: the second answer
        # cost 8.5 hours and 53 recoverable conversations. See
        # `no_server_is_mid_session` for the signal and
        # `EXIT_SERVER_DIED_MIDSESSION` for why a non-zero code is the channel.
        mid_session, boot_reason = no_server_is_mid_session()
        print(f"  cold-boot check: {boot_reason}", file=sys.stderr)
        if mid_session:
            print("🔴 THE TMUX SERVER DIED MID-SESSION — this is an INCIDENT, not "
                  "a cold boot.", file=sys.stderr)
            print(f"  A restore plan was written during THIS boot ({PLAN}), so a "
                  "workspace with live claude panes existed and does not now. "
                  "Nothing on this host starts a tmux server on its own, so "
                  "nothing will retry.", file=sys.stderr)
            print("  Start tmux (that re-triggers this unit), or recover by hand: "
                  "tmux-session-restore.py restore --best", file=sys.stderr)
            return EXIT_SERVER_DIED_MIDSESSION
        return 0
    if not dry_run and plan and server_waited > 0:
        print(f"waited {server_waited:.1f}s for a tmux server to hold a session "
              f"({why}) — the path unit triggers on the SOCKET, which tmux "
              "creates before it sources its config")
    # 🔴 NOTHING TO SEND => NOTHING TO WAIT FOR, AND NOTHING THAT CAN BE LOST.
    # Waiting here made an EMPTY plan take the full settle timeout and then
    # return 1 — a restore that had no work to do reported as a failure.
    if not dry_run and plan:
        settled, waited = wait_for_workspace_to_settle()
        if settled:
            print(f"workspace settled after {waited:.0f}s — sending now")
        else:
            print(f"🔴 workspace did NOT settle within {waited:.0f}s — sending anyway, "
                  "but panes may still be respawning and sends can be DISCARDED. "
                  "This run is best-effort, not a clean restore.", file=sys.stderr)
    # One PATH scan, not one per entry.
    cb = claude_command()
    for e in plan:
        sess, win, cwd, sid = e["session"], e["window"], e["cwd"], e["session_id"]
        target = f"{sess}:{win}"
        if not tmux_session_exists(sess) and not dry_run:
            run(["tmux", "new-session", "-d", "-s", sess, "-c", cwd])
        exists, cmd = window_state(target)
        if not exists and not dry_run:
            # `-d`: creating 33 windows must not yank the active window in every
            # session it touches. Dormant until now only because the old
            # predicate meant new-window was essentially never called.
            run(["tmux", "new-window", "-d", "-t", target, "-c", cwd])
            cmd = ""
        # Never clobber a window that already has claude running (idempotent re-runs).
        if cmd == "claude":
            print(f"  skip {e['codename']}:{win} — claude already running")
            skipped += 1
            continue
        resume = f"{cb} --resume {sid}" if sid else f"{cb} --resume"
        line = f"cd {cwd} && {resume}"
        if dry_run:
            print(f"{tag}send to {e['codename']}:{win}: {line}")
        else:
            run(["tmux", "send-keys", "-t", target, line, "Enter"])
            print(f"→ {e['codename']}:{win}  {resume}")
            targets_sent.append((f"{e['codename']}:{win}", target))
        sent += 1

    verb = "would relaunch" if dry_run else "relaunched"
    print(f"\n{verb} {sent} windows, skipped {skipped}. "
          + ("(nothing changed — dry run)" if dry_run else "Attach with: tmux attach"))
    if dry_run:
        return 0

    # 🔴 VERIFY THE SENDS LANDED. `tmux send-keys` returns 0 for keystrokes that
    # go nowhere, so "sent" is a claim about THIS process, never about the
    # workspace. On 2026-09-06 that gap let the unit report success having
    # started nothing at all. A send is only real once the pane is running
    # claude.
    landed, lost = _verify_sends(targets_sent)
    if lost:
        print(f"🔴 {len(lost)} of {sent} send(s) did NOT start claude: "
              + ", ".join(name for name, _ in lost[:8])
              + ("…" if len(lost) > 8 else ""), file=sys.stderr)
        # NOT "discarded by the pane" — that was this arc's first diagnosis and
        # it was refuted. tmux accepted the keystrokes; where they went is
        # exactly what this branch cannot determine. Name the OBSERVATION.
        print("  tmux accepted the keystrokes and these panes are not running "
              "claude. Re-run once the workspace is idle; if it recurs, check "
              "whether the server is still the one the sends went to "
              "(`tmux display-message -p '#{pid}'`).", file=sys.stderr)
        return 1
    if not settled and sent:
        print(f"⚠ all {landed} send(s) landed, but the workspace never settled — "
              "treat this run as lucky, not correct.", file=sys.stderr)
        return 1
    print(f"verified: {landed} of {sent} window(s) are running claude")
    return 0


def _verify_sends(targets: list[tuple[str, str]], attempts: int = 15,
                  sleep=time.sleep) -> tuple[int, list[tuple[str, str]]]:
    """(count that reached `claude`, list of those that did not).

    Polls rather than sleeping once: claude's startup time varies with the
    transcript size being resumed, and a single fixed wait would mis-report the
    slow ones as lost.
    """
    pending = list(targets)
    landed = 0
    for _ in range(attempts):
        if not pending:
            break
        sleep(1.0)
        still = []
        for name, target in pending:
            _, cmd = window_state(target)
            if cmd == "claude":
                landed += 1
            else:
                still.append((name, target))
        pending = still
    return landed, pending


def main(argv: list[str]) -> int:
    if argv[:1] == ["save"]:
        return cmd_save()
    if argv[:1] == ["show"]:
        return cmd_show()
    if argv[:1] == ["restore"]:
        rest = argv[1:]
        dry = "--dry-run" in rest or "-n" in rest
        staleness = None
        if "--staleness-check" in rest:
            i = rest.index("--staleness-check")
            if i + 1 < len(rest) and rest[i + 1].replace(".", "").isdigit():
                staleness = float(rest[i + 1])
            else:
                staleness = 2.0  # default: 2 hours
        plan_path = None
        if "--plan" in rest:
            i = rest.index("--plan")
            if i + 1 < len(rest):
                plan_path = Path(os.path.expanduser(rest[i + 1]))
        return cmd_restore(dry_run=dry, plan_path=plan_path,
                           staleness_hours=staleness,
                           prefer_best="--best" in rest)
    print(__doc__.strip().split("\n\n")[0])
    print("\nusage: tmux-session-restore.py "
          "{save | restore [--dry-run] [--plan PATH] [--best] | show}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
