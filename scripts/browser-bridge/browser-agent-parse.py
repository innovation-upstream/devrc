#!/usr/bin/env python3
"""browser-agent-parse.py — extract the browser-agent's final-answer schema from
an opencode `--format json` transcript.

opencode `run --format json` emits **newline-delimited JSON events** (one object
per line), NOT a single JSON document. Observed shape (opencode 1.18.4, verified
live on this host):

    {"type":"step_start","part":{"type":"step-start",...}}
    {"type":"text","part":{"type":"text","text":"...assistant text...",...}}
    {"type":"step_finish","part":{"type":"step-finish","reason":"stop",
                                  "tokens":{...},"cost":...}}

Tool calls appear as additional event lines; we ignore everything except the
assistant's TEXT parts. The agent's harness requires its final message to be ONE
JSON object matching the schema:

    {"answer":str, "evidence":[str,...], "steps_used":int,
     "status":"ok"|"partial"|"blocked"}

This reader is deliberately DEFENSIVE about the envelope (the two hosts are on
different opencode versions — laptop 1.18.4, workbench 1.17.20 — and the event
shape may drift): it concatenates every text part it can find (several nesting
shapes accepted), then extracts the LAST balanced top-level JSON object that
parses and carries the schema keys. It NEVER prints raw page content — only the
normalized compact schema.

Usage:
    browser-agent-parse.py <transcript.jsonl>   # or `-` for stdin

Exit 0 + prints the compact schema JSON on success; exit 2 if no valid schema
object is present (the wrapper then retries once, else returns `blocked`).
"""
from __future__ import annotations

import json
import sys

VALID_STATUS = ("ok", "partial", "blocked")


def _text_from_event(ev):
    """Best-effort: pull the assistant TEXT out of one decoded event, tolerant of
    version-skew envelope shapes. Returns a string ("" if this event carries no
    assistant text)."""
    if not isinstance(ev, dict):
        return ""
    # Observed shape: {"type":"text","part":{"type":"text","text":"..."}}
    part = ev.get("part")
    if isinstance(part, dict):
        if part.get("type") in ("text", None) and isinstance(part.get("text"), str):
            # Only treat as assistant text when the OUTER type says text (avoids
            # picking up tool args), OR the part explicitly is a text part.
            if ev.get("type") == "text" or part.get("type") == "text":
                return part["text"]
    # Defensive alternates: {"type":"text","text":"..."} or {"text":"..."}.
    if ev.get("type") == "text" and isinstance(ev.get("text"), str):
        return ev["text"]
    return ""


def collect_text(lines) -> str:
    """Concatenate every assistant text part across the JSONL transcript, in
    order. Non-JSON lines are skipped (opencode may interleave nothing on stdout,
    but a stray line must never crash the parse)."""
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except (ValueError, TypeError):
            continue
        t = _text_from_event(ev)
        if t:
            out.append(t)
    return "".join(out)


def _iter_json_objects(text: str):
    """Yield every balanced top-level {...} substring of `text` (brace-matched,
    string-aware so a `}` inside a JSON string doesn't end an object early)."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start:i + 1]


def _normalize(obj):
    """Validate + normalize a candidate dict to the exact schema, or None if it
    is not a schema object (missing the identifying keys)."""
    if not isinstance(obj, dict):
        return None
    # Identify a schema object by the presence of both `answer` and `status`
    # (a plain `{}` or some unrelated JSON blob is rejected → keep scanning).
    if "answer" not in obj or "status" not in obj:
        return None
    answer = obj.get("answer")
    answer = answer if isinstance(answer, str) else json.dumps(answer,
                                                               ensure_ascii=False)
    ev = obj.get("evidence")
    if isinstance(ev, list):
        evidence = [e if isinstance(e, str) else json.dumps(e, ensure_ascii=False)
                    for e in ev]
    elif ev is None:
        evidence = []
    else:
        evidence = [ev if isinstance(ev, str) else json.dumps(ev,
                                                              ensure_ascii=False)]
    steps = obj.get("steps_used")
    try:
        steps_used = int(steps)
    except (TypeError, ValueError):
        steps_used = 0
    status = obj.get("status")
    status = status if status in VALID_STATUS else "blocked"
    return {"answer": answer, "evidence": evidence,
            "steps_used": steps_used, "status": status}


def extract_schema(text: str):
    """Return the LAST valid schema object found anywhere in `text`, or None.

    'Last' because the harness demands the final message be the schema — if the
    model narrated earlier and then emitted JSON, the final JSON is the answer.
    """
    found = None
    for cand in _iter_json_objects(text):
        try:
            obj = json.loads(cand)
        except (ValueError, TypeError):
            continue
        norm = _normalize(obj)
        if norm is not None:
            found = norm
    return found


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: browser-agent-parse.py <transcript.jsonl|->\n")
        return 2
    src = argv[0]
    if src == "-":
        lines = sys.stdin.read().splitlines()
    else:
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            sys.stderr.write(f"browser-agent-parse: cannot read {src}: {e}\n")
            return 2
    schema = extract_schema(collect_text(lines))
    if schema is None:
        return 2
    sys.stdout.write(json.dumps(schema, ensure_ascii=False,
                                separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
