package ghapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// 🔴 EVERY FIXTURE HERE IS SYNTHETIC — this repository is PUBLIC and captured
// text must not land in it in any form, fixtures included. The shapes are real;
// the values are invented.
//
// 🔴 EXPECTED VALUES ARE WRITTEN BY HAND FROM THE GRAPHQL SCHEMA AND THE CARD
// SPEC, NEVER READ OFF THE DECODER.

// --- Classify: the states must be DISTINGUISHABLE ----------------------------

func TestClassifyDistinguishesNoTokenFromRejected(t *testing.T) {
	cases := []struct {
		name       string
		haveToken  bool
		status     int
		want       AuthState
	}{
		{"no token at all", false, 0, AuthNoToken},
		// 🔴 AND NO TOKEN WINS EVEN OVER A 200. An empty token cannot have
		// produced a successful authenticated response, so a classifier that
		// checked the status first would report OK for a request that was never
		// authenticated.
		{"no token, absurd 200", false, http.StatusOK, AuthNoToken},
		{"token accepted", true, http.StatusOK, AuthOK},
		{"token refused", true, http.StatusUnauthorized, AuthRejected},
		{"not visible", true, http.StatusNotFound, AuthNotFound},
		{"too many requests", true, http.StatusTooManyRequests, AuthRateLimited},
		{"something else", true, http.StatusBadGateway, AuthOther},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := Classify(c.haveToken, c.status); got != c.want {
				t.Errorf("Classify(%v,%d) = %v, want %v", c.haveToken, c.status, got, c.want)
			}
		})
	}
	// The property the §6 cards depend on, asserted directly: the two words
	// differ, because the FIXES differ.
	if AuthNoToken.Word() == AuthRejected.Word() {
		t.Fatal("NO TOKEN and TOKEN REJECTED are the same word")
	}
}

// ⚠ 403 IS AMBIGUOUS AND IS NOT ASSUMED TO BE RATE LIMITING. GitHub answers 403
// both for a spent budget and for a token whose scopes do not cover the
// request. Calling a scope failure RATE LIMITED would send the operator to wait
// for a reset that is not coming.
func TestA403WithBudgetLeftIsNotCalledRateLimited(t *testing.T) {
	if got := ClassifyResponse(true, http.StatusForbidden, 0); got != AuthRateLimited {
		t.Errorf("403 with remaining=0 = %v, want AuthRateLimited", got)
	}
	if got := ClassifyResponse(true, http.StatusForbidden, 4231); got != AuthOther {
		t.Errorf("403 with remaining=4231 = %v, want AuthOther", got)
	}
	// An absent header (-1) is UNKNOWN, not zero — it must not be read as a
	// spent budget.
	if got := ClassifyResponse(true, http.StatusForbidden, -1); got != AuthOther {
		t.Errorf("403 with no rate-limit header = %v, want AuthOther", got)
	}
	// 429 is unambiguous and stays RATE LIMITED whatever the header says.
	if got := ClassifyResponse(true, http.StatusTooManyRequests, 4231); got != AuthRateLimited {
		t.Errorf("429 = %v, want AuthRateLimited", got)
	}
}

// --- decodeSnapshot ----------------------------------------------------------

// decodeFixture decodes a body through a real client, because `decodeSnapshot`
// is a METHOD now — it has to reach `Client.redact` before it can put a server
// string into an error.
//
// ⚠ THE TOKEN IS A NON-EMPTY FIXTURE THAT APPEARS IN NO BODY BELOW. An empty
// token would make `redact` a no-op, so every test in this file would be
// decoding through a client that structurally cannot redact — a green that
// proves nothing about the shipped path. The leak tests further down supply
// their own token and their own body carrying it.
func decodeFixture(body []byte, repo string, num int) (*Snapshot, error) {
	return NewClient("tok-decode-fixture-appears-in-no-body", nil).decodeSnapshot(body, repo, num)
}

