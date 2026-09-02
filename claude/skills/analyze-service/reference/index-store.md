# The `/analyze-service` index store — location, resolution, schema, safety

Loaded on demand, not every run. `SKILL.md` calls `scripts/lib/service_recon.py`,
which performs the READ half of this document deterministically (through
`scripts/lib/subsystem_recall.py`, the store's one reader). Read this when you
need to **resolve a ref by hand**, **write an entry** (see `write-back.md`), or
**understand what the recon brief's `index:` line is telling you**.

## What it is

Alongside the live recon, `/analyze-service` keeps a **markdown pointer/nuance
sheet per service under `~/.claude`**, so each run front-loads "this bit us with
X" instead of re-discovering every gotcha. It holds **pointers + nuance only** —
never live state, never re-derived config values.

- **Location:** `~/.claude/analyze-service-index/<scope>/<slug>.md` — local, never inside a cluster repo or `devrc`. But **not "outside git": each `<scope>/` is its own remote-less git repo** (the store root is not one) — see 🔴 **Store safety** below before running any git command there.
- **`<scope>`** defaults to the basename of the owning repo root the service resolved into: `datapacket-talos`, `homelab-talos`, else the current working repo's basename — derived from the locate step, no separate assumption. 🔴 **On READ, recon asks every searched root's scope, not only that one** — see "The index is NOT gated on `locate`" below; a WRITE still lands in exactly one scope, so check which scope already answered before creating an entry. A scope **need not be a repo**: a ritual owned by no repo, or a client spanning several, may use a plain scope word — a deliberate choice, so say so in the brief.

## Resolution rules

<!-- resolver-rules:begin — hashed by scripts/tests/test_subsystem_resolver.py. Editing ANYTHING between these markers, including ADDING a bullet, fails that test on purpose: the code implementing these rules is scripts/lib/subsystem_resolver.py and the two must move together. -->
- **`<slug>`** is normalized: lowercase, `_` → `-`, any other char outside `[a-z0-9.-]` → `-`, collapsed, trimmed of leading/trailing `-` — applied identically on read and write **and to `aliases:` before comparing**, so `External DNS` / `externaldns` / `external-dns` land on one file, and so do `image_ingestion` / `image-ingestion`. The `_` fold matters: the index links `_`-spelled `MEMORY.md` slugs (`bastion_config_stale_until_reload_2026_07_08`). **Keep the pre-fold spelling in `aliases:`** — it stays a valid ref and records how the thing is really written.
- **Kind qualification — only when disambiguation is needed.** One slug can name two KINDS of thing (`devrc/repo-cos` is both a code subsystem and the weekly ritual about it): qualify with `<slug>.<kind>.md` (`repo-cos.process.md`), kind ∈ `service` | `process` | `org` | `doc`. A trailing dot-segment is a kind **only if it is in that enum**, else it's part of the slug. 🔴 **Bare `<slug>.md` stays the default: no existing file is renamed, and a scope with no qualified filename behaves exactly as before.**
- **Resolution — ambiguity is an ERROR, never a shadow.** Two tiers; an alias can never outrank a filename:
  1. **Filename tier** — normalized ref vs `<slug>.md` *and* every `<slug>.<kind>.md` in the scope. A ref naming its own kind (`repo-cos.process`) matches only that qualified file.
  2. **Alias tier** — normalized `aliases:` across the scope, consulted **only if tier 1 returned zero hits**.
  One hit → use it. **>1 in a tier → never pick: stop, call the ref ambiguous and list the candidates** (`repo-cos.md` vs `repo-cos.process.md`) for the user to choose. Zero in both → no index yet.
- 🔴 **The EXECUTABLE authority for the two rules above is `scripts/lib/subsystem_resolver.py`** (`normalize_ref`, `split_kind`, `resolve_ref_tiered`). The prose here exists because *you* are the other implementation — but two implementations of one predicate drift, and here the drift is silent: a ref stops resolving and the miss reads as "no index yet". `scripts/tests/test_subsystem_resolver.py::TestCommandDocIsPinned` holds the sentences above as literal substrings alongside the behaviour each asserts, so **rewording either side without the other goes red naming the sentence that moved.** Change both in one commit.
- **Lazy** — a scope dir or service file may not exist yet; it appears only on a confirmed write-back (see "## Write-back (opt-in)").
<!-- resolver-rules:end — deliberately AFTER the last bullet of this list, not before it: an editor appending a rule appends at the END, and a boundary that stops short of the append point leaves the likeliest drift outside the hash. -->

⚠ The last bullet above says "see `## Write-back (opt-in)`" — that section is now
`~/.claude/skills/analyze-service/reference/write-back.md`, in this same directory. The reference is **inside the
hashed region**, so correcting the wording would change a sha that is pinned to
the resolver's code; the redirect is stated here instead, deliberately.

⚠ **Same bullet, second stale word: "a *confirmed* write-back".** The confirm
prompt was retired everywhere on 2026-08-31 — a scope dir or entry file still
appears only on a WRITE, and the write still shows a diff first, but nothing asks
a y/N. The protocol is `~/.claude/skills/subsystem-index/SKILL.md`, one document
for every caller of this store; `write-back.md` decides *whether* an
`/analyze-service` run has anything worth recording and points there for *how*.
Stated out here for the same reason as the redirect above: the wording is inside
the hash.

🔴 **Store safety.** The content is **curated, client-confidential, and not re-derivable by re-running recon.** ⚠ **This paragraph used to say "with no off-machine backup". That has been false since 2026-08-21 and it was false in a RECOVERY path** — an agent reading it concludes a loss is unrecoverable and never looks for the restore tooling below. Two layers exist, and neither makes the store disposable:
- **hourly, local** — each `<scope>/` is its own git repo, committed by `analyze-service-index-commit.service` (`OnCalendar=hourly`, `RandomizedDelaySec=600`, so it fires within ten minutes AFTER the hour, not on it).
- **daily, off-machine** — `analyze-service-index-backup.service` bundles every scope, **age-encrypted**, to the homelab MinIO `minio-archive` tenant, bucket `analyze-service-index-backups`, key `<host>-<machine-id>/<scope>/<ts>.bundle.age`. Read them back with `scripts/analyze-service-index/restore-verify.py`; `escrow-verify.py` checks the key material.

🔴 **So price a destructive write honestly — and note the two prices are DIFFERENT.** An accidental overwrite or deletion costs back to the last hourly commit locally and the last daily bundle off-machine; **uncommitted working-tree state is in NEITHER**. A history rewrite costs strictly more, and the bullets below say which is which. Neither price makes the store disposable, and the rules stand unchanged either way: they rest on **confidentiality and curation**, never on the absence of a backup. Inside any scope dir:
- **Never `git stash`** — `refs/stash` is repo-**global** and concurrent sessions share this store, so your stash can be popped or dropped by another session. Set work aside with `cp <file> /tmp/…` instead.
- **Never `git reset --hard` (bare), `git clean`, or `git checkout --`** — each destroys **uncommitted** curated content, which is exactly the part no commit and no bundle holds. ⚠ "bare" qualifies `git reset --hard` ONLY, contrasted with the `<ref>` form below: `git checkout --` always takes a pathspec and `git clean` is almost always `-fd`, so reading "bare" across all three would license exactly the commands this bullet forbids.
- 🔴 **`git reset --hard <ref>` is WORSE than the bare form, not milder — it orphans COMMITTED content.** `backup.py` bundles with `git bundle create --all`, which walks **reachable refs only**, so once the branch has moved back, the orphaned commits are in no FUTURE bundle. They are still inside the bundles ALREADY in the bucket, which hold whatever was reachable when each was made — `ASIB_KEEP` daily runs, default 14 (`backup.py`) — so run `restore-verify.py` before calling anything lost. Past that window the reflog is the only holder. Do not read "it is committed, so a bundle has it" as safety against a history rewrite.
- **Never add a remote, never push**, and never copy a line into `devrc` (PUBLIC) or any public repo, issue, PR, gist or commit message. devrc `60e6d9d` exists because this data class had to be scrubbed out of a public repo retroactively.
- Each scope's own `README.md` states the policy governing it — **read it before writing there**.

## File schema

Markdown, so prose is surfaced verbatim via Read and reads well in a diff.

<!-- entry-schema:begin — hashed, same contract as resolver-rules above: `SubsystemEntry.from_mapping` + `load_index` implement the identity fields; `subsystem_touch.census` implements `created_by`. -->
- **Front-matter — identity, sensitivity + provenance only:** `service` (canonical name, matches the filename's slug part), `aliases` (alternate spellings, incl. pre-normalization ones), `scope` (owning repo basename or the non-repo scope word — **replaces `repo:`**, which older files still carry and reads as `scope`), `sensitivity` (below), `namespace` (**optional** — keep it for load-bearing k8s infra, `multiple` for umbrella services; **omit rather than write `n/a`**), `kind` (optional; only meaningful on a kind-qualified filename). No machine/location fields.
- **`created_by:` — which writer created the entry**, one of `analyze-service` | `handoff`. Stamped on a NEW file only, never edited afterwards, and **absent means the entry predates the stamp — never fold an absent value into either writer.** It exists to make one question answerable by counting instead of by recollection: *do entries accrue outside infra recon?* Read it with `scripts/lib/subsystem_touch.py --census`; the threshold that would reopen the store design lives in `claudedocs/decision-subsystem-store-rejected-2026-08-11.md`, not here.
- **`tasks:` — which tasks this entry answers**, a list of `<system>:<id>` refs (`tasks: [clickup:868abc123, github:owner/repo#428]`). `task:` is sugar for a one-element list; setting both is an error. **The id half is preserved VERBATIM and the split is on the FIRST colon**, so GitHub's lossless `owner/repo#N` survives whole — an encoding that cannot carry a `#` cannot carry a GitHub reference. 🔴 **No system is enumerated:** `linear:ENG-441` stores, validates and round-trips without the parser knowing the name; only URL resolution is system-specific, and it lives outside the parser so an unknown system is never an unstorable one. Optional and additive — an entry without it behaves exactly as before. Inline and block list forms both parse; a ref with no colon, an empty half, whitespace or a comma is rejected as MALFORMED naming the file. The lossy tag encoding a tag surface can hold is **derived** from a ref (`lossy_tag_for`) and is deliberately **not invertible** — two distinct refs can flatten to one tag.
<!-- entry-schema:end -->

- 🔴 **`sensitivity:` — fail-safe: absent means sensitive.** One of `client-confidential` | `personal` | `public`; **absent or unrecognized ⇒ `client-confidential`, never public**, and `public` is a deliberate operator claim a recon run may never infer. Live, not hypothetical — entries carry client-identifying infrastructure detail, down to named individuals. Handling rules are in 🔴 **Store safety** above; this spec only **marks**.
- **`## What it is`** — one-line description. For an umbrella/multi-instance service (redis, monitoring, meilisearch) enumerate the instances in prose — it's an index OF instances, not one location.
- **`## Pointers`** — each entry is a path/slug + one-clause why, **never a copy** of the pointed-to content:
  - `manage-* skill:` the matching skill (e.g. `manage-redis`) — invoke it for ops.
  - `MEMORY.md slug(s):` slug filename(s) in the project memory dir.
  - `claudedocs handoff(s):` handoff doc path(s).
- **`## Nuance / work-history`** — dated bullets, newest-first: a gotcha, a lying/misleading status condition, a revert or bump that explains why someone was looking, an incident tie-in.
  - **Openness is MARKED, never deleted.** `- YYYY-MM-DD: OPEN: …` or `- YYYY-MM-DD: RESOLVED <sha>: …` at the HEAD of the bullet — the grammar is exact (`subsystem_resolver._JOURNAL_OPENNESS`); a marker on a continuation line is unreachable, and a date not followed by `:` breaks the prefix.
  - **`OPEN:` always stays. `RESOLVED` is evictable only once a target it names is verified to EXIST, and is `NO HOME — write the record first` otherwise.** The full lifecycle is `write-back.md` → "Prune-on-resolve"; `scripts/subsystem-audit.py` reports it and the `prune-index` skill drives the confirm-gated cut. 🔴 The audit is READ-ONLY and runs no git command inside the store.
  - ⚠ **"≤2 lines each" was the original rule here and the corpus disagrees with it** — 428 of 518 live bullets exceed two lines (measured 2026-08-21), and `JournalBullet` documents the same finding independently. Treat it as a trim target, not a rule: the audit reports it as an advisory and deliberately keeps it out of the verdict, because a permanently-red gate trains everyone to click through.

## How the recon brief reports this

`service_recon.py` prints one `index:` line plus the three surfaced sections, in
schema order. Where each status comes from — three different answers, because
"recall said so" and "recall was never asked" are not the same claim:

- `scope-absent`, `ref-absent`, `ref-ambiguous` — **`recall`'s own status
  strings, passed through unchanged.**
- `hit`, `store-missing`, `store-unreadable` — `service_recon`'s names for what
  `recall` DID: `hit` renames `recall`'s `recalled`, and the other two name the
  two exceptions it can raise.
- `not-attempted`, `store-unstamped` — 🔴 **produced by `service_recon` BEFORE
  `recall` is asked at all** (`read_index` / `_scope_of` / `_resolve_store`), so
  neither is a statement about the store's contents.

| line | meaning |
|---|---|
| `index: <scope>/<ref> — HIT (from index) sensitivity=<s>` | resolved; `## What it is` + `## Pointers` + `## Nuance / work-history` follow |
| `index: ref-absent (scope <s>) … — checked N scope(s): …` | every scope named was read; nothing is recorded under that ref in any of them |
| `index: scope-absent` | the store holds no directory for this repo yet |
| `index: AMBIGUOUS in <scope> — a.md \| b.md` | 🔴 more than one candidate; **pick one, never guess** — no body is surfaced |
| `index: store-missing` | the store root does not exist on this host |
| `index: store-unreadable (scope <s>) — …` | the store was reached and `recall` RAISED — a broken/unparsable store, not an absent one |
| `index: not-attempted` | 🔴 **no root could be examined**, so no scope was derivable and the store was never asked. NOT a miss. Built by `service_recon` before `recall` is asked, same as the row below. |
| `index: store-unstamped — the index could not be read: …` | 🔴 **the DEFAULT read store carries no `.sync-stamp`, so nothing was read.** Not from `recall` — `service_recon._resolve_store` refuses before asking. Run `cairn sync`, or pass `--store <path>` to read a directory deliberately. |

That `not-attempted` row is the one to read carefully: it and `ref-absent` both
show "nothing from the index", and only one of them is a finding. `store-unstamped`
is a third such shape, and also not a finding about the service.

🔴 **A read store that CAN date itself prints its date, as `stamp:` lines
directly under the `index:` line** (`synced=`, `revision=`, `snapshot=`,
`entries=`, `coverage=` — the `.sync-stamp` file's lines, with blank lines
dropped and each line's TRAILING whitespace stripped, and nothing else done to
them: no parsing, no reordering, no age computed. That is the whole
normalisation `read_stamp` applies, and "verbatim" is the word the module
retired for being false about it). **The refusal above covers UNDATEABLE, never
STALE**: a
cache last synced three days ago serves a `HIT` that reads exactly like a fresh
one, so the stamp lines are the only thing in the brief that says how old it is.
No stamp lines means the store carried no stamp — never that it is fresh.

🔴 **`## What it is` is surfaced on the BODY paths only, never on an index row.**
Until 2026-08-21 no BRIEFING path printed it — `subsystem_recall` left it out of
`SURFACED_HEADINGS` as "durable boilerplate", so neither `--ref`, nor the digest,
nor `service_recon`'s `index:` block carried it, and an agent briefed on an entry
got pointers and nuance, both of which assume you already know what the thing IS.
**`--search` is the exception and always was**: it splits an entry on *any*
heading, wholly independent of `SURFACED_HEADINGS`, so it surfaced this section
then and still does — but it only ever reaches an entry a query matched, so it
briefs nobody. It is now rendered FIRST, in full, wherever a whole entry body is
printed: `--ref` (which is what this brief's `index:` block runs), `--search`,
the digest's one featured body, and each body `--mode full` prints.

The one-line index rows that `--list` and the digest print for **every** entry
carry nothing new — that surface is where the per-entry multiplier lives and it
stays byte-identical. An entry whose `## What it is` does not PARSE — absent,
empty, or not matched as a heading (renamed, indented, inside a code fence, among
others) — prints a named notice under its own body and gets **no**
`🔴 NO <heading>` badge: that badge means "a count on this row is not a
measurement", and this section feeds no count. `subsystem_touch --validate` does
not check it, for the same reason.

🔴 **EVERY searched root's scope is asked — not just the one that "won".** Root
ranking is a path-name heuristic, so it answers two different questions badly:
it can find NOTHING (and a curated pointer sheet is worth *most* exactly then,
since "where does this live?" is what the index can answer and the matcher just
failed), and it can find the WRONG thing (a lead of a few paths between two repos
is a naming convention, not ownership). Both are covered the same way: the roots
are asked in rank order, de-duplicated by SCOPE — a worktree and its base clone
are one scope — and the FIRST hit wins, so widening the search can never move an
answer the owner's scope already gave.

The line says which scope answered and how it was reached:

- no marker → the owning repo's scope, the ordinary case;
- `[scope via the cwd repo (N paths matched)]` → the repo you are standing in;
- `[scope via a searched root (N paths matched)]` → a root locate ranked lower;
- `[scope via searched root (nothing located)]` → locate matched nothing at all.

Anything but the first means that scope was **not** confirmed to own the service
— treat the entry as a pointer to verify, and check the `lives at:` line's own
`⚠ THIN MARGIN` note if it carries one.

A miss prints `— checked N scope(s): …`, so "nothing recorded anywhere" arrives
with its denominator. `not-attempted` still means only one thing: nothing was
examined at all.
