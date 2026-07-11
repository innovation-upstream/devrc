"""session_insight — Layer B of the Claude-session telemetry pipeline.

Turns SETTLED Claude Code sessions into `source=claude, kind=session-insight`
rows in the homelab ClickHouse `activity.events` table: the QUALITATIVE facets
(underlying goal, outcome, Claude-helpfulness, friction, and — the reason this
dataset exists — automation opportunities / recurring toil / workflow gaps).

Division of labour is FIXED by design (see
`claudedocs/spec-insights-telemetry-pr2-2026-07-11.md`):

  * DETERMINISTIC PYTHON (this package) does ALL the plumbing — select settled
    + un-extracted sessions, secret-scrub the transcript, chunk it, attach the
    Layer A deterministic rollup as GROUND TRUTH, then (after extraction)
    validate + `emit` the results.
  * THE LIVE CLAUDE SESSION (operating the `activity` skill) does the extraction
    step — read each `input.json`, write a `result.json` conforming to
    `schema.py`. There is NO `claude -p`, NO external API, NO API key. Python
    never calls an LLM.

3-phase flow:  prepare (Python) → extract (the session) → write (Python).
Operated on-demand via the `activity` skill; no timer, no daemon.
"""