const prFixture = `{"data":{
  "viewer":{"login":"a-reviewer"},
  "repository":{"issueOrPullRequest":{
    "__typename":"PullRequest",
    "number":1559,
    "title":"Refresh the stale context before running",
    "state":"OPEN","isDraft":false,"merged":false,
    "url":"https://github.com/gardenersguild/trowelcast/pull/1559",
    "createdAt":"2026-09-10T09:00:00Z","updatedAt":"2026-09-12T17:30:00Z",
    "author":{"login":"an-author"},
    "baseRefName":"main","headRefName":"fix/stale-context",
    "additions":312,"deletions":40,"changedFiles":2,
    "mergeable":"MERGEABLE","mergeStateStatus":"BLOCKED",
    "reviewDecision":null,
    "commits":{"totalCount":3,"nodes":[
      {"commit":{"oid":"a1b2c3d4","abbreviatedOid":"a1b2c3d","messageHeadline":"refresh the stale context","committedDate":"2026-09-10T09:05:00Z","author":{"name":"An Author"}}},
      {"commit":{"oid":"e4f5a6b7","abbreviatedOid":"e4f5a6b","messageHeadline":"address review","committedDate":"2026-09-11T11:00:00Z","author":{"name":"An Author"}}},
      {"commit":{"oid":"c8d9e0f1","abbreviatedOid":"c8d9e0f","messageHeadline":"add the regression test","committedDate":"2026-09-12T17:00:00Z","author":null}}
    ]},
    "files":{"totalCount":2,"pageInfo":{"hasNextPage":false},"nodes":[
      {"path":"pkg/handler.go","additions":9,"deletions":1,"changeType":"MODIFIED"},
      {"path":"pkg/widget.go","additions":3,"deletions":0,"changeType":"ADDED"}
    ]},
    "reviews":{"totalCount":1,"nodes":[
      {"author":{"login":"a-reviewer"},"state":"CHANGES_REQUESTED","submittedAt":"2026-09-11T10:00:00Z"}
    ]},
    "reviewThreads":{"totalCount":7,"nodes":[
      {"isResolved":false},{"isResolved":false},{"isResolved":false},
      {"isResolved":true},{"isResolved":true},{"isResolved":true},{"isResolved":true}
    ]},
    "rollup":{"nodes":[{"commit":{"statusCheckRollup":{
      "state":"FAILURE",
      "contexts":{"totalCount":7,"nodes":[
        {"__typename":"CheckRun","name":"build","conclusion":"FAILURE","status":"COMPLETED"},
        {"__typename":"CheckRun","name":"lint","conclusion":"FAILURE","status":"COMPLETED"},
        {"__typename":"StatusContext","context":"legacy","state":"FAILURE"},
        {"__typename":"CheckRun","name":"slow","conclusion":"","status":"IN_PROGRESS"},
        {"__typename":"StatusContext","context":"waiting","state":"PENDING"},
        {"__typename":"CheckRun","name":"unit","conclusion":"SUCCESS","status":"COMPLETED"},
        {"__typename":"StatusContext","context":"ok","state":"SUCCESS"}
      ]}
    }}}]}
  }}
}}`

func TestDecodeAPullRequestPopulatesEveryPanel(t *testing.T) {
	s, err := decodeFixture([]byte(prFixture), "gardenersguild/trowelcast", 1559)
	if err != nil {
		t.Fatal(err)
	}

	// 🔴 §10.2 — the viewer login rides the SAME round trip and must survive
	// decoding. It is the multi-account mitigation, and it is not optional.
	if s.ViewerLogin != "a-reviewer" {
		t.Errorf("ViewerLogin = %q, want %q", s.ViewerLogin, "a-reviewer")
	}
	if s.Kind != KindPullRequest {
		t.Errorf("Kind = %q", s.Kind)
	}
	if s.Title != "Refresh the stale context before running" {
		t.Errorf("Title = %q", s.Title)
	}
	if s.Additions != 312 || s.Deletions != 40 || s.ChangedFiles != 2 {
		t.Errorf("counts = +%d -%d over %d files", s.Additions, s.Deletions, s.ChangedFiles)
	}
	if s.Mergeable != "MERGEABLE" || s.MergeStateStatus != "BLOCKED" {
		t.Errorf("merge = %q/%q", s.Mergeable, s.MergeStateStatus)
	}
	// 🔴 A NULL reviewDecision DECODES TO "", NOT TO A GUESS. It is what the
	// server really returns for a PR nobody has reviewed — measured on a real
	// PR — and the UI renders it as the WORD `NONE`.
	if s.ReviewDecision != "" {
		t.Errorf("ReviewDecision = %q, want empty for a null", s.ReviewDecision)
	}

	if len(s.Commits) != 3 {
		t.Fatalf("Commits = %d, want 3", len(s.Commits))
	}
	if s.Commits[0].Abbrev != "a1b2c3d" || s.Commits[0].Headline != "refresh the stale context" {
		t.Errorf("Commits[0] = %+v", s.Commits[0])
	}
	// A deleted account decodes to "", not to a panic.
	if s.Commits[2].Author != "" {
		t.Errorf("a null author decoded to %q", s.Commits[2].Author)
	}
	if s.CommitsTruncated {
		t.Error("CommitsTruncated is set on a 3-of-3 page")
	}

	if len(s.Files) != 2 || s.Files[1].Path != "pkg/widget.go" || s.Files[1].ChangeType != "ADDED" {
		t.Errorf("Files = %+v", s.Files)
	}
	if s.FilesTruncated {
		t.Error("FilesTruncated is set on a 2-of-2 page")
	}

	// Counted BY HAND off the fixture: 7 threads, 3 of them unresolved.
	if s.Threads.Total != 7 || s.Threads.Unresolved != 3 {
		t.Errorf("Threads = %+v, want {Total:7 Unresolved:3}", s.Threads)
	}
	// Counted BY HAND off the contexts: 2 failing CheckRuns + 1 failing
	// StatusContext = 3 failing; 1 IN_PROGRESS CheckRun + 1 PENDING
	// StatusContext = 2 pending.
	if s.Checks.Failing != 3 {
		t.Errorf("Checks.Failing = %d, want 3", s.Checks.Failing)
	}
	if s.Checks.Pending != 2 {
		t.Errorf("Checks.Pending = %d, want 2", s.Checks.Pending)
	}
	if s.Checks.Total != 7 {
		t.Errorf("Checks.Total = %d, want 7", s.Checks.Total)
	}
	if s.Checks.State != "FAILURE" {
		t.Errorf("Checks.State = %q", s.Checks.State)
	}
}

