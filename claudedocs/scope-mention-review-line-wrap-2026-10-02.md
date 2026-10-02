# SCOPE — long diff lines and long file paths in the `mention-review` TUI

**Status: SCOPE ONLY.** No production code written, no PR opened, no clawgate/muster task
created, nothing deployed, no file in any repository modified. Everything below is a
proposal plus the measurements that justify it. Every measurement was taken against a
**copy** of the package in the session scratchpad, which was deleted afterwards; the
probe sources are preserved (Appendix A).

- **Date:** 2026-10-02
- **Source read:** `nix/pkgs/tools/mention-review/src` in `devrc` (`/home/zach/workspace/devrc`), at the current working-tree state of `main`. Go, Bubble Tea v2.
- **Dependency read:** `charm.land/bubbles/v2 v2.2.1` (`viewport`), `charm.land/bubbletea/v2 v2.0.9`, `charm.land/lipgloss/v2 v2.0.6` — pinned in `src/go.mod`.
- **Trigger, in the operator's words:** *"it doesn't handle long paths and file names, needs some way to toggle line-wrap (or recommend a better solution)"*

---

## 0. TL;DR — what changed in my understanding of the problem

Six findings reframe the brief. Each is sourced and measured below.

1. 🔴 **The headline defect is NOT the diff pane — it is the pane TITLE, and it breaks the
   whole frame.** `box()` (`internal/ui/panels.go:243-245`) joins the panel title to
   `a.currentFilePath()` with **no truncation of any kind**. The head then wraps inside
   `sty.Width(inner)`, the box grows taller than its `Height()`, and the rendered frame
   exceeds the terminal. MEASURED in the real renderer: at 100×30 a **56-character** path
   costs 1 row and a 64-character path costs 2; at 140×40 a 104-character path costs 2. The
   control — the same fixture with its stock 14-character path — fits exactly. **8.0% of
   4,223 real changed-file paths exceed that 56-character threshold.** This is a layout
   break, not a cosmetic truncation, and it is almost certainly the "long paths and file
   names" the operator is describing.

2. 🔴 **"There is no horizontal offset state anywhere" is TRUE OF THE APP AND FALSE ABOUT
   THE CAPABILITY — the viewport already has a complete pan API.** `bubbles/v2@v2.2.1`
   carries `xOffset`, `XOffset()`, `SetXOffset()`, `ScrollLeft()`, `ScrollRight()`,
   `SetHorizontalStep()` (default step **6**), `maxXOffset()`, and an ANSI-correct
   `ansi.Cut` slice in `visibleLines()`. The brief's grep was over `src/**.go` only, so it
   measured the app, not the trade. Panning is **wiring, not new state** — with one sharp
   exception, finding 3.

3. 🔴 **A naive pan is the "looks implemented, does nothing" defect, and `scrollDiff`'s own
   header already describes it.** `syncDiffViewport` ends in `EnsureVisible(diffCur, 0, 0)`
   (`panels.go:496`), and `EnsureVisible`'s first act is
   `if colend <= maxWidth { SetXOffset(0) }` — with `colend == 0` that is unconditional.
   MEASURED at two widths: `ScrollRight(60)` → `XOffset()==60`; **one `j` press →
   `XOffset()==0`**, and `]` likewise. So the pan survives until the next cursor key. The
   fix is one save/restore line, and it is the mutation a test must kill.

4. 🔴 **Wrap breaks hunk navigation, measured, and the mechanism is NOT the one the brief
   names.** `StyleLineFunc` is indexed by the **logical** line in both modes (`styleLines`
   is called with `ridx`, `viewport.go:343,373` — verified: indices `{0,1,2,3}` while line 0
   wrapped to 3 rows), so the cursor *styling* is safe. The break is a **unit mismatch in
   `EnsureVisible`/`YOffset`/`TotalLineCount`**, which become *rendered-row* coordinates
   under `SoftWrap` while `diffCur` stays logical. MEASURED on a 121-line, 30-hunk diff
   driven through the real `Step`: `SoftWrap=false` → cursor on screen **25/25** `]` presses
   at both widths; `SoftWrap=true` → **20/25 OFF SCREEN** at pane 62 and **16/25** at pane
   102. The library exposes no logical→rendered conversion (`calculateLine` is unexported),
   so a correct wrap mode must recompute it in the app — an O(n) pass per jump, which is
   exactly the cost `SoftWrap = false` was chosen to avoid.

5. 🔴 **The library makes wrap and pan mutually exclusive, so this is a MODE, not two
   flags.** `SetXOffset` is documented and implemented as a **no-op when `SoftWrap` is
   enabled** (`viewport.go:550-557`). Verified: `ScrollRight(6)` under `SoftWrap=true`
   leaves `XOffset()==0`. Any design that offers both at once is offering something the
   dependency refuses to do.

6. 🔴 **On the evidence I recommend NOT building the wrap toggle, and building a THIRD thing
   the brief did not consider: a word on the clipped row.** Today a too-long diff line is
   cut by `ansi.Cut(line, 0, maxWidth)` with **no ellipsis and no marker at any width**
   (verified at 20/40/62/92/140/200 cells) — so the operator cannot tell a clipped line from
   a short one. Panning with a half-pane step reaches **95.63%** of over-wide lines in two
   presses; the band where panning is genuinely awkward (beyond 10× the pane) is **0.208%**
   of over-wide lines, and **wrap does not help there either** — a 1,020-cell line wraps to
   ten rows and pushes the hunk out of view. For that tail the right answer is the `o`
   binding that already exists, and the reason nobody uses it is that nothing on screen says
   the line was cut.

---

## 1. Verification of the measurements I was handed

Instructed to verify, not re-derive. Four confirmed, one materially wrong, one re-framed.

### 1.1 Confirmed

| Claim | Result |
|---|---|
| `panels.go:615` `truncate(s, w)` right-trims and appends `…`; `truncateLeft` keeps the tail | **CONFIRMED** (`panels.go:615-627`, `635-647`). |
| `truncateLeft` has **one** call site, `panels.go:426` | **CONFIRMED.** `find … -print0 \| xargs -0 grep -n truncateLeft` over non-test files returns the definition (`:635`), its doc comment (`:629`) and exactly one call (`:426`, the directory-row arm of `renderFileRow`). |
| Diff lines are built at `panels.go:475-476` with no width handling | **CONFIRMED.** `rebuildDiffContent` does `lines[i] = plainDiffLine(ln)` and `SetContentLines`; the row is emitted whole and the viewport clips it. |
| `Diff.Truncated`, `Snap.CommitsTruncated`, `Snap.FilesTruncated` are **fetch-time**, a different axis from rendering | **CONFIRMED, and worth keeping separate in the code as well as in prose.** They are set in `internal/ghapi/query.go:848,858` from GraphQL page info (`TotalCount > len(Nodes)`, `PageInfo.HasNextPage`) and in `internal/ui/run.go:151`. They answer *"GitHub did not tell us about every file/commit"*. Nothing in this scope sets or reads them, and **no new flag should be named `*Truncated`** — a render-clip flag sharing that vocabulary is how the two axes get conflated by the next reader. §4 names the render-side state `clipped` / `panned` instead. |
| There is no wrap implementation in the app (no `Wrap` match in app source) | **CONFIRMED** for the *diff* pane. ⚠ Two nuances the brief's phrasing hides: `a.body.SoftWrap = true` is already set for the issue/error card (`app.go:227`), so the program does wrap — just not the diff; and `scroll_test.go:139-155` already contains `SoftWrap = true` **benchmarks**, so the mode is reachable from the suite today. |

### 1.2 The one I found to be WRONG

> "**There is no horizontal offset state anywhere.** `grep -rn "XOffset|xOff|HOffset|hscroll|ColOffset|colOff" --include=*.go` returns nothing. Panning would be new state."

The grep is reproducible and its conclusion about the app is right — I re-ran it through
`find … -print0 | xargs -0 grep` to be free of the `.gitignore`-blind wrapper, with a
positive control (`YOffset` → 3 files, 64 hits) to prove the pattern can match. **Zero hits
for the X-axis patterns in the app.**

But the claim *"panning would be new state"* does not follow, because the state already
exists one layer down. From `charm.land/bubbles/v2@v2.2.1/viewport/viewport.go`:

