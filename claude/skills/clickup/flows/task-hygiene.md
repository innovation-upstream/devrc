# flow: ClickUp task hygiene — before you create, and before you close

🔴 **NOTHING ENFORCES ANY OF THIS.** clawgate's equivalents
(`~/.claude/skills/clawgate/flows/task-authoring.md`,
`~/.claude/skills/clawgate/flows/task-pickup.md`) work because two PreToolUse/Stop
hooks route to them and BLOCK. There is no ClickUp hook, no server check, and no
gate anywhere in this skill that reads this file. It is a convention you follow or
do not follow, and it is written down here so it can at least be *cited*.

That warning is first because a doc that reads as a gate while providing none is
worse than none — it stops anyone looking. Do not summarise this file elsewhere as
"the ClickUp task gate". It is not one.

---

## 1. Before you CREATE — pre-verify, and expect to create nothing

Every question you can answer yourself is one you must not spend on a human.
Arrive with findings.

| question | how you answer it |
|---|---|
| Is it already done? | `git log --oneline -20 -- <path>` in the owning repo; `gh pr list --search "<keyword>" --state all` |
| Does a task already exist? | `node query.mjs search "<keyword>"` — **search before create**, every time. Duplicates on a team board are worse than on a private one: two people work them |
| Is it deliberately off / suspended? | read the config or manifest — a suspended kustomization, an `enabled: false`, a commented-out timer. A task to "fix" something that is off ON PURPOSE is worse than no task |
| Which repo / directory does it land in? | resolve it now; a ticket that does not say is a ticket someone has to come back to you about |
| Is there a verifier already? | an existing test target, a gate script, an alert — cheaper than inventing one |

**Reporting "already done" / "already exists" / "deliberately off" and creating
NOTHING is the successful outcome.** Say so, with what you measured. It is not a
failure to have produced no object.

📊 **Why this is first, measured.** `scripts/task-spec-drafter/README.md` ran the
unattended version of exactly this problem over a real batch of **8 inbound
tickets: 1 was genuinely actionable.** The other 7 dissolved on verification —
already done, stale, underspecified, or deliberately switched off. A naive
title-only pass would have filed all 8, and one of its drafts would have crashed a
deliberately-suspended service. Pre-verification is not diligence theatre; it is
where 7 of 8 objects go away.

**Name the closing condition, or do not file.** What counts as one — and who or
what checks it — is defined at question 1 of
`~/.claude/skills/clawgate/flows/task-authoring.md`. That is the single source; it
is deliberately not restated here, and a guard
(`scripts/tests/test_closing_condition_single_source.py`) fails if anyone restates
it anywhere. If you can name neither a mechanical check nor a named human
judgement over named evidence, say so in your reply instead of creating an object
nobody can close.

**Record it mechanically too**: pass `--cond` on `create` **or `subtask`**
(`gh_pr_merged:<owner>/<repo>#<n>`, `alert_cleared:<name>`, `cmd_exit_zero:<id>`,
`metric_below:<id>`, or `manual:<who>` naming the human who checks it); a batch
plan takes the same value as a `cond` key per task and per subtask. Omitting it is
not fatal — it records `cond=unstated` and warns — but `unstated` is a recorded
admission that you filed something nobody can close, and it is greppable precisely
so those can be counted. 🔴 **You cannot pass `unstated` yourself.** It is what the
code writes when it OBSERVED that you named nothing; a caller able to assert it
would turn that observation back into a claim, and the count would stop measuring
anything. It is rejected at the create path, not only at the CLI.

**Body: keep it to Context + Acceptance criteria + Non-goals + Verifier.**

- **Non-goals** — what a reader would plausibly do and should not. The cheapest
  scope control there is.
- **Verifier** — the exact command, test target or metric. "Read the diff" is not a
  verifier.

## 2. Before you CLOSE — the completion write-back

**One completion comment, carrying evidence PER ACCEPTANCE CRITERION** — one line
each, naming what proves it — **plus an explicit `NOT verified:` list**.

> "All green" with no per-criterion mapping is not a completion report.

📊 **Why, measured.** An entire skill exists solely to answer the question "was
this actually done?" — `~/.claude/skills/check-clickup-addressed/SKILL.md` reads
session transcripts because the tasks themselves do not say. The one end-to-end
run recorded in this repo — `claude/skills/check-clickup-addressed/reference/validation-history.md`,
"End-to-end after round 3" (2026-08-20) — came back **0 addressed, 0 partial,
0 open, 2 unclear**: it could establish nothing about either task it looked at. A
per-criterion mapping is what would have let that verdict be read off the task
itself, instead of reconstructed from a transcript and still coming back
"unclear".

The `NOT verified:` list is the load-bearing half. It is the only place the reader
learns what you did *not* check, and omitting it converts an honest partial into a
false completion.

## 3. Before you set a status — the self-grading gate

**If YOU derived the acceptance criteria rather than the author specifying them,
you MUST NOT mark the task complete.** Report what you did, leave it for the
author, and say plainly that the criteria were yours.

Detector, not a judgement call: a heading matching `## Acceptance criteria`
(case-insensitive) in the body the author wrote → **AUTHOR-SPECIFIED**. Anything
else — including a body that merely *reads* like criteria — means you **DERIVED**
them. Writing the criteria and grading them is one act, in either order.

🔴 **The ClickUp-specific reason, and it inverts the intuition you may have brought
from clawgate.** On clawgate a dispatched agent *structurally cannot* set
`complete` (the agent route refuses it) and its comments are authored `claude-code`,
a distinct identity from `user`. **On ClickUp the API token resolves to a HUMAN
identity.** There is no bot identity, no separate author, and no status the token
is forbidden. An agent closing a ticket is **indistinguishable from the human
closing it**, in the UI and in the API. ClickUp has *weaker* structural protection
than clawgate, and therefore needs this discipline **more**, not less — the place
where the machine cannot stop you is exactly the place the convention has to hold.

## 4. Comment budget — two per task, never per turn

**Exactly two comments: one when you start, one when you finish.** ClickUp
notifies every watcher on every comment, so per-turn self-reporting spams the whole
team rather than one board owner. Per-turn chatter was measured as noise and
removed once on clawgate; do not reintroduce it here, where the blast radius is
other people's notifications.

The pre-start comment is the point at which someone can object *before* the work
rather than after: state the criteria (quoted verbatim, and labelled
`DERIVED — not author-specified` if they are yours), the plan, the non-goals, and
the assumptions that would change the work if wrong.

---

## Deliberately NOT ported from clawgate — do not re-add these

- **The `in_progress` 409 ordering trap.** That is an artifact of clawgate's own
  API (a task in progress refuses body PATCHes). ClickUp has no equivalent guard,
  so the "flip status LAST" ordering rule has nothing to protect against here.
- **Hard tag validation and reserved namespaces** (`runbook:`, `gate:`,
  `initiative:`). clawgate 400s on an invalid tag; ClickUp tags are **Space-level
  and must pre-exist**, so tagging here is best-effort — a missing space tag warns
  on stderr and the task is still created. There is no closed namespace to police.
- **The full 6-section body template.** Too heavy for a ticket other humans have to
  read. Keep Non-goals and Verifier (section 1); drop the rest.
