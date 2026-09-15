package ui

import (
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// 🔴 NO TEST IN THIS PACKAGE MAY REACH THE REAL GITHUB API.
//
// This package is where the write verbs live, so it is the package where a
// stray live call would approve or merge somebody's pull request. There are
// THREE independent locks, and none of them is "every test uses a fake today":
//
//  1. `App.runner` is nil in every pure test. `Step` never touches it, so a
//     test that forgot to set one cannot perform an effect — it gets intents,
//     which are data.
//  2. The one end-to-end test installs `fakeRunner`, which records calls into a
//     slice. `LiveRunner` is constructed by `main` and by nothing else in the
//     test binary, which `TestNoTestConstructsTheLiveRunner` below asserts.
//  3. `http.DefaultTransport` is replaced here by one that REFUSES any host
//     that is not loopback, with a control below proving it refuses
//     api.github.com and allows an httptest server.

type loopbackOnlyTransport struct{ inner http.RoundTripper }

func (t loopbackOnlyTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	h := req.URL.Hostname()
	if h == "localhost" {
		return t.inner.RoundTrip(req)
	}
	if ip := net.ParseIP(h); ip != nil && ip.IsLoopback() {
		return t.inner.RoundTrip(req)
	}
	return nil, fmt.Errorf(
		"BLOCKED: a test tried to reach %q. This package can approve and merge "+
			"pull requests; no test may talk to a real host", req.URL.Host)
}

func TestMain(m *testing.M) {
	http.DefaultTransport = loopbackOnlyTransport{inner: http.DefaultTransport}
	os.Exit(m.Run())
}

// 🔴 VALIDATE THE INSTRUMENT BEFORE READING ITS VERDICT. A transport that
// blocked nothing would leave the comment above reading as a guarantee while
// providing none, and no other test in this package would notice.
func TestTheNoNetworkGuardBlocksGitHubAndAllowsLoopback(t *testing.T) {
	_, err := http.Get("https://api.github.com/user")
	if err == nil {
		t.Fatal("the guard let a request to api.github.com THROUGH")
	}
	if !strings.Contains(err.Error(), "BLOCKED") {
		t.Fatalf("the request failed for a different reason, so this proves "+
			"nothing about the guard: %v", err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer srv.Close()
	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatalf("the guard blocked LOOPBACK, which would fail the suite for a "+
			"reason nobody would attribute to this file: %v", err)
	}
	resp.Body.Close()
}

// 🔴 LOCK 2, ASSERTED. `LiveRunner` is the only Runner that performs real
// effects, and `xdg-open` is not blocked by an HTTP transport — a test that
// constructed one could open a browser window on the operator's screen as well
// as reach GitHub.
//
// ⚠ A TRIPWIRE ON THE SPELLING, and it says so. It cannot see a LiveRunner
// obtained through a variable of interface type; it catches the one shape
// anybody would write.
func TestNoTestConstructsTheLiveRunner(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	scanned := 0
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), "_test.go") || e.Name() == "nonet_test.go" {
			continue
		}
		body, err := os.ReadFile(e.Name())
		if err != nil {
			t.Fatal(err)
		}
		scanned++
		if strings.Contains(string(body), "LiveRunner{") {
			t.Errorf("%s constructs a LiveRunner — that performs REAL effects: "+
				"GitHub writes and an xdg-open on the operator's screen", e.Name())
		}
	}
	// POSITIVE CONTROL on the scan: a walk that read nothing reports no
	// violations and reads as a clean bill of health.
	if scanned < 3 {
		t.Fatalf("the scan read %d test files — a broken glob, not a clean result", scanned)
	}
	// And the control that the matcher CAN match: the spelling it looks for
	// does occur in the production source it is protecting.
	prod, err := os.ReadFile("run.go")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(prod), "LiveRunner") {
		t.Fatal("run.go no longer spells LiveRunner — this scan is looking for " +
			"a string that no longer names anything")
	}
	t.Logf("scanned %d test files for a LiveRunner construction", scanned)
}
