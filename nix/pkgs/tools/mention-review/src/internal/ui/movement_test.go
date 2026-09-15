package ui

import (
	"fmt"
	"strings"
	"testing"
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
