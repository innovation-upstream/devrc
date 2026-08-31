---
name: prune-index
description: "Audit and prune the /analyze-service index store so recall keeps surfacing the RIGHT entry — evicts RESOLVED bullets, keeps every OPEN one, finds refs that resolve to two entries. Use for: prune/shrink/audit the analyze-service index, the subsystem index store, an entry that got huge, `--ref X` returns ref-ambiguous, RESOLVED bullets piling up, ~/.claude/analyze-service-index. Shrinking a SKILL.md body is `prune-skill`; the per-session MEMORY.md index is `prune-memory`."
argument-hint: "[SCOPE | SCOPE/ENTRY.md] — optional; defaults to the whole store"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# prune-index — audit & prune the `/analyze-service` index store

The store is a **pointer/nuance sheet per service** under `~/.claude/analyze-service-index/<scope>/<slug>.md`. Its measured value is **recency-ordered selection** — an index-primed agent avoids a superseded fact a repo-only agent asserts confidently — and selection is exactly what degrades as entries grow. Nothing applies pressure to it, so it grows monotonically. This is the pressure.

**Reference topics** — deployed at `~/.claude/skills/prune-index/reference/`, source `~/workspace/devrc/claude/skills/prune-index/reference/`:

| Load when | File |
|---|---|
| Before classifying (§3) — the five verdicts, the target-resolution rules, the traps | `~/.claude/skills/prune-index/reference/classification.md` |
| Before writing ANYTHING (§4–§5) — store safety, the diff contract, landing, verify | `~/.claude/skills/prune-index/reference/writing-and-safety.md` |

## 🔴 Store safety — read this before the audit, not after
- The store is **curated, CLIENT-CONFIDENTIAL, irreplaceable, and has no off-machine backup.** It is not re-derivable by re-running recon.
- **Each `<scope>/` is its own git repo** (the root is not). **Run NO git command inside it** — no `stash`, no `reset --hard`, no `clean`, no `checkout --`, no remote, no push. Set work aside with `cp <file> /tmp/…`.
- **Never copy an entry's content into devrc, any public repo, a PR body, an issue or a commit message.** Aggregate integers about the corpus are fine; a line of prose is not. devrc `60e6d9d` exists because this data class had to be scrubbed out of a public repo retroactively.
- Each scope's own `README.md` states the policy governing it — read it before writing there. A scope with no README has no stated policy; the audit reports those.

## Budgets (the contract)
- **Per ENTRY**, not per store. The numbers and their derivation are OWNED by `scripts/tests/test_subsystem_audit_budget.py` — **read them there, never restate them**; that module also prints the eviction playbook on failure.
- The target is the store's **own demonstrated shape** (most curated entries already fit it); the hard cap is the **proven `SKILL.md` body budget**, past which one entry outweighs a whole skill and has stopped being a pointer sheet.
- 🔴 **There is no store-wide total to gate on, deliberately.** A total grows with the number of services you legitimately work on. The per-entry cap is what the audit reports against.
- 🔴 **A few entries are ACKNOWLEDGED over the hard cap** — listed by name, each with its reason, in `subsystem-audit.py::ACKNOWLEDGED_OVER_CAP`. Each is over the cap and *provably cannot be brought under it by this lifecycle*: evicting every `EVICTABLE` bullet still leaves it over, because what remains is `OPEN:` bullets and gotchas with no other written form. Without the list the verdict read `⚠ prune needed` forever with no action that could clear it, and a permanently-red gate trains everyone to stop reading it (`claude/RULES.md`).
  **It is an ENUMERATION, never a raised cap or a threshold** — an over-cap entry not named there is still a finding — and it is **pinned both ways by the audit itself**: an unlisted over-cap entry, a listed entry that is *no longer* over cap (`STALE ACKNOWLEDGEMENT`), and a listed entry that no longer exists are all findings. **Acknowledged never means invisible**: the count, the names and the reasons print on every run, clean verdict included. To add a line you must first show the admission test — the measured `EVICTABLE` byte count that still leaves it over. Behaviour is covered by `scripts/tests/test_subsystem_audit_acknowledged.py`.

## 🔴 The lifecycle — decided, not up for re-litigation
1. **`OPEN:` ALWAYS STAYS.** Never an eviction candidate, at any age or size; it never counts toward reclaiming bytes. It is the one thing here that cannot be re-derived.
2. **`RESOLVED <sha>` is evictable only once its content has a HOME** — a target it names (a `claudedocs/` path, a commit sha, a PR/issue ref) that is **verified to exist**.
3. 🔴 **`RESOLVED` with no reachable home ⇒ `NO HOME — write the record first`.** Its bullet is the only copy; cutting it deletes the finding. Write the record, re-run, *then* evict.
4. **Nothing is ever auto-evicted.** The tool reports; a human confirms, on a diff.

Full rules incl. cross-repo targets and the stale-clone trap: `~/.claude/skills/prune-index/reference/classification.md`.

