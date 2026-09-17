# `cairn recall` — the subsystem-index surface of step 4

Routed from the `/resume` core — `~/.claude/skills/resume/SKILL.md`, source
`~/workspace/devrc/claude/skills/resume/SKILL.md`. Sliced out verbatim and then
CORRECTED IN PLACE (see below); the step numbers below are that core's steps.

Load this to read what `cairn recall` printed: the index row badges, the featured-entry
pick, `--list` / `--ref` / `--search`, `sensitivity=`, `MALFORMED` and the exit codes.

## Provenance, and how corrections land here

This file began as a verbatim line-range slice of the pre-prune skill, so it inherited
that skill's rot exactly as faithfully as its content. Claims re-measured since are
corrected **IN PLACE**, beside the text they correct.

⚠ That is a deliberate reversal. This file shipped with a prepended corrections block
and a self-issued rule that corrections must never be edited into the slice, "so the
slice stays auditable". The benefit was one-time — it made one review diff easier to
read — and the cost was permanent: every future reader of an on-demand doc met a
known-false sentence with its correction forty lines earlier, in a file that is loaded
precisely when someone needs the answer NOW. `git log -p` already preserves the
original wording, which is the auditability the rule was buying. So: fix it where it
is, date the measurement, and say what was retracted.

   🔴 **`cairn`, NOT `subsystem_recall.py` directly — and this changed on 2026-09-02.**
   The Cairn cutover made a hosted pod the datastore and FROZE the per-host mirror at
   `~/.claude/analyze-service-index` (entry files `0444`, nothing refreshes it). The
   reader's `--store` default was never repointed, so **every `/resume` between the
   cutover and that date oriented on a frozen store while being told it was complete**:
   MEASURED on the workbench, the frozen mirror served **26** `devrc/` entries and the
   synced cache **29**, and the frozen one still printed
   "ALL 26 entries in `devrc/`, none omitted" with no staleness stamp anywhere.
   `cairn recall` syncs first, then runs the SAME reader against the cache, and says in
   its banner whether it reached the pod or served a stale cache — so the answer carries
   its own freshness instead of asserting completeness it cannot check.

   🔴 **The reader now REFUSES an undateable store rather than serving it.** `cairn`
   drives `subsystem_recall` — the same module, the same output; it lives in the pinned
   `cairn` package, not in `scripts/lib/` — and run
   bare that module defaults to the synced cache and **exits 4** with
   `REFUSING to read … Run \`cairn sync\` and re-run` when that cache carries no
   `.sync-stamp`. That is a working state, not a broken one: run `cairn sync`, or just
   use `cairn recall` above. An explicit `--store <path>` is never refused — that is you
   naming a directory. A stamped read prints the stamp's own fields in its header, one
   per line and UNPARSED (`stamp: synced=…`, `revision=…`, `entries=…`, `coverage=ALL`) —
   the reader neither interprets them nor computes an age from them.

   🔴 **`--repo` takes a PATH, not a repo name.** A bare name resolves against your
   **cwd**, so `--repo datapacket-talos` becomes `$PWD/datapacket-talos`; `cairn` catches
   the resolver error and **exits 2** (`EXIT_USAGE`), printing the remedy itself. Pass an
   absolute path or one of the pre-exported handles (`$DEVRC`, `$HOMELAB`, `$DATAPACKET`,
   `$CIVITAI`), or use **`--scope <name>`**, which names the store directory directly and
   skips git derivation entirely. *(Re-measured 2026-09-17. This paragraph carried a
   2026-08-28 incident narrative and an **exit 3** with a raw `git ... cannot change to`
   error; the tool was fixed since, and the narrative described a bug that no longer
   exists. The imperative is unchanged.)*

   This is the **read half** of the store `/analyze-service` and `/handoff` write to — the terse pointer sheet that *outlives the handoff doc you just read*. It had two writers and no reader, so nothing ever opened it at resume time.

   **What the bare command prints (the digest):** the caveat, then a one-line **INDEX of every entry in the scope** — ref, `N nuance` (its `## Nuance / work-history` bullet count, *not* entry size), `sensitivity=`, and — only when they fire — the badges `🔴 N OPEN`, `🔴 N NEAR-MISS`, `⚠ N UNVERIFIABLE`, `🔴 NO <heading>` — never truncated, then **exactly ONE entry in full** (`## What it is` + `## Pointers` + `## Nuance / work-history`), then a line saying the other bodies were listed but not printed. Measured 2026-08-13 on `datapacket-talos`, then the scope holding 25 of the store's 29 entries: **4,876 B / ~1,219 tokens**, against **31,485 B / ~7,871 tokens** for the old default — which *also* hid 13 of the 25 entries. So it is now both cheaper and complete; the earlier claim that it "costs a page, not a dump" was false for the only scope big enough to matter, and this is the corrected, measured version.

   ⚠ **Every store-size figure in this file is a reading from the date beside it, not a current count — re-measure rather than quoting any of them.** 🔴 Measured 2026-09-17 with `cairn ls-entries`: the store holds **262 entries across 25 scopes, the largest (`datapacket-talos`) holding 63** — so every `29` / `37` / `53` / `25` reading dated below is far under the current count. The ≤100-line page cap still holds (63 < 100); nothing else here does. *(An earlier version of this paragraph said only "the scope has grown since" and left four undated present-tense counts standing elsewhere in the file, which is what made them read as current.)* Two deltas measured 2026-08-21, when `## What it is` was added to the printed body: the digest grew **+266 B** on `datapacket-talos` (37 entries), **+244 B** on `civitai`, **+394 B** on `devrc` — one body's worth, not one per entry — while `--list` grew by a flat **+18 B** on all three, which is the footer sentence and nothing else. **The per-entry index rows carry no `## What it is` at all**, deliberately: that surface is printed once per entry on every read.

   🔴 **`🔴 N OPEN` means N bullets in that entry DECLARE unfinished business — re-check each against live state before acting on the entry.** A remedy that has since landed reads exactly like one that has not: one entry proposed a one-line fix that shipped **two minutes later** and went on being served as outstanding for 22 days. **The absence of the badge means nothing was declared, NOT that nothing is open** — the marker is opt-in and predates almost none of the corpus.

   🔴 **Three further badges say the row's own numbers are not measurements. Read them before you read the counts beside them.**
   - `🔴 N NEAR-MISS` — N bullets **tried** to write a marker and missed the grammar (emphasis, a parenthetical before the colon, a missing date colon), so they declare nothing and `N OPEN` is **short by up to N**. Measured 2026-08-19 over the live store (53 entries, 323 nuance bullets): **8 bullets declare `OPEN:` and parse; 2 more attempted a marker and missed.** This is the population most likely to hold a stale open action — `--ref` the entry and read the bullets themselves.
   - `⚠ N UNVERIFIABLE` — N `RESOLVED:` bullets name no sha, so the closure cannot be checked with `git cat-file -e`. Advisory: closing is the point, and the sha is what makes the claim checkable rather than asserted.
   - `🔴 NO <heading>` — that heading is **absent or renamed**, so `N nuance` and every openness count on that row are **0 by parse failure, not by measurement**. It fires for `## Pointers` and `## Nuance / work-history` only — the two a count depends on. A missing `## What it is` is surfaced under that entry's own body instead, and never badges a row, because no number on the row is derived from it. Heading matching is exact-string at column 0, so a rename, a trailing colon or an indent all land here. The content is on disk and invisible to this read: open the file, or run `cairn-validate --scope <scope>`, which reports the populations the row cannot. **Do not read such a row as an empty entry** — a renamed heading is exactly how an entry with a declared `OPEN:` renders as `0 nuance` with no badge at all.

   **The featured entry is a PICK, and the output names the basis.** It is chosen by running the writer's own path→subsystem matcher over the paths quoted in this repo's newest `claudedocs/handoff-*.md` (`resolved via <doc> — N quoted path(s) name it`), and otherwise by the newest entry file (`most-recent fallback`). Read that phrase before you read the entry: on the real store the fallback fires more often than not (15 of the 40 most recent datapacket handoffs resolved to an entry), and **a fallback pick says nothing whatsoever about relevance.**

   **Drill down instead of dumping.** `--list` prints the index alone (2,580 B on that same scope, measured 2026-08-13) — use it when you only need to know what is on record. `--ref <name>` prints any single entry in full; that is the right follow-up to a line in the index, and an ambiguous ref is reported with its candidates, never picked. `--limit N` restores the old print-N-bodies behaviour with its loud truncation notice — reach for it only when you genuinely want the dump.

   **The index is capped at 100 lines per page, newest-first by entry-file mtime.** Below that cap (every scope as of 2026-09-17 — the largest holds 63) nothing changes and the header still says `none omitted`. Above it the header switches to `entries 1–100 of N` and a notice names the remainder and the flag: `--page 2`, `--page 3`, … reach the older ones, oldest last. The order is stated in the output on purpose — cutting an *alphabetical* index hides entries by an accident of their names, cutting a *recency* index hides the stale ones.

   **`--search <query>` reads by MATCH instead of by whole entry** — reach for it when you want one fact rather than an orientation, and as scopes grow past the point where a body is affordable. It prints HUNKS, each carrying its own `scope/ref`, section, `file:line`, `sensitivity=`, and its **score beside the threshold**, so a weak match is visibly weak. 🔴 **Matching is ONE-WAY** — a query term is matched by corpus words that EXTEND it (`postgres` → `postgresql`), never by ones it merely contains, so type the SHORTER form when unsure: `kube`, not `kubeconfig`. A no-match then means the term really is absent, and the printed near-miss + `--threshold` tells you which. Measured on `datapacket-talos`: `--search minio` 5,267 B / ~1,316 tok, `--search 'nginx ratelimit'` 1,711 B / ~427 tok, and a full-store scan took ~40 ms over the 29 entries the store held on that date (stdlib only — it shells out to nothing; the store is 262 entries as of 2026-09-17, so re-measure before quoting the timing).

   - Matching is fuzzy and **coverage-based**: each query term contributes its share, so a two-word query needs both words and `nginx kryptonite` returns nothing rather than `nginx`'s hits. Typos are tolerated (`conection` → `connection`); concatenations work (`ratelimit` finds `rate-limit`); tokens shorter than 4 characters must match exactly.
   - 🔴 **A no-match is not an empty screen.** It says how many entries were scanned, and either names the closest sub-threshold candidate with the exact `--threshold` that would surface it, or says plainly that nothing scored above zero — i.e. an *absent* term, not a weak one. Read which of the two you got before rephrasing.
   - `basis=entry-name` on a hunk means the ENTRY's name matched and none of its lines did — the hunk is a worked example, not the thing you searched for.
   - **Context is the enclosing bullet by default; `-C N` overrides with N raw lines** and that choice is yours. Entries are structured and their bullets wrap, so a raw window can cut one in half and emit a fragment that reads like a complete instruction. The bullet is the safe thing to quote; a raw window shows you what SURROUNDS the match (the heading above it, the next bullet), which is what you want when orienting rather than quoting.
   - `--all-scopes` searches the whole store, not just this repo's scope; `--max-hits N` raises the display cap (default 10, truncation always printed).

   **`sensitivity=client-confidential (declared: internal)` means the file wrote a marker the schema does not know and the fail-safe overrode it.** The schema knows exactly three — `client-confidential`, `personal`, `public` — and anything else, or nothing at all, folds to `client-confidential`. An absent marker is annotated with nothing (nobody claimed anything); an *overridden* one is shown so the override is never silent. Honour the effective value either way.

   🔴 **Everything it prints is `from index` — RECALL, NEVER LIVE OBSERVATION.** It was curated by *past* sessions, was not re-derived just now, and was not matched against anything in this session. Read the `caveat:` line and carry that label into your report: an index bullet is a **pointer to verify**, and it may describe a gotcha already fixed (pruning is manual). Never fold it into the live-state findings from step 2 — those were measured, these were remembered. Entries carry client-identifying detail: honour the printed `sensitivity=` and never copy a line into a public repo.

   **`scope-absent` / `scope-empty` means NOTHING RECORDED YET — that is the ordinary case, not an error and not a clean bill of health.** Coverage is uneven — 25 scopes / 262 entries as of 2026-09-17, with the three largest holding 138 of them — so plenty of repos will still print this. *(This read "the store is young (a couple of scopes …)", undated and present-tense, long after the store had outgrown it.)* Say plainly "the index has nothing for this repo yet" and move on; do **not** report it as an absence of drift, and do not go create an entry — that is `/handoff`'s job at the *end* of a session, not this step's. (It used to read "confirm-gated"; the y/N was retired 2026-08-15. The write still shows a diff first and is still declinable on content — what is gone is the prompt, not the discretion.)

   🔴 **A `🔴 MALFORMED` block means entry files exist that could NOT be indexed — the output is short, and it says so.** One bad entry used to abort the whole scope (measured: 2 good entries + 1 malformed served **0** and exited 3); it now serves the good ones and names each rejected file with its reason on its own `malformed index entry ...` row. Read those rows: the content in them is real, it is invisible to `--list`/`--ref`/`--search`, and the index header stops claiming `none omitted` for exactly that reason. Report it as a **store defect to fix**, never as an absence of content — and never assume a name is unrecorded just because `--ref` missed it while a reject is listed. The fix is `cairn-validate --validate <path>` (front matter is parsed line by line, so the usual cause is a value wrapped across two lines — an `aliases: [...]` list must be on ONE line). The launcher prepends `--validate` with no value, and argparse's last occurrence wins, so passing `--validate <path>` selects the single-file form.

   **`scope-unreadable` is NOT `scope-empty`.** It means the scope holds entry files and *not one* of them could be indexed, so nothing was read at all — the command exits non-zero for it, and it is the one "empty screen" you must not report as "nothing recorded yet".

   **Non-blocking, always.** It never prompts, and it never writes the *store*. ⚠ **But `cairn recall` is not read-only and not offline** — it fetches from the pod (20s timeout) and unpacks the refreshed cache, `.sync-stamp` included, *before* running the reader. Only the reader half is the read-only, clock-free, no-network thing; the sync in front of it is the part that makes the answer dateable. An outage is absorbed, not fatal: it serves the cache behind a `⚠ cairn: cached …` banner at exit **0**.

   **The READER exits non-zero only when NOTHING readable came back** (missing store, unreadable entry, or `scope-unreadable`/`search-unreadable` ⇒ **3**) — a scope that served some entries alongside a `MALFORMED` block exits **0**, because recall was available and was also honest about its gaps. ⚠ **That rule is the reader's, and `cairn` wraps it with codes of its own that do NOT follow it**: **2** (`EXIT_USAGE` — no scope could be derived, or `--repo` got a name instead of a path; pass `--scope`), **5** (the pod answered with something cairn refuses to install — which fires *even when a perfectly readable cache is sitting there*, because installing a corrupt snapshot over it is the worse outcome) and **11** (`EXIT_UNROUTED` — the scope resolves to no instance; cairn REFUSES rather than falling back to the first one). *(Verified 2026-09-17 against the pinned rev in `flake.lock` — `EXIT_UNROUTED = 11`, reached from `_report`, which is what `cairn recall` calls. This list read "**2** and **5**" until then and was presented as closed.)* So a non-zero from `cairn recall` does not by itself mean nothing was readable; read the banner. If it does exit non-zero, print the stderr line verbatim, note that recall was unavailable, and **continue the resume** — a broken index is not a reason to stop re-entering the work.

   🔴 **Do NOT read a `4` from `cairn` as "run `cairn sync`".** Two different tools spell 4 differently and the wrong reading sends you to the command that just failed. **`cairn recall` never returns 4** — it reaches the reader as a *library*, where the refusal below does not exist. `cairn`'s own 4 is `sync`-only (`EXIT_REFRESH_FAILED`) and means *the store was NOT reached, but a usable cache survived*; re-running `cairn sync` is exactly the thing that just did not work. **The 4 that `cairn sync` fixes belongs to the raw reader** — `python3 "$(python3 ~/workspace/devrc/scripts/lib/cairn_pin.py)/subsystem_recall.py"` run bare against a default store carrying no `.sync-stamp`, which refuses rather than serving a store that cannot date itself. (That module ships in the pinned `cairn` package; `cairn_pin.py` is the one thing that knows where.) Never fall back to recollection about what the index "probably says".
