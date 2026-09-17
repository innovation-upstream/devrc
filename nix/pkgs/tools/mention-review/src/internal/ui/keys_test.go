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

// 🔴 ONCE PER MODE, AND THE PER-MODE PART IS NOT DECORATION. Phase 2 added a
// compose table and a confirm table. A ledger that walked only the browse table
// would report "every binding is helped" while every key that composes or
// confirms was invisible in the footer — the original defect, reintroduced
// inside a mode, and passing.
func TestEveryDispatchedBindingHasAHelpEntryAndViceVersa(t *testing.T) {
	checked := 0
	for _, m := range Modes() {
		var dispatched []key.Binding
		for _, b := range DispatchFor(m) {
			dispatched = append(dispatched, b.Binding)
		}
		helped := Keys.helpedBindingsFor(m)

		grew, shrank := ledgerDiff(dispatched, helped)
		for _, id := range grew {
			t.Errorf("mode %s: binding %q is dispatched on but absent from FullHelp",
				m.Word(), id)
		}
		for _, id := range shrank {
			t.Errorf("mode %s: help entry %q is dispatched on nowhere", m.Word(), id)
		}

		// 🔴 POSITIVE CONTROL PER MODE. Equality over two EMPTY sets is the
		// silent zero — it is exactly what a mode whose table was never wired
		// produces, and it would read as a clean two-way ledger.
		if len(dispatched) == 0 {
			t.Errorf("mode %s dispatches NOTHING — this ledger observed nothing", m.Word())
		}
		if len(helped) == 0 {
			t.Errorf("mode %s helps NOTHING — this ledger observed nothing", m.Word())
		}
		checked += len(dispatched)
	}
	if checked == 0 {
		t.Fatal("no mode had any bindings at all")
	}
	t.Logf("compared %d dispatched bindings across %d modes", checked, len(Modes()))
}

// --- LAYER 3(a'), THE HALF THE LEDGER ABOVE CANNOT SEE ------------------------
//
// 🔴 THE LEDGER ABOVE IS STRUCTURAL, AND STRUCTURAL IS NOT ENOUGH. `Dispatch()`
// and `FullHelpFor()` are two lists of the SAME `key.Binding` values, so
// comparing them catches a binding added to one list and not the other — and
// NOTHING ELSE. In particular it cannot see the failure that matters most here:
// an Action that is dispatched, helped, and that no handler implements. `act()`
// ends by falling through to `move(act)`, and `move`/`moveIn` switch on the
// action and do nothing at all for one they do not know — so such a key sits in
// the footer, in the dispatch table, and is dead. That defect is on this
// project's batched list; it is closed here because the `J`/`K` legend entry now
// leans on this ledger to stay honest.
//
// 🔴 SO THE INDEPENDENT SOURCE IS BEHAVIOUR, NOT A SECOND LITERAL. For every
// binding, in every reachable state of its OWN mode, press its keys and ask
// whether anything observable moved. Then compare, two-way: every dispatched
// action must be LIVE somewhere, and every help entry must name a live action.
// It fails when the live set SHRINKS (a handler removed, a key gone dead) and
// when the helped set GROWS past it (a legend entry for a key that does
// nothing).

// observable is the App state a keypress can move, projected so two Apps are
// comparable with `==`.
//
// 🔴 NOT `reflect.DeepEqual` OVER `App`. `App` holds two `viewport.Model`s, and
// a viewport carries a `StyleLineFunc` FUNC field: DeepEqual reports two
// non-nil funcs as UNEQUAL always, so it would answer "something changed" for
// every key pressed — a ledger that is green over a dead binding, which is
// precisely the defect this exists to catch. `syncDiffViewport` installs a fresh
// closure on most paths, so this is not hypothetical.
type observable struct {
	focus     Panel
	commitCur int
	fileCur   int
	diffCur   int
	diffYOff  int
	bodyYOff  int
	mode      Mode
	showFull  bool
	quitting  bool
	notice    string
	compose   string
	composeAt int
	prompt    string
	load      LoadState
	hasErr    bool
	hasDiff   bool
	hasSnap   bool
}