| capability | location |
|---|---|
| `xOffset int` — the horizontal scroll position | `:74-75` |
| `XOffset() int` / `SetXOffset(n int)` | `:547-557` |
| `ScrollLeft(n)` / `ScrollRight(n)` | `:559-567` |
| `SetHorizontalStep(n)`, `defaultHorizontalStep = 6` | `:16`, `:540-545` |
| `maxXOffset() = longestLineWidth - Width()` | `:308-312` |
| the slice itself — `ansi.Cut(lines[i], m.xOffset, m.xOffset+maxWidth)` | `:360-364` |
| `EnsureVisible(line, colstart, colend)` already takes **columns** | `:471-480` |
| `LeftGutterFunc` + `GutterContext{Index, TotalLines, Soft}` | `:110`, `:405-443` |

⚠ **The viewport's own `Update` is never called in this program** — `find … | xargs -0 grep
'\.Update('` over all app sources returns nothing, and `Step` is pure by design
(`app.go:243-247`). So the viewport's built-in `left`/`right` pan keys (`viewport.go:690-693`)
are **inert here**. That is a feature, not an obstacle: it means the pan must be routed
through `Dispatch()`, which is what makes `keys_test.go`'s two-way ledger demand help text
for it automatically.

**Consequence:** finding 2 of the TL;DR. The cost of a pan is three method calls plus the
`EnsureVisible` fix (finding 3) — not a new state machine. This *strengthens* the
coordinator's recommendation 1 rather than weakening it, and it changes the effort estimate
materially.

### 1.3 Re-framed: `TestALongTitleIsTruncatedAndTheTailSurvives`

Read at `internal/ui/confirm_test.go:166-184`. **It would survive every change proposed here,
untouched, and it is not the guard it looks like from the brief.**

- It exercises `ConfirmPrompt`, not any panel. The cap it tests is `maxPromptTitle = 60`
  (`internal/ui/confirm.go:35`), a **fixed rune budget applied to the PR title** inside the
  confirmation sentence. It never reads a pane width, a path, or a diff line.
- Its three assertions are `strings.Contains(got, "…")`, a `HasSuffix` on the whole tail
  clause, and `len([]rune(got)) <= 200`. Nothing in §4 touches `ConfirmPrompt`,
  `maxPromptTitle`, or `truncate`'s behaviour — so the test neither breaks nor needs
  re-deriving. I ran it in the scratchpad copy: **PASS**.
- ⚠ It is nonetheless **itself width-blind, and its own comment says so while the code does
  not**: the comment at `confirm.go:30-32` justifies the cap by *"an 80-column pane"*, but
  the constant is a rune count with no pane width in sight. A 60-rune title plus the
  surrounding clause renders past 80 columns on a narrow terminal. **Out of scope, flagged,
  not fixed** — it is a confirmation-bar concern, not a diff/path one, and bundling it would
  blur a PR that should be legible.

---

## 2. Measurements taken for this scope

All read-only with respect to every repository. Three instruments, each with its controls.

### 2.1 Instrument validation, first

Per the house rule, each instrument's verdict is a claim about the instrument until two
controls have been watched to work.

| instrument | negative control (can it go red?) | positive control (can it observe the thing?) |
|---|---|---|
| **`vpprobe`** — a scratchpad Go module importing the pinned `viewport`/`lipgloss` | the gutter-shortfall arm reports `shortfall=0, lastcell_reachable=true` with no gutter and `shortfall=2/4, reachable=false` with one — it distinguishes states rather than printing one | the clip arm reports `rendered == pane width` for a 301-cell line at six widths, and `rendered == source length` is never printed, i.e. the measurement moves with the input |
| **the package's own test harness**, run on a scratchpad copy | `TestProbeFrameFitsWithALongPath` **FAILS** (long path) where `TestControlUnmodifiedFixtureFrameFits` **passes** (stock path) — same assertion, one fixture dimension changed | `TestALongTitleIsTruncatedAndTheTailSurvives` and `TestTheRenderedFrameFitsTheTerminalInEveryMode` both **PASS** in the copy, so the copy is faithful and the harness is wired to real code |
| **the prevalence scan** (`prevalence.py`, `reach.py`) | the two panes disagree — 24.16% vs 2.29% over-wide — so the predicate is not a constant | 617,544 lines and 4,223 paths sampled and reported beside every percentage; a zero is never quoted alone |

⚠ **The instrument I did NOT use: a live `tmux` run of the binary.** See §6 and §9.1 — the
one claim it would add is what a real terminal does with an over-tall frame, and that is a
closing-condition step, not a conclusion here.

### 2.2 How long are real diff lines and real paths?

Population: **40 distinct local repositories** (de-duplicated by `origin` URL hash), the
newest 40 merge-free commits of each = **780 commits**, `git show --unified=3`.
**617,544** diff content lines (`+`/`-`/context, excluding `+++`/`---`) and **4,223**
changed-file paths. Lengths are measured on the rendered row, i.e. including the
`+`/`-`/space marker `plainDiffLine` prepends (`panels.go:506-516`).

The two pane widths are the real ones, derived from `relayout()` (`panels.go:595-600`,
`a.vp.SetWidth(max(10, a.Width-leftW-4))`) at the two terminal sizes
`layout_test.go:24-27` already pins:

| terminal | `leftColWidth` arm | diff pane | source |
|---|---|---|---|
| 100×30 (`New()`'s default) | 34 | **62** cells | `app.go:207-208`, `panels.go:595-599` |
| 140×40 (`ready(t)`'s size) | 34 | **102** cells | `app_test.go:119` |

**Diff line length, cells:**

| p50 | p75 | p90 | p95 | p99 | p99.9 | max | mean |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 31 | 61 | 82 | 92 | 125 | 441 | 405,662 | 42.8 |

**How often is a line wider than the pane:**

| terminal | pane | over-wide lines | share |
|---|---:|---:|---:|
| 100×30 | 62 | 149,213 / 617,544 | **24.16%** |
| 140×40 | 102 | 14,172 / 617,544 | **2.29%** |

**Commits containing at least one over-wide line, at pane 62: 718 / 780 = 92.1%.**
So at the default terminal size this is not an edge case; it is almost every review.

⚠ **The two numbers differ by 10×, which is the whole reason a one-width measurement would
have been worthless here.** Quoting 2.29% would make the problem look marginal; quoting
24.16% would make it look universal. Both are true, of different terminals.

**Changed-file path length, characters:**

| p50 | p75 | p90 | p95 | p99 | max |
|---:|---:|---:|---:|---:|---:|
| 32 | 46 | 54 | 61 | 78 | 104 |

**Basename length** (what a *file* row in the tree actually shows): p50 **13**, p90 **26**,
p99 **40**, max **60**.

### 2.3 Can panning reach them? (`reach.py`)

A half-pane pan step is the usual TUI choice; `diffScrollStep = 3` lines is this program's
precedent for "a step, not a page" (`app.go:593-600`).

| terminal / pane | 1 press | 2 presses | 4 presses | 8 presses |
|---|---:|---:|---:|---:|
| 100×30, pane 62, step 31 | **81.89%** | **95.63%** | 98.50% | 99.34% |
| 140×40, pane 102, step 51 | 76.83% | 86.70% | 92.81% | 96.34% |

(percentages are of the *over-wide* lines, not of all lines)

**The tail, where panning stops being pleasant:**

| band | pane 62 | pane 102 |
|---|---:|---:|
| beyond 2× the pane | 4.374% of over-wide / **1.0569%** of all | 13.301% / 0.3052% |
| beyond 10× the pane | 0.208% of over-wide / **0.0502%** of all | 1.376% / 0.0316% |
| beyond 100× the pane | 0.020% / 0.0049% | 0.162% / 0.0037% |

🔴 **This is the argument against the wrap toggle, and it is quantitative.** Panning handles
95.63% of the problem in two keystrokes at the default size. The residue — one line in two
thousand — is where wrap is supposed to earn its place, and wrap fails there too: at pane
62, a 620-cell line becomes **10 rendered rows**, a 6,200-cell line becomes **100**, and the
hunk header the operator was reading leaves the screen. A mode that is unnecessary for 95%
of cases and insufficient for the rest is not a mode worth its defects.

### 2.4 The silent clip, at six widths

`visibleLines()` short-circuits only when nothing is too long
(`viewport.go:352: if (m.xOffset == 0 && m.longestLineWidth <= maxWidth) …`); otherwise every
visible row goes through `ansi.Cut`. Measured on a 301-cell line:

| pane width | 20 | 40 | 62 | 92 | 140 | 200 |
|---|---:|---:|---:|---:|---:|---:|
| rendered cells | 20 | 40 | 62 | 92 | 140 | 200 |
| `…` present | no | no | no | no | no | no |
| any cut marker | no | no | no | no | no | no |

Reproduced through the **real renderer** as well (`TestProbeALongDiffLineSaysNothing`): a
410-cell source line at 140×40 and 100×30, `any_cut_marker_on_the_clipped_row=false` both
times. The `…` that does appear in the frame is the *PR title* in the Overview panel, which
is why the probe asserts on the clipped row specifically rather than on the frame.

🔴 **So the operator's complaint has a half nobody has named: the tool does not merely cut
long lines, it cuts them silently.** `truncate()` appends `…` precisely so "a shortened
title cannot be mistaken for the whole one" (`confirm.go:33-34`). The diff pane — the one
pane whose content is being reviewed for correctness — makes no such promise.

### 2.5 The pane-title overflow, in the real renderer, with a control

Same assertion as `layout_test.go`'s `TestTheRenderedFrameFitsTheTerminalInEveryMode`
(count the frame's lines, compare to the terminal's height), with **one fixture dimension
changed**: the file path. Everything else — snapshot, diff, mode, merge method — is
`ready(t)`'s.

**Control, the unmodified fixture (longest path 14 characters):**

| terminal | frame rows | budget | verdict |
|---|---:|---:|---|
| 140×40 | 40 | 40 | fits — **exactly at budget** |
| 100×30 | 30 | 30 | fits — **exactly at budget** |
| 80×24 | 24 | 24 | fits — **exactly at budget** |
| 62×20 | **22** | 20 | 🔴 overflows by 2 — **pre-existing, see below** |

**With the path lengthened, nothing else:**

| path chars | 140×40 | 100×30 |
|---:|---:|---:|
| 14 (control) | 40 ✓ | 30 ✓ |
| 40 | 40 ✓ | 30 ✓ |
| 56 | 40 ✓ | **31 ✗ (+1)** |
| 64 | 40 ✓ | **32 ✗ (+2)** |
| 80 | 40 ✓ | **32 ✗ (+2)** |
| 104 | **42 ✗ (+2)** | **32 ✗ (+2)** |

The thresholds match the arithmetic exactly: the head's available width is
`inner - len(" > DIFF ")` = `(a.Width - leftW - 2) - 8` = **56** at 100×30 and **96** at
140×40. Crossing it wraps the head, the box outgrows its `Height(max(1, h-2))`, and the
frame exceeds the terminal — *"the bottom rows are pushed off screen"*, in the existing
guard's own words (`layout_test.go:52-54`).

🔴 **Why it overflows at all is the sharpest part: the layout has ZERO vertical headroom.**
The control sits at 40/40, 30/30 and 24/24 — not under budget, *at* it. So the pane head is
the one unbounded-width element in a frame with no slack, and a single wrapped row is
sufficient. Cross-referenced against §2.2: **8.0%** of the 4,223 real paths exceed 56
characters (p95 is 61), and **0.1%** exceed 96.

⚠ **Two things I must not over-claim.**
- The **62×20 row is confounded** and I have excluded it from the attribution. The control
  overflows there too, by 2 rows, with a 14-character path — a *pre-existing* narrow-terminal
  defect in the left column (the Overview's `BaseRef + " <- " + HeadRef` row is 25 cells
  against an 18-cell inner width and wraps). `minWidth = 60` admits that size and
  `layout_test.go` does not test it. **Separate finding, separate fix, not mine to bundle**
  — §9.3.
- The frame is also **146 cells wide at every size measured**, over a 140- and 100-cell
  budget. That is **not a new finding**: `keys.go:319-326` measured and documented it
  already (*"the browse row already renders 146 columns wide … it OVERFLOWS even a
  140-column terminal before anything is added"*). It is noted here only because it is a
  **blind spot of the row-count guard** — §8.1.

### 2.6 Wrap, measured through the real `Step`

Fixture: one file, 30 hunks, each carrying a 240-cell added line; 121 logical lines. Driven
by 25 real `]` presses through `pressAll`, then asked whether the row `diffCur` now points
at is actually rendered in `a.vp.View()`.

| `SoftWrap` | terminal | pane | logical lines | rendered rows | `]` presses | cursor OFF SCREEN |
|---|---|---:|---:|---:|---:|---:|
| false | 100×30 | 62 | 121 | 121 | 25 | **0** |
| false | 140×40 | 102 | 121 | 121 | 25 | **0** |
| true | 100×30 | 62 | 121 | **211** | 25 | **20** |
| true | 140×40 | 102 | 121 | **181** | 25 | **16** |

🔴 **80% of `]` presses at the default terminal size land the cursor somewhere the operator
cannot see.** And the rate is **width-dependent** (80% → 64%), so a suite that pinned one
width would under-report it by a quarter. The brief predicted this defect; the measurement
confirms it and corrects its mechanism (TL;DR 4).

### 2.7 Wrap's CPU cost, re-measured at two widths

20 `View()` calls per cell, after a warm call, `StyleLineFunc` installed (as the app does):

| pane | lines | `SoftWrap=false` | `SoftWrap=true` | ratio |
|---:|---:|---:|---:|---:|
| 62 | 1,000 | 241 µs | 1,281 µs | 5.3× |
| 62 | 4,000 | 167 µs | 3,674 µs | 22.0× |
| 62 | 10,000 | 200 µs | 9,670 µs | **48.2×** |
| 92 | 1,000 | 197 µs | 2,800 µs | 14.2× |
| 92 | 4,000 | 225 µs | 4,602 µs | 20.5× |
| 92 | 10,000 | 481 µs | 9,053 µs | 18.8× |

⚠ **This CONFIRMS the shape of `app.go:213-226`'s table and DISAGREES with its magnitude by
~2.4×** (that table reports 21,554 µs at 10,000 lines and width 92; I get 9,053 µs). Both
can be right: the absolute number depends on how many lines actually wrap, which depends on
the fixture's line lengths — and the box was carrying other work during my run, visible in
the `false` column jittering 167–481 µs where it should be flat. **The direction is not in
doubt and is the only part I rely on:** `false` is flat in buffer size, `true` is linear;
at 10,000 lines one `View()` costs 9–21 ms against a 16.7 ms 60 fps frame budget. I do **not**
quote a ratio as a property of the program — only of these two runs at these two widths.

### 2.8 Three library facts a design must respect

1. **Wrap and pan are mutually exclusive by construction.** `SetXOffset` is a documented
   no-op under `SoftWrap` (`viewport.go:550-557`). Verified: `ScrollRight(6)` with
   `SoftWrap=true` leaves `XOffset()==0`.
2. **`LeftGutterFunc` already marks soft continuations** — `GutterContext.Soft` is `idx > 0`
   inside the wrap loop (`viewport.go:426-437`). Verified: a 4-row wrapped line rendered as
   `"  +abc…"`, `"│ hij…"`, `"│ fgh…"`, `"│ def…"`. **So "mark continuations visibly" needs
   zero new rendering code** — if wrap is ever built.
3. 🔴 **But a gutter makes the end of the longest line unreachable by panning.**
   `maxXOffset()` is `longestLineWidth - Width()` (`:310-312`) while the usable area is
   `maxWidth() = Width() - frame - gutter` (`:316-322`). Measured on a 301-cell line:

   | pane | gutter | `maxXOffset` | needed | shortfall | last cell reachable |
   |---:|---:|---:|---:|---:|---|
   | 62 | 0 | 239 | 239 | 0 | yes |
   | 62 | 2 | 239 | 241 | **2** | **no** |
   | 62 | 4 | 239 | 243 | **4** | **no** |
   | 92 | 0 | 209 | 209 | 0 | yes |
   | 92 | 2 | 209 | 211 | **2** | **no** |
   | 92 | 4 | 209 | 213 | **4** | **no** |

   An upstream off-by-gutter. It is why §4's cut indicator goes in the **pane title**, not in
   a left gutter — the cheaper choice happens to also be the correct one.
4. ⚠ **`longestLineWidth` is buffer-global** (`SetContentLines`, `:256`). The pan range is
   governed by the single longest line in the *entire* diff, so panning while on a
   short-line file scrolls into empty space. Acceptable (it is what `less -S` does), but it
   must be stated in the indicator's design so a blank pane does not read as a bug.

---

## 3. Three problems, three fixes — and one recommendation to decline

The ask bundles three defects. They share a sentence and nothing else.

| # | the defect | the axis | the fix |
|---|---|---|---|
| **A** | a too-long diff line is clipped **silently** | render, per row | a cut indicator in the pane title (W1) |
| **B** | a clipped line's tail is **unreachable** | render, per row | horizontal pan (W2) |
| **C** | a long **path** in the pane title breaks the frame | layout, per frame | middle-ellipsis in the head (W3) |

And the one the operator asked for by name:

| **D** | wrap as a secondary mode, default off | render mode | **recommend DECLINE** (§3.1) |

Problem **C** is not a wrap problem and not a diff problem — it is a layout break with a
measured threshold and a two-line fix. It is also the only one of the four that loses the
operator information they are not even looking for (the footer legend, the whole point of
the preceding arc per `keys.go:5-10`). **It should ship first.**

### 3.1 Why I recommend declining the wrap toggle

The brief invited an argument, and the evidence makes a stronger one than the brief's own
hedge. Collected:

| against | measurement |
|---|---|
| it is **unnecessary** for the overwhelming majority | pan reaches 95.63% of over-wide lines in 2 presses at the default size (§2.3) |
| it is **insufficient** for the residue it exists to serve | beyond 10× the pane — 0.208% of over-wide lines — wrap yields 10–100 rendered rows per logical line and pushes the hunk off screen |
| it **breaks hunk navigation today** | 20/25 `]` presses land off screen at pane 62 (§2.6) |
| fixing that needs an **O(n) pass per jump**, in the app | `calculateLine` is unexported; `EnsureVisible`/`YOffset`/`TotalLineCount` are rendered-row under wrap (§2.8) — the app would have to re-derive `Σ ceil(width(line)/maxWidth)` over `[0, cur)` |
| it **re-introduces the cost** the architecture was chosen to avoid | 19–48× per `View()` at 10,000 lines (§2.7), against a decision documented as *"MEASURED, NOT A STYLE ONE"* (`app.go:213`) |
| it **cannot coexist** with the pan that does work | `SetXOffset` is a no-op under `SoftWrap` (§2.8) |
| the hard cases already have an answer | `o` opens the file in the browser — bound, zero cost (`keys.go:159`). **Nothing tells the operator the line was cut, which is why they have not reached for it.** W1 fixes that for the price of a title string. |

⚠ **The honest case FOR it**, which belongs in the operator's hands and not mine: wrap is
genuinely the right tool for a **prose or long-string** line in the 125–440 cell band (p99
to p99.9) where you want to *read* the content rather than *locate* a change in it — a
changed Markdown paragraph, a long test assertion, a multi-kilobyte JSON literal. I have
not measured how much of the operator's review traffic is that shape, only how long the
lines are, and line length does not distinguish "minified blob" from "English paragraph".
**That is the measurement that would change my recommendation, and it is listed as an open
question (§7.2), not asserted away.**

🔴 **And if wrap is built anyway, it must be built as a MODE with the logical→rendered
conversion, never as `a.vp.SoftWrap = true`.** The one-line version is the whole defect:
every test in the package would stay green (the suite's `YOffset` literals are pinned under
`SoftWrap=false`, `movement_test.go:213` says so outright) while `]`, `[`, `}`, `{`, `g`,
`G`, `j` and `k` all stopped landing on screen. §8.4 specifies the guard.

---

## 4. Work items

Dependencies: **W1 → W2** (W1's indicator reads the pan offset, so W2 completes its text,
but W1 ships useful alone). **W3 → none.** **W4 → W1, W2, W3** (it is the harness).
**W5 → none.** W3 is independently revertible of everything.

### W1 — say that a line was cut, in the pane title

**Files:** `internal/ui/panels.go` (`box`, and a new pure helper) · **Keys:** none ·
**Deps:** none

Today a clipped row is indistinguishable from a complete one at every width (§2.4). Add a
**per-pane** indicator to the Diff panel's head, next to the path:

```
 > DIFF  …/internal/ui/panels.go          COL 1-62 OF 410
```

Shown only when the widest row **currently visible** exceeds the pane, or when the pan
offset is non-zero. In words, never a colour, per the operator's font constraint
(`panels.go:419-421`, `app.go` passim).

**Why the title and not a per-row marker — the measured reason:**
- A marker baked into the buffer by `rebuildDiffContent` would leave **nothing for the pan
  to reveal**: the buffer *is* the pannable content (`viewport.go:360-364` cuts from
  `m.lines`). The marker must therefore be render-time.
- The render-time mechanism the library offers is `LeftGutterFunc`, and a gutter makes the
  **last 2–4 cells of the longest line unreachable** (§2.8.3, measured at two widths). It
  also costs a cell on every row, forever, to report a condition that applies to some rows.
- A title indicator costs zero rows, zero columns of content, no gutter, and no shortfall —
  **and it carries one thing a per-row marker cannot: that panning is available at all.**

⚠ **It must name the escape hatch for the tail.** When the widest visible row exceeds some
multiple of the pane (beyond 10× is 0.208% of over-wide lines, §2.3), the indicator should
read `COL 1-62 OF 405662 · o OPENS IT` — because at that size neither pan nor wrap is the
answer and `o` already is.

🔴 **`clipped`, not `truncated`.** `Diff.Truncated` means *GitHub did not send every file*
(§1.1). A render-clip flag sharing that word is how the two axes get conflated by the next
reader, and the brief was explicit that they must not be.

**Budget:** one title string, one pure function. No new state beyond what W2 adds.

### W2 — horizontal pan on `H` / `L`, and `0` to return

**Files:** `internal/ui/keys.go` (3 bindings, 3 actions, 3 `Dispatch` rows, `FullHelpFor`),
`internal/ui/app.go` (a `panDiff`, a `diffPanStep`), `internal/ui/panels.go`
(`syncDiffViewport`) · **Deps:** none; completes W1's text

```go
// mirrors scrollDiff: touches the VIEWPORT, never a cursor.
func (a App) panDiff(n int) App { … a.vp.ScrollRight(n) / ScrollLeft(-n) … }
```

🔴 **The load-bearing line is in `syncDiffViewport`, not in `panDiff`.** Measured (§TL;DR 3):
`EnsureVisible(cur, 0, 0)` does `SetXOffset(0)` unconditionally, so the pan is reset by the
very next `j`. The fix is to preserve it across the vertical scroll-to-cursor:

```go
x := a.vp.XOffset()
a.vp.EnsureVisible(cur, 0, 0)
a.vp.SetXOffset(x)
```

…and **not** by passing columns into `EnsureVisible`: its else-branch is
`SetXOffset(colstart - m.horizontalStep)` (`viewport.go:477`), which would drift the view 6
cells left on every cursor move. ⚠ `panDiff` itself must **not** call `syncDiffViewport`, for
exactly the reason `scrollDiff`'s header gives (`app.go:602-622`): it would yank the view
back and leave *"a feature that looks implemented, passes a naive test, and does literally
nothing on screen."* That header is the template; this item is the same hazard on the other
axis.

**Step size: half the pane**, i.e. `a.vp.Width()/2`, as a named constant's divisor so a
mutant has one place to hide — following `diffScrollStep = 3`'s own argument
(`app.go:593-600`). ⚠ **Not the library's `defaultHorizontalStep = 6`**: at 6 cells a p95
line (92 cells) needs 5 presses where a half-pane step needs 1. The §2.3 table is computed
at a half-pane step and is the justification.

### W3 — truncate the path in the pane head (the frame-breaking one)

**Files:** `internal/ui/panels.go` (`box`, lines 242-248) · **Deps:** none ·
**Independently revertible of W1 and W2**

```go
if a.Focus == PanelDiff && p == PanelDiff && a.currentFilePath() != "" {
    avail := inner - lipgloss.Width(title)
    head = lipgloss.JoinHorizontal(lipgloss.Top, title,
        styDim.Render(truncateMiddle(a.currentFilePath(), avail)))
}
```

**Middle-ellipsis — keep the root AND the basename — and the boundary is measured.** The
three strategies on a 120-character path:

| width | strategy | result |
|---:|---|---|
| 26 | right | `segment/segment/segment/s…` |
| 26 | left | `…/averyverylongfilename.go` |
| 26 | **middle** | `segment/segm…ngfilename.go` |
| 60 | right | `segment/segment/…/segment/seg…` (basename lost) |
| 60 | left | `…nt/segment/segment/segment/averyverylongfilename.go` |
| 60 | **middle** | `segment/segment/segment/segme…gment/averyverylongfilename.go` |

🔴 **Middle wins only where the width can hold the basename, and the diff head is the wide
pane** — `avail` is 56 at 100×30 and 96 at 140×40, against a basename p99 of 40 and a max of
60 (§2.2). At 26 cells middle is *worse* than left, because it eats into the basename. So:
**middle-ellipsis in the head, with a documented fallback to `truncateLeft` when
`avail < len(basename) + 8`** — a boundary a test must pin at both widths, not a rule of
thumb.

⚠ **`truncateMiddle` is a NEW helper beside `truncate`/`truncateLeft`, not a third flag on
either.** Both existing functions have pinned tests (`tree_test.go:730-760`) and one
documented call site each; widening their contracts would put three behaviours behind one
name.

🔴 **The Files tree needs NO change, and the brief's recommendation 3 is already
implemented there.** `renderFileRow` already left-truncates directory rows and
right-truncates file rows, with the asymmetry argued at `panels.go:402-409` and pinned at
`tree_test.go:730-760`. Touching it would re-open a settled decision. ⚠ Separately: 21.57%
of real basenames exceed the tree's ~20-cell name column, and a right-trim loses the
extension and any numeric discriminator (`module_001.go` vs `module_002.go` differ in the
*tail*). That is a real question — and it is an **open question (§7.3)**, not a work item,
because nothing in the operator's complaint points at it and the existing choice has a
written argument I have no measurement against.

### W4 — widen the frame guard's FIXTURE; do not add a guard

**Files:** `internal/ui/layout_test.go`, `internal/ui/app_test.go` (fixture) ·
**Deps:** W1/W2/W3 for the assertions

See §8.1. The guard that should have caught W3 already exists and already sweeps two
heights; its reach is bounded by a 14-character fixture path, not by its prose. **The fix is
a fixture dimension, not a mechanism** — strictly cheaper, and it repairs a guard whose
docstring currently over-claims.

### W5 — two stale comments, free to fix in whichever PR touches the file

| file:line | says | reality |
|---|---|---|
| `internal/udiff/udiff.go:57` | *"so `]h` / `[h` are an index lookup rather than a scan"* | the bindings are `]` / `[` — `]h` was the abandoned pending-key design (`keys.go:136-141`) |
| `internal/udiff/udiff.go:306` | *"`]h` is 'the next hunk in this review'"* | same |

Trivial, and worth doing because the same two-key spelling is what the brief had to rule out
again from scratch. A comment is a claim too.

---

## 5. The keys — enumerated before proposing one

Read from `keys.go:125-193` and `Dispatch()` at `:263-301`. **Every binding, all three
modes.** Modes are disjoint and nothing falls through (`keys.go:29-32`), so a browse key
cannot disturb a compose one — but reusing a *confirm* key in browse would still be a human
hazard, so I have avoided it.

| mode | keys claimed |
|---|---|
| **browse** (26 bindings) | `tab` · `shift+tab` · `k`/`up` · `j`/`down` · `ctrl+u`/`pgup` · `ctrl+d`/`pgdown` · `g`/`home` · `G`/`end` · `J` · `K` · `]` · `[` · `}` · `{` · `l`/`right` · `h`/`left` · `enter` · `o` · `r` · `?` · `q`/`ctrl+c`/`esc` · **`c`** · **`a`** · **`R`** · **`v`** · **`m`** |
| **compose** | `ctrl+d` · `enter` · `backspace` · `left` · `right` · `esc` |
| **confirm** | `y` · `n`/`esc` |

**The five write verbs are `c` `a` `R` `v` `m`** — bold above. A collision with one of those
is the dangerous class, because they act on real GitHub as the operator, so none of my
proposals goes near them.

**Proposed, all single presses — `bubbles/key` matches one `KeyPressMsg.String()` and a
pending-key sequence was tried and abandoned (`keys.go:136-141`):**

| key | action | why this key |
|---|---|---|
| **`L`** | pan diff right | `J`/`K` already established bare capitals as *the viewport-pan family* (`keys.go:136-143`), and `H`/`L` is the horizontal member of the same idiom. `h`/`l` are taken by the tree, which forces the capital — and the capital is the better answer anyway. |
| **`H`** | pan diff left | same |
| **`0`** | pan back to column 1 | free; vim's start-of-line; the "I am lost" key. `g`/`G` are the vertical equivalents and are already capitals/lowercase pairs, so `0` does not collide with that pattern. ⚠ alternative `^`, also free — operator's taste. |

Unbound after this change, for the record, so the next proposal does not have to re-derive
it: `b` `d` `e` `f` `i` `n` `p` `s` `t` `u` `w` `x` `y` `z`, `<` `>` `|` `\` `-` `=` `~`,
digits `1`–`9`, and every remaining capital.

⚠ **If wrap is built after all (§7.1), `w` and `z` are both free.** `w` is the better
mnemonic; `z` is vim's fold prefix and means nothing alone here. Neither is a write verb.

🔴 **All three go in `FullHelpFor` only, NOT `ShortHelpFor`** — the persistent browse row
already renders **146 columns** (measured, `keys.go:319-326`, and reproduced in §2.5), i.e.
it overflows a 140-column terminal before anything is added. `J`/`K`, `C-u`, `C-d`, `g`, `G`
and the tree keys are all FullHelp-only for exactly this reason; `?` is where the operator
already looks for this class of binding. The two-way ledger (`keys_test.go`) binds to
`FullHelpFor`, so this is free of the ledger's demands either way.

---

## 6. Sequencing and the recommended first PR

| PR | item | revertible of | why here |
|---|---|---|---|
| **1** | **W3 + W4** — truncate the head, widen the fixture | everything | The only **layout break** in the set, with a measured threshold (56 chars at 100×30) crossed by **8.0%** of real paths. Two lines of render change, one fixture dimension, no new key, no new state, no mode. **Ship it alone so the regression it fixes is legible.** |
| **2** | **W1** — the cut indicator | W2 | Closes the "silently" half of the complaint, which is the half nobody named. Useful **before** panning exists, because it tells the operator `o` is the answer for the extreme tail. |
| **3** | **W2** — pan on `H`/`L`/`0` | — (reads W1's offset; W1's text completes with it) | The actual fix for problem B, and now a small one. Its risk is concentrated in one line of `syncDiffViewport`, which §8.3 pins by mutation. |
| **4** | **W5** — the two stale `]h` comments | all | Fold into whichever PR touches `udiff.go`; do not open a PR for it. |
| **—** | **D (wrap)** | — | **Recommend declining.** If the operator wants it, it is its own PR, after PR 3, and it must carry the logical→rendered conversion and §8.4's guard — not `a.vp.SoftWrap = true`. |

⚠ **I did not run the binary.** Every claim above is from the package's own renderer and the
pinned dependency, executed in a scratchpad copy at two to six widths with controls. The
one thing that is *not* covered is what a real terminal does when handed a 31-row frame and
a 146-cell footer — whether the top scrolls away, the bottom clips, or the footer re-wraps
and makes it worse. **That is a step in the closing condition (§8.6), not a conclusion
here**, and it is why PR 1's verification is a live `tmux` run and not a green suite.

---

## 7. The FORK — what I need from the operator

### 7.1 Wrap: build it, or decline it? *(the one real decision)*

You asked for a wrap toggle and invited an alternative. **My recommendation is to decline
wrap and ship W1+W2+W3 instead**, on the evidence in §3.1: panning reaches 95.63% of
over-wide lines in two presses, wrap fails on the residue it exists for, it breaks `]` on
80% of presses at your default terminal size, correcting that needs an O(n) pass the library
will not give us, and it cannot coexist with the pan that does work.

**What would change my mind** is §7.2. Blast radius either way is this one read-only TUI on
this one host; nothing leaves the machine and nothing touches GitHub. **Your call to
proceed.**

### 7.2 The measurement that would reverse §7.1

How much of your review traffic is a **long prose or long-string** line — a changed Markdown
paragraph, a long test assertion, a big JSON literal — as opposed to a **minified or
generated blob**? Line length cannot tell them apart and I did not try. If the 125–440 cell
band is mostly prose you want to *read*, wrap earns its place for that band and the
recommendation flips. It is answerable from the same corpus with a content classifier, and
it is roughly an hour's work. **Worth doing before building wrap; not worth doing before
PRs 1–3**, which are right either way.

### 7.3 The Files-tree basename question *(not in scope; flag only)*

21.57% of real basenames exceed the tree's ~20-cell name column, and the current right-trim
drops the extension and any trailing discriminator. `renderFileRow`'s asymmetry has a
written argument (`panels.go:402-409`) and a pinned test, your complaint does not point at
it, and I have no measurement that the trim actually confuses you. **Raising it, not
proposing it.** If it does bother you, say so and it becomes its own item.

### 7.4 Two pre-existing defects I found and did not touch

- **62×20 overflows by 2 rows with the stock fixture** (§2.5) — the left column's Overview
  rows are wider than its 18-cell inner width at that size. `minWidth = 60` admits it;
  `layout_test.go` does not test it. Real, separate, and bundling it would blur PR 1.
- **The frame is 146 cells wide against a 140-cell budget** — already measured and
  documented at `keys.go:319-326`, still unfixed, and invisible to the row-count guard
  (§8.1). Also separate.

Neither is a regression from anything proposed here. Both deserve their own item; neither is
urgent.

---

## 8. Test coverage, specified

For each item: the guard, the base it must be **red** at, the mutation that must kill it
**with that guard's own message**, and the fixture states required. Base for every "red at"
claim: the current working-tree state of `main` at the time of reading, which must be
restated as a sha in the PR.

🔴 **The renderer must be a pure function `(content, width, mode) -> []string` and the
harness must sweep at least two widths — a boundary and a middle — naming them in the
assertion.** Every number in §2 is reported at two or more widths for this reason, and §2.2
shows why: the over-wide share is 24.16% at pane 62 and 2.29% at pane 102. A one-width suite
would not be wrong, it would be *confidently narrow*.

### 8.1 🔴 What the EXISTING guards structurally cannot see

| guard | what it pins | what it cannot see |
|---|---|---|
| `TestTheRenderedFrameFitsTheTerminalInEveryMode` (`layout_test.go:23-66`) | the frame's **row count** ≤ the terminal's height, at **two** sizes (140×40, 100×30), across 5 mode cases, **with a positive control** on the measurement | 🔴 **the W3 overflow** — its fixture's longest path is **14 characters** (`pkg/handler.go`, `app_test.go:79`) against an `avail` of 56 and 96. Its docstring says *"THE FRAME NEVER EXCEEDS THE TERMINAL, IN ANY MODE"*; its reach is *"…for a 14-character path."* ⚠ Also blind to **column** overflow — it counts `\n`, so the documented 146-cell footer is one row to it and two rows in a 100-column terminal. |
| `movement_test.go`'s `YOffset` literals (`:348-648`) | exact viewport offsets after each key | they are **`SoftWrap=false`-dependent and the file knows it** — `:213` reads *"viewport: no soft wrap, so the offsets below are line counts"*, and `:595` asserts `TotalLineCount() == bigDiffLines`, which is false the moment any line wraps. Turning wrap on globally would red them; turning it on **as a mode** leaves them silently scoped to one mode. |
| `scroll_test.go:139-155` | `SoftWrap=true` **benchmarks** | a benchmark asserts nothing. The mode is reachable from the suite and **no test asserts any behaviour in it.** |
| `tree_test.go:730-760` | `truncateLeft`'s ellipsis position, rune budget, tail survival, and that it **differs** from `truncate` | nothing about the **head**, which has no truncation to test |
| `TestALongTitleIsTruncatedAndTheTailSurvives` (`confirm_test.go:168`) | the confirm prompt's 60-rune title cap and its surviving tail | nothing in this scope; it is width-independent (§1.3) |
| the whole `internal/ui` suite | — | 🔴 **no test anywhere exercises a diff line WIDER than the pane.** The longest fixture diff line is under 50 cells (`app_test.go:105-108`). Problems A and B are, today, structurally untestable. |

### 8.2 W3 — the head truncation

- **Guard:** widen `TestTheRenderedFrameFitsTheTerminalInEveryMode`'s fixture axis — a
  `pathLen` dimension `{14, 56, 64, 104}` crossed with the existing `{140×40, 100×30}` and
  its five mode cases. **Not a new test.** The assertion, the error message and the positive
  control are already right; only the fixture was narrow.
- **Red at base:** measured — 31 rows at 100×30/56 chars, 32 at 64, 42 at 140×40/104 chars,
  against budgets of 30 and 40. The 14-char row must stay green, as the control that makes
  the others attributable.
- 🔴 **Isolate the mutation.** The 62×20 case is **confounded** (the stock fixture already
  overflows there, §2.5) and must be **excluded** from this axis or the test dies for
  another defect's reason — the textbook "mutant removed together with its enclosing
  condition". Keep the two sizes `layout_test.go` already uses.
- **Mutation that must kill it with this guard's own message:** revert `box`'s head to the
  untruncated `JoinHorizontal`. The failure must name the **row surplus** (*"the frame is 31
  lines, 1 more than the terminal has"*), not merely "output differs".
- **Second guard, on the strategy and not the length:** `TestTheHeadKeepsTheRootAndTheBasename`
  — assert **literal bytes** of the truncated head at `avail` = 56 **and** 96, each asserting
  (a) a `HasPrefix` on the first path segment, (b) a `HasSuffix` on the **whole basename**,
  (c) exactly one `…`, (d) `lipgloss.Width(head) <= inner`.
  🔴 **Pin the literal string, not a predicate over a constant.** A prior round in this
  codebase swapped two chevron constants' *values* and the package stayed green because every
  assertion was phrased in those same constants. An assertion written as
  `strings.Contains(head, ellipsis)` passes for a head that is all ellipsis; an assertion
  written as `got != "segment/segment/…/averyverylongfilename.go"` does not.
- **Third guard, on the documented boundary:** `TestTheHeadFallsBackToLeftEllipsisWhenTheBasenameWillNotFit`
  — at an `avail` below `len(basename)+8` the head must keep the **whole basename** and drop
  the root. Fixture must include a path whose basename is 60 chars (the measured max, §2.2)
  at `avail` 56, i.e. a case where no strategy can keep both.
- **Fixture states required:** a path with **no** separator (a bare basename); a path whose
  basename **alone** exceeds `avail`; a path shorter than `avail` (must be returned
  **byte-identical**, untouched); a path with multi-byte runes (the function must slice
  runes, as `truncate` already does); `avail <= 1` (the degenerate arm both existing helpers
  handle explicitly).

### 8.3 W2 — the pan

- **Guard A, behavioural:** `TestAPanRevealsTheTailOfALongLine` — a fixture diff line of
  known length with a **distinct sentinel in its last 8 cells**, asserted absent before the
  pan and present after, at pane **62 and 102**.
  🔴 **Pick fixture lengths that overshoot and are NOT a multiple of the step.** A line
  whose length is exactly `pane + k*step` lands the final pan exactly on the boundary, where
  the clamp and the correct value coincide — the guard then never executes against a real
  offset and survives a mutant that is off by one.
- **Guard B, the one that matters:** `TestAPanSurvivesTheNextCursorMove` — pan, then press
  `j`, `k`, `]`, `[`, `}`, `{`, `g` and `G`, asserting `XOffset()` is **unchanged** after
  each. **Red at base by construction** (the binding does not exist), and red against the
  naive implementation: measured, `ScrollRight(60)` → one `j` → `XOffset()==0` at both
  widths.
- **The mutation matrix, each killing a NAMED assertion:**
  - delete the `SetXOffset(x)` restore in `syncDiffViewport` → **Guard B** goes red with
    *"the pan was reset to column 0 by a cursor move"*; Guard A stays green, which is the
    point — it is what makes B non-redundant.
  - make `panDiff` call `syncDiffViewport` → **Guard A** red (the view snaps back), the
    exact defect `scrollDiff`'s header warns about.
  - change the step divisor from 2 → Guard A's literal offset assertion red. ⚠ **Mutate the
    divisor, not the whole expression** — a mutant that deletes the step *and* its
    enclosing call proves nothing about the step.
  - clamp `panDiff` by hand instead of letting the viewport do it → a guard asserting
    `XOffset()` at the extremes equals `maxXOffset()` must red, naming the double clamp
    (`scrollDiff`'s header makes this argument for the Y axis already).
- **Guard C, the cursor is unmoved:** `TestAPanMovesNoCursor` — `diffCur`, `commitCur`,
  `fileRowCur` and `Focus` identical before and after, from **every** panel. This is
  `scroll_test.go`'s existing `J`/`K` discipline (`movement_test.go:889-912`) applied to the
  new axis; reuse that test's shape rather than writing a new idiom.
- **Guard D, the ledger:** `keys_test.go`'s two-way per-mode ledger must fail if `H`/`L`/`0`
  are dispatched without help, or helped without dispatch. **Already exists; no new
  mechanism.** Watch it red in **both** directions before believing it covers the new keys —
  add the binding without the `FullHelpFor` entry, then the reverse.

### 8.4 W1 — the cut indicator

- **Guard:** `TestThePaneTitleSaysWhenARowIsCut` — assert the **whole normalised title
  string** at pane 62 and 102, for four states: no row over-wide (**indicator absent**), a
  row over-wide at offset 0, the same panned, and a row beyond 10× the pane (**the `o` hint
  present**).
  🔴 **Assert the absence case, and assert it as a literal.** *"The indicator appears"* is
  satisfied by an indicator that always appears, which is the mutant that makes the title
  useless. The positive and negative state must both be pinned, and the four fixture rows
  must carry **pairwise-distinct lengths** — all distinct from the pane width itself, so a
  mutant hardcoding `maxWidth` survives nothing.
- **Red at base:** the title has no such text; the `absent` case is green at base and the
  three others red. Report the matrix — **and label the absence case as an invariant guard,
  not a regression guard**, since the bug never violated it.
- **Mutations:** make the indicator unconditional → the absence assertion red. Compute the
  widest row from the **buffer** rather than the **visible slice** → the absence assertion
  red on a fixture whose long line is scrolled off, which must therefore be one of the
  fixtures. Drop the `o` hint → the fourth case red by literal.

### 8.5 If wrap is built — the guard it must carry

- 🔴 **`TestHunkNavigationLandsOnScreenInBothRenderModes`** — for `mode ∈ {pan, wrap}` ×
  `width ∈ {62, 102}`, press `]` 25 times and assert the row `diffCur` points at is
  **rendered in `a.vp.View()`** every time. Measured outcome at base: **0/25 off screen**
  under pan, **20/25** at pane 62 and **16/25** at pane 102 under a naive wrap.
  **This is the guard whose absence would let the one-line wrap ship.**
- **Assert the STATE, not a word.** The mode must be an enumerated value on `App` read by
  the renderer, and the guard must assert the **rendered row's bytes**, not the presence of a
  continuation glyph — a guard on `│` passes for a wrap that marks every row, and for one
  that marks none if the fixture happens not to wrap.
- **Positive control on the fixture:** assert `TotalLineCount() > len(Diff.Lines)` in wrap
  mode. A fixture whose lines all fit cannot see any wrap defect, and would report a
  reassuring 0/25.
- **The suite's `SoftWrap=false` assumption must be made explicit, not inherited.**
  `movement_test.go`'s `YOffset` literals and `:595`'s `TotalLineCount` equality would become
  mode-scoped claims; they must **say which mode** in the test name or the assertion, or the
  next reader will take them for universal. `movement_test.go:213`'s comment is the warning
  that was already written and would otherwise be the only record.

### 8.6 The live confirmation PR 1 owes — read-only, and the gaps it closes

Not a unit test; it is what makes PR 1's claim honest (§6). Read-only keys only.

```bash
P=mrprobe                       # a PRIVATE socket; never the default one
tmux -L $P new-session -d -x 100 -y 30 -s t 'mention-review <a PR with a >56-char path>'
tmux -L $P send-keys -t t '}'   # read-only: next file
tmux -L $P display-message -p -t t '#{pane_title}'   # STATE comes from the pane title
tmux -L $P capture-pane -p -t t                      # the frame
tmux -L $P kill-server          # kill YOUR socket; the default one is hook-blocked, correctly
```

- 🔴 **Read the pane TITLE for state, never a fixed line number.** A line probe conflates
  the sticky file header with viewport content and invents findings.
- 🔴 **No write key** — `c` `a` `R` `v` `m` act on real GitHub as the operator. The
  navigation above is `}`, `]`, `j`, `H`, `L`, `0`, `q` only.
- **Any bounded wait must carry and print a `matched=0/1` flag.** A poll loop that times out
  otherwise returns a plausible duration; this project has already recorded a manufactured
  32.5 s "measurement" that was three identical readings equal to the loop's own ceiling.
- **Do not `pkill -f` / `-x`.** Resolve PIDs and confirm `/proc/<pid>/cwd` is the right tree.
- **Report rows captured beside the verdict.** A clean frame from a capture that read nothing
  is the failure, not the all-clear.
- **What it adds that no unit test can:** whether an over-tall frame scrolls the top away or
  clips the bottom, and whether the 146-cell footer re-wraps in a 100-column terminal and
  makes the surplus worse than the row count suggests.

---

## 9. What I did NOT investigate

Explicitly, so nobody reads this scope as wider than it is.

1. **I never ran the binary.** No `tmux`, no `mention-review` process, no GitHub request.
   Every measurement is the package's own renderer or the pinned dependency, executed in a
   scratchpad copy. §8.6 is the gap, and it is PR 1's verification step.
2. **I did not build or test anything in the repository.** No `go build`, no
   `scripts/gate.sh`, no `scripts/scoped-tests.sh`, no `nix build`. The only `go test` runs
   were in the deleted scratchpad copy, scoped to `./internal/ui/` with `-run` filters. Every
   "red at base" and every mutation in §8 is a **specification of what must be watched**, not
   a measurement. **They must each be run.**
3. **The 62×20 narrow-terminal overflow** (§2.5, §7.4) — observed in the control, diagnosed
   only as far as "the Overview's rows are wider than its inner width". I did not read the
   left column's arithmetic, did not check the Commits or Files panels, and did not look for
   the threshold.
4. **The 146-cell footer** — I reproduced the number `keys.go:319-326` already records and
   did nothing further. I do not know whether `renderFooter` could be given a `Width`, nor
   what that would do to `help.Model`'s own eliding.
5. **Whether `go-gitdiff` can emit a line longer than the API sends.** `appendFragments`
   does `strings.TrimSuffix(ln.Line, "\n")` and nothing else (`udiff.go:276`), so I assume
   the rendered length is the source length. I did not test a line containing a `\r`, a tab
   expansion, or a wide/combining rune — and **`ansi.Cut` works in CELLS while
   `truncate`/`truncateLeft` work in RUNES**, which for CJK or emoji content are different
   numbers. **Open question for W1's indicator**, whose `COL n-m OF k` is a cell count.
6. **The `e2e_test.go` suite** — 292 lines, not read. I do not know whether it asserts a
   frame shape that W3 would move.
7. **Whether the 40-repository corpus represents the operator's review traffic.** It is the
   newest 40 merge-free commits of each local checkout on this host — the repositories they
   *clone*, which is not the same population as the PRs they *review*, and the brief itself
   notes 91% of clickable repositories have no local clone (`udiff.go:4-8`). Read §2.2 as
   "recent local commits", not "the PRs you open". The 8.0% and 24.16% figures should be
   re-derived against actual review traffic before either is quoted as a property of the
   operator's workflow.
8. **Prose versus minified, in the 125–440 cell band** — the measurement §7.2 names, and the
   one that would reverse §3.1. Not attempted.
9. **The gruvbox palette** — untouched and unexamined beyond confirming nothing proposed here
   adds, removes or re-references a colour. `theme_test.go` pins it to a nix file; no item in
   §4 goes near `theme.go`.
10. **Whether `--tier go` gates this package in CI.** `scripts/gate.sh` has a `go` tier and
    `checks.gotests` exists, but whether the infra repo's pipeline builds that leg is
    answered by `gh pr checks <n>` and I did not ask. **Until checked, assume it gates
    nothing**, and do not read a green local run as CI coverage.

---

## 10. Open questions

1. **Wrap: build or decline?** §7.1. My recommendation is decline; it is the operator's call
   and it is the only decision blocking anything.
2. **Prose vs minified in the 125–440 cell band?** §7.2. The measurement that would reverse
   question 1. ~1 hour, same corpus, a content classifier.
3. **Cells or runes in the indicator, and what does `ansi.Cut` do to a wide rune at the cut
   boundary?** §9.5. `COL 1-62 OF 410` is a cell count while `truncate` counts runes; for CJK
   or emoji the two disagree and the indicator would be wrong rather than imprecise.
4. **Does the Files tree's basename right-trim actually confuse the operator?** §7.3.
   21.57% of real basenames exceed the column; the existing choice has a written argument and
   a pinned test; nothing in the complaint points at it.
5. **Is the 40-repository corpus the right population?** §9.7. The 8.0% and 24.16% headline
   shares depend on it, and the PR-review population is measurable from the same tool's own
   history.
6. **Should the pane head show the path at all when the Files panel is already showing it?**
   Not investigated, and it is the `the-algorithm` question: the cheapest fix for problem C
   is **deleting** the head path, not truncating it. ⚠ Against that: `box` only shows it when
   `Focus == PanelDiff` (`panels.go:243`), precisely when the Files panel is *not* focused and
   its row may be scrolled out of view — so the head is probably the only place the path is
   reliably on screen. **Worth one measurement before W3 is built**: how often is the current
   file's tree row inside the Files panel's window at the two terminal sizes? If it is
   usually visible, deleting the head path is strictly simpler than W3 and ships the same
   frame fix.
7. **Does `minWidth = 60` still make sense** if the frame provably does not fit at 62×20
   (§7.4)? A declared minimum the layout cannot honour is a worse promise than a higher one.

---

## 11. Closing condition

This scope is closed when **all four** of the following hold. Each is a command a later
session can run, or a named judgement over named evidence.

1. **A decision is recorded on question 1 (wrap: build or decline).** Checker: the operator,
   reading §3.1 and §7.1, in a reply or a commit message that says which way and why. Until
   that exists this document is open regardless of what ships, because item D is the one the
   operator asked for by name.
2. **PRs 1–3 of §6 are merged, or explicitly dropped**, each verifiable mechanically:
   `gh pr view <n> --json mergedAt,mergeCommit` plus a content diff against `origin/main` —
   **never by ancestry**, since a squash merge never makes the branch head an ancestor.
3. **Each guard in §8 has been watched RED at a named base sha and GREEN at HEAD, and its
   mutation matrix run**, with the result reported as a matrix in the PR body. Checker:
   `scripts/scoped-tests.sh` for the iteration loop and the two `nix build` sandbox tiers
   (`pytests`, `nodetests`) **built one at a time** for the verdict — plus
   `scripts/gate.sh --tier go` if `gh pr checks` shows that leg is actually built (§9.10).
   🔴 A clean delta round **ends** the ladder; do not run another to confirm one.
4. **The live probe of §8.6 has been run on a real PR with a >56-character path at 100×30,
   and its captured frame reported** — rows captured beside the verdict. Until then PR 1's
   claim is *"verified against the renderer at two widths"*, which is true and is **not**
   *"verified on screen"*.

⚠ **If question 1 is answered "build wrap"**, this scope does **not** cover it: §8.5
specifies the guard but §4 contains no work item, no key is allocated, and the
logical→rendered conversion is undesigned. That becomes a **new scope**, and this one still
closes on the four conditions above.

---

## Appendix A — measurement reproduction

All four instruments were written to the session scratchpad and are **not** committed.

```bash
S=<scratchpad>

