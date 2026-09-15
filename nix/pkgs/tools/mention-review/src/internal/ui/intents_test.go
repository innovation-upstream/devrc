package ui

import (
	"sort"
	"strings"
	"testing"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
)

// 🔴 LAYER 3(b) — THE REGISTRY AND THE §3.7 CONFIRMATION LEDGER, BOTH DRIVEN
// FROM THE KEYBOARD.
//
// ⚠ THE LEDGER WAS DELETED IN PHASE 1 AND IS BACK, AND THE REASON IT WAS
// DELETED IS THE STANDARD IT NOW HAS TO MEET. Phase 1 emitted no write intents,
// so `LedgerViolations(KnownIntents())` was a provable constant `nil` over two
// empty map literals — a guard that READS as coverage while providing none,
// which this repo holds to be worse than no guard. The test that makes it
// non-vacuous this time is not the one asserting the violations are empty; it
// is `TestTheDispatchWalkEmitsWriteIntents`, which asserts the keyboard walk
// PRODUCES write intents. Without that, every assertion below is satisfied by a
// program with no write verbs at all.

// unregisteredIntent exists ONLY in this file. It is the positive control for
// `Run`'s panic backstop below: an intent `Run` does not handle must panic, or
// the loop that walks the registry proves nothing.
type unregisteredIntent struct{}

func (unregisteredIntent) intentName() string { return "TestOnlyUnregistered" }
func (unregisteredIntent) Write() bool        { return false }

// testWrite is a synthetic WRITE intent used by the ledger's positive controls.
// It is never registered and never emitted — it exists so the controls can feed
// `ledgerViolations` a case that MUST be reported.
type testWrite struct{ name string }

func (w testWrite) intentName() string { return w.name }
func (testWrite) Write() bool          { return true }

// walkState is one reachable app state plus how it was reached, so a failure
// names something a human can reproduce.
type walkState struct {
	name string
	app  App
}

// reachableStates builds the states the walks below drive.
//
// 🔴 EVERY MODAL STATE IS REACHED BY PRESSING A KEY, NOT BY SETTING A FIELD. A
// test that assigned `a.mode = ModeConfirm` would be asserting against a state
// the program might have no way to enter — and would keep passing if the key
// that is supposed to enter it stopped working.
func reachableStates(t *testing.T) []walkState { return buildStates(t, false) }

// browseStates is the subset built WITHOUT pressing any key. See the comment
// inside `buildStates` for why the browse sweep must not share the modal list.
func browseStates(t *testing.T) []walkState { return buildStates(t, true) }

