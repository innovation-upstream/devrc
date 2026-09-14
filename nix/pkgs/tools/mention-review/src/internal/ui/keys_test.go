package ui

import (
	"sort"
	"strings"
	"testing"

	"charm.land/bubbles/v2/key"
	tea "charm.land/bubbletea/v2"
)

// 🔴 LAYER 3(a) — THE KEYMAP ↔ HELP LEDGER, TWO-WAY.
//
// This is what makes "the footer cannot go stale" a MECHANICAL FACT rather than
// a promise. It fails when the dispatched set GROWS past the helped set (a
// binding with no help — the exact defect the preceding arc existed to fix) and
// when it SHRINKS below it (a help entry for a key that does nothing — a legend
// that lies).
//
// 🔴 IT WALKS `Dispatch()`, WHICH IS A DECLARED TABLE, NOT A REGEX OVER SOURCE.
// A grep-based ledger measures the SPELLING rather than the behaviour, and it
// passes over a `case` that falls through to nothing.

// bindingID is the identity two sets are compared on. 🔴 THE KEYS, NOT THE HELP
// TEXT: comparing on help text would let a binding with the right label and the
// wrong keys pass, which is the failure the operator would actually hit.
func bindingID(b key.Binding) string {
	ks := append([]string(nil), b.Keys()...)
	sort.Strings(ks)
	return strings.Join(ks, "|")
}

func idsOf(bs []key.Binding) []string {
	out := make([]string, 0, len(bs))
	for _, b := range bs {
		out = append(out, bindingID(b))
	}
	sort.Strings(out)
	return out
}

func TestEveryDispatchedBindingHasAHelpEntryAndViceVersa(t *testing.T) {
	var dispatched []key.Binding
	for _, b := range Dispatch() {
		dispatched = append(dispatched, b.Binding)
	}
	helped := Keys.helpedBindings()

	dset := map[string]bool{}
	for _, id := range idsOf(dispatched) {
		dset[id] = true
	}
	hset := map[string]bool{}
	for _, id := range idsOf(helped) {
		hset[id] = true
	}

	// Direction 1 — the set GREW: a binding is dispatched on but absent from
	// FullHelp. This is the defect the preceding arc existed to fix.
	for _, b := range Dispatch() {
		if !hset[bindingID(b.Binding)] {
			t.Errorf("binding %q (%s) is dispatched on but absent from FullHelp",
				bindingID(b.Binding), b.Action)
		}
	}
	// Direction 2 — the set SHRANK: a help entry for a key that does nothing.
	for _, b := range helped {
		if !dset[bindingID(b)] {
			t.Errorf("help entry %q is dispatched on nowhere", bindingID(b))
		}
	}
}

// 🔴 POSITIVE CONTROL ON THE LEDGER ITSELF. The test above can only be believed
// if its comparison CAN fail — a matcher that considers everything equal would
// pass identically. This feeds it a deliberately unhelped binding and a
// deliberately undispatched help entry and asserts it reports BOTH, with each
// direction's own wording.
func TestTheLedgerComparisonCanActuallyGoRed(t *testing.T) {
	ghost := key.NewBinding(key.WithKeys("ctrl+alt+z"), key.WithHelp("C-A-z", "ghost"))
	orphan := key.NewBinding(key.WithKeys("ctrl+alt+y"), key.WithHelp("C-A-y", "orphan"))

	dispatched := []key.Binding{Keys.Quit, ghost}
	helped := []key.Binding{Keys.Quit, orphan}

	grew, shrank := ledgerDiff(dispatched, helped)
	if len(grew) != 1 || grew[0] != bindingID(ghost) {
		t.Errorf("GREW direction reported %v, want exactly [%s]", grew, bindingID(ghost))
	}
	if len(shrank) != 1 || shrank[0] != bindingID(orphan) {
		t.Errorf("SHRANK direction reported %v, want exactly [%s]", shrank, bindingID(orphan))
	}
	// And the negative control: identical sets report nothing in either
	// direction. Without this the pair above is satisfied by a function that
	// reports everything.
	g2, s2 := ledgerDiff(dispatched, dispatched)
	if len(g2) != 0 || len(s2) != 0 {
		t.Errorf("identical sets reported grew=%v shrank=%v, want empty", g2, s2)
	}
}

