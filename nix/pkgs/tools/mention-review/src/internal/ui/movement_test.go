package ui

import (
	"errors"
	"fmt"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
)

// 🔴 WHY THIS FILE EXISTS — FIVE ACTIONS WERE NEVER PRESSED BY ANY TEST.
//
// `keys_test.go` walks `Dispatch()` and compares it to `FullHelp()`/`ShortHelp()`
// — and both of those are built from the SAME literal, so that ledger asserts
// the keymap agrees with ITSELF. It says nothing about where the cursor lands.
// MEASURED at 4a6c6f88: inverting all four arms of `moveIn`'s movement switch
//
//	ActPageUp   *cur -= page  ->  *cur += page
//	ActPageDown *cur += page  ->  *cur -= page
//	ActTop      *cur = 0      ->  *cur = n - 1
//	ActBottom   *cur = n - 1  ->  *cur = 0
//
// left ALL FIVE Go packages green. `ActNextFile` was equally unpressed. The
// suite is not uniformly weak — mutating `udiff.go`'s `h.LineIndex > from` to
// `>= from` kills three tests in two packages — the hole was exactly these five.
//
// 🔴 EVERY EXPECTATION HERE IS A LITERAL READ OFF THE SPEC, NOT OFF `moveIn`.
// The viewport height is PINNED BY THE TEST rather than taken from the layout,
// so "half a page" is 10 because the test made the page 20 — the help text's own
// words (`C-u` "half page up") are the contract, and a `moveIn` that computed a
// different number would have to disagree with that sentence to pass.
//
// ⚠ THE SECOND MOVEMENT SWITCH IS COVERED TOO. `move()` has a second, entirely
// separate movement `switch` for the Overview panel's body viewport
// (`HalfPageUp`/`HalfPageDown`/`GotoTop`/`GotoBottom`), which no mutation run
// had ever touched. `TestTheOverviewBodyPagesAndJumpsToItsEnds` presses the same
// five keys there.

// --- fixture pins -------------------------------------------------------------

// bigDiffLines is what `bigApp(t, 200)` parses to: 200 body lines, one hunk
// header, one file header. PINNED as a literal so a fixture that silently
// changed size cannot quietly move every expectation below with it.
const bigDiffLines = 202

// pagedDiff is a Diff-panel App whose page size the TEST owns.
//
// 🔴 `a.vp.SetHeight(20)` is the whole point: `page` is `panelBodyHeight()/2`,
// and a test that read the page size out of the live layout would be deriving
// its expectation from the code it is testing. Nothing on a movement path calls
// `relayout()`, so this height survives every `Step` below.
func pagedDiff(t *testing.T) App {
	t.Helper()
	a := bigApp(t, 200)
	if got := len(a.Diff.Lines); got != bigDiffLines {
		t.Fatalf("fixture drifted: the big diff parsed to %d lines, want %d", got, bigDiffLines)
	}
	if a.diffCur != 0 {
		t.Fatalf("a freshly loaded diff starts at line %d, want 0", a.diffCur)
	}
	a.Focus = PanelDiff
	a.vp.SetHeight(20)
	if got := a.panelBodyHeight(); got != 20 {
		t.Fatalf("the test failed to pin the page: panelBodyHeight() = %d, want 20", got)
	}
	return a
}

// --- ctrl+d / ctrl+u ----------------------------------------------------------

// A half page of a 20-line panel is 10 lines, and `ctrl+u` undoes `ctrl+d`.
//
// 🔴 THE SECOND PRESS IS NOT REDUNDANT. A single press from 0 cannot tell a
// half-page step from any other single jump; two presses pin the STEP, and the
// return journey pins the SIGN of each arm independently.
func TestPageDownAndPageUpMoveByHalfAPageInOppositeDirections(t *testing.T) {
	a := pagedDiff(t)

	down1, intents := a.Step(keyPress("ctrl+d"))
	if len(intents) != 0 {
		t.Errorf("ctrl+d emitted %v — moving a cursor is local", intents)
	}
	if down1.diffCur != 10 {
		t.Errorf("one ctrl+d from the top -> diffCur = %d, want 10", down1.diffCur)
	}

	down2, _ := down1.Step(keyPress("ctrl+d"))
	if down2.diffCur != 20 {
		t.Errorf("two ctrl+d from the top -> diffCur = %d, want 20", down2.diffCur)
	}

	up1, intents := down2.Step(keyPress("ctrl+u"))
	if len(intents) != 0 {
		t.Errorf("ctrl+u emitted %v — moving a cursor is local", intents)
	}
	if up1.diffCur != 10 {
		t.Errorf("ctrl+u from line 20 -> diffCur = %d, want 10", up1.diffCur)
	}

	up2, _ := up1.Step(keyPress("ctrl+u"))
	if up2.diffCur != 0 {
		t.Errorf("a second ctrl+u -> diffCur = %d, want 0", up2.diffCur)
	}
}

// 🔴 THE BOUNDARIES ARE WHERE AN INVERTED ARM IS LOUDEST. At the top of the
// buffer a correct `ctrl+u` is INERT (clamped), while an arm that added instead
// of subtracting would move ten lines DOWN and still look like "a page".
func TestPageUpAtTheTopAndPageDownAtTheBottomAreInert(t *testing.T) {
	a := pagedDiff(t)

	top, _ := a.Step(keyPress("ctrl+u"))
	if top.diffCur != 0 {
		t.Errorf("ctrl+u at the top of the buffer -> diffCur = %d, want 0", top.diffCur)
	}

	// Placed on the last line WITHOUT pressing any of the keys under test, so
	// this case cannot be rescued by a different arm being right.
	a.diffCur = bigDiffLines - 1
	a.syncDiffViewport()
	bottom, _ := a.Step(keyPress("ctrl+d"))
	if bottom.diffCur != bigDiffLines-1 {
		t.Errorf("ctrl+d on the last line -> diffCur = %d, want %d",
			bottom.diffCur, bigDiffLines-1)
	}
}

// --- g / G --------------------------------------------------------------------

// `g` is the FIRST line and `G` is the LAST one. Started from the middle so
// neither expectation is already true when the key is pressed.
func TestTopAndBottomJumpToTheEndsOfTheDiff(t *testing.T) {
	a := pagedDiff(t)
	a.diffCur = 100
	a.syncDiffViewport()

	top, intents := a.Step(keyPress("g"))
	if len(intents) != 0 {
		t.Errorf("g emitted %v — moving a cursor is local", intents)
	}
	if top.diffCur != 0 {
		t.Errorf("g from line 100 -> diffCur = %d, want 0", top.diffCur)
	}

	bottom, intents := a.Step(keyPress("G"))
	if len(intents) != 0 {
		t.Errorf("G emitted %v — moving a cursor is local", intents)
	}
	if bottom.diffCur != bigDiffLines-1 {
		t.Errorf("G from line 100 -> diffCur = %d, want %d", bottom.diffCur, bigDiffLines-1)
	}

	// And the pair composes: G then g is back at the top, g then G at the end.
	if back, _ := bottom.Step(keyPress("g")); back.diffCur != 0 {
		t.Errorf("G then g -> diffCur = %d, want 0", back.diffCur)
	}
	if fwd, _ := top.Step(keyPress("G")); fwd.diffCur != bigDiffLines-1 {
		t.Errorf("g then G -> diffCur = %d, want %d", fwd.diffCur, bigDiffLines-1)
	}
}

