---
name: opencode
description: Dispatch a task to opencode — the separate headless CLI agent — instead of burning this session's context on it. Preflights the brief first (a brief naming paths outside --dir makes `opencode run` auto-reject and exit 0 having done nothing), then launches detached. Use for "dispatch opencode", "call opencode", "run this with opencode", "hand it to flash/mimo/pro", opencode token efficiency. NOT the Agent tool — "dispatch a subagent" means that, not this.
---

# opencode dispatch

Hand a bounded task to **opencode**, a separate headless agent CLI with its own
context window. The point is *your* context budget: seven weeks of opencode cost
**$11.42** total, median dispatch **$0.014** — money is not the variable.

## The whole invocation

```bash
opencode-dispatch run --dir <PROJECT-DIR> --title "<short title>" -m flash <<'BRIEF'
<the complete task specification>
BRIEF
```

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

## Check a brief without dispatching

```bash
opencode-dispatch preflight --dir <PROJECT-DIR> --brief <FILE>   # or stdin
```

`--json` for a machine-readable report. Exit **3** means the brief was refused.

## Reading the result

`status` and `watch` are **deliberately not built** — they would depend on
opencode's internal SQLite schema, which carries no stability guarantee. That
dependency is worth taking only once this skill is proven to get used. For now:

```bash
tail -n 60 <the log path printed on dispatch>
```

## Why this exists — four measured failure modes, three success-shaped

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

## When preflight refuses

It refused because a path in the brief is not under `--dir`. Fix the **brief or
the `--dir`**, never the check — three options, in order of preference:

1. move the file under `--dir`;
2. widen `--dir` to a directory containing both;
3. inline the content into the brief instead of pointing at it.

There is no override flag. The failure it prevents is a silent exit 0, which is
the one shape nobody notices.

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
  `--dir`**.
- Put commands in fenced ```bash blocks so preflight can see them.
- Assume no follow-up questions are possible. opencode is unattended; every
  `ask` is an auto-reject, not a prompt.

## Files

`scripts/opencode/` — `opencode-dispatch` (the CLI), `lib/oc_permissions.py`
(the shared resolver `scripts/tests/test_opencode_config.py` also pins),
`lib/brief_scan.py` (both scanners), `opencode.jsonc` (the permission block
preflight reads). Config, agents and the guard plugin are documented in
`scripts/opencode/README.md`.
