package ui

import (
	"context"
	"io"
	"strings"
	"sync"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// 🔴 LAYER 4 — EXACTLY ONE END-TO-END TEST.
//
// It does only what the layers above structurally cannot: prove the WIRING.
// Fake effects -> a real `tea.Program` -> scripted keys -> assertions on the
// MODEL'S FINAL STATE, never on a frame. Its value is catching "the panels
// never got wired to the fetched data", which every isolated test passes. Its
// cost is being the flakiest test in the suite, so there is ONE.
//
// 🔴 `tea.WithoutRenderer()` — a headless program, deliberately chosen over
// `x/exp/teatest/v2`. teatest has NO semver tag (pseudo-versions only) and
// lives under `exp/` with "no backwards compatibility guarantees", which is a
// poor fit for a vendorHash-pinned Nix build; its `Output()` returns the raw
// byte stream of every frame the renderer emitted rather than a screen; and
// Bubble Tea v2's capability handshake injects environment-dependent bytes into
// that stream. It is the tool for golden frames, and those are rejected.
//
// 🔴 IT OPENS NO WINDOW AND TOUCHES NO TERMINAL. Input is a pipe, output is
// discarded, and there is no renderer at all.

// fakeRunner records what was asked for and answers from fixtures.
type fakeRunner struct {
	mu       sync.Mutex
	prCalls  int
	difCalls int
	opened   []string
	gotOwner string
	gotName  string
	gotNum   int
	diffErr  error
}

func (f *fakeRunner) FetchPR(_ context.Context, owner, name string, num int) (*ghapi.Snapshot, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.prCalls++
	f.gotOwner, f.gotName, f.gotNum = owner, name, num
	return fixturePR(), nil
}

func (f *fakeRunner) FetchDiff(_ context.Context, _, _ string, _ int) (*udiff.Diff, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.difCalls++
	if f.diffErr != nil {
		return nil, f.diffErr
	}
	d, err := udiff.Parse([]udiff.FileInput{
		{Path: "pkg/handler.go", ChangeType: "MODIFIED", Additions: 9, Deletions: 1,
			Patch: "@@ -12,1 +12,2 @@ func handle(req *Request) error {\n ctx := build(req)\n+\tctx.refresh()\n"},
		{Path: "pkg/widget.go", ChangeType: "ADDED", Additions: 3,
			Patch: "@@ -0,0 +1,3 @@\n+package widget\n+\n+const Name = \"widget\"\n"},
	})
	return d, err
}

func (f *fakeRunner) OpenBrowser(url string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.opened = append(f.opened, url)
	return nil
}

func (f *fakeRunner) counts() (pr, dif int, opened []string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.prCalls, f.difCalls, append([]string(nil), f.opened...)
}

func TestEndToEndTheProgramFetchesWiresAndQuits(t *testing.T) {
	fake := &fakeRunner{}

	app := New(fxOwner, fxName, fxNum)
	app.SetRunner(fake)

	inR, inW := io.Pipe()
	defer inW.Close()

	p := tea.NewProgram(app,
		tea.WithInput(inR),
		tea.WithOutput(io.Discard),
		tea.WithoutRenderer(),
		tea.WithoutSignalHandler(),
		tea.WithWindowSize(140, 40),
	)

	done := make(chan tea.Model, 1)
	errc := make(chan error, 1)
	go func() {
		final, err := p.Run()
		errc <- err
		done <- final
	}()

	// Wait for BOTH reads to land, rather than sleeping a fixed interval.
	deadline := time.Now().Add(10 * time.Second)
	for {
		pr, dif, _ := fake.counts()
		if pr >= 1 && dif >= 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("the program never completed its reads: pr=%d diff=%d", pr, dif)
		}
		time.Sleep(5 * time.Millisecond)
	}

	// Script some keys through the REAL event loop.
	p.Send(keyPress("tab"))
	p.Send(keyPress("]"))
	p.Send(keyPress("o"))
	time.Sleep(50 * time.Millisecond)
	p.Send(keyPress("q"))

	select {
	case err := <-errc:
		if err != nil {
			t.Fatalf("program error: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the program did not exit after `q`")
	}
	final := <-done

	got, ok := final.(App)
	if !ok {
		t.Fatalf("final model is %T, want App", final)
	}

	// --- the wiring assertions, on STATE -------------------------------------

	pr, dif, opened := fake.counts()

	// 🔴 EXACTLY ONE GRAPHQL READ. This is the Phase-0 kill criterion, held at
	// the level where a retry loop or a duplicated Init would break it.
	if pr != 1 {
		t.Errorf("FetchPR was called %d times, want exactly 1", pr)
	}
	if dif != 1 {
		t.Errorf("FetchDiff was called %d times, want exactly 1", dif)
	}
	// The argv reached the client unmangled — the whole point of the seam.
	if fake.gotOwner != fxOwner || fake.gotName != fxName || fake.gotNum != fxNum {
		t.Errorf("the client was asked for %s/%s#%d, want %s/%s#%d",
			fake.gotOwner, fake.gotName, fake.gotNum, fxOwner, fxName, fxNum)
	}

	// 🔴 THE PANELS GOT WIRED TO THE FETCHED DATA. This is the defect every
	// isolated test passes: each component works, and nothing ever built the
	// combined state.
	if got.Snap == nil {
		t.Fatal("the fetched snapshot never reached the model")
	}
	if got.Snap.ViewerLogin != "a-reviewer" {
		t.Errorf("ViewerLogin = %q", got.Snap.ViewerLogin)
	}
	if got.Diff == nil {
		t.Fatal("the fetched diff never reached the model")
	}
	if len(got.Diff.Files) != 2 {
		t.Errorf("the diff carries %d files, want 2", len(got.Diff.Files))
	}
	if got.Load != LoadReady {
		t.Errorf("Load = %v, want LoadReady", got.Load)
	}

	// The scripted keys actually took effect through the real loop.
	if got.Focus == FocusDefault {
		t.Errorf("`tab` did not move focus off %v", FocusDefault)
	}
	if len(opened) != 1 || !strings.HasSuffix(opened[0], "/pull/1559") {
		t.Errorf("`o` opened %v, want exactly one PR URL", opened)
	}
	if !got.Quitting {
		t.Error("the final model does not record the quit")
	}

	// And the screen it would draw is populated — one rendering assertion, on
	// CONTENT rather than on bytes, because a program that wired everything and
	// then rendered an empty frame is still broken.
	screen := stripANSI(got.render())
	for _, want := range []string{"pkg/handler.go", "a-reviewer", "3 FAILING"} {
		if !strings.Contains(screen, want) {
			t.Errorf("the final screen is missing %q", want)
		}
	}
}