// --- } ------------------------------------------------------------------------

// `}` lands on the first line of the NEXT file, and is inert on the last file.
//
// ⚠ THE FIXTURE PIN IS LOAD-BEARING: the two-file fixture parses to 9 lines with
// the second file's header at index 4, so `4` below is a position in a buffer
// this test has just measured, not a number copied out of `FileStart`.
func TestNextFileJumpsToTheStartOfTheFollowingFile(t *testing.T) {
	a := ready(t)
	a.Focus = PanelDiff
	if got := len(a.Diff.Files); got != 2 {
		t.Fatalf("fixture drifted: %d files, want 2", got)
	}
	if got := len(a.Diff.Lines); got != 9 {
		t.Fatalf("fixture drifted: %d diff lines, want 9", got)
	}
	if got := a.Diff.FileStart(1); got != 4 {
		t.Fatalf("fixture drifted: file 1 starts at line %d, want 4", got)
	}

	next, intents := a.Step(keyPress("}"))
	if len(intents) != 0 {
		t.Errorf("} emitted %v — navigation is local", intents)
	}
	if next.diffCur != 4 {
		t.Errorf("} from the first file -> diffCur = %d, want 4", next.diffCur)
	}
	// The Files panel follows the diff cursor, as `{` already asserts in the
	// other direction. ⚠ ROW 2, NOT FILE 1: the `ready` fixture's two files
	// live under `pkg/`, so the tree is [0 pkg/, 1 handler.go, 2 widget.go] and
	// the SECOND file is the THIRD row. Asserting the path as well as the index
	// is what makes this a claim about the right file rather than an ordinal.
	if next.fileRowCur != 2 {
		t.Errorf("} left fileRowCur = %d, want 2", next.fileRowCur)
	}
	if got := next.SelectedFilePath(); got != "pkg/widget.go" {
		t.Errorf("} selected %q, want pkg/widget.go", got)
	}

	// On the LAST file `}` is inert rather than wrapping or running off the end.
	last, _ := next.Step(keyPress("}"))
	if last.diffCur != 4 {
		t.Errorf("} on the last file moved the cursor to %d, want it to stay at 4", last.diffCur)
	}
}

// --- the SECOND movement switch: the Overview body viewport --------------------

// longIssue is an issue card whose body is longer than the viewport, so its
// scroll offset can actually move. 100 short lines in an 80-column, 10-row
// viewport: no soft wrap, so the offsets below are line counts.
func longIssue(t *testing.T) App {
	t.Helper()
	snap := fixtureIssue()
	var b strings.Builder
	for i := 0; i < 100; i++ {
		fmt.Fprintf(&b, "line %03d\n", i)
	}
	snap.Body = strings.TrimSuffix(b.String(), "\n")

	a := New(fxOwner, fxName, fxNum)
	a.Width, a.Height = 140, 40
	a, _ = a.Step(PRLoaded{Snap: snap})
	a.Focus = PanelOverview
	a.body.SetWidth(80)
	a.body.SetHeight(10)
	a.body.GotoTop()

	if got := a.body.TotalLineCount(); got != 100 {
		t.Fatalf("the issue body wrapped: %d viewport lines, want 100", got)
	}
	if got := a.body.YOffset(); got != 0 {
		t.Fatalf("the body did not start at the top: YOffset = %d", got)
	}
	return a
}

// 🔴 THE OVERVIEW PANEL HAS ITS OWN MOVEMENT SWITCH, and it was as untested as
// the diff one. Same five keys, same contract, a different cursor: the body
// viewport's scroll offset.
func TestTheOverviewBodyPagesAndJumpsToItsEnds(t *testing.T) {
	a := longIssue(t)

	down1, intents := a.Step(keyPress("ctrl+d"))
	if len(intents) != 0 {
		t.Errorf("ctrl+d on the Overview emitted %v", intents)
	}
	if got := down1.body.YOffset(); got != 5 {
		t.Errorf("one ctrl+d -> YOffset = %d, want 5 (half of a 10-row viewport)", got)
	}
	down2, _ := down1.Step(keyPress("ctrl+d"))
	if got := down2.body.YOffset(); got != 10 {
		t.Errorf("two ctrl+d -> YOffset = %d, want 10", got)
	}
	up, _ := down2.Step(keyPress("ctrl+u"))
	if got := up.body.YOffset(); got != 5 {
		t.Errorf("ctrl+u from offset 10 -> YOffset = %d, want 5", got)
	}

	// `ctrl+u` at the top is inert — the mirror of the diff-panel boundary.
	if got, _ := a.Step(keyPress("ctrl+u")); got.body.YOffset() != 0 {
		t.Errorf("ctrl+u at the top -> YOffset = %d, want 0", got.body.YOffset())
	}

	// 100 lines in a 10-row viewport bottoms out at offset 90.
	bottom, intents := down1.Step(keyPress("G"))
	if len(intents) != 0 {
		t.Errorf("G on the Overview emitted %v", intents)
	}
	if got := bottom.body.YOffset(); got != 90 {
		t.Errorf("G -> YOffset = %d, want 90", got)
	}
	top, _ := bottom.Step(keyPress("g"))
	if got := top.body.YOffset(); got != 0 {
		t.Errorf("g from the bottom -> YOffset = %d, want 0", got)
	}
	// And `ctrl+d` at the bottom is inert.
	if got, _ := bottom.Step(keyPress("ctrl+d")); got.body.YOffset() != 90 {
		t.Errorf("ctrl+d at the bottom -> YOffset = %d, want 90", got.body.YOffset())
	}
}

// --- J / K: the diff-viewport pan ---------------------------------------------
//
// 🔴 THESE ASSERT A PAN AND A NON-MOVE AT THE SAME TIME, AND THE SECOND HALF IS
// THE POINT. `J`/`K` are vim's `ctrl+e`/`ctrl+y`: they move the WINDOW and leave
// `diffCur`, `fileRowCur` and `commitCur` exactly where they were. A version wired
// through `move()`/`moveIn()` would move a cursor; a version that called
// `syncDiffViewport()` afterwards would move the window and then immediately
// `EnsureVisible` it straight back to the cursor — a feature that looks
// implemented and does nothing on screen. Both halves are asserted below.
//
// 🔴 THE FIXTURE OVERSHOOTS THE STEP ON PURPOSE. 202 buffer lines, a 20-row
// viewport and a 182-line scroll range: none is a multiple of 3, and the half
// page is 10. A fixture sized in multiples of the step lets a `3 -> 1` mutant
// land on the same final offset and survive a fully green suite.

