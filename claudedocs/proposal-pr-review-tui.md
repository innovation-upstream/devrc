# Proposal: replace `nvim-octo` with a purpose-built PR review TUI

Status: **PROPOSAL — nothing built.** Written 2026-09-14.
Scope: research + recommendation. No application code exists; the snippets below are
illustrative shapes, not a partial implementation.

---

## 0. TL;DR

**Recommendation: BUILD IT — confidence MEDIUM-HIGH.**

**But not for the reason it was asked for.** The measured evidence says the rewrite is
justified by the *diff-reading experience*, not by speed. The speed half of the complaint is
real, is quantified below, and roughly 60% of it is reachable inside the existing Lua for
about a tenth of the cost. If the operator's actual irritation turns out to be latency, this
is an over-correction and §7 says so in detail.

The performance design that matters is **one GraphQL round trip instead of three, a race
between a local `git fetch` and the REST diff, and an on-disk cache** — not the language.
A Go rewrite that keeps octo's sequential fetch pattern would feel *identical*.

Phasing: a genuinely useful **read-only slice in ~3–5 days**; write actions +2–3; the speed
work +2–3; retirement +1. **~9–13 focused days total.** Phase 1 is independently shippable
and Phase 4 is the only irreversible step.

---

## 1. The measurements this proposal stands on

Two sets. The first was measured by the operator; the second I measured today on the
workbench, both to sanity-check the first and to get the numbers the design actually needs.
Everything below is **median of 3–5 runs**, and every probe recorded the exit code and the
stdout byte count so a fast failure could not be read as a fast success.

### 1a. Carried over (operator-measured, sanity-checked and reproduced)

| thing | operator | my re-measure | agree? |
|---|---|---|---|
| `nvim-octo` startup, warm | 0.08s | not re-run | taken as given |
| `nvim-octo` startup, cold | 0.58s | not re-run | taken as given |
| `gh pr view` (PR metadata) | 0.49–0.53s | **0.56s** (min 0.52) | yes |
| `gh pr diff` | 0.78–0.90s | **0.76s** (min 0.75) | yes |
| changed-files list | 0.56s | **0.45s** | yes, slightly faster |

Nothing I measured contradicts the operator's numbers. **The editor is not the bottleneck**
is confirmed by the rest of this table: every single network action costs more than a warm
neovim start.

### 1b. New measurements (mine, today, workbench)

