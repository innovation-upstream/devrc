package ghapi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// 🔴 THE MERGE GATE — THE READ IS UNCONDITIONAL, THE POLL IS BOUNDED, AND THE
// WRITE IS NEVER RETRIED.
//
// Every merge re-reads mergeability immediately before dispatching. The rule is
// the operator's, recorded in `claudedocs/handoff-mention-review-tui.md`: "never
// merge on a stale `CLEAN`". A gate that read only when it was TOLD the state
// was unresolved could not see the case that matters — the base branch moving
// after the snapshot was taken, which leaves the caller holding a stale
// `MERGEABLE` and nothing to trigger on.
//
// ⚠ NO DIAGNOSIS OF THE 2026-09-17 FAILURES IS ASSERTED HERE, BY THIS FILE OR
// BY THE CODE IT TESTS. Two merges were reported as `unprocessable entity`; the
// response bodies were never captured, so the reason is unknown and not
// recoverable. These tests pin what the gate DOES, which stands on the recorded
// rule and not on a story about that evening.
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
	// prState and merged are the pull request's own `state`/`merged`, which the
	// gate reads BEFORE it looks at mergeability. ⚠ THE DEFAULT IS `OPEN`, and
	// that is the realistic fixture — every response the real API returns for a
	// pull request carries this field, and a fake that omitted it would be
	// testing the gate against a shape the server never sends.
	prState string
	merged  bool
}

// gqlIdent matches a GraphQL identifier, which is how `requestedFields` turns a
// query document into the fields it names.
var gqlIdent = regexp.MustCompile(`[A-Za-z_][A-Za-z0-9_]*`)

func gqlSpace(b byte) bool { return b == ' ' || b == '\n' || b == '\t' || b == '\r' }

// requestedFields maps every field a GraphQL document names onto the KEY a
// server answers it under.
//
// 🔴 THE FAKE HONOURS THE QUERY, AND THAT IS NOT DECORATION. A real GraphQL
// server returns a field ONLY if it was asked for. A fake that answered every
// field regardless is blind to the one mutation that matters most here —
// deleting `state merged` from `MergeableQuery` — because the decode struct
// would keep working against a response the real server would never send.
// MEASURED: with the unconditional fake, a mutant that shortened the query to
// `{ mergeable }` SURVIVED the whole suite; with this, it is killed by the
// terminal-pull-request test.
//
// 🔴 A KEY, NOT A PRESENCE BIT, AND THE ALIAS IS WHY. A server keys an ALIASED
// selection by the alias: `prState: state` comes back as `{"prState":…}` and
// `state` is absent from the body entirely. A fake that answered `"state"` there
// would satisfy a decoder reading the `state` json tag while production read
// nothing — so an alias mutant survived a green suite. MEASURED at `cb7c7949`:
// aliasing `state` in `MergeableQuery` left `go test ./internal/ghapi/` `ok`.
//
// Argument lists are skipped rather than scanned, because `commits(last:100)`
// names `last` in front of a colon and that is not an alias.
func requestedFields(query string) map[string]string {
	out := map[string]string{}
	alias := ""
	for i := 0; i < len(query); i++ {
		if query[i] == '(' {
			j := strings.IndexByte(query[i:], ')')
			if j < 0 {
				break
			}
			i += j
			continue
		}
		loc := gqlIdent.FindStringIndex(query[i:])
		if loc == nil || loc[0] != 0 {
			continue
		}
		name := query[i : i+loc[1]]
		i += loc[1] - 1
		k := i + 1
		for k < len(query) && gqlSpace(query[k]) {
			k++
		}
		if k < len(query) && query[k] == ':' {
			alias = name
			i = k
			continue
		}
		key := name
		if alias != "" {
			key, alias = alias, ""
		}
		out[name] = key
	}
	return out
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
			var req struct {
				Query string `json:"query"`
			}
			raw, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(raw, &req)
			asked := requestedFields(req.Query)

			i := f.graphqlCalls - 1
			if i >= len(f.states) {
				i = len(f.states) - 1
			}
			// ⚠ THE RESPONSE KEY, NOT THE FIELD NAME. An aliased selection comes
			// back under its alias, which is what a real server does and what makes
			// an alias mutant visible here.
			var fields []string
			if key := asked["mergeable"]; key != "" {
				value := "null"
				if f.states[i] != "" {
					value = `"` + f.states[i] + `"`
				}
				fields = append(fields, `"`+key+`":`+value)
			}
			if key := asked["state"]; key != "" {
				fields = append(fields, `"`+key+`":"`+f.prState+`"`)
			}
			if key := asked["merged"]; key != "" {
				fields = append(fields, `"`+key+`":`+strconv.FormatBool(f.merged))
			}
			_, _ = io.WriteString(w, `{"data":{"repository":{"pullRequest":{`+
				strings.Join(fields, ",")+`}}}}`)
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
	f := &mergeFake{states: states, prState: PRStateOpen}
	srv := httptest.NewServer(f.handler())
	c := NewClient("tok-mergegate-fixture", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)
	c.SetMergeablePoll(attempts, time.Millisecond)
	return c, f, srv.Close
}

