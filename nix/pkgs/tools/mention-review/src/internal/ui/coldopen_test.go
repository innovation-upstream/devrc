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

// 🔴 THE COLD OPEN, AND THE STATE CLASS IT CREATED.
//
// The two reads used to run in series: `Init` asked for the panels, and the
// diff was asked for only from `Step`'s `PRLoaded` arm. `PRLoaded` therefore
// ALWAYS arrived first, and every other test in this package was written inside
// that guarantee. Now both reads leave together, so `DiffLoaded` CAN ARRIVE
// FIRST — a reachability class nothing in this suite had ever built.
//
// This file is that class: both orders, each crossed with a failure on either
// leg, plus the ISSUE case where the diff read is expected to fail and its
// failure is not the operator's business.
//
// ⚠ EVERY TEST HERE NAMES THE ORDER IT DRIVES. "It works" is a claim about one
// interleaving; the pair is the claim worth making.

// --- the two orders, as one helper -------------------------------------------

// sized is a fresh App that has been through the startup the runtime performs.
//
// 🔴 IT SENDS A `tea.WindowSizeMsg` RATHER THAN ASSIGNING `Width`/`Height`.
// That message is what calls `relayout`, which is what SIZES THE VIEWPORTS —
// and the diff body and the error card body are both viewports. Assigning the
// fields leaves them at their zero size, so a test built that way renders a
// frame with correct borders and an EMPTY interior, and then asserts the
// interior is empty for reasons that have nothing to do with the code under
// test. Bubble Tea sends this before any command's result can arrive, so this
// is the state every load message actually lands in.
func sized(t *testing.T) App {
	t.Helper()
	a := New(fxOwner, fxName, fxNum)
	a.SetMergeMethod(fxMergeMethod)
	a, _ = a.Step(tea.WindowSizeMsg{Width: 140, Height: 40})
	if a.vp.Height() < 3 {
		t.Fatalf("the diff viewport is %d rows after a resize — this fixture "+
			"would render an empty pane whatever the code did", a.vp.Height())
	}
	return a
}

// deliver applies the two load messages in the given order to a fresh App.
//
// 🔴 THE ORDER IS A PARAMETER, NOT TWO COPIES OF THE TEST. A duplicated body is
// how one order silently stops being exercised.
func deliver(t *testing.T, diffFirst bool, pr PRLoaded, diff DiffLoaded) App {
	t.Helper()
	a := sized(t)
	if diffFirst {
		a, _ = a.Step(diff)
		a, _ = a.Step(pr)
		return a
	}
	a, _ = a.Step(pr)
	a, _ = a.Step(diff)
	return a
}

// bothOrders names the two runs so a failure says which one broke.
var bothOrders = []struct {
	name      string
	diffFirst bool
}{
	{"panel-first (the only order that used to exist)", false},
	{"diff-first (new, and only reachable since the reads went parallel)", true},
}

// 🔴 A DIFF THAT ARRIVES BEFORE THE PANELS IS KEPT, NOT DROPPED.
//
// This is the defect the parallel `Init` could most easily have shipped: the
// `DiffLoaded` arm runs against `Snap == nil`, and anything in it that reached
// through the snapshot would have discarded the diff or panicked. The
// assertions are on the FILE COUNT and the rendered PATH, not on `Diff != nil`,
// because a non-nil empty diff would satisfy the weaker claim.
func TestADiffThatArrivesBeforeThePanelsSurvives(t *testing.T) {
	for _, o := range bothOrders {
		got := deliver(t, o.diffFirst,
			PRLoaded{Snap: fixturePR()}, DiffLoaded{Diff: fixtureDiff(t)})

		if got.Diff == nil {
			t.Fatalf("%s: the diff never reached the model", o.name)
		}
		if len(got.Diff.Files) != 2 {
			t.Errorf("%s: the diff carries %d files, want 2",
				o.name, len(got.Diff.Files))
		}
		if got.Load != LoadReady {
			t.Errorf("%s: Load = %v, want LoadReady", o.name, got.Load)
		}
		if got.Err != nil {
			t.Errorf("%s: Err = %v on a screen where both reads succeeded",
				o.name, got.Err)
		}
		screen := stripANSI(got.render())
		for _, want := range []string{"pkg/handler.go", "pkg/widget.go", fxViewer} {
			if !strings.Contains(screen, want) {
				t.Errorf("%s: the screen is missing %q\n%s", o.name, want, screen)
			}
		}
	}
}

