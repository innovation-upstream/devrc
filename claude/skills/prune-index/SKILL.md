---
name: prune-index
description: "Audit and prune the /analyze-service index store. Use for: prune/shrink/audit the analyze-service index, the subsystem index store, an entry that got huge, `--ref X` is ref-ambiguous, RESOLVED bullets piling up, the frozen ~/.claude/analyze-service-index. A SKILL.md body is `prune-skill`; MEMORY.md is `prune-memory`; the store itself is `cairn`."
argument-hint: "[SCOPE | SCOPE/ENTRY.md] — optional; defaults to the whole store"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# prune-index — audit & prune the `/analyze-service` index store

The store is a **pointer/nuance sheet per service**, held on the pod and read through **one read-through cache per configured instance** — `$DEVRC/scripts/cairn-ops/health.sh instances` names them. (`~/.claude/analyze-service-index/<scope>/<slug>.md` is the frozen pre-cutover mirror, not the store; cg#563 owns the code default that still points there.) Its measured value is **recency-ordered selection** — an index-primed agent avoids a superseded fact a repo-only agent asserts confidently — and selection is exactly what degrades as entries grow. Nothing applies pressure to it, so it grows monotonically. This is the pressure.

**Reference topics** — deployed at `~/.claude/skills/prune-index/reference/`, source `~/workspace/devrc/claude/skills/prune-index/reference/`:

| Load when | File |
|---|---|
| Before classifying (§3) — the five verdicts, the target-resolution rules, the traps | `~/.claude/skills/prune-index/reference/classification.md` |
| Before writing ANYTHING (§4–§5) — store safety, the diff contract, landing, verify | `~/.claude/skills/prune-index/reference/writing-and-safety.md` |

## 🔴 Store safety — read this before the audit, not after
- The store is **curated, CLIENT-CONFIDENTIAL, and not re-derivable by re-running recon.** ⚠ This bullet used to read "and has no off-machine backup" — false since 2026-08-21: `analyze-service-index-commit.service` commits each scope hourly and `analyze-service-index-backup.service` sends age-encrypted bundles to MinIO daily (`restore-verify.py` reads them back). **The rules below are unchanged, because they never rested on that** — a prune loses back to the last hourly commit, and **uncommitted state is in no commit and no bundle.** Detail: `~/.claude/skills/analyze-service/reference/index-store.md` → Store safety.
- **Each `<scope>/` is its own git repo** (the root is not). **Run NO git command inside it** — no `stash`, no `reset --hard`, no `clean`, no `checkout --`, no remote, no push. Set work aside with `cp <file> /tmp/…`.
- 🔴 **`~/.claude/analyze-service-index/` is a FROZEN mirror; the pod is the authority.** §4 writes through the store API instead. ⚠ **The `0444` is a marker, not an enforcement — measured 2026-09-02:** a shell `>>` gets `EACCES`, but `Edit` rewrites-and-renames through it (leaving the file still `0444`) and `Write` makes a fresh `0644` file. An editor write there SUCCEEDS and is then invisible to every reader, so the protection is the verb you choose, never the mode bits. The store itself, its client and `cairn doctor` are the **`cairn`** skill.
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
🔴 **Sync first, and audit the cache the READER resolves for THAT SCOPE.** Two distinct
reasons, and the second one is newer:

- `~/.claude/analyze-service-index/` is a frozen (`0444`) pre-cutover mirror that **no write
  updates** — every bullet appended through `cairn append` since the freeze is missing from
  it, so auditing it silently under-counts `OPEN:` bullets, which is the one number §6
  compares before and after. (cg#563 owns the code default that still points there; this
  skill's job is not to describe it as the store.)
- 🔴 **AND THERE IS NO SINGLE CACHE EITHER — one read-through cache PER CONFIGURED
  INSTANCE.** Measured on this host 2026-09-27: two caches plus the frozen mirror, three
  trees. A literal `S=~/.cache/subsystem-store` audits the DEFAULT instance and walks an
  empty directory for any scope that lives on another one, at exit 0. devrc PR #1872 carries
  the split's measurement; `hygiene.sh` refuses that zero with rc **22**.

```bash
$DEVRC/scripts/cairn-ops/health.sh sync
$DEVRC/scripts/cairn-ops/hygiene.sh audit --scope devrc         # one scope, its own instance
$DEVRC/scripts/cairn-ops/hygiene.sh audit --scope devrc --all   # list every entry
$DEVRC/scripts/cairn-ops/health.sh instances                    # <alias> <cache root>, one line each
```
⚠ **The whole-store sweep has no per-instance form and is still the bare tool** — it takes
one `--store`, so run it once per cache root `health.sh instances` printed:
`python3 $DEVRC/scripts/subsystem-audit.py --store <root>`. Naming that as a gap is the
point; a single invocation that looked whole-store and read one instance is the reading this
section exists to stop.
Prints, each **with its denominator**: per-entry bytes vs budget; bullet shape vs the schema (advisory); the lifecycle split (OPEN kept / EVICTABLE / **NO HOME** / NOT CHECKED); pointer integrity; front-matter completeness; **ref collisions**; scopes with no README; and a verdict.

🔴 **`NOT CHECKED` is not a pass.** It means a scope had no derivable owning repo, or a PR ref needs the network (`--check-prs`). Read it as an unmeasured scope, never fold it into a clean count.

If the verdict says **"no prune needed (stop; do not churn the files)"** — stop. It is a claim about the classes above and nothing else.

## 2. Back up first (the cut rewrites curated files the timers have not captured yet)
🔴 **Chain with `&&` and count the files** — `cp …; echo ok` prints success even when the copy failed. Back up the **synced cache**: that is what you are about to overwrite, and the frozen local mirror is a different, older set of bytes.
```bash
$DEVRC/scripts/cairn-ops/health.sh sync && BK=/tmp/index-prune-$(date +%s) && mkdir -p "$BK"
S=$($DEVRC/scripts/cairn-ops/health.sh instances --scope <scope> | cut -f2)
cp -a "$S"/. "$BK"/ && echo "backed up to $BK: $(find "$BK" -type f | wc -l) file(s)"
```
🔴 **BACK UP THE CACHE THE SCOPE ACTUALLY LIVES ON.** A literal default-cache path here backs
up bytes that were never at risk, and a reassuring copy is what makes the loss
unrecoverable. `hygiene.sh prune` takes this backup itself, of the resolved instance, before
it writes.

## 3. Classify every bullet in an over-budget entry
- **KEEP_OPEN** — any `OPEN:` bullet. 🔒 Off the table. Also anything the audit reports as a *near-miss* or *unmarked action*: those are open bullets whose marker did not parse, so fix the marker, never cut the bullet.
- **EVICT_RESOLVED** — a `RESOLVED` bullet the audit lists as EVICTABLE, **with the target it named**. Open that target and confirm it actually carries the finding before cutting.
- **DROP_REDUNDANT** — the bullet restates something a `## Pointers` target already holds. 🔴 A one-shot "I found it there" is not enough — read the destination.
- **MERGE_DUP** — one gotcha restated across several appended bullets → one statement, keeping the oldest date and the newest sha.
- **KEEP_HOT** — `## What it is`, the pointers themselves, and any gotcha whose only written form is this bullet.

🔴 **A `NO HOME` bullet is in NONE of these buckets.** It is blocked work: write the record, re-run the audit, then it becomes EVICT_RESOLVED. Surfacing that mechanically is the point of the classifier, not an edge case.

Bias toward EVICT/MERGE **only inside the RESOLVED population**. Everywhere else this store is a router *and* the sole archive of things nobody wrote down.

## 4. Propose — confirm-gated, diff first
Present a **unified diff** against the current file, one compact block, ask one yes/no. On confirm, land the cut **through the store API** — the local entry files are `0444`, but 🔴 **that does NOT stop an editor: `Edit` rewrites-and-renames straight through it and `Write` makes a fresh `0644` file** (only a shell `>>` gets `EACCES`). A cut applied locally therefore SUCCEEDS silently and is invisible to every reader, which is the loss this step exists to prevent:
```bash
$DEVRC/scripts/cairn-ops/health.sh sync                       # the live bytes
S=$($DEVRC/scripts/cairn-ops/health.sh instances --scope <scope> | cut -f2)
cp "$S"/<scope>/<entry>.md /tmp/prune-<entry>.md
#   apply the CONFIRMED cut to /tmp/prune-<entry>.md — the scratch copy, never the store
$DEVRC/scripts/cairn-ops/hygiene.sh prune --scope <scope> --ref <entry> \
  --file /tmp/prune-<entry>.md --confirm
```
🔴 **`--confirm` IS THE GATE, IN THE TOOL RATHER THAN IN THIS PROSE.** `hygiene.sh prune`
refuses without it (rc 2), takes its own `cp -a` backup of the resolved instance cache first,
and then goes through `write.sh put`, so the mandated post-write check runs on a prune exactly
as on any other write. The y/N above is still yours to ask — the flag records that you did.
🔴 **`cairn put` derives its `If-Match` from a LIVE sync, and that is what REPLACES "re-read the file first, re-apply to current bytes" — a replacement, not an omission.** A session that appended between your sync and your put makes the put fail with **exit 8** instead of silently deleting their bullet, which is exactly the loss the old re-read rule was guessing at. **Exit 8 IS that writer**: `cairn sync`, re-apply the cut to the NEW bytes, show the diff again, ask again, put again. Never retry the same file and never pass `--if-match` by hand — that is the clobber the precondition exists to stop. Exit 6 = refused (bad ref or scope); exit 7 = the store was unreachable and **nothing was written or queued**. On decline, discard the scratch file. Full contract: `~/.claude/skills/prune-index/reference/writing-and-safety.md`.

⚠ **This used to read "same contract as `analyze-service`'s write-back", and that pointer is now false** — the append prompt was retired everywhere on 2026-08-31 and `write-back.md` no longer carries a protocol at all (the one append protocol is `~/.claude/skills/subsystem-index/SKILL.md`). 🔴 **A prune is NOT an append, so the retirement does not reach it**: the evidence that retired the prompt was "the answer was always `y`" on an APPEND, and a cut REMOVES bytes that are often their content's only copy. Blast radius earns the gate. **Keep the y/N here.** Only the write MECHANISM moved: a cut necessarily rewrites the whole entry, so it is a `cairn put` rather than the append verb — and step 2's `cp -a` backup, not the API, is still what lets you read back what left.

🔴 **Never silent-mutate, never batch a whole scope behind one prompt, and run NO git command in the store** — it has an out-of-band autocommit of its own. ⚠ This used to say "write the file and run NO git command"; there is no file to write here any more, only a `cairn put`, and the git half is what the sentence was always for.

## 5. Fix a ref collision
An ambiguous ref surfaces **nothing at all** — `--ref <it>` returns `ref-ambiguous` and no body, so the entry is unreachable by the name a human would type. Drop the alias from whichever entry it does not actually name (usually the one where it is an *initialism* rather than the word itself). 🔴 **The `aliases:` line is inside a frozen entry file, so this is a `cairn put` too** — same scratch-copy route as §4, same exit-8 rule; it is a one-line edit, not an exemption. Then prove the fix:
```bash
REF=<the ambiguous ref>; SCOPE=<the scope>
$DEVRC/scripts/cairn-ops/health.sh sync
$DEVRC/scripts/cairn-ops/read.sh recall --scope "$SCOPE" --ref "$REF"
```
Must print `status=hit`, naming the entry you expect. 🔴 Clearing the collision by making the ref resolve to **nothing** is a regression, not a fix.

## 6. Verify (don't trust — measure)
```bash
$DEVRC/scripts/cairn-ops/health.sh sync
$DEVRC/scripts/cairn-ops/hygiene.sh audit --scope <scope>
```
🔴 **Sync again, or you re-measure the bytes you measured in §1** — the put landed on the pod, and a cache read without a refresh is a claim about your own pre-put copy, which is byte-identical whether or not the write succeeded.

**Structural**: entries under budget, no collisions, `NO HOME` count unchanged or lower, and — the one that matters — **the OPEN count is IDENTICAL to before**. A prune that lost an OPEN bullet destroyed the store's only irreplaceable content while every other number improved.

🔴 **A structural pass is not content survival.** Diff each rewritten entry against the §2 backup and read what left. Then drive the entry once for real: run `/analyze-service` against that service and check the brief still answers the question the cut bullets used to.

Detail — the survival check, the landing rules, and why the audit may never write: `~/.claude/skills/prune-index/reference/writing-and-safety.md`.

Pair: `prune-skill` (a bloated `SKILL.md` body) and `prune-memory` (the per-session `MEMORY.md` index). Both prune something loaded by the *harness*; this one prunes something loaded by *recall*.
