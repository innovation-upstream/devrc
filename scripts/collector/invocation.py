#!/usr/bin/env python3
"""invocation — best-effort adoption/impact telemetry for the "gametape" tools.

We ship productivity tools but some die unused (a measured past failure: opt-in
commands didn't stick). This helper lets a tool emit ONE deterministic
`source=tool kind=invocation` event per run so `adoption-scan.py` can tell which
shipped tools are actually USED and what OUTCOMES they yield — vs sitting idle.

CONTRACT (two hard promises)
----------------------------
  * BEST-EFFORT: emitting must NEVER change the calling tool's exit code, output,
    or behaviour, and must never block it. Every failure path (spool module
    absent, spool unwritable, a malformed dim, telemetry off) is swallowed — a
    tool with the collector absent behaves EXACTLY as before. `emit_invocation`
    never raises; call sites still wrap it as defence-in-depth.

  * PRIVACY: only the tool name, a low-cardinality `outcome` enum, and the small
    caller-supplied `dims` leave the process. NEVER raw --query text, ticket
    search terms, ticket bodies, file contents, or secrets. Callers pass ONLY
    safe low-cardinality dims (preset/cluster/verdict NAMES, version strings,
    booleans, detected-stack lists). This module additionally hard-caps the
    number and length of dims as defence-in-depth, but it is NOT a scrubber —
    the call site is the privacy boundary.

Reuses the pipeline's `spool_emit` (the same v1 spool line the collector daemon
already ships) — no new schema, no subprocess fork, stdlib only. If the daemon
is not running the line simply accumulates locally and is never shipped, which
is the graceful telemetry-off no-op.

Event shape (payload is a JSON string):
    source=tool  kind=invocation  text=<tool>  duration_ms=<int?>  exit_code=<int?>
    payload = {"tool": <tool>, "outcome": <enum>, ...flattened safe dims...}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# spool_emit is the single source of truth for the v1 line format; it lives in
# the sibling keylog/ dir (shared by the keylogger + browser receiver + i3).
_KEYLOG = Path(__file__).resolve().parent / "keylog"
if str(_KEYLOG) not in sys.path:
    sys.path.insert(0, str(_KEYLOG))

SOURCE = "tool"
KIND = "invocation"

# Defence-in-depth caps. Safe dims are short NAMES/versions/booleans; anything
# larger is almost certainly a mistake (a leaked query/body), so we bound it.
_MAX_DIMS = 12
_MAX_KEY_LEN = 64
_MAX_VALUE_LEN = 120
_MAX_LIST_ITEMS = 12


def sanitize_dims(dims) -> dict:
    """Coerce caller dims to a small, JSON-safe, bounded dict. Pure.

    Bools/ints/floats pass through; strings are truncated; short lists become
    lists of truncated strings; everything else is stringified+truncated. This
    caps cardinality/size but is NOT a content scrubber — the call site must only
    pass safe values in the first place (see the module PRIVACY note).
    """
    out: dict = {}
    if not isinstance(dims, dict):
        return out
    for k, v in list(dims.items())[:_MAX_DIMS]:
        key = str(k)[:_MAX_KEY_LEN]
        if isinstance(v, bool) or v is None:
            out[key] = v
        elif isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, (list, tuple)):
            out[key] = [str(x)[:_MAX_VALUE_LEN] for x in list(v)[:_MAX_LIST_ITEMS]]
        else:
            out[key] = str(v)[:_MAX_VALUE_LEN]
    return out


def build_fields(tool, outcome, dims=None, duration_ms=None, exit_code=None) -> dict:
    """Build the spool field dict for one invocation event. Pure/testable.

    `outcome` and the flattened `dims` live in the JSON `payload`; the tool name
    is ALSO put in `text` so the report can group by tool without parsing JSON.
    """
    payload = {"tool": str(tool), "outcome": str(outcome)}
    payload.update(sanitize_dims(dims))
    fields: dict = {
        "source": SOURCE,
        "kind": KIND,
        "text": str(tool),
        "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }
    if duration_ms is not None:
        try:
            fields["duration_ms"] = int(duration_ms)
        except (ValueError, TypeError):
            pass
    if exit_code is not None:
        try:
            fields["exit_code"] = int(exit_code)
        except (ValueError, TypeError):
            pass
    return fields


def emit_invocation(tool, outcome, dims=None, duration_ms=None, exit_code=None,
                    spool_dir=None) -> str:
    """Emit ONE invocation event, best-effort. Returns the written line, or "" on
    ANY failure. NEVER raises — telemetry must not break its caller."""
    try:
        import spool_emit as SE  # imported lazily so an absent module is a no-op
        fields = build_fields(tool, outcome, dims=dims,
                              duration_ms=duration_ms, exit_code=exit_code)
        return SE.emit(fields, spool_dir=spool_dir)
    except Exception:  # noqa: BLE001 — best-effort telemetry never raises
        return ""
