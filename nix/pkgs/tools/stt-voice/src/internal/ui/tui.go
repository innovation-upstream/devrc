// Package ui is the transcript TUI: a bubbletea program the stop path spawns
// inside an alacritty (class stt-voice, matched by i3's for_window rule),
// showing the newest transcript with its duration/elapsed/RTFx and the
// history to browse.
//
// 🔴 THE MODEL IS PURE; EFFECTS ARE INTENTS. Step() returns intents; Run()
// turns an intent into a tea.Cmd that calls the Effects interface. Every
// action (clipboard, send-as-input) is testable against a recording fake —
// no test here can reach X11, and none should.
//
// 🔴 SEND-AS-INPUT NEVER TYPES A RETURN. The transcript is stripped of every
// newline before typing (effects.StripNewlines), because a Return submits
// whatever form the operator's focused window is showing. The window focused
// is the one CAPTURED AT STOP TIME — before the TUI opened — passed here as
// Target; a Target of 0 (nothing answerable was focused) refuses in words
// rather than typing into whatever happens to have focus.
package ui

import (
	"fmt"
	"strings"

	"charm.land/bubbles/v2/key"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/innovation-upstream/devrc/stt-voice/internal/effects"
	"github.com/innovation-upstream/devrc/stt-voice/internal/history"
)

// --- intents ----------------------------------------------------------------

type Intent interface{ intentName() string }

type CopyText struct{ Text string }

func (CopyText) intentName() string { return "copy" }

type SendText struct {
	Text   string
	Target int
}

func (SendText) intentName() string { return "send" }

// --- messages ---------------------------------------------------------------

// Copied / Sent report an effect's outcome back into the loop.
type Copied struct{ Err error }

type Sent struct{ Err error }

// --- keymap -----------------------------------------------------------------

type keyMap struct {
	Copy  key.Binding
	Send  key.Binding
	Older key.Binding
	Newer key.Binding
	Quit  key.Binding
}

func newKeyMap() keyMap {
	return keyMap{
		Copy:  key.NewBinding(key.WithKeys("c"), key.WithHelp("c", "copy")),
		Send:  key.NewBinding(key.WithKeys("t", "ctrl+enter"), key.WithHelp("t/⏎", "send as input")),
		Older: key.NewBinding(key.WithKeys("up"), key.WithHelp("↑", "older")),
		Newer: key.NewBinding(key.WithKeys("down"), key.WithHelp("↓", "newer")),
		Quit:  key.NewBinding(key.WithKeys("esc"), key.WithHelp("esc", "close")),
	}
}

// Dispatch is the table every key handler walks (and the tests ledger).
func (k keyMap) dispatch() []key.Binding {
	return []key.Binding{k.Copy, k.Send, k.Older, k.Newer, k.Quit}
}

// --- model ------------------------------------------------------------------

type Model struct {
	keys     keyMap
	entries  []history.Entry // NEWEST FIRST
	idx      int             // the entry on screen
	target   int             // captured window id; 0 = nothing captured
	fx       effects.Effects // injected; LiveEffects in the real program
	Width    int
	Height   int
	notice   string
	quitting bool
}

// New builds the model. `openID` is the entry the stop path wrote (the TUI
// opens on it); a missing id falls back to the newest.
func New(entries []history.Entry, openID int64, target int) Model {
	return NewWithEffects(entries, openID, target, effects.Live{})
}

// NewWithEffects is the injectable form the tests (and only they) use.
func NewWithEffects(entries []history.Entry, openID int64, target int, fx effects.Effects) Model {
	// Load() is oldest→newest; the TUI browses newest→oldest.
	m := Model{keys: newKeyMap(), target: target, fx: fx, Width: 80, Height: 24}
	m.entries = make([]history.Entry, 0, len(entries))
	for i := len(entries) - 1; i >= 0; i-- {
		m.entries = append(m.entries, entries[i])
	}
	for i, e := range m.entries {
		if e.ID == openID {
			m.idx = i
			break
		}
	}
	return m
}

func (m Model) Init() tea.Cmd { return nil }

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch v := msg.(type) {
	case tea.KeyPressMsg:
		// Step is the pure half: the notice it set (if any) is in the model
		// it returns, and the intents it produced become commands here.
		next, intents := m.Step(v)
		return next, tea.Batch(RunAll(intents, m.fx)...)
	case Copied:
		if v.Err != nil {
			m.notice = "copy failed: " + v.Err.Error()
		} else {
			m.notice = "copied to clipboard"
		}
		return m, nil
	case Sent:
		// 🔴 THE TUI CLOSES AFTER THE TYPING, NOT BEFORE: focus and type ran
		// inside the Cmd while the window was still up; quitting now closes
		// alacritty and leaves the operator's window focused.
		if v.Err != nil {
			m.notice = "send failed: " + v.Err.Error()
			return m, nil
		}
		m.quitting = true
		return m, tea.Quit
	}
	return m, nil
}

func (m Model) View() tea.View {
	if m.quitting {
		return tea.NewView("")
	}
	return tea.NewView(m.render())
}