func observe(a App) observable {
	o := observable{
		focus:     a.Focus,
		commitCur: a.commitCur,
		fileCur:   a.fileCur,
		diffCur:   a.diffCur,
		diffYOff:  a.vp.YOffset(),
		bodyYOff:  a.body.YOffset(),
		mode:      a.mode,
		showFull:  a.showFull,
		quitting:  a.Quitting,
		notice:    a.notice,
		compose:   string(a.compose.Buf),
		composeAt: a.compose.Cur,
		load:      a.Load,
		hasErr:    a.Err != nil,
		hasDiff:   a.Diff != nil,
		hasSnap:   a.Snap != nil,
	}
	if a.pending != nil {
		o.prompt = a.pending.Prompt
	}
	return o
}

// liveStates are the app states the behavioural ledger drives.
//
// 🔴 IT IS NOT `reachableStates()`. That list is built for the INTENT walk and
// every one of its PR states has `Diff == nil` and every cursor at 0, so the
// whole movement family — `j`, `k`, `C-u`, `C-d`, `g`, `G`, `]`, `[`, `}`, `{`,
// `J`, `K` — is clamped or inert in all of them. A ledger driven from that list
// would report twelve dead actions, which is a fact about the fixtures.
func liveStates(t *testing.T) []walkState {
	t.Helper()
	states := append([]walkState(nil), reachableStates(t)...)

	// A loaded diff with the cursor parked in the middle of a buffer that is
	// longer than the window: every movement key has room in both directions.
	//
	// 🔴 EVERY CURSOR HERE IS INTERNALLY CONSISTENT, AND THAT IS NOT TIDINESS —
	// IT IS WHAT MAKES THE LEDGER ABLE TO SEE A DEAD ACTION AT ALL. `act()`
	// ends by falling through to `move()`, whose Diff arm runs
	// `syncFileCursorFromDiff()` unconditionally. Feed it a state whose
	// `fileCur` DISAGREES with `FileAt(diffCur)` — which `scrollable` does on
	// purpose, so the movement tests can see a cursor being touched — and an
	// unhandled action "changes something" by dragging `fileCur` into
	// agreement. MEASURED: with `scrollable` here, a dispatched action with no
	// handler at all was reported LIVE. So this list must not reuse it.
	for _, p := range []Panel{PanelOverview, PanelCommits, PanelFiles, PanelDiff} {
		a := bigApp(t, 200)
		a.Focus = p
		a.vp.SetHeight(scrollFixtureHeight)
		a.diffCur = 100
		a.commitCur = 1
		a.syncFileCursorFromDiff()
		a.syncDiffViewport()
		if a.fileCur != a.Diff.FileAt(a.diffCur) {
			t.Fatalf("the %s ledger state is inconsistent (fileCur=%d, FileAt=%d) — a "+
				"dead action would read as live from it", p.Title(), a.fileCur,
				a.Diff.FileAt(a.diffCur))
		}
		states = append(states, walkState{"big-diff-mid-" + p.Title(), a})
	}
	// The two-file fixture, so `}` and `]` have a following file and hunk to
	// jump to — the big fixture has exactly one of each.
	twoFiles := ready(t)
	twoFiles.Focus = PanelDiff
	states = append(states, walkState{"two-file-diff", twoFiles})

	// ⚠ AND THE SAME FIXTURE PARKED IN THE SECOND FILE, WHICH IS THE ONLY
	// STATE `{` AND `[` CAN DO ANYTHING FROM. Without it `PrevFile` reads as a
	// dead action — and, before the consistency fix above, it read as a LIVE one
	// for the wrong reason: the fall-through was dragging `fileCur` around.
	secondFile := ready(t)
	secondFile.Focus = PanelDiff
	secondFile.diffCur = secondFile.Diff.FileStart(1)
	secondFile.syncFileCursorFromDiff()
	secondFile.syncDiffViewport()
	if secondFile.Diff.FileAt(secondFile.diffCur) != 1 {
		t.Fatalf("the second-file state is not in the second file (FileAt = %d)",
			secondFile.Diff.FileAt(secondFile.diffCur))
	}
	states = append(states, walkState{"two-file-diff-second-file", secondFile})

	// An issue body long enough for the Overview's own viewport to move.
	states = append(states, walkState{"long-issue-overview", longIssue(t)})

	// A compose buffer with the cursor NOT at the end, so `right` has room.
	// (`left` already has room in `composing-with-text`.)
	mid, _ := ready(t).Step(keyPress("c"))
	for _, k := range []string{"h", "i", "left"} {
		mid, _ = mid.Step(keyPress(k))
	}
	states = append(states, walkState{"composing-cursor-not-at-end", mid})

	return states
}