## 1. Audit (deterministic, READ-ONLY — no edits, no git in the store)
```bash
python3 /home/zach/workspace/devrc/scripts/subsystem-audit.py                 # whole store
python3 /home/zach/workspace/devrc/scripts/subsystem-audit.py --scope devrc   # one scope
python3 /home/zach/workspace/devrc/scripts/subsystem-audit.py --all           # list every entry
```
Prints, each **with its denominator**: per-entry bytes vs budget; bullet shape vs the schema (advisory); the lifecycle split (OPEN kept / EVICTABLE / **NO HOME** / NOT CHECKED); pointer integrity; front-matter completeness; **ref collisions**; scopes with no README; and a verdict.

🔴 **`NOT CHECKED` is not a pass.** It means a scope had no derivable owning repo, or a PR ref needs the network (`--check-prs`). Read it as an unmeasured scope, never fold it into a clean count.

If the verdict says **"no prune needed (stop; do not churn the files)"** — stop. It is a claim about the classes above and nothing else.

## 2. Back up first (the cut rewrites curated, unbacked-up files)
🔴 **Chain with `&&` and count the files** — `cp …; echo ok` prints success even when the copy failed.
```bash
BK=/tmp/index-prune-$(date +%s); mkdir -p "$BK"
cp -a ~/.claude/analyze-service-index/. "$BK"/ && echo "backed up to $BK: $(find "$BK" -type f | wc -l) file(s)"
```

## 3. Classify every bullet in an over-budget entry
- **KEEP_OPEN** — any `OPEN:` bullet. 🔒 Off the table. Also anything the audit reports as a *near-miss* or *unmarked action*: those are open bullets whose marker did not parse, so fix the marker, never cut the bullet.
- **EVICT_RESOLVED** — a `RESOLVED` bullet the audit lists as EVICTABLE, **with the target it named**. Open that target and confirm it actually carries the finding before cutting.
- **DROP_REDUNDANT** — the bullet restates something a `## Pointers` target already holds. 🔴 A one-shot "I found it there" is not enough — read the destination.
- **MERGE_DUP** — one gotcha restated across several appended bullets → one statement, keeping the oldest date and the newest sha.
- **KEEP_HOT** — `## What it is`, the pointers themselves, and any gotcha whose only written form is this bullet.

🔴 **A `NO HOME` bullet is in NONE of these buckets.** It is blocked work: write the record, re-run the audit, then it becomes EVICT_RESOLVED. Surfacing that mechanically is the point of the classifier, not an edge case.

Bias toward EVICT/MERGE **only inside the RESOLVED population**. Everywhere else this store is a router *and* the sole archive of things nobody wrote down.

## 4. Propose — confirm-gated, diff first
Present a **unified diff** against the current file, one compact block, ask one yes/no. On confirm, **re-read the file first** (a concurrent session may have appended), re-apply to current bytes, then plain `Write`. On decline, discard. Full contract: `~/.claude/skills/prune-index/reference/writing-and-safety.md`.

⚠ **This used to read "same contract as `analyze-service`'s write-back", and that pointer is now false** — the append prompt was retired everywhere on 2026-08-31 and `write-back.md` no longer carries a protocol at all (the one append protocol is `~/.claude/skills/subsystem-index/SKILL.md`, which asks nothing and mandates `Edit`). 🔴 **A prune is NOT an append, so the retirement does not reach it**: the evidence that retired the prompt was "the answer was always `y`" on an APPEND, and a cut REMOVES bytes that are often their content's only copy. Blast radius earns the gate. Keep the y/N here, and keep `Write` here — a cut necessarily rewrites the whole file, so the append rule's `Edit` anchor does not apply; step 2's `cp -a` backup is what stands in for it.

🔴 **Never silent-mutate, never batch a whole scope behind one prompt, and write the file and run NO git command** — the store has an out-of-band autocommit of its own.

## 5. Fix a ref collision
An ambiguous ref surfaces **nothing at all** — `--ref <it>` returns `ref-ambiguous` and no body, so the entry is unreachable by the name a human would type. Drop the alias from whichever entry it does not actually name (usually the one where it is an *initialism* rather than the word itself), then prove the fix:
```bash
python3 /home/zach/workspace/devrc/scripts/lib/subsystem_recall.py --ref <ref> --scope <scope>
```
Must print `status=hit`, naming the entry you expect. 🔴 Clearing the collision by making the ref resolve to **nothing** is a regression, not a fix.

## 6. Verify (don't trust — measure)
```bash
python3 /home/zach/workspace/devrc/scripts/subsystem-audit.py --scope <scope>
```
**Structural**: entries under budget, no collisions, `NO HOME` count unchanged or lower, and — the one that matters — **the OPEN count is IDENTICAL to before**. A prune that lost an OPEN bullet destroyed the store's only irreplaceable content while every other number improved.

🔴 **A structural pass is not content survival.** Diff each rewritten entry against the §2 backup and read what left. Then drive the entry once for real: run `/analyze-service` against that service and check the brief still answers the question the cut bullets used to.

Detail — the survival check, the landing rules, and why the audit may never write: `~/.claude/skills/prune-index/reference/writing-and-safety.md`.

Pair: `prune-skill` (a bloated `SKILL.md` body) and `prune-memory` (the per-session `MEMORY.md` index). Both prune something loaded by the *harness*; this one prunes something loaded by *recall*.
