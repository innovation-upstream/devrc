package ui

import (
	"fmt"
	"reflect"
	"sort"
)

// 🔴 THE COMMAND SEAM (§3.3) — the single most important design decision for
// testability, and it has to be in the design from day one because retrofitting
// it means rewriting every handler.
//
// `Step` is a PURE function. It may not perform I/O and it may not construct a
// closure that performs I/O, because a `tea.Cmd` is an opaque `func() tea.Msg`
// and a test cannot assert anything about one. So `Step` returns a TAGGED
// INTENT — plain data — and one thin, separately-tested runner (`run.go`)
// converts intents into `tea.Cmd`s.
//
// That is what makes an assertion like "pressing this key produces zero network
// intents" a real, mechanical test rather than a golden-frame snapshot.

// Intent is a request for the outside world, as data.
type Intent interface {
	// intentName is the ledger key. Unexported so nothing outside this package
	// can add an intent the ledger has never seen.
	intentName() string
	// Write reports whether this intent CHANGES anything on GitHub.
	//
	// 🔴 A METHOD, NOT A NAME PREFIX. A predicate that asked "does the type
	// name start with Merge/Approve/…" is a guard SPELLED rather than
	// STRUCTURAL: it passes while the hazard exists under a different
	// spelling. This one cannot be walked around by renaming.
	Write() bool
}

// --- the intents Phase 1 can emit -------------------------------------------

// FetchPR is the one GraphQL read.
type FetchPR struct {
	Owner string
	Name  string
	Num   int
}

func (FetchPR) intentName() string { return "FetchPR" }
func (FetchPR) Write() bool        { return false }

// FetchDiff is the REST files+patch read.
type FetchDiff struct {
	Owner string
	Name  string
	Num   int
}

func (FetchDiff) intentName() string { return "FetchDiff" }
func (FetchDiff) Write() bool        { return false }

// OpenBrowser hands a URL to xdg-open.
//
// ⚠ IT IS NOT A GITHUB WRITE. It leaves this machine, but it changes nothing on
// the server, so it is not in the confirmation ledger — and that is a judgement
// worth writing down rather than leaving to be re-derived.
type OpenBrowser struct{ URL string }

func (OpenBrowser) intentName() string { return "OpenBrowser" }
func (OpenBrowser) Write() bool        { return false }

// --- the ledger -------------------------------------------------------------

// KnownIntents is the REGISTRY. Every intent type this package can emit appears
// here exactly once.
//
// 🔴 A REGISTRY IS UNAVOIDABLE IN GO: there is no way to enumerate every
// implementation of an interface at run time. So the registry is made
// load-bearing instead of decorative — `intents_test.go` drives `Step` over
// EVERY binding in `Dispatch()` and asserts that every intent it emits is
// registered. An unregistered intent therefore fails the suite at the moment a
// key can produce it, not at the moment someone remembers to list it.
func KnownIntents() []Intent {
	return []Intent{
		FetchPR{},
		FetchDiff{},
		OpenBrowser{},
	}
}

// Confirmation is the §3.7 ledger, spelled as TWO explicitly enumerated sets.
//
// 🔴 TWO-WAY, AND BOTH SETS ARE EXPLICIT. An intent in NEITHER set fails the
// suite; an entry naming no registered intent fails the suite. A new write verb
// cannot be added silently in either direction. This mirrors what
// `CONFIRMED_VERBS` does in the Lua today, carried across structurally rather
// than re-implemented on trust.
//
// 🔴 PHASE 1 HAS NO WRITE INTENTS, AND THE LEDGER IS DELIBERATELY EMPTY RATHER
// THAN ABSENT. An empty ledger over an empty set is a VACUOUS guard — it would
// pass wired to nothing — so `intents_test.go` carries a POSITIVE CONTROL: a
// test-local write intent is fed to `LedgerViolations` and the guard must
// report it, with this guard's own error. Report the pair, never the zero.
var (
	// Confirmed — the verb prompts before it acts.
	Confirmed = map[string]bool{}

	// NotConfirmed — the verb acts immediately, and the reason it may is
	// stated per entry. Additive-and-trivially-reversible is the only reason
	// accepted so far (a posted comment can be deleted; a merge cannot).
	NotConfirmed = map[string]string{}
)

// LedgerViolations returns every way the ledger and the registry disagree.
// Empty means they agree.
//
// 🔴 IT RETURNS ALL OF THEM, NOT THE FIRST. A guard that stops at the first
// violation makes a second one invisible until the first is fixed, which turns
// one red run into three.
func LedgerViolations(intents []Intent) []string {
	var out []string
	registered := map[string]bool{}

	for _, i := range intents {
		n := i.intentName()
		if registered[n] {
			out = append(out, fmt.Sprintf(
				"intent %q is registered more than once", n))
		}
		registered[n] = true
		if !i.Write() {
			// A read intent must not be ledgered at all — a confirmation
			// prompt for a read is noise, and an entry for one means somebody
			// mislabelled the intent.
			if Confirmed[n] {
				out = append(out, fmt.Sprintf(
					"read intent %q is listed in CONFIRMED", n))
			}
			if _, ok := NotConfirmed[n]; ok {
				out = append(out, fmt.Sprintf(
					"read intent %q is listed in NOT_CONFIRMED", n))
			}
			continue
		}
		_, inNot := NotConfirmed[n]
		switch {
		case Confirmed[n] && inNot:
			out = append(out, fmt.Sprintf(
				"write intent %q is in BOTH CONFIRMED and NOT_CONFIRMED", n))
		case !Confirmed[n] && !inNot:
			out = append(out, fmt.Sprintf(
				"write intent %q is in neither CONFIRMED nor NOT_CONFIRMED", n))
		}
	}

	// The other direction: a ledger entry naming nothing.
	for n := range Confirmed {
		if !registered[n] {
			out = append(out, fmt.Sprintf(
				"CONFIRMED names %q, which is not a registered intent", n))
		}
	}
	for n := range NotConfirmed {
		if !registered[n] {
			out = append(out, fmt.Sprintf(
				"NOT_CONFIRMED names %q, which is not a registered intent", n))
		}
	}
	sort.Strings(out)
	return out
}

// IntentTypeName is the runtime type name of an intent, used by the test that
// checks emitted intents against the registry. It is here rather than in the
// test so the production and test views of "what type is this" cannot diverge.
func IntentTypeName(i Intent) string { return reflect.TypeOf(i).Name() }