func buildStates(t *testing.T, browseOnly bool) []walkState {
	t.Helper()
	base := func() App {
		a := New(fxOwner, fxName, fxNum)
		a.Width, a.Height = 140, 40
		a.SetMergeMethod(fxMergeMethod)
		return a
	}
	readyPR := func() App {
		a, _ := base().Step(PRLoaded{Snap: fixturePR()})
		return a
	}
	press := func(a App, keys ...string) App {
		for _, k := range keys {
			a, _ = a.Step(keyPress(k))
		}
		return a
	}
	noLogin := func() App {
		s := fixturePR()
		s.ViewerLogin = "" // the §10.2 hazard, as a state
		a, _ := base().Step(PRLoaded{Snap: s})
		return a
	}
	noMethod := func() App {
		a := New(fxOwner, fxName, fxNum)
		a.Width, a.Height = 140, 40
		// MergeMethod deliberately left UNKNOWN.
		a, _ = a.Step(PRLoaded{Snap: fixturePR()})
		return a
	}

	// 🔴 THE BROWSE STATES ARE BUILT WITHOUT PRESSING ANY KEY, AND THAT
	// SEPARATION IS LOAD-BEARING. `browseStates` below is what the
	// no-confirmed-write-from-browse sweep drives, and it must not depend on a
	// key chain — a mutant that made `m` fire a merge directly ALSO breaks the
	// chain that builds the `confirm-merge` fixture, so a combined list kills
	// that mutant with the FIXTURE's error instead of the sweep's. Measured:
	// it did exactly that on the first run of this battery.
	browse := []walkState{
		{"loading", base()},
		{"ready-pr", readyPR()},
		{"ready-issue", func() App {
			a, _ := base().Step(PRLoaded{Snap: fixtureIssue()})
			return a
		}()},
		{"failed", func() App {
			a, _ := base().Step(PRLoaded{Err: &ghapi.APIError{State: ghapi.AuthNoToken}})
			return a
		}()},
		{"ready-pr-no-viewer-login", noLogin()},
		{"ready-pr-unknown-merge-method", noMethod()},
	}
	for _, s := range browse {
		if s.app.Mode() != ModeBrowse {
			t.Fatalf("state %q is in mode %s, want BROWSE", s.name, s.app.Mode().Word())
		}
	}
	if len(browse) < 4 {
		t.Fatal("the browse state list is too short to be measuring anything")
	}
	if browseOnly {
		return browse
	}

	states := append([]walkState(nil), browse...)
	states = append(states, []walkState{
		{"composing-comment", press(readyPR(), "c")},
		{"composing-request-changes", press(readyPR(), "R")},
		{"composing-submit-review", press(readyPR(), "v")},
		{"composing-with-text", press(readyPR(), "c", "h", "i")},
		{"confirm-approve", press(readyPR(), "a")},
		{"confirm-merge", press(readyPR(), "m")},
		{"confirm-request-changes", press(readyPR(), "R", "n", "o", "ctrl+d")},
		// ⚠ THIS STATE WAS MISSING IN THE FIRST DRAFT AND
		// `TestTheDispatchWalkEmitsWriteIntents` FOUND IT — the walk emitted
		// four of the five write verbs and `SubmitReview` was never reachable
		// from any state it drove. That is exactly the vacuity the write-verb
		// positive control exists to catch, caught on its first run.
		{"confirm-submit-review", press(readyPR(), "v", "n", "o", "ctrl+d")},
	}...)

	// 🔴 THE MODAL FIXTURES MUST BE THE STATES THEY CLAIM TO BE. A `press` chain
	// that silently failed to change mode would make every modal walk a second
	// copy of the browse walk — passing, and measuring nothing.
	want := map[string]Mode{
		"composing-comment":         ModeCompose,
		"composing-request-changes": ModeCompose,
		"composing-submit-review":   ModeCompose,
		"composing-with-text":       ModeCompose,
		"confirm-approve":           ModeConfirm,
		"confirm-merge":             ModeConfirm,
		"confirm-request-changes":   ModeConfirm,
		"confirm-submit-review":     ModeConfirm,
	}
	for _, s := range states {
		w, ok := want[s.name]
		if !ok {
			continue // a browse state, already checked above
		}
		if s.app.Mode() != w {
			t.Fatalf("state %q is in mode %s, want %s — the key chain that builds "+
				"it no longer reaches that mode, so every walk over it is vacuous",
				s.name, s.app.Mode().Word(), w.Word())
		}
	}
	return states
}

// emittedByKeyboard drives every binding of each state's OWN mode and returns
// what came out, keyed by a human-readable origin.
func emittedByKeyboard(t *testing.T, states []walkState) map[string][]Intent {
	t.Helper()
	out := map[string][]Intent{}
	for _, s := range states {
		for _, b := range DispatchFor(s.app.Mode()) {
			for _, k := range b.Binding.Keys() {
				_, intents := s.app.Step(keyPress(k))
				if len(intents) > 0 {
					out[s.name+" "+string(b.Action)+" ("+k+")"] = intents
				}
			}
		}
	}
	return out
}

// 🔴 THIS IS WHAT MAKES THE REGISTRY LOAD-BEARING RATHER THAN DECORATIVE.
//
// Go cannot enumerate every implementation of an interface at run time, so the
// registry has to be hand-written — which is exactly the shape that rots. This
// drives `Step` over EVERY binding in every mode, in every reachable app state,
// and asserts that every intent it emits is registered. An unregistered intent
// therefore fails the suite at the moment a KEY can produce it, not at the
// moment someone remembers to list it.
func TestEveryIntentStepCanEmitIsRegistered(t *testing.T) {
	registered := map[string]bool{}
	for _, i := range KnownIntents() {
		registered[i.intentName()] = true
	}

	seen := map[string]bool{}
	for origin, intents := range emittedByKeyboard(t, reachableStates(t)) {
		for _, i := range intents {
			n := i.intentName()
			seen[n] = true
			if !registered[n] {
				t.Errorf("%s emitted intent %q, which is not in KnownIntents()", origin, n)
			}
		}
	}

	// The message-driven half. 🔴 `WriteDone` EMITS AN INTENT WITHOUT A
	// KEYPRESS — it re-reads the PR after a successful write — so a walk that
	// only pressed keys would be blind to the one intent-producing path that no
	// key reaches.
	a := ready(t)
	for name, msg := range map[string]any{
		"WriteDone-ok":     WriteDone{Verb: "PostComment"},
		"WriteDone-failed": WriteDone{Verb: "MergePR", Err: &ghapi.APIError{State: ghapi.AuthRejected}},
		"PRLoaded-pr":      PRLoaded{Snap: fixturePR()},
		"PRLoaded-issue":   PRLoaded{Snap: fixtureIssue()},
		"DiffLoaded-ok":    DiffLoaded{Diff: fixtureDiff(t)},
	} {
		_, intents := a.Step(msg)
		for _, i := range intents {
			n := i.intentName()
			seen[n] = true
			if !registered[n] {
				t.Errorf("message %s emitted intent %q, which is not in KnownIntents()", name, n)
			}
		}
	}

	// 🔴 POSITIVE CONTROL: the walk must have SEEN intents. A walk that emitted
	// nothing would pass the loops above identically, and "every intent it
	// emitted was registered" over an empty set is the silent zero.
	if len(seen) == 0 {
		t.Fatal("the walk emitted NO intents at all — this guard observed nothing")
	}
	names := make([]string, 0, len(seen))
	for n := range seen {
		names = append(names, n)
	}
	sort.Strings(names)
	t.Logf("the walk emitted %d distinct intents: %v", len(seen), names)
}

