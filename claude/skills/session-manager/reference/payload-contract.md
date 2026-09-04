# The JSON payload contract — flags, counts, views, row fields, caveats

Loaded when you are reading `--json` output field by field, when you need a flag the
SKILL body's table does not list, or when you are wondering whether an absent key is a
measured zero. 🔴 **The payload is the authority on all of it** — everything here was
written against `scripts/session-manager` and can only ever be a snapshot of it.

_Moved verbatim out of `SKILL.md` on 2026-08-21, when the body was cut from 23,233 B. The core keeps every load-bearing claim below in compressed form; this is the wording it was cut from, and the evidence behind it._

## Every flag

| flag | effect |
|---|---|
| `--json` | JSON (default is a table). Compact, not indented — 34% of the payload was whitespace for a reader that does not exist |
| `--lean` | 🔴 **with `--json`, prefer this.** The agent-shaped view: rows trimmed to the fields that answer this tool's question, **untruncated**, with every measurement discriminator and the caveats kept |
| `--host workbench\|laptop\|all` | default `all`; `tail` resolves `all` to LOCAL |
| `--claude-only` | drop the **shell** rows (`CLASS=shell`) — a `cluster` dispatch is an agent and is KEPT. Every count then describes the FILTERED set; `summary.excluded_shells` says how many went and `summary.kinds_excluded_by_filter` names any kind removed entirely |
| `--match SUBSTR` | **repeatable**, `scan`/`list`/`detail`. Keep only rows whose **`task`, `label` or `codename`** contains SUBSTR, case-insensitively. Terms are **ANDed** (a row must match all — the same default `find-session.py` uses), and one term may satisfy itself in a different field from another. Every count then describes the MATCHED set; see below |
| `--match-path` | add **`path`** to the fields `--match` searches. **OFF by default, and that is the feature** |
| `--no-ch` | skip ClickHouse — the client is never constructed |
| `--no-capture` | skip the pane scrape; **every** `waiting_probable` AND `unsent_prompt` becomes `null` (both roll-up numbers `null`, never `0`) |
| `--pane-preview` | publish each Claude pane's **visible screen** as `pane_preview`. Costs no extra tmux work (the capture already runs — `waiting`/`unsent` are derived from it and it used to throw the screen away), but makes the document **2.63x** larger: measured live back to back, 122,731 B without / 322,204 B with. Off by default for that reason; `--lean` drops it, so do not pass both |
| `--fuzzyclaw` / `--no-fuzzyclaw` | the task-file join is **OFF by default** (see below) |
| `--no-ledger` | skip the per-host agent-ledger read. Rows then have **no age and no session id** — the #419 view, reproducible on demand |
| `--plain` | `tail` only: strip ANSI at the source instead of `sed`-ing it out |
| `--stale-threshold <secs>` | default 3600; `age >= threshold` is stale |
| `--lines N` | `tail` scrollback depth (default 100) |


## 🔴 `--match` — asking the LIVE scan "which window is about X"

This is the flag that makes `session-manager` the right instrument for *"find the thing I
lost track of"*. Measured 2026-08-28: the transcript-archive walk (`find-session.py`) takes
**30.1 s**; `session-manager --json --lean --no-ch` takes **1.82 s** and 66 of its 72 rows
carried a populated `task`. For a question about what is running NOW, the archive is the
wrong corpus. `find-session.py --live` drives exactly this flag.

🔴 **`path` IS NOT SEARCHED BY DEFAULT, AND THE NUMBERS ARE WHY.** One query substring, one
72-row scan:

| matched against | rows hit |
|---|---|
| `task` | **1** — the correct window |
| `path` | **29** — nearly every window shares a repo path |

A filter whose answer is 40% of the fleet is the unfiltered scan wearing a filter's
authority. Pass `--match-path` when you genuinely mean "everything in that checkout".

**What the payload tells you, so a zero is never ambiguous** — in `report["filters"]` and
mirrored into `summary`:

| key | meaning |
|---|---|
| `filters.match` / `summary.match` | the terms. **`null`, never `[]`**, when no filter ran |
| `filters.match_fields` / `summary.match_fields` | what was ACTUALLY searched — this is where you see whether `path` was in the set |
| `filters.matched` / `summary.matched` | rows that survived, stated positively |
| `filters.excluded_by_match` / `summary.excluded_by_match` | rows the filter dropped |

