package ui

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/oc-sent-tui/internal/sent"
)

func fixture(n int) sent.Data {
	d := sent.Data{
		Session: sent.Session{ID: "ses_1", Title: "Research something"},
		Messages: []sent.Message{
			{EpochMs: 1, TS: "2026-09-30 21:28", Agent: "build", Text: "first prompt"},
			{EpochMs: 2, TS: "2026-09-30 21:53", Agent: "build", Text: "second prompt"},
		},
	}
	for i := 0; i < n-2; i++ {
		d.Messages = append(d.Messages, sent.Message{
			EpochMs: int64(i + 3), TS: "2026-10-01 10:00",
			Agent: "build", Text: strings.Repeat("word ", 40) + "prompt",
		})
	}
	return d
}

// keyPress builds a v2 key message the same way stt-voice's tui_test.go does
// — through the real matcher shapes, never a second translation. It PANICS
// on an unknown spelling: a zero KeyPressMsg matches nothing, so a silent
// fallback would make every assertion about that key vacuous (measured here:
// `press(m, "enter")` built by rune('e') pressed the letter e, and the
// expand test went green-for-the-wrong-reason).
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
	case "ctrl+c":
		// mention-review's keyPress shape: ctrl+<letter> is the letter with
		// ModCtrl (v2 has no KeyCtrl* constants — the upgrade guide names
		// msg.String()/Code+Mod as the spellings)
		return tea.KeyPressMsg{Code: 'c', Mod: tea.ModCtrl}
	}
	if r := []rune(s); len(r) == 1 {
		return tea.KeyPressMsg{Code: r[0], Text: s}
	}
	panic("keyPress: unhandled spelling " + s)
}

// press runs one keypress through Update.
func press(m Model, key string) Model {
	next, _ := m.Update(keyPress(key))
	return next.(Model)
}

// 🔴 KeyPressMsg construction: the tests build the msg by CODE so the
// spellings the keyMap accepts ("G" vs "shift+g") are exercised against what
// bubbletea v2 actually reports for that key — a test that builds the msg
// from the SAME string the binding names would pin nothing about the key
// itself. Run the suite with -run TestKeySpellingsReachTheBindings locally
// against a real terminal if a key stops working.

func TestSelectionMovement(t *testing.T) {
	m := New(fixture(4), 100, 30)
	if m.idx != 0 {
		t.Fatalf("starts at 0, got %d", m.idx)
	}
	m = press(m, "j")
	if m.idx != 1 {
		t.Fatalf("j moves down, got %d", m.idx)
	}
	m = press(m, "k")
	m = press(m, "k")
	if m.idx != 0 {
		t.Fatalf("k clamps at top, got %d", m.idx)
	}
	m = press(m, "G")
	if m.idx != 3 {
		t.Fatalf("G goes to bottom, got %d", m.idx)
	}
	m = press(m, "g")
	if m.idx != 0 {
		t.Fatalf("g goes to top, got %d", m.idx)
	}
}

// plain strips ANSI escapes — assertions run on TEXT, not on styling; the
// palette itself is pinned by theme_test.go.
func plain(s string) string {
	for {
		start := strings.IndexByte(s, '\x1b')
		if start < 0 {
			return s
		}
		end := strings.IndexByte(s[start:], 'm')
		if end < 0 {
			return s
		}
		s = s[:start] + s[start+end+1:]
	}
}

func TestExpandToggleShowsRawNewlines(t *testing.T) {
	m := New(fixture(2), 100, 30)
	m.data.Messages[0].Text = "line one\nline two"

	folded := plain(strings.Join(m.bodyLines(), "\n"))
	if !strings.Contains(folded, "line one ⏎ line two") {
		t.Fatalf("collapsed should fold with ⏎: %q", folded)
	}
	if strings.Contains(folded, "line one\n") {
		t.Fatalf("collapsed must NOT contain raw newlines")
	}

	m = press(m, "enter")
	expanded := plain(strings.Join(m.bodyLines(), "\n"))
	if !strings.Contains(expanded, "line one\n") {
		t.Fatalf("expanded shows raw newlines: %q", expanded)
	}
	if strings.Contains(expanded, "⏎") {
		t.Fatalf("expanded must not fold: %q", expanded)
	}

	m = press(m, "enter")
	if strings.Contains(plain(strings.Join(m.bodyLines(), "\n")), "line one\n") {
		t.Fatalf("second enter collapses again")
	}
}