const (
	// scrollFixtureHeight is the viewport height the diff-scroll fixture pins.
	// 20 % 3 == 2 — see the note above.
	scrollFixtureHeight = 20
	// scrollFixtureBottom is the LAST offset the fixture can reach. MEASURED
	// from the fixture by the clamp test below rather than computed from the
	// viewport's own rule. 182 % 3 == 2, so a clamp assertion cannot be
	// satisfied by a wrong step.
	scrollFixtureBottom = 182

	// The cursors the fixture starts on. 🔴 ALL NON-ZERO, PAIRWISE DISTINCT,
	// AND DISTINCT FROM THE STEP AND ITS MULTIPLES (3, 6, 9, 12) AND FROM THE
	// BUFFER BOUNDS (0, 20, 182, 202). A cursor that starts at 0 cannot see a
	// mutant that resets it to 0.
	//
	// ⚠ `scrollFixtureRowCur` INDEXES TREE ROWS. `bigApp(t, 200)` has one file,
	// `pkg/generated/module_000.go`, whose single-child chain compacts to two
	// rows: [0 `pkg/generated`, 1 `module_000.go`]. Row 1 is the file row.
	scrollFixtureDiffCur   = 7
	scrollFixtureRowCur    = 1
	scrollFixtureCommitCur = 2
)

// errScrollFixture is the synthetic failure the nil-diff case injects.
var errScrollFixture = errors.New("the diff read failed")

// scrollable is a loaded App whose diff viewport has room to move, focused on
// `focus`, with all three cursors parked on distinctive non-zero values.
func scrollable(t *testing.T, focus Panel) App {
	t.Helper()
	a := bigApp(t, 200)
	if got := len(a.Diff.Lines); got != bigDiffLines {
		t.Fatalf("fixture drifted: the big diff parsed to %d lines, want %d", got, bigDiffLines)
	}
	a.Focus = focus
	a.commitCur = scrollFixtureCommitCur
	a.fileRowCur = scrollFixtureRowCur
	a.diffCur = scrollFixtureDiffCur
	a.vp.SetHeight(scrollFixtureHeight)
	a.syncDiffViewport()

	// 🔴 THE FIXTURE PINS ITS OWN STARTING POINT. Every expectation below is an
	// offset relative to 0, so a fixture that silently started scrolled would
	// move them all together and still read green.
	if got := a.vp.Height(); got != scrollFixtureHeight {
		t.Fatalf("the test failed to pin the viewport height: %d, want %d",
			got, scrollFixtureHeight)
	}
	if got := a.vp.YOffset(); got != 0 {
		t.Fatalf("the fixture starts scrolled to %d, want 0 — cursor line %d is inside "+
			"the first %d rows, so EnsureVisible should not have moved anything",
			got, scrollFixtureDiffCur, scrollFixtureHeight)
	}
	// POSITIVE CONTROL ON THE FIXTURE: the viewport must have somewhere to go,
	// or every "it scrolled" assertion below is satisfied by a no-op.
	if got := a.vp.TotalLineCount(); got <= scrollFixtureHeight {
		t.Fatalf("the buffer is %d lines in a %d-row viewport — it cannot scroll at "+
			"all, so this fixture measures nothing", got, scrollFixtureHeight)
	}
	return a
}

// cursors is the triple `J`/`K` must never touch, as one comparable value.
type cursors struct{ commit, row, diff int }

func cursorsOf(a App) cursors {
	return cursors{commit: a.commitCur, row: a.fileRowCur, diff: a.diffCur}
}

// 🔴 THE STEP IS EXACTLY THREE, AND THE TWO DIRECTIONS ARE OPPOSITE. Two presses
// pin the STEP — one press from 0 cannot tell 3 from any other single jump — and
// the return journey pins each arm's SIGN independently.
func TestScrollDiffPansTheViewportByExactlyThreeLinesPerPress(t *testing.T) {
	a := scrollable(t, PanelDiff)

	down1, intents := a.Step(keyPress("J"))
	if len(intents) != 0 {
		t.Errorf("J emitted %v — panning a viewport is local", intents)
	}
	if got := down1.vp.YOffset(); got != 3 {
		t.Errorf("one J from offset 0 -> YOffset = %d, want 3", got)
	}

	down2, _ := down1.Step(keyPress("J"))
	if got := down2.vp.YOffset(); got != 6 {
		t.Errorf("two J from offset 0 -> YOffset = %d, want 6", got)
	}

	up1, intents := down2.Step(keyPress("K"))
	if len(intents) != 0 {
		t.Errorf("K emitted %v — panning a viewport is local", intents)
	}
	if got := up1.vp.YOffset(); got != 3 {
		t.Errorf("K from offset 6 -> YOffset = %d, want 3", got)
	}

	up2, _ := up1.Step(keyPress("K"))
	if got := up2.vp.YOffset(); got != 0 {
		t.Errorf("a second K -> YOffset = %d, want 0", got)
	}
}

// 🔴 THE DEFINING TEST. From EVERY panel, `J` pans the diff viewport and leaves
// all three cursors byte-identical. If a cursor moves, the feature is wrong — it
// has become a cursor key with a capital letter.
func TestScrollDiffPansFromEveryPanelAndMovesNoCursor(t *testing.T) {
	want := cursors{
		commit: scrollFixtureCommitCur,
		row:    scrollFixtureRowCur,
		diff:   scrollFixtureDiffCur,
	}
	for _, p := range []Panel{PanelOverview, PanelCommits, PanelFiles, PanelDiff} {
		t.Run(p.Title(), func(t *testing.T) {
			a := scrollable(t, p)
			if got := cursorsOf(a); got != want {
				t.Fatalf("the fixture starts at %+v, want %+v", got, want)
			}
			bodyBefore := a.body.YOffset()

			down, intents := a.Step(keyPress("J"))
			if len(intents) != 0 {
				t.Errorf("J in %s emitted %v", p.Title(), intents)
			}
			if got := down.vp.YOffset(); got != 3 {
				t.Errorf("J in %s -> diff YOffset = %d, want 3 — the pan must work "+
					"from every panel, not only the Diff one", p.Title(), got)
			}
			if got := cursorsOf(down); got != want {
				t.Errorf("J in %s moved a cursor: %+v, want %+v unchanged — J pans the "+
					"window, it is not a cursor key", p.Title(), got, want)
			}
			// 🔴 AND IT IS NOT THE FOCUSED PANEL'S OWN SCROLLER. The Overview
			// body has its own viewport, which `j`/`k` drive; `J` must leave it
			// alone, or the binding means two different things in two panels.
			if got := down.body.YOffset(); got != bodyBefore {
				t.Errorf("J in %s moved the Overview body viewport to %d, want %d",
					p.Title(), got, bodyBefore)
			}

			up, _ := down.Step(keyPress("K"))
			if got := up.vp.YOffset(); got != 0 {
				t.Errorf("K in %s -> diff YOffset = %d, want 0", p.Title(), got)
			}
			if got := cursorsOf(up); got != want {
				t.Errorf("K in %s moved a cursor: %+v, want %+v unchanged", p.Title(), got, want)
			}

			// 🔴 AND THE PAN SURVIVES LEAVING THE CURSOR BEHIND — THIS IS THE
			// INERT-FEATURE DETECTOR. A `scrollDiff` that ends in
			// `syncDiffViewport()` still passes every assertion above, because
			// three lines is not enough to push cursor line 7 out of a 20-row
			// window and `EnsureVisible` correctly does nothing. Five presses
			// (offset 15) put it above the window, where a stray
			// `EnsureVisible(7)` would yank the view back to 7 and the feature
			// would be dead on screen while reading as implemented. MEASURED:
			// with that mutation, the assertions above stay GREEN and this one
			// goes red.
			far := a
			for i := 0; i < 5; i++ {
				far, _ = far.Step(keyPress("J"))
			}
			if got := far.vp.YOffset(); got != 15 {
				t.Errorf("five J in %s -> diff YOffset = %d, want 15 — the window must "+
					"STAY where it was panned to, above cursor line %d, rather than "+
					"snapping back to it", p.Title(), got, scrollFixtureDiffCur)
			}
			if got := cursorsOf(far); got != want {
				t.Errorf("five J in %s moved a cursor: %+v, want %+v unchanged",
					p.Title(), got, want)
			}
		})
	}
}

