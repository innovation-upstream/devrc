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

Kill-switch: CLAUDE_NOTIFY=off (or 0/false/no) disables it for a session.
Config env:
  CLAUDE_NOTIFY_MIN_SECONDS  threshold in seconds (default 60)
  CLAUDE_NOTIFY              off/0/false/no -> disabled
  CLAWGATE_API_URL           clawgate base URL (else read from clawgate.env)
  CLAWGATE_HOOK_TOKEN        clawgate machine bearer token (else clawgate.env)
"""
import os
import sys
import json
import time
import shutil
import subprocess

HOME = os.path.expanduser("~")
CACHE_DIR = os.path.join(HOME, ".cache", "claude-notify")
CLAWGATE_ENV = os.path.join(HOME, ".claude", "clawgate.env")
LOG_FILE = os.path.join(HOME, ".claude", "claude-notify.log")
DEFAULT_THRESHOLD = 60
STALE_SECONDS = 24 * 60 * 60  # prune start files older than 1 day


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


def safe_session_id(sid):
    """Sanitize a session id for use as a filename (never traverse)."""
    keep = [c for c in (sid or "") if c.isalnum() or c in "-_"]
    return "".join(keep)[:128]


def start_path(sid):
    return os.path.join(CACHE_DIR, safe_session_id(sid) + ".start")


def prune_stale():
    """Best-effort: drop start files older than a day (crashed/abandoned turns)."""
    try:
        now = time.time()
        for name in os.listdir(CACHE_DIR):
            if not name.endswith(".start"):
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
    d = notify_desktop(title, body)
    # clawgate (phone push) is a FALLBACK: only when there's no local desktop
    # toast (i.e. the headless workbench / away-from-laptop). Firing both would
    # push a redundant phone card while the user is sitting at the laptop.
    c = notify_clawgate(title, body, host, project, cwd, session) if not d else False
    log("notify event=%s project=%s elapsed=%s desktop=%s clawgate=%s"
        % (event, project, elapsed, d, c))


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

    # Stop always ends the turn: consume the start file regardless of threshold.
    # SubagentStop leaves it in place (the parent turn continues).
    if event == "Stop":
        try:
            os.remove(sp)
        except Exception:
            pass

    if elapsed < threshold():
        return

    do_notify.elapsed_str = fmt_elapsed(elapsed)
    do_notify(event, cwd, session)


def main():
    try:
        handle()
    except Exception as e:  # never break a session
        log("error: %r" % (e,))
    # ALWAYS exit 0, ALWAYS empty stdout (best-effort side effect only).
    sys.exit(0)


if __name__ == "__main__":
    main()
