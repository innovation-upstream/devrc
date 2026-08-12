#!/usr/bin/env python3
"""Turn-finished notifier for Claude Code.

One script, three hook events (dispatched on stdin's `hook_event_name`):

  UserPromptSubmit  -> record turn-start epoch in a per-session file.
  Stop              -> main agent finished a turn: if it ran >= threshold,
                       fire a notification, then delete the start file.
  SubagentStop      -> a dispatched subagent finished: if the (parent) turn has
                       already run >= threshold, notify. Does NOT delete the
                       start file (the parent turn is still going; its Stop will).

Purpose: long turns (the measured time sink is `claude` foreground-blocking,
557m/14d with a tail to ~80min) ping the user instead of being babysat. Short
turns stay silent (threshold gate; default 60s, env CLAUDE_NOTIFY_MIN_SECONDS).

Delivery, best-effort. The desktop toast is preferred; the clawgate phone push
is a FALLBACK used only when there is no local desktop (headless workbench /
away from the laptop) -- firing both would push a redundant phone card while the
user is sitting at the machine:
  - Local desktop (DISPLAY set): dunstify / notify-send low-urgency toast.
  - Else (headless / cross-host): clawgate push (POST /api/notify with the
    machine hook token from ~/.claude/clawgate.env) -> Web Push to the phone.
    /api/notify is notify-only (no approve/deny card); added in clawgate 0.7.67.

Contract: this hook is a best-effort SIDE EFFECT only. It ALWAYS exits 0 and
never emits blocking output — a non-zero/blocking Stop hook would perturb the
session. Any internal error is swallowed. Worst case == the hook not existing.

GLOBAL COALESCING (2026-08-12 — the do-not-disturb fix). The per-session
cooldown above bounds ONE session's ping rate and does nothing to the aggregate:
with N concurrent sessions the desktop sees N independent streams. Measured from
this log: 1914 desktop toasts in 11 days on the workbench (~174/day, peak 386)
and 1183 in 18 days on the laptop — enough that DND was switched on, which then
swallowed every class of alert except the `notify-failure` deadman. So a second,
CROSS-SESSION gate sits in front of the desktop toast: at most one toast per
CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS across all sessions on this host.

It COALESCES rather than drops. Every turn the window suppresses is recorded in a
shared state file, and the next toast that does fire names how many other turns
finished and which projects they were — nothing that would have been said goes
unsaid, it arrives later and batched onto one line. The gate is applied ONLY to
the desktop path, so the headless/away-from-machine clawgate push is unchanged
and a suppressed toast never spills onto the phone instead.

Set CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS=0 to disable the gate and get exactly
the pre-2026-08-12 behaviour back.

Kill-switch: CLAUDE_NOTIFY=off (or 0/false/no) disables it for a session.
Config env:
  CLAUDE_NOTIFY_MIN_SECONDS       threshold in seconds (default 60)
  CLAUDE_NOTIFY_COOLDOWN_SECONDS  min seconds between pings per session
                                  (default = the threshold) — collapses a burst
                                  of parallel SubagentStops into one ping.
  CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS
                                  min seconds between DESKTOP toasts across ALL
                                  sessions (default 600). 0 disables the gate.
  CLAUDE_NOTIFY                   off/0/false/no -> disabled
  CLAWGATE_API_URL           clawgate base URL (else read from clawgate.env)
  CLAWGATE_HOOK_TOKEN        clawgate machine bearer token (else clawgate.env)
"""
import os
import sys
import json
import time
import fcntl
import shutil
import subprocess

HOME = os.path.expanduser("~")
CACHE_DIR = os.path.join(HOME, ".cache", "claude-notify")
CLAWGATE_ENV = os.path.join(HOME, ".claude", "clawgate.env")
LOG_FILE = os.path.join(HOME, ".claude", "claude-notify.log")
# Cross-session coalescing state, shared by every hook process on this host.
GLOBAL_STATE = os.path.join(CACHE_DIR, "global.json")
DEFAULT_THRESHOLD = 60
DEFAULT_GLOBAL_COOLDOWN = 600
# Bound the pending list so a long quiet-window stretch cannot grow the state
# file without limit. Only the NAMES are capped; `held` counts every turn.
PENDING_CAP = 200
STALE_SECONDS = 24 * 60 * 60  # prune start/lastnotify files older than 1 day