// 🔴 THIS IS WHAT JUSTIFIES A SECOND BINDING. In the panel where `j` is already
// live, `J` must do something DIFFERENT: `j` moves `diffCur` and leaves the
// window alone (the new line is already on screen), `J` moves the window and
// leaves `diffCur` alone. Asserted as a PAIR from ONE starting state, because
// either half alone is satisfied by a `J` that is a second spelling of `j`.
func TestInTheDiffPanelCapitalJPansWhileLowercaseJMovesTheCursor(t *testing.T) {
	a := scrollable(t, PanelDiff)
	// Park the cursor deep enough that the window has already followed it, so
	// "the window did not move" is a claim about a window that CAN move.
	a.diffCur = 100
	a.syncDiffViewport()
	if got := a.vp.YOffset(); got != 100 {
		t.Fatalf("the fixture did not follow the cursor to line 100: YOffset = %d", got)
	}
	// 🔴 THE FILES CURSOR IS PARKED ON THE *DIRECTORY* ROW, WHICH IS WHAT MAKES
	// THE PAIR BELOW DISCRIMINATING. `j` drags it onto the file the diff cursor
	// is in (row 1); `J` must leave it on row 0. Start it on row 1 and both keys
	// produce the same number and the assertion proves nothing.
	a.fileRowCur = 0
	if r, ok := a.currentRow(); !ok || !r.IsDir {
		t.Fatalf("row 0 of the big fixture is not a directory row (%+v) — this "+
			"test's starting point is wrong", r)
	}

	lower, _ := a.Step(keyPress("j"))
	if lower.diffCur != 101 {
		t.Errorf("j from line 100 -> diffCur = %d, want 101", lower.diffCur)
	}
	if got := lower.vp.YOffset(); got != 100 {
		t.Errorf("j from line 100 -> YOffset = %d, want 100 — line 101 is already on "+
			"screen, so the window has no reason to move", got)
	}

	upper, _ := a.Step(keyPress("J"))
	if upper.diffCur != 100 {
		t.Errorf("J from line 100 -> diffCur = %d, want 100 — J is not a cursor key",
			upper.diffCur)
	}
	if got := upper.vp.YOffset(); got != 103 {
		t.Errorf("J from line 100 -> YOffset = %d, want 103", got)
	}
	// ⚠ AND THE FILE CURSOR SEPARATES THEM TOO. `j` drags the Files highlight
	// onto whatever file the diff cursor landed in (`syncFileCursorFromDiff`);
	// `J` touches nothing, so the fixture's row cursor survives.
	if lower.fileRowCur != 1 {
		t.Errorf("j left fileRowCur = %d, want 1 — the big fixture's one file is "+
			"row 1, under its compacted `pkg/generated` directory row", lower.fileRowCur)
	}
	if upper.fileRowCur != 0 {
		t.Errorf("J left fileRowCur = %d, want 0 unchanged", upper.fileRowCur)
	}
}

// 🔴 BOTH ENDS, AND NEITHER PANICS. `K` at the top must not produce a negative
// offset and `J` at the bottom must not run past the last line.
func TestScrollDiffClampsAtBothEndsWithoutPanicking(t *testing.T) {
	top := scrollable(t, PanelDiff)
	if got := top.vp.YOffset(); got != 0 {
		t.Fatalf("the fixture is not at the top: YOffset = %d", got)
	}
	stay, _ := top.Step(keyPress("K"))
	if got := stay.vp.YOffset(); got != 0 {
		t.Errorf("K at the top -> YOffset = %d, want 0 — an offset must never go negative", got)
	}

	// Driven to the end WITHOUT pressing either key under test, so this case
	// cannot be rescued by the other arm being right.
	bottom := scrollable(t, PanelDiff)
	bottom.vp.SetYOffset(bigDiffLines * 2) // the viewport clamps this itself
	if got := bottom.vp.YOffset(); got != scrollFixtureBottom {
		t.Fatalf("fixture drifted: the last reachable offset is %d, want %d — every "+
			"clamp expectation below is relative to it", got, scrollFixtureBottom)
	}
	past, _ := bottom.Step(keyPress("J"))
	if got := past.vp.YOffset(); got != scrollFixtureBottom {
		t.Errorf("J at the bottom -> YOffset = %d, want %d", got, scrollFixtureBottom)
	}
	// ⚠ AND THE LAST PARTIAL STEP IS A STEP, NOT A REFUSAL. Two lines from the
	// end, `J` moves those two rather than declining because three do not fit.
	near := scrollable(t, PanelDiff)
	near.vp.SetYOffset(scrollFixtureBottom - 2)
	short, _ := near.Step(keyPress("J"))
	if got := short.vp.YOffset(); got != scrollFixtureBottom {
		t.Errorf("J two lines from the end -> YOffset = %d, want %d", got, scrollFixtureBottom)
	}
}