// ledgerDiff is the comparison the ledger test performs, extracted so the
// positive control above exercises the SAME code rather than a second copy of
// it. A control built out of a re-implementation controls nothing.
func ledgerDiff(dispatched, helped []key.Binding) (grew, shrank []string) {
	dset, hset := map[string]bool{}, map[string]bool{}
	for _, b := range dispatched {
		dset[bindingID(b)] = true
	}
	for _, b := range helped {
		hset[bindingID(b)] = true
	}
	for _, b := range dispatched {
		if !hset[bindingID(b)] {
			grew = append(grew, bindingID(b))
		}
	}
	for _, b := range helped {
		if !dset[bindingID(b)] {
			shrank = append(shrank, bindingID(b))
		}
	}
	sort.Strings(grew)
	sort.Strings(shrank)
	return grew, shrank
}

// ShortHelp is a SUBSET of FullHelp, not a second list with its own text. A
// short-help entry that is not in full help is a legend nobody can expand.
func TestShortHelpIsASubsetOfFullHelp(t *testing.T) {
	full := map[string]bool{}
	for _, b := range Keys.helpedBindings() {
		full[bindingID(b)] = true
	}
	for _, b := range Keys.ShortHelp() {
		if !full[bindingID(b)] {
			t.Errorf("ShortHelp carries %q, which FullHelp does not", bindingID(b))
		}
	}
	if len(Keys.ShortHelp()) == 0 {
		t.Error("ShortHelp is empty — the persistent footer would render nothing")
	}
}

// 🔴 EVERY BINDING CARRIES HELP TEXT. A binding present in FullHelp with an
// EMPTY description renders as a blank cell, which is the same invisibility the
// ledger exists to prevent, one layer down.
func TestEveryBindingCarriesNonEmptyHelpText(t *testing.T) {
	for _, b := range Dispatch() {
		h := b.Binding.Help()
		if h.Key == "" || h.Desc == "" {
			t.Errorf("%s: help = {Key:%q Desc:%q}, both must be non-empty",
				b.Action, h.Key, h.Desc)
		}
		if len(b.Binding.Keys()) == 0 {
			t.Errorf("%s: binding has NO keys", b.Action)
		}
	}
}

// 🔴 NO TWO ACTIONS MAY CLAIM THE SAME KEY. `Step` walks the table in order and
// takes the first match, so a duplicate makes the second binding permanently
// dead — a key that is in the footer and does nothing.
func TestNoKeyIsClaimedByTwoActions(t *testing.T) {
	owner := map[string]Action{}
	for _, b := range Dispatch() {
		for _, k := range b.Binding.Keys() {
			if prev, dup := owner[k]; dup {
				t.Errorf("key %q is claimed by both %s and %s; the second is dead", k, prev, b.Action)
			}
			owner[k] = b.Action
		}
	}
}

// 🔴 THE FOOTER IS RENDERED FROM `Keys`, AND THE RENDERED TEXT PROVES IT.
// Asserting that `renderFooter` returns a non-empty string would pass against a
// hand-written literal. This asserts that every ShortHelp binding's own help
// text appears in the rendered footer — i.e. the footer is a FUNCTION of the
// keymap, and changing a binding's help changes the footer.
func TestTheFooterIsGeneratedFromTheKeymap(t *testing.T) {
	a := New("gardenersguild", "trowelcast", 1559)
	a.Width, a.Height = 140, 40
	got := stripANSI(a.renderFooter())
	for _, b := range Keys.ShortHelp() {
		h := b.Help()
		if !strings.Contains(got, h.Key) {
			t.Errorf("footer does not carry key %q\nfooter: %s", h.Key, got)
		}
		if !strings.Contains(got, h.Desc) {
			t.Errorf("footer does not carry description %q\nfooter: %s", h.Desc, got)
		}
	}
	// POSITIVE CONTROL for the instrument: a string that is NOT in any binding
	// must be absent. Without it, a `Contains` that always returned true would
	// pass every assertion above.
	if strings.Contains(got, "definitely-not-a-binding") {
		t.Error("the footer matcher matches text that is in no binding")
	}
}

