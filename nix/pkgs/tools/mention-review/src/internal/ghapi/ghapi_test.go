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
	s, err := decodeSnapshot([]byte(prFixture), "gardenersguild/trowelcast", 1559)
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

	s, err := decodeSnapshot(raw, "gardenersguild/trowelcast", 1559)
	if err != nil {
		t.Fatal(err)
	}
	if !s.FilesTruncated {
		t.Error("hasNextPage=true did not set FilesTruncated")
	}
	// And the negative control, from the unmodified fixture, so "true" above is
	// a claim about the field rather than about a constant.
	s2, _ := decodeSnapshot([]byte(prFixture), "gardenersguild/trowelcast", 1559)
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
	s, err := decodeSnapshot([]byte(issueFixture), "gardenersguild/trowelcast", 1656)
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
	s, err := decodeSnapshot([]byte(`{"data":{"viewer":{"login":"x"},"repository":null}}`),
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
	_, err := decodeSnapshot([]byte(body), "gardenersguild/trowelcast", 1559)
	var ae *APIError
	if !errors.As(err, &ae) || ae.State != AuthNotFound {
		t.Fatalf("err = %v, want a NOT_FOUND APIError", err)
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
func TestNoErrorPathEverCarriesTheToken(t *testing.T) {
	const secret = "gho_thisisnotarealtokenitisatestfixture"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		// Even a server that echoes the credential back must not get it into
		// an error the UI renders.
		_, _ = w.Write([]byte(`{"message":"Bad credentials for ` + secret + `"}`))
	}))
	defer srv.Close()

	c := NewClient(secret, srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)
	_, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
	if err == nil {
		t.Fatal("expected an error")
	}
	// ⚠ THE SERVER ECHOED IT, SO THIS *CAN* FAIL — which is what makes the
	// assertion evidence rather than a tautology. The client must not reflect
	// the credential out of a response body it did not construct.
	if strings.Contains(err.Error(), secret) {
		t.Errorf("the token leaked into an error string: %q", err.Error())
	}
	// The positive control: the error is not empty, so "does not contain" is
	// not satisfied by there being no error text at all.
	if err.Error() == "" {
		t.Error("the error is empty — the leak check observed nothing")
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
