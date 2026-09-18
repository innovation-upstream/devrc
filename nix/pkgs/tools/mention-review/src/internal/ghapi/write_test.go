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
)

// 🔴 EVERY REQUEST IN THIS FILE GOES TO AN `httptest` SERVER ON LOOPBACK.
// `nonet_test.go` makes that structural rather than conventional: its
// `TestMain` blocks any non-loopback host, with a control proving it rejects
// api.github.com.

// recorder captures what the client actually sent, so the assertions are about
// the REQUEST rather than about the absence of an error. A client that sent the
// wrong method to the wrong path and got a 200 would otherwise pass.
type recorder struct {
	mu     sync.Mutex
	method string
	path   string
	body   map[string]any
	status int
	reply  string
}

func (r *recorder) handler() http.HandlerFunc {
	return func(w http.ResponseWriter, req *http.Request) {
		r.mu.Lock()
		defer r.mu.Unlock()
		r.method, r.path = req.Method, req.URL.Path
		raw, _ := io.ReadAll(req.Body)
		r.body = map[string]any{}
		_ = json.Unmarshal(raw, &r.body)
		if r.status == 0 {
			r.status = http.StatusOK
		}
		w.WriteHeader(r.status)
		if r.reply != "" {
			_, _ = io.WriteString(w, r.reply)
		}
	}
}

func newRecorded(t *testing.T, status int, reply string) (*Client, *recorder, func()) {
	t.Helper()
	rec := &recorder{status: status, reply: reply}
	srv := httptest.NewServer(rec.handler())
	c := NewClient("tok-not-a-real-credential", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)
	return c, rec, srv.Close
}

// ⚠ THE FIXTURES ARE SYNTHETIC AND PAIRWISE DISTINCT — owner `gardenersguild`,
// repo `trowelcast`, number 1559. This repository is PUBLIC, and a fixture that
// can only ever produce a constant's own value cannot see a mutant that
// hardcodes the literal.
const (
	wOwner = "gardenersguild"
	wName  = "trowelcast"
	wNum   = 1559
)

func TestPostCommentSendsThePRLevelEndpointAndTheBody(t *testing.T) {
	c, rec, done := newRecorded(t, http.StatusCreated, `{"id":1}`)
	defer done()

	if err := c.PostComment(context.Background(), wOwner, wName, wNum, "looks right"); err != nil {
		t.Fatalf("PostComment: %v", err)
	}
	if rec.method != http.MethodPost {
		t.Errorf("method = %s, want POST", rec.method)
	}
	// 🔴 `/issues/{n}/comments` IS THE PR-LEVEL ENDPOINT. `/pulls/{n}/comments`
	// is the INLINE one, which needs a diff position — and inline commenting is
	// explicitly out of scope for this phase. A test that only checked "some
	// POST happened" would not see that swap.
	if want := "/repos/gardenersguild/trowelcast/issues/1559/comments"; rec.path != want {
		t.Errorf("path = %s, want %s", rec.path, want)
	}
	if got := rec.body["body"]; got != "looks right" {
		t.Errorf("body = %v, want %q", got, "looks right")
	}
}

// 🔴 A `201 Created` IS A SUCCESS. The comment endpoint answers 201 where every
// Phase-1 read answered 200; before `Classify` was widened, a successfully
// posted comment rendered as an ERROR card. This is the regression test for
// that, driven through the real client rather than through `Classify` alone.
func TestA201FromTheCommentEndpointIsNotAnError(t *testing.T) {
	if got := Classify(true, http.StatusCreated); got != AuthOK {
		t.Errorf("Classify(true, 201) = %v, want AuthOK", got)
	}
	c, _, done := newRecorded(t, http.StatusCreated, `{"id":1}`)
	defer done()
	if err := c.PostComment(context.Background(), wOwner, wName, wNum, "x"); err != nil {
		t.Errorf("a 201 was reported as an error: %v", err)
	}
}