// 🔴 NO DIFF, NO PAN, NO PANIC — AND THE SECOND SUB-CASE IS THE ONE THAT MEANS
// ANYTHING. A freshly loading App has an EMPTY viewport, so its inertness is a
// property of the viewport, not of this code. The reachable state that separates
// them is a diff read that FAILED AFTER one succeeded: `DiffLoaded{Err}` nils
// `Diff` without rebuilding the content, so the viewport still holds the
// previous diff's 202 lines and would scroll them happily. Only the
// `Diff == nil` guard in `scrollDiff` stops it.
func TestScrollDiffIsInertWhenThereIsNoDiff(t *testing.T) {
	t.Run("still loading", func(t *testing.T) {
		a := New(fxOwner, fxName, fxNum)
		a.Width, a.Height = 140, 40
		if a.Diff != nil {
			t.Fatal("fixture is wrong: a freshly built App already has a diff")
		}
		for _, k := range []string{"J", "K"} {
			next, intents := a.Step(keyPress(k)) // must not panic
			if got := next.vp.YOffset(); got != 0 {
				t.Errorf("%s while loading -> YOffset = %d, want 0", k, got)
			}
			if len(intents) != 0 {
				t.Errorf("%s while loading emitted %v", k, intents)
			}
		}
	})

	t.Run("the diff read failed over a populated viewport", func(t *testing.T) {
		a := scrollable(t, PanelDiff)
		a, _ = a.Step(DiffLoaded{Err: errScrollFixture})
		if a.Diff != nil {
			t.Fatal("DiffLoaded{Err} left a diff behind — this case is not the one it claims")
		}
		// POSITIVE CONTROL: the stale content really is still there, so an
		// unguarded ScrollDown WOULD have moved. Without this the assertion
		// below passes against an empty viewport and proves nothing.
		if got := a.vp.TotalLineCount(); got != bigDiffLines {
			t.Fatalf("the viewport was emptied by the failure (%d lines) — the guard "+
				"under test is unreachable from this state, so this case is vacuous", got)
		}
		for _, k := range []string{"J", "K"} {
			next, _ := a.Step(keyPress(k))
			if got := next.vp.YOffset(); got != 0 {
				t.Errorf("%s with no diff -> YOffset = %d, want 0 — the viewport still "+
					"holds the previous diff's lines, and scrolling them shows the "+
					"operator a diff that is no longer loaded", k, got)
			}
		}
	})
}

// 🔴 THE PAN IS NOT STICKY, AND THAT IS STATED RATHER THAN DISCOVERED. Anything
// that calls `relayout()` — `tab`, `?`, a resize — ends in
// `EnsureVisible(diffCur, 0, 0)` and snaps the window back to the cursor. That
// is vim-like and acceptable; it is pinned here so the next person reads it as a
// decision rather than rediscovering it as a bug.
func TestRelayoutSnapsAScrolledViewportBackToTheCursor(t *testing.T) {
	// FOUR presses, not three: four put the window (offset 12) past cursor line
	// 7, so "not visible" is genuinely true when relayout runs. Three would
	// leave line 7 on screen and EnsureVisible would correctly do nothing.
	panned := scrollable(t, PanelDiff)
	for i := 0; i < 4; i++ {
		panned, _ = panned.Step(keyPress("J"))
	}
	if got := panned.vp.YOffset(); got != 12 {
		t.Fatalf("four J -> YOffset = %d, want 12", got)
	}
	if panned.diffCur != scrollFixtureDiffCur {
		t.Fatalf("four J moved diffCur to %d", panned.diffCur)
	}

	for _, c := range []struct {
		name string
		step func(App) App
	}{
		{"tab", func(a App) App { n, _ := a.Step(keyPress("tab")); return n }},
		{"?", func(a App) App { n, _ := a.Step(keyPress("?")); return n }},
		{"resize", func(a App) App {
			n, _ := a.Step(tea.WindowSizeMsg{Width: 120, Height: 36})
			return n
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			back := c.step(panned)
			if got := back.vp.YOffset(); got != scrollFixtureDiffCur {
				t.Errorf("%s after a pan -> YOffset = %d, want %d (the cursor's line) — "+
					"relayout ends in EnsureVisible, which is the STATED behaviour",
					c.name, got, scrollFixtureDiffCur)
			}
			if back.diffCur != scrollFixtureDiffCur {
				t.Errorf("%s moved diffCur to %d", c.name, back.diffCur)
			}
		})
	}
}

// --- the Files panel's directory tree -----------------------------------------
//
// 🔴 THE CURSOR HALF. `tree_test.go` owns construction, compaction, aggregation
// and rendering; what follows is what a KEY does — which row the cursor lands
// on, and what that does to the diff. The fixture is `treeApp`, whose tree
// order, `Snap.Files` order and `Diff.Files` order are three DIFFERENT orders on
// purpose (it proves that about itself before returning).

// walkTo drives the Files cursor down to `row` with `j`.
//
// 🔴 IT IS BOUNDED, AND THE BOUND IS NOT PARANOIA. The first draft was a bare
// `for a.fileRowCur < row { press("j") }`, and against a tree that produced NO
// rows it spun forever: the test binary hung, `go test` was killed, and the
// harness reading its output reported a tidy "5 tests failed" for a run that had
// never finished. An unreachable row is a FAILURE with a name, not a hang.
func walkTo(t *testing.T, a App, row int) App {
	t.Helper()
	for i := 0; i <= len(a.fileRows)+2; i++ {
		if a.fileRowCur == row {
			return a
		}
		a, _ = a.Step(keyPress("j"))
	}
	t.Fatalf("`j` never reached row %d — the cursor stopped at %d of %d rows:\n%+v",
		row, a.fileRowCur, len(a.fileRows), shapesOf(a.fileRows))
	return a
}

// diffIndexOfPath is the test's own lookup, written out rather than borrowed
// from the scan inside `App.selectRow` — an expectation derived from the code
// under test would agree with it however wrong that code was. 🔴 DO NOT
// "SIMPLIFY" THIS INTO A CALL TO THE IMPLEMENTATION; being a second,
// independent spelling is the whole point of it. (It was written against a
// PATH -> index map that `selectRow` has since stopped keeping, and it needed
// no change, which is the property working.)
func diffIndexOfPath(t *testing.T, a App, path string) int {
	t.Helper()
	for i, f := range a.Diff.Files {
		if f.Path == path {
			return i
		}
	}
	t.Fatalf("%q is in the Files panel and in no diff file", path)
	return -1
}