// 🔴 `hasNextPage` IS CARRIED, NOT INFERRED, so a PR past the 100-file cap says
// so in words instead of showing a list that LOOKS complete.
func TestAPagedFileListIsMarkedTruncated(t *testing.T) {
	var doc map[string]any
	if err := json.Unmarshal([]byte(prFixture), &doc); err != nil {
		t.Fatal(err)
	}
	node := doc["data"].(map[string]any)["repository"].(map[string]any)["issueOrPullRequest"].(map[string]any)
	node["files"].(map[string]any)["pageInfo"].(map[string]any)["hasNextPage"] = true
	raw, _ := json.Marshal(doc)

	s, err := decodeFixture(raw, "gardenersguild/trowelcast", 1559)
	if err != nil {
		t.Fatal(err)
	}
	if !s.FilesTruncated {
		t.Error("hasNextPage=true did not set FilesTruncated")
	}
	// And the negative control, from the unmodified fixture, so "true" above is
	// a claim about the field rather than about a constant.
	s2, _ := decodeFixture([]byte(prFixture), "gardenersguild/trowelcast", 1559)
	if s2.FilesTruncated {
		t.Error("the unmodified fixture also reports truncated — the field is ignored")
	}
}

const issueFixture = `{"data":{
  "viewer":{"login":"a-reviewer"},
  "repository":{"issueOrPullRequest":{
    "__typename":"Issue",
    "number":1656,
    "title":"Widget refresh drops the stale context",
    "state":"OPEN",
    "body":"The widget keeps a context past its refresh window.",
    "url":"https://github.com/gardenersguild/trowelcast/issues/1656",
    "createdAt":"2026-09-13T08:00:00Z","updatedAt":"2026-09-13T08:00:00Z",
    "author":{"login":"an-author"}
  }}
}}`

// 🔴 THE SERVER ANSWERS THE ISSUE-VS-PR QUESTION, IN THE SAME ROUND TRIP.
// `mention-open.py` builds `/pull/{id}` for every mention and cannot know the
// kind — and must not find out, because it carries a test-pinned property that
// THE CLICK PATH MAKES NO NETWORK CALL.
func TestDecodeAnIssueYieldsTheCardFieldsAndNoPRFields(t *testing.T) {
	s, err := decodeFixture([]byte(issueFixture), "gardenersguild/trowelcast", 1656)
	if err != nil {
		t.Fatal(err)
	}
	if s.Kind != KindIssue {
		t.Fatalf("Kind = %q, want Issue", s.Kind)
	}
	if s.Body == "" || s.Title == "" || s.Author == "" {
		t.Errorf("the card fields are incomplete: %+v", s)
	}
	if s.ViewerLogin != "a-reviewer" {
		t.Errorf("ViewerLogin = %q", s.ViewerLogin)
	}
	// The PR-only fields stay zero rather than carrying stale or invented data.
	if len(s.Commits) != 0 || len(s.Files) != 0 || s.Additions != 0 {
		t.Errorf("an issue decoded PR fields: %+v", s)
	}
}

func TestANullRepositoryIsReportedAsNotFound(t *testing.T) {
	s, err := decodeFixture([]byte(`{"data":{"viewer":{"login":"x"},"repository":null}}`),
		"gardenersguild/trowelcast", 1559)
	if s != nil {
		t.Errorf("returned a snapshot for a null repository: %+v", s)
	}
	var ae *APIError
	if !errors.As(err, &ae) {
		t.Fatalf("err is %T, want *APIError", err)
	}
	if ae.State != AuthNotFound {
		t.Errorf("State = %v, want AuthNotFound", ae.State)
	}
	if !strings.Contains(ae.Detail, "gardenersguild/trowelcast#1559") {
		t.Errorf("Detail does not name the reference: %q", ae.Detail)
	}
}

func TestAGraphQLNotFoundErrorIsReportedAsNotFound(t *testing.T) {
	body := `{"errors":[{"type":"NOT_FOUND","message":"Could not resolve to a Repository"}]}`
	_, err := decodeFixture([]byte(body), "gardenersguild/trowelcast", 1559)
	var ae *APIError
	if !errors.As(err, &ae) || ae.State != AuthNotFound {
		t.Fatalf("err = %v, want a NOT_FOUND APIError", err)
	}
}

