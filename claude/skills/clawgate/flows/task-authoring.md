# flow: authoring a clawgate task — the alignment interview

**Run this BEFORE `clawgatectl task create`.** It is enforced:
`~/.claude/hooks/clawgate-task-interview-guard.py` (PreToolUse on Bash) DENIES a
create whose body carries no `## Acceptance criteria` heading, and denies one whose
body it cannot read at all. The block message names this file, because a `flows/`
file does not auto-fire the way a skill description does — the hook is the router.

**What it is for.** A task body is the ONLY thing a dispatched agent executes
against. `create` returns `{"id":N}` and nothing else; there is no second chance to
explain. And a body with no `## Acceptance criteria` heading forces every pickup to
end at `ready_for_review` — the agent derives the criteria and may not grade an exam
it wrote (`SKILL.md` → "Status gate"). So the heading is not decoration; it is the
one lever the author has, and this interview exists to produce it.

**Why an interview and not a form.** `scripts/task-spec-drafter/README.md` measured
the unattended version of this problem on a real 8-ticket batch: deep-context
verification scored **8/8** against a naive title-only pass at **~2/8**, and of the 8
inbound "tickets" only **1** was genuinely dispatch-ready — the rest dissolved on
verification (already done / stale / underspecified / deliberately-off). One naive
draft would have crashed a deliberately-suspended service. The drafter is the
UNATTENDED path (ClickUp → verified queue → clawgate). This flow is its INTERACTIVE
counterpart. **Do not duplicate the drafter** — if the input is a ticket rather than
a sentence Zach just said, that pipeline already owns it.

---

## Phase 0 — PRE-VERIFY, before asking anything

🔴 **Never ask what you could have looked up.** Every question spent on a fact you
could have measured is a question you no longer have for the thing only Zach knows.
Arrive at Phase 1 with findings, not a blank form.

| question | how you answer it |
|---|---|
| Is it already done? | `git log --oneline -20 -- <path>` in the repo; `gh pr list --search "<keyword>" --state all` |
| Does a task already exist? | `clawgatectl task ls --summary --status open` (filters SERVER-side), then `--status in_progress` |
| Is it deliberately off / suspended? | read the config or manifest — a suspended kustomization, a `enabled: false`, a commented-out timer. **This is the meili-cron lesson: a task to "fix" something that is off ON PURPOSE is worse than no task.** |
| Which repo / directory does it land in? | resolve it now — `--repo` / `--directory` are dispatch inputs, not prose |
| Is there a verifier already? | an existing test target, a gate script, a `drift-check` rc — cheaper than inventing one |

If Phase 0 answers "already done" / "already a task" / "deliberately off": **say so and
stop.** Reporting that is the successful outcome. Do not create the task, and do not
ask Zach to confirm a fact you just measured.

## Phase 1 — INTERVIEW, at most 2 rounds

