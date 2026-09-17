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
//
// 🔴 PHASE 2 MADE THAT LEDGER PER-MODE, AND THAT WAS NOT OPTIONAL. Write
// actions need a compose bar and a y/N bar, and a modal key is a key: a ledger
// that walked only the browse table would assert "every binding is helped"
// while every key that composes or confirms was invisible — the original defect
// reintroduced inside a mode. So `Mode` is part of a binding's identity, the
// ledger runs once per mode, and the footer renders that mode's help.

// Mode is which key table is live.
//
// 🔴 MODES ARE DISJOINT, WHICH IS A SAFETY PROPERTY AND NOT ONLY A UI ONE.
// While a confirmation is pending, `q` does not quit and `m` does not start a
// second merge, because neither is in the confirm table at all. There is no
// "fall through to browse" arm anywhere.
type Mode int

const (
	// ModeBrowse — reading the PR. Every read action lives here.
	ModeBrowse Mode = iota
	// ModeCompose — typing a comment or review body.
	ModeCompose
	// ModeConfirm — a write intent is built and waiting on y/N.
	ModeConfirm
	modeCount
)

// Word is the mode as a WORD, because the mode is a meaning-bearing state like
// any other and the operator's font cannot carry meaning in colour.
func (m Mode) Word() string {
	switch m {
	case ModeCompose:
		return "COMPOSING"
	case ModeConfirm:
		return "CONFIRM"
	}
	return "BROWSE"
}

