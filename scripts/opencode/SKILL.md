---
name: opencode
description: Dispatch a task to opencode — the separate headless CLI agent — instead of burning this session's context on it. Preflights the brief, then launches detached. Use for "dispatch opencode", "call opencode", "run this with opencode", "hand it to flash/mimo/pro", opencode token efficiency. NOT the Agent tool — "dispatch a subagent" means that, not this.
---

# opencode dispatch

Hand a bounded task to **opencode**, a separate headless agent CLI with its own
context window. The point is *your* context budget: seven weeks of opencode cost
**$11.42** total, median dispatch **$0.014** — money is not the variable.

## The whole invocation

````bash
opencode-dispatch run --dir <PROJECT-DIR> --title "<short title>" -m flash <<'BRIEF'
<the complete task specification>

```claims
[]
```
BRIEF
````

🔴 **The `claims` block is REQUIRED — a brief without one is refused (rc 6).**
See "Every brief carries its sources" below; `[]` is the honest declaration when
the brief genuinely asserts no premise the agent would act on.

That is it. Do **not** re-derive the CLI — `opencode --version`, `--help`,
`run --help` and `models` were re-run from scratch in **nine** separate sessions
and answered nothing that is not on this page.

- The brief comes in on **stdin** (or `--brief FILE`). It is written **into
  `--dir`** at `.opencode-dispatch/<ts>-<slug>.md`, which self-ignores in
  whatever repo that is.
- `--dir` is required and is opencode's project root. Every path the brief names
  must be under it.
- The run is **detached**. The call returns immediately and prints a log path.
  There is no foreground mode and no flag to ask for one.

### Models

| alias | model |
|---|---|
| `flash` | `openrouter/deepseek/deepseek-v4-flash` |
| `mimo` | `openrouter/xiaomi/mimo-v2.5` |
| `pro` | `openrouter/deepseek/deepseek-v4-pro` |

Omit `-m` to take the `opencode.jsonc` default (what 18 of 24 measured
dispatches did). A full `provider/vendor/model` id passes through; an unknown
bare word is a usage error, not a silent pass-through.

### Other flags, in order of how often they actually get used

`--dir` 18 · `--title` 7 · `--file` 7 (extra attachments; the brief is attached
automatically) · `-m` 6 · `--auto` 4 · `-c` 2 (continue the last session).

**`--file` attachments are containment-checked too** — each must resolve under
`--dir`, exactly like a path named in the brief. `preflight` accepts `--file`
as well, so you can check them without dispatching.

## Check a brief without dispatching

```bash
opencode-dispatch preflight --dir <PROJECT-DIR> --brief <FILE>   # or stdin
```

`--json` for a machine-readable report. Exit **3** (a path escape) or **6** (an
uncited claim) means the brief was refused — see "When preflight refuses".

## Every brief carries its sources

🔴 **A brief with no `claims` block is REFUSED — rc 6, nothing written, nothing
dispatched.** Declare each load-bearing claim: a fact the brief states that the
agent will *act on*, and that would change the work if it were wrong.

````markdown
```claims
[
  {"claim":   "the preview DB is a clone, not production",
   "source":  "https://pr-4260.example.com/api/health",
   "read_at": "2026-08-21",
   "basis":   "measurement"},
  {"claim":   "the avatar refresh is driven by the localStorage roster",
   "source":  "src/components/AccountSwitcher.tsx:42",
   "read_at": "2026-08-21",
   "basis":   "inference"}
]
```
````

All four fields are required on every entry. `basis` is a **closed enum** —
`measurement` (you ran it, read it, saw the output) or `inference` (you concluded
it). Nothing else; a misspelling is refused rather than read as a measurement.
Extra fields are kept and warned about, never rejected.

**`[]` is a valid, honest declaration** — "this brief asserts no load-bearing
claim". It is an assertion on the record rather than a silence, and the report
prints it as `NONE DECLARED`.

### 🔴 What this covers — ~0.9% of the briefs this machine produces

| surface | rate | covered? |
|---|---|---|
| opencode dispatch (this tool) | ~1.2/day | ✅ |
| Claude `Agent`-tool subagent brief | ~137/day | ❌ no chokepoint exists |
| clawgate `build_task_body()` | uncounted | ❌ |

Basis: 6 briefs on disk across 3 project dirs over Aug 21–25 (a **floor** — the
dirs are deleted with their projects) vs 1,921 `Agent` calls in the audit's
14-day window. Different windows; treat it as an order of magnitude.

🔴 **Every measured instance below came from the UNCOVERED surface** — the wrong
root cause reached three *subagent* briefs; the three subagents correcting a
stale brief were Agent-tool subagents. **Do not cite this guard as evidence that
premise-propagation is handled.** It is a bridgehead on a real dispatch path, and
the schema a wider guard would reuse.

### Why it refuses instead of reminding

A 14-day audit of 443 sessions found **wrong premises propagating into subagent
briefs in four of six audit slices** — the highest-cost error class found, and
one the adversarial audit ladder is structurally blind to. A session built an
entire storage layer on a belief lifted from a stale comment and a stale README;
**four audit rounds read past it**. A wrong root-cause diagnosis was pushed into
**three** subagent briefs before being retracted. A homelab session *inferred* an
auth constraint from a token prefix, reported it as fact, and it propagated into
a downstream session's opening brief — "my inference presented as fact".

A prose instruction is the mechanism that already failed there four times. So
this is a schema, checked in code, that refuses.