// liveActions presses every key of every bound binding in every state OF THAT
// BINDING'S MODE and returns, for each action that measurably did something, the
// state and key that proved it.
//
// ⚠ IT PRESSES THE KEY AND WATCHES THE WHOLE APP, rather than calling `act()`
// directly. Calling `act()` would prove a handler exists while saying nothing
// about whether any KEY reaches it, which is the other half of "the legend is
// honest".
func liveActions(t *testing.T, states []walkState, bounds []Bound) map[Action]string {
	t.Helper()
	live := map[Action]string{}
	for _, s := range states {
		before := observe(s.app)
		for _, b := range bounds {
			if b.Mode != s.app.Mode() {
				continue
			}
			for _, k := range b.Binding.Keys() {
				next, intents := s.app.Step(keyPress(k))
				if len(intents) == 0 && observe(next) == before {
					continue
				}
				if _, seen := live[b.Action]; !seen {
					live[b.Action] = s.name + " + " + k
				}
			}
		}
	}
	return live
}

// deadAmong is the comparison, extracted so the positive control below exercises
// the SAME code rather than a second copy of it.
func deadAmong(bounds []Bound, live map[Action]string) []Action {
	var dead []Action
	seen := map[Action]bool{}
	for _, b := range bounds {
		if live[b.Action] != "" || seen[b.Action] {
			continue
		}
		seen[b.Action] = true
		dead = append(dead, b.Action)
	}
	return dead
}

// 🔴 EVERY DISPATCHED ACTION IS LIVE, AND EVERY HELP ENTRY NAMES A LIVE ACTION.
func TestEveryDispatchedActionDoesSomethingAndEveryHelpEntryNamesOne(t *testing.T) {
	states := liveStates(t)
	live := liveActions(t, states, Dispatch())

	for _, a := range deadAmong(Dispatch(), live) {
		t.Errorf("action %s is dispatched on — and rendered into the footer — but no "+
			"key press in any of the %d reachable states changed anything. It is a "+
			"legend entry for a key that does nothing.", a, len(states))
	}

	// The other direction: walk the HELP, not the dispatch table, and require
	// each entry to resolve to a live action. A help entry whose binding is in
	// no dispatch table at all is caught by the structural ledger above; this
	// catches the one that IS dispatched and is dead.
	byBinding := map[string]Action{}
	for _, b := range Dispatch() {
		byBinding[bindingID(b.Binding)] = b.Action
	}
	helped := 0
	for _, m := range Modes() {
		for _, b := range Keys.helpedBindingsFor(m) {
			helped++
			act, ok := byBinding[bindingID(b)]
			if !ok {
				continue // the structural ledger's job, and it reports it
			}
			if live[act] == "" {
				t.Errorf("mode %s: the footer advertises %q (%s) and pressing it "+
					"changes nothing", m.Word(), bindingID(b), act)
			}
		}
	}

	// 🔴 POSITIVE CONTROLS. Two empty sets compare equal, so "nothing was dead"
	// is indistinguishable from "nothing was measured".
	if len(live) == 0 {
		t.Fatal("no action did anything — this ledger observed nothing")
	}
	if helped == 0 {
		t.Fatal("no help entries were walked — this ledger observed nothing")
	}
	if len(live) < len(Dispatch())/2 {
		t.Fatalf("only %d of %d dispatched actions were observed live — that is a "+
			"broken state list, not a small keymap", len(live), len(Dispatch()))
	}
	t.Logf("proved %d actions live across %d states, and walked %d help entries",
		len(live), len(states), helped)
}