// 🔴 "I COULD NOT READ THIS RESPONSE" MUST NOT BE REPORTED AS "NOT FOUND".
//
// Consolidating the `errors[]` mapping into `Client.decodeGQLError` deleted the
// `Errors` field from `gqlResponse` and from `Mergeability`'s local struct, so
// neither caller decodes `errors` for itself any more. `decodeGQLError` returned
// nil on ANY unmarshal failure, which turned "present but not decodable" into
// "absent" — and the arm below the call site then answered NOT FOUND.
//
// MEASURED at `d2ec72c3`, body `{"errors":{"message":"you may not do that"}}`:
//
//	Fetch        -> NOT FOUND — o/n#1 is not visible to this token
//	Mergeability -> NOT FOUND — o/n#1 is not a pull request this token can read
//
// At `264a567f` both answered `AuthOther — unreadable response: …`. A confident,
// false diagnosis sends the operator to fix a token permission that is fine,
// which is strictly worse than saying the response could not be read.
//
// ⚠ BOTH DIRECTIONS ARE IN ONE TABLE ON PURPOSE. Making "undecodable" loud is
// only half the claim; the other half is that a body with NO `errors` — or a
// null one, or an empty array — still gets the NOT-FOUND answer, which is the
// right one. A fix that reported everything as unreadable would pass the first
// three cases and fail the last three.
func TestAnUnreadableGraphQLErrorsKeyIsNotReportedAsNotFound(t *testing.T) {
	// ⚠ WRITTEN OUT HERE, not composed from the strings under test: these are the
	// two false diagnoses the measurement above produced.
	notFoundWords := []string{
		"is not visible to this token",
		"is not a pull request this token can read",
	}

	shapes := []struct {
		name      string
		body      string
		wantState AuthState
		// wantUnreadable says the detail must be the could-not-read answer and
		// must NOT be either NOT-FOUND sentence.
		wantUnreadable bool
	}{
		{
			name:           "`errors` is an OBJECT — present, and not an array",
			body:           `{"errors":{"message":"you may not do that"}}`,
			wantState:      AuthOther,
			wantUnreadable: true,
		},
		{
			name:           "`errors` is a STRING — present, and not an array",
			body:           `{"errors":"you may not do that"}`,
			wantState:      AuthOther,
			wantUnreadable: true,
		},
		{
			name:           "`errors` is an array of the WRONG element type",
			body:           `{"errors":[42,17]}`,
			wantState:      AuthOther,
			wantUnreadable: true,
		},
		{
			name:      "no `errors` key at all — NOT FOUND is the correct answer",
			body:      `{"data":{"repository":null}}`,
			wantState: AuthNotFound,
		},
		{
			name:      "`errors` is null",
			body:      `{"errors":null,"data":{"repository":null}}`,
			wantState: AuthNotFound,
		},
		{
			name:      "`errors` is an EMPTY array",
			body:      `{"errors":[],"data":{"repository":null}}`,
			wantState: AuthNotFound,
		},
	}

	callers := map[string]func(*Client) error{
		"the panel read": func(c *Client) error {
			_, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
			return err
		},
		"the merge gate's read": func(c *Client) error {
			_, err := c.Mergeability(context.Background(), "gardenersguild", "trowelcast", 1559)
			return err
		},
	}

	for _, sh := range shapes {
		for callerName, call := range callers {
			t.Run(sh.name+", "+callerName, func(t *testing.T) {
				srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					_, _ = w.Write([]byte(sh.body))
				}))
				defer srv.Close()
				c := NewClient("tok-errors-shape-fixture", srv.Client())
				c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

				err := call(c)
				if err == nil {
					t.Fatal("the response was reported as a success")
				}
				var ae *APIError
				if !errors.As(err, &ae) {
					t.Fatalf("err is %T, want *APIError: %v", err, err)
				}
				if ae.State != sh.wantState {
					t.Errorf("State = %v, want %v — detail was %q", ae.State, sh.wantState, ae.Detail)
				}
				if !sh.wantUnreadable {
					return
				}
				if !strings.Contains(ae.Detail, "unreadable response") {
					t.Errorf("an `errors` key that is PRESENT and does not decode was not "+
						"reported as unreadable: %q", ae.Detail)
				}
				for _, w := range notFoundWords {
					if strings.Contains(ae.Detail, w) {
						t.Errorf("a response nobody managed to read was diagnosed as %q — that "+
							"sends the operator to fix a token permission that is fine: %q",
							w, ae.Detail)
					}
				}
			})
		}
	}
}

// --- the client, against a FAKE server ---------------------------------------

func TestFetchAgainstAFakeServerDecodesAndReusesOneRoundTrip(t *testing.T) {
	var calls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if got := r.Header.Get("Authorization"); got != "bearer a-test-token" {
			t.Errorf("Authorization = %q", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(prFixture))
	}))
	defer srv.Close()

	c := NewClient("a-test-token", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

	s, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
	if err != nil {
		t.Fatal(err)
	}
	if s.ViewerLogin != "a-reviewer" {
		t.Errorf("ViewerLogin = %q", s.ViewerLogin)
	}
	// 🔴 ONE ROUND TRIP. This is the Phase-0 kill criterion, pinned so a later
	// change that splits the query into two reads fails the suite rather than
	// quietly halving the tool's headline property.
	if calls != 1 {
		t.Errorf("Fetch made %d HTTP calls, want exactly 1", calls)
	}
}

