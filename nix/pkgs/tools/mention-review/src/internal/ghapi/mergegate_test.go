package ghapi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// 🔴 THE MERGE GATE — A READ IS POLLED, A WRITE IS NOT RETRIED.
//
// MEASURED 2026-09-17: two merges pressed 25 s before and 20 s after the base
// branch moved both failed. GitHub resets `mergeable` to null while it
// recomputes against the new base, and a merge dispatched into that window is
// rejected. The repo's own recorded lesson is "poll it — never merge on a stale
// CLEAN, and never read UNKNOWN as a blocker".
//
// ⚠ NOTHING HERE MERGES ANYTHING. Every request goes to an `httptest` server on
// loopback; `nonet_test.go`'s TestMain refuses any other host.

// mergeFake answers a scripted sequence of mergeability reads and records the
// merge PUT — so every assertion below is about WHAT WAS SENT, and the ones
// that matter most are about a PUT that was NOT.
type mergeFake struct {
	mu sync.Mutex
	// states is consumed one per GraphQL read; the last entry repeats forever.
	// An empty string is sent as JSON `null`, which is what the server really
	// returns while the computation is in flight.
	states       []string
	graphqlCalls int
	putCalls     int
	putMethod    string
	// graphqlStatus, when non-zero, makes the read fail instead of answering.
	graphqlStatus int
}

func (f *mergeFake) handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		if strings.HasSuffix(r.URL.Path, "/graphql") {
			f.graphqlCalls++
			if f.graphqlStatus != 0 {
				w.WriteHeader(f.graphqlStatus)
				_, _ = io.WriteString(w, `{"message":"Bad credentials"}`)
				return
			}
			i := f.graphqlCalls - 1
			if i >= len(f.states) {
				i = len(f.states) - 1
			}
			value := "null"
			if f.states[i] != "" {
				value = `"` + f.states[i] + `"`
			}
			_, _ = io.WriteString(w, `{"data":{"repository":{"pullRequest":{"mergeable":`+
				value+`,"mergeStateStatus":"UNKNOWN"}}}}`)
			return
		}
		f.putCalls++
		var body map[string]any
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &body)
		if m, ok := body["merge_method"].(string); ok {
			f.putMethod = m
		}
		_, _ = io.WriteString(w, `{"merged":true}`)
	}
}

func (f *mergeFake) seen() (graphql, put int, method string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.graphqlCalls, f.putCalls, f.putMethod
}

// newMergeFake wires a client to the fake with a poll bound of `attempts` and a
// 1 ms interval. ⚠ THE INTERVAL IS THE ONLY THING SHORTENED — the loop, the
// counting and the refusals are the production ones, and a separate test pins
// the real bound `NewClient` installs.
func newMergeFake(t *testing.T, attempts int, states ...string) (*Client, *mergeFake, func()) {
	t.Helper()
	f := &mergeFake{states: states}
	srv := httptest.NewServer(f.handler())
	c := NewClient("tok-mergegate-fixture", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)
	c.SetMergeablePoll(attempts, time.Millisecond)
	return c, f, srv.Close
}

// 🔴 UNKNOWN IS NOT A BLOCKER. The recompute settles and the merge goes.
func TestAMergeWhoseSnapshotSaysUnknownPollsAndThenSends(t *testing.T) {
	// The first read answers JSON `null` — the in-flight spelling — and the
	// second resolves. ⚠ Two DIFFERENT spellings of the same condition on
	// purpose: "" and "UNKNOWN" arrive by different routes.
	c, f, done := newMergeFake(t, 4, "", MergeableYes)
	defer done()

	if err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase", MergeableUnknown); err != nil {
		t.Fatalf("a merge that resolved MERGEABLE was refused: %v", err)
	}
	graphql, put, method := f.seen()
	if graphql != 2 {
		t.Errorf("the gate made %d mergeability reads, want 2 (one UNKNOWN, one resolved)", graphql)
	}
	if put != 1 {
		t.Fatalf("the merge PUT was sent %d times, want exactly 1", put)
	}
	// ⚠ `rebase`, not the declared default `squash`: a gate that rebuilt the
	// request instead of forwarding the argument would show up here.
	if method != "rebase" {
		t.Errorf("merge_method = %q, want %q", method, "rebase")
	}
}

