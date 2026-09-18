package ui

import (
	"charm.land/bubbles/v2/help"
	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/viewport"
	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// Panel identifies a focusable pane.
type Panel int

const (
	PanelOverview Panel = iota
	PanelCommits
	PanelFiles
	PanelDiff
	panelCount
)

// Title is the WORD in the panel's border, numbered so `tab` has a visible
// order rather than a remembered one.
func (p Panel) Title() string {
	switch p {
	case PanelOverview:
		return "1 Overview"
	case PanelCommits:
		return "2 Commits"
	case PanelFiles:
		return "3 Files"
	}
	return "4 Diff"
}

// --- messages ---------------------------------------------------------------

// PRLoaded carries the one GraphQL read's result.
type PRLoaded struct {
	Snap *ghapi.Snapshot
	Err  error
}

// DiffLoaded carries the REST diff's result.
type DiffLoaded struct {
	Diff *udiff.Diff
	Err  error
}

// WriteDone carries the outcome of ONE write (§3.7's five verbs).
//
// 🔴 A WRITE REPORTS BACK IN WORDS AND THEN RE-READS THE PR. A merge that
// succeeded leaves every panel on screen asserting the PR is still OPEN, which
// is a screen that lies about the thing the operator just did. So a successful
// write emits `FetchPR` — the ONE place in this program where an intent is
// produced by something other than a keypress, which is why `intents_test.go`
// walks messages as well as keys.
type WriteDone struct {
	// Verb is the intent's registry name, so the notice can say which verb
	// finished without the UI re-deriving it from the error text.
	Verb string
	Err  error
}

// --- the model --------------------------------------------------------------

// App is the root model. It owns everything more than one panel reads (§3.2).
type App struct {
	Owner string
	Name  string
	Num   int

	Load  LoadState
	Snap  *ghapi.Snapshot
	Diff  *udiff.Diff
	Err   error
	Focus Panel

	// MergeMethod is what `internal/cfg` resolved at startup.
	//
	// 🔴 EMPTY MEANS UNKNOWN, AND UNKNOWN MEANS REFUSE. `New` does not fill it
	// in: an App that was never told the method must not merge with a plausible
	// one. This mirrors `nvim-octo`'s `MERGE_METHOD_UNKNOWN` sentinel, which
	// exists because a mutation replacing it with `"squash"` survived a fully
	// green suite there — nothing reached the arm. Here the arm is reached from
	// the keyboard, by a test.
	MergeMethod string

	Width, Height int

	// Per-panel cursors. A sub-model owns state only IT reads.
	commitCur int
	diffCur   int

	// fileRowCur is the Files panel's cursor, and it indexes `fileRows` — the
	// VISIBLE TREE ROWS — not `Snap.Files`.
	//
	// 🔴 IT REPLACED A `fileCur` THAT INDEXED TWO DIFFERENT LISTS. That one
	// integer was read as an index into `Snap.Files` (to render) and into
	// `Diff.Files` (to jump the diff), which worked only while the two were
	// positionally parallel. A tree interleaves directory rows with file rows
	// and groups files by directory, so "the Nth row" is no longer "the Nth
	// file" in either list. Everything downstream derives from this cursor: the
	// selected ROW (`currentRow`), and from it the selected FILE BY PATH.
	fileRowCur int

	// fileTree is the built directory tree, and fileRows its flattened visible
	// rows.
	//
	// 🔴 THEY ARE STATE BECAUSE THE CURSOR INDEXES THEM, NOT AS A SPEED
	// OPTIMISATION. `fileRowCur` above is an index into `fileRows`, and
	// `moveIn` (called from `move`) reads `len(a.fileRows)` to bound it — not
	// on a render path, so a list that existed only inside `filesBody` would
	// leave the cursor indexing nothing between frames. That is a correctness
	// requirement, and it holds however cheap the rebuild is.
	//
	// ⚠ `moveIn` IS THE ONLY LIVE BOUND ON THIS CURSOR. This comment named
	// `clampCursors` as a second one and that was FALSE: `clampCursors` has a
	// single call site, in `Step`'s `PRLoaded`, immediately after
	// `rebuildFileTree` has already put the cursor on a row that exists — so
	// its `fileRowCur` line cannot move it, and deleting that line leaves the
	// package green. The line is kept as a bound on a state nothing currently
	// produces; it is not evidence that this cursor is checked anywhere else.
	//
	// ⚠ `panels.go`'s per-frame-styling measurement is about 10,000 DIFF LINES;
	// it is prior art here, not this field's reason.
	fileTree *treeNode
	fileRows []FileRow

	// collapsedDirs holds the paths of the directories the operator has closed.
	//
	// 🔴 EMPTY MEANS FULLY EXPANDED, AND THAT IS THE STATE A FRESHLY BUILT App
	// IS IN. No PR may get a worse first screen than it had before the tree
	// existed: every changed file is visible the moment the panel appears.
	//
	// 🔴 "ON EVERY OPEN" IS WHAT THIS USED TO SAY AND IT WAS TOO WIDE.
	// `PRLoaded` also arrives mid-session — a successful write re-reads the PR
	// — and resetting this set there discarded the operator's collapse state on
	// the most common write flow there is. `rebuildFileTree` carries it across
	// a re-read instead, pruned to the directories the new tree still has; see
	// its header. The first open is still fully expanded because an App that
	// has never loaded one has nothing in here.
	//
	// 🔴 COPY-ON-WRITE. `App` travels BY VALUE through `Step`, so a map mutated
	// in place would also change the App the caller is still holding — and every
	// test in this package compares a `next` against the `a` it came from.
	// `setCollapsed` and `revealFile` replace the map; nothing writes through it.
	collapsedDirs map[string]bool

	// 🔴 THERE IS DELIBERATELY NO PATH -> `Diff.Files` INDEX HERE. One existed,
	// and it was a cache of `Diff` that nothing kept honest: every assignment to
	// `Diff` owed it a rebuild, two branches of `Step` paid that by hand, and no
	// test or type could see a third that did not. `selectRow` reads `Diff`
	// directly instead — see its header.

	vp       viewport.Model
	body     viewport.Model // the issue card / error card body
	help     help.Model
	showFull bool

	// mode is which key table is live. See keys.go — the modes are DISJOINT,
	// which is why `q` cannot quit out from under a pending confirmation.
	mode Mode

	// compose is the body being typed, when mode == ModeCompose.
	compose composeState

	// pending is the built-but-unsent write intent, when mode == ModeConfirm.
	//
	// 🔴 THE INTENT IS BUILT BEFORE THE PROMPT IS SHOWN, AND THE PROMPT IS
	// BUILT FROM THAT INTENT. The value the operator reads and the value the
	// runner sends are then the same object, not two derivations that could
	// drift.
	pending *pendingWrite

	// notice is the last outcome, in words: a refusal, an abort, or a write
	// result. Never a colour, never an icon.
	notice string

	// runner is the effect surface. 🔴 NOT A PACKAGE-LEVEL GLOBAL: a global
	// would make `Update` depend on process state, and a test that forgot to
	// set it would silently exercise the LIVE network. Nil is safe — `Step` is
	// pure and never touches it, so every pure test constructs an App without
	// one and cannot reach the network even by mistake.
	runner Runner

	// Quitting is set by the pure Step; the impure Update turns it into
	// tea.Quit. 🔴 Step never returns a tea.Cmd, not even that one.
	Quitting bool
}

// Repo renders "owner/repo".
func (a App) Repo() string { return a.Owner + "/" + a.Name }

// New builds a loading App.
func New(owner, name string, num int) App {
	h := help.New()
	h.ShowAll = false
	a := App{
		Owner:  owner,
		Name:   name,
		Num:    num,
		Load:   LoadLoading,
		Focus:  PanelDiff, // ⚠ see FocusDefault
		Width:  100,
		Height: 30,
		vp:     viewport.New(),
		body:   viewport.New(),
		help:   h,
	}
	// 🔴 SoftWrap = false IS A PERFORMANCE DECISION, MEASURED, NOT A STYLE ONE.
	// With soft wrap ON, `viewport.calculateLine` loops EVERY line calling
	// `ansi.StringWidth`, and `View()`, `TotalLineCount()` and `maxYOffset()`
	// each trigger their own full pass. MEASURED on this host at width 92:
	//
	//        lines   SoftWrap=false   SoftWrap=true
	//         1000          318 us         2,450 us
	//         4000          347 us         8,457 us
	//        10000          342 us        21,554 us
	//
	// i.e. false is FLAT in buffer size and true is linear — 63x slower at
	// 10,000 lines, and 21.5 ms per View() blows a 60 fps frame budget on its
	// own. Horizontal scrolling is the trade, and it is the right one.
	a.vp.SoftWrap = false
	a.body.SoftWrap = true // a prose body is short; wrapping is what it wants
	return a
}

// FocusDefault is the panel the cursor starts in.
//
// ⚠ THE DIFF, NOT THE FILES PANEL. MEASURED over 300 recent PRs: the median PR
// in this repo touches ONE file, and four in a second repository of the
// operator's. For a large fraction of openings the Files panel has nothing to
// choose, and landing there wastes a keystroke on every single review. The
// panels still exist for the p90 (6 files and 19 respectively); they just
// should not be where the cursor starts.
const FocusDefault = PanelDiff

// --- the pure step ----------------------------------------------------------

// Step is the PURE half of Update. It returns the next App and the intents it
// wants performed. It performs no I/O and constructs no closure that does.
//
// 🔴 EVERYTHING INTERESTING HAPPENS HERE, AND 90% OF THE TESTS DRIVE THIS.
func (a App) Step(msg tea.Msg) (App, []Intent) {
	switch m := msg.(type) {

	case tea.WindowSizeMsg:
		a.Width, a.Height = m.Width, m.Height
		a.relayout()
		return a, nil

	case PRLoaded:
		if m.Err != nil {
			a.Load = LoadFailed
			a.Err = m.Err
			a.setBody(errorCardBody(a.Repo(), a.Num, m.Err))
			return a, nil
		}
		a.Load = LoadReady
		a.Snap = m.Snap
		// 🔴 THE TREE IS BUILT HERE, WHERE THE FILE LIST CHANGES — not in
		// `filesBody`, which runs every frame. See tree.go's header.
		a.rebuildFileTree()
		a.clampCursors()
		if m.Snap.Kind == ghapi.KindIssue {
			// 🔴 THE ISSUE CARD IS TERMINAL. No comment posting, no labels, no
			// close/reopen, no navigation to related issues. §4 draws that line
			// explicitly, and the card exists only because `mention-open.py`
			// structurally cannot know whether `#N` is an issue or a PR — it
			// builds `/pull/{id}` for everything and lets github.com redirect.
			// There is nothing to fetch a diff for.
			//
			// 🔴 AND THE SPECULATIVE DIFF READ IS DISCARDED HERE, BECAUSE THE
			// OTHER ARRIVAL ORDER EXISTS. `ReadIntents` asks for the diff before
			// anything knows this is an issue, so that read's 404 can land
			// BEFORE this message — in which case `diffResultIsMoot` never saw
			// it and `a.Err` already holds it. Clearing both fields here is what
			// makes "opening an issue shows no diff error" true in EITHER order
			// rather than in the one a test happened to script.
			a.Diff = nil
			a.Err = nil
			a.setBody(m.Snap.Body)
			return a, nil
		}
		a.relayout()
		// 🔴 NO `FetchDiff` HERE ANY MORE — IT WAS ALREADY ASKED FOR. This arm
		// used to emit it, which is what serialised a cold open into
		// t_graphql + t_rest. `ReadIntents` emits the pair together; re-emitting
		// it here would buy a second, duplicate REST read and nothing else.
		return a, nil

	case DiffLoaded:
		// 🔴 THE DIFF READ IS SPECULATIVE NOW, SO ITS RESULT CAN BE MOOT.
		// See `diffResultIsMoot` for which two screens have nowhere to put an
		// answer, and why the in-flight request is DISCARDED rather than
		// cancelled.
		if a.diffResultIsMoot() {
			return a, nil
		}
		if m.Err != nil {
			// 🔴 A DIFF FAILURE IS NOT A PAGE FAILURE. The metadata panels are
			// already on screen and still true; replacing them with an error
			// card would throw away a working screen. The Diff panel says so
			// and everything else keeps working.
			a.Diff = nil
			a.Err = m.Err
			return a, nil
		}
		a.Diff = m.Diff
		a.diffCur = 0
		a.rebuildDiffContent()
		a.syncDiffViewport()
		return a, nil

	case WriteDone:
		return a.stepWriteDone(m)

	case tea.KeyPressMsg:
		return a.stepKey(m)
	}
	return a, nil
}

// diffResultIsMoot reports whether a `DiffLoaded` may be dropped on the floor.
//
// 🔴 IT IS A STATE TEST, NOT A "DID WE SPECULATE" FLAG. A boolean would have to
// be set correctly on every path that starts a read and cleared on every path
// that consumes one; this asks the only question that matters — is there a Diff
// panel on screen for an answer to go into — and both of its arms are reachable
// in EITHER arrival order.
//
// ⚠ DISCARDED, NOT CANCELLED, AND THE REASON IS NOT "CANCELLATION IS HARD".
// Cancelling would mean a `context.CancelFunc` living on `App`, and `App`
// travels BY VALUE through a pure `Step` that every test in this package
// compares against the value it came from. The saving it would buy is the
// remainder of one small in-flight response — by the time `PRLoaded` says ISSUE
// the request has already been sent. That trade is not worth a live handle in a
// value-typed model, so the result is read and dropped. `run.go` stays the only
// place a context exists.
func (a App) diffResultIsMoot() bool {
	// The PAGE failed, so the error card owns the screen and `a.Err` is the
	// page's own error. A diff result here would overwrite that: `errorCardTitle`
	// reads `a.Err` while the card BODY was built from the `PRLoaded` error, so
	// the operator would read a title about the diff over a body about the pull
	// request.
	if a.Load == LoadFailed {
		return true
	}
	// An ISSUE has no diff, and §4's card is terminal. The speculative read
	// against `/pulls/{n}` 404s for one, and that 404 is an EXPECTED result, not
	// something the operator did or can act on.
	return a.Snap != nil && a.Snap.Kind != ghapi.KindPullRequest
}

// stepKey walks the dispatch table FOR THE CURRENT MODE.
//
// 🔴 THERE IS NO FALL-THROUGH TO ANOTHER MODE'S TABLE. A key that this mode
// does not bind does nothing, which is what makes "while a confirmation is
// pending, `q` does not quit" a structural fact rather than an ordering
// accident.
func (a App) stepKey(k tea.KeyPressMsg) (App, []Intent) {
	for _, b := range DispatchFor(a.mode) {
		if !key.Matches(k, b.Binding) {
			continue
		}
		return a.act(b.Action)
	}
	// 🔴 TEXT ENTRY IS THE ONLY THING THAT RUNS AFTER THE TABLE, AND ONLY IN
	// COMPOSE MODE. It is last so a bound chord can never be typed into the
	// buffer instead of acting, and it is mode-scoped so an ordinary `y` in
	// browse mode still means nothing.
	if a.mode == ModeCompose && k.Text != "" {
		return a.composeInsert(k.Text), nil
	}
	return a, nil
}

// act is the handler body for one action. Pure.
func (a App) act(act Action) (App, []Intent) {
	switch act {
	case ActQuit:
		a.Quitting = true
		return a, nil

	case ActFullHelp:
		a.showFull = !a.showFull
		a.help.ShowAll = a.showFull
		a.relayout()
		return a, nil

	case ActBrowser:
		// 🔴 A ZERO-NETWORK-INTENT PATH IS NOT WHAT THIS IS. It emits exactly
		// one intent and the test asserts the value, so the assertion has its
		// own positive control built in.
		url := a.browserURL()
		if url == "" {
			return a, nil
		}
		return a, []Intent{OpenBrowser{URL: url}}

	case ActRetry:
		if a.Load != LoadFailed {
			// ⚠ `r` IS INERT ON A HEALTHY SCREEN, DELIBERATELY. Re-fetching
			// under the operator would move the cursor out from under them.
			return a, nil
		}
		a.Load = LoadLoading
		a.Err = nil
		return a, a.ReadIntents()

	// --- the diff viewport pan (§ `J`/`K`) -----------------------------------
	//
	// 🔴 HANDLED HERE, BEFORE THE FALL-THROUGH TO `move()`, AND THAT PLACEMENT
	// IS THE FEATURE. `move()` switches on `a.Focus`; these two must mean the
	// same thing in all four panels, so they must never reach it.
	case ActScrollDiffDown:
		return a.scrollDiff(+diffScrollStep), nil
	case ActScrollDiffUp:
		return a.scrollDiff(-diffScrollStep), nil

	// --- the Files tree ------------------------------------------------------
	//
	// 🔴 BROWSE MODE ONLY, AND THE FILES PANEL ONLY. `enter`, `left` and `right`
	// are all bound in COMPOSE mode; the tables are disjoint, so there is no
	// collision — but a handler that acted regardless of focus would make `←`
	// jump the Files cursor while the operator is reading the diff.
	case ActTreeExpand:
		return a.treeExpand(), nil
	case ActTreeCollapse:
		return a.treeCollapse(), nil
	case ActTreeToggle:
		return a.treeToggle(), nil

	case ActNextPanel:
		a.Focus = a.nextFocusable(+1)
		a.relayout()
		return a, nil
	case ActPrevPanel:
		a.Focus = a.nextFocusable(-1)
		a.relayout()
		return a, nil

	// --- the write verbs (§3.7) ---------------------------------------------
	case ActComment, ActRequestChanges, ActSubmitReview:
		return a.beginCompose(act), nil
	case ActApprove:
		return a.propose(Approve{Owner: a.Owner, Name: a.Name, Num: a.Num})
	case ActMerge:
		return a.proposeMerge()

	// --- compose mode --------------------------------------------------------
	case ActComposeNewline:
		return a.composeInsert("\n"), nil
	case ActComposeBackspace:
		return a.composeBackspace(), nil
	case ActComposeLeft:
		return a.composeMove(-1), nil
	case ActComposeRight:
		return a.composeMove(+1), nil
	case ActComposeCancel:
		return a.composeCancel(), nil
	case ActComposeSend:
		return a.composeSend()

	// --- confirm mode --------------------------------------------------------
	case ActConfirmYes:
		return a.confirmYes()
	case ActConfirmNo:
		return a.confirmNo(), nil
	}

	// Everything below moves a cursor inside the focused panel.
	return a.move(act), nil
}

// nextFocusable cycles focus, skipping panels that do not exist in the current
// state. 🔴 An issue has no commits, files or diff, so `tab` on an issue card
// must not park the cursor on three empty boxes — it stays on Overview, which
// is the only panel an issue HAS.
func (a App) nextFocusable(dir int) Panel {
	if !a.isPullRequest() {
		return PanelOverview
	}
	n := int(panelCount)
	return Panel(((int(a.Focus)+dir)%n + n) % n)
}

func (a App) isPullRequest() bool {
	return a.Snap != nil && a.Snap.Kind == ghapi.KindPullRequest
}

// diffScrollStep is how far ONE `J`/`K` press pans the diff viewport.
//
// ⚠ THREE LINES, NOT ONE AND NOT A PAGE. One line is too slow to be worth a
// second binding when `j` already exists, and a page is what `C-d`/`C-u`
// already do; three is the step a reader uses to peek past the cursor without
// losing the surrounding context. It is a NAMED constant so a test can pin the
// literal offset and a mutant that changes it has exactly one place to hide.
const diffScrollStep = 3

// scrollDiff pans the DIFF VIEWPORT by n lines — positive is down — and touches
// NOTHING ELSE.
//
// 🔴 IT MUST NOT CALL `syncDiffViewport()`. That function ends in
// `EnsureVisible(diffCur, 0, 0)`, which yanks the view straight back to the
// cursor: calling it here would leave a feature that looks implemented, passes
// a naive "did Step return without error" test, and does literally nothing on
// screen. The style closure `syncDiffViewport` installed is still correct after
// a pan, because the CURSOR did not move — which is the whole point of the
// binding.
//
// 🔴 IT IS NOT ON THE `move()` PATH EITHER. `moveIn` clamps against a cursor
// range and `move`'s `PanelFiles` arm drags `diffCur` to a file start, so a
// viewport pan routed through them would move the cursors it exists to leave
// alone.
//
// ⚠ THE `a.Diff == nil` GUARD IS REACHABLE AND LOAD-BEARING, not a nil-check
// reflex. `DiffLoaded{Err: …}` sets `Diff` to nil WITHOUT rebuilding the
// viewport's content — a state the program enters when a write's follow-up
// re-read succeeds for the PR and fails for the diff — so the viewport still
// holds the previous diff's lines and would happily scroll them.
func (a App) scrollDiff(n int) App {
	if a.Diff == nil {
		return a
	}
	// ⚠ The viewport clamps for us: `ScrollDown`/`ScrollUp` return early at the
	// bottom/top and go through `SetYOffset`, which clamps to `[0, maxYOffset]`.
	// A hand-rolled clamp here would be a second copy of that rule.
	if n >= 0 {
		a.vp.ScrollDown(n)
	} else {
		a.vp.ScrollUp(-n)
	}
	return a
}

// move applies a cursor action to whichever panel has focus.
func (a *App) moveIn(act Action, cur *int, n int) {
	if n == 0 {
		return
	}
	page := max(1, a.panelBodyHeight()/2)
	switch act {
	case ActUp:
		*cur--
	case ActDown:
		*cur++
	case ActPageUp:
		*cur -= page
	case ActPageDown:
		*cur += page
	case ActTop:
		*cur = 0
	case ActBottom:
		*cur = n - 1
	}
	*cur = clamp(*cur, 0, n-1)
}

func (a App) move(act Action) App {
	switch a.Focus {
	case PanelCommits:
		if a.Snap != nil {
			a.moveIn(act, &a.commitCur, len(a.Snap.Commits))
		}
	case PanelFiles:
		if a.Snap != nil {
			// 🔴 THE CURSOR WALKS ROWS, NOT FILES. Directory rows are part of
			// the sequence `j`/`k` steps through, which is what makes a
			// collapsed tree navigable at all.
			before := a.fileRowCur
			a.moveIn(act, &a.fileRowCur, len(a.fileRows))
			if a.fileRowCur != before {
				a.selectRow()
			}
		}
	case PanelDiff:
		if a.Diff != nil {
			switch act {
			case ActNextHunk:
				if i := a.Diff.NextHunk(a.diffCur); i >= 0 {
					a.diffCur = i
				}
			case ActPrevHunk:
				if i := a.Diff.PrevHunk(a.diffCur); i >= 0 {
					a.diffCur = i
				}
			case ActNextFile:
				if f := a.Diff.FileAt(a.diffCur); f >= 0 && f+1 < len(a.Diff.Files) {
					a.diffCur = a.Diff.FileStart(f + 1)
				}
			case ActPrevFile:
				if f := a.Diff.FileAt(a.diffCur); f > 0 {
					a.diffCur = a.Diff.FileStart(f - 1)
				}
			default:
				a.moveIn(act, &a.diffCur, len(a.Diff.Lines))
			}
			a.syncFileCursorFromDiff()
			a.syncDiffViewport()
		}
	case PanelOverview:
		// The body viewport (an issue body, or an error card) scrolls.
		switch act {
		case ActUp:
			a.body.ScrollUp(1)
		case ActDown:
			a.body.ScrollDown(1)
		case ActPageUp:
			a.body.HalfPageUp()
		case ActPageDown:
			a.body.HalfPageDown()
		case ActTop:
			a.body.GotoTop()
		case ActBottom:
			a.body.GotoBottom()
		}
	}
	return a
}

// syncFileCursorFromDiff keeps the Files panel's highlight on whatever file the
// diff cursor is inside — the other half of the one-way cross-panel link.
//
// 🔴 IT REVEALS, IT DOES NOT ONLY HIGHLIGHT. If the diff cursor moves into a
// file inside a COLLAPSED directory, highlighting a row that is not on screen
// would leave the operator reading a diff the Files panel silently disagrees
// with. So the panel opens whatever it has to and the highlighted file is
// ALWAYS visible.
//
// ⚠ THIS IS ALSO WHAT `}`/`{` AND `]`/`[` REACH. Those four stay DIFF-ordered —
// they are Diff-panel keys and "the next file" means the next file in the
// review, not the next row of the tree — but every one of them ends here, so
// each of them now opens the directory it lands in.
func (a *App) syncFileCursorFromDiff() {
	if a.Diff == nil {
		return
	}
	f := a.Diff.FileAt(a.diffCur)
	if f < 0 || f >= len(a.Diff.Files) {
		return
	}
	a.revealFile(a.Diff.Files[f].Path)
}

func (a *App) clampCursors() {
	if a.Snap == nil {
		return
	}
	a.commitCur = clamp(a.commitCur, 0, max(0, len(a.Snap.Commits)-1))
	a.fileRowCur = clamp(a.fileRowCur, 0, max(0, len(a.fileRows)-1))
}

func (a App) browserURL() string {
	if a.Snap != nil && a.Snap.URL != "" {
		return a.Snap.URL
	}
	// 🔴 A URL EVEN WHEN THE FETCH FAILED. §6.1: a 404 card offers `o` because
	// a browser session may have access this token does not, and an OFFLINE
	// card offers it because the browser may reach what we could not. Built
	// from argv, which is always present, rather than from a snapshot that may
	// not be.
	return "https://github.com/" + a.Repo() + "/pull/" + itoa(a.Num)
}

// --- the impure half --------------------------------------------------------

// ReadIntents is the pair of reads ONE open of a reference needs, and it is the
// only place that pair is spelled.
//
// 🔴 THE TWO READS ARE CONCURRENT, AND NOTHING EVER FORCED THEM TO BE
// OTHERWISE. `FetchDiff` needs `Owner`, `Name` and `Num`; all three come from
// argv and are known before a byte has left this machine. The diff request used
// to be emitted from `Step`'s `PRLoaded` arm instead, so a cold open paid
// t_graphql THEN t_rest in series — the project handoff recorded that first
// read as "already at the API floor", and it was not.
//
// MEASURED on this host, one public 4-file / 1,395-line pull request, five
// BASE/AFTER pairs run INTERLEAVED (the host moves faster than one block of
// five), each open driven headlessly in its own tmux socket, poll granularity
// 12–18 ms, every reading recorded with its own matched flag:
//
//	                     in series      concurrent
//	 diff readable        1,206 ms          667 ms   (medians; AFTER won 5 of 5)
//	 first frame            204 ms          230 ms   (process start, not the API)
//
// ⚠ THE SECOND ROW IS NOT A WIN AND IS NOT CLAIMED AS ONE. The first frame was
// already painted before any network call; the skeleton changed what it shows,
// and lays out four boxes where a card used to be, which is what the ~26 ms
// costs.
//
// 🔴 ONE RULE, ONE PLACE. Three sites want "read this reference": `Init`, the
// `r` retry, and the re-read after a successful write. Spelled separately, the
// pair is one forgetful edit away from regrowing the serialisation at a site
// nobody re-measures — so `TestEveryFetchPRTravelsWithAFetchDiff` walks the
// keyboard AND the messages and asserts the two always travel together.
//
// ⚠ IT IS PURE AND RETURNS DATA, like every other intent producer here. `Init`
// below is the only impure part, and all it does is hand these to the runner.
func (a App) ReadIntents() []Intent {
	return []Intent{
		FetchPR{Owner: a.Owner, Name: a.Name, Num: a.Num},
		FetchDiff{Owner: a.Owner, Name: a.Name, Num: a.Num},
	}
}

// Init fires BOTH reads at once.
//
// 🔴 `tea.Batch`, WHOSE CONTRACT IS "concurrently with no ordering guarantees".
// That is the whole change: `tea.Sequence` would run them one after the other
// and buy nothing. The consequence is a new reachable state — `DiffLoaded` can
// now arrive BEFORE `PRLoaded` — which `Step` handles in both orders.
//
// ✅ `Init() tea.Cmd` IS UNCHANGED IN v2. It churned during the beta and
// reverted; several secondary sources still say otherwise and they are wrong.
func (a App) Init() tea.Cmd {
	if a.runner == nil {
		return nil
	}
	return tea.Batch(RunAll(a.ReadIntents(), a.runner)...)
}

// SetRunner installs the effect surface. Called once, at startup, by main —
// and by the one end-to-end test, with a fake.
func (a *App) SetRunner(r Runner) { a.runner = r }

// SetMergeMethod installs the method `internal/cfg` resolved.
//
// 🔴 CALLING IT WITH "" IS MEANINGFUL: it says the config could not be read,
// and the merge key then refuses in words instead of asking. `main` passes ""
// on a config error deliberately rather than exiting — a TUI that vanished
// because a config file was malformed would teach the operator nothing.
func (a *App) SetMergeMethod(m string) { a.MergeMethod = m }

// Update is the thin impure shell over Step. 🔴 IT CONTAINS NO LOGIC — every
// branch lives in Step, where a test can see it.
func (a App) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	next, intents := a.Step(msg)
	if next.Quitting {
		return next, tea.Quit
	}
	if next.runner == nil {
		return next, nil
	}
	return next, tea.Batch(RunAll(intents, next.runner)...)
}

func clamp(v, lo, hi int) int {
	if hi < lo {
		return lo
	}
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}