// 🔴 AN EMPTY TOKEN NEVER REACHES THE NETWORK, AND IT PRODUCES `NO TOKEN`, NOT
// A 401. go-gh's fourth precedence rung returns ("", "default") with NO error,
// so an empty token is a VALUE rather than a failure — a client that only
// checked `err` would send an unauthenticated request and render a card naming
// the wrong fix.
func TestAnEmptyTokenShortCircuitsBeforeTheNetwork(t *testing.T) {
	var calls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()

	c := NewClient("", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

	_, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
	var ae *APIError
	if !errors.As(err, &ae) {
		t.Fatalf("err = %v, want an APIError", err)
	}
	if ae.State != AuthNoToken {
		t.Errorf("State = %v, want AuthNoToken", ae.State)
	}
	if calls != 0 {
		t.Errorf("an empty token still made %d request(s)", calls)
	}
	// POSITIVE CONTROL on the counter: a NON-empty token must reach the server,
	// or `calls == 0` proves nothing about the short circuit.
	c2 := NewClient("a-test-token", srv.Client())
	c2.SetBaseURLs(srv.URL+"/graphql", srv.URL)
	_, err2 := c2.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
	if calls == 0 {
		t.Fatal("a real token ALSO made no request — the counter is wired to nothing")
	}
	var ae2 *APIError
	if !errors.As(err2, &ae2) || ae2.State != AuthRejected {
		t.Errorf("a 401 with a token = %v, want TOKEN REJECTED", err2)
	}
}

// 🔴 THE TOKEN IS NEVER IN AN ERROR. §10.4: the cards say which condition holds
// and nothing more.
//
// 🔴 THE NAME IS A UNIVERSAL CLAIM, SO THE IMPLEMENTATION HAS TO BE ONE — AND
// FOR A WHILE IT WAS NOT. This test used to exercise exactly one path: an HTTP
// 401 whose BODY echoed the credential, decoded by `do`. It read as coverage of
// every error path while providing coverage of one, which is worse than no
// coverage because it stops anyone looking. It did not cover the GraphQL 200
// carrying `errors[0].message`, and that path was genuinely leaking: both
// `Fetch` and `Mergeability` returned `Detail: r.Errors[0].Message` raw, with no
// `redact` and no `clipDetail`.
//
// 🔴 THE LEDGER BELOW IS THE CLAIM, AND IT HAS BEEN SHORT TWICE. Every one of
// these is a path that turns SERVER-SUPPLIED text into an `APIError.Detail` the
// UI renders:
//
//  1. an HTTP failure status, body decoded by `apiMessage` inside `do`;
//  2. a GraphQL 200 whose `errors[]` is decoded by the panel read (`Fetch`);
//  3. a GraphQL 200 whose `errors[]` is decoded by the merge gate's read
//     (`Mergeability`);
//  4. the merge gate's `default:` refusal, which interpolates the SERVER's
//     `mergeable` word (`NormalizeMergeable` of `pr.Mergeable`) into the card;
//  5. `do`'s transport error, whose `url.Error` names the LAST request URL —
//     which a server chooses, because this client follows redirects and sets no
//     `CheckRedirect`. ⚠ IT IS NOT EXERCISED BY THE TABLE BELOW, and that is
//     deliberate rather than an omission: no credential is known to reach it
//     (the token is header-only and Go strips userinfo passwords), so it is the
//     unbounded-card hazard and not a leak. Its clip is pinned by
//     `TestATransportErrorNeverCarriesAnUnclippedRedirectTarget`.
//
// 🔴 ENTRY 4 IS THE ONE A COMMENT SAID COULD NOT EXIST. Round 1 added entries 2
// and 3 and closed the ledger with "Paths NOT listed carry no server text at
// all" — while `write.go`'s `default:` arm interpolated `read.Mergeable` with no
// `redact` and no `clipDetail`, measured at 5,081 unclipped runes from a probe
// server (`mergegate_test.go`'s fixture reproduces that length).
// `mergeable` is a GraphQL ENUM, so a CONFORMING server cannot send text
// there — which is exactly as true of `errors[0].message`, and this file treats
// that one as a real defect. The threat model is a proxy or a gateway, not the
// schema, and it does not stop at one field.
//
// ⚠ ENTRY 4 REDACTS AT A DIFFERENT POINT, AND THAT IS NOT A STYLE CHOICE.
// `NormalizeMergeable` UPPERCASES the server's string, so a redaction applied
// after it would be looking for a needle that no longer exists in the haystack.
// `Mergeability` redacts BEFORE normalising, which is why this case expects the
// marker in upper case.
//
// Paths NOT listed carry no server text at all: the NOT-FOUND details are
// composed from the owner/name/number this program already holds, and the
// `unreadable response: …` / `unreadable files response: …` details are the JSON
// decoder's own prose — which can quote one offending byte of the body, so they
// go through `Client.detail` too rather than resting on that being harmless.
//
// 🔴 THIS LEDGER IS AN INSTRUCTION TO MAINTAINERS. NOTHING ABOUT IT IS
// ENFORCED, AND `query.go` USED TO SAY OTHERWISE. The table below is
// hand-written and walked by a `for` loop: it enumerates no call site, counts no
// `Client.detail` invocation and inspects no source, so a sixth reflecting path
// added tomorrow leaves it green. `query.go` claimed this test "fails if a fifth
// appears unledgered" while entry 5 above already existed unledgered — the third
// time a completeness sentence in this change has been false. What IS enforced,
// mechanically, is narrower and lives elsewhere:
// `TestEveryAPIErrorDetailIsRoutedOrLedgered` parses this package's own non-test
// `.go` files and fails on a `Detail` — set either as an `APIError` literal's
// keyed `Detail:` element or by assignment to a `.Detail` selector — that is
// neither a literal, nor routed through `Client.detail`, nor ledgered at its own
// `file:function` site with a reason. That is the whole of it: it reads syntax,
// in one package, so it cannot tell you whether a path carries SERVER text, and
// it cannot see a `Detail` set outside this package, through an interface, or by
// reflection. It is a check on the shapes it parses, not a proof about the UI.
func TestNoErrorPathEverCarriesTheToken(t *testing.T) {
	const secret = "gho_thisisnotarealtokenitisatestfixture"

	// Each case answers ONE canned body and then calls ONE client method.
	paths := []struct {
		name   string
		status int
		body   string
		call   func(*Client) error
		// marker is the redaction marker this path leaves behind. It is upper
		// case for the one path that normalises the string after redacting it.
		marker string
	}{
		{
			name:   "an HTTP failure body decoded by `do`",
			status: http.StatusUnauthorized,
			body:   `{"message":"Bad credentials for ` + secret + `"}`,
			call: func(c *Client) error {
				_, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
				return err
			},
		},
		{
			name:   "a GraphQL errors[] entry on the panel read",
			status: http.StatusOK,
			body: `{"errors":[{"type":"FORBIDDEN","message":"the credential ` + secret +
				` may not read this"}]}`,
			call: func(c *Client) error {
				_, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
				return err
			},
		},
		{
			name:   "a GraphQL errors[] entry on the merge gate's read",
			status: http.StatusOK,
			body: `{"errors":[{"type":"FORBIDDEN","message":"the credential ` + secret +
				` may not read this"}]}`,
			call: func(c *Client) error {
				_, err := c.Mergeability(context.Background(), "gardenersguild", "trowelcast", 1559)
				return err
			},
		},
		{
			// 🔴 THE FOURTH PATH. A pull request that is OPEN and not merged, so
			// the gate reaches its mergeability switch, answering a `mergeable`
			// the server made up. Nothing is ever PUT — the `default:` arm refuses.
			name:   "the server's `mergeable` word reflected by the merge gate's refusal",
			status: http.StatusOK,
			body: `{"data":{"repository":{"pullRequest":{"mergeable":"` + secret +
				`","state":"OPEN","merged":false}}}}`,
			call: func(c *Client) error {
				return c.Merge(context.Background(), "gardenersguild", "trowelcast", 1559, "squash")
			},
			marker: "<REDACTED>",
		},
	}

	for _, p := range paths {
		t.Run(p.name, func(t *testing.T) {
			marker := p.marker
			if marker == "" {
				marker = "<redacted>"
			}
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				// Even a server that echoes the credential back must not get it
				// into an error the UI renders.
				w.WriteHeader(p.status)
				_, _ = w.Write([]byte(p.body))
			}))
			defer srv.Close()

			c := NewClient(secret, srv.Client())
			c.SetBaseURLs(srv.URL+"/graphql", srv.URL)
			err := p.call(c)
			if err == nil {
				t.Fatal("expected an error")
			}
			// ⚠ THE SERVER ECHOED IT, SO THIS *CAN* FAIL — which is what makes the
			// assertion evidence rather than a tautology. The client must not
			// reflect the credential out of a response body it did not construct.
			if strings.Contains(err.Error(), secret) {
				t.Errorf("the token leaked into an error string: %q", err.Error())
			}
			// 🔴 AND NOT IN ANY CASE EITHER. `NormalizeMergeable` uppercases the
			// server's word, so a redaction applied AFTER it would miss the
			// credential entirely and ship `GHO_…` to the card — the whole secret
			// with its case changed. Checking only the exact spelling would call
			// that a pass.
			if strings.Contains(strings.ToUpper(err.Error()), strings.ToUpper(secret)) {
				t.Errorf("the token leaked into an error string in a different case — a "+
					"transform between the redaction and the card moved it out from under "+
					"the needle: %q", err.Error())
			}
			// POSITIVE CONTROL 1: the error is not empty, so "does not contain" is
			// not satisfied by there being no error text at all.
			if err.Error() == "" {
				t.Fatal("the error is empty — the leak check observed nothing")
			}
			// POSITIVE CONTROL 2: the redaction MARKER is present, so the check
			// above is not satisfied by the whole message having been dropped.
			if !strings.Contains(err.Error(), marker) {
				t.Errorf("the server's message was discarded rather than redacted, so "+
					"this case would pass with no redaction at all: %q", err.Error())
			}
		})
	}
}