Use the `AskUserQuestion` tool. **At most 2 rounds; at most 4 questions per round**
(4 is the tool's hard cap), 2–4 options each.

🔴 **The cap is the design, not a limitation.** An interrogation becomes a gate that
gets clicked through, and a clicked-through gate reports alignment it did not
produce. Two rounds of four is the budget; spend it on what Phase 0 could NOT
resolve.

Good uses of a question — roughly in order of value:

1. **What does DONE look like?** (this is the acceptance criteria; if you get
   nothing else, get this)
2. **Scope boundary** — the smallest version that is still worth doing vs the full
   one, offered as concrete options
3. **A decision only Zach can make** — a user-facing micro-decision, a naming
   choice, which of two reasonable behaviours he wants
4. **An assumption that would change the work if wrong** — surface it as a question
   rather than a bullet he has to catch

Bad uses, all of which Phase 0 should have removed: which repo, whether it is
already done, what the file is called, whether he wants tests.

## Phase 2 — RECOMMEND, do not just ask

🔴 **Arrive with an opinion.** A questionnaire that hands every decision back is a
cost, not a service. Before drafting, say out loud:

- **the scope cut** — "half of this is the migration; the other half is a rename we
  can skip. Ship the migration only?"
- **the simpler approach** — a deterministic/structural fix in place of a
  prompt/heuristic one (PRINCIPLES.md), a config change in place of code
- **"this is already solved by X"** — name X. If Phase 0 found a partial answer, the
  task is the delta, not the whole thing
- **explicit non-goals** — the things a dispatched agent would plausibly do and
  should not. These go in the body; they are the cheapest scope control there is
- **the blast radius** — reversible / costly / irreversible, and what the rollback is

## Phase 3 — DRAFT, and validate the tags

Write the body to the template below, then **validate every tag before posting**.

🔴 **One invalid tag or one unknown `runbook:` is a hard 400 that fails the WHOLE
create** (`~/.claude/skills/clawgate/reference/task-api.md` → "Tags are
hard-validated"). It is a load-bearing
wire contract, not a warning.

```bash
HOOK=$(grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2)
curl -sf http://192.168.50.250:30302/api/tags -H "Authorization: Bearer $HOOK" | jq -r '.[].tag'
```

Grammar, in one line: lowercased, ≤20 tags, ≤64 runes each, charset `[a-z0-9._/-]`,
at most one `:`, no empty half. Reserved namespaces are a CLOSED set — `runbook:`
(hard-validated), `initiative:` (soft), `gate:` (**blocks dispatch, 409**),
`auto:dispatch` (off).

### The body template

```markdown
<one line: the outcome, in Zach's words>

## Context
<why now, and what Phase 0 measured — including what it ruled OUT>

## Acceptance criteria
1. <observable, checkable, one per line>
2. …

## Non-goals
- <what a dispatched agent would plausibly do and must not>

## Assumptions
- <the ones that would change the work if wrong>

## Verifier
<how we know it is done, cheaply and automatically: the exact command, the test
target, the metric. "Read the diff" is not a verifier.>

## Blast radius
<reversible | costly | irreversible> — <what the rollback is>
```

🔴 **`## Acceptance criteria` is REQUIRED and its spelling is checked**: a level-2
ATX heading, exactly two hashes, case-insensitive, trailing text allowed. `###`,
`**Acceptance criteria**`, and a heading that only appears inside a ``` fence do NOT
count — none of them satisfies the detector a pickup applies, so passing them would
be a gate that reports safety it did not deliver.

**Write criteria that a machine can settle.** "The bar pill turns red above 3" is a
criterion. "The bar feels better" is not. Each one should map to something in the
Verifier section.

## Phase 4 — CONFIRM

**Render the full body and get approval before POSTing.** `create` returns only
`{"id":N}` — no echo, no preview — and a dispatched agent executes against exactly
those bytes. A body Zach has not read is a body nobody has read.

Show: the title, the full body, the tags, `--repo`/`--branch`/`--directory`/`--model`
if set. Ask once. Apply the edits. Do not re-ask.

## Phase 5 — CREATE

```bash
clawgatectl task create --title "<title>" --body-file /tmp/task-body.md \
  --repo <repo> --directory <dir>            # --tag/--branch/--model as settled
```

`--body` and `--body-file` are mutually exclusive; `--body-file -` reads stdin.
Returns `{"id":N}` on stdout. Report the id.

🔴 **Do not pipe a generated body in** (`gen.sh | … --body-file -`): the gate cannot
read stdin and BLOCKS, by design — a guard that fails open on an unreadable body is
walkable by changing the shape of the call rather than its content. Write the file
first, or pass `--body`, or use a heredoc (`--body "$(cat <<'EOF' … EOF)"` — that IS
readable).

---

## Escape hatches — both deliberate

- **A body that already carries `## Acceptance criteria` passes the gate silently.**
  Zach's one-liner still works; it just has to say what "done" means.
- **`CLAWGATE_NO_INTERVIEW=1 clawgatectl task create …`** skips the gate for one
  call. One spelling, on purpose (`true`/`yes`/`0` do nothing), so "when did we skip
  the interview" is a `grep`-able question.

## What this flow is NOT

- Not the **pickup** ritual — that is `SKILL.md` → "task pickup", enforced by a
  different hook (`clawgate-writeback-guard.py`).
- Not the **drafter** — `scripts/task-spec-drafter/` owns the unattended
  ticket→queue path and its safety-escalation gate. Do not reimplement it here.
- Not a gate on the unattended producers. repo-cos, the drafter, clickup-mirror and
  the browser extension POST from their own processes and never cross a PreToolUse
  hook; nothing here changes their behaviour.