| # | probe | result |
|---|---|---|
| M1 | `gh auth token` (process + config + keyring, **no network**) | **0.07s** |
| M2 | single authenticated HTTPS round trip to `api.github.com` (TLS + request) | **0.30s** |
| M3 | 4 requests on **one reused connection** | 0.80s → **~0.17s per additional request** |
| M4 | `gh api graphql` one-shot: metadata + commits + files + reviews + threads | **0.64s** |
| M5 | `issueOrPullRequest(number:)` — **resolves issue-vs-PR AND returns all panel data** | **0.54s** (small PR) / **0.69s** (23 files) |
| M6 | `GET /pulls/N/files?per_page=100` — per-file `patch` included | **0.66s**, 23/23 files carried a patch, 4,064 patch lines |
| M7 | `gh pr diff` on the largest PR in this repo (`#1630`, 23 files, 4,181 diff lines, 243 KB) | **1.14s** |
| M8 | `git diff` locally, 243 KB / 3,418 diff lines | **0.019s** |
| M9 | `git diff` locally, 5.6 MB / 94,779 diff lines | **0.165s** |
| M10 | `git fetch origin pull/N/head` for an **already-fetched** ref | **0.90s** |
| M11 | `git rev-parse HEAD` (proxy for a static binary's process floor) | **0.004s** |
| M12 | clickable universe with a local clone, by leaf name | 26 / 371 = **7%** |
| M13 | clickable universe with a local clone, by each checkout's **own `origin` remote** | 31 / 341 = **9%** |

M12 and M13 are two different methods reaching the same answer, which is the point of running
both — M12 is blind to a clone whose directory name differs from the repo name, M13 is not.

Everything in 1b is **counts and ratios only**. `known_repos.json` names private
repositories and no repository name from it appears in this document or in any probe output
that was retained.

### 1c. What the measurements mean, stated plainly

1. 🔴 **There is a hard floor of ~0.30s (M2).** No architecture beats the speed of light plus
   a TLS handshake to `api.github.com`. Any claim that the new TUI "opens instantly" over the
   network is false. What is achievable is *one* 0.3–0.7s wait instead of three.

2. 🔴 **Process startup is not a lever worth naming.** M11 says a static binary starts in
   ~4ms against neovim's 80ms warm. That 76ms saving is **a quarter of one network round
   trip**. If this proposal's performance argument rested on "Go is faster than Lua", it
   would be arguing over 76ms while the real cost is 1,770ms. It does not.

3. 🔴 **`gh` process overhead is small but not free (M1 = 0.07s).** Shelling out to `gh` for
   every call costs 70ms per call *and* throws away connection reuse — M2 vs M3 says a warm
   connection is ~0.17s versus ~0.30s cold. An in-process HTTP client is worth roughly
   0.2s per call after the first. Real, but secondary to the round-trip *count*.

4. 🔴 **The local-git win is enormous and it is the biggest single number here: 60× (M7 vs
   M8).** 1.14s → 0.019s on the largest PR in this repo, and M9 shows it stays sub-200ms at
   95,000 diff lines. This is the one place where the new design is not incrementally better
   but categorically better.

5. 🔴 **…and the settled decision's premise needs one correction. See §8.** `git fetch` of a
   PR ref costs **0.90s (M10)**, which is *more* than the REST diff (0.66s, M6). "Local git
   first, API fallback" cannot mean "wait for git" on first open. It must mean **race them**.

6. 🔴 **…and a second correction: the API path is the MAJORITY path, not the fallback.**
   Only **9% of clickable repos have a local clone** (M13). The brief frames the API as the
   fallback "for repos he can click but has not cloned"; by repo count it is the 91% case.
   By *click* frequency it is probably much better — clicks concentrate on the handful of
   repos actually being worked in — but **that is unmeasured and I could not measure it**:
   `~/.config/mention-open/picks.jsonl` does not exist on the workbench, which is itself the
   finding in §8.3.

7. 🔴 **One GraphQL query answers everything, including the issue-vs-PR question (M5).**
   This is the most consequential thing I measured. `issueOrPullRequest(number:)` with an
   inline fragment on each type returns `__typename` *plus* metadata, commits, file list,
   reviews and review threads in **0.54–0.69s**. It makes §4's gap resolution free.

---

## 2. Recommendation, confidence, and the alternatives rejected

### 2.1 The recommendation

Build **`mention-review`** — a single-purpose Go/Bubble Tea TUI for reading, commenting on,
approving and merging one GitHub pull request, spawned by the existing Alacritty hint path.
Retire `nvim-octo` at the end, in its own revertable commit.

### 2.2 Confidence, split by claim

| claim | confidence | why |
|---|---|---|
| The performance analysis in §1 and §3 is correct | **HIGH** | measured here, twice, with controls |
| One GraphQL round trip supplies all four panels | **HIGH** | M5, executed against this repo |
| A lazygit-style diff surface is not reachable by patching octo | **MEDIUM-HIGH** | §7; based on octo's architecture, not on a fork attempt |
| The rewrite is the right call | **MEDIUM-HIGH** | the primary ask is UX and only a rewrite reaches it; the risk is §2.4 |
| The effort estimate (~9–13 days) | **MEDIUM** | estimates of this shape run long; Phase 1 is the confident part |

### 2.3 Alternatives considered and rejected

**(a) Prefetch inside the existing octo Lua, keep neovim.**
Not rejected on the merits — see §7, it is genuinely the cheapest path to most of the
latency win. Rejected as *the* answer because it cannot deliver the thing that was actually
asked for. It remains the correct fallback if the build stalls.

**(b) Use `gh-dash` (an existing Bubble Tea GitHub TUI).**
Rejected: it is a *dashboard* over many PRs — the opposite of this shape, which is spawned
with one `owner/repo` + `N` and must open straight into that PR. Adopting it would mean
fighting its navigation model to reach a single-PR deep link, and its diff surface is not the
lazygit-style commits/files/hunks layout. Worth reading for prior art; not worth adopting.

**(c) Shell out to `lazygit` itself.**
Rejected, and it is worth saying why because the operator named lazygit: lazygit is a
*local-git* tool. It has no PR metadata, no review threads, no approve, no merge — and 91%
of clickable repos (M13) have no local clone for it to operate on at all. It is the right
*reference for the layout*, not a component.

**(d) Delegate diff rendering to `delta` / `difftastic` in a pager.**
Rejected as the primary surface (a pager is not a navigable panel), but **explicitly kept as
the escape hatch for a file too large to viewport** — see §6.4.

**(e) Extend the TUI to issues, notifications, repo browsing.**
Rejected by the operator, and the measurements support him: scope is what makes the one-shot
query in M5 possible. A general GitHub client cannot prefetch, because it does not know what
you are about to look at.

### 2.4 🔴 The risk that should be stated before the work starts

This is a **~10-day build for a single-user tool**, and the operator's own recorded lesson is
that he has retired shipped things that turned out unused. The mitigations are structural,
not optimistic:

- **Phase 1 is independently shippable and independently useful.** If interest evaporates
  after Phase 1, the result is a working read-only PR viewer and octo is still installed.
- **`nvim-octo` stays in the tree until Phase 4.** Rollback through Phases 1–3 is flipping
  one constant.
- **Phase 4 is one commit and `git revert` restores octo.**

If the operator is not confident he will use this daily, the honest move is to do §7's
octo prefetch instead and stop.

---

## 3. Architecture

### 3.1 Shape on screen

Persistent multi-panel layout, lazygit's arrangement: a stacked left column, one large right
panel, and a **generated** key footer. Not a picker, not modals.

```
┌─ 1 Overview ─────────────┬─ 4 Diff ─ scripts/example.py ────────────────┐
│ #1674  Fix the widget    │  @@ -12,7 +12,9 @@ def handle(req):          │
│ author · +312 / -40      │      ctx = build(req)                        │
│ REVIEW: CHANGES          │ -    return ctx.run()                        │
│ MERGE:  CLEAN            │ +    if ctx.stale():                         │
│ CHECKS: 1 FAILING        │ +        ctx.refresh()                       │
│                          │ +    return ctx.run()                        │
├─ 2 Commits ──────────────┤                                              │
│ a1b2c3d fix the widget   │      log.debug("done")                       │
│ e4f5a6b address review   │                                              │
├─ 3 Files ────────────────┤  @@ -40,3 +42,3 @@                           │
│ M scripts/example.py  +9 │ ...                                          │
│ A scripts/new.py     +48 │                                              │
│ D scripts/old.py     -22 │                                              │
└──────────────────────────┴──────────────────────────────────────────────┘
 tab panel · j/k move · ]h next hunk · c comment · a approve · m merge · ? keys
```

🔴 **Every meaning-bearing state is a WORD.** `CHANGES`, `CLEAN`, `1 FAILING`, `M`/`A`/`D`.
Colour is decoration on top of a word that already says it, never the carrier. This is a
stated operator constraint (the red/yellow/green severity circles render as one
indistinguishable glyph in his font) and §5.3 makes it a test, not an intention.

**Why each panel exists** (KISS/YAGNI — each must earn its place):

| panel | justification |
|---|---|
| Diff | the literal ask |
| Files | the literal ask |
| Commits | the literal ask; also the only way to review an iterated/force-pushed PR one step at a time |
| Overview | you cannot decide to *merge* without seeing review state, unresolved threads and check status. Merge is in scope, so this is in scope. |

Four panels. Nothing else. No issue list, no notifications, no repo tree, no settings screen.

### 3.2 Models

Root model `App` owns everything that more than one panel reads:

```go
type App struct {
    repo  string        // "owner/repo", from argv
    num   int           // from argv
    state State         // Loading | Ready | Issue | Failed
    pr    *PullRequest  // immutable snapshot; replaced wholesale, never mutated
    diff  *Diff         // parsed unified diff; nil until it lands
    focus Panel         // which panel has focus

    overview OverviewPanel
    commits  CommitsPanel
    files    FilesPanel
    diffView DiffPanel

    keys KeyMap         // 🔴 the single source of truth for every binding
    help help.Model     // renders the footer FROM keys — see §3.6
    confirm *Confirm    // non-nil iff a destructive verb is pending
}
```

**When to split a model:** a sub-model owns state only *it* reads (its own scroll offset,
its own cursor index). Anything two panels read lives on `App`. Sub-models never hold
pointers to each other; cross-panel effects go through root-level messages:

```
FilesPanel emits fileSelectedMsg{path}
  -> App.Update routes it to diffView.SetFile(path)
```

This is the rule that stops a four-panel Bubble Tea app turning into a graph. It also makes
`Update` testable in isolation, which §5 depends on completely.

### 3.3 🔴 The command seam — the single most important design decision for testability

`Update` must be a **pure function**. It may not perform I/O and it may not construct a
closure that performs I/O, because a `tea.Cmd` is an opaque `func() tea.Msg` and a test
cannot assert anything about one.

So `Update` returns a **tagged intent**, and one thin, separately-tested runner converts
intents to `tea.Cmd`:

```go
// Pure. Fully assertable. This is what 90% of the tests drive.
func (a App) Step(msg tea.Msg) (App, []Intent)

type Intent interface{ intent() }
type FetchPR   struct{ Repo string; Num int }
type FetchDiff struct{ Repo string; Num int; Source DiffSource } // Local | API
type Merge     struct{ Repo string; Num int; Method string }
type Approve   struct{ Repo string; Num int }
// ...

// The only place I/O is constructed. ~50 lines, tested once against a fake client.
func run(i Intent, c *Client) tea.Cmd

func (a App) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
    next, intents := a.Step(msg)
    return next, tea.Batch(mapRun(intents, a.client)...)
}
```

🔴 **This must be in the design from day one.** Retrofitting it means rewriting every
handler. It is what makes assertions like *"pressing `m` produces a pending confirmation and
**zero** network intents"* a real, mechanical test rather than a golden-frame snapshot.

### 3.4 Data flow, prefetch and the local-vs-API race

```
Init()
  └─ tea.Batch(
        FetchPR{repo,num}          ← ONE GraphQL issueOrPullRequest query (M5, 0.54–0.69s)
        ProbeClone{repo}           ← pure local, ~4ms: is there a checkout for this repo?
        LoadCache{repo,num}        ← pure local, ~5ms: a previous snapshot on disk
     )

LoadCache hit   → render IMMEDIATELY from cache, banner "REVALIDATING"     (~5ms to paint)
ProbeClone hit  → tea.Batch(FetchRef{...})   ← git fetch pull/N/head, 0.90s warm (M10)
PRLoaded        → render the three metadata panels; the screen is usable NOW
                → tea.Batch(FetchDiff{source: API})   ← REST files+patch, 0.66s (M6)

        ┌──────────────── the race ────────────────┐
        │ FetchRef done  → LocalDiffReady (0.019s) │  first one to arrive WINS;
        │ FetchDiff done → ApiDiffReady  (0.66s)   │  the loser's result is DISCARDED
        └──────────────────────────────────────────┘
```

🔴 **Why a race and not a preference.** M10 measured `git fetch` at 0.90s — *slower* than the
0.66s REST diff. A naive "local first" implementation would make the common case **worse**.
Racing them gives `min(0.66s, fetch)` on first open, and — the part that matters — once the
ref is local, **every subsequent read in that PR is 0.019s (M8)**: a different commit's diff,
a re-scroll, a whitespace-mode toggle, a word-diff. The local path's value is in the *second
through Nth* read, not the first.

**Local is preferred once both are available**, for three reasons beyond speed: the API omits
`patch` for very large files (M6 saw 23/23 present, but that is one PR, not a guarantee); the
API caps per-file patch size; and local git can produce word-diff, whitespace-ignoring and
arbitrary commit ranges that the API simply does not offer.

**Cache.** `$XDG_CACHE_HOME/mention-review/<owner>/<repo>/<n>.json`, validated against the
PR's `updatedAt`. Render from it instantly, revalidate behind a `REVALIDATING` word in the
Overview panel, swap when the fresh snapshot lands. Bounded (LRU by entry count and total
bytes) so it cannot grow without limit.

🔴 **The cache is mode 0600 inside a 0700 directory, and this is not optional.** It contains
private-repository titles, bodies and review comments. It sits under `$XDG_CACHE_HOME`,
outside every checkout, with exactly the posture `known_repos.json` already has and for
exactly the reason recorded there — an ancestor of that file disclosed 232 private repository
names into this public repo. A cache that ever lands inside a working tree is the same
failure with a different filename.

### 3.5 The API client

One `*Client` built once at startup over a single `http.Client` with HTTP/2 keep-alive, so
every call after the first pays ~0.17s of connection cost instead of ~0.30s (M2 vs M3).
GraphQL for reads (one query, M5); REST for the diff (M6) because GraphQL cannot return patch
text; REST for writes.

### 3.6 🔴 Discoverability: the footer is GENERATED, and cannot go stale

The immediately-preceding arc (`#1653`) existed *only* because 131 keybindings were invisible
and the operator asked "how do I merge?". A hand-written legend is the exact defect that arc
fixed. So:

`bubbles/key` + `bubbles/help` make the generated footer a first-class mechanism. Each
binding carries its own help text, and `help.Model.View(km)` renders the footer from the very
same values `key.Matches` dispatches on:

```go
type KeyMap struct {
    NextPanel key.Binding
    NextHunk  key.Binding
    Merge     key.Binding
    // ...
}

var Keys = KeyMap{
    NextPanel: key.NewBinding(key.WithKeys("tab"),      key.WithHelp("tab", "panel")),
    NextHunk:  key.NewBinding(key.WithKeys("]h"),       key.WithHelp("]h",  "next hunk")),
    Merge:     key.NewBinding(key.WithKeys("m"),        key.WithHelp("m",   "merge (asks first)")),
}

func (k KeyMap) ShortHelp() []key.Binding  { /* the footer row */ }
func (k KeyMap) FullHelp() [][]key.Binding { /* `?` — grouped by panel */ }
```

**There is exactly one place a binding is spelled.** The dispatcher reads `Keys.Merge`; the
footer reads `Keys.Merge`. They cannot disagree because they are the same value — and §5.3
adds a two-way ledger test that fails if a binding is dispatched on but absent from
`FullHelp()`, **or** present in `FullHelp()` and dispatched on nowhere.

Persistent footer (`ShortHelp`) by default, as the operator asked. `?` toggles `FullHelp`
grouped by panel — an expansion of the same generated data, not a separate modal legend with
its own text.

### 3.7 Write actions and confirmation

Carried over from `#1653` unchanged in substance — the operator authored that behaviour twice
and it is not up for revision:

| verb | confirmed? |
|---|---|
| merge | 🔴 yes — prompt names **repo, PR number and merge method** |
| approve | yes |
| request changes | yes |
| submit review | yes |
| delete own comment | yes |
| post a comment | no — additive and trivially reversible |

Merge method defaults to `squash`, matching the current Lua's `default_merge_method` and the
operator's actual merge history. 🔴 **It is read from config, never guessed** — the existing
Lua refuses rather than naming a guessed method in the prompt, and that posture carries over.

---

## 4. 🔴 The issue-vs-PR gap, resolved

### The gap

`mention-open.py` builds `/pull/{id}` for **every** GitHub mention and lets github.com
redirect, because it structurally cannot know whether `#N` is an issue or a PR. Today that is
fine: `Octo <N> <repo>` fires one `issueOrPullRequest` GraphQL query and dispatches on the
`__typename` the *server* returns. If the replacement is PR-only and octo is retired, an
issue click has nowhere to land.

### The resolution: the TUI resolves the kind, and issues get a one-screen card

**M5 is what makes this free.** The exact query the TUI already fires to populate its four
panels is `issueOrPullRequest(number:)` with an inline fragment per type. It returns
`__typename` alongside everything else in **0.54s**. There is no extra round trip, no extra
latency, and no guess anywhere in the chain.

When `__typename == "Issue"`, the TUI renders a **card**, not a PR:

```
┌──────────────────────────────────────────────────────────────┐
│  ISSUE — not a pull request, so there is nothing to review   │
│                                                              │
│  owner/repo#1674   OPEN                                      │
│  Widget refresh drops the stale context                      │
│  opened by <author>                                          │
│                                                              │
│  <issue body, scrollable>                                    │
└──────────────────────────────────────────────────────────────┘
 o open in browser · q close
```

The body is already in the response, so rendering it costs nothing extra.

**The line this draws, explicitly:** the card is read-only and terminal. No comment posting,
no labels, no close/reopen, no threading, no navigation to related issues. If any of those
are ever wanted, that is a new decision with a new justification — not a drift.

### Alternatives rejected

**(a) Detect the issue and `exec xdg-open`, then exit.** Rejected. Alacritty exits 0 whether
its `-e` command exits 0 or 127, so what the operator sees is a window that flashes open and
vanishes with no explanation — the exact silent dead end that `open_tui`'s `which` pre-flight
exists to prevent, reintroduced one layer down. And it throws away data already fetched.

**(b) Have `mention-open.py` resolve the kind before spawning.** 🔴 Rejected hard. That module
carries a deliberate, test-pinned property — *"THE CLICK PATH MAKES NO NETWORK CALL"*, held
by `test_the_resolution_path_spawns_ONLY_these_local_commands`. A GraphQL call there breaks an
architectural invariant *and* adds ~0.5s to every click including the ones that are neither
issue nor PR. The current design is right: resolution is local and kind-blind; the *child*
asks the server. The replacement should inherit that, not undo it.

**(c) Guess from `known_ranges.json`.** It records the highest issue-or-PR number per repo and
by construction cannot distinguish the two. No signal.

**(d) A full issue view.** Rejected as scope — the operator said "we only need pr review and
merge, drop the rest", and the card satisfies the gap with data already on hand.

---

## 5. Testing strategy

TUI tests are notoriously vacuous. The question every test below must survive is
**"would this ever go red on a real defect?"** — and the answer for a golden-frame snapshot
is *no*, so there are none.

### 5.0 🔴 What is rejected, and why

**Golden frames are rejected as the primary mechanism.** `teatest.RequireEqualOutput` with
`-update` asserts "the bytes did not change". It goes red on every cosmetic edit — a padding
tweak, a reworded label — and the person who is annoyed regenerates it with `-update`. Over a
few months it converges on asserting nothing while *reading* as thorough coverage, which is
worse than no test because it stops anyone looking. At most **one** golden frame may exist,
as a smoke check that the renderer produces *something*, and it may never be the evidence for
a behavioural claim.

### 5.1 Layer 1 — pure domain tests (the bulk of the suite)

Ordinary Go table tests over pure functions, with **literal expected values written by hand
from the spec, never derived from the implementation**:

- unified-diff parsing: text → files → hunks → lines, including renames, binary files,
  mode-only changes, "\ No newline at end of file", and an empty diff
- hunk navigation: `]h` / `[h` across file boundaries; first hunk, last hunk, single-hunk file
- the **diff-source decision function** — clone present/absent × ref fetched/not × API
  available/not → which source, as a pure table
- cache validity: `updatedAt` newer / equal / older / absent / malformed
- the confirmation predicate: verb → confirmed or not

### 5.2 Layer 2 — `Step()` driven over message sequences

Because §3.3 made `Step` pure and made intents data, these assert on **state and intents**,
never on a rendered string:

```go
// Pressing merge must ARM a confirmation and must NOT talk to GitHub.
app := ready(fixturePR())
next, intents := app.Step(key("m"))
require.NotNil(t, next.confirm)
require.Equal(t, "merge", next.confirm.Verb)
require.Empty(t, intents)                       // 🔴 zero network intents

// Declining must disarm it and STILL not talk to GitHub.
next2, intents2 := next.Step(key("n"))
require.Nil(t, next2.confirm)
require.Empty(t, intents2)

// Only confirming emits the merge — with the right method.
next3, intents3 := next.Step(key("y"))
require.Equal(t, []Intent{Merge{Repo: "owner/repo", Num: 42, Method: "squash"}}, intents3)
```

🔴 **`require.Empty(intents)` is a silent zero** — it passes identically if `Step` is wired to
nothing. It is only evidence alongside the third assertion, which proves the same fixture on
the same path *can* produce a non-zero intent list. **Report the pair.** Every "zero" assertion
in this suite carries its positive control in the same test.

### 5.3 Layer 3 — seam and ledger guards

These are the tests that pin *relationships*, which is what isolated component tests
structurally cannot see.

**(a) 🔴 Keymap ↔ help, two-way.** Walk `Step`'s dispatch (a declared `[]binding` table, not a
regex over source) and walk `FullHelp()`. Assert the two sets are **equal**. Fails when the
set **grows** (a binding with no help — the exact `#1653` defect) *and* when it **shrinks** (a
help entry for a key that does nothing — a legend that lies). This is what makes "the footer
cannot go stale" a mechanical fact rather than a promise.

