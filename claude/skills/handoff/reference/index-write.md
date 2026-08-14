# `/handoff` step 4 — the measured evidence behind the index rules

**Rationale only. Every rule you must follow is in `SKILL.md` step 4 and is complete there** — nothing in this file needs to be read before running the read-only probe, and nothing here relaxes a rule. Read it when a rule looks arbitrary, when you are tempted to work around one, or before proposing a change to one.

Source: `~/workspace/devrc/claude/skills/handoff/reference/index-write.md` (deployed at `~/.claude/skills/handoff/reference/index-write.md`).

🔴 **Long lines are deliberate.** Several sentences below are pinned verbatim by `scripts/tests/test_subsystem_recall.py`; hard-wrapping one puts a newline inside the pinned string and the guard goes red. Do not reflow this file.

---

## 1. Concurrency — why the append uses `Edit`, and the retraction that forced a rewrite

Two sessions appending to one store entry were simulated against a fixture (2026-08-12). What was measured:

| protocol | what the second session does | result for the first session's bullet |
|---|---|---|
| `Write` | retypes the whole file from bytes it read **before** the first session wrote | **silently gone** — no error, and nothing about it appears in the diff that was shown |
| `Edit` | rewrites only the matched region | **survives regardless** |

🔴 **The corrected mechanism, stated as MEASURED and not as reasoned: the protection is that `Edit` is BOUNDED, not that a stale anchor fails.** A whole-file retype of a curated, unbacked-up entry can lose content the confirm-diff never displayed; a bounded region rewrite cannot.

🔴 **It does NOT reliably fail loudly, and an earlier version of this rationale said it did.** Measured on the anchor the protocol actually names — the bare `## Nuance / work-history` line — the second `Edit` **succeeds silently** and both bullets land. Benign in outcome, but you are *not* told a collision happened. It errors only in two cases: when the anchor spans the insertion point (the header *plus* the first existing bullet), or when the first session's insert has made the anchor non-unique.

**Operational consequence, which is why the rule in `SKILL.md` is phrased the way it is:** re-reading the file and re-applying to current bytes is the actual safeguard, not the anchor. Do it every time. Do not treat "no error" as evidence you were alone. Do not restore the retracted wording.

### The autocommit timer cannot disturb an anchor

Measured: the store's hourly autocommit runs `add`/`commit` only and **rewrites no working-tree byte**. So it can never move or mangle an `Edit` anchor out from under a session, and it is not a reason to prefer `Write`.

## 2. A brand-new scope directory is unversioned for up to an hour — by design

Measured twice, on two new scopes (2026-08-12): the store's hourly timer `git init`s a brand-new scope directory, seeds a local identity, and commits it — with **no remote**.

That is the whole reason `SKILL.md` says not to create the repository yourself. Without the measurement, an agent that finds no `.git` beside a just-written entry cannot distinguish "waiting for the timer" from "silently not backed up" — which is the reading one session actually reached, at the cost of a round trip and a near-miss `git init` inside a client-confidential store.

## 3. What each path source is blind to, with the numbers

The three sources are blind in **opposite** directions, which is why the tool refuses to merge their path sets and why each run carries its own `caveat:` line.

| source | sees | measured blind spot |
|---|---|---|
| `--session <uuid>` | what *this* session's own transcript records as a file-tool edit — independent of git, so merged work still counts | a **subagent's** edits are a separate transcript: **196 of 733 file-tool calls** across the 40 most recent transcripts. Also: files written by a `Bash` command rather than a file tool, paths outside the session cwd (counted, never dropped), and a session whose cwd is a *different repo* from where its work landed (answered by the `session-absolute` window when it named absolute paths here, refused otherwise — see below). |
| `--pr <n>[,…]` | every byte on the branch, whichever agent, session or tool wrote it — the **only** source that can see a subagent's work | over-reports in exactly the direction the session source under-reports: it is the union of every commit on the branch, so it carries other sessions' commits and older work on a long-lived branch, and omits anything that never reached a PR. |
| `--commit <sha>[,…]` | exactly what those commits changed — the primitive the other two reduce to, so it reaches repos the others cannot | excludes uncommitted work and sibling commits on the same branch; over-reports when a commit carried more than the work being recorded (a formatting sweep, a stray `git add`). |

**Why the commit source exists at all**, measured on the repo that holds almost every entry in the store: its own rules mandate committing from a throwaway `/tmp/wt-*` worktree, so `--session` reported **25 paths outside the session cwd and 0 inside**; and **144 of its last 200 mainline commits** carry no `(#N)` suffix, so `--pr` cannot see 72% of what lands there. Both pre-existing windows were structurally blind in the one repo that mattered most.