// 🔴 THE ASSERTION THAT MAKES THE WHOLE LEDGER NON-VACUOUS. Phase 1's ledger
// was deleted because it compared two empty maps; if this test ever passes
// trivially the ledger is back to being decoration. So it asserts, by NAME,
// that the keyboard produces each of the five §3.7 write verbs.
func TestTheDispatchWalkEmitsWriteIntents(t *testing.T) {
	emitted := emittedByKeyboard(t, reachableStates(t))
	writes := map[string]bool{}
	for _, intents := range emitted {
		for _, i := range intents {
			if i.Write() {
				writes[i.intentName()] = true
			}
		}
	}
	for _, want := range []string{
		"PostComment", "Approve", "RequestChanges", "SubmitReview", "MergePR",
	} {
		if !writes[want] {
			t.Errorf("no reachable keypress emits the write intent %q — "+
				"the §3.7 ledger entry for it is guarding nothing", want)
		}
	}
	t.Logf("the keyboard emitted %d distinct WRITE intents", len(writes))
}

// 🔴 THE LEDGER AND THE REGISTRY AGREE, TWO-WAY (§5.3(b)).
func TestTheLiveLedgerAndTheRegistryAgree(t *testing.T) {
	if v := LedgerViolations(KnownIntents()); len(v) != 0 {
		t.Errorf("the live ledger disagrees with the registry:\n  %s",
			strings.Join(v, "\n  "))
	}
	// A second, independent claim: the ledger is not EMPTY. `LedgerViolations`
	// returns nothing for two empty maps over zero write intents — the exact
	// state Phase 1 deleted — so agreement alone is not evidence of coverage.
	if len(Confirmed) == 0 {
		t.Error("CONFIRMED is empty — no verb prompts, which is not §3.7")
	}
	if len(NotConfirmed) == 0 {
		t.Error("NOT_CONFIRMED is empty — §3.7 lists a comment as unconfirmed")
	}
}