// 🔴 A GRAPHQL `errors[0].message` IS CLIPPED LIKE EVERY OTHER DETAIL, AND IT
// USED NOT TO BE.
//
// `maxDetailRunes`' own docstring says it "caps the WHOLE composed detail, after
// redaction", and that sentence was false: the cap lived at the single call site
// inside `do`, so a GraphQL 200 carrying a 5,000-rune `errors[0].message` went
// to the UI at full length. Both GraphQL decoders share `decodeGQLError` now,
// which is where the cap moved to.
//
// ⚠ THE CEILING IS ABSOLUTE AND IS NOT MADE OF THE CONSTANT UNDER TEST. A
// mutant that raises `maxDetailRunes` still "clips", at its own number, so an
// assertion phrased only against that constant cannot see it. Same trap, same
// control, as `TestOnePathologicalEntryCannotBlowUpTheCard`.
func TestAGraphQLErrorMessageIsClippedLikeEveryOtherDetail(t *testing.T) {
	// 5,000 runes in one entry — the shape the auditor measured coming out
	// intact at 5,057.
	huge := strings.Repeat("qwertyuiop", 500)
	body := `{"errors":[{"type":"INTERNAL","message":"` + huge + `"}]}`

	calls := map[string]func(*Client) error{
		"the panel read": func(c *Client) error {
			_, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
			return err
		},
		"the merge gate's read": func(c *Client) error {
			_, err := c.Mergeability(context.Background(), "gardenersguild", "trowelcast", 1559)
			return err
		},
	}
	for name, call := range calls {
		t.Run(name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = w.Write([]byte(body))
			}))
			defer srv.Close()
			c := NewClient("tok-graphql-clip-fixture", srv.Client())
			c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

			err := call(c)
			if err == nil {
				t.Fatal("a GraphQL errors[] response was reported as a success")
			}
			var ae *APIError
			if !errors.As(err, &ae) {
				t.Fatalf("err is %T, want *APIError: %v", err, err)
			}
			const cardCeiling = 600
			n := len([]rune(ae.Detail))
			if n > cardCeiling {
				t.Errorf("the detail is %d runes — past the %d-rune ceiling a single "+
					"terminal line can carry, whatever `maxDetailRunes` says", n, cardCeiling)
			}
			if n != maxDetailRunes {
				t.Errorf("the detail is %d runes, want it clipped to exactly %d", n, maxDetailRunes)
			}
			if !strings.HasSuffix(ae.Detail, "…") {
				t.Errorf("a clipped detail must say it was clipped: %q", ae.Detail)
			}
			// POSITIVE CONTROL: a SHORT GraphQL message is not padded or cut, so
			// the equality above is a claim about clipping rather than about a
			// decoder that always emits the same length.
			srv2 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = w.Write([]byte(`{"errors":[{"type":"INTERNAL","message":"something went wrong"}]}`))
			}))
			defer srv2.Close()
			c2 := NewClient("tok-graphql-clip-fixture", srv2.Client())
			c2.SetBaseURLs(srv2.URL+"/graphql", srv2.URL)
			err2 := call(c2)
			var ae2 *APIError
			if !errors.As(err2, &ae2) {
				t.Fatalf("err2 is %T, want *APIError: %v", err2, err2)
			}
			if ae2.Detail != "something went wrong" {
				t.Errorf("a short GraphQL message came back as %q, want it verbatim", ae2.Detail)
			}
		})
	}
}