// Modes enumerates every mode, so tests and the help renderer iterate the same
// list rather than each counting to three.
func Modes() []Mode {
	out := make([]Mode, 0, int(modeCount))
	for m := Mode(0); m < modeCount; m++ {
		out = append(out, m)
	}
	return out
}

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

	// ScrollDiffDown/Up are vim's `ctrl+e`/`ctrl+y`, not a cursor move: they
	// pan the DIFF VIEWPORT and leave every cursor where it was. See
	// `App.scrollDiff`.
	ScrollDiffDown key.Binding
	ScrollDiffUp   key.Binding

	NextHunk key.Binding
	PrevHunk key.Binding
	NextFile key.Binding
	PrevFile key.Binding

	Browser        key.Binding
	Retry          key.Binding
	FullHelpToggle key.Binding
	Quit           key.Binding

	// --- the write verbs (browse mode) ---
	Comment        key.Binding
	Approve        key.Binding
	RequestChanges key.Binding
	SubmitReview   key.Binding
	Merge          key.Binding

	// --- compose mode ---
	ComposeSend      key.Binding
	ComposeNewline   key.Binding
	ComposeBackspace key.Binding
	ComposeLeft      key.Binding
	ComposeRight     key.Binding
	ComposeCancel    key.Binding

	// --- confirm mode ---
	ConfirmYes key.Binding
	ConfirmNo  key.Binding
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

	// 🔴 BARE CAPITALS, AND THEY ARE A SECOND BINDING RATHER THAN A MODE ON
	// `j`/`k` ON PURPOSE. `J`/`K` mean "scroll the diff" from EVERY panel, so
	// the operator can read past the cursor without losing their place — vim's
	// `ctrl+e`/`ctrl+y`. A pending-key state machine (`]h`, `[h`) was tried and
	// abandoned: `G` and `R` prove this key table matches a bare capital
	// directly, so no machine is needed.
	ScrollDiffDown: key.NewBinding(key.WithKeys("J"), key.WithHelp("J", "scroll diff down")),
	ScrollDiffUp:   key.NewBinding(key.WithKeys("K"), key.WithHelp("K", "scroll diff up")),

	NextHunk: key.NewBinding(key.WithKeys("]"), key.WithHelp("]", "next hunk")),
	PrevHunk: key.NewBinding(key.WithKeys("["), key.WithHelp("[", "prev hunk")),
	NextFile: key.NewBinding(key.WithKeys("}"), key.WithHelp("}", "next file")),
	PrevFile: key.NewBinding(key.WithKeys("{"), key.WithHelp("{", "prev file")),

	Browser:        key.NewBinding(key.WithKeys("o"), key.WithHelp("o", "browser")),
	Retry:          key.NewBinding(key.WithKeys("r"), key.WithHelp("r", "retry")),
	FullHelpToggle: key.NewBinding(key.WithKeys("?"), key.WithHelp("?", "keys")),
	Quit:           key.NewBinding(key.WithKeys("q", "ctrl+c", "esc"), key.WithHelp("q", "quit")),

	// 🔴 THE WRITE VERBS SAY WHAT THEY DO IN THE FOOTER, AND THE CONFIRMED ONES
	// SAY SO. `approve (asks)` is four characters of footer that tell the
	// operator the key is not immediately destructive — the same information
	// `nvim-octo`'s legend carries as "ASKS FOR CONFIRMATION FIRST".
	Comment:        key.NewBinding(key.WithKeys("c"), key.WithHelp("c", "comment")),
	Approve:        key.NewBinding(key.WithKeys("a"), key.WithHelp("a", "approve (asks)")),
	RequestChanges: key.NewBinding(key.WithKeys("R"), key.WithHelp("R", "request changes (asks)")),
	SubmitReview:   key.NewBinding(key.WithKeys("v"), key.WithHelp("v", "submit review (asks)")),
	Merge:          key.NewBinding(key.WithKeys("m"), key.WithHelp("m", "merge (asks)")),

	// ⚠ `ctrl+d` SENDS, NOT `ctrl+s`, AND THE REASON IS FLOW CONTROL. `ctrl+s`
	// is XOFF on a terminal that has not cleared IXON, and whether the raw-mode
	// setup clears it is a property of the host's termios that this program
	// cannot assert. A send key that silently freezes the terminal on one
	// machine is a worse failure than an unfamiliar chord. `ctrl+d` collides
	// with `half page down` only across modes, which are disjoint.
	ComposeSend:      key.NewBinding(key.WithKeys("ctrl+d"), key.WithHelp("C-d", "send")),
	ComposeNewline:   key.NewBinding(key.WithKeys("enter"), key.WithHelp("enter", "newline")),
	ComposeBackspace: key.NewBinding(key.WithKeys("backspace"), key.WithHelp("bksp", "delete")),
	ComposeLeft:      key.NewBinding(key.WithKeys("left"), key.WithHelp("←", "cursor left")),
	ComposeRight:     key.NewBinding(key.WithKeys("right"), key.WithHelp("→", "cursor right")),
	ComposeCancel:    key.NewBinding(key.WithKeys("esc"), key.WithHelp("esc", "discard")),

	// 🔴 `y` IS THE ONLY KEY THAT PROCEEDS, AND EVERYTHING ELSE IN THE TABLE
	// ABORTS. `n` and `esc` are spelled so the footer can say so; a key that is
	// in NEITHER binding is handled by the table walk finding no match, which
	// leaves the confirmation standing rather than proceeding.
	ConfirmYes: key.NewBinding(key.WithKeys("y"), key.WithHelp("y", "yes — do it")),
	ConfirmNo:  key.NewBinding(key.WithKeys("n", "esc"), key.WithHelp("n/esc", "abort")),
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

	// 🔴 THEIR OWN ACTIONS, NOT A `move()` ARM. `move`/`moveIn` are CURSOR
	// movers — `moveIn` clamps against a cursor range and the `PanelFiles` arm
	// drags `diffCur` to a file start — so routing a viewport pan through them
	// would move the very cursors this binding exists to leave alone.
	ActScrollDiffDown Action = "ScrollDiffDown"
	ActScrollDiffUp   Action = "ScrollDiffUp"

	ActNextHunk Action = "NextHunk"
	ActPrevHunk Action = "PrevHunk"
	ActNextFile Action = "NextFile"
	ActPrevFile Action = "PrevFile"
	ActBrowser  Action = "Browser"
	ActRetry    Action = "Retry"
	ActFullHelp Action = "FullHelp"
	ActQuit     Action = "Quit"

	ActComment        Action = "Comment"
	ActApprove        Action = "Approve"
	ActRequestChanges Action = "RequestChanges"
	ActSubmitReview   Action = "SubmitReview"
	ActMerge          Action = "Merge"

	ActComposeSend      Action = "ComposeSend"
	ActComposeNewline   Action = "ComposeNewline"
	ActComposeBackspace Action = "ComposeBackspace"
	ActComposeLeft      Action = "ComposeLeft"
	ActComposeRight     Action = "ComposeRight"
	ActComposeCancel    Action = "ComposeCancel"

	ActConfirmYes Action = "ConfirmYes"
	ActConfirmNo  Action = "ConfirmNo"
)