**(b) 🔴 Destructive-verb ledger, two-way.** An enumerated `CONFIRMED` set and an explicitly
enumerated `NOT_CONFIRMED` set. Assert their union equals the set of write intents `Step` can
emit. A new write verb that is in neither list **fails the suite** — it cannot be added
silently, in either direction. Mirrors what `CONFIRMED_VERBS` does in the Lua today; the
substance of `#1653` is carried across structurally rather than re-implemented on trust.

**(c) 🔴 Confirmation text, whole normalised string.** The artifact under test is prose, so a
guard on *words* is walkable by rewording. Assert the **entire normalised prompt** against a
fixture:

```
merge owner/repo#42 "Fix the widget" using SQUASH — this cannot be undone. [y/N]
```

A cosmetic reword then fails the test. That cost is paid deliberately, for a machine-readable
claim about what the operator is shown before an irreversible action.

**(d) 🔴 Meaning is never colour-only.** Render every meaning-bearing state with the lipgloss
colour profile forced to **`Ascii`** (no colour at all) and assert the state WORD is present:
`APPROVED`, `CHANGES`, `DRAFT`, `CLEAN`, `BLOCKED`, `CONFLICT`, `N FAILING`, `STALE`,
`OFFLINE`. Goes red the day someone encodes approval as a green dot. This is the operator's
font constraint turned into a gate.