// 🔴 THE REGRESSION TEST FOR THE CORE HAZARD OF THIS CHANGE: A ROW REACHES ITS
// DIFF FILE BY PATH, NEVER BY AN INDEX.
//
// Before the tree, ONE integer indexed both `Snap.Files` and `Diff.Files`, which
// worked only while the two were positionally parallel. Grouping by directory
// re-orders rows relative to both lists, and the two lists come from two
// different endpoints and may disagree with each other as well. Every wrong
// mapping still opens SOME file's hunk and looks entirely correct on screen.
//
// The expectations below are LITERALS read off the fixture, and the failure
// message names what each rival mapping would have produced.
func TestTheFilesPanelResolvesItsDiffFileByPathAndNotByAnyIndex(t *testing.T) {
	a := treeApp(t)

	// row -> the file it shows, and that file's position in `Diff.Files`.
	cases := []struct {
		row       int
		path      string
		diffIndex int
	}{
		{2, "src/a/one.go", 3},
		{3, "src/a/two.go", 1},
		{5, "src/b/deep/three.go", 2},
		{6, "src/root.go", 0},
	}

	// 🔴 THE INSTRUMENT CHECK. For each row, work out what the rival mappings
	// would say, and require that at least one row separates each of them from
	// the truth. Without this, a fixture whose orders happened to coincide would
	// let an index-based implementation pass every assertion below.
	sepRow, sepFileOrdinal, sepSnapIndex := false, false, false
	for fileOrdinal, c := range cases {
		if c.row != c.diffIndex {
			sepRow = true
		}
		if fileOrdinal != c.diffIndex {
			sepFileOrdinal = true
		}
		snapIndex := -1
		for i, f := range a.Snap.Files {
			if f.Path == c.path {
				snapIndex = i
			}
		}
		if snapIndex != c.diffIndex {
			sepSnapIndex = true
		}
	}
	for _, s := range []struct {
		name string
		ok   bool
	}{
		{"the ROW ordinal", sepRow},
		{"the ordinal among FILE rows", sepFileOrdinal},
		{"the `Snap.Files` index", sepSnapIndex},
	} {
		if !s.ok {
			t.Fatalf("no row in this fixture separates the correct mapping from %s — "+
				"an index-based implementation would pass this test", s.name)
		}
	}

	// Walk the panel with `j`, the way the operator does, and check every file
	// row on the way down.
	for _, c := range cases {
		a = walkTo(t, a, c.row)
		if got := a.SelectedFilePath(); got != c.path {
			t.Fatalf("row %d shows %q, want %q — the FIXTURE has drifted", c.row, got, c.path)
		}
		want := a.Diff.FileStart(diffIndexOfPath(t, a, c.path))
		if a.diffCur != want {
			t.Errorf("selecting row %d (%s) put the diff cursor at line %d, want %d "+
				"— by the row ordinal that would be %d",
				c.row, c.path, a.diffCur, want, a.Diff.FileStart(c.row))
		}
		// And the diff really is inside that file, stated without `FileStart`
		// so a broken `FileStart` cannot satisfy both halves.
		if got := a.Diff.Files[a.Diff.FileAt(a.diffCur)].Path; got != c.path {
			t.Errorf("selecting row %d (%s) opened %q in the diff", c.row, c.path, got)
		}
	}
}

// 🔴 THE CROSS-PANEL INVARIANT, OVER EVERY FILE IN THE FIXTURE: moving the diff
// cursor into a file leaves that file's row BOTH highlighted AND visible —
// including when its directory started CLOSED. Spot-checking one file would
// miss exactly the file whose ancestors need opening.
func TestEveryFileIsRevealedAndHighlightedWhenTheDiffCursorEntersIt(t *testing.T) {
	a := treeApp(t)
	// Start with EVERY directory closed, which is the state that makes this
	// test about revealing rather than about highlighting.
	a.collapsedDirs = map[string]bool{"src": true, "src/a": true, "src/b/deep": true}
	a.rebuildFileRows()
	if len(a.fileRows) != 1 || !a.fileRows[0].IsDir {
		t.Fatalf("the fixture is not fully collapsed: %+v", shapesOf(a.fileRows))
	}
	a.Focus = PanelDiff

	// `g` puts the cursor on line 0, then `}` walks the files IN DIFF ORDER —
	// which is what those keys mean, and is deliberately not the tree's order.
	a, _ = a.Step(keyPress("g"))
	for i := range a.Diff.Files {
		if i > 0 {
			a, _ = a.Step(keyPress("}"))
		}
		want := a.Diff.Files[i].Path
		if got := a.Diff.Files[a.Diff.FileAt(a.diffCur)].Path; got != want {
			t.Fatalf("the diff cursor is in %q, want %q — the walk is wrong", got, want)
		}
		// HIGHLIGHTED: the Files cursor is on that file's row.
		if got := a.SelectedFilePath(); got != want {
			t.Errorf("the diff is showing %s and the Files panel has %q selected", want, got)
		}
		// VISIBLE: that row is in the flattened rows at all...
		found := -1
		for j, r := range a.fileRows {
			if !r.IsDir && r.Path == want {
				found = j
			}
		}
		if found < 0 {
			t.Errorf("%s has no visible row — its directory was never opened:\n%+v",
				want, shapesOf(a.fileRows))
			continue
		}
		if found != a.fileRowCur {
			t.Errorf("%s is at row %d and the cursor is on row %d", want, found, a.fileRowCur)
		}
		// ...and it is on the rendered screen, which is the claim the operator
		// would actually make.
		base := want[strings.LastIndex(want, "/")+1:]
		if body := stripANSI(a.filesBody(30, 20)); !strings.Contains(body, base) {
			t.Errorf("%s is not on the rendered Files panel:\n%s", want, body)
		}
	}

	// POSITIVE CONTROL ON THE REVEAL: the directories really were closed and
	// really did open, so "visible" above is not a fact about a tree that was
	// never collapsed.
	if len(a.collapsedDirs) != 0 {
		t.Errorf("%v is still collapsed after visiting every file", a.collapsedDirs)
	}
	if got := shapesOf(a.fileRows); !shapesEqual(got, treeFixtureRows) {
		t.Errorf("after the walk the tree is\n%+v\nwant fully expanded\n%+v",
			got, treeFixtureRows)
	}
}

// 🔴 A DIRECTORY ROW IS PURE NAVIGATION — LANDING ON ONE MUST NOT MOVE THE DIFF.
// A directory is not something the diff can show, so jerking the diff to its
// first file would make it impossible to walk PAST a directory while reading:
// every step through the tree would throw away the operator's place.
// 🔴 HOW THE CURSOR ARRIVES AT THE DIRECTORY ROW IS THE WHOLE TEST, AND AN
// EARLIER VERSION OF IT GOT THAT WRONG AND WAS VACUOUS. MEASURED: a mutant that
// made a directory row jump the diff to its FIRST DESCENDANT FILE survived a
// fully green suite, because that draft stepped UP onto `src/a` from
// `src/a/one.go` — which IS `src/a`'s first descendant, so the wrong answer and
// the right answer were the same number. Every case below therefore lands on a
// directory whose first descendant is NOT the file the diff is currently in,
// and each one asserts that about itself before asserting anything else.
func TestLandingOnADirectoryRowLeavesTheDiffCursorByteIdentical(t *testing.T) {
	// firstDescendantOf is the file a "jump to the directory's contents"
	// implementation would pick — the rival this test must be able to see.
	firstDescendantOf := func(a App, dir string) string {
		for _, r := range a.fileRows {
			if !r.IsDir && strings.HasPrefix(r.Path, dir+"/") {
				return r.Path
			}
		}
		return ""
	}

	for _, c := range []struct {
		name string
		from int    // the FILE row the cursor starts on
		key  string // the key that lands it on a directory row
		dir  string // the directory row it must land on
		file string // the file the diff must STAY in
	}{
		{"stepping DOWN onto src/b/deep", 3, "j", "src/b/deep", "src/a/two.go"},
		{"jumping to the TOP onto src", 3, "g", "src", "src/a/two.go"},
		{"paging UP onto src", 6, "ctrl+u", "src", "src/root.go"},
	} {
		t.Run(c.name, func(t *testing.T) {
			a := walkTo(t, treeApp(t), c.from)
			if got := a.SelectedFilePath(); got != c.file {
				t.Fatalf("row %d shows %q, want %q", c.from, got, c.file)
			}
			diffBefore, offBefore := a.diffCur, a.vp.YOffset()

			// 🔴 THE INSTRUMENT CHECK, PER CASE.
			if rival := firstDescendantOf(a, c.dir); rival == c.file {
				t.Fatalf("%s's first descendant IS %s — landing on it cannot tell a "+
					"directory row that leaves the diff alone from one that jumps to "+
					"its first file", c.dir, c.file)
			}

			next, intents := a.Step(keyPress(c.key))
			if len(intents) != 0 {
				t.Errorf("moving onto a directory row emitted %v", intents)
			}
			r, ok := next.currentRow()
			if !ok || !r.IsDir || r.Path != c.dir {
				t.Fatalf("%q landed on %+v, want the %s directory row", c.key, r, c.dir)
			}
			if next.diffCur != diffBefore {
				t.Errorf("landing on the %s row moved the diff cursor from %d to %d "+
					"— a directory is pure navigation, and jumping the diff would "+
					"throw away the operator's place every time they walked past one",
					c.dir, diffBefore, next.diffCur)
			}
			if got := next.vp.YOffset(); got != offBefore {
				t.Errorf("landing on the %s row scrolled the diff viewport from %d to %d",
					c.dir, offBefore, got)
			}
			// And the diff is still showing the same FILE, stated in the
			// operator's terms rather than as a line number.
			if got := next.Diff.Files[next.Diff.FileAt(next.diffCur)].Path; got != c.file {
				t.Errorf("landing on the %s row left the diff showing %q, want %q",
					c.dir, got, c.file)
			}

			// 🔴 POSITIVE CONTROL, IN THE SAME SUB-TEST. A `selectRow` wired to
			// nothing would satisfy every assertion above. Moving onto a
			// different FILE row MUST move the diff.
			onto := walkTo(t, next, 5) // src/b/deep/three.go
			if got := onto.SelectedFilePath(); got != "src/b/deep/three.go" {
				t.Fatalf("the control step selected %q", got)
			}
			if onto.diffCur == diffBefore {
				t.Fatal("moving onto a DIFFERENT file row did not move the diff either " +
					"— the assertions above are about a cross-panel link that is dead")
			}
		})
	}
}