// 🔴 POSITIVE AND NEGATIVE CONTROLS ON THE BEHAVIOURAL LEDGER ITSELF. The test
// above can only be believed if (a) its observer can tell "changed" from
// "unchanged", and (b) its comparison can report a dead action. A `observe` that
// always returned a fresh value — the `reflect.DeepEqual`-over-funcs failure —
// would mark every action live and pass silently.
func TestTheBehaviouralLedgerCanActuallyGoRed(t *testing.T) {
	a := ready(t)

	// (a) NEGATIVE CONTROL on the observer: the same App twice is unchanged...
	if observe(a) != observe(a) {
		t.Fatal("observe() is not stable across two reads of one App — every action " +
			"would be reported live and the ledger would be inert")
	}
	// ...and a key that is bound to NOTHING leaves it unchanged. This is the
	// assertion the func-field trap fails.
	if idle, _ := a.Step(keyPress("z")); observe(idle) != observe(a) {
		t.Error("pressing an UNBOUND key changed the observed state — observe() is " +
			"reporting change unconditionally, so the ledger cannot see a dead action")
	}
	// (b) POSITIVE CONTROL on the observer: a key that is bound moves it.
	if quit, _ := a.Step(keyPress("q")); observe(quit) == observe(a) {
		t.Error("pressing `q` did not change the observed state — observe() is blind, " +
			"so every action would be reported dead or the projection is missing fields")
	}

	// (c) POSITIVE CONTROL on the comparison: a fabricated action bound to a key
	// nothing handles must be reported dead, and a real one must not.
	ghost := Bound{ModeBrowse, "TestOnlyGhostAction",
		key.NewBinding(key.WithKeys("z"), key.WithHelp("z", "ghost"))}
	bounds := []Bound{ghost, {ModeBrowse, ActQuit, Keys.Quit}}
	live := liveActions(t, liveStates(t), bounds)
	dead := deadAmong(bounds, live)
	if len(dead) != 1 || dead[0] != ghost.Action {
		t.Errorf("deadAmong reported %v, want exactly [%s] — the ledger cannot see an "+
			"action that does nothing", dead, ghost.Action)
	}
	if live[ActQuit] == "" {
		t.Error("the walk did not see `q` do anything — it is not driving Step at all")
	}
}

// 🔴 THE NEW BINDING IS IN THE LEGEND, SPELLED OUT. An invisible binding is the
// defect this whole keymap arc exists to fix, so `J`/`K` are asserted by their
// LITERAL rendered words rather than by echoing `Keys.ScrollDiffDown.Help()`
// back at itself — which would pass against an empty legend and an empty
// binding alike.
func TestTheLegendCarriesTheDiffScrollBinding(t *testing.T) {
	a := ready(t)
	a.Width, a.Height = 140, 40
	a.relayout()

	full, intents := a.Step(keyPress("?"))
	if len(intents) != 0 {
		t.Errorf("`?` emitted %v", intents)
	}
	if !full.showFull {
		t.Fatal("`?` did not expand the help")
	}
	legend := stripANSI(full.renderFooter())

	for _, want := range []string{"J scroll diff down", "K scroll diff up"} {
		// ⚠ The rendered help pads between the key and the description, so the
		// two halves are checked separately rather than as one spaced literal.
		parts := strings.SplitN(want, " ", 2)
		if !strings.Contains(legend, parts[1]) {
			t.Errorf("the expanded legend does not carry %q\nlegend:\n%s", parts[1], legend)
		}
		if !strings.Contains(legend, parts[0]) {
			t.Errorf("the expanded legend does not carry the key %q\nlegend:\n%s", parts[0], legend)
		}
	}
	// POSITIVE CONTROL for the matcher: a phrase in no binding must be absent.
	if strings.Contains(legend, "scroll diff sideways") {
		t.Error("the legend matcher matches text that is in no binding")
	}
}