// 🔴 THE FIFTH REFLECTING PATH: A REDIRECT TARGET THE SERVER CHOSE.
//
// `do` reported a transport failure as `"could not reach api.github.com: " +
// err.Error()` with no `Client.detail`. This client sets no `CheckRedirect`, so
// Go follows redirects and the resulting `url.Error` names the LAST request URL
// — a string the SERVER supplied, in a `Location` header. MEASURED at
// `cb7c7949`: a 5,000-rune redirect path produced a detail past 5,000 runes,
// longer than the merge-gate path the same change was fixing, while `query.go`
// asserted that a fifth such path could not appear unnoticed. ⚠ NO EXACT LENGTH
// IS QUOTED, because it moves with the ephemeral port inside the reflected URL —
// the assertion below is a ceiling, not an equality, for that reason.
//
// ⚠ THIS IS AN UNBOUNDED CARD, NOT A LEAK, AND THE TEST CLAIMS ONLY THAT. The
// token is header-only and Go strips userinfo passwords out of a `url.Error`, so
// nothing here says a credential can reach this string.
//
// ⚠ THE CEILING IS ABSOLUTE AND IS NOT MADE OF THE CONSTANT UNDER TEST. A mutant
// that raises `maxDetailRunes` still "clips", at its own number.
func TestATransportErrorNeverCarriesAnUnclippedRedirectTarget(t *testing.T) {
	var target string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", target)
		w.WriteHeader(http.StatusFound)
	}))
	defer srv.Close()
	// ⚠ IT REDIRECTS TO ITSELF, so the client gives up after ten hops rather than
	// dialling a port this test guessed at — the failure is deterministic and
	// stays on loopback.
	target = srv.URL + "/" + strings.Repeat("z", 5000)

	c := NewClient("tok-redirect-clip-fixture", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

	_, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
	if err == nil {
		t.Fatal("a redirect loop was reported as a success")
	}
	var ae *APIError
	if !errors.As(err, &ae) {
		t.Fatalf("err is %T, want *APIError: %v", err, err)
	}
	// This case proves nothing unless it went down the transport arm.
	if !strings.HasPrefix(ae.Detail, "could not reach api.github.com: ") {
		t.Fatalf("this is not `do`'s transport path: %.160q", ae.Detail)
	}
	const cardCeiling = 600
	if n := len([]rune(ae.Detail)); n > cardCeiling {
		t.Errorf("the detail is %d runes — past the %d-rune ceiling a single terminal "+
			"line can carry, whatever `maxDetailRunes` says. The server's redirect "+
			"target reached the card unclipped: %.160q", n, cardCeiling, ae.Detail)
	}
	if !strings.HasSuffix(ae.Detail, "…") {
		t.Errorf("a 5,000-rune redirect target produced a detail with no clip marker: %.160q",
			ae.Detail)
	}

	// 🔴 POSITIVE CONTROL: AN ORDINARY SHORT TRANSPORT FAILURE COMES THROUGH
	// WHOLE. Without it the assertions above are satisfied by a path that
	// truncates everything, including the one word that says what went wrong.
	dead := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	deadURL := dead.URL
	dead.Close()
	c2 := NewClient("tok-redirect-clip-fixture", nil)
	c2.SetBaseURLs(deadURL+"/graphql", deadURL)
	_, err2 := c2.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
	var ae2 *APIError
	if !errors.As(err2, &ae2) {
		t.Fatalf("err2 is %T, want *APIError: %v", err2, err2)
	}
	if strings.Contains(ae2.Detail, "…") {
		t.Errorf("a short dial failure was clipped — the bound is wired to something much "+
			"smaller than a card: %q", ae2.Detail)
	}
	if !strings.HasPrefix(ae2.Detail, "could not reach api.github.com: ") {
		t.Errorf("a short dial failure lost the sentence that names the condition: %q", ae2.Detail)
	}
}