// 🔴 EACH TREE KEY, ON EACH KIND OF ROW, WITH LITERAL EXPECTATIONS.
func TestTheTreeKeysExpandCollapseAndToggle(t *testing.T) {
	// rowAt walks the cursor down to `row` with `j` from a fresh fixture.
	rowAt := func(t *testing.T, row int) App {
		t.Helper()
		return walkTo(t, treeApp(t), row)
	}

	t.Run("h collapses an open directory and l opens it again", func(t *testing.T) {
		a := rowAt(t, 1) // src/a, open
		closed, intents := a.Step(keyPress("h"))
		if len(intents) != 0 {
			t.Errorf("`h` emitted %v — collapsing is local", intents)
		}
		want := []rowShape{
			{0, "dir-open", "src", 46, 11},
			{1, "dir-closed", "a", 16, 5},
			{1, "dir-open", "b/deep", 23, 6},
			{2, "RENAMED", "three.go", 23, 6},
			{1, "ADDED", "root.go", 7, 0},
		}
		if got := shapesOf(closed.fileRows); !shapesEqual(got, want) {
			t.Errorf("after `h` on src/a the rows are\n%+v\nwant\n%+v", got, want)
		}
		// The cursor stays ON the directory it just closed.
		if r, _ := closed.currentRow(); r.Path != "src/a" {
			t.Errorf("`h` left the cursor on %q, want src/a", r.Path)
		}
		// ...and `l` puts it back, exactly.
		reopened, _ := closed.Step(keyPress("l"))
		if got := shapesOf(reopened.fileRows); !shapesEqual(got, treeFixtureRows) {
			t.Errorf("after `l` the rows are\n%+v\nwant\n%+v", got, treeFixtureRows)
		}
		// ⚠ AND NEITHER KEY TOUCHED THE DIFF. Opening and closing directories
		// is navigation; the diff stays where the operator left it.
		if closed.diffCur != a.diffCur || reopened.diffCur != a.diffCur {
			t.Errorf("collapse/expand moved the diff cursor: %d -> %d -> %d",
				a.diffCur, closed.diffCur, reopened.diffCur)
		}
	})

	t.Run("the arrow keys are the same two actions", func(t *testing.T) {
		a := rowAt(t, 1)
		byLetter, _ := a.Step(keyPress("h"))
		byArrow, _ := a.Step(keyPress("left"))
		if !shapesEqual(shapesOf(byLetter.fileRows), shapesOf(byArrow.fileRows)) {
			t.Errorf("`h` and `←` disagree:\n%+v\nvs\n%+v",
				shapesOf(byLetter.fileRows), shapesOf(byArrow.fileRows))
		}
		openByLetter, _ := byLetter.Step(keyPress("l"))
		openByArrow, _ := byArrow.Step(keyPress("right"))
		if !shapesEqual(shapesOf(openByLetter.fileRows), shapesOf(openByArrow.fileRows)) {
			t.Error("`l` and `→` disagree")
		}
	})

	t.Run("h on a FILE row selects its parent directory", func(t *testing.T) {
		a := rowAt(t, 2) // src/a/one.go, depth 2
		up, _ := a.Step(keyPress("h"))
		if up.fileRowCur != 1 {
			t.Errorf("`h` on src/a/one.go -> row %d, want 1 (the src/a row)", up.fileRowCur)
		}
		if r, _ := up.currentRow(); r.Path != "src/a" || !r.IsDir {
			t.Errorf("`h` landed on %+v, want the src/a directory row", r)
		}
		// 🔴 IT STEPS OUT, IT DOES NOT CLOSE. src/a must still be open, or the
		// key would mean two things on one press.
		if !up.fileRows[1].Expanded {
			t.Error("`h` on a file row CLOSED its parent as well as selecting it")
		}
		// And it did not move the diff — it landed on a directory row.
		if up.diffCur != a.diffCur {
			t.Errorf("`h` onto the parent row moved the diff cursor to %d", up.diffCur)
		}

		// A DEPTH-1 file row steps out to the top-level directory.
		root := rowAt(t, 6) // src/root.go, depth 1
		outer, _ := root.Step(keyPress("h"))
		if r, _ := outer.currentRow(); r.Path != "src" {
			t.Errorf("`h` on src/root.go landed on %q, want src", r.Path)
		}
	})

	t.Run("h on a CLOSED directory steps out to its parent", func(t *testing.T) {
		a := rowAt(t, 1)
		closed, _ := a.Step(keyPress("h")) // src/a is now closed, cursor on it
		out, _ := closed.Step(keyPress("h"))
		if r, _ := out.currentRow(); r.Path != "src" {
			t.Errorf("a second `h` landed on %q, want src", r.Path)
		}
		if out.fileRows[1].Expanded {
			t.Error("stepping out re-opened src/a")
		}
	})

	t.Run("h at the top level is inert", func(t *testing.T) {
		a := treeApp(t) // row 0 is `src`, depth 0, and it is open
		closed, _ := a.Step(keyPress("h"))
		if closed.fileRowCur != 0 {
			t.Errorf("`h` on the top row moved to %d", closed.fileRowCur)
		}
		stay, _ := closed.Step(keyPress("h")) // now closed; there is no parent
		if stay.fileRowCur != 0 {
			t.Errorf("`h` on a closed top-level row moved to %d", stay.fileRowCur)
		}
		if !shapesEqual(shapesOf(stay.fileRows), shapesOf(closed.fileRows)) {
			t.Error("`h` at the top level changed the rows")
		}
	})

	t.Run("l on an already-open directory is inert", func(t *testing.T) {
		a := rowAt(t, 1)
		same, intents := a.Step(keyPress("l"))
		if len(intents) != 0 {
			t.Errorf("`l` emitted %v", intents)
		}
		if same.fileRowCur != a.fileRowCur || same.Focus != PanelFiles {
			t.Errorf("`l` on an open directory moved to row %d / panel %v",
				same.fileRowCur, same.Focus)
		}
		if got := shapesOf(same.fileRows); !shapesEqual(got, treeFixtureRows) {
			t.Error("`l` on an open directory changed the rows")
		}
	})

	t.Run("l on a FILE row focuses the Diff panel", func(t *testing.T) {
		a := rowAt(t, 2)
		open, intents := a.Step(keyPress("l"))
		if len(intents) != 0 {
			t.Errorf("`l` emitted %v — focusing a panel is local", intents)
		}
		if open.Focus != PanelDiff {
			t.Errorf("`l` on a file row left focus on %v, want the Diff panel", open.Focus)
		}
		// It opens THIS file: the diff cursor stays in src/a/one.go.
		if got := open.Diff.Files[open.Diff.FileAt(open.diffCur)].Path; got != "src/a/one.go" {
			t.Errorf("`l` on the src/a/one.go row left the diff in %q", got)
		}
		if open.fileRowCur != a.fileRowCur {
			t.Errorf("`l` moved the Files cursor to %d", open.fileRowCur)
		}
	})

	t.Run("enter toggles a directory both ways", func(t *testing.T) {
		a := rowAt(t, 1)
		closed, intents := a.Step(keyPress("enter"))
		if len(intents) != 0 {
			t.Errorf("`enter` emitted %v", intents)
		}
		if closed.fileRows[1].Expanded {
			t.Error("`enter` on an open directory did not close it")
		}
		reopened, _ := closed.Step(keyPress("enter"))
		if !reopened.fileRows[1].Expanded {
			t.Error("a second `enter` did not re-open it")
		}
		if got := shapesOf(reopened.fileRows); !shapesEqual(got, treeFixtureRows) {
			t.Errorf("enter/enter did not return to the opening state:\n%+v", got)
		}
	})

	t.Run("enter on a FILE row focuses the Diff panel", func(t *testing.T) {
		a := rowAt(t, 5) // src/b/deep/three.go
		open, _ := a.Step(keyPress("enter"))
		if open.Focus != PanelDiff {
			t.Errorf("`enter` on a file row left focus on %v", open.Focus)
		}
		if got := open.Diff.Files[open.Diff.FileAt(open.diffCur)].Path; got != "src/b/deep/three.go" {
			t.Errorf("`enter` left the diff in %q", got)
		}
	})

	t.Run("the tree keys are inert outside the Files panel", func(t *testing.T) {
		for _, p := range []Panel{PanelOverview, PanelCommits, PanelDiff} {
			for _, k := range []string{"h", "l", "left", "right", "enter"} {
				a := treeApp(t)
				a.Focus = p
				next, intents := a.Step(keyPress(k))
				if len(intents) != 0 {
					t.Errorf("%q in %s emitted %v", k, p.Title(), intents)
				}
				if next.Focus != p {
					t.Errorf("%q in %s changed focus to %v", k, p.Title(), next.Focus)
				}
				if next.fileRowCur != a.fileRowCur {
					t.Errorf("%q in %s moved the Files cursor to %d",
						k, p.Title(), next.fileRowCur)
				}
				if !shapesEqual(shapesOf(next.fileRows), shapesOf(a.fileRows)) {
					t.Errorf("%q in %s changed the tree", k, p.Title())
				}
			}
		}
	})

	// 🔴 AND THEY DO NOT REACH COMPOSE MODE. `enter`, `left` and `right` are all
	// bound in the compose table; the tables are disjoint, and this is the
	// assertion that says so in behaviour rather than in a comment.
	t.Run("compose mode still owns enter, left and right", func(t *testing.T) {
		a, _ := pressAll(treeApp(t), "c", "a", "b")
		if a.Mode() != ModeCompose {
			t.Fatalf("mode = %s, want COMPOSING", a.Mode().Word())
		}
		nl, _ := a.Step(keyPress("enter"))
		if got := nl.ComposeBody(); got != "ab\n" {
			t.Errorf("`enter` while composing produced %q, want %q", got, "ab\n")
		}
		if nl.Focus != a.Focus {
			t.Errorf("`enter` while composing changed focus to %v", nl.Focus)
		}
		left, _ := a.Step(keyPress("left"))
		if left.compose.Cur != 1 {
			t.Errorf("`←` while composing put the compose cursor at %d, want 1", left.compose.Cur)
		}
		if left.fileRowCur != a.fileRowCur {
			t.Errorf("`←` while composing moved the FILES cursor to %d", left.fileRowCur)
		}
	})
}

