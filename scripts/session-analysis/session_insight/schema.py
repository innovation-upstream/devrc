#!/usr/bin/env python3
"""schema — the single source of truth for the `session-insight` payload.

Shared by prepare.py (embeds the schema block + anti-confab contract into each
input.json), write.py (validates every result before emitting), consolidate.py
(schema-quarantines a bad result) and insights.py (reads the field names / vocab
so the report is a drop-in over the payload).

The QUALITATIVE fields are produced by the model. Deterministic COUNTS are NEVER
produced by the model — they live in Layer A (`session-summary`) and are handed to
the model as `ground_truth` (see prepare.py + the anti-confabulation contract
below). `validate()` hard-fails only the closed enums (`outcome`, `session_type`,
`leverage`, `workflow_gap.kind`) and the unreadable/reason invariant; it SOFT-fails
(a warning, never a rejection) on out-of-vocab category tags so the controlled
vocabularies can grow without breaking extraction (decision O2).
"""
from __future__ import annotations

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- #
# Controlled vocabularies (extensible — soft-fail on unknowns)
# --------------------------------------------------------------------------- #
GOAL_CATEGORIES = [
    "infra", "deploy", "feature", "bugfix", "refactor", "config",
    "docs", "research", "ops", "review", "chore", "data",
]
SESSION_TYPES = [
    "feature_build", "bugfix", "deployment", "investigation", "refactor",
    "config_change", "research", "review", "chore", "exploration",
]
FRICTION_CATEGORIES = [
    "wrong_approach", "repeated_correction", "tool_error", "permission_block",
    "context_loss", "hallucination", "missing_info", "env_breakage",
    "slow_feedback",
]
TOIL_CATEGORIES = [
    "env-setup", "deploy", "debugging", "context-gathering", "boilerplate",
    "manual-verification", "data-wrangling", "other",
]

# --------------------------------------------------------------------------- #
# Closed enums (hard-fail on an out-of-set value)
# --------------------------------------------------------------------------- #
OUTCOMES = [
    "fully_achieved", "mostly_achieved", "partially_achieved",
    "not_achieved", "unclear",
]
LEVERAGES = ["high", "medium", "low"]
WORKFLOW_GAP_KINDS = [
    "missing_tool", "missing_doc", "missing_automation",
    "config_gap", "knowledge_gap",
]

# --------------------------------------------------------------------------- #
# claude_helpfulness (1–5) anchors (decision O3)
# --------------------------------------------------------------------------- #
HELPFULNESS_ANCHORS = {
    5: "Claude materially drove the win",
    4: "clearly helped",
    3: "mixed / neutral",
    2: "more hindrance than help",
    1: "mostly got in the way",
}

# --------------------------------------------------------------------------- #
# The anti-confabulation contract (spec §7) — embedded in every input.json AND
# reinforced in the `activity` SKILL.md. NON-NEGOTIABLE.
# --------------------------------------------------------------------------- #
ANTI_CONFABULATION_CONTRACT = (
    "ANTI-CONFABULATION CONTRACT. The `ground_truth` block holds DETERMINISTIC "
    "counts computed from the transcript (tool_counts, tokens, git_commits, "
    "files_modified, lines_added/removed, tool_errors, interruptions, models, "
    "durations). These are FACTS. You MUST NOT contradict or restate them as if "
    "you counted them, and you MUST NOT invent any count, limit, or metric of "
    "your own (there is no \"output-token maximum\" — that earlier story was a "
    "confabulation; do not reproduce that failure mode). Your job is ONLY the "
    "qualitative facets in the schema: goal, outcome, helpfulness, friction "
    "DESCRIPTIONS, successes, and the automation/toil/gap observations. If the "
    "(chunked) transcript is too degraded, truncated, or ambiguous to judge a "
    "facet honestly, set `unreadable=true` with a one-line `unreadable_reason` "
    "and leave the qualitative fields empty — flag it honestly rather than "
    "fabricate. `friction_counts` are your qualitative tallies of INTERACTION "
    "friction (wrong approaches, repeated corrections); they are distinct from "
    "Layer A's mechanical `tool_errors`. `<REDACTED:...>` tokens are scrubbed "
    "secrets — treat them as opaque and never guess the original value."
)