// 🔴 EVERY MODE IN `Modes()` IS ACTUALLY WALKED BY THE LEDGER ABOVE, AND THAT
// IS A SEPARATE CLAIM FROM THE LEDGER PASSING. A mode added to the enum but
// given no table would make the loop above silently skip it — the positive
// controls catch that — while a mode REMOVED from `Modes()` would take its
// table out of every ledger with no test noticing. This pins the other
// direction: every mode a binding declares must be one `Modes()` enumerates.
func TestEveryModeABindingDeclaresIsEnumerated(t *testing.T) {
	enumerated := map[Mode]bool{}
	for _, m := range Modes() {
		enumerated[m] = true
	}
	for _, b := range Dispatch() {
		if !enumerated[b.Mode] {
			t.Errorf("binding %s declares mode %d, which Modes() does not enumerate — "+
				"its table is invisible to every per-mode ledger", b.Action, int(b.Mode))
		}
	}
	// And the mirror: a mode nobody binds is a mode whose footer renders
	// nothing, which the per-mode positive control above also catches. Assert
	// it here too so the failure names the mode rather than the emptiness.
	used := map[Mode]bool{}
	for _, b := range Dispatch() {
		used[b.Mode] = true
	}
	for _, m := range Modes() {
		if !used[m] {
			t.Errorf("mode %s is enumerated but no binding declares it", m.Word())
		}
	}
}

// 🔴 POSITIVE CONTROL ON THE LEDGER ITSELF. The test above can only be believed
// if its comparison CAN fail — a matcher that considers everything equal would
// pass identically. This feeds it a deliberately unhelped binding and a
// deliberately undispatched help entry and asserts it reports BOTH, with each
// direction's own wording.
func TestTheKeymapLedgerComparisonCanActuallyGoRed(t *testing.T) {
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
	for _, m := range Modes() {
		full := map[string]bool{}
		for _, b := range Keys.helpedBindingsFor(m) {
			full[bindingID(b)] = true
		}
		for _, b := range Keys.ShortHelpFor(m) {
			if !full[bindingID(b)] {
				t.Errorf("mode %s: ShortHelp carries %q, which FullHelp does not",
					m.Word(), bindingID(b))
			}
		}
		if len(Keys.ShortHelpFor(m)) == 0 {
			t.Errorf("mode %s: ShortHelp is empty — the persistent footer would "+
				"render nothing in that mode", m.Word())
		}
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
// ⚠ PER MODE, because the modes are DISJOINT: `ctrl+d` is `half page down` in
// browse and `send` in compose, and neither can shadow the other because `Step`
// only ever walks one table. Comparing across modes would forbid a reuse that
// is safe by construction; not comparing WITHIN a mode would miss the real one.
func TestNoKeyIsClaimedByTwoActions(t *testing.T) {
	for _, m := range Modes() {
		owner := map[string]Action{}
		for _, b := range DispatchFor(m) {
			for _, k := range b.Binding.Keys() {
				if prev, dup := owner[k]; dup {
					t.Errorf("mode %s: key %q is claimed by both %s and %s; the second is dead",
						m.Word(), k, prev, b.Action)
				}
				owner[k] = b.Action
			}
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
	for _, b := range Keys.ShortHelpFor(ModeBrowse) {
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
	"tab":       tea.KeyTab,
	"esc":       tea.KeyEscape,
	"up":        tea.KeyUp,
	"down":      tea.KeyDown,
	"pgup":      tea.KeyPgUp,
	"pgdown":    tea.KeyPgDown,
	"home":      tea.KeyHome,
	"end":       tea.KeyEnd,
	"left":      tea.KeyLeft,
	"right":     tea.KeyRight,
	"enter":     tea.KeyEnter,
	"backspace": tea.KeyBackspace,
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
