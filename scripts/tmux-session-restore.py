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
  --plan PATH             use a custom plan file instead of the default
  --staleness-check [H]   refuse to restore if plan is > H hours old (default: 2h)

State: ~/.config/initiatives/restore-plan.json  (+ restore-cheatsheet.md)
Scratchpad codenames come from the canonical scripts/tmux-scratch-slots.sh.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.config/initiatives"))
PLAN = STATE_DIR / "restore-plan.json"
CHEAT = STATE_DIR / "restore-cheatsheet.md"
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
    days. `write_record` itself does NOT prune — both writers prune on their
    SESSION BOUNDARIES only (`agent-ledger-hook.py`'s `PRUNE_EVENTS` is
    `SessionStart`/`Stop`, a subset of the four events that write) — but a prune
    sweeps the WHOLE directory, so it is OTHER sessions' boundaries that delete an
    idle pane's record. Either way a live pane idle longer than that ends up with
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


def cmd_save() -> int:
    plan = build_plan()
    if not plan:
        print("no live claude panes found — nothing to snapshot", file=sys.stderr)
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(json.dumps(plan, indent=2))
    CHEAT.write_text(cheat_sheet(plan))
    n_ledger = sum(1 for e in plan if e.get("bind_source") == "ledger")
    n_fuzzy = sum(1 for e in plan if e.get("bind_source") == "fuzzy")
    print(f"saved {len(plan)} windows → {PLAN}")
    print(f"bound: {n_ledger} ledger, {n_fuzzy} pane-content, "
          f"{len(plan) - n_ledger - n_fuzzy} unbound (picker at restore)")
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
    return subprocess.run(["tmux", "has-session", "-t", name],
                          capture_output=True).returncode == 0


def window_state(target: str) -> tuple[bool, str]:
    """(window exists?, its pane_current_command) for a `session:window` target."""
    out = run(["tmux", "display-message", "-p", "-t", target, "#{pane_current_command}"])
    return (bool(out.strip()), out.strip())


def plan_age_hours() -> float | None:
    """Age of the current restore plan in hours, or None if no plan exists."""
    if not PLAN.exists():
        return None
    import time
    return (time.time() - PLAN.stat().st_mtime) / 3600


def cmd_restore(dry_run: bool = False, plan_path: Path | None = None,
                 staleness_hours: float | None = None) -> int:
    src = plan_path or PLAN
    if not src.exists():
        print(f"no restore plan at {src} — run `save` before rebooting", file=sys.stderr)
        return 1
    if staleness_hours is not None and plan_path is None:
        age = plan_age_hours()
        if age is not None and age > staleness_hours:
            print(f"restore plan is {age:.1f}h old (limit {staleness_hours}h) — "
                  f"too stale, skipping. Run `save` first.", file=sys.stderr)
            return 1
    plan = json.loads(src.read_text())
    tag = "[dry-run] would " if dry_run else ""
    sent = skipped = 0
    for e in plan:
        sess, win, cwd, sid = e["session"], e["window"], e["cwd"], e["session_id"]
        target = f"{sess}:{win}"
        if not tmux_session_exists(sess) and not dry_run:
            run(["tmux", "new-session", "-d", "-s", sess, "-c", cwd])
        exists, cmd = window_state(target)
        if not exists and not dry_run:
            run(["tmux", "new-window", "-t", target, "-c", cwd])
            cmd = ""
        # Never clobber a window that already has claude running (idempotent re-runs).
        if cmd == "claude":
            print(f"  skip {e['codename']}:{win} — claude already running")
            skipped += 1
            continue
        resume = f"claude --resume {sid}" if sid else "claude --resume"
        line = f"cd {cwd} && {resume}"
        if dry_run:
            print(f"{tag}send to {e['codename']}:{win}: {line}")
        else:
            run(["tmux", "send-keys", "-t", target, line, "Enter"])
            print(f"→ {e['codename']}:{win}  {resume}")
        sent += 1
    verb = "would relaunch" if dry_run else "relaunched"
    print(f"\n{verb} {sent} windows, skipped {skipped}. "
          + ("(nothing changed — dry run)" if dry_run else "Attach with: tmux attach"))
    return 0


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
                           staleness_hours=staleness)
    print(__doc__.strip().split("\n\n")[0])
    print("\nusage: tmux-session-restore.py "
          "{save | restore [--dry-run] [--plan PATH] | show}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
