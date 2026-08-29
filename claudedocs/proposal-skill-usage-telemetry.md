# Making skill usage measurable

**Status:** part shipped (this PR), part deferred with a stated precondition.
**Origin:** an opencode session asked "identify signal skill usage", spent ~34 minutes
across 8 turns, and reached a **reversed** conclusion — "the skill has never been invoked
operationally", stated twice in bold — before being corrected by the operator. The answer
was one bounded query away the whole time.

---

## 1. The finding that reframes everything

```sql
source='claude' AND kind='command' AND startsWith(text,'/signal')   -->   laptop 1
```

Slash-command telemetry has flowed into `activity.events` since long before that session
(960 rows / 30d across both hosts). Nothing in the skill layer routes anyone to it.

But it is only **half** the signal, and that is the real story. Measured 2026-08-29 over
the workbench transcript corpus, counting SESSIONS (subagent transcripts excluded):

| skill | attributed | typed `/name` | attributed-only | typed-only |
|---|---|---|---|---|
| `browser` | 50 | 0 | **50** | 0 |
| `clawgate` | 72 | 28 | 44 | 0 |
| `activity` | 8 | 3 | 5 | 0 |
| `handoff` | 383 | 346 | 40 | **3** |

⚠ An earlier revision of this doc cited `clawgate` **4 typed-only** as the evidence that
both directions have live instances. That number was **wrong**: it came from a raw file
grep, which matches `<command-name>` inside quoted **tool output** — the exact false
positive the shipped code narrows against, and one of the four "typed" sessions was the
session writing this PR, grepping transcripts. clawgate's typed-only is **0**. The live
typed-only instance is `handoff` (3). Do not re-derive either number with a grep.

Most skill usage never involves typing the command — the skill **auto-fires from its
description**. The only field that sees that is Claude Code's own `attributionSkill`,
which sits on **120,366 records** under `~/.claude/projects` and had **zero** references
anywhere in `scripts/` or `claude/`.

Note both directions have live instances (`clawgate` typed-only = 4). Neither signal is a
superset of the other, so this is two fields, not one.

---

## 2. The five gaps

**G1 — skill usage is unmeasured on the runtime where most work happens.**
`adoption-scan`'s `REGISTRY` tracks 9 tools that emit through `invocation.py`; skills emit
nothing, and its own SKILL.md documents that blindness. Meanwhile **opencode skill usage
is already in ClickHouse** — 118 `skill` tool-calls across 97 sessions / 30d on both hosts
— because there a skill invocation *is* a tool call. So the fleet was half-instrumented
and nothing said which half. Reaching for `adoption-scan` first was correct routing into a
structurally blind instrument.

**G2 — `find-session`'s cross-host claim is true of one corpus and false of the other.**
`scripts/lib/opencode_search.py` has a real SSH peer loop. `scripts/lib/transcript_search.py`
is `Path.home()/".claude"/"projects"` with **no remote leg**. The skill description says
"searches both runtimes on both hosts". This is the direct cause of the reversed verdict:
the search ran on the workbench, the five real sessions were on the laptop, and the zero
read as a fleet fact. Worst class of gap — the doc asserts the coverage, so the reader
stops checking.

**G3 — no skill-attribution search surface.** The three surfaces are text / tool_use input
/ tool_result output. Skill invocation is in none of them, so the question degraded to
keyword grep: **666** "matches" for a question whose true answer was **1**, dominated by
`scripts/signal/tests/…` path noise.

