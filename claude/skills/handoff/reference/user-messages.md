# Reading back what the operator actually asked for, across one arc

`scripts/session-analysis/extract_user_msgs.py` extracts the genuinely
user-TYPED messages from a **scoped** set of sessions — the kickoff, the
corrections, the "no, do X instead" — with the model's replies, the tool output
and the harness's injected boilerplate removed.

Load this when you need the operator's own words: reconstructing why an arc
went the way it did, auditing whether a handoff doc reflects what was actually
asked, or recovering an instruction that was given once and never written down.

## The cost it removes — MEASURED 2026-09-25 on this host

Before the selectors existed, the tool took one argument (an output path),
walked the whole corpus, and wrote records of `{project, kind, text}`:

| | unscoped corpus walk | `--arc handoff-find-session-arc-resolution` |
|---|---|---|
| transcripts read | 974 | 2 |
| records | 13,768 | 27 |
| output | **54.1 MiB** | **172 KiB** (≈322× smaller) |
| wall time | 12 s | 23 s (21 s of it is the arc resolver's corpus walk) |

⚠ These counts and the dedup counts further down come from runs minutes apart on
a **live** corpus, so they differ by a few records. Neither is wrong; the corpus
grows while you measure it, which is exactly the confound the dedup section
below had to be re-measured to remove.

🔴 **And the obvious compose could not be completed at all.** Enumerate an arc's
sessions with `find-session.py --arc … --json`, extract corpus-wide, then grep
the ids out — except the records carried **no session id**. The only filter
available was `project`, a cwd basename, which for devrc work selects the entire
devrc corpus. So the third step had nothing to grep on, and the answer to "what
did I ask for across this arc" was 54 MiB of everything, hand-read.

⚠ `--arc` is not faster in wall time; it is 322× smaller in what you have to
read. Once you already hold the ids, `--session` extraction is **~0 s** — the
whole 21 s is the arc resolver's own corpus walk for reader sessions.

## Commands

```bash
E=~/workspace/devrc/scripts/session-analysis/extract_user_msgs.py

# an arc — a slug, a basename, a path, or a SESSION ID that opened with the doc
python3 $E --arc handoff-cairn-phase3
python3 $E --arc claudedocs/handoff-cairn-phase3.md
python3 $E --arc 8951d8f0-1064-4113-aae8-c9913f5ef5cb

# named sessions; --session repeats
python3 $E --session <id-a> --session <id-b> --jsonl -o ./msgs.jsonl

# ids on stdin
python3 ~/workspace/devrc/scripts/find-session.py --arc handoff-foo --json \
  | jq -r '.members[].session_id' \
  | python3 $E --ids-file -

# a file of ids; `#` comments and blank lines are skipped
python3 $E --ids-file ./ids.txt
```

With **no** selector it still walks the whole corpus — that is the 54 MiB run
above, and the output says `UNSCOPED` so a scoped-looking answer can never be
one of those by accident.

## The arc resolver is IMPORTED, never re-implemented

`--arc` resolves the same session chain `find-session.py --arc` prints, by
calling `find-session.arc_report` — which owns all four steps: find the repo
holding the doc, walk the corpus for reader sessions, `handoff_arc.resolve_arc`,
then append the note naming the corpus that walk did **not** search.

Each of those is a claim about coverage. A second spelling in the extractor
would publish a chain that looks complete and is not, and the two tools would
answer *"which sessions worked this doc"* with different sets while neither said
so. Under `--arc` every coverage note the resolver produced is reproduced in the
output header, including the `N of M commit(s) … carry no session id` line
**when N is zero** — an absent coverage line is indistinguishable from "nothing
missing".

## Exit codes

🔴 A zero cannot distinguish a wrong name from an empty arc. Four different
facts with four different fixes get four different codes, and every one prints
its reason on stderr.

| code | meaning |
|---|---|
| 0 | messages were extracted |
| 2 | bad invocation — a flag conflict, or an `--ids-file` that could not be read, or one holding no ids. **Nothing was searched.** |
| 3 | `--arc` only: the seed named no handoff doc, or no `$DEVRC`/`$HOMELAB`/`$DATAPACKET`/`$CIVITAI` checkout holds it. 🔴 **NOTHING WAS MEASURED** — a typo in the name lands here, not on 4. |
| 4 | `--arc` only: the doc resolved and the arc **was** measured, and it has zero member sessions. A **measured** empty arc — a real finding about the doc. |
| 5 | session ids were selected and **none** resolved to a transcript on this host. Every unresolved id is named on stderr. Check the peer host before concluding the sessions are gone. |
| 6 | transcripts **were** read and held zero user-typed messages after filtering — the sessions exist and are empty of typed input. |

The pair that matters most is **3 vs 4**: 3 means the instrument never ran, 4
means it ran and the answer is zero. Treating them alike is the scoped-zero-as-
absence mistake `handoff_arc` exists to refuse.

## Output contract

**Markdown (default)** — grouped by session, to stdout unless `-o PATH`:

```
# user messages — arc handoff-<topic>

2 session(s), 27 message(s)

> arc handoff-<topic>.md (repo devrc)
> 0 of 3 commit(s) on this doc carry no session id — those writers are NOT in this chain
> the opencode corpus was NOT searched for readers …

## <session-id> · originated · 15 message(s)

### 1. 2026-09-18 21:02:05 · typed

<the operator's message>
```

The `>` lines are the coverage notes — **read them**; they are what says whether
the chain is complete. Under `--arc` the session heading carries the member's
role (`originated` / `earliest-stamped` / `wrote` / `resumed`), which is
`handoff_arc`'s vocabulary, not a second one.

**`--jsonl`** is the canonical machine form — one record per line, every key
always present:

```json
{"session_id": "…", "project": "…", "ts": "2026-09-18T21:02:05.619Z",
 "kind": "typed", "text": "…", "arc_role": "originated"}
```

`kind` is `typed` (prose the operator wrote) or `command` (a slash command,
rendered as `<name> <args>`). `arc_role` is `null` outside `--arc`. `ts` is the
record's own timestamp, empty when the transcript carries none; rows are sorted
chronologically across sessions, and rows with no timestamp sort last rather
than first.

## Dedup

An identical message seen twice **within one project, of the same kind** is
emitted once and the suppressed count is reported on stderr (`deduped=N`). That
is the point under `--arc`: the same kickoff pasted into every resumed session is
noise, not five findings. `--no-dedup` keeps every repeat.

The key is `(project, kind, text)`. **`kind` is load-bearing**: `/handoff` typed
as prose and `/handoff` the slash command are two different events with the same
text, and a key omitting `kind` keeps whichever the walk reached first.

⚠ **This changed on 2026-09-25 and the unscoped total moved.** Commands used to
dedup GLOBALLY while typed messages deduped per-project — an inconsistency that
made which project "won" a shared `/handoff` depend on unsorted glob order. Both
now dedup per `(project, kind)`, and the corpus walk enumerates in sorted order,
so a run is deterministic.

🔴 **Measured by running both dedups over ONE frozen file list**, not by running
the two scripts back to back: the first attempt at this number did that, six
minutes apart, and live sessions wrote new messages in between — the delta was
part dedup change and part corpus growth, and three rows attributed to the
change were neither. Over 975 transcripts and 19,406 pre-dedup records:
**13,779 → 13,798 (+19, all commands)**, and the new output is a **strict
superset** of the old — nothing that used to be kept is now dropped. That
superset property is the claim worth carrying; the +19 is one corpus on one day.

## Where it reaches the corpus — one rule, one place

Both paths go through `scripts/lib/transcript_search.py`: selected sessions via
`find_transcript`, the unscoped walk via `iter_transcripts`. Both apply
`is_corpus_member`, so a `subagents/` transcript is excluded either way — a
subagent is not a session anybody typed into.

**So the unscoped walk is every SESSION transcript, not every `.jsonl`.** On this
host that is ~975 of ~6,656 files; the other ~5,681 are subagent transcripts.
If you need those, this is the wrong tool — `scripts/audit-rule-firing-sweep.py`
is the one that deliberately wants both tiers.

⚠ Retracted 2026-09-25, recorded so nobody re-derives it: the tool used to keep
a private glob justified as *"the unscoped walk wants every `.jsonl` including
`subagents/`"*. That was false in four places at once — two docstrings, the
`JSONL_GLOB_SITES` ledger reason, and the line the tool **printed on every
unscoped run** — and the private walk had never included one (measured: 0 of
5,681). It was also the *narrower* copy, checking only the immediate parent dir
where `is_corpus_member` checks every parent part.

## 🔴 The output is the operator's own words

It is the most sensitive thing this repo's tooling produces. devrc is **public**
and `claudedocs/` is committed: never paste extracted text into a handoff doc, a
commit message, a PR body or a test fixture. Write `-o` to a scratch path, read
it, and let it go.
