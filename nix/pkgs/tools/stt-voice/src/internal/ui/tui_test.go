package ui

import (
	"errors"
	"strings"
	"testing"

	"charm.land/bubbles/v2/key"
	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/stt-voice/internal/effects"
	"github.com/innovation-upstream/devrc/stt-voice/internal/history"
)

// fakeFX records every call — the interface the TUI acts through, so no test
// can reach a real X server.
type fakeFX struct {
	copied   []string
	focused  []int
	typed    []string
	failCopy bool
	failType bool
	failFoc  bool
}

func (f *fakeFX) Copy(text string) error {
	if f.failCopy {
		return errors.New("xclip dead")
	}
	f.copied = append(f.copied, text)
	return nil
}
func (f *fakeFX) Focus(id int) error {
	if f.failFoc {
		return errors.New("window gone")
	}
	f.focused = append(f.focused, id)
	return nil
}
func (f *fakeFX) Type(text string) error {
	if f.failType {
		return errors.New("xdotool dead")
	}
	f.typed = append(f.typed, text)
	return nil
}
func (f *fakeFX) CaptureActive() (int, error) { return 0, errors.New("never called in tests") }
func (f *fakeFX) Toast(msg string)            {}

func entries() []history.Entry {
	// Load order: oldest → newest (three of them)
	return []history.Entry{
		{ID: 101, TS: 1, Text: "oldest transcript"},
		{ID: 102, TS: 2, Text: "middle transcript"},
		{ID: 103, TS: 3, Text: "newest transcript\nwith a line"},
	}
}

func model(t *testing.T) (Model, *fakeFX) {
	t.Helper()
	fx := &fakeFX{}
	return NewWithEffects(entries(), 103, 9465926, fx), fx
}

// keyPress builds a v2 key message the same way mention-review's keys_test.go
// does — through the real matcher shapes, never a second translation. It
// PANICS on an unknown spelling: a zero KeyPressMsg matches nothing, so a
// silent fallback would make every assertion about that key vacuous.
func keyPress(s string) tea.KeyPressMsg {
	switch s {
	case "up":
		return tea.KeyPressMsg{Code: tea.KeyUp}
	case "down":
		return tea.KeyPressMsg{Code: tea.KeyDown}
	case "esc":
		return tea.KeyPressMsg{Code: tea.KeyEscape}
	case "enter":
		return tea.KeyPressMsg{Code: tea.KeyEnter}
	case "ctrl+enter":
		return tea.KeyPressMsg{Code: tea.KeyEnter, Mod: tea.ModCtrl}
	}
	if r := []rune(s); len(r) == 1 {
		return tea.KeyPressMsg{Code: r[0], Text: s}
	}
	panic("keyPress: unhandled spelling " + s)
}

func step(m Model, s string) (Model, []Intent) { return m.Step(keyPress(s)) }

func TestTheTUIOpensOnTheNewestEntry(t *testing.T) {
	m, _ := model(t)
	if got := m.entries[m.idx].Text; got != "newest transcript\nwith a line" {
		t.Fatalf("opens on %q", got)
	}
}

func TestTheTUIOpensOnTheRequestedEntryWhenGiven(t *testing.T) {
	m := NewWithEffects(entries(), 101, 5, &fakeFX{})
	if got := m.entries[m.idx].Text; got != "oldest transcript" {
		t.Fatalf("opens on %q, want the --entry id", got)
	}
}

func TestUpDownBrowseHistoryAndClamp(t *testing.T) {
	m, _ := model(t)
	next, _ := step(m, "up")
	if next.idx != 1 {
		t.Fatalf("up did not go older: idx=%d", next.idx)
	}
	next, _ = step(next, "up")
	if next.idx != 2 {
		t.Fatalf("up again: idx=%d", next.idx)
	}
	next, _ = step(next, "up") // clamped at the oldest
	if next.idx != 2 {
		t.Fatalf("up past the oldest moved: idx=%d", next.idx)
	}
	next, _ = step(next, "down")
	next, _ = step(next, "down")
	if next.idx != 0 {
		t.Fatalf("down past the newest moved: idx=%d", next.idx)
	}
	// browsing MOVES THE VIEW: the render must follow the cursor
	if !strings.Contains(next.View().Content, "newest transcript") {
		t.Fatal("the render does not follow the history cursor")
	}
}

