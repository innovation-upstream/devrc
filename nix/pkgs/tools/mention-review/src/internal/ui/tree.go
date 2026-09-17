package ui

import (
	"strings"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
)

// 🔴 THE FILES PANEL IS A DIRECTORY TREE, AND THE TREE IS BUILT WHEN `Snap`
// ARRIVES — NEVER IN `filesBody()`.
//
// `filesBody` runs on EVERY frame. Per-frame work in a panel renderer is an
// already-measured incident in this program: the diff viewport styled every
// line per frame and missed the 60 fps budget by 276% at 10,000 lines (the
// table is in `panels.go`, above the viewport section). Splitting paths,
// allocating nodes and walking a trie 60 times a second would be the same
// defect one panel to the left. So the tree is built once per snapshot
// (`App.rebuildFileTree`), the VISIBLE rows are flattened once per
// collapse/expand (`App.rebuildFileRows`), and the renderer only formats the
// rows it was handed.
//
// 🔴 THE ROWS ARE NOT POSITIONALLY PARALLEL TO `Snap.Files` ANY MORE, AND THAT
// IS THE WHOLE HAZARD OF THIS CHANGE. A flat list let ONE integer index both
// `Snap.Files` (for rendering) and `Diff.Files` (for the cross-panel jump),
// because the two were assumed to be the same list in the same order. A tree
// breaks that twice over: directory rows are interleaved with file rows, and
// grouping by directory RE-ORDERS files relative to the diff. So the cursor
// indexes ROWS, and a row reaches its diff file BY PATH — see
// `App.selectRow`. An index-based mapping opens the WRONG file's hunk and
// looks perfectly fine in a screenshot.

// chevronExpanded / chevronCollapsed are the open/closed markers on a directory
// row.
//
// ⚠ A SHAPE, NOT A COLOUR — which is what the operator's font constraint
// actually requires. The two glyphs point in different directions, so they are
// distinguishable with every colour stripped, the way `M`/`A`/`D` are on a file
// row. They are constants so a test can pin the literal.
const (
	chevronExpanded  = "▾"
	chevronCollapsed = "▸"
)

// treeNode is one node of the built tree. It is IMMUTABLE after
// `buildFileTree` returns: `App` travels by value through `Step`, so a node
// mutated in place would change an App a caller still holds. Expand/collapse
// state lives in `App.collapsedDirs`, never here.
type treeNode struct {
	// name is what the row SHOWS. For a compacted chain it is the joined
	// segments ("internal/ui"), which is why it is separate from `path`.
	name string
	// path is the node's full path from the repository root, and it is the
	// IDENTITY used for collapse state and for the diff lookup.
	path     string
	isDir    bool
	children []*treeNode

	// additions / deletions are the node's own counts for a file, and the SUM
	// over EVERY descendant for a directory — not the direct children's, and
	// not a file count.
	additions int
	deletions int

	changeType string
}

// 🔴 THERE IS DELIBERATELY NO `fileIndex` ON A NODE OR A ROW. An earlier draft
// carried one — the row's position in `Snap.Files` — and nothing ever read it.
// It is left out rather than kept "for later" because the only thing anyone
// would plausibly reach for it to do is the exact mistake this whole change
// exists to prevent: resolving a row's diff file by an ORDINAL. `Snap.Files`
// comes from GraphQL and `Diff.Files` from the REST files endpoint; the only
// identity the two share is the PATH.

// FileRow is one VISIBLE row of the Files panel: the flattened tree, with every
// collapsed subtree already removed.
type FileRow struct {
	// Depth is the indent level. A compacted chain counts as ONE level, so the
	// six-segment path this program lives in indents once, not six times.
	Depth int
	Name  string
	Path  string
	IsDir bool
	// Expanded is meaningful on directory rows only.
	Expanded   bool
	Additions  int
	Deletions  int
	ChangeType string
}

// buildFileTree turns a flat path list into a compacted directory tree.
//
// Child order is FIRST-APPEARANCE order within each directory — one rule, and
// a deterministic one. It is deliberately NOT "directories first": that would
// be a second ordering rule to keep in sync with nothing, and first-appearance
// keeps a file list that is already grouped reading in its original order.
func buildFileTree(files []ghapi.File) *treeNode {
	root := &treeNode{isDir: true}
	for _, f := range files {
		segs := pathSegments(f.Path)
		cur := root
		for _, s := range segs[:len(segs)-1] {
			cur = cur.childDir(s)
		}
		cur.children = append(cur.children, &treeNode{
			name:       segs[len(segs)-1],
			path:       f.Path,
			additions:  f.Additions,
			deletions:  f.Deletions,
			changeType: f.ChangeType,
		})
	}
	// 🔴 THE ROOT IS NEVER COMPACTED INTO. It is not rendered, so merging it
	// with its only child would produce a leading "/" on the first row.
	for i, c := range root.children {
		if c.isDir {
			root.children[i] = compactNode(c)
		}
	}
	aggregate(root)
	return root
}