// 🔴 AND IT IS NOT A GO-AHEAD EITHER. A recompute that resolves to a conflict
// must refuse, NAMING the state, and send nothing.
func TestAMergeRefusesWhenTheRecomputeResolvesToAConflict(t *testing.T) {
	c, f, done := newMergeFake(t, 4, MergeableUnknown, MergeableNo)
	defer done()

	err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase", MergeableUnknown)
	if err == nil {
		t.Fatal("a conflicting pull request was merged")
	}
	if !strings.Contains(err.Error(), MergeableNo) {
		t.Errorf("the refusal does not name the state in words: %v", err)
	}
	if !strings.Contains(err.Error(), "NOTHING WAS SENT") {
		t.Errorf("the refusal does not say that nothing was sent: %v", err)
	}
	graphql, put, _ := f.seen()
	if put != 0 {
		t.Fatalf("a MERGE REQUEST was sent for a CONFLICTING pull request (%d PUTs)", put)
	}
	if graphql != 2 {
		t.Errorf("the gate made %d reads, want 2 — it must stop the moment the "+
			"state resolves rather than spending the whole bound", graphql)
	}
}

// 🔴 STILL UNKNOWN AFTER THE BOUND — REFUSE, AND SAY IT IS RECOMPUTING. The
// operator needs to be told to press `m` again; the one thing this must never
// do is dispatch anyway, and the second is retry by itself.
func TestAMergeRefusesWhileGitHubIsStillRecomputingAndSaysSo(t *testing.T) {
	c, f, done := newMergeFake(t, 3, MergeableUnknown)
	defer done()

	err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase", "")
	if err == nil {
		t.Fatal("a merge was dispatched while mergeability was UNKNOWN — this is " +
			"the measured failure this gate exists to prevent")
	}
	for _, want := range []string{"still", MergeableUnknown, "base branch", "press `m` again"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal is missing %q: %v", want, err)
		}
	}
	graphql, put, _ := f.seen()
	if put != 0 {
		t.Fatalf("a MERGE REQUEST was sent anyway (%d PUTs)", put)
	}
	// 🔴 THE BOUND IS A BOUND. Three attempts means three reads — not two, and
	// not a loop that keeps going.
	if graphql != 3 {
		t.Errorf("the gate made %d reads against a bound of 3", graphql)
	}
}

// 🔴 THE POLL IS CONDITIONAL, AND THIS IS THE CONTROL THAT PROVES IT. A gate
// that re-read on EVERY merge would pass all three tests above while adding a
// round trip to every merge the operator makes.
func TestAResolvedSnapshotMergesWithNoExtraRead(t *testing.T) {
	c, f, done := newMergeFake(t, 4, MergeableNo) // would REFUSE if it were read
	defer done()

	if err := c.Merge(context.Background(), wOwner, wName, wNum, "squash", MergeableYes); err != nil {
		t.Fatalf("a MERGEABLE snapshot was refused: %v", err)
	}
	graphql, put, method := f.seen()
	if graphql != 0 {
		t.Errorf("the gate made %d mergeability reads for an already-resolved "+
			"snapshot, want 0", graphql)
	}
	if put != 1 || method != "squash" {
		t.Errorf("the merge sent %d PUT(s) with method %q, want 1 and %q", put, method, "squash")
	}
}

