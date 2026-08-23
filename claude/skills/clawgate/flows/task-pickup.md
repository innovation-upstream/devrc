# flow: picking up a clawgate task — the comment/status ritual

**Run this on "read and evaluate clawgate task N", and on "local dispatch".** It is
enforced: `~/.claude/hooks/clawgate-writeback-guard.py` (PostToolUse watches, Stop
gates) BLOCKS the turn from ending when this session read a task, did real work
after that read, and a live re-read of the board shows no `claude-code` comment
since. The block message names this file, because a `flows/` file does not auto-fire
the way a skill description does — the hook is the router.

**The status gate that decides the FINAL status stays in `SKILL.md` → "Status
gate"** — it is the part a reader must not miss, so it loads whether or not this
flow was opened. Everything else about the pickup is here.

🔴 **The comment/status ritual is NOT optional and NOT a thing to be asked for.** Run it unprompted.

🔴 **A COMMENT is the only write that notifies a watcher.** A status flip pushes **only** on
*entering* `ready_for_review` (`notifyTaskDone`), so going `in_progress` notifies **nobody**. That is
*why* the pre-start comment exists: Zach's only chance to object **before** the work, not after.
Route-level cites: `task-api.md` → "Notifications".

```bash
clawgatectl task get <id>            # 1. READ — body + comments are BOTH already here
                                     #    (no /comments GET exists; it is 405)
#  2. EVALUATE and report to Zach. Do NOT flip status yet — see the ordering trap below.
#  3a. On "local dispatch": settle the acceptance criteria (detector below).
clawgatectl task comment <id> --body "$(cat <<'EOF'
**Starting** — host <host>, session <id>.
Acceptance criteria (AUTHOR-SPECIFIED | DERIVED — not author-specified):
1. … 2. …
Plan: <2–3 lines>.
Not doing: <explicit non-goals>.
Assumptions: <the ones that would change the work if wrong>.
<if DERIVED> These criteria are mine, not yours — object now if they are wrong.
EOF
)"                                                # 3b. PRE-START comment, BEFORE the flip
clawgatectl task status <id> in_progress          # 3c. THEN flip, and work
#     …4. implement per repo defaults: tests watched to FAIL at base, worktree, PR…
clawgatectl task comment <id> --body "…"          # 5. ONE completion comment (shape below)
clawgatectl task status <id> ready_for_review     # 6. …or `complete` — see the gate
```

**Acceptance-criteria detector — deterministic, not a judgement call.** A heading matching
`## Acceptance criteria` (case-insensitive) → **AUTHOR-SPECIFIED**. Anything else — including a body
that merely *reads* like criteria — means you **DERIVE** them and must label them
`DERIVED — not author-specified` in the comment.

🔴 **The verdict is frozen at your FIRST read (step 1).** Body edits are legal before the
`in_progress` flip, so otherwise an agent could PATCH the heading in, re-read it as AUTHOR-SPECIFIED
and self-complete — the exact self-grading the gate exists to stop. **Writing the criteria and
grading them is one act, in either order**: touch or reword the author's and they become yours,
DERIVED. **Quote them VERBATIM in the pre-start comment** (author-specified ones too) — that
timestamped copy is what makes this auditable rather than honour-system.

**The completion comment (5)** carries evidence **per criterion** — one line each, naming what proves
it — plus an explicit **NOT verified** list. "All green" with no per-criterion mapping is not a
completion report.

🔴 **Step 6 is the STATUS GATE, and it lives in `SKILL.md` → "Status gate"** — the
AUTHOR-SPECIFIED/DERIVED × validated table that decides `complete` vs `ready_for_review`. Read it
there; it is deliberately not duplicated here.

🔴 **That gate is LOCAL-pickup only, structurally.** The in-devpod agent route
`PATCH /agent/task/status` **forbids `complete`** (`notes.StatusAllowedForAgent`), so a dispatched
devpod agent ends at `ready_for_review` regardless of what this skill says. Only the machine route
this ritual uses can set `complete` at all — the gate governs that permission, nothing else.

📌 **For the task AUTHOR (Zach): a `## Acceptance criteria` section in the body is what unlocks agent
self-completion.** Without one, every pickup comes back `ready_for_review` for a human read. That is
the one lever you have; `flows/task-authoring.md` is the interview that produces it.

- 🔴 **Ordering trap — flip to `in_progress` LAST, after any edit to the task ITSELF.** The
  `in_progress` 409 is refined but real: once in progress, a `PATCH /api/tasks/{id}` carrying any
  non-tag field (or any routing tag) is refused. Descriptive-tag-only edits still succeed.
  **Comments are exempt** — different route, no in-progress guard. So derived criteria go in the
  **comment, never PATCHed into the body**: it dodges the 409, leaves Zach's task text untouched, and
  makes provenance unambiguous (body = the author's words, comments = the agent's).
- **Exactly TWO comments per pickup — start and finish, never per turn.** Per-turn self-reporting was
  measured as noise and removed once already (memory `clawgate-loop-validation`); do not reintroduce
  it in a new costume.
- **Comments author as `claude-code`** via `X-Clawgate-Source`; no `--author` flag — the header IS
  the impersonation guard, and `user`/`operator` are unreachable by design. An unknown `--source`
  silently downgrades to `api`, so the CLI warns on stderr when the author it gets back differs.
- ⚠ **A comment/status write also refreshes the task's idle clock** — the 7d idle reaper, which is
  non-destructive since 0.7.96 (one copy of that fact: `SKILL.md` → "machine (hook-token) Task API").

---

## What this flow is NOT

- Not the **authoring** interview — that is `flows/task-authoring.md`, enforced by a
  different hook (`clawgate-task-interview-guard.py`).
- Not the **status gate** itself — that stays in `SKILL.md`, so a reader who never
  opens this file still meets it.
- Not a description of what the guard hook can and cannot see. Its blind spots,
  escalation ladder and the `--dismiss` escape are in the hook's own module
  docstring (`devrc/scripts/claude-hooks/clawgate-writeback-guard.py`).
