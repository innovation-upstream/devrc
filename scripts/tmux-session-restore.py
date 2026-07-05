#!/usr/bin/env python3
"""tmux-session-restore — snapshot the live claude/tmux workspace, resume it post-reboot.

The gap: tmux-resurrect/continuum (already on: `@continuum-restore on`) restores every
scratchpad session's windows + working dirs on reboot — but it relaunches a bare shell,
NOT the `claude` conversation that was in each window. This captures which claude session
was where and, after reboot, relaunches `claude --resume <id>` in the right window.

Binding a window to its EXACT session id is inherently fuzzy (claude appends-and-closes
its jsonl, holding no fd; the session summary isn't stored) — so per repo we rank the
session jsonls by recency and assign the newest ones (the live conversations) to that
repo's live windows, in window order. The cheat-sheet prints each guess with its summary
line so you can eyeball / correct before running restore.

Usage:
  tmux-session-restore.py save      # BEFORE reboot — writes the plan + cheat-sheet
  tmux-session-restore.py restore   # AFTER reboot   — relaunches claude per window
  tmux-session-restore.py show      # print the last saved cheat-sheet

State: ~/.config/initiatives/restore-plan.json  (+ restore-cheatsheet.md)
Scratchpad codenames come from the canonical scripts/tmux-scratch-slots.sh.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.config/initiatives"))
PLAN = STATE_DIR / "restore-plan.json"
CHEAT = STATE_DIR / "restore-cheatsheet.md"
PROJECTS = Path(os.path.expanduser("~/.claude/projects"))
SLOTS_FILE = Path(__file__).resolve().parent / "tmux-scratch-slots.sh"
_SLOT_RE = re.compile(r'"([^":]+):([^":]+):(#[0-9a-fA-F]{6}):([^":]+)"')
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


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
    """Live claude panes: [{session, window, cwd, title}] in stable order."""
    out = run(["tmux", "list-panes", "-a", "-F",
               "#{session_name}\t#{window_index}\t#{pane_current_path}"
               "\t#{pane_current_command}\t#{pane_title}"])
    panes = []
    for ln in out.splitlines():
        p = ln.split("\t")
        if len(p) < 5 or p[3] != "claude":
            continue
        panes.append({"session": p[0], "window": p[1], "cwd": p[2], "title": p[4]})
    return panes


def build_plan() -> list[dict]:
    """Bind each live claude window to the EXACT session it runs (by pane content).

    Each pane's session is chosen from its uniquely-matched candidates, and a session
    once claimed is never reused — so two windows can't collapse onto one conversation.
    A window with no certain, unclaimed match gets an empty id (interactive picker).
    """
    codes = codenames()
    panes = live_claude_panes()
    cands = {i: unique_match_sids(f"{p['session']}:{p['window']}", p["cwd"])
             for i, p in enumerate(panes)}
    claimed: set[str] = set()
    plan = []
    for i, p in enumerate(panes):
        sid = next((s for s in cands[i] if s not in claimed), "")
        if sid:
            claimed.add(sid)
        plan.append({
            "session": p["session"],
            "window": p["window"],
            "codename": display_session(p["session"], codes),
            "cwd": p["cwd"],
            "session_id": sid,
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
            lines.append(f"- resume: `cd {e['cwd']} && claude --resume {e['session_id']}`")
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
    print(f"saved {len(plan)} windows → {PLAN}")
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


def cmd_restore(dry_run: bool = False, plan_path: Path | None = None) -> int:
    src = plan_path or PLAN
    if not src.exists():
        print(f"no restore plan at {src} — run `save` before rebooting", file=sys.stderr)
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
        plan_path = None
        if "--plan" in rest:
            i = rest.index("--plan")
            if i + 1 < len(rest):
                plan_path = Path(os.path.expanduser(rest[i + 1]))
        return cmd_restore(dry_run=dry, plan_path=plan_path)
    print(__doc__.strip().split("\n\n")[0])
    print("\nusage: tmux-session-restore.py "
          "{save | restore [--dry-run] [--plan PATH] | show}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