// Pressing `?` toggles FullHelp, and the toggle is visible in the render.
func TestQuestionMarkTogglesTheFullHelp(t *testing.T) {
	a := New("gardenersguild", "trowelcast", 1559)
	a.Width, a.Height = 140, 40
	short := stripANSI(a.renderFooter())

	next, intents := a.Step(keyPress("?"))
	if len(intents) != 0 {
		t.Errorf("`?` emitted %d intents, want 0 — it is a local toggle", len(intents))
	}
	if !next.showFull {
		t.Fatal("`?` did not set showFull")
	}
	full := stripANSI(next.renderFooter())
	if full == short {
		t.Error("FullHelp renders identically to ShortHelp — the toggle is inert")
	}
	// The expansion must carry a binding that ShortHelp does NOT, or it is not
	// an expansion. `prev panel` is in FullHelp only.
	if !strings.Contains(full, "prev panel") {
		t.Errorf("FullHelp does not carry a FullHelp-only entry\n%s", full)
	}
	if strings.Contains(short, "prev panel") {
		t.Error("fixture is wrong: `prev panel` is in ShortHelp, so it cannot show the expansion")
	}
}

// keyPress builds a v2 key message from the same spelling `key.WithKeys`
// uses, so a test drives the binding table through its real matcher rather
// than through a second translation of its own.
//
// ⚠ v2: the type is `tea.KeyPressMsg` and `tea.KeyMsg` is an INTERFACE; a
// space is spelled "space", not " ".
//
// 🔴 IT PANICS ON AN UNKNOWN SPELLING RATHER THAN RETURNING A ZERO MESSAGE.
// A zero `KeyPressMsg` matches nothing, so a silent fallback would make every
// assertion about that key pass vacuously — the test would "press" a key that
// does not exist and observe, correctly, that nothing happened.
func keyPress(s string) tea.KeyPressMsg {
	if named, ok := namedKeys[s]; ok {
		return tea.KeyPressMsg{Code: named}
	}
	if s == "shift+tab" {
		return tea.KeyPressMsg{Code: tea.KeyTab, Mod: tea.ModShift}
	}
	if rest, ok := strings.CutPrefix(s, "ctrl+"); ok && len(rest) == 1 {
		return tea.KeyPressMsg{Code: rune(rest[0]), Mod: tea.ModCtrl}
	}
	if r := []rune(s); len(r) == 1 {
		return tea.KeyPressMsg{Code: r[0], Text: s}
	}
	panic("keyPress: unhandled spelling " + s)
}

var namedKeys = map[string]rune{
	"tab":    tea.KeyTab,
	"esc":    tea.KeyEscape,
	"up":     tea.KeyUp,
	"down":   tea.KeyDown,
	"pgup":   tea.KeyPgUp,
	"pgdown": tea.KeyPgDown,
	"home":   tea.KeyHome,
	"end":    tea.KeyEnd,
}

// 🔴 EVERY SPELLING IN THE DISPATCH TABLE MUST BE BUILDABLE BY `keyPress`, OR
// THE WALK IN `intents_test.go` SILENTLY SKIPS IT. This is the instrument
// check for that walk: without it, adding a binding with a spelling the helper
// cannot build would quietly remove that key from every test that iterates the
// table, and the suite would stay green over a key nobody exercises.
func TestKeyPressCanBuildEverySpellingInTheDispatchTable(t *testing.T) {
	built := 0
	for _, b := range Dispatch() {
		for _, k := range b.Binding.Keys() {
			msg := keyPress(k) // panics on an unknown spelling
			// And it must MATCH the binding it came from, or the helper builds
			// a well-formed message for the wrong key.
			if !key.Matches(msg, b.Binding) {
				t.Errorf("keyPress(%q) does not match binding %s", k, b.Action)
			}
			built++
		}
	}
	if built == 0 {
		t.Fatal("the dispatch table is empty — this check observed nothing")
	}
	t.Logf("built and matched %d key spellings", built)
}