**(e) 🔴 argv contract.** Exit **64** on wrong argument count, **65** on a malformed
`owner/repo`, **66** on a non-numeric or empty number — driven as a table against the *binary*,
including the cases the current `case`-glob rejects: a second slash, a leading or trailing
slash, `..` traversal, and forbidden characters. These are ported directly from
`test_nvim_octo.py`, which already pins them, so the contract survives the implementation
swap rather than being re-derived.

### 5.4 Layer 4 — exactly one `teatest` end-to-end

One test, doing only what the layers above structurally cannot: proving the wiring. Fake HTTP
server → real `tea.Program` under `teatest.NewTestModel` → scripted keys → `WaitFor` a
substring → `FinalModel()` and assert on the **model's state**, not the frame.

Its value is catching "the panels never got wired to the fetched data", which every isolated
test passes. Its cost is being the flakiest test in the suite, so it stays at one.

### 5.5 🔴 Mutation controls — named in advance

The plan above is only credible if the guards have been watched to fail. For each, the
mutation that **must** kill it and the error it must produce:

| guard | mutation | must fail with |
|---|---|---|
| 5.3(a) | add a binding to the dispatch table, omit its `FullHelp` entry | *"binding `X` is dispatched on but absent from FullHelp"* |
| 5.3(a) | delete a binding from the dispatch table, keep its help entry | *"help entry `X` is dispatched on nowhere"* |
| 5.3(b) | add a new write intent, ledger it in neither set | *"write intent `X` is in neither CONFIRMED nor NOT_CONFIRMED"* |
| 5.3(c) | change `SQUASH` to `MERGE` in the prompt | whole-string mismatch, both strings printed |
| 5.3(d) | replace the word `APPROVED` with a green glyph | *"state Approved renders no word under Ascii"* |
| 5.2 | make `m` fire the merge directly with no confirmation | `intents` non-empty where empty was asserted |