// 🔴 AN UNRESOLVED READ IS NOT A BLOCKER WHILE THE BOUND LASTS. The recompute
// settles and the merge goes.
func TestAMergeThatReadsUnknownPollsAndThenSends(t *testing.T) {
	// The first read answers JSON `null` — the in-flight spelling — and the
	// second resolves. ⚠ Two DIFFERENT spellings of the same condition on
	// purpose: "" and "UNKNOWN" arrive by different routes.
	c, f, done := newMergeFake(t, 4, "", MergeableYes)
	defer done()

	if err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase"); err != nil {
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

	err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
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

	err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
	if err == nil {
		t.Fatal("a merge was dispatched while mergeability was UNKNOWN — the one " +
			"thing this gate must never do")
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

// 🔴 THE READ HAPPENS ON EVERY MERGE, INCLUDING THE ONE THAT NEEDS NO POLLING.
//
// This test REPLACES `TestAResolvedSnapshotMergesWithNoExtraRead`, which
// asserted the opposite — that a caller reporting a resolved state merged with
// ZERO reads. That assertion enforced the defect: the state a caller reports is
// a fact about when it was fetched, so a gate keyed on it is silent in exactly
// the case it exists for, the base branch moving in between. Trading one ~0.17 s
// round trip for that silence was the wrong trade, and this pins the new one.
//
// ⚠ THE EXPECTATION IS THE LITERAL 1, NOT `len(states)` OR ANY CONSTANT THE
// IMPLEMENTATION READS. A mutant that changed how many reads a resolved state
// costs cannot also move what this test wants.
func TestEveryMergeReReadsMergeabilityEvenWhenTheFirstReadResolves(t *testing.T) {
	// One state, resolved on the first read: no polling is needed, so a read
	// happening at all is the whole claim.
	c, f, done := newMergeFake(t, 4, MergeableYes)
	defer done()

	if err := c.Merge(context.Background(), wOwner, wName, wNum, "squash"); err != nil {
		t.Fatalf("a merge whose live state reads MERGEABLE was refused: %v", err)
	}
	graphql, put, method := f.seen()
	if graphql != 1 {
		t.Errorf("the gate made %d mergeability reads before dispatching, want exactly 1. "+
			"Zero means the merge went out on a state nobody checked at the moment of "+
			"the write; more than one means it polled a state that had already resolved.",
			graphql)
	}
	if put != 1 {
		t.Fatalf("the merge PUT was sent %d times, want exactly 1", put)
	}
	// ⚠ `squash` here and `rebase` in the tests above — the argument is
	// forwarded, not rebuilt from a default.
	if method != "squash" {
		t.Errorf("merge_method = %q, want %q", method, "squash")
	}
}

// 🔴 THE STALE-CLEAN CASE, WHICH IS THE ONE THE OLD GATE COULD NOT SEE.
//
// Nothing tells this gate to look: no caller reports an unresolved state, and no
// argument carries one — the signature has no place to put it. The live read
// happens anyway, comes back CONFLICTING, and the merge is refused. Under the
// previous design this was a silent dispatch, because the only trigger for a
// re-read was a caller already saying UNKNOWN.
func TestAConflictFoundOnlyByTheLiveReadStopsTheMerge(t *testing.T) {
	c, f, done := newMergeFake(t, 4, MergeableNo)
	defer done()

	err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
	if err == nil {
		t.Fatal("a pull request whose LIVE state is CONFLICTING was merged — the " +
			"read either did not happen or did not decide")
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
	if graphql != 1 {
		t.Errorf("the gate made %d reads, want 1 — one read is enough to resolve a "+
			"state that is not UNKNOWN, and it must not be skipped", graphql)
	}
}

// 🔴 A MERGED OR CLOSED PULL REQUEST IS REFUSED FOR THE RIGHT REASON, AND THE
// OPERATOR IS NOT TOLD TO TRY AGAIN.
//
// MEASURED 2026-09-18 against the public `innovation-upstream/devrc` with a
// read-only GraphQL probe: `mergeable` is `UNKNOWN` PERMANENTLY once a pull
// request is not open — #1760 (MERGED) → UNKNOWN, #1701 (CLOSED) → UNKNOWN,
// #1761 (OPEN) → MERGEABLE. So the fixtures below answer `null` for
// mergeability, which is exactly what the server sends for these two.
//
// 🔴 THE REGRESSION IS THE GATE'S OWN. Before the gate existed the merge PUT
// went out and GitHub refused it — REPORTEDLY with `405 Pull Request is not
// mergeable`. ⚠ That string is a report and not a measurement: no response body
// from 2026-09-17 was captured and none can be recovered. The hedge is the same
// one `write.go` and `types.go` carry, deliberately — this claim is spelled in
// four places and two of them used to state flat what the other two hedged,
// which is the two-evidentiary-standards defect round 1 was convened to fix.
// What IS established is that a refusal carrying a server message is rendered in
// full rather than as two words. A gate that replaced a true refusal with "still
// recomputing, press `m` again" would be a net LOSS for this case: false, and
// non-terminating, because the advice can never come true.
//
// ⚠ THE FORBIDDEN STRINGS ARE THE POINT OF THE TEST, so they are written out
// here rather than derived from the message under test.
func TestATerminalPullRequestIsRefusedWithTheAccurateReasonAndNoRetryAdvice(t *testing.T) {
	cases := []struct {
		name    string
		prState string
		merged  bool
		want    string
	}{
		{"merged", PRStateMerged, true, PRStateMerged},
		{"closed", PRStateClosed, false, PRStateClosed},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// "" is JSON `null` — the in-flight spelling, and the one a terminal
			// pull request answers with forever.
			c, f, done := newMergeFake(t, 4, "")
			defer done()
			f.mu.Lock()
			f.prState, f.merged = tc.prState, tc.merged
			f.mu.Unlock()

			err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
			if err == nil {
				t.Fatal("a pull request that is already over was merged")
			}
			got := err.Error()
			if !strings.Contains(got, tc.want) {
				t.Errorf("the refusal does not name the state %q: %s", tc.want, got)
			}
			if !strings.Contains(got, "NOTHING WAS SENT") {
				t.Errorf("the refusal does not say that nothing was sent: %s", got)
			}
			// 🔴 THE RETRY ADVICE MUST BE ABSENT. This is the whole finding: the
			// UNKNOWN branch's wording is correct for a recompute and FALSE here,
			// because no amount of pressing `m` reopens a merged pull request.
			for _, forbidden := range []string{
				"press `m` again in a moment",
				"recomputes this whenever the base branch moves",
				"still reports mergeability UNKNOWN",
			} {
				if strings.Contains(got, forbidden) {
					t.Errorf("the refusal tells the operator a false story — it contains %q: %s",
						forbidden, got)
				}
			}
			graphql, put, _ := f.seen()
			if put != 0 {
				t.Fatalf("a MERGE REQUEST was sent for a %s pull request (%d PUTs)", tc.want, put)
			}
			// 🔴 AND IT COSTS ONE READ, NOT THE WHOLE BOUND. `mergeable` never
			// resolves for these, so a gate that polled first would sit silent for
			// the full interval before saying something it already knew.
			if graphql != 1 {
				t.Errorf("the gate made %d reads, want exactly 1 — a terminal pull "+
					"request cannot become mergeable, so spending the poll bound on "+
					"one is pure silence", graphql)
			}
		})
	}
}

// 🔴 POSITIVE CONTROL ON THE FAKE ITSELF: it OMITS a field the query did not
// ask for.
//
// Without this, `requestedFields` could be wired to nothing — the fake would
// answer every field unconditionally, the terminal tests above would still pass,
// and a query that stopped asking for `state merged` would sail through a green
// suite while production read every pull request as open. This drives the fake
// directly, with a query that asks for `mergeable` alone, and watches the two
// fields disappear from the body.
func TestTheMergeFakeAnswersOnlyTheFieldsTheQueryAsksFor(t *testing.T) {
	f := &mergeFake{states: []string{MergeableYes}, prState: PRStateMerged, merged: true}
	srv := httptest.NewServer(f.handler())
	defer srv.Close()

	post := func(query string) string {
		t.Helper()
		payload, _ := json.Marshal(map[string]any{"query": query})
		resp, err := srv.Client().Post(srv.URL+"/graphql", "application/json",
			strings.NewReader(string(payload)))
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(resp.Body)
		return string(raw)
	}

	// The shipped query asks for all three, so all three come back.
	full := post(MergeableQuery)
	for _, want := range []string{`"mergeable"`, `"state"`, `"merged"`} {
		if !strings.Contains(full, want) {
			t.Errorf("the shipped query did not get %s back: %s", want, full)
		}
	}
	// A narrowed query gets only what it named.
	narrow := post(`query{ repository { pullRequest { mergeable } } }`)
	if !strings.Contains(narrow, `"mergeable"`) {
		t.Errorf("a query asking for `mergeable` did not get it: %s", narrow)
	}
	for _, unwanted := range []string{`"state"`, `"merged"`} {
		if strings.Contains(narrow, unwanted) {
			t.Errorf("the fake answered %s for a query that never asked for it — its "+
				"selection filter is wired to nothing, so every mutant that narrows "+
				"`MergeableQuery` would SURVIVE: %s", unwanted, narrow)
		}
	}

	// 🔴 AN ALIASED SELECTION COMES BACK UNDER ITS ALIAS. A fake keyed by the
	// FIELD name answers `"state"` here, the decoder's `state` json tag finds it,
	// and a mutant that aliases the field in `MergeableQuery` SURVIVES while
	// production reads nothing at all — MEASURED at `cb7c7949`.
	aliased := post(`query{ repository { pullRequest { mergeable prState: state merged } } }`)
	if !strings.Contains(aliased, `"prState"`) {
		t.Errorf("an aliased `state` did not come back under its alias: %s", aliased)
	}
	if strings.Contains(aliased, `"state":`) {
		t.Errorf("the fake answered `state` for a document that aliased it to `prState` — "+
			"it is keyed by the field name rather than the response key, so every alias "+
			"mutant would SURVIVE: %s", aliased)
	}
	// And an UNaliased field beside it is still keyed by its own name, so the
	// assertion above is about aliases rather than about a fake that renames
	// everything.
	if !strings.Contains(aliased, `"merged"`) {
		t.Errorf("an unaliased `merged` lost its own name: %s", aliased)
	}
}

// 🔴 THE PREDICATE ANSWERS ONLY WHEN IT KNOWS, AND THE ASYMMETRY IS DELIBERATE.
//
// An absent or unrecognised `state` is NOT terminal: reading a missing field as
// CLOSED would refuse every merge the moment GitHub renamed or omitted it, while
// reading it as open costs one wasted write whose refusal the renderer now shows
// in full rather than as two words. ⚠ The specific `405 Pull Request is not
// mergeable` wording is REPORTED and not measured — see the terminal-pull-request
// test above — so the asymmetry rests on the rendering, not on that string.
// The two mistakes do not cost the same, so the predicate declines rather than
// guesses.
func TestTerminalPRStateAnswersOnlyOnAPositivelyTerminalPullRequest(t *testing.T) {
	cases := []struct {
		state  string
		merged bool
		want   string
	}{
		{PRStateOpen, false, ""},
		{"open", false, ""},
		{"", false, ""},
		{"   ", false, ""},
		{"SOMETHING_GITHUB_ADDED_LATER", false, ""},
		{PRStateMerged, false, PRStateMerged},
		{"merged", false, PRStateMerged},
		{PRStateClosed, false, PRStateClosed},
		{" closed ", false, PRStateClosed},
		// `merged` alone is enough, and it WINS over a state that disagrees —
		// two fields on one object, and a response carrying only one of them is
		// still answered correctly.
		{"", true, PRStateMerged},
		{PRStateOpen, true, PRStateMerged},
		{PRStateClosed, true, PRStateMerged},
	}
	for _, tc := range cases {
		if got := TerminalPRState(tc.state, tc.merged); got != tc.want {
			t.Errorf("TerminalPRState(%q, %v) = %q, want %q", tc.state, tc.merged, got, tc.want)
		}
	}
	// The three words are distinct, or every branch above is the same branch.
	if PRStateOpen == PRStateClosed || PRStateOpen == PRStateMerged || PRStateClosed == PRStateMerged {
		t.Fatal("two of the three pull-request state words are the same string")
	}
}

// 🔴 THE REFUSAL DESCRIBES WHAT IT ACTUALLY DID.
//
// `awaitMergeable` clamps a bound below 1 up to 1, so a misconfigured client
// spent exactly one read and the message said "after 1 re-reads over 0s" — it
// called the first read a RE-read, and reported a duration of zero as though it
// had waited. A refusal that misstates its own work is one the operator cannot
// judge.
//
// ⚠ THE EXPECTED STRINGS ARE WRITTEN OUT HERE, not composed from the constants
// the message uses.
func TestTheUnknownRefusalNamesHowManyReadsItActuallyMade(t *testing.T) {
	t.Run("a single read is not a re-read and took no time", func(t *testing.T) {
		c, _, done := newMergeFake(t, 1, MergeableUnknown)
		defer done()
		err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
		if err == nil {
			t.Fatal("a merge was dispatched while mergeability was UNKNOWN")
		}
		got := err.Error()
		if !strings.Contains(got, "after 1 read.") {
			t.Errorf("a one-attempt gate does not say it made one read: %s", got)
		}
		for _, forbidden := range []string{"re-read", "over 0s", "1 reads"} {
			if strings.Contains(got, forbidden) {
				t.Errorf("the refusal contains %q, which misdescribes a single read: %s",
					forbidden, got)
			}
		}
	})
	t.Run("several reads name the count and the elapsed wait", func(t *testing.T) {
		// newMergeFake uses a 1 ms interval, so three attempts wait 2 ms.
		c, _, done := newMergeFake(t, 3, MergeableUnknown)
		defer done()
		err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
		if err == nil {
			t.Fatal("a merge was dispatched while mergeability was UNKNOWN")
		}
		got := err.Error()
		if !strings.Contains(got, "after 3 reads over 2ms.") {
			t.Errorf("the refusal does not name 3 reads over 2ms: %s", got)
		}
		if strings.Contains(got, "re-read") {
			t.Errorf("the refusal still calls the first read a re-read: %s", got)
		}
	})
}

// 🔴 THE FOURTH REFLECTING PATH: THE REFUSAL THAT QUOTES THE SERVER'S OWN WORD.
//
// `write.go`'s `default:` arm interpolates `read.Mergeable` — which is
// `NormalizeMergeable` of a string the SERVER sent — straight into the card. At
// `d2ec72c3` it did so with no `clipDetail` and no `redact`, and a probe server
// measured a 5,081-rune unclipped, unredacted detail out of it — the length the
// fixture below still reproduces — while two
// comments one file over asserted that every server-derived detail was bounded
// and that no unlisted path carried server text.
//
// ⚠ THE CLIP IS ON THE SERVER'S WORD, NOT ON THE SENTENCE. Clipping the composed
// detail would cut `NOTHING WAS SENT.` off the end exactly when the server sent
// something pathological — so this test asserts the clip AND the survival of the
// clause the operator must read.
//
// ⚠ THE CEILING IS ABSOLUTE AND IS NOT MADE OF THE CONSTANT UNDER TEST. A mutant
// that raises `maxDetailRunes` still "clips", at its own number.
func TestTheMergeabilityRefusalNeverReflectsTheServersWordUnclipped(t *testing.T) {
	t.Run("a pathological word is clipped and the refusal survives it", func(t *testing.T) {
		huge := strings.Repeat("qwertyuiop", 500) // 5,000 runes
		c, f, done := newMergeFake(t, 4, huge)
		defer done()

		err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
		if err == nil {
			t.Fatal("a merge went out on a mergeability word nobody recognised")
		}
		var ae *APIError
		if !asAPIError(err, &ae) {
			t.Fatalf("err is %T, want *APIError: %v", err, err)
		}
		// A terminal line's worth, whatever `maxDetailRunes` says.
		const cardCeiling = 600
		if n := len([]rune(ae.Detail)); n > cardCeiling {
			t.Errorf("the refusal is %d runes — past the %d-rune ceiling a single terminal "+
				"line can carry. The server's `mergeable` string reached the card unclipped: %.120q",
				n, cardCeiling, ae.Detail)
		}
		if !strings.Contains(ae.Detail, "…") {
			t.Errorf("a 5,000-rune server word produced a detail with no clip marker: %.120q",
				ae.Detail)
		}
		// 🔴 THE CLAUSE THE OPERATOR MUST READ SURVIVED. Clipping the composed
		// sentence instead of the server's word would have eaten it.
		if !strings.HasSuffix(ae.Detail, "NOTHING WAS SENT.") {
			t.Errorf("the refusal no longer ends by saying nothing was sent — the clip was "+
				"applied to the whole sentence rather than to the server's word: %.200q",
				ae.Detail)
		}
		if _, put, _ := f.seen(); put != 0 {
			t.Fatalf("a MERGE REQUEST was sent (%d PUTs)", put)
		}
	})

	// 🔴 POSITIVE CONTROL: a SHORT unrecognised word comes through verbatim, so
	// the assertions above are a claim about clipping rather than about a refusal
	// that always says the same thing.
	t.Run("a short unrecognised word is reported in full", func(t *testing.T) {
		c, _, done := newMergeFake(t, 4, "SOMETHING_GITHUB_ADDED_LATER")
		defer done()

		err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
		if err == nil {
			t.Fatal("a merge went out on a mergeability word nobody recognised")
		}
		if !strings.Contains(err.Error(), "SOMETHING_GITHUB_ADDED_LATER") {
			t.Errorf("the refusal does not name the state the server sent: %v", err)
		}
		if strings.Contains(err.Error(), "…") {
			t.Errorf("a 28-rune word was clipped — the bound is wired to something much "+
				"smaller than a card: %v", err)
		}
	})
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

	err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase")
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
		t.Fatalf("a null pull request returned %+v and no error", got)
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