// 🔴 THE NEW BINDINGS ARE IN THE LEGEND, SPELLED OUT — the defect this keymap
// arc exists to fix is an invisible binding. Asserted by their LITERAL rendered
// words, like `J`/`K` above, rather than by echoing `Keys.TreeExpand.Help()`
// back at itself: that would pass against an empty legend and an empty binding
// alike.
//
// ⚠ THE EXPANDED LEGEND, NOT THE PERSISTENT FOOTER. The browse row already
// renders 146 columns and overflows a 140-column terminal; `J`/`K` set the
// precedent that this class of key lives behind `?`.
func TestTheLegendCarriesTheTreeBindings(t *testing.T) {
	a := treeApp(t)
	a.Width, a.Height = 140, 40
	a.relayout()

	full, intents := a.Step(keyPress("?"))
	if len(intents) != 0 {
		t.Errorf("`?` emitted %v", intents)
	}
	if !full.showFull {
		t.Fatal("`?` did not expand the help")
	}
	legend := stripANSI(full.renderFooter())

	for _, want := range []string{
		"l/→", "expand dir / open file",
		"h/←", "collapse dir / go to parent",
		"enter", "toggle dir",
	} {
		if !strings.Contains(legend, want) {
			t.Errorf("the expanded legend does not carry %q\nlegend:\n%s", want, legend)
		}
	}
	// POSITIVE CONTROL for the matcher: a phrase in no binding must be absent.
	if strings.Contains(legend, "expand every directory") {
		t.Error("the legend matcher matches text that is in no binding")
	}
	// ⚠ AND NOT IN THE PERSISTENT FOOTER, which is where the width budget is.
	short := stripANSI(a.renderFooter())
	if strings.Contains(short, "toggle dir") {
		t.Error("a tree binding landed in the persistent footer, which already overflows")
	}
}