🔴 **Each must fail with THIS guard's own error.** A mutation killed by a *different* test's
error is green for the wrong reason and stays green with the guard deleted. And 🔴 **isolate
the mutation** — mutate the narrowest expression that can be wrong, never a guard together
with its enclosing condition.

🔴 **Fixtures must not equal the constants the assertions name.** The PR number fixture is not
`0` or `1`; the merge method fixture is not the string the default would produce if the
config read were deleted. A fixture that can only ever produce the constant's own value
cannot see a mutant that hardcodes the literal.

### 5.6 Where the Go tests run — 🔴 a real migration cost

This repo's gate has **two tiers** (`pytests`, `nodetests`), and Tekton builds them as
`LEG ∈ {pytests, nodetests}`. Go is a **third**, and that is not free: it touches
`flake.nix` checks, `gate.sh`, the Tekton pipeline, `main-green-check.sh` and `drift-check.sh`.

🔴 **And `doCheck` on the packaged derivation is a hazard, not a convenience.**
**Verified in the pinned nixpkgs, not assumed:** `pkgs/build-support/go/module.nix` line 288
reads `doCheck = args.doCheck or true;`, and the check phase immediately below it runs
`buildGoDir test` over every test directory. So on a package in `home.packages` **a failing Go
test fails `home-manager switch`**, which `ship.sh` reports as a *skipped host* — the failure
mode this repo's `CLAUDE.md` documents as silently stopping all future delivery to that
machine. `clawgatectl.nix` is explicitly designed never to fail a switch, and this package must
inherit that posture.