// 🔴 A FAILED READ IS NOT A GO-AHEAD. If the poll itself cannot be performed,
// the merge does not happen — an error deciding "probably fine" is how a guard
// becomes decoration.
func TestAFailedMergeabilityReadRefusesTheMerge(t *testing.T) {
	c, f, done := newMergeFake(t, 4, MergeableYes)
	defer done()
	f.mu.Lock()
	f.graphqlStatus = http.StatusUnauthorized
	f.mu.Unlock()

	err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase", MergeableUnknown)
	if err == nil {
		t.Fatal("the merge went ahead after the mergeability read failed")
	}
	var ae *APIError
	if !asAPIError(err, &ae) || ae.State != AuthRejected {
		t.Errorf("err = %v, want the read's own TOKEN REJECTED APIError", err)
	}
	if _, put, _ := f.seen(); put != 0 {
		t.Fatalf("a MERGE REQUEST was sent after a failed read (%d PUTs)", put)
	}
}

// 🔴 A PULL REQUEST THE READ CANNOT SEE IS REPORTED, NOT GUESSED AT.
func TestMergeabilityReportsAMissingPullRequestRatherThanGuessing(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"data":{"repository":{"pullRequest":null}}}`)
	}))
	defer srv.Close()
	c := NewClient("tok-mergegate-fixture", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

	got, err := c.Mergeability(context.Background(), wOwner, wName, wNum)
	if err == nil {
		t.Fatalf("a null pull request returned %q and no error", got)
	}
	var ae *APIError
	if !asAPIError(err, &ae) || ae.State != AuthNotFound {
		t.Fatalf("err = %v, want a NOT FOUND APIError", err)
	}
	if !strings.Contains(ae.Detail, "gardenersguild/trowelcast#1559") {
		t.Errorf("the detail does not name the reference: %q", ae.Detail)
	}
}

// 🔴 THE PRODUCTION BOUND, PINNED. Every test above shortens the interval
// through a test seam, so without this the shipped values could be anything at
// all — one attempt (not a poll), or a minute of silence after a keypress.
func TestTheShippedMergeablePollIsMoreThanOneReadAndIsBounded(t *testing.T) {
	c := NewClient("tok-mergegate-fixture", nil)
	if c.pollAttempts < 2 {
		t.Errorf("pollAttempts = %d — a single read is not a poll", c.pollAttempts)
	}
	if c.pollInterval <= 0 {
		t.Errorf("pollInterval = %v — a zero wait re-reads a value that cannot "+
			"have changed", c.pollInterval)
	}
	// The worst case the operator waits between `y` and an answer.
	worst := time.Duration(c.pollAttempts-1) * c.pollInterval
	if worst < time.Second {
		t.Errorf("the whole poll spends %v — too short for a recompute to settle "+
			"in, so it would refuse merges that were about to be fine", worst)
	}
	if worst > 10*time.Second {
		t.Errorf("the whole poll can block a keypress for %v — a bound nobody "+
			"would sit through is not a bound", worst)
	}
}

// 🔴 "" AND "UNKNOWN" ARE ONE CONDITION. The decoder turns the server's null
// into "", so a predicate that handled only the spelled word would let the
// in-flight case through to a merge.
func TestNormalizeMergeableFoldsNullOntoUnknown(t *testing.T) {
	cases := map[string]string{
		"":              MergeableUnknown,
		"   ":           MergeableUnknown,
		"UNKNOWN":       MergeableUnknown,
		"unknown":       MergeableUnknown,
		"MERGEABLE":     MergeableYes,
		" mergeable ":   MergeableYes,
		"CONFLICTING":   MergeableNo,
		"SOMETHING_NEW": "SOMETHING_NEW",
	}
	for in, want := range cases {
		if got := NormalizeMergeable(in); got != want {
			t.Errorf("NormalizeMergeable(%q) = %q, want %q", in, got, want)
		}
	}
	// The three words are distinct, or every branch above is the same branch.
	if MergeableYes == MergeableNo || MergeableYes == MergeableUnknown || MergeableNo == MergeableUnknown {
		t.Fatal("two of the three mergeability words are the same string")
	}
}
