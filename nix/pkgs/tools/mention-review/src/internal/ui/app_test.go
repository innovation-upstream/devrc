package ui

import (
	"context"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// 🔴 LAYER 2 — `Step()` DRIVEN OVER MESSAGE SEQUENCES.
//
// Because the command seam made `Step` PURE and made intents DATA, these assert
// on STATE and INTENTS, never on a rendered string. There are no golden frames
// anywhere in this suite: `teatest`'s `Output()` is a byte STREAM of every frame
// the renderer emitted — a transcript of REDRAWS, not a screen — so a golden
// file is coupled to render scheduling, which is not behaviour.
//
// 🔴 EVERY FIXTURE IS SYNTHETIC. This repository is public.
//
// ⚠ THE FIXTURE VALUES ARE PAIRWISE DISTINCT AND DISTINCT FROM EVERY CONSTANT
// THE ASSERTIONS NAME — PR 1559, owner `gardenersguild`, name `trowelcast`,
// 3 commits, 2 files, 7 checks. A fixture that can only ever produce a
// constant's own value cannot see a mutant that hardcodes the literal.

const (
	fxOwner = "gardenersguild"
	fxName  = "trowelcast"
	fxNum   = 1559
	fxRepo  = fxOwner + "/" + fxName

	// 🔴 THE FIXTURE LOGIN AND MERGE METHOD ARE BOTH CHOSEN TO BE UNREACHABLE
	// BY ACCIDENT. §5.5: "the merge method fixture is not the string the
	// default would produce if the config read were deleted." `cfg`'s declared
	// default is `squash`, so the fixture is `rebase` — a mutant that hardcodes
	// the default, or that drops `MergeMethod` and falls back, renders `SQUASH`
	// into a prompt the assertion pins as `REBASE` and dies.
	fxViewer      = "a-reviewer"
	fxMergeMethod = "rebase"
	fxTitle       = "Refresh the stale context before running"

	// The fixture snapshot's mergeability. ⚠ It is the RESOLVED value, so a test
	// that wants the recompute window must set UNKNOWN explicitly rather than
	// inherit it — and the merge-gate assertions use `CONFLICTING`, a third
	// value this fixture never produces.
	fxMergeable = "MERGEABLE"
)

func fixturePR() *ghapi.Snapshot {
	return &ghapi.Snapshot{
		ViewerLogin:      fxViewer,
		Kind:             ghapi.KindPullRequest,
		Repo:             fxRepo,
		Num:              fxNum,
		Title:            fxTitle,
		State:            "OPEN",
		URL:              "https://github.com/" + fxRepo + "/pull/1559",
		Author:           "an-author",
		CreatedAt:        time.Date(2026, 9, 10, 9, 0, 0, 0, time.UTC),
		UpdatedAt:        time.Date(2026, 9, 12, 17, 30, 0, 0, time.UTC),
		BaseRef:          "main",
		HeadRef:          "fix/stale-context",
		Additions:        312,
		Deletions:        40,
		ChangedFiles:     2,
		Mergeable:        fxMergeable,
		MergeStateStatus: "BLOCKED",
		ReviewDecision:   "CHANGES_REQUESTED",
		Commits: []ghapi.Commit{
			{OID: "a1b2c3d4", Abbrev: "a1b2c3d", Headline: "refresh the stale context", Author: "an-author"},
			{OID: "e4f5a6b7", Abbrev: "e4f5a6b", Headline: "address review", Author: "an-author"},
			{OID: "c8d9e0f1", Abbrev: "c8d9e0f", Headline: "add the regression test", Author: "an-author"},
		},
		Files: []ghapi.File{
			{Path: "pkg/handler.go", Additions: 9, Deletions: 1, ChangeType: "MODIFIED"},
			{Path: "pkg/widget.go", Additions: 3, Deletions: 0, ChangeType: "ADDED"},
		},
		Reviews: []ghapi.Review{{Author: "a-reviewer", State: "CHANGES_REQUESTED"}},
		Threads: ghapi.ThreadSummary{Total: 7, Unresolved: 3},
		Checks:  ghapi.CheckSummary{State: "FAILURE", Total: 7, Failing: 3},
	}
}

func fixtureIssue() *ghapi.Snapshot {
	return &ghapi.Snapshot{
		ViewerLogin: "a-reviewer",
		Kind:        ghapi.KindIssue,
		Repo:        fxRepo,
		Num:         1656,
		Title:       "Widget refresh drops the stale context",
		State:       "OPEN",
		URL:         "https://github.com/" + fxRepo + "/issues/1656",
		Author:      "an-author",
		Body:        "The widget keeps a context past its refresh window.\n\nSteps:\n1. build\n2. wait\n3. observe",
	}
}

func fixtureDiff(t *testing.T) *udiff.Diff {
	t.Helper()
	d, err := udiff.Parse([]udiff.FileInput{
		{Path: "pkg/handler.go", ChangeType: "MODIFIED", Additions: 9, Deletions: 1,
			Patch: "@@ -12,1 +12,2 @@ func handle(req *Request) error {\n ctx := build(req)\n+\tctx.refresh()\n"},
		{Path: "pkg/widget.go", ChangeType: "ADDED", Additions: 3,
			Patch: "@@ -0,0 +1,3 @@\n+package widget\n+\n+const Name = \"widget\"\n"},
	})
	if err != nil {
		t.Fatal(err)
	}
	return d
}

func ready(t *testing.T) App {
	t.Helper()
	a := New(fxOwner, fxName, fxNum)
	a.Width, a.Height = 140, 40
	// ⚠ SET EXPLICITLY, because `New` deliberately leaves it EMPTY — an App
	// that was never told the method must refuse to merge rather than guess.
	// A fixture that relied on a default would make that refusal untestable.
	a.SetMergeMethod(fxMergeMethod)
	a, _ = a.Step(PRLoaded{Snap: fixturePR()})
	a, _ = a.Step(DiffLoaded{Diff: fixtureDiff(t)})
	return a
}

// --- the fetch sequence ------------------------------------------------------

// 🔴 EXACTLY ONE GRAPHQL READ, AND THE DIFF IS THE SECOND — because GraphQL
// cannot return patch text. The assertion is on the VALUE of the intent, not
// on its count, so a mutant that emitted the right number of the wrong thing
// does not survive.
func TestLoadingAPullRequestAsksForTheDiffAndNothingElse(t *testing.T) {
	a := New(fxOwner, fxName, fxNum)
	if a.Load != LoadLoading {
		t.Fatalf("a fresh App is in state %v", a.Load)
	}
	next, intents := a.Step(PRLoaded{Snap: fixturePR()})
	if next.Load != LoadReady {
		t.Errorf("Load = %v, want LoadReady", next.Load)
	}
	want := []Intent{FetchDiff{Owner: fxOwner, Name: fxName, Num: fxNum}}
	if !intentsEqual(intents, want) {
		t.Errorf("intents = %v, want %v", intents, want)
	}
}

// 🔴 AN ISSUE ASKS FOR NO DIFF AT ALL. §4: the card is read-only and terminal,
// and there is nothing to review — so a FetchDiff here would be a wasted round
// trip against an endpoint that would 404.
//
// The pair is what makes this evidence: the SAME code path produces a non-empty
// intent list for a pull request (the test above), so "empty" here is a claim
// about the branch rather than about a `Step` wired to nothing.
func TestLoadingAnIssueEmitsZeroIntents(t *testing.T) {
	a := New(fxOwner, fxName, 1656)
	next, intents := a.Step(PRLoaded{Snap: fixtureIssue()})
	if len(intents) != 0 {
		t.Errorf("an issue emitted %v, want none", intents)
	}
	if next.Snap.Kind != ghapi.KindIssue {
		t.Errorf("Kind = %v", next.Snap.Kind)
	}
	if next.Load != LoadReady {
		t.Errorf("Load = %v, want LoadReady — an issue is a successful load", next.Load)
	}
	// The positive control, in the same test.
	_, prIntents := New(fxOwner, fxName, fxNum).Step(PRLoaded{Snap: fixturePR()})
	if len(prIntents) == 0 {
		t.Fatal("the PULL REQUEST path also emitted nothing — the zero above " +
			"is a claim about Step being wired to nothing, not about issues")
	}
}

// 🔴 A DIFF FAILURE IS NOT A PAGE FAILURE. The metadata panels are already on
// screen and still true; replacing them with an error card would throw away a
// working screen.
func TestADiffFailureLeavesTheMetadataPanelsIntact(t *testing.T) {
	a := New(fxOwner, fxName, fxNum)
	a, _ = a.Step(PRLoaded{Snap: fixturePR()})
	next, intents := a.Step(DiffLoaded{Err: &ghapi.APIError{State: ghapi.AuthOther, Detail: "connection reset"}})

	if next.Load != LoadReady {
		t.Errorf("Load = %v, want LoadReady — the PR itself loaded fine", next.Load)
	}
	if next.Snap == nil {
		t.Fatal("the snapshot was discarded by a DIFF failure")
	}
	if next.Diff != nil {
		t.Error("Diff is non-nil after a failure")
	}
	if len(intents) != 0 {
		t.Errorf("a diff failure emitted %v — it must not retry by itself", intents)
	}
	// And the failure is VISIBLE, not swallowed.
	if !strings.Contains(stripANSI(next.render()), "connection reset") {
		t.Error("the diff error is not on screen anywhere")
	}
}

func TestAFetchFailureRendersACardAndKeepsTheWindowOpen(t *testing.T) {
	for _, st := range []ghapi.AuthState{
		ghapi.AuthNoToken, ghapi.AuthRejected, ghapi.AuthNotFound, ghapi.AuthRateLimited,
	} {
		a := New(fxOwner, fxName, fxNum)
		a.Width, a.Height = 140, 40
		next, intents := a.Step(PRLoaded{Err: &ghapi.APIError{State: st, Detail: "detail text"}})
		if next.Load != LoadFailed {
			t.Errorf("%v: Load = %v", st, next.Load)
		}
		if len(intents) != 0 {
			t.Errorf("%v: emitted %v — a failure must not retry by itself", st, intents)
		}
		if next.Quitting {
			t.Errorf("%v: the app QUIT on a failure — a window that flashes and "+
				"vanishes teaches the operator nothing", st)
		}
		// 🔴 THE STATE'S OWN WORD IS ON SCREEN, so NO TOKEN and TOKEN REJECTED
		// are distinguishable by reading, not by exit code.
		screen := stripANSI(next.render())
		if !strings.Contains(screen, st.Word()) {
			t.Errorf("%v: the card does not carry the word %q\n%s", st, st.Word(), screen)
		}
	}
}

// --- keys --------------------------------------------------------------------

func TestQuitSetsQuittingAndEmitsNoIntents(t *testing.T) {
	for _, k := range []string{"q", "esc", "ctrl+c"} {
		next, intents := ready(t).Step(keyPress(k))
		if !next.Quitting {
			t.Errorf("%q did not set Quitting", k)
		}
		if len(intents) != 0 {
			t.Errorf("%q emitted %v", k, intents)
		}
	}
}

// 🔴 `o` EMITS EXACTLY ONE OpenBrowser WITH THE SNAPSHOT'S OWN URL. Asserting
// the VALUE rather than the count is what makes this a real test: a mutant
// that opened the repository root instead would produce the right count.
func TestOpenBrowserUsesTheSnapshotURL(t *testing.T) {
	_, intents := ready(t).Step(keyPress("o"))
	want := []Intent{OpenBrowser{URL: "https://github.com/" + fxRepo + "/pull/1559"}}
	if !intentsEqual(intents, want) {
		t.Errorf("intents = %v, want %v", intents, want)
	}
}

// 🔴 AND `o` WORKS WITH NO SNAPSHOT AT ALL. §6.1: a 404 card offers `o` because
// a browser session may have access this token does not, and an OFFLINE card
// offers it because the browser may reach what we could not. Built from argv,
// which is always present.
func TestOpenBrowserWorksOnAFailureCard(t *testing.T) {
	a := New(fxOwner, fxName, fxNum)
	a, _ = a.Step(PRLoaded{Err: &ghapi.APIError{State: ghapi.AuthNotFound}})
	_, intents := a.Step(keyPress("o"))
	want := []Intent{OpenBrowser{URL: "https://github.com/" + fxRepo + "/pull/1559"}}
	if !intentsEqual(intents, want) {
		t.Errorf("intents = %v, want %v", intents, want)
	}
}

// `r` re-fetches ONLY from a failed state, and it is inert otherwise —
// re-fetching under the operator would move the cursor out from under them.
func TestRetryOnlyFiresFromAFailedState(t *testing.T) {
	a := New(fxOwner, fxName, fxNum)
	a, _ = a.Step(PRLoaded{Err: &ghapi.APIError{State: ghapi.AuthRateLimited}})
	next, intents := a.Step(keyPress("r"))
	want := []Intent{FetchPR{Owner: fxOwner, Name: fxName, Num: fxNum}}
	if !intentsEqual(intents, want) {
		t.Fatalf("intents = %v, want %v", intents, want)
	}
	if next.Load != LoadLoading {
		t.Errorf("Load = %v, want LoadLoading", next.Load)
	}
	if next.Err != nil {
		t.Error("the old error survived the retry")
	}

	// The other half: inert on a healthy screen. Paired with the assertion
	// above, so "zero" here is a claim about the branch.
	_, none := ready(t).Step(keyPress("r"))
	if len(none) != 0 {
		t.Errorf("`r` on a healthy screen emitted %v", none)
	}
}

// --- focus and navigation ----------------------------------------------------

// ⚠ THE DIFF IS THE DEFAULT FOCUS, NOT THE FILES PANEL. Measured: the median PR
// in this repo touches ONE file, so landing on Files wastes a keystroke on
// every single review.
func TestFocusStartsOnTheDiffPanel(t *testing.T) {
	if got := New(fxOwner, fxName, fxNum).Focus; got != FocusDefault {
		t.Errorf("Focus = %v, want %v", got, FocusDefault)
	}
	if FocusDefault != PanelDiff {
		t.Errorf("FocusDefault = %v, want PanelDiff", FocusDefault)
	}
}

func TestTabCyclesAllFourPanelsAndWrapsAround(t *testing.T) {
	a := ready(t)
	seen := []Panel{a.Focus}
	for i := 0; i < int(panelCount); i++ {
		var intents []Intent
		a, intents = a.Step(keyPress("tab"))
		if len(intents) != 0 {
			t.Errorf("tab emitted %v — focus is local", intents)
		}
		seen = append(seen, a.Focus)
	}
	// Four distinct panels, then back to where it started.
	distinct := map[Panel]bool{}
	for _, p := range seen {
		distinct[p] = true
	}
	if len(distinct) != int(panelCount) {
		t.Errorf("tab visited %d distinct panels, want %d: %v", len(distinct), panelCount, seen)
	}
	if seen[0] != seen[len(seen)-1] {
		t.Errorf("tab did not wrap: started at %v, ended at %v", seen[0], seen[len(seen)-1])
	}
}

func TestShiftTabCyclesBackwards(t *testing.T) {
	a := ready(t)
	start := a.Focus
	fwd, _ := a.Step(keyPress("tab"))
	back, _ := fwd.Step(keyPress("shift+tab"))
	if back.Focus != start {
		t.Errorf("tab then S-tab landed on %v, want %v", back.Focus, start)
	}
}

// 🔴 AN ISSUE HAS NO COMMITS, FILES OR DIFF, so `tab` must not park the cursor
// on three empty boxes.
func TestTabOnAnIssueStaysOnOverview(t *testing.T) {
	a := New(fxOwner, fxName, 1656)
	a.Width, a.Height = 140, 40
	a, _ = a.Step(PRLoaded{Snap: fixtureIssue()})
	for i := 0; i < 5; i++ {
		a, _ = a.Step(keyPress("tab"))
		if a.Focus != PanelOverview {
			t.Fatalf("tab %d landed on %v; an issue has only the Overview", i, a.Focus)
		}
	}
}

// Selecting a file moves the diff cursor into that file. 🔴 THE CROSS-PANEL
// EFFECT GOES THROUGH THE ROOT — the Files panel holds no pointer to the Diff
// panel.
func TestSelectingAFileMovesTheDiffCursorIntoIt(t *testing.T) {
	a := ready(t)
	a.Focus = PanelFiles
	if len(a.Snap.Files) < 2 {
		t.Fatal("fixture needs at least two files for this to mean anything")
	}
	// ⚠ TWO PRESSES, BECAUSE ROW 0 IS THE `pkg/` DIRECTORY. The fixture's two
	// files share one directory, so the tree is
	// [0 pkg/, 1 handler.go, 2 widget.go].
	next, _ := pressAll(a, "j", "j")
	if next.fileRowCur != 2 {
		t.Fatalf("fileRowCur = %d, want 2", next.fileRowCur)
	}
	if got := next.SelectedFilePath(); got != "pkg/widget.go" {
		t.Fatalf("selected %q, want pkg/widget.go", got)
	}
	wantStart := next.Diff.FileStart(1)
	if next.diffCur != wantStart {
		t.Errorf("diffCur = %d, want %d (the start of pkg/widget.go)", next.diffCur, wantStart)
	}
	// And the reverse link: moving the diff cursor back into the first file
	// moves the Files highlight with it.
	next.Focus = PanelDiff
	back, _ := next.Step(keyPress("{"))
	if got := back.SelectedFilePath(); got != "pkg/handler.go" {
		t.Errorf("after `{`, the Files panel has %q selected, want pkg/handler.go", got)
	}
	if back.fileRowCur != 1 {
		t.Errorf("after `{`, fileRowCur = %d, want 1", back.fileRowCur)
	}
}

func TestHunkNavigationMovesTheDiffCursorToHunkHeaders(t *testing.T) {
	a := ready(t)
	a.Focus = PanelDiff
	if len(a.Diff.Hunks) < 2 {
		t.Fatal("fixture needs at least two hunks")
	}
	next, intents := a.Step(keyPress("]"))
	if len(intents) != 0 {
		t.Errorf("`]` emitted %v — navigation is local", intents)
	}
	if next.diffCur != a.Diff.Hunks[0].LineIndex {
		t.Errorf("diffCur = %d, want %d", next.diffCur, a.Diff.Hunks[0].LineIndex)
	}
	next2, _ := next.Step(keyPress("]"))
	if next2.diffCur != a.Diff.Hunks[1].LineIndex {
		t.Errorf("second `]` -> %d, want %d", next2.diffCur, a.Diff.Hunks[1].LineIndex)
	}
	// At the last hunk `]` is inert rather than wrapping or going out of range.
	last := next2
	for i := 0; i < 5; i++ {
		last, _ = last.Step(keyPress("]"))
	}
	if last.diffCur >= len(a.Diff.Lines) {
		t.Errorf("diffCur ran past the buffer: %d >= %d", last.diffCur, len(a.Diff.Lines))
	}
	back, _ := next2.Step(keyPress("["))
	if back.diffCur != a.Diff.Hunks[0].LineIndex {
		t.Errorf("`[` -> %d, want %d", back.diffCur, a.Diff.Hunks[0].LineIndex)
	}
}

func TestCursorMovementNeverLeavesTheBuffer(t *testing.T) {
	a := ready(t)
	a.Focus = PanelDiff
	for i := 0; i < len(a.Diff.Lines)+20; i++ {
		a, _ = a.Step(keyPress("j"))
	}
	if a.diffCur != len(a.Diff.Lines)-1 {
		t.Errorf("after running off the bottom, diffCur = %d, want %d",
			a.diffCur, len(a.Diff.Lines)-1)
	}
	for i := 0; i < len(a.Diff.Lines)+20; i++ {
		a, _ = a.Step(keyPress("k"))
	}
	if a.diffCur != 0 {
		t.Errorf("after running off the top, diffCur = %d, want 0", a.diffCur)
	}
}

func TestAnUnboundKeyChangesNothing(t *testing.T) {
	a := ready(t)
	next, intents := a.Step(keyPress("Z"))
	if len(intents) != 0 {
		t.Errorf("an unbound key emitted %v", intents)
	}
	if next.Focus != a.Focus || next.diffCur != a.diffCur || next.Quitting {
		t.Error("an unbound key changed state")
	}
}

// --- the rendered screen -----------------------------------------------------

// 🔴 §10.2 — THE AUTHENTICATED LOGIN IS ON SCREEN. cli/cli#14370: the OS keyring
// is not partitioned by account, so the resolved token can belong to a DIFFERENT
// account than the config's active one, and this host's `hosts.yml` carries two
// github.com users. For a tool that will be able to merge, that is the
// difference between approving as yourself and approving as somebody else.
//
// ⚠ THE FIXTURE LOGIN IS `a-reviewer`, WHICH IS NOT THE AUTHOR AND NOT ANY
// OTHER FIXTURE FIELD — so a mutant that rendered the author, or a constant,
// cannot produce it.
func TestTheViewerLoginIsRenderedInTheOverviewAndTheFooter(t *testing.T) {
	a := ready(t)
	screen := stripANSI(a.render())
	if !strings.Contains(screen, "a-reviewer") {
		t.Fatalf("the authenticated login is NOT on screen:\n%s", screen)
	}
	if !strings.Contains(stripANSI(a.overviewBody(80, 20)), "a-reviewer") {
		t.Error("the Overview panel does not carry the viewer login")
	}
	if !strings.Contains(stripANSI(a.renderFooter()), "a-reviewer") {
		t.Error("the footer does not carry the viewer login")
	}
	// POSITIVE CONTROL for the matcher: the author is a DIFFERENT string and
	// is also on screen, so `Contains` is not matching everything.
	if !strings.Contains(screen, "an-author") {
		t.Error("the author is missing too — the matcher may be broken")
	}
}

func TestTheIssueCardSaysItIsNotAPullRequest(t *testing.T) {
	a := New(fxOwner, fxName, 1656)
	a.Width, a.Height = 140, 40
	a, _ = a.Step(PRLoaded{Snap: fixtureIssue()})
	screen := stripANSI(a.render())

	for _, want := range []string{
		"ISSUE",
		"not a pull request",
		"Widget refresh drops the stale context",
		"an-author",
		"a-reviewer",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("the issue card does not carry %q\n%s", want, screen)
		}
	}
	// 🔴 AND IT IS NOT A PR SCREEN. The four-panel titles must be absent, or
	// the card is rendering over the wrong layout.
	for _, absent := range []string{"2 Commits", "3 Files", "4 Diff"} {
		if strings.Contains(screen, absent) {
			t.Errorf("the issue card rendered the PR panel %q", absent)
		}
	}
}