// 🔴 THE TWO ORDERS REACH THE SAME SCREEN, BYTE FOR BYTE.
//
// A per-field comparison would pass while some derived thing — the file tree,
// the viewport's content, the diff cursor — differed in a way only the frame
// shows. This asserts the WHOLE normalised string, which is the strongest claim
// available and the one a reordering bug cannot walk around by settling into a
// state that merely looks equivalent.
//
// ⚠ IT IS A RELATIONSHIP, NOT A GOLDEN FRAME. Nothing here pins what the screen
// SAYS; it pins that the two paths agree. The content assertions live in the
// test above.
func TestBothArrivalOrdersReachTheSameScreen(t *testing.T) {
	panelFirst := deliver(t, false, PRLoaded{Snap: fixturePR()}, DiffLoaded{Diff: fixtureDiff(t)})
	diffFirst := deliver(t, true, PRLoaded{Snap: fixturePR()}, DiffLoaded{Diff: fixtureDiff(t)})

	a, b := stripANSI(panelFirst.render()), stripANSI(diffFirst.render())
	if a != b {
		t.Errorf("the two arrival orders render DIFFERENT screens.\n"+
			"--- panel first ---\n%s\n--- diff first ---\n%s", a, b)
	}
	// 🔴 POSITIVE CONTROL ON THE COMPARISON ITSELF. Two empty strings are also
	// equal, and a `render()` that returned "" would make this test pass while
	// observing nothing.
	if !strings.Contains(a, "pkg/handler.go") {
		t.Fatal("the compared screens do not contain the diff — this comparison " +
			"is between two frames that were never populated")
	}
}

// --- the ISSUE case ----------------------------------------------------------

// 🔴 AN ISSUE'S SPECULATIVE DIFF FAILURE IS AN EXPECTED RESULT, NOT AN ERROR.
//
// `mention-open.py` cannot know whether `#N` is an issue or a pull request — it
// builds `/pull/{id}` for everything and lets github.com redirect — so the
// parallel cold open fires a diff read at every issue the operator opens, and
// `/pulls/{n}` 404s for one. That 404 must not reach the screen in ANY form: not
// an error card, not a DIFF UNAVAILABLE panel, not a notice.
//
// ⚠ BOTH ORDERS ARE LOAD-BEARING HERE AND THEY ARE CAUGHT BY DIFFERENT CODE.
// Panel-first is caught by `diffResultIsMoot`, which has already seen the ISSUE
// snapshot. Diff-first is caught by the `PRLoaded` issue arm, which clears the
// `a.Err` the speculative read left behind before anything could know this was
// an issue. A test that drove one order would leave the other's mechanism
// unexercised — and each has its own mutant in the battery.
func TestOpeningAnIssueNeverShowsADiffError(t *testing.T) {
	const detail = "no pull request found for this reference"
	diffErr := DiffLoaded{Err: &ghapi.APIError{State: ghapi.AuthNotFound, Detail: detail}}

	for _, o := range bothOrders {
		got := deliver(t, o.diffFirst, PRLoaded{Snap: fixtureIssue()}, diffErr)

		screen := stripANSI(got.render())
		for _, forbidden := range []string{"DIFF UNAVAILABLE", detail, "FAILED"} {
			if strings.Contains(screen, forbidden) {
				t.Errorf("%s: an ISSUE open put %q on screen — the diff read was "+
					"speculative and its failure is expected\n%s",
					o.name, forbidden, screen)
			}
		}
		if got.Notice() != "" {
			t.Errorf("%s: an ISSUE open raised the notice %q",
				o.name, got.Notice())
		}
		if got.Err != nil {
			t.Errorf("%s: Err = %v after an ISSUE open — the field means "+
				"\"why this screen is degraded\", and an issue card is not degraded",
				o.name, got.Err)
		}
		if got.Load != LoadReady {
			t.Errorf("%s: Load = %v — an issue is a SUCCESSFUL load",
				o.name, got.Load)
		}
		// The screen is the issue card, so the zeros above are about
		// suppression rather than about a frame that renders nothing.
		if !strings.Contains(screen, "ISSUE") {
			t.Errorf("%s: the issue card is not on screen at all\n%s", o.name, screen)
		}
	}
}