func TestCopyProducesACopyIntentAndStaysOpen(t *testing.T) {
	m, fx := model(t)
	next, intents := step(m, "c")
	if len(intents) != 1 {
		t.Fatalf("`c` produced %d intents", len(intents))
	}
	ct, ok := intents[0].(CopyText)
	if !ok || ct.Text != "newest transcript\nwith a line" {
		t.Fatalf("copy intent = %+v (the transcript, not the stripped form)", intents[0])
	}
	if next.quitting {
		t.Fatal("`c` quit the TUI — copy must leave it open to also send")
	}
	// and the intent really does copy, through the interface
	if msg := Run(intents[0], fx)(); msg != (Copied{}) {
		t.Fatalf("copy cmd returned %+v", msg)
	}
	if len(fx.copied) != 1 || fx.copied[0] != "newest transcript\nwith a line" {
		t.Fatalf("fakeFX saw %v", fx.copied)
	}
}

func TestSendStripsNewlinesAndUsesTheCapturedTarget(t *testing.T) {
	m, fx := model(t)
	for _, spelling := range []string{"t", "ctrl+enter"} {
		fx.typed, fx.focused = nil, nil
		next, intents := step(m, spelling)
		if len(intents) != 1 {
			t.Fatalf("%s produced %d intents", spelling, len(intents))
		}
		st, ok := intents[0].(SendText)
		if !ok {
			t.Fatalf("%s intent = %T", spelling, intents[0])
		}
		// 🔴 THE WHOLE POINT: the TYPED text carries no Return. The stored
		// transcript does.
		if st.Text != "newest transcript with a line" {
			t.Fatalf("%s sends %q — a Return would submit a form", spelling, st.Text)
		}
		if st.Target != 9465926 {
			t.Fatalf("%s targets %d", spelling, st.Target)
		}
		if msg := Run(intents[0], fx)(); msg != (Sent{}) {
			t.Fatalf("%s cmd returned %+v", spelling, msg)
		}
		if len(fx.focused) != 1 || fx.focused[0] != 9465926 {
			t.Fatalf("%s focus calls: %v", spelling, fx.focused)
		}
		if len(fx.typed) != 1 || fx.typed[0] != "newest transcript with a line" {
			t.Fatalf("%s type calls: %v", spelling, fx.typed)
		}
		// closing happens in Update(Sent), after the effects, not in Step
		after, _ := next.Update(msgOf(intents[0], fx))
		if !after.(Model).quitting {
			t.Fatalf("%s did not close the TUI after a successful send", spelling)
		}
	}
}

// msgOf runs one intent through the interface and returns the message it
// produced (the same call Update performs inside the bubbletea loop).
func msgOf(i Intent, fx *fakeFX) tea.Msg { return Run(i, fx)() }

func TestSendWithNoCapturedTargetRefusesInWords(t *testing.T) {
	m := NewWithEffects(entries(), 103, 0, &fakeFX{})
	next, intents := step(m, "t")
	if len(intents) != 0 {
		t.Fatalf("target 0 produced intents %v — typing into whatever has focus is the wrong-window leak", intents)
	}
	if next.notice == "" {
		t.Fatal("target 0 refused silently")
	}
	if next.quitting {
		t.Fatal("a refused send must leave the TUI open (the notice must be readable)")
	}
}

func TestEscClosesWithoutActing(t *testing.T) {
	m, fx := model(t)
	next, intents := step(m, "esc")
	if len(intents) != 0 {
		t.Fatalf("esc produced intents %v", intents)
	}
	if !next.quitting {
		t.Fatal("esc did not close")
	}
	if len(fx.copied) != 0 || len(fx.typed) != 0 || len(fx.focused) != 0 {
		t.Fatal("esc acted on the world")
	}
}

func TestAnUnboundKeyIsANoOp(t *testing.T) {
	m, _ := model(t)
	next, intents := step(m, "z")
	if len(intents) != 0 || next.quitting || next.idx != m.idx {
		t.Fatalf("an unbound key did something: %v", intents)
	}
}

func TestEverySpellingInTheDispatchTableIsBuildableAndMatches(t *testing.T) {
	// the instrument check: without it, adding a binding with a spelling the
	// helper cannot build silently removes that key from every test above
	built := 0
	for _, b := range newKeyMap().dispatch() {
		if len(b.Keys()) == 0 {
			t.Errorf("binding %q has no keys", b.Help().Desc)
		}
		h := b.Help()
		if h.Key == "" || h.Desc == "" {
			t.Errorf("binding has empty help: %+v", h)
		}
		for _, k := range b.Keys() {
			msg := keyPress(k) // panics on an unknown spelling
			if !key.Matches(msg, b) {
				t.Errorf("keyPress(%q) does not match its own binding", k)
			}
			built++
		}
	}
	if built == 0 {
		t.Fatal("the dispatch table is empty — this check observed nothing")
	}
}

