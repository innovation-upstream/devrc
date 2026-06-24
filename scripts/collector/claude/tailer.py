#!/usr/bin/env python3
"""claude-source tailer — emit NEW Claude Code user messages as activity events.

Fifth activity source for the personal telemetry pipeline. Periodically tails the
Claude Code transcripts under ~/.claude/projects/**/*.jsonl and emits each genuine
user-typed message (or slash-command invocation) as one `source=claude` event via
the shared `emit` helper (scripts/collector/emit). The collector daemon then ships
those spool lines to ClickHouse `activity.events` like every other source.

Design:
  * REUSE the noise-filtering from scripts/session-analysis/extract_user_msgs.py:
    skip <system-reminder>/<local-command-stdout>/<command-message> blocks, lines
    starting with [Request interrupted / Caveat: / API Error, skip isMeta /
    isSidechain lines, distinguish typed messages vs slash-commands.
  * IDEMPOTENT: a state file (default ~/.local/state/activity/claude-state.json)
    records every already-emitted message `uuid`. Each run emits ONLY uuids not in
    the state set, then persists the union. Re-runs every few minutes never
    duplicate events. Lines without a uuid get a stable synthetic id derived from
    (session, content) so they are still deduped.
  * Per-message event mapping:
        source  = claude
        kind    = "command" for slash-commands, else "prompt"
        text    = the message text
        project = repo basename from cwd
        cwd     = the message cwd
        session = session id (transcript filename stem)
        app     = claude-code
        ts      = the MESSAGE timestamp (converted to CH local DateTime64 format),
                  passed explicitly so it reflects message time, not emit time.
        payload = {"gitBranch":..., "role":..., "claude_kind":"typed"|"command"}

The host is stamped by the collector daemon (ACTIVITY_HOST), so this runs
unchanged on both workbench + laptop, each tailing its own ~/.claude transcripts.
"""
from __future__ import annotations

import datetime as _dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Noise-filtering (ported from scripts/session-analysis/extract_user_msgs.py)
# --------------------------------------------------------------------------- #
SYS_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
COMMAND_MESSAGE = re.compile(r"<command-message>.*?</command-message>", re.S)
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)

_BOILERPLATE_PREFIXES = (
    "[Request interrupted",
    "Caveat: The messages below",
    "API Error",
    "API request failed",
)


def clean_text(t: str) -> str:
    """Strip synthetic harness blocks from a raw message string."""
    t = SYS_REMINDER.sub("", t)
    t = COMMAND_STDOUT.sub("", t)
    t = COMMAND_MESSAGE.sub("", t)
    return t.strip()


def extract_blocks(content) -> list[str]:
    """Return raw text strings from a message `content` (str or list of blocks).

    Only `text`-type blocks (and bare strings) are user-typed content; tool_use /
    tool_result / image / thinking blocks are ignored.
    """
    if isinstance(content, str):
        return [content]
    out: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(block.get("text", ""))
    return out


def classify(raw: str) -> tuple[str, str] | None:
    """Classify one raw block → (kind, text) or None if it is noise.

    kind is "command" for a slash-command invocation, else "typed".
    """
    if not raw:
        return None
    cmd = COMMAND_NAME.search(raw)
    if cmd:
        cname = cmd.group(1).strip()
        cargs_m = COMMAND_ARGS.search(raw)
        cargs = cargs_m.group(1).strip() if cargs_m else ""
        text = (cname + " " + cargs).strip()
        if not text:
            return None
        return ("command", text)
    txt = clean_text(raw)
    if not txt:
        return None
    # a tiny bare tag leftover (e.g. "<foo>") is noise, not a real message
    if txt.startswith("<") and txt.endswith(">") and len(txt) < 80:
        return None
    for pref in _BOILERPLATE_PREFIXES:
        if txt.startswith(pref):
            return None
    return ("typed", txt)


# --------------------------------------------------------------------------- #
# Timestamp conversion: ISO8601 (…Z) -> ClickHouse DateTime64(3) local string
# --------------------------------------------------------------------------- #
def to_ch_ts(iso: str | None) -> str | None:
    """Convert a transcript ISO timestamp to emit's '%Y-%m-%d %H:%M:%S.%3N' UTC
    format (matches emit's `date -u`). Returns None if it cannot be parsed
    (caller then lets emit auto-fill)."""
    if not iso:
        return None
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_dt.timezone.utc)  # normalize to the UTC instant
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