// 🔴 THE POSITIVE CONTROL FOR THE SUPPRESSION ABOVE, AND IT IS SEPARATE ON
// PURPOSE. Every assertion in `TestOpeningAnIssueNeverShowsADiffError` is
// satisfied by a program that can never render a diff error at all. This drives
// the SAME message into a screen that has been told it is a PULL REQUEST and
// asserts the error IS reported.
func TestADiffFailureOnAPullRequestIsStillReported(t *testing.T) {
	const detail = "the patch endpoint reset the connection"
	diffErr := DiffLoaded{Err: &ghapi.APIError{State: ghapi.AuthOther, Detail: detail}}

	for _, o := range bothOrders {
		got := deliver(t, o.diffFirst, PRLoaded{Snap: fixturePR()}, diffErr)
		screen := stripANSI(got.render())
		if !strings.Contains(screen, "DIFF UNAVAILABLE") {
			t.Errorf("%s: a real diff failure on a pull request is not on screen\n%s",
				o.name, screen)
		}
		if !strings.Contains(screen, detail) {
			t.Errorf("%s: the failure's own words are missing\n%s", o.name, screen)
		}
		// 🔴 AND THE REST OF THE SCREEN SURVIVED IT. A diff failure is not a
		// page failure.
		if got.Load != LoadReady || got.Snap == nil {
			t.Errorf("%s: a diff failure took the page down: Load=%v Snap=%v",
				o.name, got.Load, got.Snap)
		}
	}
}

// 🔴 WHILE THE PANEL QUERY IS STILL OUT, A DIFF FAILURE SAYS NOTHING.
//
// This is the frame between the two replies, which only exists because the
// reads are parallel. At that instant a 404 from the diff leg and "this is an
// issue" are the same observation, so the panel must not pick one.
//
// ⚠ THIS IS NOT THE SAME CLAIM AS `TestAPageFailureKeepsItsOwnErrorOverADiff
// Failure`, AND THE TWO ARE DELIBERATELY BOTH HERE. They were reviewed as
// possible duplicates and they are not: this one drives `LoadLoading` with NO
// page error and guards `diffBody`'s `Load == LoadReady` condition; that one
// drives `LoadFailed` WITH a page error and guards `diffResultIsMoot`'s
// `LoadFailed` arm. The states are disjoint and so are the guards — which is
// shown rather than asserted: in the mutation battery M4 (drop `diffBody`'s
// condition) is killed by THIS test and not by that one, and M1 (drop the
// `LoadFailed` arm) is killed by that one and not by this. Delete either and a
// mutant survives.
func TestADiffFailureIsSilentUntilThePanelQueryAnswers(t *testing.T) {
	const detail = "no pull request found for this reference"
	a, _ := sized(t).Step(DiffLoaded{Err: &ghapi.APIError{State: ghapi.AuthNotFound, Detail: detail}})

	screen := stripANSI(a.render())
	if strings.Contains(screen, "DIFF UNAVAILABLE") || strings.Contains(screen, detail) {
		t.Errorf("a diff failure was reported before the panel query answered — "+
			"at this instant it is indistinguishable from an ISSUE\n%s", screen)
	}
	if !strings.Contains(screen, "LOADING DIFF") {
		t.Errorf("the Diff panel does not say it is still waiting\n%s", screen)
	}
	// 🔴 THE SAME APP, ONE MESSAGE LATER, DOES REPORT IT. Without this the
	// assertion above is satisfied by a panel that can never report anything.
	ready, _ := a.Step(PRLoaded{Snap: fixturePR()})
	if !strings.Contains(stripANSI(ready.render()), detail) {
		t.Fatal("the failure is STILL silent once the panel query said PULL " +
			"REQUEST — the suppression above is unconditional, not scoped")
	}
}