func TestNoKeyIsClaimedByTwoBindings(t *testing.T) {
	owner := map[string]string{}
	for _, b := range newKeyMap().dispatch() {
		for _, k := range b.Keys() {
			if prev, dup := owner[k]; dup {
				t.Errorf("key %q is claimed by both %q and %q — the second is dead", k, prev, b.Help().Desc)
			}
			owner[k] = b.Help().Desc
		}
	}
}

func TestTheFooterIsGeneratedFromTheKeymap(t *testing.T) {
	m, _ := model(t)
	m.Width = 100
	got := m.View().Content
	for _, b := range newKeyMap().dispatch() {
		h := b.Help()
		if !strings.Contains(got, h.Key) || !strings.Contains(got, h.Desc) {
			t.Errorf("footer is missing %q %q\n%s", h.Key, h.Desc, got)
		}
	}
	if strings.Contains(got, "definitely-not-a-binding") {
		t.Error("the footer matcher matches text in no binding")
	}
}

func TestTheViewCarriesTheMetaAndTheRTFx(t *testing.T) {
	m, _ := model(t)
	m.entries[m.idx].DurationS = 4.2
	m.entries[m.idx].ElapsedS = 0.9
	got := m.View().Content
	for _, want := range []string{"audio 4.2s", "transcribed in 0.9s", "RTFx 4.7", "1/3"} {
		if !strings.Contains(got, want) {
			t.Errorf("the view is missing %q\n%s", want, got)
		}
	}
}

func TestTheRTFxRefusesADivisionByZero(t *testing.T) {
	m, _ := model(t)
	m.entries[m.idx].ElapsedS = 0
	if strings.Contains(m.View().Content, "RTFx +Inf") || strings.Contains(m.View().Content, "RTFx NaN") {
		t.Fatal("RTFx of a zero elapsed is not a number on the screen")
	}
	if !strings.Contains(m.View().Content, "RTFx —") {
		t.Fatal("RTFx of an unmeasurable elapsed must render a dash")
	}
}

func TestWrapFitsTheWidth(t *testing.T) {
	long := strings.Repeat("word ", 60)
	for _, line := range strings.Split(wrap(long, 40), "\n") {
		if len(line) > 40 {
			t.Fatalf("a wrapped line is %d wide: %q", len(line), line)
		}
	}
	if got := wrap("one\ntwo", 40); got != "one\ntwo" {
		t.Fatalf("wrap lost a paragraph break: %q", got)
	}
}

func TestUpdateCarriesTheEffectsOutcomeIntoTheView(t *testing.T) {
	m, fx := model(t)
	next, intents := step(m, "c")
	cmds := RunAll(intents, fx)
	if len(cmds) != 1 {
		t.Fatalf("copy produced %d cmds", len(cmds))
	}
	after, _ := next.Update(cmds[0]()) // run the real cmd → Copied msg
	am := after.(Model)
	if am.quitting {
		t.Fatal("a successful copy quit the TUI")
	}
	if !strings.Contains(am.View().Content, "copied to clipboard") {
		t.Fatalf("the view does not report the copy\n%s", am.View().Content)
	}
}

func TestUpdateReportsAFailedSendInsteadOfQuitting(t *testing.T) {
	m, fx := model(t)
	fx.failType = true
	next, intents := step(m, "t")
	cmd := RunAll(intents, fx)[0]
	after, _ := next.Update(cmd())
	am := after.(Model)
	if am.quitting {
		t.Fatal("a FAILED send quit the TUI — the operator would never learn why nothing was typed")
	}
	if !strings.Contains(am.View().Content, "send failed") {
		t.Fatalf("the view does not report the failure\n%s", am.View().Content)
	}
}

func TestRunPanicsOnAnUnwiredIntent(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("Run accepted an intent nobody wired — it would be a keypress that does nothing forever")
		}
	}()
	Run(ghostIntent{}, &fakeFX{})
}

type ghostIntent struct{}

func (ghostIntent) intentName() string { return "ghost" }

// The effects bridge must not be able to type a Return: the StripNewlines is
// applied at the INTENT, so even a caller bypassing it is caught here.
func TestTheSendIntentFactoryIsTheOnlyTypePath(t *testing.T) {
	m, _ := model(t)
	m.entries[m.idx].Text = "multi\nline\ntranscript"
	_, intents := step(m, "t")
	st := intents[0].(SendText)
	if effects.StripNewlines(m.entries[m.idx].Text) != st.Text {
		t.Fatalf("the send intent did not go through StripNewlines: %q", st.Text)
	}
}