# 1. viewport semantics: the clip, the pan, the gutter shortfall, the
#    StyleLineFunc index, the EnsureVisible unit mismatch, the SoftWrap cost.
#    A standalone module importing the versions pinned in src/go.mod.
#    $S/vpprobe/{go.mod,go.sum,main.go}   (go.sum copied from src/)
GOFLAGS=-mod=mod GOPROXY=off GOWORK=off go run -C $S/vpprobe .

# 2. the real renderer, via the package's own harness on a COPY:
cp -R <devrc>/nix/pkgs/tools/mention-review/src $S/mr     # no .git inside; verified
cp $S/probes/zz_*.go $S/mr/internal/ui/
GOFLAGS=-mod=mod GOPROXY=off GOWORK=off \
  go test -C $S/mr ./internal/ui/ -run 'TestProbe|TestControl' -v
rm -rf $S/mr                                              # done after every run

# 3. prevalence: diff-line and path length over 40 local repos x 40 commits
python3 $S/prevalence.py

# 4. pan reachability of the over-wide lines
python3 $S/reach.py
```

The three probe files are preserved at `$S/probes/`:

| file | what it measures |
|---|---|
| `zz_control_test.go` | `TestControlUnmodifiedFixtureFrameFits` — the stock fixture at 4 sizes. **The control that makes §2.5 attributable.** |
| `zz_scope_probe_test.go` | the long-path frame overflow; the unmarked clip; `]` navigation under both render modes |
| `zz_pan_test.go` | that a pan is reset to column 0 by the next cursor move |

⚠ **`$S/vpprobe` needs `charm.land/bubbletea/v2 v2.0.9` named explicitly in its `go.mod`**
even though it does not import it — `bubbles` requires `v2.0.8`, which is not in the local
module cache, and `GOPROXY=off` then fails the build rather than resolving it. Without that
line the probe does not run, and the failure reads like a missing dependency rather than a
cache miss.

---

## Appendix B — key source locations, for the next reader

| fact | file:line |
|---|---|
| the pane head, **untruncated** — the W3 defect | `internal/ui/panels.go:243-245` |
| `truncate` (right-trim + `…`) | `internal/ui/panels.go:615-627` |
| `truncateLeft` (keep the tail) + its argument | `internal/ui/panels.go:629-647` |
| `truncateLeft`'s **only** call site, the directory-row arm | `internal/ui/panels.go:426` |
| the file/directory truncation asymmetry, argued | `internal/ui/panels.go:402-409` |
| diff rows built with no width handling | `internal/ui/panels.go:470-480` |
| `EnsureVisible(diffCur, 0, 0)` — what resets a pan | `internal/ui/panels.go:484-498` |
| the viewport's width/height, from `relayout` | `internal/ui/panels.go:595-600` |
| `SoftWrap = false`, the measured decision + its table | `internal/ui/app.go:213-228` |
| `diffScrollStep = 3`, and why a named constant | `internal/ui/app.go:593-600` |
| `scrollDiff`'s header — the "looks implemented, does nothing" hazard, verbatim | `internal/ui/app.go:602-622` |
| bare capitals as the pan family; the `]h` machine abandoned | `internal/ui/keys.go:136-143` |
| `h`/`l` taken by the tree, so the pan must be capitals | `internal/ui/keys.go:150-157` |
| the five write verbs | `internal/ui/keys.go:168-172` |
| the short-help row is already **146 columns** | `internal/ui/keys.go:319-326` |
| the two-way per-mode help ledger | `internal/ui/keys.go:343-346`, `keys_test.go` |
| the frame-containment guard, and its 14-char fixture | `internal/ui/layout_test.go:23-66`, `app_test.go:79` |
| the suite's `SoftWrap=false` assumption, stated | `internal/ui/movement_test.go:213`, `:595` |
| `SoftWrap=true` benchmarks, asserting nothing | `internal/ui/scroll_test.go:139-155` |
| `maxPromptTitle = 60` and its 80-column rationale | `internal/ui/confirm.go:28-35` |
| the long-title test (unaffected by this scope) | `internal/ui/confirm_test.go:166-184` |
| stale `]h` / `[h` comments | `internal/udiff/udiff.go:57`, `:306` |
| `Diff.Truncated` is a **fetch-time** flag | `internal/udiff/udiff.go:93-96`, `ghapi/query.go:848,858`, `ui/run.go:151` |
| `xOffset`, `XOffset`, `SetXOffset`, `ScrollLeft/Right` | `bubbles/v2@v2.2.1/viewport/viewport.go:74-75,547-567` |
| `SetXOffset` is a **no-op under SoftWrap** | `…/viewport.go:550-557` |
| `defaultHorizontalStep = 6`, `SetHorizontalStep` | `…/viewport.go:16,540-545` |
| `maxXOffset` uses `Width()`, not `maxWidth()` — the gutter shortfall | `…/viewport.go:308-322` |
| the silent clip — `ansi.Cut(line, xOffset, xOffset+maxWidth)` | `…/viewport.go:351-364` |
| `styleLines(…, ridx)` — `StyleLineFunc` is **logical** in both modes | `…/viewport.go:343,367-376` |
| `calculateLine` — `YOffset`/`TotalLineCount` are **rendered rows** under wrap | `…/viewport.go:270-306` |
| `EnsureVisible` does `SetXOffset(0)` when `colend <= maxWidth` | `…/viewport.go:471-480` |
| `GutterContext.Soft` marks a wrapped continuation | `…/viewport.go:405-443` |