// --- the page itself failing -------------------------------------------------

// 🔴 A DIFF RESULT MUST NOT OVERWRITE THE PAGE'S OWN ERROR, IN EITHER ORDER.
//
// `errorCardTitle` reads `a.Err` while the card's BODY was built from the
// `PRLoaded` error. Letting a diff failure through would put a title about the
// diff over a body about the pull request — a card whose two halves describe
// different failures, which is worse than either alone. Both legs failing at
// once is the ordinary case for a dead token or a lost network, so this is not
// an exotic interleaving.
//
// ⚠ ITS SIBLING IS `TestADiffFailureIsSilentUntilThePanelQueryAnswers`, WHICH IS
// A DIFFERENT CLAIM. That one is `LoadLoading` with no page error; this one is
// `LoadFailed` with one. See its header for the mutation evidence that neither
// subsumes the other.
//
// ⚠ THE TWO ORDERS ARE CAUGHT BY DIFFERENT CODE AND ARE NOT EQUALLY NEW.
// Panel-first needs `diffResultIsMoot`'s `LoadFailed` arm and is RED without it.
// Diff-first is an INVARIANT GUARD: `Step`'s `PRLoaded` error arm overwrites
// `a.Err` unconditionally, which was already true before this change — but that
// order was unreachable before the reads went parallel, so nothing had ever
// asserted it. Both are here; only the first is regression coverage.
func TestAPageFailureKeepsItsOwnErrorOverADiffFailure(t *testing.T) {
	// ⚠ `AuthNotFound` IS CHOSEN BECAUSE ITS CARD BODY PRINTS `Detail`. Several
	// states print canned prose instead, and an assertion on the detail text
	// under one of those would fail for a reason that has nothing to do with
	// which leg's error won.
	pageErr := &ghapi.APIError{State: ghapi.AuthNotFound, Detail: "the page leg's own words"}
	diffErr := &ghapi.APIError{State: ghapi.AuthRateLimited, Detail: "the diff leg's own words"}

	for _, o := range bothOrders {
		got := deliver(t, o.diffFirst, PRLoaded{Err: pageErr}, DiffLoaded{Err: diffErr})

		if got.Load != LoadFailed {
			t.Fatalf("%s: Load = %v, want LoadFailed", o.name, got.Load)
		}
		screen := stripANSI(got.render())
		// The card carries the PAGE's state word and the PAGE's words.
		if !strings.Contains(screen, ghapi.AuthNotFound.Word()) {
			t.Errorf("%s: the error card does not carry the PAGE's state word %q\n%s",
				o.name, ghapi.AuthNotFound.Word(), screen)
		}
		if !strings.Contains(screen, "the page leg's own words") {
			t.Errorf("%s: the error card does not carry the PAGE's message\n%s",
				o.name, screen)
		}
		// And NOT the diff leg's.
		if strings.Contains(screen, ghapi.AuthRateLimited.Word()) {
			t.Errorf("%s: the diff leg's state word %q reached the error card, which "+
				"was built from the PAGE's failure\n%s",
				o.name, ghapi.AuthRateLimited.Word(), screen)
		}
		if strings.Contains(screen, "the diff leg's own words") {
			t.Errorf("%s: the diff leg's message reached the error card\n%s",
				o.name, screen)
		}
	}
}

