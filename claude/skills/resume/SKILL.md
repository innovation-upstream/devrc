---
name: resume
description: "Re-enter work from the latest handoff doc: read it, re-verify it against live state, and propose ranked next steps. Use when starting a session, returning after a few days, or told to pick up where we left off."
argument-hint: "[topic-slug] — optional; defaults to the most recently modified handoff doc"
allowed-tools: Bash, Read, Grep, Glob
---

# /resume — re-enter from a handoff

Goal: rebuild context fast and **verify it's still true** before acting (a handoff reflects what was true when written — live state may have moved).

Topic argument (optional): `$ARGUMENTS`.

## Steps

1. **Locate the handoff**: if a topic is given, read `claudedocs/handoff-<topic>.md`; otherwise find the most recently modified `claudedocs/handoff-*.md` in the active repo (`ls -t claudedocs/handoff-*.md | head`). **Not every repo uses that lowercase shape** — civitai-manager names its handoff `claudedocs/SESSION-HANDOFF.md` — so if the glob comes back empty, fall back to `ls -t claudedocs/*HANDOFF*.md | head` before concluding there is no handoff (`resume-state.sh` resolves it in exactly that order). If BOTH come back empty, say so and offer to reconstruct state from git/PRs instead — and say plainly that nothing was reconciled, rather than reporting the absence of drift as a clean bill of health.

2. **Read it fully.**

