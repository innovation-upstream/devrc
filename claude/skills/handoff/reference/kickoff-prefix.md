# Why the kickoff block's `/resume` prefix is not the deterministic hook

Rationale for step 3 of `claude/skills/handoff/SKILL.md`. Evidence only — every
rule you must follow is in the skill body. Read this when the rule looks
arbitrary, or before "improving" the kickoff block by leaning harder on the
prefix.

## Measurement 1 — a prose kickoff skipped the index entirely

MEASURED 2026-08-13: a kickoff emitted as plain prose (`Continue the <topic>
work. Canonical handoff (read first): …`) was pasted into the next session, and
that session made **zero** calls to `subsystem_recall.py` / `subsystem_touch.py`
— `/resume` was never invoked and the skill did **not** auto-fire, despite "pick
up where we left off" matching its description almost verbatim. So `/resume`
step 4 — the index READ — never ran, and the entry written by `/handoff` step 4
an hour earlier, describing the very subsystem that session was working on, was
never seen. A kickoff that only *points at* a doc gets the doc read and the
index skipped.

## Measurement 2 — CORRECTED the same day: the prefix is not sufficient either

The claim that "typing the command makes the read deterministic" was **wrong**.
Re-measured against a DISPATCHED agent given the new `/resume`-prefixed kickoff
verbatim and nothing else: **zero `Skill` tool calls, zero
`subsystem_recall.py` executions** — behaviourally identical to the prose
kickoff it replaced. It read the doc, oriented, and went straight to the named
next step.

The mechanism: a subagent receives the kickoff as prompt TEXT — there is no CLI
slash-command parsing on that path — so a leading `/resume —` reads as a topic
label, and the clause after it is a perfectly actionable instruction on its own.
This is not a corner case: dispatching implementation to a subagent is the
standing default here.

## What survived both measurements

Both sessions read the handoff DOC first, immediately, before anything else.
That is the reliable behaviour, which is why the index command lives at the TOP
OF THE DOC (step 2's template) where reading it leads into running it — and why
the `/resume` prefix is kept but never relied on alone. It costs nothing and it
does work in an interactive session, where the CLI parses it before the model
sees it.
