// Package ui is the oc-sent popup TUI: the messages the user sent in the
// current opencode session, loaded via internal/sent (which execs the
// oc-sent CLI — see that package's header for why this never touches the
// store).
//
// 🔴 THE MODEL IS PURE. Every key handler is a method on the value Model
// returning the next Model; there are no intents because there are no
// effects — this is a read-only viewer, and q is the only side effect
// (quit), which the popup shell closes on. That makes every interaction
// testable without a terminal.
//
// 🔴 THE FRAME IS A SPLIT, NOT ONE SCROLLING SLAB: header (who this is) and
// footer (how to drive) are PINNED — a scrolled popup that loses its own
// legend is a popup that has to be quit to be understood. Only the message
// body scrolls, within bodyHeight = Height - header - footer.
package ui

import (
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/innovation-upstream/devrc/oc-sent-tui/internal/sent"
)

// --- the model ----------------------------------------------------------------

type Model struct {
	data     sent.Data
	expanded map[int]bool // idx → expanded (raw text with real newlines)
	idx      int          // selected message, oldest-first
	scroll   int          // first visible BODY line
	Width    int
	Height   int
	quitting bool
	err      string // load failure, rendered instead of the list
}

// New builds the model around loaded data.
func New(d sent.Data, width, height int) Model {
	m := Model{
		data:     d,
		expanded: map[int]bool{},
		Width:    width,
		Height:   height,
	}
	m.clampIdx()
	m.ensureVisible()
	return m
}

// NewError builds the model for a load failure — the popup must show WHY it
// is empty, never a silent blank.
func NewError(msg string, width, height int) Model {
	m := Model{err: msg, Width: width, Height: height}
	m.ensureVisible()
	return m
}

// --- keys ---------------------------------------------------------------------

// keyMap is the binding table; the slices accept the spellings v2 has been
// seen to use for a key across versions ("G" and "shift+g" both).
type keyMap struct {
	Up     []string
	Down   []string
	Top    []string
	Bottom []string
	Expand []string
	Quit   []string
}

// Dispatch is the ledger the footer renders and the tests walk — every
// binding appears here, so a new key that never reached the footer is a
// test failure, not a hidden key.
func (k keyMap) Dispatch() []Binding {
	return []Binding{
		{"j/k ↑/↓", "move"},
		{"⏎", "expand"},
		{"g/G", "ends"},
		{"q", "close"},
	}
}

// Binding is one footer entry.
type Binding struct {
	Keys string
	Desc string
}

func (k keyMap) matches(msg tea.KeyPressMsg, keys []string) bool {
	s := msg.String()
	for _, k := range keys {
		if s == k {
			return true
		}
	}
	return false
}

// --- update -------------------------------------------------------------------

func (m Model) Init() tea.Cmd { return nil }

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	k := m.keys()
	switch v := msg.(type) {
	case tea.KeyPressMsg:
		switch {
		case k.matches(v, k.Quit):
			m.quitting = true
			return m, tea.Quit
		case k.matches(v, k.Up):
			if m.idx > 0 {
				m.idx--
			}
			m.ensureVisible()
			return m, nil
		case k.matches(v, k.Down):
			if m.idx < len(m.data.Messages)-1 {
				m.idx++
			}
			m.ensureVisible()
			return m, nil
		case k.matches(v, k.Top):
			m.idx = 0
			m.ensureVisible()
			return m, nil
		case k.matches(v, k.Bottom):
			m.idx = len(m.data.Messages) - 1
			m.ensureVisible()
			return m, nil
		case k.matches(v, k.Expand):
			if m.idx >= 0 && m.idx < len(m.data.Messages) {
				if m.expanded[m.idx] {
					delete(m.expanded, m.idx)
				} else {
					m.expanded[m.idx] = true
				}
			}
			m.ensureVisible()
			return m, nil
		}
	case tea.WindowSizeMsg:
		m.Width, m.Height = v.Width, v.Height
		m.ensureVisible()
		return m, nil
	}
	return m, nil
}

func (m Model) keys() keyMap {
	return keyMap{
		Up:     []string{"up", "k"},
		Down:   []string{"down", "j"},
		Top:    []string{"g"},
		Bottom: []string{"G", "shift+g"},
		Expand: []string{"enter"},
		Quit:   []string{"q", "esc", "ctrl+c"},
	}
}

// --- rendering ----------------------------------------------------------------

var (
	titleStyle = lipgloss.NewStyle().Bold(true).
			Foreground(fg).Background(titleBg)
	dimStyle    = lipgloss.NewStyle().Foreground(dim)
	textStyle   = lipgloss.NewStyle().Foreground(fg)
	selectedTxt = lipgloss.NewStyle().Foreground(fg).Bold(true)
	barStyle    = lipgloss.NewStyle().Foreground(accent).Bold(true)
	errStyle    = lipgloss.NewStyle().Foreground(c("bright.red"))
)

func (m Model) View() tea.View {
	if m.quitting {
		return tea.NewView("")
	}
	return tea.NewView(strings.Join(m.Lines(), "\n"))
}

// Lines is the full rendered frame, one string per terminal line — the pure
// half the tests pin.
func (m Model) Lines() []string {
	if m.err != "" {
		return append(m.headerLines(),
			errStyle.Render(" oc-sent: "+m.err),
			dimStyle.Render(" fix the cause and reopen (Alt+S)"),
			"", dimStyle.Render(" "+m.footer()))
	}
	out := m.headerLines()
	body := m.bodyLines()
	lo, hi := m.visibleBodyRange(len(body))
	out = append(out, body[lo:hi]...)
	out = append(out, m.footerLines()...)
	return out
}

