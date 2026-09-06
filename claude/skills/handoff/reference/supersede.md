# Superseding a block, not just appending after it

`Open investigations` / `Findings` / `Gotchas` are APPEND headings. That is deliberate
and correct: the value of a handoff is partly the trail showing a prior reading was
*corrected* rather than quietly deleted. This file is about the failure mode that
design has, and the one extra step that closes it.

## The failure

**Append preserves the old block VERBATIM — including its heading, its status word,
and any instruction it gave.** So after your delta lands, the document asserts BOTH
readings at once, and a reader meets whichever comes first in the file. That is
almost always the OLD one, because appends go to the bottom.

MEASURED, in `<datapacket-talos>/claudedocs/handoff-draft-reaper-bug.md`, 2026-09-03
→ 2026-09-06:

- A block told the next session that a `Skipped …` log count should be **non-zero**
  and that non-zero was "the fence working". This was **backwards** — zero is the
  healthy result, because the counter measures fence *disagreement*, not fence
  *operation*.
- The correction was written, correctly and in detail — as a NEW gotcha, appended
  **342 lines further down**.
- Both were live for three days. A session following the document top-to-bottom would
  have reported a working fix as broken, then found the correction only if it kept
  reading to the end.

A second instance in the same document on the same day: a block headed `UNFIXED`,
whose `Next probe` asked the reader to *decide whether* to make a change that had
already been decided, implemented, merged, deployed and measured. The ranked item had
been struck through three days earlier; **the block that argued for it had not been
touched**, and a struck-through rank is visibly a ledger entry while a prose block in
the present tense carries no such signal.

Two of that document's nine investigation blocks were stale this way simultaneously.
Treat that as the base rate, not an outlier.

## The rule

**When your delta supersedes an existing block, edit that block's heading in the SAME
delta.** The append still carries your new finding; the edit is what stops the old one
reading as current. Concretely:

- Change the heading to `~~<old heading>~~ SUPERSEDED <date> — see <the new block>`,
  or `~~…~~ RESOLVED — <one line>`.
- **Delete any INSTRUCTION the block gave that is now wrong.** Preserving a corrected
  *reading* is the point; preserving a corrected *instruction* is arming a landmine.
  These are different things and the append heading does not distinguish them.
- Keep the measurements. Values age well and are often still the baseline the new
  block compares against — the retired block above kept its pre-merge baseline for
  exactly that reason.
- Say in the retired block *why* it was wrong, not only that it was. That is the part
  a future reader cannot reconstruct.

## The trigger, so this is not left to memory

**Closing a ranked item does not close the block that motivated it.** When you strike
a rank through, grep the document for the block that argued for it and retire it in
the same commit. If you cannot find one, say so — it means the rank was filed without
a supporting finding, which is worth knowing on its own.

## The neighbouring failure

Same document, same session: a PR changed two surfaces and the ledger ranked only one.
The half that was covered was covered thoroughly — five audit rounds, a mutation sweep,
a production measurement — and **the thoroughness is what made the ledger look
finished**. Nothing pointed at the other half for eleven days.

**Enumerate what a change touched from the CHANGE, not from the ranks it generated.**
A ranked list records what someone thought to write down; it is not a coverage map.