func TestWrappedExpandedLinesCarryTheTsOnlyOnce(t *testing.T) {
	m := New(fixture(2), 100, 30)
	m.data.Messages[0].Text = "aaa bbb ccc"
	m = press(m, "enter")
	// force a wrap by shrinking width
	m.Width = 30
	expanded := plain(strings.Join(m.bodyLines(), "\n"))
	first := true
	for _, l := range strings.Split(expanded, "\n") {
		if !strings.Contains(l, "2026-09-30 21:28") {
			first = false
			continue
		}
		if !first {
			t.Fatalf("timestamp repeated on a continuation line: %q", l)
		}
	}
}

func TestFooterListsEveryBindingAndThePosition(t *testing.T) {
	m := New(fixture(4), 100, 30)
	foot := m.footer()
	for _, b := range m.keys().Dispatch() {
		if !strings.Contains(foot, b.Keys+" "+b.Desc) {
			t.Fatalf("footer missing binding %q: %q", b.Keys, foot)
		}
	}
	if !strings.Contains(foot, "1/4") {
		t.Fatalf("footer missing position: %q", foot)
	}
}

func TestHeaderNamesTheSessionAndTheNotes(t *testing.T) {
	d := fixture(2)
	d.Notes = []string{"(no session directory matches /x — showing the most recently active session)"}
	m := New(d, 100, 30)
	head := strings.Join(m.headerLines(), "\n")
	if !strings.Contains(head, "oc-sent — Research something") {
		t.Fatalf("header missing title: %q", head)
	}
	if !strings.Contains(head, "ses_1") {
		t.Fatalf("header missing id: %q", head)
	}
	if !strings.Contains(head, "no session directory matches /x") {
		t.Fatalf("header missing fallback note: %q", head)
	}
}

func TestEmptyStateNamesTheEscapeHatch(t *testing.T) {
	// fixture(0) still carries the two base messages — an EMPTY session is
	// built directly, and the hidden hint is what makes rc-4 popups useful
	d := sent.Data{
		Session:          sent.Session{ID: "ses_1", Title: "Research something"},
		OtherAgentHidden: 3,
	}
	m := New(d, 100, 30)
	body := plain(strings.Join(m.bodyLines(), "\n"))
	if !strings.Contains(body, "no user messages") {
		t.Fatalf("empty state missing: %q", body)
	}
	if !strings.Contains(body, "3 non-build") {
		t.Fatalf("empty state missing hidden hint: %q", body)
	}
}

func TestErrorStateRendersTheCause(t *testing.T) {
	m := NewError("oc-sent failed (rc 5): store is unreadable", 100, 30)
	lines := m.Lines()
	joined := plain(strings.Join(lines, "\n"))
	if !strings.Contains(joined, "oc-sent: oc-sent failed (rc 5)") {
		t.Fatalf("error not rendered: %q", joined)
	}
	if !strings.Contains(joined, "store is unreadable") {
		t.Fatalf("error detail lost: %q", joined)
	}
}

func TestScrollKeepsTheSelectionVisible(t *testing.T) {
	m := New(fixture(40), 100, 10)
	if m.bodyHeight() >= len(m.bodyLines()) {
		t.Fatalf("fixture must overflow the viewport to test scrolling")
	}
	if m.scroll != 0 {
		t.Fatalf("starts at scroll 0, got %d", m.scroll)
	}
	for i := 0; i < 20; i++ {
		m = press(m, "j")
	}
	if m.scroll == 0 {
		t.Fatalf("selection moved below the fold but scroll did not")
	}
	// the selected bar must be inside the visible window
	lo, hi := m.visibleBodyRange(len(m.bodyLines()))
	sel := m.selectedBodyLine(m.bodyLines())
	if sel < lo || sel >= hi {
		t.Fatalf("selected line %d outside visible [%d,%d)", sel, lo, hi)
	}
	m = press(m, "g")
	if m.scroll != 0 || m.idx != 0 {
		t.Fatalf("g returns to the top: idx=%d scroll=%d", m.idx, m.scroll)
	}
}

func TestHeaderAndFooterStayPinnedWhenScrolled(t *testing.T) {
	m := New(fixture(40), 100, 12)
	for i := 0; i < 15; i++ {
		m = press(m, "j")
	}
	lines := m.Lines()
	if !strings.Contains(lines[0], "oc-sent — Research something") {
		t.Fatalf("header scrolled away: %q", lines[0])
	}
	foot := lines[len(lines)-1]
	if !strings.Contains(foot, "q close") {
		t.Fatalf("footer scrolled away: %q", foot)
	}
}

func TestQuittingProducesAnEmptyView(t *testing.T) {
	m := New(fixture(2), 100, 30)
	m = press(m, "q")
	if !m.quitting {
		t.Fatal("q sets quitting")
	}
	if m.View().Content != "" {
		t.Fatalf("quit view not empty: %q", m.View().Content)
	}
}
