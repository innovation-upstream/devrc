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
	// other direction.
	if next.fileCur != 1 {
		t.Errorf("} left fileCur = %d, want 1", next.fileCur)
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
// `diffCur`, `fileCur` and `commitCur` exactly where they were. A version wired
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
	scrollFixtureDiffCur   = 7
	scrollFixtureFileCur   = 1
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
	a.fileCur = scrollFixtureFileCur
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
type cursors struct{ commit, file, diff int }

func cursorsOf(a App) cursors {
	return cursors{commit: a.commitCur, file: a.fileCur, diff: a.diffCur}
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
		file:   scrollFixtureFileCur,
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
	// `J` touches nothing, so the fixture's `fileCur` survives.
	if lower.fileCur != 0 {
		t.Errorf("j left fileCur = %d, want 0 (the big fixture has one file)", lower.fileCur)
	}
	if upper.fileCur != scrollFixtureFileCur {
		t.Errorf("J left fileCur = %d, want %d unchanged", upper.fileCur, scrollFixtureFileCur)
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
