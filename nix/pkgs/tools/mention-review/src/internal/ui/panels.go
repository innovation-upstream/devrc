package ui

import (
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// Layout constants. The left column is fixed; the diff takes the rest.
const (
	leftColWidth = 34
	minWidth     = 60
	minHeight    = 12
)

// View renders the whole screen.
//
// 🔴 v2: View RETURNS A tea.View STRUCT, NOT A STRING — and that struct is also
// where AltScreen, Cursor and MouseMode now live, because `tea.WithAltScreen()`
// and the terminal-feature program options are GONE. Every v1 tutorial opens
// with `tea.WithAltScreen()`; it does not compile here.
func (a App) View() tea.View {
	v := tea.NewView(a.render())
	v.AltScreen = true
	v.BackgroundColor = bg0
	return v
}

func (a App) render() string {
	if a.Width < minWidth || a.Height < minHeight {
		// A window too small to lay out gets a WORD, not a mangled frame.
		return styWarn.Render(fmt.Sprintf(
			"WINDOW TOO SMALL — %dx%d, need at least %dx%d",
			a.Width, a.Height, minWidth, minHeight))
	}
	footer := a.renderFooter()
	bar := a.renderBar()
	bodyH := a.Height - lipgloss.Height(footer)
	if bar != "" {
		bodyH -= lipgloss.Height(bar)
	}

	var body string
	switch {
	case a.Load == LoadFailed:
		body = a.renderCard(bodyH, a.errorCardTitle(), a.body.View())
	case !a.panelsAreOnScreen():
		// 🔴 THE SAME PREDICATE `nextFocusable` READS. Spelled as its own
		// condition here, this arm and the focus rule drifted apart the moment
		// the skeleton arrived. With `LoadFailed` already taken above, this is
		// exactly "a snapshot that is not a pull request" — i.e. the issue card —
		// so `a.Snap` is non-nil whenever this runs.
		body = a.renderIssueCard(bodyH)
	default:
		// 🔴 THE SKELETON AND THE LOADED SCREEN ARE THE SAME FRAME. `LoadLoading`
		// used to get its own full-width card reading "LOADING — owner/repo#N",
		// so a cold open drew a card, threw it away, and drew a four-panel layout
		// in its place. The panel bodies each say what they are waiting for
		// instead — chrome, borders, titles and footer are on screen from the
		// first frame and nothing under the operator's eye moves when the reads
		// land.
		//
		// 🔴 AND IT IS WORTH 187 ms ON THE HEADLINE NUMBER, WHICH IS NOT OBVIOUS
		// AND WAS NEARLY LOST. Without this arm the diff cannot be drawn until
		// `PRLoaded` lands, so a cold open reaches a readable diff at
		// max(t_graphql, t_rest); with it, the diff paints the moment it
		// arrives, so the cost is t_rest alone. That only pays if the REST leg
		// is the FASTER one — and on this host it is, by 189 ms. MEASURED, five
		// interleaved rounds: t_rest median 666 ms against t_graphql 855 ms,
		// REST first in 5 of 5. See `ReadIntents` for the full table.
		//
		// ⚠ THIS DOES NOT MOVE t_first_frame AND IS NOT CLAIMED TO. The card was
		// already painted before any network call, so there was never anything
		// there to win: 277 ms in series, 285 ms concurrent, 287 ms with this
		// arm — no consistent direction across rounds, i.e. a null, not a cost.
		// What it changes is WHAT the first frame shows and that the layout no
		// longer reflows.
		body = a.renderPanels(bodyH)
	}
	if bar != "" {
		return lipgloss.JoinVertical(lipgloss.Left, body, bar, footer)
	}
	return lipgloss.JoinVertical(lipgloss.Left, body, footer)
}

// renderBar is the write surface: the confirmation line, the compose buffer, or
// the last outcome. Empty in the ordinary browsing case.
//
// 🔴 THE CONFIRMATION IS RENDERED AS THE STORED STRING, VERBATIM. It is not
// rebuilt here from the intent, because then the sentence asserted by
// `confirm_test.go` and the sentence on screen would be two derivations that
// could drift — and the whole point of pinning the WHOLE normalised string is
// that what the operator reads is what was pinned.
func (a App) renderBar() string {
	switch a.mode {
	case ModeConfirm:
		if a.pending == nil {
			// A mode with no pending intent is a bug, and it says so rather
			// than rendering an empty bar that looks like an ordinary screen.
			return styBad.Render("CONFIRM — no pending action; this is a bug.")
		}
		return lipgloss.NewStyle().Padding(0, 1).Render(lipgloss.JoinHorizontal(lipgloss.Top,
			ModeWord(ModeConfirm).Render(), styDim.Render("  "), styTitle.Render(a.pending.Prompt)))

	case ModeCompose:
		// ⚠ THE HEADER ALREADY LEADS WITH THE MODE WORD. `ComposeHeader` starts
		// "COMPOSING …", so prefixing `ModeWord(ModeCompose)` here printed it
		// twice — and a duplicated word reads as a rendering fault rather than
		// as emphasis. The confirm arm above does prefix it, because its
		// payload is the prompt and the prompt does not name the mode.
		head := ComposeHeader(a.compose.Verb, a.Repo()+"#"+itoa(a.Num), a.viewerLogin())
		return lipgloss.NewStyle().Padding(0, 1).Render(lipgloss.JoinVertical(lipgloss.Left,
			ModeWord(ModeCompose).Style.Render(head),
			styText.Render(a.composeRender()),
		))
	}
	if a.notice != "" {
		return lipgloss.NewStyle().Padding(0, 1).Render(styWarn.Render(a.notice))
	}
	return ""
}

func (a App) viewerLogin() string {
	if a.Snap == nil || a.Snap.ViewerLogin == "" {
		// 🔴 A WORD, NOT A BLANK. An empty login rendered as nothing reads as
		// "this field is broken"; `UNKNOWN LOGIN` reads as the §10.2 hazard it
		// actually is — and the write gate refuses in that state anyway.
		return "UNKNOWN LOGIN"
	}
	return a.Snap.ViewerLogin
}

// composeBarLines is how many body rows the compose bar shows.
//
// 🔴 A FIXED NUMBER, AND THAT IS WHY THE FRAME CANNOT OVERFLOW. `relayout`
// sizes the diff viewport against `lipgloss.Height(renderBar())`; if the bar
// could grow as the operator typed, the layout computed when compose OPENED
// would be wrong by however many lines they went on to write, and the bottom of
// the frame would push off the terminal. A window over the buffer keeps the
// height constant while still following the cursor.
const composeBarLines = 4

// composeRender draws the buffer with a visible cursor, in exactly
// `composeBarLines` rows.
//
// ⚠ THE CURSOR IS A CHARACTER, NOT A TERMINAL CURSOR POSITION. `tea.View`'s
// `Cursor` field would be the native way, and it is deliberately not used: the
// bar is composed with `lipgloss.JoinVertical`, so its absolute screen
// coordinates are not known here, and a cursor placed at a guessed coordinate
// is worse than a visible marker. `▏` survives colour removal, which is the
// constraint every other state in this program is held to.
func (a App) composeRender() string {
	cur := clamp(a.compose.Cur, 0, len(a.compose.Buf))
	withCursor := string(a.compose.Buf[:cur]) + "▏" + string(a.compose.Buf[cur:])
	lines := strings.Split(withCursor, "\n")

	// The window follows the cursor's LINE, so a long comment scrolls rather
	// than hiding the end the operator is typing at.
	curLine := strings.Count(string(a.compose.Buf[:cur]), "\n")
	start := clamp(curLine-composeBarLines+1, 0, max(0, len(lines)-composeBarLines))
	end := start + composeBarLines
	if end > len(lines) {
		end = len(lines)
	}
	out := append([]string(nil), lines[start:end]...)
	for len(out) < composeBarLines {
		out = append(out, "")
	}
	return strings.Join(out, "\n")
}

// renderFooter renders the GENERATED help.
//
// 🔴 IT IS `help.Model.View(Keys)` AND NOTHING ELSE. There is no string literal
// here listing keys. The footer reads the very same `key.Binding` values
// `Dispatch()` matches on, so the two cannot disagree — and `keys_test.go`
// asserts the two SETS are equal in both directions so that stays true when
// somebody adds a binding.
func (a App) renderFooter() string {
	// 🔴 THE FOOTER RENDERS *THIS MODE'S* HELP. A footer that always showed the
	// browse keys would be a legend that lies the moment a confirmation is
	// pending — the same defect the preceding arc existed to fix, hidden inside
	// a mode. `keys_test.go` asserts the dispatched and helped sets are equal
	// PER MODE, so this cannot be half-true.
	line := a.help.View(modeKeys{a.mode})
	if a.Snap != nil && a.Snap.ViewerLogin != "" {
		// 🔴 THE AUTHENTICATED LOGIN IS ON SCREEN AT ALL TIMES (§10.2).
		// cli/cli#14370: the OS keyring is not partitioned by account, so the
		// resolved token can belong to a DIFFERENT account than the config's
		// active one — and this host's hosts.yml carries two github.com users.
		// It is a curiosity for a read-only tool and the difference between
		// approving as yourself and approving as somebody else for one that can
		// merge. Putting it on screen now means it is already there when the
		// write actions land.
		line = lipgloss.JoinHorizontal(lipgloss.Top,
			line, styDim.Render("  ·  as "), styAccent.Render(a.Snap.ViewerLogin))
	}
	return lipgloss.NewStyle().Padding(0, 1).Render(line)
}

func (a App) renderPanels(h int) string {
	leftW := leftColWidth
	if a.Width < leftColWidth*2 {
		leftW = a.Width / 3
	}
	rightW := a.Width - leftW

	// Three stacked boxes on the left; the tallest gets the remainder.
	ovH := max(7, h/3)
	cmH := max(4, (h-ovH)/2)
	flH := h - ovH - cmH

	left := lipgloss.JoinVertical(lipgloss.Left,
		a.box(PanelOverview, leftW, ovH, a.overviewBody(leftW-4, ovH-2)),
		a.box(PanelCommits, leftW, cmH, a.commitsBody(leftW-4, cmH-2)),
		a.box(PanelFiles, leftW, flH, a.filesBody(leftW-4, flH-2)),
	)
	right := a.box(PanelDiff, rightW, h, a.diffBody())
	return lipgloss.JoinHorizontal(lipgloss.Top, left, right)
}

// box draws one panel with its border and title. The focused panel's border is
// coloured AND its title carries a marker, so focus survives colour removal.
func (a App) box(p Panel, w, h int, body string) string {
	sty := borderBlur
	title := styDim.Render(" " + p.Title() + " ")
	if a.Focus == p {
		sty = borderFocus
		// 🔴 `>` IS THE FOCUS CARRIER, THE COLOUR IS DECORATION. Focus is a
		// meaning-bearing state like any other, so it is spelled.
		title = styTitle.Render(" > " + p.Title() + " ")
	}
	inner := w - 2
	if inner < 1 {
		inner = 1
	}
	head := title
	if a.Focus == PanelDiff && p == PanelDiff && a.currentFilePath() != "" {
		head = lipgloss.JoinHorizontal(lipgloss.Top, title, styDim.Render(a.currentFilePath()))
	}
	content := lipgloss.JoinVertical(lipgloss.Left, head, body)
	return sty.Width(inner).Height(max(1, h-2)).Render(content)
}

func (a App) currentFilePath() string {
	if a.Diff == nil || len(a.Diff.Files) == 0 {
		return ""
	}
	i := a.Diff.FileAt(a.diffCur)
	if i < 0 || i >= len(a.Diff.Files) {
		return ""
	}
	return a.Diff.Files[i].Path
}

// --- panel bodies -----------------------------------------------------------

// overviewBody is the top-left box.
//
// 🔴 WITH NO SNAPSHOT IT NAMES WHAT IS BEING WAITED FOR, IT DOES NOT GO BLANK.
// This is the skeleton's only identifying text: the reference the process was
// launched with, which comes from argv and is therefore known before any read
// returns. An empty box reads as a broken panel; the card this replaced at least
// said which pull request it was loading, and that must not be lost.
func (a App) overviewBody(w, h int) string {
	if a.Snap == nil {
		return strings.Join(clip([]string{
			styTitle.Render("#" + itoa(a.Num) + "  " + truncate(a.Repo(), max(1, w-8))),
			"",
			kv("STATE ", a.Load.Word()),
		}, h), "\n")
	}
	s := a.Snap
	rows := []string{
		styTitle.Render("#" + itoa(s.Num) + "  " + truncate(s.Title, w-8)),
		styDim.Render(s.Author+"  ") + styGood.Render("+"+itoa(s.Additions)) +
			styDim.Render(" / ") + styBad.Render("-"+itoa(s.Deletions)),
		"",
		kv("STATE ", PRStateWord(s.State, s.IsDraft)),
		kv("REVIEW", ReviewWord(s.ReviewDecision)),
		kv("MERGE ", MergeWord(s.Mergeable, s.MergeStateStatus, s.Merged)),
		kv("CHECKS", ChecksWord(s.Checks)),
		kv("THREAD", ThreadsWord(s.Threads)),
		styDim.Render("VIEWER as ") + styAccent.Render(s.ViewerLogin),
		styDim.Render(s.BaseRef + " <- " + s.HeadRef),
	}
	return strings.Join(clip(rows, h), "\n")
}

// diffBody is the right-hand pane.
//
// 🔴 A DIFF THAT FAILED SAYS SO *HERE*, NOT IN THE OVERVIEW. The Overview is
// height-clipped, so an error appended to its tail is the row that gets cut —
// measured: the message was off-screen at the default 100x30 and the test that
// asserts it is visible caught it. The Diff panel is the pane that has nothing
// to show, so it is the pane that explains why.
func (a App) diffBody() string {
	if a.Diff == nil {
		// 🔴 A DIFF FAILURE IS NOT REPORTABLE UNTIL THE PANEL QUERY HAS
		// ANSWERED, AND `LoadReady` IS THAT ANSWER. The diff read is speculative
		// — `ReadIntents` fires it before anything knows whether `#N` is a pull
		// request at all, because `mention-open.py` builds `/pull/{id}` for
		// every reference and lets github.com redirect. While the panel query is
		// still out, a 404 from the diff leg is indistinguishable from "this is
		// an ISSUE", so reporting it would flash DIFF UNAVAILABLE across the
		// skeleton of every issue the operator opens, one frame before the issue
		// card replaces it.
		//
		// 🔴 THE PREDICATE IS `diffIsRetryable`, NOT A SECOND SPELLING OF IT.
		// This arm prints "`r` retries" and `act`'s `ActRetry` arm is what makes
		// `r` do something; they were two inline conditions that happened to
		// agree, and nothing bound them. See `diffIsRetryable`'s header for why
		// each of its three terms is load-bearing.
		//
		// ⚠ `LoadFailed` CANNOT REACH HERE — `render` takes the error-card arm
		// first — so the states that can are LOADING (say nothing yet) and READY
		// (this really is a pull request whose diff failed).
		if a.diffIsRetryable() {
			return lipgloss.JoinVertical(lipgloss.Left,
				styBad.Render("DIFF UNAVAILABLE"),
				"",
				styDim.Render(a.Err.Error()),
				"",
				styDim.Render("`r` retries · `o` opens it in the browser"))
		}
		return styDim.Render("LOADING DIFF")
	}
	if a.Diff.Truncated {
		return lipgloss.JoinVertical(lipgloss.Left,
			styWarn.Render("TRUNCATED — more files than one page carries"),
			a.vp.View())
	}
	return a.vp.View()
}

func kv(label string, w StateWord) string {
	return styDim.Render(label+": ") + w.Render()
}

// 🔴 "NO COMMITS" AND "LOADING COMMITS" ARE DIFFERENT CLAIMS AND THE SKELETON
// MADE THE DIFFERENCE VISIBLE. While `Snap` is nil nothing knows how many
// commits this pull request has, so printing the empty-case word would be the
// panel asserting a fact it does not have — the same defect as a file list that
// is quietly short. Before the skeleton this arm was unreachable on screen,
// because a loading App drew a card instead of panels.
func (a App) commitsBody(w, h int) string {
	if a.Snap == nil {
		return styDim.Render("LOADING COMMITS")
	}
	if len(a.Snap.Commits) == 0 {
		return styDim.Render("NO COMMITS")
	}
	var rows []string
	for i, c := range a.Snap.Commits {
		line := fmt.Sprintf("%s %s", c.Abbrev, truncate(c.Headline, max(1, w-10)))
		if i == a.commitCur && a.Focus == PanelCommits {
			rows = append(rows, styCursor.Render(line))
		} else {
			rows = append(rows, styText.Render(line))
		}
	}
	if a.Snap.CommitsTruncated {
		rows = append(rows, styWarn.Render("TRUNCATED — more commits than one page"))
	}
	return strings.Join(window(rows, a.commitCur, h), "\n")
}

// filesBody renders the DIRECTORY TREE.
//
// 🔴 IT BUILDS NOTHING. `a.fileRows` was flattened when the snapshot arrived or
// when a directory was opened or closed; this function formats the rows it was
// handed and windows them. See tree.go's header for why: this runs on every
// frame, and the diff viewport one pane to the right is the measured example of
// what per-frame work in a renderer costs.
func (a App) filesBody(w, h int) string {
	// 🔴 SAME DISTINCTION AS `commitsBody`: with no snapshot the file list is
	// UNKNOWN, not empty.
	if a.Snap == nil {
		return styDim.Render("LOADING FILES")
	}
	if len(a.Snap.Files) == 0 {
		return styDim.Render("NO FILES")
	}
	rows := make([]string, 0, len(a.fileRows)+1)
	for i, r := range a.fileRows {
		rows = append(rows, a.renderFileRow(r, i, w))
	}
	if a.Snap.FilesTruncated {
		rows = append(rows, styWarn.Render("TRUNCATED — more files than one page"))
	}
	// 🔴 WINDOWED ON THE ROW CURSOR, which is the only cursor that indexes
	// `rows`. Windowing on a FILE index would scroll to the wrong row the
	// moment a directory row appeared above it.
	return strings.Join(window(rows, a.fileRowCur, h), "\n")
}

// renderFileRow formats ONE tree row into `w` columns.
//
// 🔴 A DIRECTORY ROW TRUNCATES FROM THE LEFT AND A FILE ROW FROM THE RIGHT, AND
// THAT ASYMMETRY IS THE POINT. A compacted chain's informative half is its TAIL
// — `nix/pkgs/tools/mention…` says nothing, `…/internal/ui` says everything —
// while a file row shows a BASENAME, whose head is what identifies it. Note the
// tree is a width WIN for files: dropping the directory prefix frees far more
// columns than the indent costs.
func (a App) renderFileRow(r FileRow, i, w int) string {
	indent := strings.Repeat("  ", r.Depth)
	count := fmt.Sprintf("+%d -%d", r.Additions, r.Deletions)
	// indent + one marker column + a space either side of the name.
	avail := max(1, w-len(indent)-len(count)-3)

	mark := FileWord(r.ChangeType)
	name := truncate(r.Name, avail)
	if r.IsDir {
		// ⚠ The chevron is a SHAPE, not a colour — it survives colour removal
		// the way the `M`/`A`/`D` letters on a file row do.
		glyph := chevronCollapsed
		if r.Expanded {
			glyph = chevronExpanded
		}
		mark = StateWord{glyph, styDim}
		name = truncateLeft(r.Name, avail)
	}

	if i == a.fileRowCur && a.Focus == PanelFiles {
		return indent + styCursor.Render(mark.Word+" "+name+" "+count)
	}
	body := name
	if r.IsDir {
		body = styTitle.Render(name)
	}
	return indent + mark.Render() + " " + body + " " + styDim.Render(count)
}

// --- the diff viewport ------------------------------------------------------

// 🔴 THE BUFFER IS SET ONCE, PLAIN; ONLY THE *VISIBLE* ROWS ARE STYLED.
//
// This is §6.2 hazard 2, and the first implementation of this file got it wrong
// in exactly the way the hazard describes — while carrying a comment claiming
// the opposite. It rendered every line through a lipgloss Style on every cursor
// move, and the Phase-1 measurement on the real panel caught it:
//
//	     diff lines   CPU per frame   60 fps budget
//	            200          4.1 ms          24.6 %
//	           4000         20.5 ms         123.1 %   <- misses the budget
//	          10000         46.1 ms         276.3 %   <- misses it badly
//
// Phase 0's bare viewport was FLAT across the same buffer sizes, so this defect
// was structurally invisible there: the bare probe never styled anything. That
// is precisely why the proposal made this a Phase-1 finding rather than a
// Phase-3 one, and it is a worked example of "verified in isolation" — both
// halves measured clean, the seam between them broken.
//
// The fix uses the viewport's own `StyleLineFunc`, which `visibleLines()` calls
// on the SLICE it is about to render, with an offset — so the styling work is
// bounded by the pane height (~36 rows) instead of the buffer (up to 10,000).

// rebuildDiffContent sets the plain text. Called when the DIFF changes or the
// pane is resized — never on a cursor move.
//
// 🔴 `SetContentLines`, NOT `JoinVertical` + `SetContent`. The join-then-
// resplit round trip is a measured 8% of total CPU in a comparable case study,
// and switching that one path took it from 2.19 s to ~270 ms. It is a Bubble
// Tea v2-era addition — a small, concrete reason the version choice matters.
func (a *App) rebuildDiffContent() {
	if a.Diff == nil {
		a.vp.SetContentLines(nil)
		return
	}
	lines := make([]string, len(a.Diff.Lines))
	for i, ln := range a.Diff.Lines {
		lines[i] = plainDiffLine(ln)
	}
	a.vp.SetContentLines(lines)
}

// syncDiffViewport re-points the style function at the current cursor and
// scrolls to it. 🔴 O(1) IN BUFFER SIZE — it allocates one closure.
func (a *App) syncDiffViewport() {
	d, cur := a.Diff, a.diffCur
	a.vp.StyleLineFunc = func(i int) lipgloss.Style {
		if i == cur {
			return styCursor
		}
		if d == nil || i < 0 || i >= len(d.Lines) {
			return styCtx
		}
		return diffLineStyle(d.Lines[i].Op)
	}
	if d != nil {
		a.vp.EnsureVisible(cur, 0, 0)
	}
}

// plainDiffLine is the row's TEXT, with no colour at all.
//
// 🔴 THE MARKER CHARACTER IS THE CARRIER AND IT IS PART OF THE TEXT. `+`, `-`
// and a leading space say what the line does, so stripping every colour leaves
// a readable unified diff — exactly the property the operator's font constraint
// demands, and the same property `words_test.go` asserts for every other state.
func plainDiffLine(ln udiff.Line) string {
	switch ln.Op {
	case udiff.OpAdd:
		return "+" + ln.Text
	case udiff.OpDelete:
		return "-" + ln.Text
	case udiff.OpHunk, udiff.OpMeta:
		return ln.Text
	}
	return " " + ln.Text
}

// diffLineStyle is lazygit's three colours and nothing else (§6.3) — no lexer,
// no syntax highlighting.
func diffLineStyle(op udiff.Op) lipgloss.Style {
	switch op {
	case udiff.OpAdd:
		return styAdd
	case udiff.OpDelete:
		return styDel
	case udiff.OpHunk:
		return styHunk
	case udiff.OpMeta:
		return styMeta
	}
	return styCtx
}

// --- cards ------------------------------------------------------------------

// renderIssueCard is §4's one-screen card.
//
// 🔴 READ-ONLY AND TERMINAL. The line this draws is explicit: no comment
// posting, no labels, no close/reopen, no threading, no navigation to related
// issues. The body is already in the GraphQL response, so rendering it costs
// nothing extra and there is no second round trip anywhere in the chain.
func (a App) renderIssueCard(h int) string {
	s := a.Snap
	head := lipgloss.JoinVertical(lipgloss.Left,
		styWarn.Render("ISSUE — not a pull request, so there is nothing to review"),
		"",
		styDim.Render(s.Repo+"#"+itoa(s.Num)+"   ")+PRStateWord(s.State, false).Render(),
		styTitle.Render(s.Title),
		styDim.Render("opened by "+s.Author),
		styDim.Render("as "+s.ViewerLogin),
		"",
	)
	return a.renderCard(h, "", lipgloss.JoinVertical(lipgloss.Left, head, a.body.View()))
}

func (a App) errorCardTitle() string {
	st := ghapi.AuthOther
	var ae *ghapi.APIError
	if errorsAs(a.Err, &ae) {
		st = ae.State
	}
	w := StateWord{st.Word(), styBad}
	if st == ghapi.AuthRateLimited {
		w.Style = styWarn
	}
	return w.Render() + styDim.Render(" — "+st.Hint())
}

func (a App) renderCard(h int, title, body string) string {
	content := body
	if title != "" {
		content = lipgloss.JoinVertical(lipgloss.Left, title, "", body)
	}
	return borderFocus.
		Width(max(1, a.Width-2)).
		Height(max(1, h-2)).
		Render(content)
}

// --- layout helpers ---------------------------------------------------------

func (a *App) relayout() {
	// 🔴 THE BAR IS PART OF THE LAYOUT, NOT AN OVERLAY. `render` subtracts its
	// height from the body; if `relayout` did not subtract the SAME height, the
	// diff viewport would still be sized for a bar-less frame and the bottom
	// rows would push off the terminal the moment a confirmation appeared.
	// Both call `renderBar()`, so they cannot disagree — and `composeBarLines`
	// is what stops that height moving while the operator types.
	chromeH := lipgloss.Height(a.renderFooter())
	if bar := a.renderBar(); bar != "" {
		chromeH += lipgloss.Height(bar)
	}
	bodyH := max(minHeight, a.Height-chromeH)

	leftW := leftColWidth
	if a.Width < leftColWidth*2 {
		leftW = a.Width / 3
	}
	a.vp.SetWidth(max(10, a.Width-leftW-4))
	a.vp.SetHeight(max(3, bodyH-3))

	a.body.SetWidth(max(10, a.Width-6))
	a.body.SetHeight(max(3, bodyH-10))
	a.rebuildDiffContent()
	a.syncDiffViewport()
}

func (a *App) setBody(s string) {
	a.body.SetContent(s)
	a.body.GotoTop()
}

func (a App) panelBodyHeight() int { return max(1, a.vp.Height()) }

func truncate(s string, w int) string {
	if w <= 0 {
		return ""
	}
	r := []rune(s)
	if len(r) <= w {
		return s
	}
	if w <= 1 {
		return string(r[:w])
	}
	return string(r[:w-1]) + "…"
}

// truncateLeft keeps the TAIL and puts the ellipsis at the FRONT.
//
// 🔴 IT EXISTS FOR COMPACTED DIRECTORY ROWS. `truncate` cuts the tail, which on
// a joined path is the half that carries the meaning: in a 30-column panel
// `nix/pkgs/tools/mention…` identifies nothing, while `…/internal/ui` names the
// directory exactly.
func truncateLeft(s string, w int) string {
	if w <= 0 {
		return ""
	}
	r := []rune(s)
	if len(r) <= w {
		return s
	}
	if w <= 1 {
		return string(r[len(r)-w:])
	}
	return "…" + string(r[len(r)-(w-1):])
}

// clip takes the first h rows.
func clip(rows []string, h int) []string {
	if h <= 0 || len(rows) <= h {
		return rows
	}
	return rows[:h]
}

// window returns h rows centred enough to keep `cur` visible. 🔴 It slices
// rather than styling everything and then hiding most of it — same reason as
// syncDiffViewport.
func window(rows []string, cur, h int) []string {
	if h <= 0 || len(rows) <= h {
		return rows
	}
	start := cur - h/2
	start = clamp(start, 0, len(rows)-h)
	return rows[start : start+h]
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
