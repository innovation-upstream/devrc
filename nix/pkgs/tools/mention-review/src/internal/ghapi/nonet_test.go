package ghapi

import (
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// 🔴 NO TEST IN THIS PACKAGE MAY REACH THE REAL GITHUB API — AND THIS IS THE
// MECHANISM, NOT THE PROMISE.
//
// Phase 2 can approve and merge real pull requests. "Every test uses a fake"
// is a claim about the tests that exist today; the moment somebody writes one
// that constructs a client without `SetBaseURLs`, it reaches api.github.com
// with the operator's token, and the only evidence would be a PR that got
// approved by a test run.
//
// So `TestMain` replaces `http.DefaultTransport` with one that REFUSES any host
// that is not loopback. Every `http.Client` in this package's tests — including
// the one `NewClient` builds when handed nil — uses `DefaultTransport` unless
// it sets its own, so a stray request fails loudly with the message below
// rather than succeeding quietly.
//
// ⚠ BE HONEST ABOUT THE SCOPE. This covers HTTP that goes through
// `http.DefaultTransport`, which is every request this package can make. It
// does NOT cover a test that builds its own `&http.Transport{}` (none does, and
// `TestNoTestBuildsItsOwnTransport` says so), and it says nothing about
// subprocesses. It is one lock; the other is that no test holds a real token.

type loopbackOnlyTransport struct{ inner http.RoundTripper }

func (t loopbackOnlyTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	host := req.URL.Hostname()
	if isLoopbackHost(host) {
		return t.inner.RoundTrip(req)
	}
	return nil, fmt.Errorf(
		"BLOCKED: a test tried to reach %q over %s. No test in this module may "+
			"talk to a real host — this phase can approve and merge pull requests. "+
			"Point the client at an httptest server with SetBaseURLs",
		req.URL.Host, req.URL.Scheme)
}

func isLoopbackHost(host string) bool {
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func TestMain(m *testing.M) {
	http.DefaultTransport = loopbackOnlyTransport{inner: http.DefaultTransport}
	os.Exit(m.Run())
}

// 🔴 VALIDATE THE INSTRUMENT BEFORE READING ITS VERDICT — BOTH DIRECTIONS.
//
// A transport that blocked EVERYTHING would make every test in this package
// fail loudly, so that direction is self-announcing. The dangerous one is a
// transport that blocks NOTHING: it would let a real call through while the
// file above reads as an airtight guarantee. So this feeds it a real GitHub URL
// and watches it refuse, and feeds it a loopback server and watches it answer.
func TestTheNoNetworkGuardBlocksGitHubAndAllowsLoopback(t *testing.T) {
	// NEGATIVE CONTROL — the case it MUST refuse.
	//
	// ⚠ THE URL IS NEVER DIALLED. The transport rejects before `inner` is
	// reached, which is the whole point: this test must not depend on the box
	// having, or not having, a network.
	_, err := http.Get("https://api.github.com/user")
	if err == nil {
		t.Fatal("the guard let a request to api.github.com THROUGH — every " +
			"claim in this file that no test reaches GitHub is false")
	}
	if !strings.Contains(err.Error(), "BLOCKED") {
		t.Fatalf("the request failed for some other reason, so this proves "+
			"nothing about the guard: %v", err)
	}

	// POSITIVE CONTROL — the case it MUST allow, or the whole suite is failing
	// for a reason nobody would attribute to this file.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "ok")
	}))
	defer srv.Close()
	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatalf("the guard blocked LOOPBACK: %v", err)
	}
	resp.Body.Close()
}

// 🔴 THE LOCK ONLY HOLDS IF NOBODY BUILDS THEIR OWN TRANSPORT. `DefaultTransport`
// is what `NewClient` uses when handed a plain `&http.Client{}`; a test that set
// `Transport:` would route around the guard above and the guard would keep
// passing. This reads the package's own test sources and says so.
//
// ⚠ IT IS A TRIPWIRE, NOT A PROOF — it sees the spelling `http.Transport{`, so a
// transport obtained some other way is invisible to it. It is cheap and it
// catches the one shape anybody would actually write.
func TestNoTestBuildsItsOwnTransport(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	scanned := 0
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), "_test.go") {
			continue
		}
		body, err := os.ReadFile(e.Name())
		if err != nil {
			t.Fatal(err)
		}
		scanned++
		if e.Name() == "nonet_test.go" {
			continue // this file legitimately talks about transports
		}
		if strings.Contains(string(body), "http.Transport{") {
			t.Errorf("%s builds its own http.Transport, which routes around the "+
				"loopback-only guard in nonet_test.go", e.Name())
		}
	}
	// POSITIVE CONTROL on the scan itself: a walk that read no files would
	// report no violations and read as a clean bill of health.
	if scanned < 2 {
		t.Fatalf("the scan read %d test files — a broken glob, not a clean result", scanned)
	}
	t.Logf("scanned %d test files for a hand-built transport", scanned)
}
