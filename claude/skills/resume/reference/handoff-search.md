# `handoff_search` — the cross-repo corpus surface of step 4

Routed from the `/resume` core — `~/.claude/skills/resume/SKILL.md`, source
`~/workspace/devrc/claude/skills/resume/SKILL.md`. Sliced out VERBATIM; the step
numbers below are that core's steps.

Load this for `--exclude-slug` (how to tell it landed), the exit-code vocabulary, and
the scoping difference that makes this surface a client-content leak risk.

4. **Surface what past sessions already recorded — TWO recall surfaces, BOTH UNCONDITIONAL. Run both now, before the report:**

   ```bash
   cairn recall --repo "<path>"
   python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "<this handoff's topic, in plain words>" --limit 3 --exclude-slug "<the handoff: basename from step 2>"
   ```

   **Query the TOPIC, not an open item** — every resume has a topic; not every resume has an open item, and a step that can only run in the second case is a conditional. **What it is FOR:** the `Ruled out:` bullets scattered across the corpus are what stop you re-running a probe someone already ran, which is why the index is section-grained. When a specific open item is not covered by the topic query, run the command again with that item's own words.

   🔴 **`--exclude-slug` IS NOT OPTIONAL POLISH — WITHOUT IT THE TOP HIT IS THE DOC YOU JUST READ.** Measured over 20 real runs: the session's own handoff took **23 of 60** hit slots and was the **#1 hit in 13 of 20**. That is arithmetic, not a ranker bug — you are told to query the handoff's TOPIC, and the best text match for a doc's topic is that doc. Pass the value step 2 printed on its `handoff:` line; the flag also takes a bare slug or a `claudedocs/…` path. The freed slots go to documents you have **not** read.

   How to tell it landed: the run prints `excluded=<slug>` on its scope line and an `in_scope_docs` **lower than** `indexed_docs`. ⚠ Not necessarily by one — it excludes that slug in **every** repo. 🔴 `excluded=` proves the flag PARSED, not that it MATCHED — the count is the half that proves a match, so read the pair.

   🔴 **A zero from an excluded run: read the rc, not the prose.** Excluding every document the corpus could have answered with is `🔴 EMPTY SCOPE` at **rc 4** — your filter emptied it — and only a scope that still holds rows returns `NO MATCH` at rc 0. Both name the exclusion in their own output.

   🔴 **THE TWO SURFACES ARE NOT SCOPED ALIKE, AND THIS ONE IS CORPUS-WIDE.** `cairn recall --repo` is scoped to one repo; `handoff_search` takes no `--repo` here and searches **every** repo in `handoff_index.REPO_ENV_HANDLES` — on this host `devrc`, `homelab-talos`, `datapacket-talos`, `civitai`, two of which are client repos. A devrc-topic query routinely returns client-repo sections above the devrc ones, and the tool's banner carries a staleness posture but **no** sensitivity language. So: read hits from another repo as recall, and **never paste one into a public repo or a client-facing artifact without checking which repo it came from** — the `<repo>/<slug>` on each hit is what tells you. Pass `--repo <label>` to scope it when that matters more than reach.

   **Keep `--offline`** — it answers from git refs with **no database**; dropping it silently starts requiring one that no test has ever exercised. Every response carries a recall banner and the literal `indexed_docs=N indexed_sections=M`: a hit is a **POINTER TO VERIFY**, never a current reading, and it may describe a gotcha already fixed. 🔴 **A zero is not automatically an answer** — the tool names which zero it got and exits non-zero for the four that are not readings: **3** broken index (nothing was ever indexed) · **4** empty scope (your filter selected no rows) · **6** unmeasurable corpus (the repos did not resolve) · **7** the repos resolved and derived zero handoff docs. 🔴 **A fifth non-zero is not a zero at all** — rc **2**, usage: the `--exclude-slug` value named NO slug (blank, or a bare prefix/affix like `claudedocs`, `/`, `handoff-.md`), so the search never ran. It is reachable from THIS fence, because the value comes from step 2's `handoff:` line: fix the value and re-run, or drop the flag. Only `NO MATCH` at rc **0** means the corpus was asked and is silent. **Non-blocking:** on any non-zero, print the stderr line, say retrieval was unavailable, and carry on with the item.

   **The rest of this step is about `cairn recall`, the first command.**
