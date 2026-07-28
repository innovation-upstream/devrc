#!/usr/bin/env python3
"""`initiatives-query` — the deterministic, READ-ONLY tool surface the initiatives
OpenClaw agent calls (one tool per invocation), emitting GROUNDED JSON.

This is the "skills" half of the Phase-1 initiatives agent (see
`claudedocs/handoff-initiatives-agent-phase1-2026-07-24.md`). The OpenClaw agent does the
intent-understanding + tool-SELECTION (what a model is reliably good at, incl. COMPOUND
questions → multiple tool calls); THIS script does the deterministic store/route query and
returns facts the agent may not invent. It is the successor to `assistant.py`'s regex
`classify_intent` routing layer: the determinism stays in the TOOLS, the model drives which
tool(s) to run — so a wrong pick yields a less-relevant-but-grounded answer, never a
fabricated fact.

Design — REUSE, don't reimplement. Every tool here is one of `assistant.py`'s existing pure
tool functions (`run_tool` → `tool_blocked_on_me`/`tool_by_momentum`/… ), fed the SAME
normalized initiative views the viewer renders (`assistant.load_initiatives`), and projected
to JSON with the SAME grounded `build_facts` + deterministic `sources_of`. No new query
logic, no new store schema, no model call. Read-only: it only SELECTs the store.

Store reach — `assistant.load_initiatives` → `viewer.load_latest` → `mail-actions/_db.py`
(`MailDB`), which already honors the #156 DIRECT in-cluster mode via `MAILBOX_PG_HOST`
(+ `MAILBOX_PG_DSN`) — so inside the homelab devpod it connects straight to
`mailbox-postgres.mailbox.svc` with NO kubectl port-forward. On the workbench (dev/test) the
same call falls back to the port-forward. No code path here opens a port-forward itself.

CLI (the contract the SKILL.md documents to the agent):
    query.py <tool> [--target TEXT]
    tools (no arg):     blocked_on_me active slowing stalled most_recent live_sessions overview
    tools (--target):   status_of --target <name>      # a named initiative
                        read_handoff --target <name>   # deep detail / handoff for one
                        route --target "<signal text>" # which initiative a new signal belongs to
                        by_repo [--target <repo>]       # optional repo scope
    query.py --list     # machine-readable tool catalog (name, takes_target, description)

Output: a compact JSON object on stdout:
    {"ok": true, "tool": "...", "target": "...",
     "facts": { ...grounded, the ONLY facts the agent may state... },
     "sources": [{"slug": ..., "repo": ...}]}   # deterministic citation, from tool output
On a store-read failure: {"ok": false, "tool": ..., "error": "..."} and exit 2 (the agent is
told to report the failure, never to invent an answer). Never raises to the shell uncaught.

Deps in the devpod: psycopg2 (store) + requests (route.py's single-sourced token matcher
loads the scan, which imports chquery→requests). Both are apt-installed at devpod init.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# assistant.py lives one dir up; load it by explicit importlib path (mirrors how the whole
# initiatives package cross-loads siblings — no reliance on package layout / sys.path).
_ASSISTANT_PATH = Path(__file__).resolve().parents[1] / "assistant.py"

# tool name (agent-facing) -> assistant intent (run_tool dispatch key). Names are the agent's
# vocabulary; intents are assistant.py's internal enum. `read_handoff` is the only rename
# (assistant intent "handoff") — kept explicit for a clear agent-facing catalog.
_TOOL_TO_INTENT = {
    "blocked_on_me": "blocked_on_me",
    "active": "active",
    "slowing": "slowing",
    "stalled": "stalled",
    "most_recent": "most_recent",
    "live_sessions": "live_sessions",
    "status_of": "status_of",
    "recommend_next_step": "recommend_next_step",
    "by_repo": "by_repo",
    "route": "route",
    "read_handoff": "handoff",
    "overview": "overview",
}

# Which tools consume --target, and a one-line description for the agent-facing catalog.
_TOOLS = {
    "blocked_on_me":  (False, "Initiatives that NEED Zach's attention — either waiting on his input/call/decision/review (blocked on him) OR carrying an active live RISK (an unresolved 5xx/outage/disk/crashloop-type problem). Use for 'what's blocked on me', 'what needs my attention', 'what's at risk / on fire'."),
    "active":         (False, "Initiatives with active momentum — in-flight / what he's currently working on."),
    "slowing":        (False, "Initiatives losing momentum / cooling / slowing down."),
    "stalled":        (False, "Initiatives that are stalled / stuck / gone quiet / dormant."),
    "most_recent":    (False, "The initiative(s) he touched most recently / last worked on."),
    "live_sessions":  (False, "Initiatives with a live tmux/Claude session running right now (workbench-derived; may be empty in-cluster)."),
    "overview":       (False, "A summary bucketed list of ALL initiatives (active/slowing/stalled)."),
    "status_of":      (True,  "The current status / where he left off on ONE named initiative. --target = its name."),
    "recommend_next_step": (True, "The recommended logical NEXT STEP for ONE named initiative (grounded in its handoff next-step / open PRs / open investigations / your last prompt / status). --target = its name."),
    "read_handoff":   (True,  "Deep detail / the handoff doc for ONE named initiative. --target = its name."),
    "route":          (True,  "Which EXISTING initiative a new free-text signal/idea/task belongs to (triage). --target = the signal text."),
    "by_repo":        (True,  "Initiatives grouped by repo, or scoped to one repo. --target = repo name (optional)."),
}


def _assistant():
    spec = importlib.util.spec_from_file_location("initiatives_query_assistant", _ASSISTANT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_ASSISTANT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_query(tool: str, target: str, *, loader=None) -> dict:
    """PURE-ish: run ONE deterministic tool and project it to grounded JSON.

    `loader()` -> (views, unmatched) is injectable for tests (default:
    assistant.load_initiatives — the live store read). Reuses assistant.run_tool /
    build_facts / sources_of verbatim, so the facts + citations are byte-for-byte what the
    Q&A pipeline already produces. Returns the JSON-able result dict; raises only if the
    loader raises (main() turns that into the ok:false error contract)."""
    intent = _TOOL_TO_INTENT[tool]
    A = _assistant()
    loader = loader or A.load_initiatives
    views, unmatched = loader()
    info = {"intent": intent, "target": target or ""}
    result = A.run_tool(intent, info, views or [], unmatched or [])
    return {
        "ok": True,
        "tool": tool,
        "target": target or "",
        "facts": A.build_facts(result),
        "sources": A.sources_of(result),
    }


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="query.py",
        description="Read-only initiatives tool surface for the OpenClaw agent — run ONE "
                    "deterministic tool, emit grounded JSON. The agent selects the tool(s).")
    p.add_argument("tool", nargs="?", choices=sorted(_TOOLS),
                   help="which deterministic tool to run")
    p.add_argument("--target", default="",
                   help="the named initiative / signal text / repo (tools that take one)")
    p.add_argument("--list", action="store_true",
                   help="print the machine-readable tool catalog and exit")
    return p.parse_args(argv)


def _catalog() -> dict:
    return {"tools": [{"name": n, "takes_target": t, "description": d}
                      for n, (t, d) in sorted(_TOOLS.items())]}


def main(argv=None) -> int:
    a = _parse_args(argv)
    if a.list:
        print(json.dumps(_catalog(), ensure_ascii=False))
        return 0
    if not a.tool:
        print(json.dumps({"ok": False, "error": "missing tool (see --list)"}), file=sys.stdout)
        return 2
    try:
        out = run_query(a.tool, a.target)
    except Exception as exc:  # noqa: BLE001 - store unreachable → the ok:false contract
        print(json.dumps({"ok": False, "tool": a.tool, "target": a.target,
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
