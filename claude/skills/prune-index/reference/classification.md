# prune-index — the lifecycle verdicts, how a target is RESOLVED, and the traps

Loaded on demand. The core (`~/.claude/skills/prune-index/SKILL.md`) states the
four lifecycle rules; this file is the detail behind them and the false findings
they generate if you skip it.

## The six bullet populations, and which are eviction-eligible

`subsystem_resolver.JournalBullet.openness_population` is the SINGLE source of
this classification — every surface branches on it, so do not re-derive it by
reading the prose. Precedence, most-certain first:

| population | what it means | eviction-eligible? |
|---|---|---|
| `open` | the writer typed `OPEN:` | 🔒 **never**, at any age or size |
| `unverifiable` | `RESOLVED:` naming **no sha** — closed, unprovable | never: it is the strongest `NO HOME` there is |
| `resolved` | `RESOLVED <sha>:` | **only** once a target it names is verified to exist |
| `near-miss` | no marker parsed, but the line looks like an attempt | never — **fix the marker**, do not cut the bullet |
| `unmarked` | no marker; the prose matches a narrow floor | never — same, promote it to `OPEN:` |
| `none` | everything else — the overwhelming majority | not part of this lifecycle at all |

🔴 **`near-miss` and `unmarked` are the two that get mis-cut.** They read as
ordinary prose bullets and they are not: a `near-miss` is a writer whose
`RESOLVED <sha> (<repo>):` silently did nothing, and an `unmarked` is prose that
proposes a remedy nobody marked. Both are OPEN work wearing the wrong clothes.
The audit prints them; treat them as `KEEP_OPEN` plus a marker fix.

🔴 **The marker grammar is EXACT, and two near-misses are common enough to name:**
a date **not followed by `:`** (`- 2026-08-15 OPEN:` — the prefix does not parse)
and a marker on a **continuation line** (unreachable: the pattern is anchored at
the head of the bullet). The audit reports the second as an unreachable marker.

## What counts as a HOME

A `RESOLVED` bullet's content has a home when the bullet **names a target that is
verified to exist**. Three kinds, checked three ways:

- **a commit sha** — the `RESOLVED <sha>:` marker's own sha, plus any backticked
  hex token in the bullet. Checked with `git cat-file -e <sha>^{commit}`.
- **a path** — `claudedocs/…`, `scripts/…`, an absolute or `~`-relative path.
  Checked with a plain `exists()`.
- **a PR/issue ref** — `#123` or `owner/repo#123`. 🔴 **NOT checked by default**,
  because verifying one needs the network and a deterministic offline run is what
  makes the rest of the audit trustworthy. It comes back `NOT CHECKED`; pass
  `--check-prs` to resolve them with `gh`.

**Any ONE verified target is enough.** A bullet whose sha is dead but which names
a `claudedocs/` doc that exists is evictable — the record has a home, which is
the whole question.

## 🔴 The three traps that produce a WRONG verdict

**1. The owning repo is not the only home.** The store spans several repos, and a
`RESOLVED <sha>` legitimately names a commit in a sibling one — 4 of 29 live
`RESOLVED` bullets resolved *only* outside their own scope's repo (measured
2026-08-21). The audit therefore searches every derivable scope repo and prints
`found in <repo>, not <scope>` for those. **That is a weaker claim**: confirm the
attribution before evicting, because a 7–9 character short sha can collide
across repos.

**2. A stale local clone manufactures `NO HOME`.** `git cat-file -e` answers
about the clone on this disk, not about the remote. A sha pushed from the other
host and never fetched here is absent locally and present in reality. 🔴 **`git
fetch` the scope's repo and re-run before acting on any `NO HOME` whose target is
a sha.** The audit says so in its own output; believe it.

**3. `NOT CHECKED` is not `NO HOME`.** A scope with no derivable owning repo, or
a bullet naming only a PR ref, yields a target that could not be tested. An
unmeasured target is not an absent one, and reporting it as absent sends someone
to re-write a record that already exists. The verdict is a **tri-state** for
exactly this reason.

## Ref collisions

Two entries in one scope can both make the same ref addressable. The resolver
then **refuses to pick** — `--ref <it>` returns `ref-ambiguous` and surfaces **no
body at all**, so the entry becomes unreachable by the name a human would type,
and the failure looks exactly like "nothing is recorded here".

Two tiers, and only one kind of collision is real:

- **filename tier** — a bare ref matches `<slug>.md` *and* every
  `<slug>.<kind>.md`. So a scope holding both `repo-cos.md` and
  `repo-cos.process.md` makes the bare ref `repo-cos` ambiguous. Fix by using the
  qualified ref, not by renaming.
- **alias tier** — consulted **only when the filename tier returned zero hits**.
  Two entries declaring the same alias collide here.
- 🔴 **An alias that merely equals another entry's FILENAME is NOT a collision.**
  The filename tier wins outright and the alias tier is never reached. A
  hand-rolled "count the claims" detector reports this as ambiguous and is wrong;
  the audit avoids it by feeding every candidate ref back through
  `resolve_ref_tiered` — the executable authority — rather than re-implementing
  the rule.

**Live case, fixed 2026-08-21:** `alert-diagnose-svc.md` and
`civitai-advertising.md` both declared the alias `ads` in one scope, so `--ref
ads` surfaced nothing. It was dropped from `alert-diagnose-svc` — there it is an
*initialism*, whereas for the advertising service it is the actual word. 🔴 Prove
such a fix by resolving the ref afterwards: clearing the ambiguity by making the
ref resolve to **nothing** satisfies "no collision" and is a regression.

## The bullet-length advisory

The schema says "≤2 lines each". The corpus disagrees by a wide margin — 428 of
518 live bullets exceed it (measured 2026-08-21), and `JournalBullet`'s own
docstring records the same finding from an independent measurement ("a real
bullet is WRAPPED PROSE… the median bullet is 3 lines"). The audit reports it
with its denominator and **keeps it out of the verdict**: a gate that is red on
83% of a curated corpus is a permanently-red gate, which trains everyone to click
through. Use the worst few as trim targets and leave the rest alone.