func TestSubmitReviewSendsTheEventAndTheBody(t *testing.T) {
	cases := []struct {
		event string
		body  string
	}{
		{ReviewApprove, ""},
		{ReviewRequestChanges, "please split this"},
		{ReviewComment, "a note"},
	}
	for _, tc := range cases {
		t.Run(tc.event, func(t *testing.T) {
			c, rec, done := newRecorded(t, http.StatusOK, `{"id":2}`)
			defer done()
			if err := c.SubmitReview(context.Background(), wOwner, wName, wNum, tc.event, tc.body); err != nil {
				t.Fatalf("SubmitReview(%s): %v", tc.event, err)
			}
			if want := "/repos/gardenersguild/trowelcast/pulls/1559/reviews"; rec.path != want {
				t.Errorf("path = %s, want %s", rec.path, want)
			}
			if got := rec.body["event"]; got != tc.event {
				t.Errorf("event = %v, want %q", got, tc.event)
			}
			if tc.body == "" {
				if _, present := rec.body["body"]; present {
					t.Errorf("an empty body was sent as a field: %v", rec.body)
				}
			} else if got := rec.body["body"]; got != tc.body {
				t.Errorf("body = %v, want %q", got, tc.body)
			}
		})
	}
}

// 🔴 AN UNRECOGNISED EVENT NEVER LEAVES THIS PROCESS. `ui/run.go` maps three
// distinct intents onto this one endpoint, so a typo in one of them would be a
// review submitted with the wrong verdict — approving where the operator asked
// to block.
func TestSubmitReviewRefusesAnUnknownEventWithoutSendingAnything(t *testing.T) {
	c, rec, done := newRecorded(t, http.StatusOK, "")
	defer done()
	err := c.SubmitReview(context.Background(), wOwner, wName, wNum, "APPROVE_MAYBE", "x")
	if err == nil {
		t.Fatal("an unknown review event was accepted")
	}
	if !strings.Contains(err.Error(), "refusing to submit review event") {
		t.Errorf("the refusal came from elsewhere: %v", err)
	}
	if rec.method != "" {
		t.Errorf("a request was sent anyway: %s %s", rec.method, rec.path)
	}
}

func TestRequestingChangesWithAnEmptyBodyIsRefusedLocally(t *testing.T) {
	c, rec, done := newRecorded(t, http.StatusOK, "")
	defer done()
	err := c.SubmitReview(context.Background(), wOwner, wName, wNum, ReviewRequestChanges, "  ")
	if err == nil {
		t.Fatal("REQUEST_CHANGES with an empty body was accepted")
	}
	if !strings.Contains(err.Error(), "empty body") {
		t.Errorf("the refusal came from elsewhere: %v", err)
	}
	if rec.method != "" {
		t.Errorf("a request was sent anyway: %s %s", rec.method, rec.path)
	}
	// POSITIVE CONTROL: APPROVE with an empty body IS allowed, so the arm above
	// is the REQUEST_CHANGES rule and not a blanket ban on empty bodies.
	if err := c.SubmitReview(context.Background(), wOwner, wName, wNum, ReviewApprove, ""); err != nil {
		t.Errorf("APPROVE with no body was refused: %v", err)
	}
}

func TestMergeSendsThePutAndTheMethod(t *testing.T) {
	c, rec, done := newRecorded(t, http.StatusOK, `{"merged":true}`)
	defer done()
	// ⚠ `rebase`, NOT `squash`. The declared default is `squash`, so a mutant
	// that ignored the argument and used the default would produce the default
	// here and be caught.
	if err := c.Merge(context.Background(), wOwner, wName, wNum, "rebase", MergeableYes); err != nil {
		t.Fatalf("Merge: %v", err)
	}
	if rec.method != http.MethodPut {
		t.Errorf("method = %s, want PUT", rec.method)
	}
	if want := "/repos/gardenersguild/trowelcast/pulls/1559/merge"; rec.path != want {
		t.Errorf("path = %s, want %s", rec.path, want)
	}
	if got := rec.body["merge_method"]; got != "rebase" {
		t.Errorf("merge_method = %v, want %q", got, "rebase")
	}
}

