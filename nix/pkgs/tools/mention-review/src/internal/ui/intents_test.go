package ui

import (
	"sort"
	"strings"
	"testing"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
)

// 🔴 LAYER 3(b) — THE DESTRUCTIVE-VERB LEDGER, TWO-WAY.
//
// An enumerated CONFIRMED set and an explicitly enumerated NOT_CONFIRMED set;
// their union must equal the set of WRITE intents. A new write verb in neither
// list fails the suite, in either direction.
//
// 🔴 PHASE 1 HAS NO WRITE INTENTS, SO THE PRODUCTION HALF OF THIS GUARD IS
// LEGITIMATELY VACUOUS — and a vacuous guard reads as coverage while providing
// none, which this repo holds to be worse than no guard at all. So the guard is
// not left to speak for itself: `TestTheLedgerCatchesAnUnledgeredWriteIntent`
// feeds it a write intent that is in neither set and asserts it reports it,
// with THIS guard's own error string. Report the pair, never the bare zero.

func TestTheProductionLedgerAndRegistryAgree(t *testing.T) {
	v := LedgerViolations(KnownIntents())
	if len(v) != 0 {
		t.Errorf("ledger violations:\n  %s", strings.Join(v, "\n  "))
	}
	// 🔴 THE POSITIVE CONTROL'S OTHER HALF, stated in the same test so the zero
	// above is never read alone: the registry is NOT empty, so "no violations"
	// is a claim about real intents rather than about an empty loop.
	if len(KnownIntents()) == 0 {
		t.Fatal("the intent registry is EMPTY — the zero above means nothing")
	}
	t.Logf("checked %d registered intents, %d of them writes; violations=0",
		len(KnownIntents()), countWrites(KnownIntents()))
}

// testWrite is a write intent that exists ONLY in this file. It is the positive
// control for the ledger: production has no write intents yet, so without it
// the guard could be wired to nothing and still be green.
type testWrite struct{}

func (testWrite) intentName() string { return "TestOnlyWrite" }
func (testWrite) Write() bool        { return true }

func TestTheLedgerCatchesAnUnledgeredWriteIntent(t *testing.T) {
	v := LedgerViolations(append(KnownIntents(), testWrite{}))
	want := `write intent "TestOnlyWrite" is in neither CONFIRMED nor NOT_CONFIRMED`
	if !containsString(v, want) {
		t.Fatalf("the ledger did NOT report an unledgered write intent.\n"+
			"got:  %v\nwant: %s", v, want)
	}
	// 🔴 AND IT MUST FAIL WITH *THIS* GUARD'S OWN ERROR. A violation reported
	// by some other arm would be green for the wrong reason and would stay
	// green with this arm deleted.
	if len(v) != 1 {
		t.Errorf("expected exactly the one violation, got %d: %v", len(v), v)
	}
}

func TestTheLedgerCatchesAnEntryNamingNoIntent(t *testing.T) {
	Confirmed["GhostVerb"] = true
	defer delete(Confirmed, "GhostVerb")

	v := LedgerViolations(KnownIntents())
	want := `CONFIRMED names "GhostVerb", which is not a registered intent`
	if !containsString(v, want) {
		t.Fatalf("the ledger did NOT report a dangling CONFIRMED entry.\n"+
			"got:  %v\nwant: %s", v, want)
	}
}

func TestTheLedgerCatchesAVerbInBothSets(t *testing.T) {
	Confirmed["TestOnlyWrite"] = true
	NotConfirmed["TestOnlyWrite"] = "for the control"
	defer func() {
		delete(Confirmed, "TestOnlyWrite")
		delete(NotConfirmed, "TestOnlyWrite")
	}()

	v := LedgerViolations(append(KnownIntents(), testWrite{}))
	want := `write intent "TestOnlyWrite" is in BOTH CONFIRMED and NOT_CONFIRMED`
	if !containsString(v, want) {
		t.Fatalf("got: %v\nwant: %s", v, want)
	}
}

// A READ intent must not be ledgered — a confirmation prompt for a read is
// noise, and an entry for one means somebody mislabelled the intent.
func TestTheLedgerCatchesALedgeredReadIntent(t *testing.T) {
	Confirmed["FetchPR"] = true
	defer delete(Confirmed, "FetchPR")

	v := LedgerViolations(KnownIntents())
	want := `read intent "FetchPR" is listed in CONFIRMED`
	if !containsString(v, want) {
		t.Fatalf("got: %v\nwant: %s", v, want)
	}
}

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
		_ = Run(testWrite{}, stubRunner{})
	}()
}

func countWrites(is []Intent) int {
	n := 0
	for _, i := range is {
		if i.Write() {
			n++
		}
	}
	return n
}

func containsString(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}
