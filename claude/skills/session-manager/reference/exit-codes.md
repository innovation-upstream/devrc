# Exit-code rationale

The table is in the SKILL body. This is the *why*, kept out of the always-on cost.

## The contract

"Ran, found nothing" and "could not run" MUST be distinct. A caller reading success off a
truncated run is the whole hazard the exit contract exists for.

| code | meaning |
|---|---|
| `0` | the scan ran and produced ≥1 window row — **including** a partial scan where one host was unreachable |
| `2` | bad arguments / malformed target; for `tail`, the host answered and there is no such window |
| `3` | every requested host answered and the answer is a real, measured `0` |
| `4` | no requested host could be reached — the `0` is unmeasured |
| `5` | `tail` only: the host answered and there is no tmux **server** on it |

## Why `tail`'s "no such window" is 2, not 4

The host answered. Reporting it as unreachable states a false fact about the host and sends
the operator to debug SSH over a typo. `tail`'s JSON carries `reachable` **and** `found`, and
`found: null` means the host never answered — so it has said nothing about whether the target
exists.

`--json` prints that payload on **every** exit path (0/2/3/4/5), not only on success. It used
to print only on success, so exits 2, 4 and 5 wrote nothing to stdout while the docs
documented their payload; a machine consumer got an empty stdout on exactly the three
outcomes the discriminants exist to tell apart, and could only re-derive them by parsing
English off stderr. The human sentence still goes to stderr, so stdout stays parseable.

## Why exit 5 had to be split out of 3

`tmux` saying *"no server running"* is a **reachable** host — correct for a whole-host scan,
where it means a live host with zero windows. But `tail` then took the success branch and
published `found: true, text: ""` → exit 3, which is identical to *the window exists and its
scrollback is empty*, the one thing exit 3 is documented to mean. Two different facts, one
exit code, and the doc matched neither.

A down server now gets `found: false, no_server: true` and its own code. Deliberately **not**
exit 2 (the target may be spelled perfectly — the repair is starting tmux, not fixing a typo)
and **not** exit 4 (the host *did* answer).

The no-server branch must come **first** in `tail_window`, before the plain `reachable`
return, or it is unreachable.

## `--host` defaulting under `tail`

`--host` defaults to `all`, which is meaningless for a command targeting one window, so
`tail` resolves it to the **local** host. The default is recorded, not silent:
`host_defaulted: true` in the JSON, and the not-found message names the host searched plus
how to search the other one. In plain-text mode it goes to stderr, so piping the scrollback
is unaffected.

## `--claude-only` and exit 3

Under `--claude-only`, every summary count describes the **filtered** set. That is the safe
direction: counting the unfiltered set would publish `total_sessions: 20` beside an empty
table and **exit 0** — "ran, found windows" over nothing printed. Counting the filtered set
makes the same run a real measured zero (**exit 3**), because zero *agent* windows is what
was asked, and `summary.excluded_shells` keeps it from reading as an empty host.

🔴 **The predicate is the CLASS axis, not the `claude` flag.** `--claude-only` drops
`CLASS=shell` (via `dropped_by_claude_only` → `row_class`), so a `cluster` dispatch — an
agent with no pane, hence `claude: null` — is **kept**. The old `r["claude"]` spelling was
correct only while every row was a tmux pane; with the `kind` axis it silently reclassified
every cluster agent as a shell and deleted it.

🔴 **And whatever the filter removed is attributed to the FILTER, not to the build.** The
filter runs *before* `summarize` and `measured_caveats`, both of which derive from the rows
that survived — so a kind it removed entirely would have shown up in `kinds_produced` as a
kind this build never emits, and rendered *"X is ENUMERATED but NOT PRODUCED, so no such row
appears and its absence is NOT a measured zero"*, which would be false in all three clauses.
`caveats.kind_scope.kinds_excluded_by_filter` (mirrored at
`summary.kinds_excluded_by_filter`) names those kinds, and the rendered caveat carries an
explicit *"a FILTER REMOVED every kind=… row this scan produced"* clause instead. The key is
**absent when no filter ran** and `[]` when one ran and removed no whole kind — the same
not-measured-vs-measured-none distinction as `excluded_shells` being `null` rather than `0`.

Reachable today with no cluster row anywhere: `--claude-only` over a host whose tmux rows are
all bare shells removes the last `tmux` row.

🔴 **`excluded_shells` was `excluded_non_claude` until this change, and THE SCRIPT IS THE
AUTHORITY ON WHICH NAME IS LIVE — not this file.** These skill docs deploy as a **nix-store
copy**, so they only change on a `home-manager switch`; `scripts/session-manager` is read from
the checkout. Between a `git pull` and the switch the two genuinely disagree, and the doc is
the stale one. Settle it by reading the emitter, never the prose:
`git grep -n 'excluded_' scripts/session-manager`, or just run a scan and look at the key.
Same rule for every field named here.