// pathSegments splits a path and drops empty segments, so "a//b" and a stray
// trailing slash cannot produce an unnamed row. A path with nothing left is
// kept as ONE segment spelled exactly as it arrived — a file the API named
// oddly is still a file, and dropping it would make the panel disagree with
// the diff about how many files there are.
func pathSegments(p string) []string {
	parts := strings.Split(p, "/")
	out := parts[:0:0]
	for _, s := range parts {
		if s != "" {
			out = append(out, s)
		}
	}
	if len(out) == 0 {
		return []string{p}
	}
	return out
}

func (n *treeNode) childDir(name string) *treeNode {
	for _, c := range n.children {
		if c.isDir && c.name == name {
			return c
		}
	}
	child := &treeNode{
		name:  name,
		path:  joinPath(n.path, name),
		isDir: true,
	}
	n.children = append(n.children, child)
	return child
}

func joinPath(base, name string) string {
	if base == "" {
		return name
	}
	return base + "/" + name
}

// compactNode collapses a chain of single-child directories into ONE node, as
// GitHub's "Files changed" sidebar does.
//
// 🔴 WITHOUT IT THIS PROGRAM'S OWN SOURCE TREE IS SIX ROWS OF NOTHING.
// `nix/pkgs/tools/mention-review/src/internal/ui/` is six nested directories
// with exactly one child each; in a ~30-column panel that is six rows spent on
// indentation, and the informative part — `internal/ui` — is the part that gets
// pushed off the right edge.
//
// ONE merge after the recursion is enough, and that is a property rather than
// an accident: a child returns already maximally compacted, so its own children
// are either two or more, or a single NON-directory. Either way the merged node
// cannot immediately qualify again.
func compactNode(n *treeNode) *treeNode {
	for i, c := range n.children {
		if c.isDir {
			n.children[i] = compactNode(c)
		}
	}
	if len(n.children) == 1 && n.children[0].isDir {
		c := n.children[0]
		n.name = n.name + "/" + c.name
		n.path = c.path
		n.children = c.children
	}
	return n
}

// aggregate sums a directory's counts over ALL of its descendants.
//
// 🔴 A SUM OVER THE WHOLE SUBTREE, NOT OVER THE DIRECT CHILDREN AND NOT A FILE
// COUNT. `tree_test.go` pins a fixture where those three answers are three
// different numbers, because a fixture where they coincide cannot see a `+` ->
// `max` mutant and survives a fully green suite.
func aggregate(n *treeNode) (int, int) {
	if !n.isDir {
		return n.additions, n.deletions
	}
	adds, dels := 0, 0
	for _, c := range n.children {
		ca, cd := aggregate(c)
		adds += ca
		dels += cd
	}
	n.additions, n.deletions = adds, dels
	return adds, dels
}

// flattenTree produces the VISIBLE rows: a pre-order walk that does not descend
// into a collapsed directory.
func flattenTree(root *treeNode, collapsed map[string]bool) []FileRow {
	if root == nil {
		return nil
	}
	var rows []FileRow
	var walk func(n *treeNode, depth int)
	walk = func(n *treeNode, depth int) {
		for _, c := range n.children {
			if !c.isDir {
				rows = append(rows, FileRow{
					Depth:      depth,
					Name:       c.name,
					Path:       c.path,
					Additions:  c.additions,
					Deletions:  c.deletions,
					ChangeType: c.changeType,
				})
				continue
			}
			expanded := !collapsed[c.path]
			rows = append(rows, FileRow{
				Depth:     depth,
				Name:      c.name,
				Path:      c.path,
				IsDir:     true,
				Expanded:  expanded,
				Additions: c.additions,
				Deletions: c.deletions,
			})
			if expanded {
				walk(c, depth+1)
			}
		}
	}
	walk(root, 0)
	return rows
}