// 🔴 POSITIVE CONTROLS ON THE LEDGER ITSELF, each asserting THIS guard's own
// error. A mutation killed by a different test's error is green for the wrong
// reason and stays green with this guard deleted.
//
// ⚠ They drive `ledgerViolations` — the same function `LedgerViolations` calls,
// with maps passed in — rather than mutating the package-level `Confirmed` and
// `NotConfirmed`, which another test in this same binary reads.
func TestTheConfirmationLedgerComparisonCanActuallyGoRed(t *testing.T) {
	cases := []struct {
		name         string
		intents      []Intent
		confirmed    map[string]bool
		notConfirmed map[string]string
		want         string
	}{
		{
			name:         "a write verb in neither set",
			intents:      []Intent{testWrite{"Detonate"}},
			confirmed:    map[string]bool{},
			notConfirmed: map[string]string{},
			want:         `write intent "Detonate" is in neither CONFIRMED nor NOT_CONFIRMED`,
		},
		{
			name:         "a write verb in both sets",
			intents:      []Intent{testWrite{"Detonate"}},
			confirmed:    map[string]bool{"Detonate": true},
			notConfirmed: map[string]string{"Detonate": "because"},
			want:         `write intent "Detonate" is in BOTH CONFIRMED and NOT_CONFIRMED`,
		},
		{
			name:         "a READ intent ledgered as confirmed",
			intents:      []Intent{FetchPR{}},
			confirmed:    map[string]bool{"FetchPR": true},
			notConfirmed: map[string]string{},
			want:         `read intent "FetchPR" is listed in CONFIRMED`,
		},
		{
			name:         "a ledger entry naming no registered intent",
			intents:      []Intent{FetchPR{}},
			confirmed:    map[string]bool{"Ghost": true},
			notConfirmed: map[string]string{},
			want:         `CONFIRMED names "Ghost", which is not a registered intent`,
		},
		{
			name:         "an unconfirmed write with no stated reason",
			intents:      []Intent{testWrite{"Detonate"}},
			confirmed:    map[string]bool{},
			notConfirmed: map[string]string{"Detonate": ""},
			want:         `NOT_CONFIRMED lists "Detonate" with an EMPTY reason`,
		},
		{
			name:         "the same intent registered twice",
			intents:      []Intent{FetchPR{}, FetchPR{}},
			confirmed:    map[string]bool{},
			notConfirmed: map[string]string{},
			want:         `intent "FetchPR" is registered more than once`,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := ledgerViolations(c.intents, c.confirmed, c.notConfirmed)
			found := false
			for _, g := range got {
				if g == c.want {
					found = true
				}
			}
			if !found {
				t.Errorf("ledgerViolations reported %v\nwant it to contain: %s", got, c.want)
			}
		})
	}

	// 🔴 AND THE NEGATIVE CONTROL: a consistent ledger reports NOTHING. Without
	// it, every assertion above is satisfied by a function that reports every
	// possible complaint unconditionally.
	clean := ledgerViolations(
		[]Intent{FetchPR{}, testWrite{"Detonate"}},
		map[string]bool{"Detonate": true},
		map[string]string{},
	)
	if len(clean) != 0 {
		t.Errorf("a consistent ledger reported %v, want nothing", clean)
	}
}

// 🔴 NO CONFIRMED VERB IS EVER ONE KEYPRESS AWAY. This is §5.5's row: "make `m`
// fire the merge directly with no confirmation" must break a test that asserted
// `intents` was empty. It is asserted over EVERY browse-mode key in EVERY
// reachable state, not only over `m`, so a sixth verb wired straight to a key
// fails it too.
func TestNoConfirmedWriteIsEmittedFromBrowseMode(t *testing.T) {
	checked := 0
	// ⚠ `browseStates`, NOT `reachableStates` — see `buildStates`. The modal
	// fixtures are built by pressing keys, so the very mutation this test
	// exists to catch also breaks them, and a combined list kills the mutant
	// with the FIXTURE's error instead of this assertion's.
	for _, s := range browseStates(t) {
		if s.app.Mode() != ModeBrowse {
			continue
		}
		for _, b := range DispatchFor(ModeBrowse) {
			for _, k := range b.Binding.Keys() {
				next, intents := s.app.Step(keyPress(k))
				checked++
				for _, i := range intents {
					if RequiresConfirmation(i) {
						t.Errorf("state %q: pressing %q (%s) emitted the CONFIRMED "+
							"write intent %q with no prompt in between",
							s.name, k, b.Action, i.intentName())
					}
				}
				_ = next
			}
		}
	}
	if checked == 0 {
		t.Fatal("no browse-mode key was pressed — this guard observed nothing")
	}
	t.Logf("pressed %d browse-mode keys, none emitted a confirmed write", checked)
}

// Every registered intent must be handled by `Run`. An unhandled one is a
// keypress that does nothing, forever, with no error anywhere.
func TestEveryRegisteredIntentIsHandledByRun(t *testing.T) {
	for _, i := range KnownIntents() {
		func() {
			defer func() {
				if r := recover(); r != nil {
					t.Errorf("Run does not handle %q: %v", i.intentName(), r)
				}
			}()
			if cmd := Run(i, stubRunner{}); cmd == nil {
				t.Errorf("Run(%q) returned a nil command", i.intentName())
			}
		}()
	}
	// POSITIVE CONTROL on the panic detector: an intent Run does NOT handle
	// must panic, or the loop above proves nothing.
	func() {
		defer func() {
			if r := recover(); r == nil {
				t.Error("Run accepted an unhandled intent without panicking — " +
					"a new intent could be added and silently do nothing")
			}
		}()
		_ = Run(unregisteredIntent{}, stubRunner{})
	}()
}