func TestTheOverviewCarriesEveryStateWord(t *testing.T) {
	a := ready(t)
	body := stripANSI(a.overviewBody(80, 20))
	// Each of these is derived from the fixture BY HAND, not read off the
	// implementation: CHANGES_REQUESTED -> CHANGES, MERGEABLE+BLOCKED ->
	// BLOCKED, 3 failing of 7 -> "3 FAILING", 3 unresolved -> "3 UNRESOLVED".
	for _, want := range []string{"CHANGES", "BLOCKED", "3 FAILING", "3 UNRESOLVED", "OPEN"} {
		if !strings.Contains(body, want) {
			t.Errorf("the Overview does not carry %q\n%s", want, body)
		}
	}
}

func TestAWindowTooSmallSaysSoInWords(t *testing.T) {
	a := ready(t)
	a.Width, a.Height = 20, 5
	screen := stripANSI(a.render())
	if !strings.Contains(screen, "WINDOW TOO SMALL") {
		t.Errorf("a tiny window rendered a mangled frame instead of a word:\n%s", screen)
	}
}

func TestAWindowSizeMessageIsAbsorbedWithoutIntents(t *testing.T) {
	a := ready(t)
	next, intents := a.Step(tea.WindowSizeMsg{Width: 200, Height: 60})
	if len(intents) != 0 {
		t.Errorf("a resize emitted %v", intents)
	}
	if next.Width != 200 || next.Height != 60 {
		t.Errorf("size = %dx%d, want 200x60", next.Width, next.Height)
	}
}

// --- helpers -----------------------------------------------------------------

func intentsEqual(got, want []Intent) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}

// stubRunner satisfies Runner without touching the network.
//
// 🔴 EVERY WRITE METHOD RETURNS WITHOUT DOING ANYTHING, AND NONE OF THEM HAS A
// NETWORK CALL IN IT. This type is the only Runner the non-end-to-end tests can
// see, and `App.runner` is nil in every pure test — so a test cannot reach
// GitHub even by mistake. `nonet_test.go` is the second lock: a transport that
// refuses any non-loopback host, with a control proving it rejects a real
// GitHub URL.
type stubRunner struct{}

func (stubRunner) FetchPR(context.Context, string, string, int) (*ghapi.Snapshot, error) {
	return fixturePR(), nil
}
func (stubRunner) FetchDiff(context.Context, string, string, int) (*udiff.Diff, error) {
	return &udiff.Diff{}, nil
}
func (stubRunner) OpenBrowser(string) error                                       { return nil }
func (stubRunner) PostComment(context.Context, string, string, int, string) error { return nil }
func (stubRunner) SubmitReview(context.Context, string, string, int, string, string) error {
	return nil
}
func (stubRunner) Merge(context.Context, string, string, int, string, string) error { return nil }
