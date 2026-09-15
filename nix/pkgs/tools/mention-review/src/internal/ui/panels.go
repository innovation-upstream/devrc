package ui

import (
	"fmt"
	"strings"

	"charm.land/lipgloss/v2"
	tea "charm.land/bubbletea/v2"

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
	bodyH := a.Height - lipgloss.Height(footer)

	var body string
	switch {
	case a.Load == LoadFailed:
		body = a.renderCard(bodyH, a.errorCardTitle(), a.body.View())
	case a.Load == LoadLoading:
		body = a.renderCard(bodyH, "LOADING — "+a.Repo()+"#"+itoa(a.Num), "")
	case a.Snap != nil && a.Snap.Kind == ghapi.KindIssue:
		body = a.renderIssueCard(bodyH)
	default:
		body = a.renderPanels(bodyH)
	}
	return lipgloss.JoinVertical(lipgloss.Left, body, footer)
}

// renderFooter renders the GENERATED help.
//
// 🔴 IT IS `help.Model.View(Keys)` AND NOTHING ELSE. There is no string literal
// here listing keys. The footer reads the very same `key.Binding` values
// `Dispatch()` matches on, so the two cannot disagree — and `keys_test.go`
// asserts the two SETS are equal in both directions so that stays true when
// somebody adds a binding.
func (a App) renderFooter() string {
	line := a.help.View(Keys)
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

func (a App) overviewBody(w, h int) string {
	if a.Snap == nil {
		return ""
	}
	s := a.Snap
	rows := []string{
		styTitle.Render("#" + itoa(s.Num) + "  " + truncate(s.Title, w-8)),
		styDim.Render(s.Author + "  ") + styGood.Render("+"+itoa(s.Additions)) +
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
		if a.Err != nil {
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

func (a App) commitsBody(w, h int) string {
	if a.Snap == nil || len(a.Snap.Commits) == 0 {
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

func (a App) filesBody(w, h int) string {
	if a.Snap == nil || len(a.Snap.Files) == 0 {
		return styDim.Render("NO FILES")
	}
	var rows []string
	for i, f := range a.Snap.Files {
		mark := FileWord(f.ChangeType)
		count := fmt.Sprintf("+%d -%d", f.Additions, f.Deletions)
		name := truncate(f.Path, max(1, w-len(count)-3))
		line := mark.Render() + " " + name + " " + styDim.Render(count)
		if i == a.fileCur && a.Focus == PanelFiles {
			line = styCursor.Render(mark.Word + " " + name + " " + count)
		}
		rows = append(rows, line)
	}
	if a.Snap.FilesTruncated {
		rows = append(rows, styWarn.Render("TRUNCATED — more files than one page"))
	}
	return strings.Join(window(rows, a.fileCur, h), "\n")
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
	footerH := lipgloss.Height(a.renderFooter())
	bodyH := max(minHeight, a.Height-footerH)

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
