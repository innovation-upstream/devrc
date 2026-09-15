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
	//
	// 🔴 IT WAS DELETED IN PHASE 1 AND IS BACK ON PURPOSE. In Phase 1 every
	// implementation returned a constant `false`, so the ledger built on it was
	// provably `nil` over two empty maps — a guard that READS as coverage while
	// providing none, which this repo holds to be worse than no guard. Phase 2
	// emits five write intents, so it is no longer vacuous, and
	// `intents_test.go` proves that by asserting the keyboard walk EMITS a
	// write intent rather than by asserting a zero.
	Write() bool
}

// --- the read intents --------------------------------------------------------

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
// the server, so it is not in the confirmation ledger — a judgement worth
// writing down rather than leaving to be re-derived.
type OpenBrowser struct{ URL string }

func (OpenBrowser) intentName() string { return "OpenBrowser" }
func (OpenBrowser) Write() bool        { return false }

// --- the write intents (§3.7) ------------------------------------------------
//
// 🔴 FIVE TYPES, NOT ONE TYPE WITH AN `Event` FIELD. A single `SubmitReview{Event
// string}` would give the ledger ONE key covering three verbs of different
// consequence — a ledger narrower than the sentence describing it, which is the
// defect class this file exists to prevent. Approving and requesting changes are
// different acts; they get different keys in the ledger.

// PostComment posts a PR-LEVEL comment.
//
// 🔴 PR-LEVEL ONLY. Inline diff-line comments need review-thread positioning
// against the diff, which the operator ruled OUT of this phase (proposal §12.4,
// answered). This is `gh pr comment`, not a review thread, and nothing in this
// package computes a diff position.
type PostComment struct {
	Owner string
	Name  string
	Num   int
	Body  string
}

func (PostComment) intentName() string { return "PostComment" }
func (PostComment) Write() bool        { return true }

// Approve submits an APPROVE review with no body.
type Approve struct {
	Owner string
	Name  string
	Num   int
}

func (Approve) intentName() string { return "Approve" }
func (Approve) Write() bool        { return true }

// RequestChanges submits a REQUEST_CHANGES review.
//
// ⚠ GITHUB REQUIRES A BODY FOR THIS EVENT and answers 422 without one, so the
// compose step is not a nicety here — `app.go` refuses to build the intent from
// an empty buffer.
type RequestChanges struct {
	Owner string
	Name  string
	Num   int
	Body  string
}

func (RequestChanges) intentName() string { return "RequestChanges" }
func (RequestChanges) Write() bool        { return true }

// SubmitReview submits a COMMENT review — a review verdict that neither
// approves nor blocks.
type SubmitReview struct {
	Owner string
	Name  string
	Num   int
	Body  string
}

func (SubmitReview) intentName() string { return "SubmitReview" }
func (SubmitReview) Write() bool        { return true }

// MergePR merges the pull request.
//
// 🔴 `Method` IS CARRIED ON THE INTENT, not looked up by the runner. The
// confirmation prompt names the method, and the prompt and the request must be
// provably the same value — a runner that re-read the config could merge with a
// method the operator never saw.
type MergePR struct {
	Owner  string
	Name   string
	Num    int
	Method string
}

func (MergePR) intentName() string { return "MergePR" }
func (MergePR) Write() bool        { return true }

// --- the registry ------------------------------------------------------------

// KnownIntents is the REGISTRY. Every intent type this package can emit appears
// here exactly once.
//
// 🔴 A REGISTRY IS UNAVOIDABLE IN GO: there is no way to enumerate every
// implementation of an interface at run time. So the registry is made
// load-bearing instead of decorative — `intents_test.go` drives `Step` over
// EVERY binding in `Dispatch()` in every mode and reachable app state, and
// asserts that every intent it emits is registered AND ledgered. An
// unregistered intent therefore fails the suite at the moment a key can produce
// it, not at the moment someone remembers to list it.
func KnownIntents() []Intent {
	return []Intent{
		FetchPR{},
		FetchDiff{},
		OpenBrowser{},
		PostComment{},
		Approve{},
		RequestChanges{},
		SubmitReview{},
		MergePR{},
	}
}

// --- the confirmation ledger (§3.7, §5.3(b)) ---------------------------------