3. **Re-verify against live state — run the deterministic reconciler, don't hand-roll it:**
   ```bash
   bash ~/workspace/devrc/scripts/resume-state.sh "$ARGUMENTS"
   ```
   This is the initiative-scoped, on-demand collector (modeled on `standup.sh`). It resolves the handoff, then reconciles it against FRESH live state in one call and prints a compact digest: `GIT/PR` (branch ahead/behind, dirty, referenced PR states + CI, branch existence), `WORKLOAD` (handoff-named deployment readiness + canary phase — v1: datapacket), `ALERTS` (firing alerts scoped to the initiative's namespace), and a `DRIFT` block. **Interpret the digest — especially `DRIFT`** (the lines where live state contradicts the handoff, e.g. a PR the doc calls in-flight has already merged). Do NOT re-derive this by hand-rolling `git`/`kubectl`/`gh`. It degrades gracefully (git-only) when a source is unreachable or the repo isn't datapacket; only reach for a targeted `kubectl`/`gh` drill-down if the digest flags something needing one. 🔴 **An empty `DRIFT` is only good news if something was actually reconciled — check that FIRST, in two places.** (a) The `handoff:` line: `(none found — git-only)` means no doc was loaded and nothing was reconciled at all. (b) Any `DRIFT` line starting with `!` — those are *gaps*, sources that did not answer (e.g. `gh answered for 0 of 3 referenced PR(s)`), and they print alongside real findings too, so a list of `-` findings is complete only if no `!` line sits beside it. The digest states both conditions itself: `(no handoff loaded — nothing to reconcile…)` and `(nothing detected, but a source did not answer — NOT a clean bill of health)`. Only `(none detected — live state matches the handoff's claims)` is an actual all-clear. ⚠ And even that is not yet airtight: an unreachable cluster, or an explicit handoff path outside a git repo, still reaches it having checked nothing — tracked as a follow-up on PR #326.

4. **Surface what the subsystem index already records for this repo** (read-only, ~1 command, no network):

   ```bash
   python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo <repo>
   ```

   This is the **read half** of the store `/analyze-service` and `/handoff` write to — the terse pointer sheet that *outlives the handoff doc you just read*. It had two writers and no reader, so nothing ever opened it at resume time.

   **What the bare command prints (the digest):** the caveat, then a one-line **INDEX of every entry in the scope** — ref, bullet count, `sensitivity=` — never truncated, then **exactly ONE entry in full** (`## Pointers` + `## Nuance / work-history`), then a line saying the other bodies were listed but not printed. Measured 2026-08-13 on `datapacket-talos`, the scope holding 25 of the store's 29 entries: **4,876 B / ~1,219 tokens**, against **31,485 B / ~7,871 tokens** for the old default — which *also* hid 13 of the 25 entries. So it is now both cheaper and complete; the earlier claim that it "costs a page, not a dump" was false for the only scope big enough to matter, and this is the corrected, measured version.

   **The featured entry is a PICK, and the output names the basis.** It is chosen by running the writer's own path→subsystem matcher over the paths quoted in this repo's newest `claudedocs/handoff-*.md` (`resolved via <doc> — N quoted path(s) name it`), and otherwise by the newest entry file (`most-recent fallback`). Read that phrase before you read the entry: on the real store the fallback fires more often than not (15 of the 40 most recent datapacket handoffs resolved to an entry), and **a fallback pick says nothing whatsoever about relevance.**

   **Drill down instead of dumping.** `--list` prints the index alone (2,580 B on that same scope, measured 2026-08-13) — use it when you only need to know what is on record. `--ref <name>` prints any single entry in full; that is the right follow-up to a line in the index, and an ambiguous ref is reported with its candidates, never picked. `--limit N` restores the old print-N-bodies behaviour with its loud truncation notice — reach for it only when you genuinely want the dump.

   **The index is capped at 100 lines per page, newest-first by entry-file mtime.** Below that cap (every scope today — the largest holds 25) nothing changes and the header still says `none omitted`. Above it the header switches to `entries 1–100 of N` and a notice names the remainder and the flag: `--page 2`, `--page 3`, … reach the older ones, oldest last. The order is stated in the output on purpose — cutting an *alphabetical* index hides entries by an accident of their names, cutting a *recency* index hides the stale ones.

   **`--search <query>` reads by MATCH instead of by whole entry** — reach for it when you want one fact rather than an orientation, and as scopes grow past the point where a body is affordable. It prints HUNKS, each carrying its own `scope/ref`, section, `file:line`, `sensitivity=`, and its **score beside the threshold**, so a weak match is visibly weak. Measured on `datapacket-talos`: `--search minio` 5,267 B / ~1,316 tok, `--search 'nginx ratelimit'` 1,711 B / ~427 tok, and a full-store scan of all 29 entries takes ~40 ms (stdlib only — it shells out to nothing).

   - Matching is fuzzy and **coverage-based**: each query term contributes its share, so a two-word query needs both words and `nginx kryptonite` returns nothing rather than `nginx`'s hits. Typos are tolerated (`conection` → `connection`); concatenations work (`ratelimit` finds `rate-limit`); tokens shorter than 4 characters must match exactly.
   - 🔴 **A no-match is not an empty screen.** It says how many entries were scanned, and either names the closest sub-threshold candidate with the exact `--threshold` that would surface it, or says plainly that nothing scored above zero — i.e. an *absent* term, not a weak one. Read which of the two you got before rephrasing.
   - `basis=entry-name` on a hunk means the ENTRY's name matched and none of its lines did — the hunk is a worked example, not the thing you searched for.
   - **Context is the enclosing bullet by default; `-C N` overrides with N raw lines** and that choice is yours. Entries are structured and their bullets wrap, so a raw window can cut one in half and emit a fragment that reads like a complete instruction. The bullet is the safe thing to quote; a raw window shows you what SURROUNDS the match (the heading above it, the next bullet), which is what you want when orienting rather than quoting.
   - `--all-scopes` searches the whole store, not just this repo's scope; `--max-hits N` raises the display cap (default 10, truncation always printed).

   🔴 **Everything it prints is `from index` — RECALL, NEVER LIVE OBSERVATION.** It was curated by *past* sessions, was not re-derived just now, and was not matched against anything in this session. Read the `caveat:` line and carry that label into your report: an index bullet is a **pointer to verify**, and it may describe a gotcha already fixed (pruning is manual). Never fold it into the live-state findings from step 3 — those were measured, these were remembered. Entries carry client-identifying detail: honour the printed `sensitivity=` and never copy a line into a public repo.

   **`scope-absent` / `scope-empty` means NOTHING RECORDED YET — that is the ordinary case, not an error and not a clean bill of health.** The store is young (a couple of scopes against work spanning ~12 repos), so most repos will print this. Say plainly "the index has nothing for this repo yet" and move on; do **not** report it as an absence of drift, and do not go create an entry — that is `/handoff`'s confirm-gated job at the *end* of a session, not this step's.

   **Non-blocking, always.** It never prompts and never writes. If it exits non-zero (missing store, unreadable or malformed entry) print the stderr line verbatim, note that recall was unavailable, and **continue the resume** — a broken index is not a reason to stop re-entering the work. Never fall back to recollection about what the index "probably says".

5. **Report**:
   - One-paragraph "where things stand" (reconciled with what you just verified).
   - **Ranked next steps**, with the single highest-leverage action first.
   - Any drift you found between the handoff and live state.
   - Anything the index recalled that bears on the next steps — labelled `from index`, kept separate from what step 3 measured.

Then wait for direction. Pair: `/handoff` (it writes the index entries this step reads).