// Bound pairs a binding with the action it dispatches to, IN ONE MODE.
type Bound struct {
	Mode    Mode
	Action  Action
	Binding key.Binding
}

// Dispatch is THE dispatch table, all modes. `Step` walks `DispatchFor(mode)`;
// nothing else maps a keypress to behaviour.
//
// 🔴 A DECLARED TABLE, NOT A `switch`, AND THAT IS WHY THE LEDGER TEST IS
// HONEST. §5.3(a) requires walking "Step's dispatch (a declared []binding
// table, not a regex over source)". A `switch` would force the test to grep
// this file, which measures the SPELLING rather than the behaviour — and a
// grep-based ledger passes over a `case` that falls through to nothing.
func Dispatch() []Bound {
	return []Bound{
		{ModeBrowse, ActNextPanel, Keys.NextPanel},
		{ModeBrowse, ActPrevPanel, Keys.PrevPanel},
		{ModeBrowse, ActUp, Keys.Up},
		{ModeBrowse, ActDown, Keys.Down},
		{ModeBrowse, ActPageUp, Keys.PageUp},
		{ModeBrowse, ActPageDown, Keys.PageDown},
		{ModeBrowse, ActTop, Keys.Top},
		{ModeBrowse, ActBottom, Keys.Bottom},
		{ModeBrowse, ActScrollDiffDown, Keys.ScrollDiffDown},
		{ModeBrowse, ActScrollDiffUp, Keys.ScrollDiffUp},
		{ModeBrowse, ActNextHunk, Keys.NextHunk},
		{ModeBrowse, ActPrevHunk, Keys.PrevHunk},
		{ModeBrowse, ActNextFile, Keys.NextFile},
		{ModeBrowse, ActPrevFile, Keys.PrevFile},
		{ModeBrowse, ActBrowser, Keys.Browser},
		{ModeBrowse, ActRetry, Keys.Retry},
		{ModeBrowse, ActFullHelp, Keys.FullHelpToggle},
		{ModeBrowse, ActQuit, Keys.Quit},
		{ModeBrowse, ActComment, Keys.Comment},
		{ModeBrowse, ActApprove, Keys.Approve},
		{ModeBrowse, ActRequestChanges, Keys.RequestChanges},
		{ModeBrowse, ActSubmitReview, Keys.SubmitReview},
		{ModeBrowse, ActMerge, Keys.Merge},

		{ModeCompose, ActComposeSend, Keys.ComposeSend},
		{ModeCompose, ActComposeNewline, Keys.ComposeNewline},
		{ModeCompose, ActComposeBackspace, Keys.ComposeBackspace},
		{ModeCompose, ActComposeLeft, Keys.ComposeLeft},
		{ModeCompose, ActComposeRight, Keys.ComposeRight},
		{ModeCompose, ActComposeCancel, Keys.ComposeCancel},

		{ModeConfirm, ActConfirmYes, Keys.ConfirmYes},
		{ModeConfirm, ActConfirmNo, Keys.ConfirmNo},
	}
}