So: **`doCheck = false` on the deploy derivation, and a separate `checks.gotests` leg** that
runs the tests and is read by CI. That mirrors exactly how `pytests`/`nodetests` are separate
from the deploy path. **Budget a day for the third tier and do not discover it in Phase 4.**

⚠ **Side observation, offered as a lead rather than a finding: `clawgatectl.nix` sets no
`doCheck`,** so by the default just verified it runs `go test` over the clawgate module on
every `home-manager switch` on both hosts — and a red test there would fail the switch of a
repo that has nothing to do with it. I have **not** watched that happen and have not tried to
make it happen; it may be that `subPackages` narrows the test set enough that it never bites.
Worth a few minutes from someone who owns that package. It is outside this proposal's scope
and is named only because verifying the default for §5.6 is what surfaced it.

---

## 6. Degraded behaviour — the honest failure story

🔴 **A window that flashes and vanishes is the worst outcome and must never happen.** Alacritty
exits 0 whether its `-e` command exits 0 or 127, so an exit code teaches the operator nothing;
every failure below renders a readable card and waits for `q`.

| condition | behaviour |
|---|---|
| **no network, cache hit** | render from cache with `STALE — snapshot from 4h ago, offline`. Usable for reading. Write keys disabled and the footer says `OFFLINE` where it would say `merge`. |
| **no network, cache miss** | `OFFLINE — could not reach api.github.com` + the underlying error + `o` open in browser · `r` retry · `q` |
| **no token** | `NO TOKEN — run \`gh auth login\``. 🔴 Explicitly distinguished from a 401, because the fixes differ. |
| **401 with a token** | `TOKEN REJECTED — the token exists but GitHub refused it` |
| **404 / no access** | `NOT FOUND — owner/repo#N is not visible to this token` + `o` (a browser session may have access this token does not) |
| **rate limited** | `RATE LIMITED — resets at 14:32` — the reset time in words, from the response header |
| **huge PR** | file list and metadata are always fine. A file whose patch exceeds a threshold renders `TRUNCATED — 12,401 lines · <key> opens it in $PAGER` instead of being pushed through a viewport. |
| **API omits a patch** (too large / binary) | that file's row says `NO PATCH — binary or too large for the API` and, if the local clone has the ref, offers the local diff, which has no such limit |
| **`git fetch` fails** | silent — the API result was racing it anyway and wins by default. Reported only in a `d` debug pane, never as an error card. |
| **malformed argv** | exits 64/65/66 *before* drawing anything, exactly as today |

### 6.4 Large diffs — what actually breaks

M9 says local `git diff` produces 94,779 diff lines in 0.165s, so *producing* a huge diff is
not the problem. The problems are downstream: styling and re-wrapping every line through
lipgloss is per-cell work, and syntax highlighting a 5,000-line file is not free.

The mitigations, in order:

1. **Parse once, style lazily.** Keep the diff as a `[]Line` structure and style only the
   window the viewport is about to show. Never build a styled 5,000-line string and hand it
   to a viewport to slice.
2. **Highlight lazily and per-file**, with a hard cap: above N lines, drop to plain
   add/remove/context styling and say so in words (`NO HIGHLIGHTING — file too large`).
3. **Re-wrap only on resize**, cached by width.
4. **The `$PAGER` escape hatch** for anything past the cap — this is where `delta` earns its
   place (§2.3(d)).

🔴 **This must be measured on a real 5,000-line diff during Phase 1, not assumed.** If the
render budget cannot be met, the viewport strategy is the thing to change, and finding that
out in Phase 3 is too late.

---

## 7. 🔴 Why not just fix octo

The operator may be over-correcting, and the honest answer is: **partly yes.**

### 7.1 What IS reachable in the existing Lua, cheaply

octo's job primitives are async — `nix/pkgs/tools/nvim-octo/default.nix` records why
`plenary-nvim` is a mandatory runtime dependency: *"octo's async/job primitives;
`require("plenary.job")`"*. Nothing prevents a `setup()`-time hook from firing the file list
and the diff **concurrently** with the PR fetch instead of sequentially on demand. The
estimated ceiling for that work, built from the measured per-call costs:

| | today (octo, sequential) | with Lua prefetch |
|---|---|---|
| metadata + files + diff | 0.56 + 0.45 + 0.76 = **1.77s** | **~0.8s** (the slowest of three, in parallel) |

That is **~1.0s saved per PR open, for roughly 1–2 days of Lua** — call it 10–15% of the
rewrite's cost for a large majority of the latency win.

⚠ **The 1.77s is measured; the ~0.8s is an estimate and should be treated as one.** It
assumes three concurrent `gh` invocations do not contend — plausible, since each is a separate
process opening its own connection, but not measured. The *direction* is not in doubt; the
exact figure is. Anyone acting on §7.3's first branch should measure it before committing.

**And octo already has three things the rewrite must re-earn from scratch:**
`<C-b>` open-in-browser on five buffer kinds; the generated per-buffer legend (`?`, `g?`)
shipped in `#1653`; and the merge/approve confirmation ledger. The rewrite pays for those
again. The octo-fix does not.

### 7.2 What is NOT reachable, at any reasonable cost

1. **The lazygit-style persistent commits/files/hunks layout — the actual ask.** octo's review
   surface is buffer-and-window based, driven by octo's own review-session module, in a plugin
   this repo does not own and which nixpkgs pins. Changing its *layout* is not "prefetching in
   the existing Lua" — it is forking octo and carrying the fork. That is not a smaller project
   than the rewrite; it is a similar-sized project with worse ergonomics and a permanent
   upstream-divergence tax.