# --------------------------------------------------------------------------- #
# Transcript scan
# --------------------------------------------------------------------------- #
def project_basename(cwd: str | None) -> str:
    if not cwd:
        return ""
    return os.path.basename(cwd.rstrip("/"))


def message_id(obj: dict, session: str, text: str) -> str:
    """Stable identity for dedup. Prefer the line's own uuid; fall back to a hash
    of (session, content) so uuid-less lines are still deduped deterministically."""
    uuid = obj.get("uuid")
    if isinstance(uuid, str) and uuid:
        return uuid
    return "h:" + hashlib.sha1(f"{session}\x00{text}".encode("utf-8")).hexdigest()


def iter_messages(roots: list[str]):
    """Yield event-ready dicts for every genuine user message across all
    transcripts. Does NOT consult state — emission filtering happens in run()."""
    for root in roots:
        for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            project_dir = os.path.basename(os.path.dirname(path))
            # skip synthetic agent transcript dirs (subagents / workflow runs)
            if project_dir == "subagents" or project_dir.startswith("wf_"):
                continue
            session = Path(path).stem
            try:
                fh = open(path, errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") != "user":
                        continue
                    if obj.get("isMeta") or obj.get("isSidechain"):
                        continue
                    msg = obj.get("message") or {}
                    if msg.get("role") != "user":
                        continue
                    cwd = obj.get("cwd")
                    for raw in extract_blocks(msg.get("content")):
                        result = classify(raw)
                        if result is None:
                            continue
                        claude_kind, text = result
                        yield {
                            "id": message_id(obj, session, text),
                            "source": "claude",
                            "kind": "command" if claude_kind == "command" else "prompt",
                            "claude_kind": claude_kind,
                            "text": text,
                            "project": project_basename(cwd),
                            "cwd": cwd or "",
                            "session": session,
                            "app": "claude-code",
                            "ts": to_ch_ts(obj.get("timestamp")),
                            "gitBranch": obj.get("gitBranch") or "",
                            "role": msg.get("role") or "user",
                        }


# --------------------------------------------------------------------------- #
# State (idempotency)
# --------------------------------------------------------------------------- #
def state_path() -> Path:
    explicit = os.environ.get("CLAUDE_SOURCE_STATE")
    if explicit:
        return Path(explicit)
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "activity" / "claude-state.json"


def load_state(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    seen = data.get("seen") if isinstance(data, dict) else None
    return set(seen) if isinstance(seen, list) else set()


def save_state(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "seen": sorted(seen)}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)  # atomic


# --------------------------------------------------------------------------- #
# Emit
# --------------------------------------------------------------------------- #
def emit_path() -> str:
    explicit = os.environ.get("CLAUDE_SOURCE_EMIT")
    if explicit:
        return explicit
    # Prefer the symlinked sibling emit (this file lives in scripts/collector/claude).
    here = Path(__file__).resolve().parent.parent / "emit"
    if here.exists():
        return str(here)
    return str(Path.home() / ".config/activity-collector/emit")


def build_emit_args(ev: dict) -> list[str]:
    payload = json.dumps(
        {
            "gitBranch": ev["gitBranch"],
            "role": ev["role"],
            "claude_kind": ev["claude_kind"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    args = [
        "source=claude",
        f"kind={ev['kind']}",
        f"b64:text={ev['text']}",
        f"b64:project={ev['project']}",
        f"b64:session={ev['session']}",
        f"b64:cwd={ev['cwd']}",
        "b64:app=claude-code",
        f"b64:payload={payload}",
    ]
    if ev["ts"]:
        args.append(f"ts={ev['ts']}")
    return args


def emit_event(emit: str, ev: dict) -> None:
    subprocess.run([emit, *build_emit_args(ev)], check=True)


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run() -> int:
    roots_env = os.environ.get("CLAUDE_PROJECTS_DIR")
    roots = (
        [p for p in roots_env.split(os.pathsep) if p]
        if roots_env
        else [os.path.expanduser("~/.claude/projects")]
    )
    sp = state_path()
    seen = load_state(sp)
    emit = emit_path()

    emitted = 0
    new_ids: set[str] = set()
    for ev in iter_messages(roots):
        mid = ev["id"]
        if mid in seen or mid in new_ids:
            continue
        emit_event(emit, ev)
        new_ids.add(mid)
        emitted += 1

    if new_ids:
        save_state(sp, seen | new_ids)
    print(f"claude-source: emitted={emitted} state={sp} total_seen={len(seen | new_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
