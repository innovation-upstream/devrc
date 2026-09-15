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
	fileCur   int
	diffCur   int

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
		a.clampCursors()
		if m.Snap.Kind == ghapi.KindIssue {
			// 🔴 THE ISSUE CARD IS TERMINAL. No comment posting, no labels, no
			// close/reopen, no navigation to related issues. §4 draws that line
			// explicitly, and the card exists only because `mention-open.py`
			// structurally cannot know whether `#N` is an issue or a PR — it
			// builds `/pull/{id}` for everything and lets github.com redirect.
			// There is nothing to fetch a diff for.
			a.setBody(m.Snap.Body)
			return a, nil
		}
		a.relayout()
		// The three metadata panels are usable NOW; the diff is a second read
		// because GraphQL cannot return patch text.
		return a, []Intent{FetchDiff{Owner: a.Owner, Name: a.Name, Num: a.Num}}

	case DiffLoaded:
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
		return a, []Intent{FetchPR{Owner: a.Owner, Name: a.Name, Num: a.Num}}

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
			n := len(a.Snap.Files)
			before := a.fileCur
			a.moveIn(act, &a.fileCur, n)
			if a.fileCur != before && a.Diff != nil {
				// 🔴 CROSS-PANEL EFFECTS GO THROUGH THE ROOT, NOT THROUGH A
				// POINTER BETWEEN SUB-MODELS (§3.2). Selecting a file moves the
				// diff cursor; the Files panel does not hold the Diff panel.
				if start := a.Diff.FileStart(a.fileCur); start >= 0 {
					a.diffCur = start
					a.syncDiffViewport()
				}
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
func (a *App) syncFileCursorFromDiff() {
	if a.Diff == nil {
		return
	}
	if f := a.Diff.FileAt(a.diffCur); f >= 0 {
		a.fileCur = f
	}
}

func (a *App) clampCursors() {
	if a.Snap == nil {
		return
	}
	a.commitCur = clamp(a.commitCur, 0, max(0, len(a.Snap.Commits)-1))
	a.fileCur = clamp(a.fileCur, 0, max(0, len(a.Snap.Files)-1))
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

// Init fires the one GraphQL read.
//
// ✅ `Init() tea.Cmd` IS UNCHANGED IN v2. It churned during the beta and
// reverted; several secondary sources still say otherwise and they are wrong.
func (a App) Init() tea.Cmd {
	if a.runner == nil {
		return nil
	}
	return Run(FetchPR{Owner: a.Owner, Name: a.Name, Num: a.Num}, a.runner)
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