2. **The 60× local-git diff read (M7 vs M8).** octo fetches diff content through `gh`'s
   GraphQL unconditionally. Teaching it to read from a local clone means reimplementing its
   diff pipeline.

3. **Instant re-open.** octo has no persistent cache across invocations; every window is cold.

### 7.3 The verdict

> **If the complaint is latency, fix octo.** ~1–2 days buys ~1.0s of the ~1.77s. Do that
> instead and stop.
>
> **If the complaint is the diff-reading experience, no amount of Lua prefetching touches
> it,** and the rewrite is the only thing that reaches it.

The operator said both, but he said the diff sentence **first** and it is the more specific
one: *"the commits/files/hunk diff viewing experience is not good. I like lazygit."* That is a
UI complaint, and it is what this proposal is justified by.

🔴 **So this proposal explicitly does not claim the rewrite is justified by speed.** The speed
work is real, it is quantified in §3.4, and it is available either way. The thing only a
rewrite delivers is the layout.

**If the operator reads §7.1 and decides 1.0s was the whole problem — that is the right call
and this proposal should be dropped.**

---

## 8. Findings that qualify or contradict the brief

Four. Two qualify a settled decision's premise; two are new.

### 8.1 🔴 "Local git first (~0ms), API fallback" — the ~0ms is conditional

Accurate once the PR ref is local (M8: 0.019s, 60× faster than the API). **Not** accurate on
first open: `git fetch origin pull/N/head` costs **0.90s (M10)** for an already-fetched ref,
which is *slower* than the 0.66s REST diff. The decision stands; the implementation must be a
**race**, not a preference. §3.4.

### 8.2 🔴 The API path is the majority path, not a fallback

The brief frames the API as covering "repos he can click but has not cloned". Measured, that
is **91% of clickable repos** (M13: 31 of 341 have a local clone; M12 agrees at 7% by a
different method). The consequence is a priority inversion: the API path must be the
*polished* one, and local git is the accelerator for the handful of repos he lives in — not
the other way round.

⚠ **This is a repo-count claim, not a click-count claim.** Clicks almost certainly
concentrate on the ~30 cloned repos, which would make local git the common path *in practice*.
I could not measure it, for the reason in 8.3.

### 8.3 ⚠ The review TUI window may never have opened on the workbench

`~/.config/mention-open/picks.jsonl` **does not exist** on this host, so there is no record of
any mention click. `mention-open.py`'s own source notes the same thing about
`REVIEW_LINES`/`REVIEW_COLUMNS`: *"The rendered grid has never been OBSERVED on the workbench:
that host's picks.jsonl does not exist, so this window has never opened there."*

That does **not** prove the operator has never used the review TUI — he reported exercising it
on a real screen on 2026-09-14 when closing `#1653`, and `picks.jsonl` records *picker* picks,
not every click. 🔴 **An empty result cannot distinguish two mechanisms**, and this one cannot
distinguish "never used" from "used, but never through the path that logs". It is flagged
because it means **the click-weighted version of 8.2 is unmeasurable from here**, and because
a usage baseline before a ~10-day build would be worth having. `record_pick`'s coverage is the
signal that would disagree between the two, and it is the thing to check.

### 8.4 ⚠ "`mention-open.py` needs no change" is true of the CONTRACT, not of the file

The argv contract is preserved **exactly**: two positional arguments `<owner/repo> <number>`,
exit 64 (usage) / 65 (bad repo) / 66 (bad number), cwd-independent, no quoting hazard, and
argv[0] still a constant string literal so the AST ledger in `test_mention_open.py` keeps
reading it.

But the **executable name** must change, and it is spelled at four pinned sites:

| site | change |
|---|---|
| `scripts/mention-open.py` | `REVIEW_EXE = "nvim-octo"` → `"mention-review"` (one line) |
| `flake.nix` | the overlay attribute `nvim-octo` → `mention-review` |
| `nix/programs/alacritty/default.nix` | `pkgs.nvim-octo` → `pkgs.mention-review` in `lib.makeBinPath` |
| `scripts/testlib/launcher_scan.py` | the launcher-name ledger entry |

Four lines plus the test ledgers that pin them two-way (which is the system working — those
ledgers exist precisely so a rename cannot silently unhook the wrapper's PATH from what the
handler spawns).

🔴 **Name it `mention-review`, not `gh-review` or `prreview`.** It matches the existing window
class `REVIEW_CLASS = "float,mention-review"`, and it names **the surface rather than the
implementation** — so the *next* re-implementation, in whatever language, does not force a
fifth rename through the same four pinned sites.

---

## 9. Packaging

Follows `nix/pkgs/tools/clawgatectl.nix` exactly, because its two load-bearing properties are
the right ones here:

### 9.1 🔴 The version is read out of the Go source being compiled

Never a literal. `clawgatectl.nix` exists in its current form because a hand-maintained
`version = "x.y.z"` in one repo was a claim about code in another, and on 2026-08-14 it
stamped `0.7.95` onto a binary built from `0.7.87` source — producing a CLI that printed help
and **exited 0** for a subcommand it did not have.

Here both the source and the Nix expression live in *this* repo, so the drift window is
smaller — but the mechanism is free and the failure mode is identical, so it is kept:

```nix
versionFile    = ./src/cmd/mention-review/version.go;
versionPattern = "var buildVersion = \"([^\"]+)\".*";
# exactly one matching line, or null → available = false
```

🔴 **An unparseable source means NO BINARY, never a fallback literal.** `available = false`,
the package is simply not installed, and the failure is loud at use time
(`mention-review: command not found`) rather than silent at build time. Failing the *switch*
is the worse outcome and is deliberately not what happens — a failed switch is a host
`ship.sh` reports as **skipped**, which this repo documents as silently stopping all future
delivery to that machine.

### 9.2 The overlay, and why the spelling is load-bearing