🔴 **What it cannot see, stated plainly:** it checks that *declared* claims carry
sources. It cannot know your prose asserts a fifth premise you never declared —
detecting a claim in free text needs a heuristic, a heuristic over-matches, and a
permanently-red gate is worse than no gate. What the mandatory block buys is
narrower and real: you cannot dispatch without having been asked the question.

## Reading the result

`status` and `watch` are **deliberately not built** — they would depend on
opencode's internal SQLite schema, which carries no stability guarantee. That
dependency is worth taking only once this skill is proven to get used. For now:

```bash
tail -n 60 <the log path printed on dispatch>
```

## Why this exists — five measured failure modes, three success-shaped

1. 🔴 **`external_directory: "ask"` → headless auto-reject → exit 0, no work.**
   The brief named a path outside `--dir`. `opencode run` **auto-rejects** an
   `ask` (only the interactive TUI prompts). Fingerprint in opencode's own
   store: 10 of 321 sessions with `model IS NULL` and 0 tokens. Happened twice,
   in both directions — a read, then a write.
   *Closed two ways:* the brief is written **inside** `--dir`, and preflight
   **refuses (rc 3)** if the brief's text names any absolute path outside it.
2. **`--file` is an array option** and swallowed the message as a second
   filename (`Error: File not found: Execute the task described in…`). The
   message positional now always comes first, pinned by a test on its argv index.
3. **`permission.bash` `ask` → auto-reject mid-run.** opencode needed
   `kubectl exec … psql`, was auto-rejected, and the dispatch was abandoned.
   Preflight **warns** (never blocks) and names the glob.
4. **20% of opencode sessions exceed the Bash tool's hard 600,000 ms ceiling**
   (p90 2508s, p95 9119s); one died at exactly 600s with `Exit code 143`. Hence
   detached-always.
5. 🔴 **A wrong premise, stated as fact, propagating into the brief.** Found in
   **four of six** slices of a 14-day audit over 443 sessions — the highest-cost
   error class, and the one the adversarial audit ladder is blind to. Preflight
   **refuses (rc 6)** a brief that declares no citations. See "Every brief
   carries its sources".

## When preflight refuses

**Read the exit code — the two refusals are fixed by different edits.**

| rc | meaning | fix |
|---|---|---|
| 3 | a path in the brief or a `--file` resolves outside `--dir` | move/widen/inline (below) |
| 6 | a load-bearing claim carries no citation | add or complete the `claims` block |

### rc 3 — a path escape

The offender is printed as written *and* resolved, tagged `[brief]` or
`[attachment]`. Fix the **brief or the `--dir`**, never the check —
three options, in order of preference:

1. move the file under `--dir`;
2. widen `--dir` to a directory containing both;
3. inline the content into the brief instead of pointing at it.

There is no override flag. The failure it prevents is a silent exit 0, which is
the one shape nobody notices.

Read the three verdict lines literally — they are different claims:

- `external paths : none — all N examined resolve under --dir` — checked, clean.
- `external paths : NOT EXAMINED — no resolvable path in the brief, and no
  attachments` — **not** an all-clear. Nothing was there to check.
- `UNMEASURED paths : N` — variable-built paths that cannot be decided
  statically. Neither blocked nor cleared; check them yourself.
- `claim citations : N claim(s) cited — X measurement, Y inference` — the brief
  declared them and every one is well-formed.
- `claim citations : NONE DECLARED — …` — the brief **asserts** it carries no
  load-bearing claim. That is a claim about the brief, not an all-clear about
  its prose.

The report then lists each **inference** individually with its source and date.
🔴 Those are the ones to **re-verify before acting on**, not to treat as facts —
an inference reported as established fact is the measured failure.

A `⚠ WARN` line is different — it is advisory and the dispatch proceeds. It goes
through a parser that deliberately over-matches, so blocking on it would build a
gate people learn to click through.

## What this is NOT

- **Not a Claude subagent.** "dispatch a subagent to implement X" is the **Agent
  tool**. Only the literal word *opencode* routes here.
- **Not the `browser` skill.** "dispatch a browser agent" is that one.
- **No auto-retry.** The measured failures are corrective, not transient:
  re-running a known-bad brief is not a fix. Fix the brief, dispatch again.

## Writing a brief that lands

- State the goal, the acceptance check, and where the code is — **relative to
  `--dir`, and never with a `..` segment**. `../other-repo/x` escapes just as
  surely as an absolute path (opencode runs with `cwd=--dir`), and preflight
  refuses it.
- Put commands in fenced ```bash blocks so preflight can see them.
- **Avoid `$VAR/path` and `${VAR}/path`.** A variable-built path cannot be
  resolved statically, so preflight reports it as `UNMEASURED` — neither
  blocked nor cleared — and you have to check it yourself. Write the literal
  relative path instead.
- The brief must not be empty. An empty one is refused (rc 2), because a dropped
  heredoc otherwise dispatches a run that does nothing and exits 0.
- Assume no follow-up questions are possible. opencode is unattended; every
  `ask` is an auto-reject, not a prompt.

## Files

`scripts/opencode/` — `opencode-dispatch` (the CLI), `lib/oc_permissions.py`
(the shared resolver `scripts/tests/test_opencode_config.py` also pins),
`lib/brief_scan.py` (both scanners), `opencode.jsonc` (the permission block
preflight reads). Config, agents and the guard plugin are documented in
`scripts/opencode/README.md`.