# --------------------------------------------------------------------------- #
# The schema block embedded in each input.json (what the live session reads)
# --------------------------------------------------------------------------- #
def schema_block() -> dict:
    """A self-contained description of the required result.json, embedded in every
    input.json so the extracting session/subagent needs no external doc."""
    return {
        "schema_version": SCHEMA_VERSION,
        "anti_confabulation_contract": ANTI_CONFABULATION_CONTRACT,
        "helpfulness_anchors": {str(k): v for k, v in HELPFULNESS_ANCHORS.items()},
        "controlled_vocabularies": {
            "goal_categories": GOAL_CATEGORIES,
            "session_types": SESSION_TYPES,
            "friction_categories": FRICTION_CATEGORIES,
            "toil_categories": TOIL_CATEGORIES,
        },
        "closed_enums": {
            "outcome": OUTCOMES,
            "leverage": LEVERAGES,
            "workflow_gap.kind": WORKFLOW_GAP_KINDS,
        },
        "fields": {
            "schema_version": "int — always %d for this version" % SCHEMA_VERSION,
            "session": "str — echo the input's `session` for self-containment",
            "underlying_goal": "str — one sentence: what the user was ACTUALLY "
                               "trying to accomplish (not the literal first prompt)",
            "goal_categories": "list[str] — 1–3 tags from goal_categories vocab",
            "outcome": "enum — one of the outcome values",
            "session_type": "enum — one of the session_types values",
            "claude_helpfulness": "int 1–5 (see helpfulness_anchors)",
            "friction_counts": "dict[str,int] — friction-category → count "
                              "(INTERACTION friction, distinct from tool_errors); "
                              "empty dict = no notable friction",
            "friction_detail": "list[str] — ≤5 short concrete descriptions of the "
                              "notable friction moments",
            "primary_success": "str — the single most valuable thing accomplished; "
                              "\"\" if not_achieved",
            "brief_summary": "str — 1–3 neutral sentences (also copied to the "
                            "event `text` column)",
            "automation_opportunity": "object or null — {present, description, "
                                      "trigger, leverage(high|medium|low), evidence}",
            "recurring_toil": "object or null — {present, description, "
                             "category(toil vocab), frequency_hint}",
            "workflow_gap": "object or null — {present, description, "
                           "kind(closed enum)}",
            "unreadable": "bool — honesty flag; transcript could not be judged",
            "unreadable_reason": "str — required non-empty iff unreadable=true; "
                               "else \"\"",
        },
        "notes": [
            "When a facet (automation_opportunity/recurring_toil/workflow_gap) is "
            "genuinely absent, emit null — NOT a {present:false} husk.",
            "Counts come from `ground_truth`; NEVER recount from the transcript.",
        ],
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(payload) -> list[str]:
    """Return a list of HARD-error strings (empty list = valid).

    Hard-fails: non-dict payload, missing required field, wrong type on a
    required field, an out-of-set CLOSED enum (`outcome`/`session_type`/
    `leverage`/`workflow_gap.kind`), `claude_helpfulness` outside 1–5, and the
    unreadable/reason invariant. Out-of-vocab CATEGORY tags are NOT hard errors —
    see `vocab_warnings()`.
    """
    errs: list[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a JSON object"]

    unreadable = payload.get("unreadable")
    if not isinstance(unreadable, bool):
        errs.append("`unreadable` must be a bool")
        unreadable = bool(unreadable)

    reason = payload.get("unreadable_reason", "")
    if not isinstance(reason, str):
        errs.append("`unreadable_reason` must be a string")
        reason = ""
    if unreadable and not reason.strip():
        errs.append("`unreadable_reason` is required (non-empty) when unreadable=true")
    if not unreadable and reason.strip():
        errs.append("`unreadable_reason` must be empty when unreadable=false")

    if not isinstance(payload.get("session", ""), str):
        errs.append("`session` must be a string")
    if not isinstance(payload.get("brief_summary", ""), str):
        errs.append("`brief_summary` must be a string")

    # Qualitative facets are only meaningful (and only enum-checked) for a
    # READABLE session. An unreadable row legitimately carries empty facets.
    if not unreadable:
        if not isinstance(payload.get("underlying_goal", ""), str):
            errs.append("`underlying_goal` must be a string")
        gc = payload.get("goal_categories")
        if not isinstance(gc, list):
            errs.append("`goal_categories` must be a list")

        outcome = payload.get("outcome")
        if outcome not in OUTCOMES:
            errs.append(f"`outcome`={outcome!r} not in {OUTCOMES}")
        stype = payload.get("session_type")
        if stype not in SESSION_TYPES:
            errs.append(f"`session_type`={stype!r} not in {SESSION_TYPES}")

        help_ = payload.get("claude_helpfulness")
        if not _is_int(help_) or not (1 <= help_ <= 5):
            errs.append("`claude_helpfulness` must be an int in 1–5")

        fc = payload.get("friction_counts")
        if not isinstance(fc, dict):
            errs.append("`friction_counts` must be a dict")
        else:
            for k, v in fc.items():
                if not _is_int(v):
                    errs.append(f"`friction_counts[{k!r}]` must be an int count")
        fd = payload.get("friction_detail")
        if not isinstance(fd, list):
            errs.append("`friction_detail` must be a list")

    # Enriched objects (null when absent — hard-check the CLOSED enums inside).
    errs += _validate_obj(payload.get("automation_opportunity"),
                          "automation_opportunity",
                          closed={"leverage": LEVERAGES}, required=("description",))
    errs += _validate_obj(payload.get("recurring_toil"), "recurring_toil",
                          closed={}, required=("description",))
    errs += _validate_obj(payload.get("workflow_gap"), "workflow_gap",
                          closed={"kind": WORKFLOW_GAP_KINDS},
                          required=("description",))
    return errs


def _validate_obj(obj, name, closed: dict, required: tuple) -> list[str]:
    if obj is None:
        return []
    if not isinstance(obj, dict):
        return [f"`{name}` must be an object or null"]
    errs = []
    for field in required:
        if not str(obj.get(field, "")).strip():
            errs.append(f"`{name}.{field}` is required")
    for field, allowed in closed.items():
        if obj.get(field) not in allowed:
            errs.append(f"`{name}.{field}`={obj.get(field)!r} not in {allowed}")
    return errs


def vocab_warnings(payload) -> list[str]:
    """Return SOFT warnings for out-of-vocab category tags (never a rejection)."""
    if not isinstance(payload, dict):
        return []
    warns: list[str] = []
    for tag in payload.get("goal_categories") or []:
        if tag not in GOAL_CATEGORIES:
            warns.append(f"goal_category {tag!r} not in the controlled vocab")
    for tag in (payload.get("friction_counts") or {}):
        if tag not in FRICTION_CATEGORIES:
            warns.append(f"friction_category {tag!r} not in the controlled vocab")
    toil = payload.get("recurring_toil")
    if isinstance(toil, dict):
        cat = toil.get("category")
        if cat is not None and cat not in TOIL_CATEGORIES:
            warns.append(f"recurring_toil.category {cat!r} not in the controlled vocab")
    return warns