// 🔴 THE MIRROR CASE: THE PAGE FAILS AND THE DIFF SUCCEEDS.
//
// Now reachable in either order, and a success is the result most likely to be
// applied to a screen that has no room for it. The error card must still own
// the frame.
func TestAPageFailureOwnsTheScreenEvenWhenTheDiffSucceeded(t *testing.T) {
	pageErr := &ghapi.APIError{State: ghapi.AuthRejected, Detail: "the page leg refused"}

	for _, o := range bothOrders {
		got := deliver(t, o.diffFirst,
			PRLoaded{Err: pageErr}, DiffLoaded{Diff: fixtureDiff(t)})

		if got.Load != LoadFailed {
			t.Errorf("%s: Load = %v, want LoadFailed", o.name, got.Load)
		}
		screen := stripANSI(got.render())
		if !strings.Contains(screen, ghapi.AuthRejected.Word()) {
			t.Errorf("%s: the error card is not on screen\n%s", o.name, screen)
		}
		if strings.Contains(screen, "pkg/handler.go") {
			t.Errorf("%s: a diff was rendered over a page that failed to load\n%s",
				o.name, screen)
		}
	}
}

// --- the pairing rule --------------------------------------------------------

// 🔴 EVERY `FetchPR` TRAVELS WITH A `FetchDiff`, WHEREVER IT IS EMITTED.
//
// The serialisation this PR removed lived in ONE place, and the fix put the
// pair in one place — but three sites ask for a read (`Init`, the `r` retry,
// and the re-read after a successful write) and nothing in the type system
// stops a fourth from spelling `FetchPR` alone. That fourth site would
// reintroduce the sequential open, or a screen whose Diff panel never updates,
// with every existing test still green.
//
// So this is a RELATIONSHIP guard: it walks the same keyboard and message
// sweeps the registry tests use and asserts the two intents are never
// separated. It fails whether a site drops the diff read OR gains a lone
// `FetchPR`.
func TestEveryFetchPRTravelsWithAFetchDiff(t *testing.T) {
	check := func(origin string, intents []Intent) (sawPR bool) {
		pr, diff := 0, 0
		for _, i := range intents {
			switch i.(type) {
			case FetchPR:
				pr++
			case FetchDiff:
				diff++
			}
		}
		if pr > 0 && diff != pr {
			t.Errorf("%s emitted %d FetchPR and %d FetchDiff — the two reads of "+
				"one open must travel together, or the cold open is sequential "+
				"again at this site: %v", origin, pr, diff, intents)
		}
		return pr > 0
	}

	sites := 0
	// (a) the cold open itself.
	if check("ReadIntents()", New(fxOwner, fxName, fxNum).ReadIntents()) {
		sites++
	}
	// (b) every binding of every mode in every reachable state.
	for origin, intents := range emittedByKeyboard(t, reachableStates(t)) {
		if check(origin, intents) {
			sites++
		}
	}
	// (c) the message-driven half — the re-read no key reaches.
	a := ready(t)
	for name, msg := range map[string]tea.Msg{
		"WriteDone-ok":     WriteDone{Verb: "PostComment"},
		"WriteDone-failed": WriteDone{Verb: "MergePR", Err: &ghapi.APIError{State: ghapi.AuthRejected}},
		"PRLoaded-pr":      PRLoaded{Snap: fixturePR()},
		"PRLoaded-issue":   PRLoaded{Snap: fixtureIssue()},
		"DiffLoaded-ok":    DiffLoaded{Diff: fixtureDiff(t)},
	} {
		_, intents := a.Step(msg)
		if check(name, intents) {
			sites++
		}
	}

	// 🔴 POSITIVE CONTROL. "No violation" over zero observed `FetchPR`s is the
	// silent zero: a Step wired to nothing passes every branch above. Three is
	// the number of sites that ask for a read today, and the literal is here so
	// that losing one is a failure rather than a quieter pass.
	if sites < 3 {
		t.Fatalf("the sweep found %d sites emitting FetchPR, want at least 3 "+
			"(the cold open, the `r` retry, and the re-read after a successful "+
			"write) — this guard observed almost nothing", sites)
	}
	t.Logf("checked %d FetchPR-emitting sites", sites)
}

// --- the parallelism itself --------------------------------------------------

