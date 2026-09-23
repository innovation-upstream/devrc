# Why right-aligning the rank marker moves fzf's ranking — MECHANISM UNKNOWN

Demoted from `claudedocs/handoff-mention-review-tui.md` on 2026-09-21 under that doc's byte
ceiling, per `test_no_handoff_doc_exceeds_its_budget`'s playbook step 2 (*demote dated evidence,
leave a pointer*). 🔴 **STILL OPEN — this was NOT closed.** It carries a live `Next probe`, and
the handoff keeps a pointer to it.

- as-of: 2026-09-20
- **Symptom + exact repro:** rendering the rank `f"{rank:>3}"` instead of `f"{rank:<3}"` changes
  fzf's output order. 120-row synthetic corpus, 20 queries,
  `fzf --filter <q> -i --tiebreak=end --nth=2..`, marker present in both arms.
- **Observed (with values):** right-aligned — top-1 changed on **7 of 20**, tail on 13 of 20.
  Left-aligned — top-1 on **0 of 20**, tail on 1 of 20. Match SET identical 20/20 both. fzf
  0.74.4. via: measurement
- **Ruled out:** that the marker becomes MATCHABLE under right alignment — this was the shipped
  explanation and it is FALSE. A query matching only the marker digits returns **0 rows under
  BOTH alignments**; positive control, 1 row with `--nth` dropped. via: measurement
- **Ruled out:** that field-1 WIDTH explains it. Width genuinely does reach fzf's positional
  tiebreak (identical field-2.. text at widths 3 vs 6 inverts; input order once equalised, and
  under `--tiebreak=index`) — but `:<3` and `:>3` are the SAME width, so offsets are unchanged
  between the two alignments. via: measurement
- **Leading hypothesis:** none. Recorded as unknown deliberately — the first explanation read as
  well as a true one and was wrong; a second invented under pressure would be a hypothesis
  wearing a comment's clothes.
- **Next probe:** `fzf --filter` both alignments over a corpus where every rank has the SAME
  digit count (ranks 100–199). Difference vanishes ⇒ digit-count variation is the cause; persists
  at constant digits AND width ⇒ it is in fzf's scorer, worth reporting upstream.

## Why the shipped code keeps `:<`

`PICKER_MARKER_RANK_W`/`picker_marker` in `scripts/mention-open.py` render left-aligned because
left-aligned measured better (top-1 changed on 0 of 20 vs 7 of 20). The *direction* is
reproducible and the choice rests on it; **why** right-alignment moves the ranking at constant
width is unknown, and the source comment says so rather than supplying a second plausible
mechanism.