// dirPathsOnTheWayTo returns the paths of every DIRECTORY node that contains
// `file`, so a reveal can open exactly the directories it has to.
//
// 🔴 IT WALKS THE NODES RATHER THAN SLICING THE STRING. After compaction a
// directory's path is the path of the LAST segment in its chain, so the set of
// openable directories is not "every prefix of the file path" — asking the tree
// is the only way to name them.
func dirPathsOnTheWayTo(root *treeNode, file string) []string {
	if root == nil {
		return nil
	}
	var out []string
	for _, d := range dirNodes(root) {
		if strings.HasPrefix(file, d.path+"/") {
			out = append(out, d.path)
		}
	}
	return out
}

func dirNodes(root *treeNode) []*treeNode {
	var out []*treeNode
	var walk func(n *treeNode)
	walk = func(n *treeNode) {
		for _, c := range n.children {
			if c.isDir {
				out = append(out, c)
				walk(c)
			}
		}
	}
	walk(root)
	return out
}

// --- the App's half ----------------------------------------------------------

// rebuildFileTree is called when `Snap` changes, and NOWHERE ON A RENDER PATH.
func (a *App) rebuildFileTree() {
	if a.Snap == nil {
		a.fileTree, a.fileRows, a.collapsedDirs = nil, nil, nil
		a.fileRowCur = 0
		return
	}
	a.fileTree = buildFileTree(a.Snap.Files)
	// 🔴 FULLY EXPANDED ON EVERY OPEN (see App.collapsedDirs).
	a.collapsedDirs = nil
	a.rebuildFileRows()
	a.fileRowCur = 0
}

// rebuildFileRows re-flattens the visible rows. Called when the collapse state
// changes, and nowhere else.
func (a *App) rebuildFileRows() {
	a.fileRows = flattenTree(a.fileTree, a.collapsedDirs)
}

// indexDiffFilesByPath builds the PATH -> `Diff.Files` index once per diff.
func (a *App) indexDiffFilesByPath() {
	if a.Diff == nil {
		a.diffFileByPath = nil
		return
	}
	m := make(map[string]int, len(a.Diff.Files))
	for i, f := range a.Diff.Files {
		// ⚠ FIRST WINS. Two entries with one path is a disagreement inside the
		// API response, not something to resolve by silently preferring the
		// later one.
		if _, dup := m[f.Path]; !dup {
			m[f.Path] = i
		}
	}
	a.diffFileByPath = m
}

// currentRow is the row under the Files cursor.
func (a App) currentRow() (FileRow, bool) {
	if a.fileRowCur < 0 || a.fileRowCur >= len(a.fileRows) {
		return FileRow{}, false
	}
	return a.fileRows[a.fileRowCur], true
}

// SelectedFilePath is the path of the file the Files cursor is on, or "" when
// it is on a directory row or there are no rows.
//
// ⚠ "" IS A REAL ANSWER, NOT A MISSING ONE — a directory row is a legitimate
// place for the cursor to be, and it is precisely the state in which nothing is
// selected. Exported alongside `Repo`, `Mode` and `ComposeBody`, which are the
// same shape of read-only accessor.
func (a App) SelectedFilePath() string {
	r, ok := a.currentRow()
	if !ok || r.IsDir {
		return ""
	}
	return r.Path
}

// selectRow points the DIFF at whatever the Files cursor now sits on.
//
// 🔴 A DIRECTORY ROW IS PURE NAVIGATION AND MUST NOT MOVE THE DIFF. A directory
// is not a thing the diff can show, so landing on one and jerking the diff to
// its first file would make it impossible to walk PAST a directory while
// reading — every step through the tree would throw away the operator's place.
//
// 🔴 AND THE FILE IS FOUND BY PATH. `Diff.Files` is the REST endpoint's list;
// the tree's rows are the GraphQL list grouped by directory. Reusing the row's
// ordinal — or even its `Snap.Files` index — would open a different file's hunk
// the moment those orders diverge, which grouping alone is enough to cause.
func (a *App) selectRow() {
	if a.Diff == nil {
		return
	}
	r, ok := a.currentRow()
	if !ok || r.IsDir {
		return
	}
	i, ok := a.diffFileByPath[r.Path]
	if !ok {
		// A file the Files panel knows about and the diff does not. The two
		// lists come from two endpoints; saying nothing is better than jumping
		// the diff somewhere arbitrary.
		return
	}
	if start := a.Diff.FileStart(i); start >= 0 {
		a.diffCur = start
		a.syncDiffViewport()
	}
}

