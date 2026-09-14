package ui

import "charm.land/bubbles/v2/key"

// 🔴 THE HELP FOOTER IS GENERATED, AND IT CANNOT GO STALE.
//
// The immediately-preceding arc existed ONLY because 131 keybindings were
// invisible and the operator asked "how do I merge?". A hand-written legend is
// the exact defect that arc fixed, and reintroducing one here would be undoing
// it one layer down.
//
// So there is EXACTLY ONE PLACE A BINDING IS SPELLED. The dispatcher reads
// `Keys.NextPanel`; the footer reads `Keys.NextPanel`. They cannot disagree
// because they are the same value. And because "they are the same value" is
// a property of how this file happens to be written today, `keys_test.go` adds
// a TWO-WAY ledger test that fails when the dispatched set GROWS past the
// helped set (a binding with no help) and when it SHRINKS below it (a help
// entry for a key that does nothing — a legend that lies).

// KeyMap is the single source of truth for every binding.
type KeyMap struct {
	NextPanel key.Binding
	PrevPanel key.Binding

	Up       key.Binding
	Down     key.Binding
	PageUp   key.Binding
	PageDown key.Binding
	Top      key.Binding
	Bottom   key.Binding

	NextHunk key.Binding
	PrevHunk key.Binding
	NextFile key.Binding
	PrevFile key.Binding

	Browser  key.Binding
	Retry    key.Binding
	FullHelpToggle key.Binding
	Quit     key.Binding
}

// Keys is the live map.
//
// ⚠ v2 NOTE: match `tea.KeyPressMsg`, not `tea.KeyMsg` — the latter is an
// INTERFACE in Bubble Tea v2. And a space is spelled `"space"`, not `" "`.
var Keys = KeyMap{
	NextPanel: key.NewBinding(key.WithKeys("tab"), key.WithHelp("tab", "panel")),
	PrevPanel: key.NewBinding(key.WithKeys("shift+tab"), key.WithHelp("S-tab", "prev panel")),

	Up:       key.NewBinding(key.WithKeys("k", "up"), key.WithHelp("k/↑", "up")),
	Down:     key.NewBinding(key.WithKeys("j", "down"), key.WithHelp("j/↓", "down")),
	PageUp:   key.NewBinding(key.WithKeys("ctrl+u", "pgup"), key.WithHelp("C-u", "half page up")),
	PageDown: key.NewBinding(key.WithKeys("ctrl+d", "pgdown"), key.WithHelp("C-d", "half page down")),
	Top:      key.NewBinding(key.WithKeys("g", "home"), key.WithHelp("g", "top")),
	Bottom:   key.NewBinding(key.WithKeys("G", "end"), key.WithHelp("G", "bottom")),

	NextHunk: key.NewBinding(key.WithKeys("]"), key.WithHelp("]", "next hunk")),
	PrevHunk: key.NewBinding(key.WithKeys("["), key.WithHelp("[", "prev hunk")),
	NextFile: key.NewBinding(key.WithKeys("}"), key.WithHelp("}", "next file")),
	PrevFile: key.NewBinding(key.WithKeys("{"), key.WithHelp("{", "prev file")),

	Browser:        key.NewBinding(key.WithKeys("o"), key.WithHelp("o", "browser")),
	Retry:          key.NewBinding(key.WithKeys("r"), key.WithHelp("r", "retry")),
	FullHelpToggle: key.NewBinding(key.WithKeys("?"), key.WithHelp("?", "keys")),
	Quit:           key.NewBinding(key.WithKeys("q", "ctrl+c", "esc"), key.WithHelp("q", "quit")),
}

// Action names the handler a binding dispatches to. It is a VALUE, not a
// closure: the ledger test compares SETS of these against the help entries,
// and a set of opaque funcs cannot be compared.
type Action string

const (
	ActNextPanel Action = "NextPanel"
	ActPrevPanel Action = "PrevPanel"
	ActUp        Action = "Up"
	ActDown      Action = "Down"
	ActPageUp    Action = "PageUp"
	ActPageDown  Action = "PageDown"
	ActTop       Action = "Top"
	ActBottom    Action = "Bottom"
	ActNextHunk  Action = "NextHunk"
	ActPrevHunk  Action = "PrevHunk"
	ActNextFile  Action = "NextFile"
	ActPrevFile  Action = "PrevFile"
	ActBrowser   Action = "Browser"
	ActRetry     Action = "Retry"
	ActFullHelp  Action = "FullHelp"
	ActQuit      Action = "Quit"
)

// Bound pairs a binding with the action it dispatches to.
type Bound struct {
	Action  Action
	Binding key.Binding
}

// Dispatch is THE dispatch table. `Step` walks exactly this slice; nothing
// else maps a keypress to behaviour.
//
// 🔴 A DECLARED TABLE, NOT A `switch`, AND THAT IS WHY THE LEDGER TEST IS
// HONEST. §5.3(a) requires walking "Step's dispatch (a declared []binding
// table, not a regex over source)". A `switch` would force the test to grep
// this file, which measures the SPELLING rather than the behaviour — and a
// grep-based ledger passes over a `case` that falls through to nothing.
func Dispatch() []Bound {
	return []Bound{
		{ActNextPanel, Keys.NextPanel},
		{ActPrevPanel, Keys.PrevPanel},
		{ActUp, Keys.Up},
		{ActDown, Keys.Down},
		{ActPageUp, Keys.PageUp},
		{ActPageDown, Keys.PageDown},
		{ActTop, Keys.Top},
		{ActBottom, Keys.Bottom},
		{ActNextHunk, Keys.NextHunk},
		{ActPrevHunk, Keys.PrevHunk},
		{ActNextFile, Keys.NextFile},
		{ActPrevFile, Keys.PrevFile},
		{ActBrowser, Keys.Browser},
		{ActRetry, Keys.Retry},
		{ActFullHelp, Keys.FullHelpToggle},
		{ActQuit, Keys.Quit},
	}
}

// ShortHelp is the persistent footer row — on by default, as the operator
// asked. It is a SUBSET of FullHelp, chosen for width, not a second list with
// its own text.
func (k KeyMap) ShortHelp() []key.Binding {
	return []key.Binding{
		k.NextPanel, k.Down, k.NextHunk, k.NextFile, k.Browser, k.FullHelpToggle, k.Quit,
	}
}

// FullHelp is what `?` expands to, GROUPED BY PANEL — an expansion of the same
// generated data, never a separate modal legend with its own text.
//
// 🔴 EVERY BINDING IN `Dispatch()` MUST APPEAR HERE, AND NOTHING ELSE MAY.
// `keys_test.go` asserts set equality in both directions.
func (k KeyMap) FullHelp() [][]key.Binding {
	return [][]key.Binding{
		{k.NextPanel, k.PrevPanel},
		{k.Up, k.Down, k.PageUp, k.PageDown, k.Top, k.Bottom},
		{k.NextHunk, k.PrevHunk, k.NextFile, k.PrevFile},
		{k.Browser, k.Retry, k.FullHelpToggle, k.Quit},
	}
}

// helpedBindings flattens FullHelp. Used by the ledger test and by nothing
// else — it is here rather than in the test file so the test cannot quietly
// flatten a DIFFERENT structure from the one the footer renders.
func (k KeyMap) helpedBindings() []key.Binding {
	var out []key.Binding
	for _, g := range k.FullHelp() {
		out = append(out, g...)
	}
	return out
}