**G4 — three skills claim adjacent territory and none owns the question.**
`adoption-scan` ("is it USED"), `find-session` ("recover a session"), `activity` ("query
the telemetry"). Each is a plausible entry; none says "skill usage lives here." This gap
degrades as the skill count grows.

**G5 — the credentials-and-query path is a copy-paste bash recipe.** Both operational
failures in that session came from an agent editing the recipe under pressure: the
ClickHouse reader password printed in cleartext (now persisted in `opencode-stable.db`),
and two pod OOMs from `ILIKE` full-scans of the keylog table. The prose instruction to
read `queries.md` first was in the loaded skill body and was ignored — evidence that prose
is the wrong instrument here, not that the warning needs strengthening.

---

## 3. What this PR ships

**P1 — skill invocation becomes telemetry.** `session-tailer.py`'s `build_rollup` now
records two independent maps on the Layer A rollup:

- `skills_used` — `{skill: attributed assistant-records}`, from `attributionSkill`. The
  only signal that sees an auto-fired skill.
- `commands_typed` — `{command: times typed}`, from `<command-name>`. Named for what it
  holds: it includes **built-ins** (`/login`, `/clear`), which are not skills, so a reader
  wanting skills must intersect with the skill list.

The collection points sit inside a loop that already visits exactly those records and
already builds `tool_counts` — no new daemon, no new deploy surface. It lands cross-host,
ClickHouse-backed, dedupe-able via `argMax`, versioned with the rest of the rollup.

Only the command **name** is kept, never its args: the args are operator free-text and
this payload ships to ClickHouse for every session on both hosts. Two tests pin that,
one of them against the emitted event rather than the dict, because `build_event`
json-dumps the whole rollup.

**P3 — `find-session.py --skill NAME`.** Exact-match on the attributed identity, ORing the
two invocation routes, ANDing with any search terms. Usable with no terms at all.

Verified against the live corpus: `--skill signal` → **1** session, where the keyword form
returns 666. Counts cross-checked against an independent grep baseline at two points
(`activity` 8 = 8, `browser` 50 = 50).

Coverage limits, all deliberate, all documented in the CLI help and the SKILL.md:
- It counts **sessions**, so a skill used only inside a dispatched subagent is not counted
  (`activity` had 15 such transcripts against 8 sessions). This matches the corpus
  boundary `find-session` already had, and the one `session-tailer` already had.
- The **opencode corpus has no per-record attribution**, so that leg is skipped under
  `--skill` and the omission is **printed to stderr** — including on the `--json` path,
  which is the one consumer that cannot infer scope from prose. Running it unfiltered
  would quietly answer a different question; skipping it silently would hand back a
  partial count that reads as the whole fleet. Both are the failure this flag exists to end.
- A typed **built-in** matches (`--skill login` finds sessions that typed `/login`): the
  typed route cannot distinguish a skill from a built-in, and dropping it would lose the
  typed-only sessions no other route sees.

### Three routes, not two

A first revision read only attribution and typed commands. Claude Code also records an
explicit **`Skill` tool_use** carrying `input.skill` — 1,305 blocks in the corpus — and
missing it undercounted real usage by up to **87.5%** on one skill (`next-lever`: 1
returned, 8 actual; `check-app` 14 vs 19; `resume` 100 vs 103). All three routes are now
read, and `input.args` beside `input.skill` is **never** kept: it is operator free-text and
live examples carry account identifiers.

---

## 4. What is deliberately NOT in this PR

**The `adoption-scan` `via: "skill"` registry arm.** It was in the original plan and it is
being held back on the repo's own rule. `adoption-scan` raises a loud `⚠ DEAD` flag at
zero uses, and its SKILL.md warns explicitly against adding a registry row with no
emitter behind it, because "a zero here is indistinguishable from wired-to-nothing".

`skills_used` is **forward-only**: no row carries it until a `home-manager switch` lands
the new tailer and the 5-minute timer runs. Shipping the reader arm in the same PR would
make `adoption-scan` confidently report **every skill as DEAD** until data accumulated.

**Precondition for the follow-up:** rows exist. Check with

```sql
SELECT count() FROM activity.events
WHERE source='claude' AND kind='session-summary'
  AND JSONLength(payload, 'skills_used') > 0
```

and only then add the registry arm — with a positive control showing a non-zero count
before quoting any zero.

**The deadman for `attributionSkill` itself.** It is an **undocumented upstream field**. If
Claude Code renames or drops it, `skills_used` becomes `{}` for every session on both
hosts, `--skill X` returns 0 for every skill, and *nothing fails* — a permanent silent
zero, the exact class this work exists to remove. The right home is a
`scripts/validation/invariants.py` check that at least one recent session carries a
non-empty `skills_used`. It is **not** in this PR for the same reason as the reader arm:
until rows accumulate it would be a **permanently-red gate**, which trains everyone to
click through. Same precondition, same query — add both together.

Note the other two routes fail differently and are less exposed: `Skill` tool_use and
`<command-name>` are observable shapes in the transcript rather than a single named field,
so a rename breaks one signal, not all three. That is an argument for keeping three
fields, not for skipping the deadman.

**P2 (the remote leg for the Claude corpus)** and **P5 (the creds/query helper)** are not
here either. P2 is the higher-value of the two and is now partly disclosed rather than
fixed: `--skill` prints that its results are this host only. The false "both hosts" claim
in `find-session`'s description is **still there** — deliberately untouched, because
PR #989 is actively rewriting that file and a conflict on a doc line is pure cost. It
should be corrected in #989's wake, whichever way P2 goes.

**Also outstanding, from the same session:** a credential-hygiene item was handed to the
operator out-of-band. It is deliberately not described here — this repo is **PUBLIC**, and
`SECRETS.md`'s convention is to record **dead** credentials with their rotation, never a
live exposure and its blast radius.

---

## 5. One thing checked and closed

The 2,460 `source=opencode kind=tool-call text='unknown'` rows are **historical residue**
of the fixed name-capture bug: `max(ts)` = 2026-08-04, and the current sentinel
`__name_capture_failed__` has zero rows. Capture is healthy — do not re-open it.