// 🔴 `Init` HANDS THE RUNTIME A BATCH, NOT ONE COMMAND.
//
// `tea.Batch`'s contract is "concurrently with no ordering guarantees";
// `tea.Sequence` would run the same two commands one after the other and buy
// nothing. This is the cheap structural half — the behavioural half is the
// overlap test below, which is what actually proves the runtime honours it.
func TestInitHandsTheRuntimeBothReadsAtOnce(t *testing.T) {
	a := New(fxOwner, fxName, fxNum)
	a.SetRunner(stubRunner{})

	cmd := a.Init()
	if cmd == nil {
		t.Fatal("Init returned no command at all")
	}
	batch, ok := cmd().(tea.BatchMsg)
	if !ok {
		t.Fatalf("Init's command yielded %T, want tea.BatchMsg — a single command "+
			"means the two reads are still in series", cmd())
	}
	if len(batch) != 2 {
		t.Errorf("the batch carries %d commands, want 2 (the panel query and the "+
			"diff read)", len(batch))
	}

	// ⚠ AND NIL RUNNER STILL MEANS NO COMMAND. Every pure test in this package
	// relies on that: an App with no runner cannot perform an effect.
	if got := New(fxOwner, fxName, fxNum).Init(); got != nil {
		t.Error("an App with no runner returned a command from Init")
	}
}

// overlapRunner records the WALL-CLOCK INTERVAL of each read.
//
// 🔴 INTERVALS, NOT A RENDEZVOUS. A rendezvous on "has the other one started"
// is satisfied in the SERIAL case too — by the time the diff read begins, the
// panel read has already started (and finished). Overlapping intervals is the
// claim that distinguishes the two, and it cannot be satisfied by a sequential
// implementation however the messages are ordered: the serial diff read cannot
// begin until the panel read has returned.
type overlapRunner struct {
	stubRunner
	mu                sync.Mutex
	prStart, prEnd    time.Time
	difStart, difEnd  time.Time
	prCalls, difCalls int
}

// overlapHold is how long each fake read occupies. It must be long enough that
// two intervals genuinely overlap when run together and that a serial pair's do
// not touch — and it is paid twice per run, so it is small.
const overlapHold = 150 * time.Millisecond

func (r *overlapRunner) FetchPR(_ context.Context, _, _ string, _ int) (*ghapi.Snapshot, error) {
	r.mu.Lock()
	r.prCalls++
	if r.prCalls == 1 {
		r.prStart = time.Now()
	}
	first := r.prCalls == 1
	r.mu.Unlock()
	if !first {
		return fixturePR(), nil
	}
	time.Sleep(overlapHold)
	r.mu.Lock()
	r.prEnd = time.Now()
	r.mu.Unlock()
	return fixturePR(), nil
}

func (r *overlapRunner) FetchDiff(_ context.Context, _, _ string, _ int) (*udiff.Diff, error) {
	r.mu.Lock()
	r.difCalls++
	if r.difCalls == 1 {
		r.difStart = time.Now()
	}
	first := r.difCalls == 1
	r.mu.Unlock()
	if !first {
		return &udiff.Diff{}, nil
	}
	time.Sleep(overlapHold)
	r.mu.Lock()
	r.difEnd = time.Now()
	r.mu.Unlock()
	return &udiff.Diff{}, nil
}

func (r *overlapRunner) snapshot() (prS, prE, dS, dE time.Time, prN, dN int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.prStart, r.prEnd, r.difStart, r.difEnd, r.prCalls, r.difCalls
}