def log(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def disabled():
    return os.environ.get("CLAUDE_NOTIFY", "on").lower() in ("0", "off", "false", "no")


def threshold():
    try:
        return float(os.environ.get("CLAUDE_NOTIFY_MIN_SECONDS", DEFAULT_THRESHOLD))
    except Exception:
        return DEFAULT_THRESHOLD


def cooldown():
    """Min seconds between notifications for one session. Defaults to the
    threshold. Collapses a burst of parallel SubagentStops (N subagents ->
    N+1 pings) down to one ping while a genuinely spread-out sequence still
    pings each time it clears the cooldown."""
    try:
        return float(os.environ.get("CLAUDE_NOTIFY_COOLDOWN_SECONDS", threshold()))
    except Exception:
        return threshold()


def global_cooldown():
    """Min seconds between DESKTOP toasts across ALL sessions on this host.

    0 (or negative, or unparseable-as-0) disables the cross-session gate.
    """
    try:
        return float(os.environ.get("CLAUDE_NOTIFY_GLOBAL_COOLDOWN_SECONDS",
                                    DEFAULT_GLOBAL_COOLDOWN))
    except Exception:
        return DEFAULT_GLOBAL_COOLDOWN


def coalesce_decide(state, now, project, window):
    """PURE. The whole cross-session policy, with no I/O, so it can be replayed
    against the historical log and unit-tested directly.

    state  -- {"last_emit": epoch, "pending": [project, ...], "held": int}
    returns (emit, new_state, (held, distinct_projects))

    Emit when the window has elapsed since the last emitted toast; otherwise
    hold this turn and fold it into whatever fires next. `held` counts EVERY
    suppressed turn even after the name list hits PENDING_CAP, so the number in
    the toast stays truthful when the names are truncated.
    """
    try:
        last = float(state.get("last_emit") or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    pending = [p for p in (state.get("pending") or []) if isinstance(p, str)]
    try:
        held = int(state.get("held") or 0)
    except (TypeError, ValueError):
        held = 0

    # `not last` is the NEVER-EMITTED case and is handled explicitly rather than
    # left to `now - 0 >= window` arithmetic: that only happens to be true
    # because real epochs are large, so a fresh cache on a host with a bogus or
    # reset clock would hold the very first toast instead of emitting it.
    if window <= 0 or not last or (now - last) >= window:
        distinct = []
        for p in pending:
            if p and p not in distinct:
                distinct.append(p)
        return True, {"last_emit": now, "pending": [], "held": 0}, (held, distinct)

    pending.append(project or "")
    held += 1
    if len(pending) > PENDING_CAP:
        pending = pending[-PENDING_CAP:]
    return False, {"last_emit": last, "pending": pending, "held": held}, (0, [])


def coalesce_gate(now, project):
    """Apply coalesce_decide() to the shared on-disk state under an exclusive
    lock (many hook processes run concurrently, one per session).

    FAILS OPEN: any error reading/writing/locking the state emits the toast.
    A broken gate must never become a third silent-delivery failure — the worst
    case is the old, noisy behaviour, not a lost notification.
    """
    window = global_cooldown()
    if window <= 0:
        return True, (0, [])
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(GLOBAL_STATE, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            raw = f.read()
            try:
                state = json.loads(raw) if raw.strip() else {}
            except Exception:
                state = {}
            if not isinstance(state, dict):
                state = {}
            emit, new_state, summary = coalesce_decide(state, now, project, window)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(new_state))
            f.flush()
            return emit, summary
    except Exception as e:
        log("coalesce gate error (failing open, toast emitted): %r" % (e,))
        return True, (0, [])


def coalesce_suffix(held, projects):
    """The line appended to a toast body naming what was folded into it.

    Empty string when nothing was held, so an uncoalesced toast is byte-for-byte
    what it was before this feature existed.
    """
    if held <= 0:
        return ""
    shown = projects[:6]
    names = ", ".join(p for p in shown if p)
    if len(projects) > len(shown):
        names += ", …"
    line = "+%d other turn%s finished" % (held, "" if held == 1 else "s")
    return (line + ": " + names) if names else line


def safe_session_id(sid):
    """Sanitize a session id for use as a filename (never traverse)."""
    keep = [c for c in (sid or "") if c.isalnum() or c in "-_"]
    return "".join(keep)[:128]


def start_path(sid):
    return os.path.join(CACHE_DIR, safe_session_id(sid) + ".start")


def lastnotify_path(sid):
    return os.path.join(CACHE_DIR, safe_session_id(sid) + ".lastnotify")


def prune_stale():
    """Best-effort: drop start/lastnotify files older than a day (crashed/abandoned turns)."""
    try:
        now = time.time()
        for name in os.listdir(CACHE_DIR):
            if not (name.endswith(".start") or name.endswith(".lastnotify")):
                continue
            p = os.path.join(CACHE_DIR, name)
            try:
                if now - os.path.getmtime(p) > STALE_SECONDS:
                    os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def fmt_elapsed(sec):
    sec = int(sec)
    m, s = divmod(sec, 60)
    if m:
        return "%dm %ds" % (m, s)
    return "%ds" % s


def load_clawgate_env():
    """Read CLAWGATE_API_URL / CLAWGATE_HOOK_TOKEN from env or ~/.claude/clawgate.env."""
    url = os.environ.get("CLAWGATE_API_URL", "")
    token = os.environ.get("CLAWGATE_HOOK_TOKEN", "")
    try:
        if (not url or not token) and os.path.isfile(CLAWGATE_ENV):
            with open(CLAWGATE_ENV) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "CLAWGATE_API_URL" and not url:
                        url = v
                    elif k == "CLAWGATE_HOOK_TOKEN" and not token:
                        token = v
    except Exception:
        pass
    return url, token


def desktop_available():
    """True when a desktop toast could actually be dispatched from here.

    Deliberately mirrors notify_desktop()'s own preconditions: it decides whether
    the CROSS-SESSION gate applies at all, and the gate must apply exactly when
    the desktop path is the one that would fire. On a headless host this is
    False, the gate is skipped, and the clawgate phone fallback is untouched.
    """
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False
    return bool(shutil.which("dunstify") or shutil.which("notify-send"))


def notify_desktop(title, body):
    """Low-urgency toast via dunstify (preferred) or notify-send. No-op headless."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False
    try:
        if shutil.which("dunstify"):
            subprocess.run(
                ["dunstify", "-u", "low", "-a", "claude", title, body],
                timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        if shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "-u", "low", "-a", "claude", title, body],
                timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
    except Exception:
        pass
    return False


def notify_clawgate(title, body, host, project, cwd, session):
    """Fire-and-forget Web Push via clawgate's notify-only /api/notify endpoint.

    /api/notify (clawgate 0.7.68+) sends a plain Web Push with NO approve/deny
    card — the right semantics for a "finished" ping (the old /api/send created a
    self-expiring permission card). Requires the machine hook token; silently
    no-ops if unavailable or curl is missing. `cwd`/`session` are unused by the
    endpoint (kept in the signature for a stable call site).
    """
    url, token = load_clawgate_env()
    if not url or not token or not shutil.which("curl"):
        return False
    try:
        payload = json.dumps({
            "title": title,         # e.g. "✅ Claude finished (4m 12s)"
            "body": body,           # e.g. "civitai — turn ran 4m 12s"
            "host": host,
            "project": project,
        })
        r = subprocess.run(
            ["curl", "-sf", "--max-time", "6", "-X", "POST",
             url.rstrip("/") + "/api/notify",
             "-H", "Content-Type: application/json",
             "-H", "Authorization: Bearer " + token,
             "-d", payload],
            timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception:
        return False


def do_notify(event, cwd, session):
    project = os.path.basename(cwd) if cwd else ""
    try:
        host = os.uname().nodename
    except Exception:
        host = os.environ.get("CLAUDE_HOST", "")
    elapsed = do_notify.elapsed_str
    label = "subagent finished" if event == "SubagentStop" else "finished"
    title = "✅ Claude %s (%s)" % (label, elapsed)
    body = ("%s — turn ran %s" % (project, elapsed)) if project else ("turn ran %s" % elapsed)

    # CROSS-SESSION gate, desktop path only. When it holds this turn we emit
    # nothing at all — deliberately NOT falling through to clawgate, which would
    # just move the noise to the phone while the user sits at the machine.
    held = 0
    if desktop_available():
        emit, (held, projects) = coalesce_gate(time.time(), project)
        if not emit:
            log("coalesced event=%s project=%s elapsed=%s (held for next toast)"
                % (event, project, elapsed))
            return
        suffix = coalesce_suffix(held, projects)
        if suffix:
            title = "✅ Claude finished (%s) +%d" % (elapsed, held)
            body = body + "\n" + suffix

    d = notify_desktop(title, body)
    # clawgate (phone push) is a FALLBACK: only when there's no local desktop
    # toast (i.e. the headless workbench / away-from-laptop). Firing both would
    # push a redundant phone card while the user is sitting at the laptop.
    c = notify_clawgate(title, body, host, project, cwd, session) if not d else False
    # `coalesced=` is part of the log CONTRACT: it distinguishes a toast that
    # correctly merged N held turns from one that fired because the gate crashed
    # and failed open (which logs its own "coalesce gate error" line and
    # coalesced=0). Replay/attribution tooling reads both fields.
    log("notify event=%s project=%s elapsed=%s desktop=%s clawgate=%s coalesced=%d"
        % (event, project, elapsed, d, c, held))


def handle():
    if disabled():
        return

    raw = sys.stdin.read()
    if not raw:
        return
    data = json.loads(raw)

    event = data.get("hook_event_name", "")
    session = data.get("session_id", "")
    cwd = data.get("cwd", "") or os.getcwd()

    os.makedirs(CACHE_DIR, exist_ok=True)

    if event == "UserPromptSubmit":
        # Record turn start.
        try:
            with open(start_path(session), "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass
        prune_stale()
        return

    if event not in ("Stop", "SubagentStop"):
        return

    # Loop safety: already in a stop continuation -> never do anything.
    if data.get("stop_hook_active"):
        return

    sp = start_path(session)
    if not os.path.isfile(sp):
        # No recorded start (edge case: subagent w/o prompt, restart, pruned) -> skip.
        return

    try:
        with open(sp) as f:
            start = float(f.read().strip())
    except Exception:
        return

    elapsed = time.time() - start
    lp = lastnotify_path(session)

    # Read the cooldown marker BEFORE any Stop cleanup, so a Stop that follows a
    # just-fired SubagentStop can still see the marker and suppress itself.
    now = time.time()
    within_cooldown = False
    try:
        if os.path.isfile(lp):
            with open(lp) as f:
                last = float(f.read().strip())
            within_cooldown = (now - last) < cooldown()
    except Exception:  # fail-open: unreadable marker -> treat as not cooling down
        within_cooldown = False

    # Stop always ends the turn: consume the start file (and its lastnotify
    # sibling) regardless of threshold/cooldown. SubagentStop leaves them in
    # place (the parent turn continues).
    if event == "Stop":
        for p in (sp, lp):
            try:
                os.remove(p)
            except Exception:
                pass

    if elapsed < threshold():
        return

    # Per-session notification cooldown: a burst of parallel SubagentStops (N
    # subagents past the parent threshold would otherwise fire N+1 pings)
    # collapses to ONE ping; a Stop right after that SubagentStop is likewise
    # suppressed if within the cooldown. A genuinely spread-out sequence
    # (>cooldown apart) still pings each time.
    if within_cooldown:
        return

    do_notify.elapsed_str = fmt_elapsed(elapsed)
    do_notify(event, cwd, session)

    # Record this notification time so the next event within the cooldown is
    # suppressed. Only meaningful for SubagentStop (the parent turn continues);
    # on Stop the turn is over and the marker was already removed above, so skip
    # the write to avoid orphaning a marker with no start file.
    if event != "Stop":
        try:
            with open(lp, "w") as f:
                f.write(str(now))
        except Exception:
            pass


def main():
    try:
        handle()
    except Exception as e:  # never break a session
        log("error: %r" % (e,))
    # ALWAYS exit 0, ALWAYS empty stdout (best-effort side effect only).
    sys.exit(0)


if __name__ == "__main__":
    main()