func (m Model) headerLines() []string {
	if m.err != "" {
		return []string{titleStyle.Render(" oc-sent")}
	}
	d := m.data
	out := []string{
		titleStyle.Render(" oc-sent — " + d.Session.Title),
		dimStyle.Render(" " + d.Session.ID +
			fmt.Sprintf(" · %d messages", len(d.Messages))),
	}
	for _, n := range d.Notes {
		out = append(out, dimStyle.Render(" "+n))
	}
	if hidden := m.hiddenNote(); hidden != "" {
		out = append(out, dimStyle.Render(" "+hidden))
	}
	return out
}

func (m Model) bodyLines() []string {
	d := m.data
	if len(d.Messages) == 0 {
		out := []string{"", textStyle.Render(" no user messages")}
		if d.OtherAgentHidden > 0 {
			out = append(out, dimStyle.Render(fmt.Sprintf(
				" %d non-build message(s) hidden — `oc-sent here --all-agents` shows them",
				d.OtherAgentHidden)))
		}
		return out
	}
	var out []string
	for i, msg := range d.Messages {
		bar := "  "
		sty := textStyle
		if i == m.idx {
			bar = barStyle.Render("▍ ")
			sty = selectedTxt
		}
		text := fold(msg.Text)
		if m.expanded[i] {
			text = wrap(msg.Text, m.textWidth())
		}
		for j, tl := range strings.Split(text, "\n") {
			prefix := dimStyle.Render(fmt.Sprintf("%s%-16s", bar, msg.TS))
			if j > 0 {
				prefix = dimStyle.Render(bar + strings.Repeat(" ", 16))
			}
			out = append(out, prefix+sty.Render(" "+tl))
		}
	}
	return out
}

func (m Model) footerLines() []string {
	return []string{"", dimStyle.Render(" " + m.footer())}
}

func (m Model) footer() string {
	parts := make([]string, 0, 5)
	for _, b := range m.keys().Dispatch() {
		parts = append(parts, b.Keys+" "+b.Desc)
	}
	if len(m.data.Messages) > 0 {
		parts = append(parts, fmt.Sprintf("%d/%d", m.idx+1, len(m.data.Messages)))
	}
	return strings.Join(parts, " · ")
}

func (m Model) hiddenNote() string {
	var bits []string
	if m.data.TextlessHidden > 0 {
		bits = append(bits, fmt.Sprintf("%d textless", m.data.TextlessHidden))
	}
	if m.data.OtherAgentHidden > 0 {
		bits = append(bits, fmt.Sprintf("%d non-build", m.data.OtherAgentHidden))
	}
	if len(bits) == 0 {
		return ""
	}
	return fmt.Sprintf("(%s hidden — --all-agents / --json for detail)",
		strings.Join(bits, ", "))
}

// fold renders collapsed text: one greppable line, internal newlines as " ⏎ "
// — the same folding the python CLI's human renderer uses, chosen so the
// popup and `oc-sent here` read the same way.
func fold(text string) string {
	return strings.Join(nonEmpty(strings.Split(text, "\n")), " ⏎ ")
}

func nonEmpty(lines []string) []string {
	out := make([]string, 0, len(lines))
	for _, l := range lines {
		if strings.TrimSpace(l) != "" {
			out = append(out, strings.TrimSpace(l))
		}
	}
	return out
}

// wrap is the naive word wrap stt-voice's TUI uses (same rationale: a plain
// terminal, no unicode edge cases worth a dependency).
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

func (m Model) textWidth() int {
	if w := m.Width - 24; w > 20 {
		return w
	}
	return 20
}

// --- scrolling ----------------------------------------------------------------

// bodyHeight is how many BODY lines fit under the pinned header and above
// the pinned footer.
func (m Model) bodyHeight() int {
	h := m.Height - len(m.headerLines()) - len(m.footerLines())
	if h < 1 {
		return 1
	}
	return h
}

// clampIdx keeps the selection inside the list (and valid when empty).
func (m *Model) clampIdx() {
	if len(m.data.Messages) == 0 {
		m.idx = 0
		return
	}
	if m.idx < 0 {
		m.idx = 0
	}
	if m.idx >= len(m.data.Messages) {
		m.idx = len(m.data.Messages) - 1
	}
}

// selectedBodyLine is the body-line index of the selected message's FIRST
// line, or -1.
func (m Model) selectedBodyLine(body []string) int {
	if len(m.data.Messages) == 0 {
		return -1
	}
	// walk the body counting lines per message — the same split bodyLines
	// used, without re-rendering
	count := 0
	for i, msg := range m.data.Messages {
		text := fold(msg.Text)
		if m.expanded[i] {
			text = wrap(msg.Text, m.textWidth())
		}
		n := len(strings.Split(text, "\n"))
		if i == m.idx {
			return count
		}
		count += n
	}
	_ = body
	return -1
}

// ensureVisible scrolls the body so the selected message's first line is on
// screen, clamped to the body's length.
func (m *Model) ensureVisible() {
	body := m.bodyLines()
	sel := m.selectedBodyLine(body)
	h := m.bodyHeight()
	switch {
	case sel < 0:
		m.scroll = 0
	case sel < m.scroll:
		m.scroll = sel
	case sel >= m.scroll+h:
		// the selected line is the first VISIBLE one from the top of its
		// own block — a bar one line in reads better than one at the edge
		m.scroll = sel
	}
	if max := maxInt(0, len(body)-h); m.scroll > max {
		m.scroll = max
	}
}

func (m Model) visibleBodyRange(n int) (int, int) {
	h := m.bodyHeight()
	lo := m.scroll
	if lo > n {
		lo = n
	}
	hi := lo + h
	if hi > n {
		hi = n
	}
	return lo, hi
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