### `--session` is blind by construction in FOUR ways, and the answer is always a different source

This is the shape worth seeing at a glance, because each case has arrived separately and each time the instinct was to fix the guard:

1. **Subagent turns** — a separate transcript. 196 of 733 file-tool calls (above). Answer: `--pr`.
2. **Files written by a `Bash` command** rather than a file tool — never recorded as an edit. Answer: `--pr`/`--commit`.
3. **A repo whose rules mandate committing from a throwaway worktree** — 25 paths outside the session cwd and 0 inside. Answer: `--commit` **or** `--pr`, and the worktree does not decide which — **whether the work landed as a PR does.** In the repo measured above, 144 of 200 mainline commits carry no `(#N)`, so `--commit` is right there; a worktree whose branch was opened as a PR is a `--pr` case, and reading "worktree" as "use `--commit`" cost a session its index write on 2026-08-14 (five base-clone shas, one `claudedocs/` path, reported as a real zero).
4. **A session whose cwd is one repo while its work landed in another** — *partially* answered since 2026-08-13 by the `session-absolute` window, and refused with `transcript cwd does not match` only when that window is empty. Answer when refused: `--pr`/`--commit`.

🔴 **All four are CORRECT behaviour, all four are already stated in the `caveat:` line, and in none of them is the fix a change to the session source** — with case 4 the documented exception, and the way it was wrong is worth keeping.

The refusal used to be unconditional, on the stated ground that *"every path in a transcript is relative to the session's own cwd"*. That sentence is **false**. A transcript entry is the tool call's own `file_path`, which is **absolute** whenever the caller passed an absolute path — and an absolute path that resolves under `--repo` needs no re-anchoring at all, whatever cwd the session ran in. Those entries were being discarded by a guard whose reason did not apply to them, and two agents in one day read the reason, believed it, and correctly stopped. **The guard's outcome was right and its stated reason was wrong** — the failure mode that survives a green suite indefinitely, because nothing tests a rationale.

What did **not** change, and is the actual safety property: a **relative** entry is never re-anchored. `src/a.py` in session-cwd A and `src/a.py` under repo B are unrelated strings that happen to spell the same thing, and reading one as the other files another repo's work here — silently, and plausibly enough that nobody would catch it, because the manufactured path resolves perfectly.

🔴 **Read the `caveat:` line: `session-absolute` is a FLOOR, not a list.** The relative paths are *excluded*, not counted, so how much of the session is missing is **unknown** rather than merely unlisted — which is a weaker claim than the ordinary session window's, where the remainder is at least a number. 🔴 **When it does refuse, the ordinary fallback is still wrong**: the git branch window is empty too when the session ran elsewhere, so "drop `--session` and use the git window" sends you to a second dead source. `SKILL.md` states that as a conditional rule; this is why.

**Why `--session` is worth passing even so:** the tool's first real invocation reported nothing, because a session that landed its work through merged PRs has an empty `git diff HEAD` and a HEAD sitting at the merge-base by the time this step runs. The git **branch** window is honest and tested, but blind to work that has already merged.

## 4. `gh pr view --json files` truncates at 100 entries, silently

Measured 2026-08-12 at three points either side of the cap:

| PR | `changedFiles` | `len(files)` |
|---|---|---|
| a 39-file PR | 39 | 39 |
| a 301-file PR | 301 | 100 |
| a 411-file PR | 411 | 100 |

The API says **nothing** about having truncated. The returned list is a *prefix*, so a late-sorting subtree can be missing entirely and every count below it would be wrong.

The tool therefore **refuses** rather than reporting the prefix — the opposite of what the session source does with its own extractor cap (a loud note, then carry on). The difference is what the caller can do about it: the extractor's cap is internal and its paths are still genuinely this session's, whereas here the caller has an exact, provenance-preserving alternative — `git diff --name-only <base>...<head> | subsystem_touch.py --paths-from -`, whose caveat states outright that provenance is the caller's to declare.

The guard is `len(files) < changedFiles`, deliberately **cap-agnostic**: pinning the literal 100 would go stale the day GitHub changes it, and the comparison needs no constant at all.

## 5. Why this step exists at all

`/analyze-service` was the store's only writer, so entries accumulated for infra services inside a single scope while the work being recorded spans ~12 repos. `/handoff` is the second writer, and the one that runs at the end of every session rather than only when someone asks for a recon.
