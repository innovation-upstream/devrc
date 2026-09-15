package ui

import (
	"strings"
	"testing"
)

// 🔴 THE FRAME NEVER EXCEEDS THE TERMINAL, IN ANY MODE.
//
// ⚠ THIS GUARD EXISTS BECAUSE THE FIRST DRAFT OF THE WRITE BAR WAS BROKEN AND
// EVERY OTHER TEST PASSED. `render` subtracted the bar's height from the body,
// and `relayout` — which is what actually SIZES the diff viewport — did not. So
// opening a compose buffer left the viewport sized for a bar-less frame and
// pushed the bottom rows off the bottom of the terminal. Every assertion in
// this package is about STATE or about a substring of the frame; not one of
// them could see it, because the state was correct and the substring was
// present. Only counting the frame's LINES finds it.
//
// 🔴 IT MEASURES AT TWO HEIGHTS, NOT ONE. The layout branches on available
// space (`max(minHeight, …)`, `max(7, h/3)`), so a claim made at one size is
// not a claim about the other — and the interesting arm is the SHORT terminal,
// where the bar competes with a body that already has a floor.
func TestTheRenderedFrameFitsTheTerminalInEveryMode(t *testing.T) {
	sizes := []struct{ w, h int }{
		{140, 40}, // the roomy case
		{100, 30}, // New()'s own default, and a plausible real window
	}
	cases := []struct {
		name string
		keys []string
		mode Mode
	}{
		{"browse", nil, ModeBrowse},
		{"notice", []string{"m", "n"}, ModeBrowse},
		{"composing", []string{"c"}, ModeCompose},
		{"composing-multiline", []string{"c", "a", "enter", "b", "enter", "c", "enter", "d", "enter", "e", "enter", "f"}, ModeCompose},
		{"confirming", []string{"m"}, ModeConfirm},
	}
	for _, sz := range sizes {
		for _, c := range cases {
			t.Run(c.name, func(t *testing.T) {
				a := ready(t)
				a.Width, a.Height = sz.w, sz.h
				a.relayout()
				a, _ = pressAll(a, c.keys...)
				if a.Mode() != c.mode {
					t.Fatalf("fixture is wrong: mode = %s, want %s", a.Mode().Word(), c.mode.Word())
				}
				frame := stripANSI(a.render())
				got := len(strings.Split(frame, "\n"))
				if got > sz.h {
					t.Errorf("at %dx%d in mode %s the frame is %d lines, which is %d "+
						"more than the terminal has — the bottom rows are pushed off screen",
						sz.w, sz.h, c.mode.Word(), got, got-sz.h)
				}
				// 🔴 POSITIVE CONTROL ON THE MEASUREMENT. A `render` that
				// returned "" would satisfy the assertion above at every size
				// and in every mode. The frame must be substantially there.
				if got < 5 {
					t.Fatalf("at %dx%d the frame is only %d lines — this guard is "+
						"measuring an empty render", sz.w, sz.h, got)
				}
			})
		}
	}
}

// 🔴 THE COMPOSE BAR'S HEIGHT DOES NOT MOVE AS THE OPERATOR TYPES. That is the
// property `relayout` depends on: it is called when compose OPENS, so a bar
// that grew afterwards would invalidate the layout it computed, one line per
// newline, with nothing to recompute it.
func TestTheComposeBarHeightIsConstantWhileTyping(t *testing.T) {
	a, _ := pressAll(ready(t), "c")
	first := strings.Count(a.renderBar(), "\n")
	for i := 0; i < 12; i++ {
		a, _ = pressAll(a, "x", "enter")
		if got := strings.Count(a.renderBar(), "\n"); got != first {
			t.Fatalf("after %d newlines the compose bar is %d lines, was %d — "+
				"the layout computed when compose opened is now wrong by %d rows",
				i+1, got+1, first+1, got-first)
		}
	}
	// POSITIVE CONTROL: the buffer really did grow, so the constancy above is
	// a property of the RENDER and not of a compose buffer that ignored the keys.
	if n := strings.Count(a.ComposeBody(), "\n"); n < 12 {
		t.Fatalf("the buffer holds only %d newlines — the typing never landed, so "+
			"this test proved nothing", n)
	}
}

// The window follows the cursor, so the end of a long comment is what is on
// screen. ⚠ Asserted on the LAST line typed, because that is the one the
// operator is looking at and the one a naive "first N lines" window would hide.
func TestTheComposeWindowFollowsTheCursor(t *testing.T) {
	a, _ := pressAll(ready(t), "c")
	for _, r := range []string{"1", "2", "3", "4", "5", "6", "7"} {
		a, _ = pressAll(a, r, "enter")
	}
	a, _ = pressAll(a, "8")

	// ⚠ THE BODY ROWS ONLY, NOT THE WHOLE BAR. An earlier version of this test
	// searched the whole bar for "1" and failed on the `#1559` in the HEADER —
	// a guard on a CHARACTER, red for a reason that had nothing to do with the
	// window. The header is row 0 by construction; the window is what follows.
	rows := strings.Split(stripANSI(a.renderBar()), "\n")
	if len(rows) < 2 {
		t.Fatalf("the compose bar has %d rows — this guard is measuring nothing", len(rows))
	}
	body := strings.Join(rows[1:], "\n")
	if !strings.Contains(body, "8") {
		t.Errorf("the compose bar does not show the line being typed:\n%s", body)
	}
	if strings.Contains(body, "1") {
		t.Errorf("the compose bar still shows the FIRST line after 8 — the window "+
			"is not following the cursor:\n%s", body)
	}
}
