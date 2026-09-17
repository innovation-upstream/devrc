#!/usr/bin/env python3
"""Read the SERVER's advertised ingest bounds out of the transcript digest.

🔴 WHY THIS EXISTS: THE FEEDER'S TAIL SIZE WAS THE REAL INGEST BOUND, AND
NOBODY COULD SEE IT. `transcript-push.sh` sent a 192 KiB tail while clawgate's
`transcript.MaxTailBytes` said 256 KiB, and the bulk push REPLACES the stored
tail — so the stored tail was pinned at 192 KiB no matter what the server
believed. Measured 2026-09-16 over 7,537 real session files: the server thought
94.23% of sessions were truncated; the DEPLOYED figure was 96.13%. Raising the
server constant alone delivered nothing, and nothing in either repo could have
noticed.

So the feeder asks. `GET /api/transcripts/digest` is the dedupe pre-flight this
script already calls on every tick, and clawgate now answers it with a `limits`
object carrying the constants it enforces. The answer therefore costs no extra
request, and the two sides cannot drift apart again.

🔴 THE SERVER'S NUMBER IS CLAMPED, NEVER ADOPTED. It bounds how many bytes this
host reads off local disk and puts on the wire, so a server that is wrong — buggy,
rolled back, or simply not the one this host thought it was talking to — must not
be able to make this script read gigabytes. The effective value is
`min(server, ceiling)`, and the ceiling lives here on the host.

🔴 AN ABSENT `limits` IS NOT AN ERROR, AND THAT IS WHAT MAKES THE TWO REPOS'
CHANGES ORDER-INDEPENDENT. A server that predates this key is answered with the
fallback — today's conservative value — so shipping this feeder BEFORE the server
changes nothing at all. The mirror case is equally deliberate: a server that
advertises a larger bound to an OLD feeder is ignored by it. Neither half is
broken by landing first.
"""

from __future__ import annotations

import json
import sys


def advertised_tail_bytes(doc: object) -> int | None:
    """Return the server's `limits.maxTailBytes`, or None when it says nothing.

    🔴 EVERY MALFORMED SHAPE RETURNS None RATHER THAN RAISING. This value is read
    on the ordinary success path of a pre-flight that has already been checked for
    HTTP status and parsed by the builder; a crash here would take down a feeder
    that is otherwise working, over a field whose absence is a supported state.
    A hostile or broken value is refused the same way — see the bounds below,
    which are what stop `"maxTailBytes": 1e12` being obeyed.
    """
    if not isinstance(doc, dict):
        return None
    limits = doc.get("limits")
    if not isinstance(limits, dict):
        return None
    v = limits.get("maxTailBytes")
    # bool is an int subclass in Python and `True` would arrive as 1.
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        return None
    return v


def effective_tail_bytes(doc: object, ceiling: int, fallback: int) -> int:
    """Decide how many tail bytes to read per session.

    The result is `min(advertised, ceiling)` when the server advertises a usable
    number, and `fallback` otherwise. It is never larger than `ceiling` and never
    smaller than 1.

    🔴 IT CAN GO DOWN AS WELL AS UP. A server that lowered its bound — a rollback,
    or a deliberate reduction — would otherwise be sent tails it rejects, and
    `NormalizePush` refuses the WHOLE push on one oversized tail, so every other
    session in the same body is lost with it. Following the server down is what
    makes this a negotiation rather than a one-way ratchet.
    """
    if ceiling <= 0:
        raise ValueError("ceiling must be positive")
    advertised = advertised_tail_bytes(doc)
    if advertised is None:
        return max(1, min(fallback, ceiling))
    return max(1, min(advertised, ceiling))


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: transcript_limits.py <digest-file> <ceiling> <fallback>", file=sys.stderr)
        return 2
    path, ceiling, fallback = argv[1], int(argv[2]), int(argv[3])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        # 🔴 STDOUT STAYS EMPTY ON EVERY FAILURE PATH. The caller assigns this
        # module's stdout to a shell variable that becomes a byte count; anything
        # merged onto it becomes that count. Diagnostics go to stderr, and the
        # caller falls back on a non-zero exit.
        print(f"transcript_limits: could not read {path}: {exc}", file=sys.stderr)
        return 1
    print(effective_tail_bytes(doc, ceiling, fallback))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
