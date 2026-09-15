package ui

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
//
// ⚠ NO `Write() bool` YET, AND THAT IS A PHASING DECISION, NOT AN OVERSIGHT.
// Phase 1 is read-only: every intent below returns false, so the predicate and
// the confirmation ledger built on it were a provable constant. Phase 2 — the
// first write verb — MUST reintroduce both, and it can: the ledger is purely
// additive over this seam and needs no handler rewritten. It is the SEAM that
// had to exist from day one, not the ledger over it.
type Intent interface {
	// intentName is the registry key. Unexported so nothing outside this
	// package can add an intent the registry has never seen.
	intentName() string
}

// --- the intents Phase 1 can emit -------------------------------------------

// FetchPR is the one GraphQL read.
type FetchPR struct {
	Owner string
	Name  string
	Num   int
}

func (FetchPR) intentName() string { return "FetchPR" }

// FetchDiff is the REST files+patch read.
type FetchDiff struct {
	Owner string
	Name  string
	Num   int
}

func (FetchDiff) intentName() string { return "FetchDiff" }

// OpenBrowser hands a URL to xdg-open.
//
// ⚠ IT IS NOT A GITHUB WRITE. It leaves this machine, but it changes nothing on
// the server, so when Phase 2 reintroduces the write predicate this one stays
// false — a judgement worth writing down rather than leaving to be re-derived.
type OpenBrowser struct{ URL string }

func (OpenBrowser) intentName() string { return "OpenBrowser" }

// --- the registry -----------------------------------------------------------

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
