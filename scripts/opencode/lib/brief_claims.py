#!/usr/bin/env python3
"""brief_claims — make an OPENCODE dispatch brief carry its sources.

🔴 READ THE SCOPE BEFORE THE RATIONALE. THIS GUARD COVERS ~0.9% OF THE BRIEFS
THIS MACHINE PRODUCES, AND IT DOES NOT COVER THE ONES THE EVIDENCE CAME FROM.
--------------------------------------------------------------------------------
It validates exactly one thing: a brief passed to `opencode-dispatch`, which is
the only committed code path an opencode brief crosses. Measured rates:

    opencode dispatches  ~1.2 / day   <- COVERED by this module
    Claude Agent-tool    ~137 / day   <- NOT COVERED. No chokepoint exists.
    ------------------------------------------------------------------
    covered share         ~0.9%

Basis, stated so it can be re-checked and argued with: **6 briefs surviving on
disk across 3 project directories over Aug 21-25 2026** (5 days) — a FLOOR, not
a total, because `.opencode-dispatch/` is deleted with its project and the local
telemetry spool had already shipped — against **1,921 `Agent` tool calls in the
audit's 14-day window**. The two windows differ; treat 0.9% as an order of
magnitude, not a precise ratio. It is if anything an OVER-estimate of coverage:
the clawgate `build_task_body()` producers are a third brief surface counted in
neither number.

🔴 AND THE ASYMMETRY THAT MATTERS MOST: **every measured instance below came
from the UNCOVERED surface.** The wrong root cause reached three *subagent*
briefs. The three subagents who opened their reports correcting a stale brief
were Agent-tool subagents. Not one of the four was an opencode dispatch.

So this module is a bridgehead, not a fix. Do NOT read it — or cite it — as
evidence that premise-propagation is handled; a guard whose description reads
wider than its implementation is worse than none, because it stops anyone
looking. What it genuinely provides: the schema, the vocabulary and the refusal
semantics that a wider guard would reuse, proven against a real dispatch path.

THE FAILURE CLASS IT IS A BRIDGEHEAD AGAINST (measured, not hypothetical)
-------------------------------------------------------------------------
A 14-day audit of 443 sessions found WRONG PREMISES PROPAGATING INTO SUBAGENT
BRIEFS independently in FOUR OF SIX audit slices — the highest-cost error class
found, and the one the adversarial audit ladder is structurally blind to.
The measured instances:

  * A session built an entire storage layer on a belief lifted from a STALE code
    comment and a STALE README. FOUR adversarial audit rounds read past it.
  * A wrong root-cause diagnosis was filed as a GitHub issue, endorsed as the
    session's best finding, and pushed into THREE subagent briefs before being
    retracted at session end. The recommended fix would have preserved every
    false pass across a 69-site sweep.
  * A homelab session INFERRED an auth constraint from a token prefix, reported
    it as established fact several times, wrote it into a handoff as the ranked
    next step, and it propagated into a downstream session's opening brief.
    The session's own words: "my inference presented as fact."
  * Three independent subagents opened their reports by correcting the brief
    they were handed: "The brief was stale — the code won."

🔴 WHY THIS IS NOT A PROSE RULE.
The audit ladder that missed this class four times over IS instruction-based.
Adding a fifth instruction is the same mechanism that already failed. RULES.md is
explicit: prefer deterministic/structural fixes over prompt-tuning. So the check
lives in `opencode-dispatch preflight`, it is schema-validated, and the dispatch
REFUSES (rc 6) rather than warning.

THE SURFACE — A `claims` FENCE, PARSED AS JSON
-----------------------------------------------
A brief declares its load-bearing claims in one fenced block::

    ```claims
    [
      {"claim":  "the preview DB is a clone, not production",
       "source": "https://pr-4260.example.com/api/health",
       "read_at": "2026-08-21",
       "basis":  "measurement"},
      {"claim":  "the avatar refresh is driven by the localStorage roster",
       "source": "src/components/AccountSwitcher.tsx:42",
       "read_at": "2026-08-21",
       "basis":  "inference"}
    ]
    ```

JSON, not YAML, and stdlib only: `opencode-dispatch` runs under a bare
`/usr/bin/env python3` with no guaranteed site-packages, and every other yaml
user in this repo has to guard the import. A parse error here must name a line,
not degrade to "no claims found" — an unparseable block and an absent one are
different facts (the same discipline `preflight` already applies to
`NOT EXAMINED` vs `none`).

🔴 `basis` IS THE WHOLE POINT, AND IT IS A CLOSED ENUM.
`measurement` vs `inference` is the exact distinction that failed in the homelab
instance above: an inference was reported as established fact and propagated. A
brief that cannot tell its reader which of the two it is hands on a belief with
its provenance stripped. So `basis` hard-fails on anything outside the set — a
misspelling must not silently read as a measurement.

TWO TIERS, MATCHING `session_insight/schema.py`
-----------------------------------------------
`validate()` returns HARD errors (the dispatch is refused). `key_warnings()`
returns SOFT warnings that are printed and never reject — so the citation
vocabulary can grow without breaking every dispatch, which is decision O2 in
`scripts/session-analysis/session_insight/schema.py`, followed here rather than
reinvented.

🔴 TWO THINGS THIS STRUCTURALLY CANNOT SEE — stated, not left to be discovered.

  1. THE OTHER 99%. See the scope block at the top. The Agent-tool surface has
     no committed chokepoint at all, so nothing here reaches it.
  2. AN UNDECLARED PREMISE IN THE PROSE. Within a brief it does reach, it checks
     that DECLARED claims carry sources. It cannot know the prose asserts a fifth
     premise the author never declared, because detecting a "claim" in free prose
     needs a heuristic, a heuristic over-matches, and a permanently-red gate is
     worse than no gate (`opencode-dispatch`'s own reasoning for why the glob
     check warns and the path check blocks).

What the mandatory block buys is narrower than either gap and still real: on the
path it does cover, the author cannot dispatch WITHOUT having been asked the
question, and an empty declaration is an explicit on-record assertion rather than
a silence.
"""
from __future__ import annotations