// DispatchFor is the slice `Step` walks in one mode.
func DispatchFor(m Mode) []Bound {
	var out []Bound
	for _, b := range Dispatch() {
		if b.Mode == m {
			out = append(out, b)
		}
	}
	return out
}

// ShortHelpFor is the persistent footer row for a mode — on by default, as the
// operator asked. It is a SUBSET of that mode's FullHelp, chosen for width, not
// a second list with its own text.
//
// ⚠ `J`/`K` ARE DELIBERATELY NOT HERE, AND THE REASON IS MEASURED. The browse
// row already renders 146 columns wide (measured at the `ready` fixture, which
// carries a viewer login), i.e. it OVERFLOWS even a 140-column terminal before
// anything is added; two more entries make an existing overflow worse. Every
// other scrolling key — `C-u`, `C-d`, `g`, `G` — is likewise FullHelp-only, so
// `?` is where the operator already looks for this class of binding. The two
// keys are still in `FullHelpFor`, which is what the ledger and the legend test
// bind to.
func (k KeyMap) ShortHelpFor(m Mode) []key.Binding {
	switch m {
	case ModeCompose:
		return []key.Binding{k.ComposeSend, k.ComposeNewline, k.ComposeCancel}
	case ModeConfirm:
		return []key.Binding{k.ConfirmYes, k.ConfirmNo}
	}
	return []key.Binding{
		k.NextPanel, k.Down, k.NextHunk, k.NextFile, k.Browser,
		k.Comment, k.Approve, k.Merge, k.FullHelpToggle, k.Quit,
	}
}

// FullHelpFor is what `?` expands to, GROUPED BY PANEL — an expansion of the
// same generated data, never a separate modal legend with its own text.
//
// 🔴 EVERY BINDING IN `DispatchFor(m)` MUST APPEAR HERE FOR THAT SAME `m`, AND
// NOTHING ELSE MAY. `keys_test.go` asserts set equality in both directions, per
// mode.
func (k KeyMap) FullHelpFor(m Mode) [][]key.Binding {
	switch m {
	case ModeCompose:
		return [][]key.Binding{
			{k.ComposeSend, k.ComposeCancel},
			{k.ComposeNewline, k.ComposeBackspace, k.ComposeLeft, k.ComposeRight},
		}
	case ModeConfirm:
		return [][]key.Binding{{k.ConfirmYes, k.ConfirmNo}}
	}
	return [][]key.Binding{
		{k.NextPanel, k.PrevPanel},
		{k.Up, k.Down, k.PageUp, k.PageDown, k.Top, k.Bottom},
		{k.ScrollDiffDown, k.ScrollDiffUp, k.NextHunk, k.PrevHunk, k.NextFile, k.PrevFile},
		{k.Comment, k.Approve, k.RequestChanges, k.SubmitReview, k.Merge},
		{k.Browser, k.Retry, k.FullHelpToggle, k.Quit},
	}
}

// helpedBindingsFor flattens FullHelpFor. Used by the ledger test and by
// nothing else — it is here rather than in the test file so the test cannot
// quietly flatten a DIFFERENT structure from the one the footer renders.
func (k KeyMap) helpedBindingsFor(m Mode) []key.Binding {
	var out []key.Binding
	for _, g := range k.FullHelpFor(m) {
		out = append(out, g...)
	}
	return out
}

// modeKeys adapts `Keys` to `help.KeyMap` for ONE mode.
//
// 🔴 IT IS THE ONLY `help.KeyMap` IN THIS PACKAGE. The no-argument
// `ShortHelp()`/`FullHelp()` pair that Phase 1 had is deliberately gone: two
// spellings of "the help for right now" is how a footer renders one mode's keys
// while the dispatcher walks another's.
type modeKeys struct{ m Mode }

func (h modeKeys) ShortHelp() []key.Binding  { return Keys.ShortHelpFor(h.m) }
func (h modeKeys) FullHelp() [][]key.Binding { return Keys.FullHelpFor(h.m) }