// Step is the pure key handler; it returns the next model and any intents.
func (m Model) Step(msg tea.KeyPressMsg) (Model, []Intent) {
	switch {
	case key.Matches(msg, m.keys.Quit):
		m.quitting = true
		return m, nil
	case key.Matches(msg, m.keys.Copy):
		e := m.current()
		m.notice = "copied to clipboard"
		if e == nil {
			m.notice = "nothing to copy"
			return m, nil
		}
		return m, []Intent{CopyText{Text: e.Text}}
	case key.Matches(msg, m.keys.Send):
		e := m.current()
		if e == nil {
			m.notice = "nothing to send"
			return m, nil
		}
		if m.target <= 0 {
			// 🔴 REFUSE IN WORDS, NOT IN WHOEVER-HAS-FOCUS. Typing a
			// transcript into an unfocused random window is worse than no
			// send at all.
			m.notice = "no target window was captured — press c to copy instead"
			return m, nil
		}
		// 🔴 quitting is NOT set here: Update(Sent) closes the TUI only when
		// the effects SUCCEEDED — a failed send must stay open, with the
		// failure in words on screen, or the operator never learns why
		// nothing was typed.
		return m, []Intent{SendText{
			Text:   effects.StripNewlines(e.Text),
			Target: m.target,
		}}
	case key.Matches(msg, m.keys.Older):
		if m.idx < len(m.entries)-1 {
			m.idx++
		}
		return m, nil
	case key.Matches(msg, m.keys.Newer):
		if m.idx > 0 {
			m.idx--
		}
		return m, nil
	}
	return m, nil
}

func (m Model) current() *history.Entry {
	if m.idx < 0 || m.idx >= len(m.entries) {
		return nil
	}
	return &m.entries[m.idx]
}

// --- rendering --------------------------------------------------------------

var (
	titleStyle  = lipgloss.NewStyle().Bold(true)
	dimStyle    = lipgloss.NewStyle().Faint(true)
	noticeStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("214"))
)

func (m Model) render() string {
	if len(m.entries) == 0 {
		return " no transcripts\n"
	}
	e := m.entries[m.idx]
	var b strings.Builder
	b.WriteString(titleStyle.Render(" voice transcript"))
	b.WriteString("\n\n")
	b.WriteString(wrap(e.Text, maxInt(m.Width-4, 20)))
	b.WriteString("\n\n")
	b.WriteString(dimStyle.Render(fmt.Sprintf(
		" audio %.1fs · transcribed in %.1fs · RTFx %s · %d/%d",
		e.DurationS, e.ElapsedS, rtfx(e), m.idx+1, len(m.entries))))
	if m.notice != "" {
		b.WriteString("\n" + noticeStyle.Render(" "+m.notice))
	}
	b.WriteString("\n" + dimStyle.Render(" "+m.footer()))
	return b.String()
}

func (m Model) footer() string {
	parts := make([]string, 0, len(m.keys.dispatch()))
	for _, b := range m.keys.dispatch() {
		h := b.Help()
		parts = append(parts, h.Key+" "+h.Desc)
	}
	return strings.Join(parts, " · ")
}

func rtfx(e history.Entry) string {
	if e.ElapsedS <= 0 {
		return "—"
	}
	return fmt.Sprintf("%.1f", e.DurationS/e.ElapsedS)
}

// wrap is a naive word wrap for transcripts (the TUI is a plain terminal).
func wrap(text string, width int) string {
	var out []string
	for _, para := range strings.Split(text, "\n") {
		line := ""
		for _, word := range strings.Fields(para) {
			switch {
			case line == "":
				line = word
			case len(line)+1+len(word) <= width:
				line += " " + word
			default:
				out = append(out, line)
				line = word
			}
		}
		out = append(out, line)
	}
	return strings.Join(out, "\n")
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// --- the effects bridge (the only impure half) ------------------------------

// LiveEffects is the wiring the real program uses.
type LiveEffects struct{}

// Run converts ONE intent into a command. 🔴 A DEFAULT CASE THAT PANICS, NOT
// ONE THAT SILENTLY RETURNS nil — a new intent nobody wired would otherwise be
// a keypress that does nothing forever (the same rule mention-review's
// ui.Run follows, and ui_test.go's
// TestEveryRegisteredIntentIsHandledByRun asserts every intent is handled).
func Run(i Intent, fx effects.Effects) tea.Cmd {
	switch v := i.(type) {
	case CopyText:
		return func() tea.Msg {
			return Copied{Err: fx.Copy(v.Text)}
		}
	case SendText:
		return func() tea.Msg {
			// 🔴 FOCUS FIRST, THEN TYPE: the captured window may have been
			// closed while the operator was in the TUI, and typing into
			// "whatever now has focus" after a failed focus is exactly the
			// wrong-window leak this feature must never do. A failed focus
			// aborts the send (the transcript stays in history either way).
			if err := fx.Focus(v.Target); err != nil {
				return Sent{Err: fmt.Errorf("focus: %w", err)}
			}
			return Sent{Err: fx.Type(v.Text)}
		}
	}
	panic("ui.Run: unhandled intent " + i.intentName())
}

// RunAll maps a slice of intents.
func RunAll(intents []Intent, fx effects.Effects) []tea.Cmd {
	if len(intents) == 0 {
		return nil
	}
	cmds := make([]tea.Cmd, 0, len(intents))
	for _, i := range intents {
		cmds = append(cmds, Run(i, fx))
	}
	return cmds
}