import json
import re

# --------------------------------------------------------------------------- #
# The fence
# --------------------------------------------------------------------------- #
# Matches ```claims (or ```claims json, or ~~~claims, indented, or a longer
# fence) ... fence-close.
#
# The info string is anchored to the word `claims` so an ordinary ```json block
# in a brief is NOT mistaken for a declaration — a brief full of JSON examples
# must still be refused for having declared nothing.
#
# 🔴 The close accepts AT LEAST as many fence characters as the open, per
# CommonMark, via `(?P=fchar)*` after the backreference. Without it a perfectly
# legal ```…```` pair refused as "no block" — a fail-CLOSED wart, but a confusing
# one, and every case in `test_the_fence_grammar_is_pinned_in_both_directions`
# exists because it was probed rather than assumed.
CLAIMS_FENCE_RE = re.compile(
    r"^[ \t]*(?P<fence>(?P<fchar>[`~])(?P=fchar){2,})[ \t]*claims\b[^\n]*\n"
    r"(?P<body>.*?)"
    r"^[ \t]*(?P=fence)(?P=fchar)*[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# --------------------------------------------------------------------------- #
# Closed enum (hard-fail on an out-of-set value) — the measurement/inference
# distinction the audit found being erased.
# --------------------------------------------------------------------------- #
BASES = ("measurement", "inference")

# Required on EVERY claim: what was claimed, where it was read, when, and which
# of the two kinds of knowing it is. Dropping any one of these reproduces one of
# the four measured instances.
REQUIRED_FIELDS = ("claim", "source", "read_at", "basis")

# Extensible (soft-fail on unknowns) — a citation may carry more than the four.
KNOWN_FIELDS = REQUIRED_FIELDS + ("note", "verified_by", "confidence")

# A `read_at` that does not START with a YYYY-MM-DD date is a SOFT warning, not a
# rejection: "the commit before 6a1f2c3" is a legitimate temporal anchor and
# rejecting it would push authors toward a fabricated date, which is worse.
_DATEISH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# The sentinel the report prints when a brief declares an EMPTY list. It is an
# assertion the author made on the record, not a silence, and it reads that way.
NO_CLAIMS_DECLARED = (
    "  claim citations   : NONE DECLARED — the brief asserts it carries no "
    "load-bearing claim"
)

# 🔴 The operator-facing refusal. A whole normalised sentence, pinned entire by
# the tests rather than by substring: a guard on WORDS is walkable by rewording,
# and this sentence is the tool's central claim about what it refused and why.
NO_BLOCK_ERROR = (
    "the brief declares no `claims` block. A dispatch brief must carry its "
    "sources: add a fenced ```claims block listing each load-bearing claim with "
    "`claim`, `source`, `read_at` and `basis` (measurement|inference), or "
    "declare `[]` if the brief genuinely asserts no premise the subagent would "
    "act on. Measured: wrong premises propagated into subagent briefs in four of "
    "six audit slices, and four adversarial audit rounds read past one."
)


# --------------------------------------------------------------------------- #
# Extraction + parse
# --------------------------------------------------------------------------- #
def extract_block(text: str) -> str | None:
    """The FIRST `claims` fence body, or None when the brief declares none.

    Returning None for "absent" is deliberate and is NOT the same value as `[]`
    for "declared empty" — the caller hard-fails on the first and accepts the
    second, and collapsing them would turn a refusal into an all-clear.
    """
    m = CLAIMS_FENCE_RE.search(text or "")
    return m.group("body") if m else None


def count_blocks(text: str) -> int:
    """How many `claims` fences the brief carries.

    🔴 More than one is a hard error, not a merge. `extract_block` reads the
    FIRST; a second block would then be silently unvalidated, which is exactly
    the shape where an uncited claim survives a green preflight.
    """
    return len(CLAIMS_FENCE_RE.findall(text or ""))


def parse_claims(text: str):
    """`(claims, errors)`. `claims` is None whenever `errors` is non-empty.

    🔴 THE PRECISE SCOPE OF "every failure names what went wrong", because the
    two malformations are NOT alike and an earlier draft of this docstring
    claimed more than the code does:

      * A malformed BODY — a fence that opened and closed correctly around
        unparseable JSON — is a NAMED parse error carrying a line and column. It
        must not degrade into "no claims found", which would report a parse
        failure as an absence and let the author think the check ran.
      * A malformed FENCE — unterminated, or closed with FEWER characters than
        it opened — is genuinely INDISTINGUISHABLE from an absent one to any
        regex, so it reports `NO_BLOCK_ERROR`. That is a fail-CLOSED outcome
        (rc 6, refused) and never an accepted empty declaration, which is the
        direction that matters; `test_the_fence_grammar_is_pinned_in_both_
        directions` pins all ten cases so the behaviour is deliberate.
    """
    n = count_blocks(text)
    if n == 0:
        return None, [NO_BLOCK_ERROR]
    if n > 1:
        return None, [
            f"the brief carries {n} `claims` blocks; exactly one is allowed "
            "(only the first would be validated, so the rest would ship "
            "unchecked)"
        ]

    body = extract_block(text)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        return None, [
            f"the `claims` block is not valid JSON: {e.msg} (line {e.lineno}, "
            f"column {e.colno}). It must be a JSON array of claim objects."
        ]
    if not isinstance(parsed, list):
        return None, [
            "the `claims` block must be a JSON ARRAY of claim objects, got "
            f"{type(parsed).__name__}"
        ]
    return parsed, []


# --------------------------------------------------------------------------- #
# Tier 1 — HARD validation (the dispatch is refused)
# --------------------------------------------------------------------------- #
def validate(claims) -> list[str]:
    """Return a list of HARD-error strings (empty list = valid).

    Hard-fails: a non-object entry, a missing or blank REQUIRED field, a
    non-string required field, and an out-of-set `basis`. Unknown extra keys are
    NOT hard errors — see `key_warnings()`.
    """
    errs: list[str] = []
    if not isinstance(claims, list):
        return ["`claims` must be a list"]

    for i, c in enumerate(claims):
        where = f"claims[{i}]"
        if not isinstance(c, dict):
            errs.append(f"`{where}` must be an object, got {type(c).__name__}")
            continue
        for field in REQUIRED_FIELDS:
            if field not in c:
                errs.append(
                    f"`{where}.{field}` is required — an uncited claim is the "
                    "error class this check exists for"
                )
                continue
            v = c[field]
            if not isinstance(v, str):
                errs.append(f"`{where}.{field}` must be a string, got "
                            f"{type(v).__name__}")
            elif not v.strip():
                errs.append(f"`{where}.{field}` is required (non-empty)")
        # 🔴 The closed enum, checked INDEPENDENTLY of the presence loop above so
        # a present-but-wrong `basis` is reported by its own error rather than
        # riding on a missing-field message. A misspelled basis must never be
        # read as `measurement`.
        if isinstance(c.get("basis"), str) and c["basis"].strip():
            if c["basis"] not in BASES:
                errs.append(
                    f"`{where}.basis`={c['basis']!r} not in {list(BASES)} — the "
                    "measurement/inference distinction is the specific thing "
                    "that failed (an inference was reported as established fact "
                    "and propagated into a downstream brief)"
                )
    return errs


# --------------------------------------------------------------------------- #
# Tier 2 — SOFT warnings (printed, never a rejection)
# --------------------------------------------------------------------------- #
def key_warnings(claims) -> list[str]:
    """Soft warnings for out-of-vocab citation keys and unanchored dates.

    Never a rejection: the citation vocabulary must be able to grow without
    breaking every dispatch (decision O2 in session_insight/schema.py).
    """
    if not isinstance(claims, list):
        return []
    warns: list[str] = []
    for i, c in enumerate(claims):
        if not isinstance(c, dict):
            continue
        for key in c:
            if key not in KNOWN_FIELDS:
                warns.append(
                    f"claims[{i}]: unknown citation field {key!r} — kept, but "
                    "not part of the known vocabulary"
                )
        ra = c.get("read_at")
        if isinstance(ra, str) and ra.strip() and not _DATEISH_RE.match(ra.strip()):
            warns.append(
                f"claims[{i}]: `read_at`={ra!r} does not start with a YYYY-MM-DD "
                "date — kept, but a reader cannot tell how stale this is"
            )
    return warns


# --------------------------------------------------------------------------- #
# Reporting helpers
# --------------------------------------------------------------------------- #
def basis_counts(claims) -> dict:
    """`{basis: count}` over VALID bases only, both keys always present.

    Always emitting both keys is deliberate: a report that omits `inference: 0`
    cannot be distinguished from one where the field was never computed, and a
    reassuring zero from a check wired to nothing is the failure RULES.md names.
    """
    counts = {b: 0 for b in BASES}
    if not isinstance(claims, list):
        return counts
    for c in claims:
        if isinstance(c, dict) and c.get("basis") in counts:
            counts[c["basis"]] += 1
    return counts


def inference_claims(claims) -> list[dict]:
    """The claims the receiving agent must RE-VERIFY rather than act on.

    Surfaced individually in the report — an inference that reads like a
    measurement is the whole failure, so the report must not fold them into a
    count and call it done.
    """
    if not isinstance(claims, list):
        return []
    return [c for c in claims
            if isinstance(c, dict) and c.get("basis") == "inference"]