// 🔴 THE TWO COLD-OPEN READS ARE IN FLIGHT AT THE SAME TIME.
//
// This is the whole PR, asserted through the REAL Bubble Tea event loop rather
// than against the library's documentation. It went red on the code this change
// replaced: there, the diff read was emitted from `Step`'s `PRLoaded` arm, so
// it could not begin until the panel read had returned and the two intervals
// were strictly disjoint.
//
// ⚠ IT CANNOT PASS BY TIMING LUCK. A sequential implementation's intervals are
// ordered by causation, not by scheduling — the second read's message does not
// exist until the first has completed — so load on this host can make this test
// slower but cannot make a serial pair overlap.
func TestTheTwoColdOpenReadsAreInFlightTogether(t *testing.T) {
	run := &overlapRunner{}

	app := New(fxOwner, fxName, fxNum)
	app.SetRunner(run)
	app.SetMergeMethod(fxMergeMethod)

	inR, inW := io.Pipe()
	defer inW.Close()

	p := tea.NewProgram(app,
		tea.WithInput(inR),
		tea.WithOutput(io.Discard),
		tea.WithoutRenderer(),
		tea.WithoutSignalHandler(),
		tea.WithWindowSize(140, 40),
	)
	errc := make(chan error, 1)
	go func() { _, err := p.Run(); errc <- err }()

	deadline := time.Now().Add(15 * time.Second)
	var prS, prE, dS, dE time.Time
	for {
		var prN, dN int
		prS, prE, dS, dE, prN, dN = run.snapshot()
		if prN >= 1 && dN >= 1 && !prE.IsZero() && !dE.IsZero() {
			break
		}
		if time.Now().After(deadline) {
			p.Send(keyPress("q"))
			t.Fatalf("the two reads did not both complete: pr=%d diff=%d "+
				"(a diff read that never happened is the sequential shape, not a "+
				"slow one)", prN, dN)
		}
		time.Sleep(5 * time.Millisecond)
	}
	p.Send(keyPress("q"))
	select {
	case err := <-errc:
		if err != nil {
			t.Fatalf("program error: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the program did not exit after `q`")
	}

	// Two intervals overlap iff each starts before the other ends.
	overlapped := prS.Before(dE) && dS.Before(prE)
	if !overlapped {
		t.Errorf("the two cold-open reads did NOT overlap — they ran in series.\n"+
			"  panel query: %v .. %v\n  diff read:   %v .. %v\n"+
			"  gap:         %v",
			prS, prE, dS, dE, dS.Sub(prE))
	}
	// 🔴 AND THE HOLD WAS REAL. Both intervals must be at least as long as the
	// sleep each read performs, or "overlap" is a statement about two instants
	// that never occupied any time and the test proves nothing.
	if prE.Sub(prS) < overlapHold || dE.Sub(dS) < overlapHold {
		t.Fatalf("a read did not occupy the interval it was supposed to: "+
			"panel %v, diff %v, hold %v", prE.Sub(prS), dE.Sub(dS), overlapHold)
	}
	t.Logf("panel query %v, diff read %v, overlap %v",
		prE.Sub(prS), dE.Sub(dS), minTime(prE, dE).Sub(maxTime(prS, dS)))
}

func minTime(a, b time.Time) time.Time {
	if a.Before(b) {
		return a
	}
	return b
}

func maxTime(a, b time.Time) time.Time {
	if a.After(b) {
		return a
	}
	return b
}

// --- the skeleton -------------------------------------------------------------

// 🔴 THE CHROME IS ON SCREEN BEFORE ANY READ RETURNS.
//
// A loading App used to render one full-width card reading "LOADING —
// owner/repo#N", so a cold open drew a card, discarded it, and drew a four-panel
// layout in its place. Now the first frame IS the layout. The assertion is on
// all four panel titles by their literal words, because the numbers in them are
// what `tab` navigates by and a skeleton missing one is a layout that will still
// reflow.
//
// ⚠ THIS IS NOT A CLAIM THAT THE FIRST FRAME GOT FASTER. It did not: the card
// was already painted before any network call. What changed is what it shows.
func TestTheSkeletonPaintsTheChromeBeforeAnythingLoads(t *testing.T) {
	a := sized(t)
	screen := stripANSI(a.render())

	for _, want := range []string{"1 Overview", "2 Commits", "3 Files", "4 Diff"} {
		if !strings.Contains(screen, want) {
			t.Errorf("the skeleton is missing the panel %q\n%s", want, screen)
		}
	}
	// It names the reference it is loading — which comes from argv, so it is
	// known before a byte has left this machine. Losing that was the one thing
	// the card said that the panels had to keep saying.
	if !strings.Contains(screen, "#1559") {
		t.Errorf("the skeleton does not name the reference being opened\n%s", screen)
	}
	// ⚠ THE OWNER, NOT THE WHOLE `owner/name`. The Overview box is 34 columns,
	// so `gardenersguild/trowelcast` is TRUNCATED there — asserting the full
	// spelling would be a claim about the panel width, not about the skeleton.
	if !strings.Contains(screen, fxOwner) {
		t.Errorf("the skeleton does not name the repository\n%s", screen)
	}
	if !strings.Contains(screen, LoadLoading.Word().Word) {
		t.Errorf("the skeleton does not say it is %s\n%s",
			LoadLoading.Word().Word, screen)
	}
	// 🔴 AND THE FOOTER — the generated help is part of the chrome, and it is
	// the half that tells a first-time operator how to leave.
	if !strings.Contains(screen, "quit") {
		t.Errorf("the skeleton has no footer help\n%s", screen)
	}
}

// 🔴 A PANEL THAT HAS NOT LOADED MUST NOT CLAIM THE LIST IS EMPTY.
//
// "NO COMMITS" and "LOADING COMMITS" are different claims, and until the
// skeleton existed the difference was invisible: a loading App drew a card, so
// the `Snap == nil` arm of these bodies was never on screen. Now it is, and
// printing the empty-case word would be a panel asserting a fact it does not
// have.
func TestALoadingPanelDoesNotClaimTheListIsEmpty(t *testing.T) {
	a := sized(t)
	screen := stripANSI(a.render())

	for _, forbidden := range []string{"NO COMMITS", "NO FILES"} {
		if strings.Contains(screen, forbidden) {
			t.Errorf("a skeleton that has loaded nothing says %q — that is a claim "+
				"about the pull request, and nothing here knows it\n%s",
				forbidden, screen)
		}
	}
	for _, want := range []string{"LOADING COMMITS", "LOADING FILES", "LOADING DIFF"} {
		if !strings.Contains(screen, want) {
			t.Errorf("the skeleton does not say %q\n%s", want, screen)
		}
	}

	// 🔴 THE POSITIVE CONTROL, AND IT IS THE HALF THAT MAKES THE ZEROS ABOVE
	// MEAN ANYTHING: a pull request that really HAS no commits and no files
	// still says so. Without it, deleting both words entirely would pass.
	empty := fixturePR()
	empty.Commits = nil
	empty.Files = nil
	loaded, _ := sized(t).Step(PRLoaded{Snap: empty})
	loadedScreen := stripANSI(loaded.render())
	for _, want := range []string{"NO COMMITS", "NO FILES"} {
		if !strings.Contains(loadedScreen, want) {
			t.Errorf("a LOADED pull request with an empty list does not say %q — "+
				"the word was not made conditional, it was deleted\n%s",
				want, loadedScreen)
		}
	}
}

// 🔴 THE DIFF PANEL RENDERS AS SOON AS THE DIFF ARRIVES, WITHOUT WAITING FOR
// THE PANEL QUERY.
//
// This is the second half of the latency win and it only works because the
// skeleton and the parallel reads landed together: the frame between the two
// replies is a real layout, so a diff that wins the race is readable at
// t_rest rather than at max(t_graphql, t_rest).
func TestTheDiffIsReadableBeforeThePanelQueryAnswers(t *testing.T) {
	a, _ := sized(t).Step(DiffLoaded{Diff: fixtureDiff(t)})

	screen := stripANSI(a.render())
	if a.Load != LoadLoading {
		t.Fatalf("Load = %v — this test is about the frame BEFORE the panel "+
			"query answers", a.Load)
	}
	// The diff's own content, and the file path in the Diff panel's title —
	// which is the marker the latency probe reads for `t_diff_readable`.
	for _, want := range []string{"pkg/handler.go", "ctx.refresh()"} {
		if !strings.Contains(screen, want) {
			t.Errorf("the diff is not readable before the panel query answered: "+
				"missing %q\n%s", want, screen)
		}
	}
	// And the left column still says it is waiting, rather than lying about
	// what it does not have.
	if !strings.Contains(screen, "LOADING COMMITS") {
		t.Errorf("the metadata panels do not say they are still loading\n%s", screen)
	}
}