🔴 **`matched` and `excluded_by_match` are `null`, never `0`, in TWO states**: when no
filter ran, and when **no host was reachable**. A `0` there would say "the filter ran and
matched nothing" about a scan that measured nothing — the rule `detail_matched` already
followed. The **terms and the field list are still published** over an unreachable fleet:
which filter was *requested* is known regardless of whether anything answered.
⚠ A **partially** reachable fleet publishes real counts — one host answering is a
measurement. 🔴 **`excluded_shells` and its `summary` mirror now follow the SAME rule**
(#1031): `null` over an unreachable fleet, a real count otherwise. Until that landed the
two halves of one render contradicted each other — `FILTER --claude-only: 0 shell
window(s) excluded` beside `FILTER --match 'x': an unmeasured number of row(s) matched` —
so a consumer reading the first line learned a measured-looking zero about a fleet nobody
reached.

🔴 **`summary.matched` is NOT `summary.total_sessions`.** On a `detail` they differ by
construction — the row filter matched N, then `filter_report` narrowed to 0 or 1. The
table's `FILTER --match` line quotes `matched`, because that line describes the FILTER.

**Exit code**: reachable hosts + zero matches is **`3` (EXIT_EMPTY)**, never `0`. No host
reachable is still **`4`** — the zero is unmeasured either way the rows are filtered.

`--match` composes with `--claude-only`; both narrow the same row set and share ONE
`kinds_excluded_by_filter` answer. It has no effect on `tail`, which says so on stderr.

## 🔴 `detail <session>:<index>` — the output SHAPE, and the two row keys

**`detail --json` returns the FULL REPORT**, with `hosts[*].windows` narrowed to the one
matching row. It does **not** return `{"window": {...}}`. `main()` calls `filter_report`,
which keeps every host's reachability facts — dropping them would turn *"unreachable"* into
*"not found"*. So read `blob["hosts"][<host>]["windows"][0]`, not `blob["window"]`.

**The row keys are `window_index` and `path`** — not `window`, not `cwd`. A `.get("window")`
returns `None` forever and reads exactly like a measured null.

🔴 **A MISS IS NOW LOUD.** An address matching no window used to hand back a silent empty
window list, byte-identical to *"found it, the window is idle"*. It now prints to **stderr**
and records the same facts structurally:

```
detail: NO SUCH WINDOW 'scratch3:3' — session 'scratch3' has windows ['1', '2'];
you asked for index '3' (searched: workbench); NOT searched: laptop — unreachable, …
```

| key | meaning |
|---|---|
| `filters.detail_target` | the address you asked for |
| `filters.detail_matched` | rows matched. `0` is the miss; **`null` means NO host answered** |
| `filters.detail_sibling_indices` | the indices that exist for that session name **on the hosts that answered, BEFORE any row filter**. `[]` = no such session; **`null` = unmeasured** |
| `filters.detail_filtered_out` | **`true` = the window EXISTS and a row filter removed it.** `null` = unmeasured |

🔴 **UNDER `--claude-only` / `--match`, A MISS IS USUALLY THE FILTER'S DOING.** Rows are
filtered in `gather` BEFORE `filter_report` narrows, so a naive sibling list enumerates only
the survivors. Measured live at an earlier revision: `detail scratch2:1 --claude-only`
answered *"session 'scratch2' has windows ['2', '3', '4']"* while window 1 existed on BOTH
searched hosts — a flat falsehood that `(searched: …)` made read as authoritative.

`gather` therefore samples `filters.prefilter_window_indices` (`{session: [index…]}`,
**`detail` only** — a 1-row `--match` scan must not carry a fleet's index map back) and the
message now distinguishes three misses:

```
detail: WINDOW 'scratch2:1' EXISTS but a ROW FILTER (--claude-only) removed it — this
empty result is the FILTER's doing, NOT a measured absence. …
detail: NO SUCH WINDOW 'scratch2:9' — session 'scratch2' has windows ['1','2','3','4'];
you asked for index '9' (indices measured BEFORE the row filter --claude-only) …
detail: NO SUCH WINDOW 'zz:1' — no session named 'zz' exists on any host that answered …
```

⚠ **The map contains only hosts that answered — but not because it filters on that.** An
unreachable host's `windows` is `[]`, so it contributes nothing wherever rows are read. The
property is over-determined (`run_tmux` returns empty stdout on every unreachable path
*and* the population loop skips such a host), so no single-mechanism change reds a test.
Do not read a green suite as licence to delete the remaining enforcement.

🔴 **Nothing is printed and the fields are `null` when no host was reachable.** An address
that could not be CHECKED is not an address that does not EXIST — the exit code is `4` there,
not `3`, and saying "no such window" would send you to re-check your spelling instead of your
SSH. **Do not guess an index from the session NAME**: `scratch3`'s windows are `1` and `2`.

## Summary buckets, and `kind` vs `CLASS`

🔴 **`summary.status[bucket]` is one key per CLASS plus `total`** — `{claude, shell, total}`
on every scan today. `claude` and `shell` are **always present** (pre-seeded, so a zero there
is a real zero); every class **beyond** those two — `cluster`, `unknown_kind` — appears only
when such a row exists, so the *absence* of a `cluster` key is not a measured zero. Same at
the top level (`summary.claude`/`summary.shell` always; others on demand).

🔴 **`kind` (row field) and `CLASS` (table column) are DIFFERENT axes — do not conflate.**
`kind` is `tmux` | `cluster`: **what the entity is**. `CLASS` is `claude` | `shell` |
`cluster`: **how it is counted**. Every row today is `kind: tmux`; `cluster` is enumerated
for a clawgate dispatch with no pane and is **not produced yet**, which
`caveats.kind_scope` states in the payload (`kinds_produced` is **measured from the rows**,
not asserted). So an absence of cluster rows is **not** a measured absence of cluster work —
clawgate lives in `clawgate_queue`, a different population that is never double-counted with
these rows. `kind` is never null; `runtime` frequently is, and means something else
(**which agent software**, from the ledger).

## 🔴 Which output to ask for — you are the only consumer

Measured: **0 interactive shell invocations in 30 days against 55 agent references**, confirmed
by the operator 2026-08-14. An agent reads this, and pays by the token.

| | 77 rows, both hosts, 2026-08-21 | 75 rows, 2026-08-14 | faithful? |
|---|---|---|---|
| table (default) | 24,658 B ≈ 6.2k tok | ~3,280 tok | ❌ **lossy** — truncated cells, rows whose task exceeds the 25-char column |
| `--json` | 103,801 B ≈ 26k tok | ~14,017 tok | ✅ |
| `--json --lean` | 79,008 B ≈ 20k tok | ~9,629 tok | ✅ on what it keeps |

⚠ Both columns are single scans of a live fleet whose size moves; the RANKING is what the
table is for. The 08-14 column is kept only to show the drift — it predates the
`unsent_prompt` pair and `not_measured`, and was quoted as current for a week after it stopped
being so.

🔴 **`--lean` TRIMS ROW FIELDS ONLY, and the row fields are barely half the payload.** Measured
2026-08-21 on the lean output: `hosts` 42.5 KB (of which rows 42.1 KB), `clickhouse` 17.4 KB,
`clawgate_queue` 12.7 KB, `caveats` 4.3 KB, `ledger` 2.8 KB, `not_measured` 1.8 KB,
`summary` 1.0 KB. So `--lean` alone buys 24% off the full payload, not the ~31% the 08-14
figures implied, and **the second-largest block is one flag away**: `--no-ch` drops the
ClickHouse rows, which answer session-history questions rather than "is anything waiting on
me". Two smaller structural costs, neither capped: `clawgate_queue` enumerates the whole queue
(96 entries / 12.1 KB that day) and `ledger.conflicts` is emitted twice, top-level and again
per host.

**Ask for `--json --lean --no-ch` unless you need a dropped field.** It is cheaper than the full payload
AND more faithful than the cheap one. `lean_row_fields` and `lean_host_fields` travel in the payload
naming exactly what this view CARRIES — so a key absent from a row was omitted by the view, never
measured as null. `caveats`, `summary.waiting`'s tri-state, `clawgate_queue` and every per-host
measurement status are kept in full, because a cheap payload that can lie is worse than an
expensive one.

Dropped from **rows** (8): `window_id`, `window_name`, `codename`, `pane_id`, `command`, `panes`,
and the `ledger`/`fuzzyclaw` sub-objects — duplication, their useful contents are already flat on
the row. `label_source` is deliberately KEPT: like `age_source` it is provenance, and it is the
only thing separating a row labelled from a real directory from one labelled because the cwd
yielded nothing.

Dropped from **hosts** (2): `ssh_target` (fixed config the caller already knows) and
`live_window_ids` (a ~346 B array no consumer reads).


## `label` + `hotkey` — a name for the sessions the slot table never named

`codename` is null for anything outside `tmux-scratch-slots.sh`; on the workbench that was
9 of ~30 windows. Every row now also carries `label` (+ `label_source`, the tier that
answered, + `hotkey`): **`codename`** if the session is a scratch slot → else the **leaf of
the pane's cwd** (`path`) → else **`main`** (`fallback`, i.e. cwd was `$HOME`/`/`/empty).
The table's old CODENAME column is now LABEL and renders `Grove (S)`.

🔴 **`hotkey` is the actionable half** — it is the `$mod+Shift+<k>` that gets you to the
window, read from the slot table's own `key` field. It is **`null` (never `""`) on tiers 2
and 3**: no binding exists for a session outside the table, so quoting a key there would
send the operator to press something and land nowhere.

🔴 **QUOTE `hotkey_display`, NEVER DERIVE THE CHORD FROM `hotkey` YOURSELF.** Every row
carries `hotkey_display` (in `LEAN_ROW_FIELDS` too): `null` when `hotkey` is null,
`Alt+<k>` for a lower-case key, `Alt+Shift+<K>` for an upper-case one.

**Case is significant and it is NOT a Shift convention.** Per `scripts/tmux-scratch-slots.sh`,
`M-v` → `scratch3`/violet and `M-V` → `scratch4`/**Vapor** — two DIFFERENT sessions.
Measured 2026-08-28: a run read `hotkey: v` off a row, answered `Alt+Shift+V`, and sent the
operator to a real window that was the wrong one. The derivation lives in ONE function now
(`hotkey_display`) and its result is on the row, so no reader has to perform it.

⚠ The TABLE's `LABEL` cell still shows the RAW key — `violet (v)`, `Vapor (V)` — because the
column is 14 characters and `Yarrow (Alt+Shift+Y)` is 20. That parenthesis is a **key**, not
a chord; run it through the rule above (or read `--json`) before quoting it to a human.

🔴 **`label` is NOT an address — `session:window` still is.** Two sessions sitting in one
repo resolve to the SAME label, so quote both. New non-scratch sessions get a real tmux name
at creation from `scripts/tmux-autoname-session.sh`; `label` is what covers the ones that
predate it.

## 🔴 `repo` — the PROJECT key, and it is not `label`

`label` answers "where is this" for an OPERATOR (the leaf of the cwd, or a codename).
`repo` answers "which project is this" for a CONSUMER: the **MAIN CLONE** of the pane's
cwd, so **every linked worktree of a repo carries ONE name**. That is a thing no string
operation on `path` can produce — a worktree called `clawgate-extension` is a worktree of
`homelab-talos`, and `ht-r11-930492` is not derivable from `homelab-talos` at all.

Its consumer is clawgate's tmux page, whose `projectOf()` prefers `repo` when non-empty,
falls back to the LEAF of `path`, then to `Other`. Before this field existed the leaf won
every time, so each worktree formed its own project group.

🔴 **IT IS RESOLVED ON THE HOST THAT OWNS THE DIRECTORY**, by one batched `sh -c` per host
over the same SSH transport as the tmux calls — `git rev-parse --path-format=absolute
--git-common-dir`, whose parent directory is the main clone. Roughly half these rows come
off the laptop, and a LOCAL `git rev-parse` would answer about whatever happens to sit at
that path on the workbench, or about nothing. `--show-toplevel` is the wrong flag: on a
linked worktree it returns the worktree itself, so it cannot group two worktrees at all.

🔴 **`repo_status` IS WHAT MAKES A NULL `repo` READABLE.** Never read a null as "not in a
repo" without it:

| `repo_status` | means |
|---|---|
| `ok` | resolved; `repo` names the main clone. The ONLY status carrying a name. |
| `not_a_repo` | MEASURED: the owning host says this path is not in a work tree (or its common dir cannot be honestly named — a bare repo, a submodule). |
| `home` | MEASURED and deliberately withheld: the main clone IS the owning host's `$HOME`. `projectOf()` routes an unparented shell to `Other`, and `repo` is the branch it PREFERS, so a name here would override that guard. |
| `no_path` | the pane reported no cwd. |
| `missing` | the probe answered for this host but not for this path (a partial reply). |
| `unmeasured` | **nobody looked** — the probe failed, timed out, or came back without its sentinel. NOT a measured absence. |
| `skipped` | `--no-repo`. |

Per host, `repos_measured` / `repos_status` / `repos_error` / `repos_paths` /
`repos_resolved` / `repos_unparseable` are the FOURTH independent measurement beside
`reachable`, `windows_measured` and `captures_measured`. The counts are integers only when
`repos_measured` is true; otherwise they are null, never 0.

Measured on the live fleet 2026-09-03: 90 of 92 windows resolved (laptop 31/32, workbench
59/60); the two that did not are `/home/zach` and a non-repo directory, both `not_a_repo`.

## The caveats are in the OUTPUT, not just in this file

`report["caveats"]` (structured) + one footer line each in the table, printed
unconditionally — an agent that runs the script cold never reads this file:

- `claude_detection` — `pane_current_command =~ /claude/`; a claude under a wrapper shell
  reads as `shell` (shallower than the `/proc` walk in `scripts/lib/claude_sessions.py`,
  which is not reachable over SSH — so both hosts are reported by ONE rule).
- `fuzzyclaw_scope` — `local_host_only`; a REMOTE row carries null `fuzzyclaw`. It says
  **nothing** about age/session-id/stale (it used to, and that was wrong — see below).
- `ledger_scope` — `per_host`; `age_secs` / `claude_session_id` come from the agent ledger,
  read on EVERY scanned host, so a REMOTE row has both and **can** be `stale`.
- `waiting_signal` — the enumerated signal set, the claude-rows-only scope, and the
  prompt-text exclusion with its reason. 🔴 That text is still excluded from `waiting` but is
  **no longer discarded** — the caveat points at `unsent_prompt`, which now carries it.
- `unsent_prompt` — the status vocabulary, the claude-rows-only scope, and `separate_from:
  waiting_probable` as a FIELD rather than a sentence a consumer has to parse.
- `pane_preview` — the status vocabulary, the claude-rows-only scope, the opt-in flag and
  the per-pane byte cap, all as FIELDS. 🔴 It is rendered **even though the field is off by
  default**, and that is the point: a reader seeing `pane_preview: null` on every row
  otherwise cannot tell "these panes are blank" from "nobody asked for the text".
  `pane_preview_status` is the discriminator — `disabled` (never asked, and it beats every
  other reason), `not_claude` (shells are never captured), `uncaptured` / `skipped` /
  `error` (asked, not measured), `truncated` (measured, but this is a PREFIX), `ok`.
  🔴 It is the **visible screen and never scrollback** — scrollback costs ~4,014 B per line
  fleet-wide and would breach clawgate's 4 MB ingest at ~650 lines/pane. Use `tail` for
  history, one window at a time.

…plus `report["not_measured"]` and a `▸ NOT MEASURED HERE` section, which are the same idea
one level out: not a qualification on a number this tool produced, but the list of
populations it produced nothing about.

## The agent activity ledger — where age / `stale` / `claude_session_id` come from

#419 switched fuzzyclaw off, which also switched off the only supplier of `age_secs`, the
`stale` bucket derived from it, and the `claude_session_id` the ClickHouse join needs.
Measured 2026-08-12 on the shipped default view: **0 rows with an age, 0 with a session id,
no `stale` bucket at all** — and nothing in the output said so.

Two writers now record one file per tmux PANE into `~/.cache/agent-ledger/` — a devrc-owned
Claude hook, and an opencode plugin — and the script reads each host's ledger with one `sh -c`
(locally and over SSH). Read `report["ledger"]`, never just the row:

- `status` — `ok` / `partial` (some host did not answer) / `error` / `skipped`. Only `ok`
  and `partial` publish integers; the rest are `null`, never `0`.
- `hosts.<host>` — per host: `live of seen`, its `tmux_pid`, and the rejections
  (`not_live`, `generation_mismatch`, `unparseable`, `no_window`) plus
  `generation_unchecked`, which counts records that were **kept while unverified**.
- `summary.rows_with_age` / `rows_with_session_id` / `age_sources` — the meter. A `stale=0`
  bucket means *either* nothing is stale *or* nothing has an age; only this tells them apart.

Row fields: `age_secs`, `age_source` (`ledger` / `fuzzyclaw` / `null` — which SOURCE answered;
the ledger wins), `runtime` (`claude` / `opencode` / `null` — which AGENT recorded it), `ledger`
(the joined record), `claude_session_id`.

🔴 `runtime` is not `claude`. The `claude` column is `pane_current_command =~ /claude/`, so an
opencode window reads `shell` — an agent counted as a bare prompt in every bucket. `runtime` is
what the row says it actually is.

🔴 **A null age is not age 0** — it means no writer has recorded that window. A session that
has not taken a turn since the hook was registered has none yet.

🔴 **Records carry the tmux SERVER pid and the reader checks it.** Window ids restart at `@0`
when the server does, so after a reboot a stale `@41` record and a fresh `@41` window would
otherwise collide and hand the new window a dead session's id. Spec:
`claudedocs/spec-agent-activity-ledger.md`.


## 🔴 `not_measured` — what this tool CANNOT see, and who owns it

A blind dogfood found this tool "precise about what it measured but it does not tell a cold
reader what it did **not** measure" — while **60 open PRs**, one conflicting for eleven days,
sat outside every number it prints. `report["not_measured"]` names each such population and
the **skill that answers it**: `pull_requests` → `standup`, `mail_queue` → `mailbox`,
`cluster_alerts` → `standup`, `initiative_board` → `initiatives`,
`gui_windows_outside_tmux` → `i3`.

🔴 **It is DERIVED from the report's own keys, not a written-down list.** An entry is emitted
only while the report carries no key for that population, so the day PR querying lands the
claim stops being made with no edit anywhere. This file has shipped a constant masquerading
as a measurement five times; a static list of "things we do not measure" is the same defect
with a longer fuse. `clawgate_queue` (clawgate) is **not** listed — that one *is* measured.



## Where everything lives

| path | what |
|---|---|
| `scripts/session-manager` | the script |
| `scripts/tests/test_session_manager.py` | the hermetic suite (mocks tmux, SSH, CH, FS) |
| `scripts/tmux-scratch-slots.sh` | codename table |
| `scripts/validation/chquery.py` | shared CH client — a LIBRARY, `sys.path`-inserted |
| `~/.config/activity-collector/env` | CH endpoint + creds (never hardcoded) |
| `~/.cache/bar-status/clawgate.json` | the clawgate-queue cache (`scripts/bar-status-poll`) |
| `~/.tmux/tasks/*.json` | fuzzyclaw task files (UNTRUSTED) |
| `~/.cache/agent-ledger/*.json` | the agent activity ledger, one record per pane |
| `scripts/lib/agent_ledger.py` | its record shape, read protocol and join filter |
| `scripts/claude-hooks/agent-ledger-hook.py` | writer 1 (Claude Code) |

🔴 _Corrected on the way out, rather than moved verbatim: the line here used to name five
topics as bare basenames — not routes, so two of them (`clickhouse-queries.md`,
`cross-host.md`) were reachable from nothing. **The registry is the table under "Reference
topics" in `SKILL.md`**, and `scripts/tests/test_session_manager_skill_size.py` now fails on
an orphan or a route that does not resolve. Do not re-create a second registry here._

## Gotchas

- Both hosts report `hostname` as `nixos`. The local label comes from `ACTIVITY_HOST` (env,
  then the collector env file), defaulting to `workbench`.
- `tmux` saying *"no server running"* is a **reachable** host with zero windows, not an
  unreachable one. Keep the two separate.
- `signal` / `kill` are **not implemented, deliberately.** This tool never writes to,
  signals or kills a window — which is the only reason it is safe to point at a live machine
  holding 40+ windows of real work. A `waiting` flag is a read; acting on it is not. A
  destructive verb needs its own PR and its own guards.
- Merged ≠ deployed: this file only reaches `~/.claude/skills/` on a `home-manager switch` /
  `ship.sh`. The script itself runs straight from the repo checkout.