// 🔴 THE SECOND LOCK ON `MERGE_METHOD_UNKNOWN`. The UI refuses first; this
// refuses even if that arm were removed, and it refuses WITHOUT sending
// anything — which is the property that matters, since a merge cannot be undone.
func TestMergeRefusesAnEmptyOrUnknownMethodWithoutSendingAnything(t *testing.T) {
	for _, method := range []string{"", "fast-forward", "SQUASH"} {
		t.Run("method="+method, func(t *testing.T) {
			c, rec, done := newRecorded(t, http.StatusOK, "")
			defer done()
			err := c.Merge(context.Background(), wOwner, wName, wNum, method, MergeableYes)
			if err == nil {
				t.Fatalf("merge with method %q was accepted", method)
			}
			if !strings.Contains(err.Error(), "refusing to merge") {
				t.Errorf("the refusal came from elsewhere: %v", err)
			}
			if rec.method != "" {
				t.Fatalf("a MERGE REQUEST was sent with method %q: %s %s",
					method, rec.method, rec.path)
			}
		})
	}
	// POSITIVE CONTROL: a valid method DOES send, so the arms above are the
	// validation and not a merge path that is broken outright.
	c, rec, done := newRecorded(t, http.StatusOK, `{"merged":true}`)
	defer done()
	if err := c.Merge(context.Background(), wOwner, wName, wNum, "squash", MergeableYes); err != nil {
		t.Fatalf("a valid method was refused: %v", err)
	}
	if rec.method != http.MethodPut {
		t.Fatal("a valid method sent nothing — every refusal above proves nothing")
	}
}

func TestAnEmptyCommentIsRefusedLocally(t *testing.T) {
	c, rec, done := newRecorded(t, http.StatusCreated, "")
	defer done()
	if err := c.PostComment(context.Background(), wOwner, wName, wNum, "\n \t"); err == nil {
		t.Fatal("an empty comment was accepted")
	}
	if rec.method != "" {
		t.Errorf("a request was sent anyway: %s %s", rec.method, rec.path)
	}
}

// 🔴 A WRITE GOES THROUGH THE SAME AUTH AND REDACTION PATH AS A READ. `write`
// calls `do`, so an empty token short-circuits before the network and a server
// message that echoed the credential back is redacted. Both were pinned for the
// read path in Phase 1 and neither is inherited by assertion.
func TestAWriteWithNoTokenNeverReachesTheNetwork(t *testing.T) {
	rec := &recorder{status: http.StatusOK}
	srv := httptest.NewServer(rec.handler())
	defer srv.Close()
	c := NewClient("", srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

	err := c.Merge(context.Background(), wOwner, wName, wNum, "squash", MergeableYes)
	if err == nil {
		t.Fatal("a merge with no token was accepted")
	}
	var ae *APIError
	if !asAPIError(err, &ae) || ae.State != AuthNoToken {
		t.Fatalf("err = %v, want a NO TOKEN APIError", err)
	}
	if rec.method != "" {
		t.Errorf("a request was sent with no token: %s %s", rec.method, rec.path)
	}
}

func TestAWriteErrorNeverCarriesTheToken(t *testing.T) {
	const token = "tok-not-a-real-credential"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// The server reflects the credential back, which GitHub does not do
		// today and which nothing guarantees a proxy or gateway never will.
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = io.WriteString(w, `{"message":"Bad credentials for `+token+`"}`)
	}))
	defer srv.Close()
	c := NewClient(token, srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

	err := c.PostComment(context.Background(), wOwner, wName, wNum, "x")
	if err == nil {
		t.Fatal("a 401 was reported as success")
	}
	if strings.Contains(err.Error(), token) {
		t.Fatalf("the write error path leaked the token: %v", err)
	}
	// POSITIVE CONTROL: the redaction marker IS present, so the assertion above
	// is not passing because the message was dropped entirely.
	if !strings.Contains(err.Error(), "<redacted>") {
		t.Errorf("the message was not redacted, it was discarded: %v", err)
	}
}

// asAPIError is `errors.As` spelled locally so this file does not grow an
// import purely for one call.
func asAPIError(err error, target **APIError) bool {
	for err != nil {
		if e, ok := err.(*APIError); ok {
			*target = e
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

// 🔴 THE REVIEW-EVENT SET IS CLOSED, AND `ui/run.go` MAY ONLY SEND FROM IT.
// This is the seam between the two packages: `ui` names three constants, and
// this is the only place that says which strings they may be.
func TestTheReviewEventSetIsExactlyThree(t *testing.T) {
	got := ReviewEvents()
	want := []string{"APPROVE", "REQUEST_CHANGES", "COMMENT"}
	if len(got) != len(want) {
		t.Fatalf("ReviewEvents() = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("ReviewEvents()[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}