// revealFile opens every directory between the root and `file` and puts the
// cursor on that file's row. See `syncFileCursorFromDiff` for why.
func (a *App) revealFile(file string) {
	if a.fileTree == nil {
		return
	}
	var toOpen []string
	for _, d := range dirPathsOnTheWayTo(a.fileTree, file) {
		if a.collapsedDirs[d] {
			toOpen = append(toOpen, d)
		}
	}
	if len(toOpen) > 0 {
		next := cloneCollapsed(a.collapsedDirs)
		for _, d := range toOpen {
			delete(next, d)
		}
		a.collapsedDirs = next
		a.rebuildFileRows()
	}
	for i, r := range a.fileRows {
		if !r.IsDir && r.Path == file {
			a.fileRowCur = i
			return
		}
	}
}

// cloneCollapsed is the copy half of the copy-on-write contract on
// `App.collapsedDirs`.
func cloneCollapsed(m map[string]bool) map[string]bool {
	next := make(map[string]bool, len(m)+1)
	for k, v := range m {
		if v {
			next[k] = true
		}
	}
	return next
}

// setCollapsed opens or closes ONE directory and re-flattens.
//
// ⚠ IT MOVES NO DIFF CURSOR. Opening and closing directories is navigation; the
// diff stays exactly where the operator left it, and the next diff-cursor move
// re-reveals whatever it needs (`syncFileCursorFromDiff`).
func (a *App) setCollapsed(path string, collapsed bool) {
	anchor := ""
	if r, ok := a.currentRow(); ok {
		anchor = r.Path
	}
	next := cloneCollapsed(a.collapsedDirs)
	if collapsed {
		next[path] = true
	} else {
		delete(next, path)
	}
	a.collapsedDirs = next
	a.rebuildFileRows()

	// Keep the cursor on the row it was on; if that row is no longer visible —
	// it was inside what just closed — fall back to the directory itself.
	if i := a.rowIndexOf(anchor); i >= 0 {
		a.fileRowCur = i
	} else if i := a.rowIndexOf(path); i >= 0 {
		a.fileRowCur = i
	}
	a.fileRowCur = clamp(a.fileRowCur, 0, max(0, len(a.fileRows)-1))
}

func (a App) rowIndexOf(path string) int {
	if path == "" {
		return -1
	}
	for i, r := range a.fileRows {
		if r.Path == path {
			return i
		}
	}
	return -1
}

// parentRowOf is the directory row that CONTAINS row `i`: the nearest preceding
// row one level shallower. -1 at the top level, where there is no parent to
// jump to.
func (a App) parentRowOf(i int) int {
	if i < 0 || i >= len(a.fileRows) {
		return -1
	}
	want := a.fileRows[i].Depth - 1
	if want < 0 {
		return -1
	}
	for j := i - 1; j >= 0; j-- {
		if a.fileRows[j].IsDir && a.fileRows[j].Depth == want {
			return j
		}
	}
	return -1
}

// treeExpand is `l` / `→`.
//
// ⚠ ON AN ALREADY-OPEN DIRECTORY IT IS INERT rather than descending to the
// first child. `j` is already one keystroke to the first child and means the
// same thing everywhere; a second spelling of it would be a binding to
// remember for no new capability.
func (a App) treeExpand() App {
	if a.Focus != PanelFiles {
		return a
	}
	r, ok := a.currentRow()
	if !ok {
		return a
	}
	if !r.IsDir {
		// A file has nothing to expand — `l` OPENS it, i.e. moves to the pane
		// that shows it. The diff cursor is already in this file, because
		// landing on the row put it there.
		a.Focus = PanelDiff
		a.relayout()
		return a
	}
	if r.Expanded {
		return a
	}
	a.setCollapsed(r.Path, false)
	return a
}

// treeCollapse is `h` / `←`: close this directory, or — on a file row, or a
// directory that is already closed — step OUT to the parent directory row.
func (a App) treeCollapse() App {
	if a.Focus != PanelFiles {
		return a
	}
	r, ok := a.currentRow()
	if !ok {
		return a
	}
	if r.IsDir && r.Expanded {
		a.setCollapsed(r.Path, true)
		return a
	}
	if p := a.parentRowOf(a.fileRowCur); p >= 0 {
		// 🔴 MOVING ONTO A DIRECTORY ROW MUST NOT MOVE THE DIFF, so this
		// assigns the cursor directly rather than going through `selectRow`.
		a.fileRowCur = p
	}
	return a
}

// treeToggle is `enter`: flip a directory, open a file.
func (a App) treeToggle() App {
	if a.Focus != PanelFiles {
		return a
	}
	r, ok := a.currentRow()
	if !ok {
		return a
	}
	if !r.IsDir {
		a.Focus = PanelDiff
		a.relayout()
		return a
	}
	a.setCollapsed(r.Path, r.Expanded)
	return a
}
