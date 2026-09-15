package ui

import (
	"sort"
	"testing"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
)

// 🔴 LAYER 3(b) — THE REGISTRY, DRIVEN FROM THE KEYBOARD.
//
// ⚠ THE §3.7 CONFIRMATION LEDGER IS NOT HERE, AND ITS ABSENCE IS THE POINT.
// Phase 1 emits no write intents, so `LedgerViolations(KnownIntents())` was a
// provable constant `nil` over two empty map literals — a guard that READS as
// coverage while providing none, which this repo holds to be worse than no
// guard. It is deleted rather than carried: the Intent SEAM is what had to
// exist from day one, and the ledger is purely additive over it. 🔴 PHASE 2 —
// the first write verb — MUST REINTRODUCE IT, with the positive control that
// version carried (a test-local write intent in neither set, reported with the
// guard's own error string).
//
// What survives is the half that was never vacuous: the registry is driven from
// `Dispatch()` over every reachable app state, and every registered intent must
// be handled by `Run`.

// unregisteredIntent exists ONLY in this file. It is the positive control for
// `Run`'s panic backstop below: an intent `Run` does not handle must panic, or
// the loop that walks the registry proves nothing.
type unregisteredIntent struct{}

func (unregisteredIntent) intentName() string { return "TestOnlyUnregistered" }

// 🔴 THIS IS WHAT MAKES THE REGISTRY LOAD-BEARING RATHER THAN DECORATIVE.
//
// Go cannot enumerate every implementation of an interface at run time, so the
// registry has to be hand-written — which is exactly the shape that rots. This
// drives `Step` over EVERY binding in `Dispatch()`, in every reachable app
// state, and asserts that every intent it emits is registered. An unregistered
// intent therefore fails the suite at the moment a KEY can produce it, not at
// the moment someone remembers to list it.
func TestEveryIntentStepCanEmitIsRegistered(t *testing.T) {
	registered := map[string]bool{}
	for _, i := range KnownIntents() {
		registered[i.intentName()] = true
	}

	states := map[string]App{
		"loading": New("gardenersguild", "trowelcast", 1559),
		"ready-pr": func() App {
			a, _ := New("gardenersguild", "trowelcast", 1559).Step(PRLoaded{Snap: fixturePR()})
			return a
		}(),
		"ready-issue": func() App {
			a, _ := New("gardenersguild", "trowelcast", 1559).Step(PRLoaded{Snap: fixtureIssue()})
			return a
		}(),
		"failed": func() App {
			a, _ := New("gardenersguild", "trowelcast", 1559).Step(
				PRLoaded{Err: &ghapi.APIError{State: ghapi.AuthNoToken}})
			return a
		}(),
	}

	seen := map[string]bool{}
	for name, app := range states {
		for _, b := range Dispatch() {
			for _, k := range b.Binding.Keys() {
				_, intents := app.Step(keyPress(k))
				for _, i := range intents {
					n := i.intentName()
					seen[n] = true
					if !registered[n] {
						t.Errorf("state %q key %q (%s) emitted intent %q, "+
							"which is not in KnownIntents()", name, k, b.Action, n)
					}
				}
			}
		}
	}

	// 🔴 POSITIVE CONTROL: the walk must have SEEN intents. A walk that emitted
	// nothing would pass the loop above identically, and "every intent it
	// emitted was registered" over an empty set is the silent zero.
	if len(seen) == 0 {
		t.Fatal("the dispatch walk emitted NO intents at all — this guard " +
			"observed nothing")
	}
	names := make([]string, 0, len(seen))
	for n := range seen {
		names = append(names, n)
	}
	sort.Strings(names)
	t.Logf("dispatch walk emitted %d distinct intents: %v", len(seen), names)
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