// Confirmation is the §3.7 ledger, spelled as TWO explicitly enumerated sets.
//
// 🔴 TWO-WAY, AND BOTH SETS ARE EXPLICIT. A write intent in NEITHER set fails
// the suite; an entry naming no registered intent fails the suite; a write
// intent in BOTH fails the suite. A new write verb cannot be added silently in
// either direction. This mirrors what `CONFIRMED_VERBS` does in the Lua today,
// carried across structurally rather than re-implemented on trust.
var (
	// Confirmed — the verb prompts before it acts. §3.7's table, verbatim:
	// merge, approve, request changes and submit review all prompt.
	Confirmed = map[string]bool{
		"Approve":        true,
		"RequestChanges": true,
		"SubmitReview":   true,
		"MergePR":        true,
	}

	// NotConfirmed — the verb acts immediately, and the reason it may is stated
	// PER ENTRY. Additive-and-trivially-reversible is the only reason accepted
	// so far: a posted comment can be deleted, a merge cannot.
	//
	// ⚠ A COMMENT IS STILL NOT A KEYSTROKE AWAY. It has no y/N prompt because
	// §3.7 says so and that table "is not up for revision" — but reaching it
	// requires composing a non-empty body and pressing the send key, and the
	// compose bar names the authenticated login while you type. That is stated
	// here because "not confirmed" otherwise reads as "fires on one key".
	NotConfirmed = map[string]string{
		"PostComment": "additive and trivially reversible — a posted comment can be deleted",
	}
)

// LedgerViolations returns every way the LIVE ledger and the registry disagree.
// Empty means they agree.
func LedgerViolations(intents []Intent) []string {
	return ledgerViolations(intents, Confirmed, NotConfirmed)
}

// ledgerViolations is the comparison itself, over maps passed in.
//
// 🔴 THE PARAMETERISED FORM EXISTS SO THE POSITIVE CONTROLS DRIVE *THIS* CODE
// rather than a second copy of it, and without mutating package state that
// another test in the same binary would then read. A control built out of a
// re-implementation controls nothing.
//
// 🔴 IT RETURNS ALL VIOLATIONS, NOT THE FIRST. A guard that stops at the first
// makes a second invisible until the first is fixed, which turns one red run
// into three.
func ledgerViolations(intents []Intent, confirmed map[string]bool, notConfirmed map[string]string) []string {
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
			// A read intent must not be ledgered at all — a confirmation prompt
			// for a read is noise, and an entry for one means somebody
			// mislabelled the intent.
			if confirmed[n] {
				out = append(out, fmt.Sprintf(
					"read intent %q is listed in CONFIRMED", n))
			}
			if _, ok := notConfirmed[n]; ok {
				out = append(out, fmt.Sprintf(
					"read intent %q is listed in NOT_CONFIRMED", n))
			}
			continue
		}
		_, inNot := notConfirmed[n]
		switch {
		case confirmed[n] && inNot:
			out = append(out, fmt.Sprintf(
				"write intent %q is in BOTH CONFIRMED and NOT_CONFIRMED", n))
		case !confirmed[n] && !inNot:
			out = append(out, fmt.Sprintf(
				"write intent %q is in neither CONFIRMED nor NOT_CONFIRMED", n))
		}
	}

	// The other direction: a ledger entry naming nothing.
	for n := range confirmed {
		if !registered[n] {
			out = append(out, fmt.Sprintf(
				"CONFIRMED names %q, which is not a registered intent", n))
		}
	}
	for n, reason := range notConfirmed {
		if !registered[n] {
			out = append(out, fmt.Sprintf(
				"NOT_CONFIRMED names %q, which is not a registered intent", n))
			continue
		}
		// 🔴 AN UNCONFIRMED WRITE MUST CARRY ITS REASON. The whole value of the
		// NOT_CONFIRMED set over a bare "everything else" is that skipping the
		// prompt is an argued decision; an empty reason is the decision without
		// the argument.
		if reason == "" {
			out = append(out, fmt.Sprintf(
				"NOT_CONFIRMED lists %q with an EMPTY reason", n))
		}
	}
	sort.Strings(out)
	return out
}

// RequiresConfirmation reports whether this intent may only be emitted after a
// y/N prompt.
//
// 🔴 THE PRODUCTION CODE AND THE GUARD READ THE SAME MAP. `app.go` calls this
// to decide whether a verb goes through the confirm bar, and the ledger test
// walks the same `Confirmed`. If they were two spellings, the ledger could be
// perfectly consistent while the app merged without asking.
func RequiresConfirmation(i Intent) bool { return i.Write() && Confirmed[i.intentName()] }

// IntentTypeName is the runtime type name of an intent, used by the test that
// checks emitted intents against the registry. It is here rather than in the
// test so the production and test views of "what type is this" cannot diverge.
func IntentTypeName(i Intent) string { return reflect.TypeOf(i).Name() }