Same reason `nvim-octo` is an overlay attribute today: the hint wrapper's `lib.makeBinPath`
list is pinned against `mention-open.py`'s syntax tree by
`test_the_alacritty_wrapper_PATH_covers_every_executable_the_handler_spawns`, which matches
`pkgs\.[A-Za-z0-9_-]+` **textually**. A local `let`-bound derivation is not spelled that way,
so the reader would go blind to it and the wrapper would *look* like it pins nothing.

### 9.3 Vendoring

`vendorHash` derived the documented way — build with a deliberately wrong hash and take the
`got:` value. 🔴 Never hand-edited. The dependency set stays small on purpose: bubbletea,
bubbles, lipgloss, a GitHub client, a diff parser, and a highlighter. Every addition is a
vendor-hash re-derivation and a supply-chain entry, in a binary that holds a token with merge
scope.

### 9.4 `doCheck = false` — see §5.6

Non-negotiable on the deploy derivation: a red Go test must not be able to fail a
`home-manager switch`.

---

## 10. Authentication

**Reuse `gh`'s token**, resolved in-process via `cli/go-gh`'s auth helper
(`auth.TokenForHost("github.com")` → `GH_TOKEN`/`GITHUB_TOKEN`, then `gh`'s `hosts.yml`, then
its keyring). Measured cost of that resolution: **0.07s (M1)**, paid once at startup.

**The tradeoff, stated rather than glossed:**

✅ Zero new credential, zero new setup. One place to revoke. It inherits `gh`'s existing
scopes, including the ones merge requires. It is the same dependency octo already has — octo's
`setup()` checks `vim.fn.executable(gh_cmd)` and **refuses to initialise without it**, so this
is not a new coupling, it is the existing one made explicit.

❌ A **hard runtime dependency on `gh` being configured**, and on the internal layout of its
credential storage. If `gh` changes that layout, the TUI breaks at auth time. 🔴 That failure
must surface as the explicit `NO TOKEN` card in §6 — never as an empty PR view, which would
read as "GitHub is down" and send the operator debugging the wrong thing.

❌ A token with merge scope now has a **second reader**. The blast radius is materially
unchanged — the token was already on disk and already readable by anything running as this
user — but it is one more binary, and that is worth saying out loud rather than waving away.

**Rejected: shelling out to `gh api` per call.** Costs 0.07s per call (M1) and forfeits
connection reuse (~0.17s vs ~0.30s per request, M2/M3). More importantly it would mean two
auth paths with two failure modes. One in-process client, one failure mode.

🔴 **The token is never logged, never written to the cache, and never included in any error
card.** The `NO TOKEN` / `TOKEN REJECTED` cards say which condition holds and nothing more.

---

## 11. Phasing and effort

Honest framing: **this is a large build.** The estimates assume focused days.

### Phase 0 — spike · **0.5–1 day**
Prove in Go what M5 proved through `gh`: one `issueOrPullRequest` query returns everything the
four panels need, and `go-gh` resolves auth on this host.
🔴 **Kill criterion:** if the common case needs more than one round trip, the performance
argument weakens materially and the whole proposal should be re-read before continuing.

### Phase 1 — first shippable slice · **3–5 days**
Read-only, and genuinely usable.
argv contract + exit codes 64/65/66 · one GraphQL fetch · four panels with focus cycling ·
generated help footer (§3.6) · unified diff from the API only · the issue card (§4) ·
gruvbox matching the Alacritty palette · `o` browser, `q` quit · nix packaging (§9) ·
test layers 1–3 (§5).
Explicitly **not** in Phase 1: local git, caching, write actions, syntax highlighting.
🔴 **Measure a 5,000-line diff here (§6.4).** It is a Phase 1 finding, not a Phase 3 one.

### Phase 2 — write actions · **2–3 days**
Comment · approve · request changes · submit review · merge. All behind the confirmation
ledger of §3.7 and the two-way guards of §5.3(b)/(c). This is where those tests earn their
cost.

### Phase 3 — the speed work · **2–3 days**
Local-clone probe · PR-ref fetch racing the API (§3.4) · the bounded on-disk cache (0600) ·
per-commit diff · lazy syntax highlighting with the size cap.
Deliberately **last**: Phase 1 already matches octo on latency; this is what makes it
*better*, and it is the part that is easiest to get wrong.

### Phase 4 — retirement · **~1 day, its own PR**
Delete `nix/pkgs/tools/nvim-octo/` (3 files), `scripts/tests/test_nvim_octo.py`
(**67 tests**), `scripts/devhost-tests/test_nvim_octo_diff_motions.py` (**8 tests**), the
`nvimOctoOverlay` in `flake.nix`, the `pkgs.nvim-octo` entries, and the `luajit` entry in
`run-tests.sh`'s `REQUIRED_TOOLS` — which exists *only* to execute `octo-init.lua`.
Re-derive the two affected `TARGET_FLOORS` numbers by **running the gate and copying what it
prints**, never by arithmetic on the two sides.

### Rollback

- Through Phases 1–3: `nvim-octo` is still installed. Flip `REVIEW_EXE` back — one line.
- After Phase 4: `git revert` the single retirement commit.
- 🔴 Ship Phase 4 **only after the operator has used the new TUI for a real review** and said
  so. Nothing headless can close that condition, and no test can substitute for it.

### Total

**~9–13 focused days**, plus the third-tier gate work (§5.6) which is easy to under-budget.

---

## 12. Open questions for the operator

1. **§7 is the one that matters.** If ~1.0s off the current 1.77s is the whole problem, the
   octo prefetch is 10–15% of the cost. Is the diff *layout* genuinely the irritation?
2. **Side-by-side or unified diff?** Unified is proposed (lazygit's default, and it survives a
   narrow panel). Side-by-side is a Phase 3+ addition if wanted.
3. **Syntax highlighting — worth it?** It costs a dependency and a per-file budget. Plain
   add/remove/context styling may be enough, and it is what lazygit shows by default.
4. **§8.3 — has the review TUI actually been used?** Worth knowing before a ~10-day build.
5. **Comment posting: inline on a diff line, or PR-level only?** Inline is materially more
   work (review-thread positioning against the diff) and may not be wanted at all.
