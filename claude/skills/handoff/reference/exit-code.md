# The pipe that eats the exit code — measured evidence

Rationale for step 1's capture rule. The imperative is in `SKILL.md`; this is why it exists.

## The conflict the rule resolves

Step 1 says **"Act on the exit code — and on nothing else"**, then prints a table keyed on
0/3/4/5/6. That instruction is only safe once the code has survived the invocation — and the
terse shape every executor reaches for, piping to `head`/`tail` to keep the transcript short,
is exactly the one that destroys it. `$?` after a pipeline is the **last** command's status,
so `tail`'s 0 is reported as the tool's.

## Measured, twice in one session (Claude Code `1a941379`, 2026-08-25)

**First instance — `resolve`:**

```bash
bash ~/workspace/devrc/scripts/lib/clawgate_handoff.sh resolve 2>&1 | tail -12
echo "rc=$?"        # printed rc=0
```

The real status was **5** (`NOTHING RESOLVED`). Read through step 1's table, `rc=0` means
*"one WORKED task → record it in step 2's front matter"* — the opposite instruction, on a
session that had no task at all. The executor caught it only because it read the prose:
*"Note `rc=0` there is `tail`'s status, not the tool's — the pipe trap again; the message is
what I acted on."* Surviving the step required doing the opposite of what the step said.

**Second instance — `field`, and strictly worse:**

```bash
bash ~/workspace/devrc/scripts/lib/clawgate_handoff.sh field /tmp/wt/…/handoff-…md 2>&1 | head -3
echo "field-rc=$?"  # printed field-rc=0
```

Here the tool had **failed to read the file** and said so (`cannot read '…' — this says
NOTHING about any field`), while the pipe reported `field-rc=0` under a legend the same
command printed: *"(0=field present, 1=none, 2=unreadable field)"*. Two independent
wrongnesses — a failed read and a swallowed status — agreeing on a confident false
conclusion. The retry that fixed it is the form the rule now mandates:

```bash
out=$(bash …/clawgate_handoff.sh field "$DOC" 2>&1); rc=$?
echo "$out" | head -2; echo "field-rc=$rc"   # printed field-rc=1 — correct
```

## Why not `set -o pipefail`

It changes *which* non-zero you get, not *whether* you got the tool's. With
`cmd | head`, `head` exits 0 and `pipefail` still yields 0 unless `cmd` itself failed —
and when both fail you get the leftmost non-zero, which need not be the verdict you are
reading. Capture the command's own status; do not put the command in a pipeline at all.

## The generalisation, and where it recurs

**A wrapper's trailing command swallows the status** — the same class as `claude/RULES.md`
→ "Read the CONTENT, never an exit code — COUNT". It is not specific to this tool:

- Hit again **in the same session that documented it**: a background gate run invoked as
  `nix develop … --command bash ./scripts/run-tests.sh . | tail -14` was reported by the
  harness as **exit code 0** while the runner's own output said `RESULT: FAIL (exit=1)`.
  Reading the `RESULT:` line is what caught it.
- The durable fix is structural, not prose: a tool whose verdict must survive a pipe should
  print a machine-readable token **in its output** (`verdict=NOTHING_RESOLVED`), so content
  and exit code cannot disagree and neither can be lost. That is an open improvement to
  `clawgate_handoff.sh`, not something this rule can achieve on its own.