// --- the REST diff read ------------------------------------------------------

func TestFetchFilesNormalisesRESTStatusOntoOneVocabulary(t *testing.T) {
	const files = `[
	  {"filename":"pkg/handler.go","status":"modified","additions":9,"deletions":1,"patch":"@@ -1,1 +1,2 @@\n a\n+b"},
	  {"filename":"pkg/widget.go","status":"added","additions":3,"deletions":0,"patch":"@@ -0,0 +1,3 @@\n+a\n+b\n+c"},
	  {"filename":"pkg/old.go","status":"removed","additions":0,"deletions":2,"patch":"@@ -1,2 +0,0 @@\n-a\n-b"},
	  {"filename":"pkg/new_name.go","status":"renamed","previous_filename":"pkg/old_name.go","additions":0,"deletions":0},
	  {"filename":"assets/logo.png","status":"modified","additions":0,"deletions":0}
	]`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.RawQuery, "per_page=100") {
			t.Errorf("query = %q, want per_page=100", r.URL.RawQuery)
		}
		_, _ = w.Write([]byte(files))
	}))
	defer srv.Close()

	c := NewClient("a-test-token", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

	got, truncated, err := c.FetchFiles(context.Background(), "gardenersguild", "trowelcast", 1559)
	if err != nil {
		t.Fatal(err)
	}
	if truncated {
		t.Error("a 5-file page is marked truncated")
	}
	// 🔴 ONE VOCABULARY. The two endpoints spell the same fact differently
	// ("removed" vs "REMOVED"); a predicate open-coded at each reader is wrong
	// at all but one of them, in the same direction.
	want := []string{"MODIFIED", "ADDED", "REMOVED", "RENAMED", "MODIFIED"}
	for i, w := range want {
		if got[i].ChangeType != w {
			t.Errorf("file %d ChangeType = %q, want %q", i, got[i].ChangeType, w)
		}
	}
	if got[3].PreviousPath != "pkg/old_name.go" {
		t.Errorf("rename lost its previous path: %+v", got[3])
	}
	// 🔴 A MISSING PATCH IS AN EMPTY STRING, not an error and not a fabricated
	// empty diff. §6.1 renders it as the WORD `NO PATCH`.
	if got[4].Patch != "" {
		t.Errorf("a patchless file carries a patch: %q", got[4].Patch)
	}
	if got[0].Patch == "" {
		t.Error("a file WITH a patch lost it — the field is not being read")
	}
}

// An unknown REST status is reported as ITSELF rather than mapped onto a guess.
// A silent fallback to MODIFIED would make a new GitHub status render as an
// ordinary edit.
func TestAnUnknownRESTStatusIsNotMappedToModified(t *testing.T) {
	if got := restStatusToChangeType("teleported"); got == "MODIFIED" {
		t.Error("an unknown status was mapped onto MODIFIED")
	}
	if got := restStatusToChangeType("teleported"); got != "teleported" {
		t.Errorf("= %q, want the status echoed back", got)
	}
}
