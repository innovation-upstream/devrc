package ui

import (
	"strings"
	"testing"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// 🔴 THE FILES PANEL IS A TREE — THIS FILE IS ITS CONSTRUCTION AND ITS
// RENDERING. The CURSOR half (what `j`, `h`, `l`, `enter`, `}` and `{` do, and
// the cross-panel invariant) lives in `movement_test.go`, where the other
// movement tests are. `scroll_test.go` is a PERFORMANCE file and is not a home
// for either.
//
// 🔴 EVERY FIXTURE HERE IS SYNTHETIC. This repository is public.

// --- the fixture --------------------------------------------------------------
//
// 🔴 THE NUMBERS ARE CHOSEN SO THE THREE PLAUSIBLE WRONG AGGREGATIONS GIVE
// THREE DIFFERENT ANSWERS. Under `src/`:
//
//	sum over every descendant   +46 -11   <- the contract
//	max over the descendants    +23  -6   <- a `+` -> `max` mutant
//	the number of files           4    4  <- a "count the files" mutant
//	the DIRECT file children    + 7  -0   <- a "direct children only" mutant
//
// A fixture where any two of those coincide cannot see the mutant that produces
// the other, and it SURVIVES a fully green suite.
//
// 🔴 AND THE TREE ORDER IS NOT THE DIFF ORDER, ALSO BY CONSTRUCTION. Grouping
// `src/a/two.go` under the directory its sibling opened moves it past
// `src/b/deep/three.go`, and the diff fixture below is in a THIRD order again —
// which is what the two real endpoints are free to do. Three separate wrong
// mappings (the row ordinal, the ordinal among file rows, and the `Snap.Files`
// index) therefore each name a DIFFERENT file, and
// `TestTheFilesPanelResolvesItsDiffFileByPathAndNotByAnyIndex` catches all three.

func treeFiles() []ghapi.File {
	return []ghapi.File{
		{Path: "src/a/one.go", ChangeType: "MODIFIED", Additions: 5, Deletions: 1},
		{Path: "src/b/deep/three.go", ChangeType: "RENAMED",
			PreviousPath: "src/b/deep/old.go", Additions: 23, Deletions: 6},
		{Path: "src/a/two.go", ChangeType: "MODIFIED", Additions: 11, Deletions: 4},
		{Path: "src/root.go", ChangeType: "ADDED", Additions: 7, Deletions: 0},
	}
}

// treeFixtureRows is the FULLY EXPANDED row list the fixture produces, as
// literals. Every visibility expectation in this file is a subset of it.
var treeFixtureRows = []rowShape{
	{0, "dir-open", "src", 46, 11},
	{1, "dir-open", "a", 16, 5},
	{2, "MODIFIED", "one.go", 5, 1},
	{2, "MODIFIED", "two.go", 11, 4},
	{1, "dir-open", "b/deep", 23, 6},
	{2, "RENAMED", "three.go", 23, 6},
	{1, "ADDED", "root.go", 7, 0},
}

// treeDiff is the SAME four files in a THIRD order — see the header.
func treeDiff(t *testing.T) *udiff.Diff {
	t.Helper()
	d, err := udiff.Parse([]udiff.FileInput{
		{Path: "src/root.go", ChangeType: "ADDED", Additions: 7,
			Patch: "@@ -0,0 +1,1 @@\n+package main\n"},
		{Path: "src/a/two.go", ChangeType: "MODIFIED", Additions: 11, Deletions: 4,
			Patch: "@@ -3,1 +3,2 @@ func two() {\n ctx := two()\n+\tctx.two()\n"},
		{Path: "src/b/deep/three.go", ChangeType: "RENAMED", PrevPath: "src/b/deep/old.go",
			Additions: 23, Deletions: 6,
			Patch: "@@ -7,1 +7,2 @@ func three() {\n ctx := three()\n+\tctx.three()\n"},
		{Path: "src/a/one.go", ChangeType: "MODIFIED", Additions: 5, Deletions: 1,
			Patch: "@@ -12,1 +12,2 @@ func one() {\n ctx := one()\n+\tctx.one()\n"},
	})
	if err != nil {
		t.Fatal(err)
	}
	return d
}

// treeApp is a loaded App over the multi-directory fixture, focused on Files.
//
// 🔴 IT PROVES ITS OWN DISCRIMINATING PROPERTY BEFORE RETURNING. If the tree's
// file order ever came to equal the diff's, every path-versus-index assertion
// in this package would pass against an INDEX-based implementation and prove
// nothing at all. That is a fixture failure, so it is a `Fatal`, here.
func treeApp(t *testing.T) App {
	t.Helper()
	snap := fixturePR()
	snap.Files = treeFiles()
	a := New(fxOwner, fxName, fxNum)
	a.Width, a.Height = 140, 40
	a.SetMergeMethod(fxMergeMethod)
	a, _ = a.Step(PRLoaded{Snap: snap})
	a, _ = a.Step(DiffLoaded{Diff: treeDiff(t)})
	a.Focus = PanelFiles

	var inTree, inSnap, inDiff []string
	for _, r := range a.fileRows {
		if !r.IsDir {
			inTree = append(inTree, r.Path)
		}
	}
	for _, f := range snap.Files {
		inSnap = append(inSnap, f.Path)
	}
	for _, f := range a.Diff.Files {
		inDiff = append(inDiff, f.Path)
	}
	if strings.Join(inTree, ",") == strings.Join(inDiff, ",") {
		t.Fatalf("the fixture's tree order EQUALS its diff order (%v) — every "+
			"path-not-index assertion below would pass against an index-based "+
			"implementation and prove nothing", inTree)
	}
	if strings.Join(inSnap, ",") == strings.Join(inDiff, ",") {
		t.Fatalf("the fixture's Snap.Files order EQUALS its Diff.Files order (%v) — "+
			"a `Snap.Files`-index mapping would survive", inSnap)
	}
	return a
}

// --- the row projection --------------------------------------------------------

// rowShape is one row reduced to what a human reads off the panel.
//
// ⚠ `kind` SPELLS THE DIRECTORY STATE ITSELF rather than calling `FileWord` or
// reading the chevron constants, so these expectations are not derived from the
// code they test. The literal glyphs are pinned separately, by
// `TestTheChevronsAreOnScreenAndSurviveColourRemoval`.
type rowShape struct {
	depth int
	kind  string // "dir-open" | "dir-closed" | the file's ChangeType
	name  string
	adds  int
	dels  int
}

func shapesOf(rows []FileRow) []rowShape {
	out := make([]rowShape, 0, len(rows))
	for _, r := range rows {
		kind := r.ChangeType
		if r.IsDir {
			kind = "dir-closed"
			if r.Expanded {
				kind = "dir-open"
			}
		}
		out = append(out, rowShape{r.Depth, kind, r.Name, r.Additions, r.Deletions})
	}
	return out
}

func shapesEqual(got, want []rowShape) bool {
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

// --- construction ---------------------------------------------------------------

// 🔴 A FLAT PATH LIST BECOMES A TREE, AND THE EXPECTED ROWS ARE LITERALS.
func TestBuildingTheTreeFromAFlatPathList(t *testing.T) {
	for _, c := range []struct {
		name  string
		files []ghapi.File
		want  []rowShape
	}{
		{
			name:  "no files at all",
			files: nil,
			want:  nil,
		},
		{
			name:  "a file at the repository root has no directory row",
			files: []ghapi.File{{Path: "README.md", ChangeType: "MODIFIED", Additions: 2, Deletions: 3}},
			want:  []rowShape{{0, "MODIFIED", "README.md", 2, 3}},
		},
		{
			name: "two files in one directory share ONE directory row",
			files: []ghapi.File{
				{Path: "pkg/handler.go", ChangeType: "MODIFIED", Additions: 9, Deletions: 1},
				{Path: "pkg/widget.go", ChangeType: "ADDED", Additions: 3, Deletions: 0},
			},
			want: []rowShape{
				{0, "dir-open", "pkg", 12, 1},
				{1, "MODIFIED", "handler.go", 9, 1},
				{1, "ADDED", "widget.go", 3, 0},
			},
		},
		{
			// Both are directories, so the directories-first rule does not
			// separate them and first-appearance decides: `zeta` stays above
			// `alpha` even though `alpha` sorts first alphabetically.
			name: "two directories stay separate and keep first-appearance order WITHIN the directory group",
			files: []ghapi.File{
				{Path: "zeta/late.go", ChangeType: "MODIFIED", Additions: 1, Deletions: 1},
				{Path: "alpha/early.go", ChangeType: "MODIFIED", Additions: 2, Deletions: 2},
			},
			want: []rowShape{
				{0, "dir-open", "zeta", 1, 1},
				{1, "MODIFIED", "late.go", 1, 1},
				{0, "dir-open", "alpha", 2, 2},
				{1, "MODIFIED", "early.go", 2, 2},
			},
		},
		{
			// 🔴 THE FILE ARRIVES FIRST AND THE SUBDIRECTORY STILL RENDERS ABOVE
			// IT. Under the first-appearance ordering this replaced, `top.go`
			// was row 1 and `nested` row 2.
			name: "a directory holding BOTH a file and a subdirectory puts the subdirectory FIRST",
			files: []ghapi.File{
				{Path: "src/top.go", ChangeType: "MODIFIED", Additions: 4, Deletions: 2},
				{Path: "src/nested/inner.go", ChangeType: "ADDED", Additions: 8, Deletions: 0},
			},
			want: []rowShape{
				{0, "dir-open", "src", 12, 2},
				{1, "dir-open", "nested", 8, 0},
				{2, "ADDED", "inner.go", 8, 0},
				{1, "MODIFIED", "top.go", 4, 2},
			},
		},
		{
			// 🔴 THE CASE THAT FAILS UNDER FIRST-APPEARANCE ORDER, AT TWO LEVELS
			// AT ONCE, and with two files in the same group so the WITHIN-group
			// order is pinned at the same time.
			//
			// First-appearance would render this as
			//   top.go / svc / api.go / z.go / inner / deep.go
			// — the file above the directory at the root, and `inner` below the
			// two files inside `svc`. Directories-first moves BOTH.
			name: "a subdirectory sorts above a file that appeared before it, at every level",
			files: []ghapi.File{
				{Path: "top.go", ChangeType: "MODIFIED", Additions: 1, Deletions: 1},
				{Path: "svc/api.go", ChangeType: "MODIFIED", Additions: 2, Deletions: 0},
				{Path: "svc/z.go", ChangeType: "ADDED", Additions: 4, Deletions: 0},
				{Path: "svc/inner/deep.go", ChangeType: "ADDED", Additions: 8, Deletions: 3},
			},
			want: []rowShape{
				{0, "dir-open", "svc", 14, 3},
				{1, "dir-open", "inner", 8, 3},
				{2, "ADDED", "deep.go", 8, 3},
				{1, "MODIFIED", "api.go", 2, 0},
				{1, "ADDED", "z.go", 4, 0},
				{0, "MODIFIED", "top.go", 1, 1},
			},
		},
		{
			name:  "the multi-directory fixture, fully expanded",
			files: treeFiles(),
			want:  treeFixtureRows,
		},
	} {
		t.Run(c.name, func(t *testing.T) {
			got := shapesOf(flattenTree(buildFileTree(c.files), nil))
			if !shapesEqual(got, c.want) {
				t.Errorf("rows =\n%+v\nwant\n%+v", got, c.want)
			}
		})
	}
}

// 🔴 A SIX-DEEP SINGLE-CHILD CHAIN IS ONE ROW, AND THE JOINED PATH IS PINNED AS
// A LITERAL. Without compaction this program's own source tree is six rows in a
// ~30-column panel, each with exactly one child, and the informative tail is
// exactly what gets pushed off the right edge.
func TestASingleChildDirectoryChainCompactsIntoOneRow(t *testing.T) {
	const deep = "nix/pkgs/tools/mention-review/src/tree.go"
	rows := flattenTree(buildFileTree([]ghapi.File{
		{Path: deep, ChangeType: "MODIFIED", Additions: 120, Deletions: 7},
	}), nil)

	want := []rowShape{
		{0, "dir-open", "nix/pkgs/tools/mention-review/src", 120, 7},
		{1, "MODIFIED", "tree.go", 120, 7},
	}
	if got := shapesOf(rows); !shapesEqual(got, want) {
		t.Fatalf("a five-directory chain produced\n%+v\nwant\n%+v", got, want)
	}
	// 🔴 AND THE ROW'S IDENTITY IS THE FULL PATH, NOT THE JOINED LABEL. Collapse
	// state and the reveal both key on it, so a compacted node that kept its
	// FIRST segment's path would collapse the wrong subtree.
	if got := rows[0].Path; got != "nix/pkgs/tools/mention-review/src" {
		t.Errorf("the compacted row's path is %q, want the path of the LAST segment", got)
	}

	// A SIX-directory chain, the shape the brief names, plus a sibling deeper in
	// so this is not the same case twice.
	sixRows := flattenTree(buildFileTree([]ghapi.File{
		{Path: "a/b/c/d/e/f/leaf.go", ChangeType: "ADDED", Additions: 3, Deletions: 0},
	}), nil)
	sixWant := []rowShape{
		{0, "dir-open", "a/b/c/d/e/f", 3, 0},
		{1, "ADDED", "leaf.go", 3, 0},
	}
	if got := shapesOf(sixRows); !shapesEqual(got, sixWant) {
		t.Errorf("a six-directory chain produced\n%+v\nwant\n%+v", got, sixWant)
	}

	// 🔴 THE NEGATIVE CONTROL: a chain that BRANCHES must NOT be compacted past
	// the branch. Without this, "compaction" that merged everything
	// unconditionally would pass the two cases above.
	branch := shapesOf(flattenTree(buildFileTree([]ghapi.File{
		{Path: "a/b/c/left.go", ChangeType: "MODIFIED", Additions: 1, Deletions: 0},
		{Path: "a/b/d/right.go", ChangeType: "MODIFIED", Additions: 2, Deletions: 0},
	}), nil))
	branchWant := []rowShape{
		{0, "dir-open", "a/b", 3, 0},
		{1, "dir-open", "c", 1, 0},
		{2, "MODIFIED", "left.go", 1, 0},
		{1, "dir-open", "d", 2, 0},
		{2, "MODIFIED", "right.go", 2, 0},
	}
	if !shapesEqual(branch, branchWant) {
		t.Errorf("a branching chain produced\n%+v\nwant\n%+v", branch, branchWant)
	}
}

// 🔴 A DIRECTORY'S COUNTS ARE THE SUM OVER EVERY DESCENDANT.
//
// The three rival answers are named and asserted to be DIFFERENT numbers in
// this very fixture, so a reader can see that the assertion discriminates
// rather than having to trust that it does.
func TestADirectoryCountIsTheSumOverEveryDescendantNotTheMaxAndNotAFileCount(t *testing.T) {
	rows := flattenTree(buildFileTree(treeFiles()), nil)
	byPath := map[string]FileRow{}
	for _, r := range rows {
		byPath[r.Path] = r
	}

	src, ok := byPath["src"]
	if !ok {
		t.Fatalf("no `src` row in %+v", shapesOf(rows))
	}

	const (
		wantAdds = 46 // 5 + 11 + 23 + 7
		wantDels = 11 // 1 +  4 +  6 + 0
		maxAdds  = 23 // the largest single descendant
		maxDels  = 6
		fileN    = 4 // the number of files below `src`
		directA  = 7 // src/root.go alone — the only DIRECT file child
		directD  = 0
	)
	// The instrument check: the rivals really are different numbers.
	for _, rival := range []struct {
		name       string
		adds, dels int
	}{
		{"max", maxAdds, maxDels},
		{"file count", fileN, fileN},
		{"direct file children only", directA, directD},
	} {
		if rival.adds == wantAdds && rival.dels == wantDels {
			t.Fatalf("the fixture cannot tell a SUM from %q — both are +%d -%d, so a "+
				"mutant producing it survives", rival.name, wantAdds, wantDels)
		}
	}

	if src.Additions != wantAdds || src.Deletions != wantDels {
		t.Errorf("src = +%d -%d, want +%d -%d (the SUM over all four descendants; "+
			"max would be +%d -%d, a file count %d, its direct file children +%d -%d)",
			src.Additions, src.Deletions, wantAdds, wantDels,
			maxAdds, maxDels, fileN, directA, directD)
	}

	// The nested directory, whose own numbers are distinct again.
	a := byPath["src/a"]
	if a.Additions != 16 || a.Deletions != 5 {
		t.Errorf("src/a = +%d -%d, want +16 -5 (5+11 and 1+4; max is +11 -4, "+
			"the file count 2)", a.Additions, a.Deletions)
	}

	// And a FILE row carries its own counts unchanged — the recursion's base
	// case, which a mutant that aggregated leaves too would move.
	if f := byPath["src/root.go"]; f.Additions != 7 || f.Deletions != 0 {
		t.Errorf("src/root.go = +%d -%d, want +7 -0", f.Additions, f.Deletions)
	}
}

// --- collapse and expand ---------------------------------------------------------

// 🔴 COLLAPSING REMOVES EXACTLY ONE SUBTREE, AND THE EXACT ROW SET IS PINNED
// BOTH BEFORE AND AFTER. Asserting only that "fewer rows are visible" would
// pass against an implementation that hid the wrong subtree.
func TestCollapsingADirectoryHidesExactlyItsSubtree(t *testing.T) {
	root := buildFileTree(treeFiles())

	if got := shapesOf(flattenTree(root, nil)); !shapesEqual(got, treeFixtureRows) {
		t.Fatalf("the fully expanded fixture is\n%+v\nwant\n%+v", got, treeFixtureRows)
	}

	for _, c := range []struct {
		name      string
		collapsed map[string]bool
		want      []rowShape
	}{
		{
			name:      "the nested `src/a` closes, and nothing else moves",
			collapsed: map[string]bool{"src/a": true},
			want: []rowShape{
				{0, "dir-open", "src", 46, 11},
				{1, "dir-closed", "a", 16, 5},
				{1, "dir-open", "b/deep", 23, 6},
				{2, "RENAMED", "three.go", 23, 6},
				{1, "ADDED", "root.go", 7, 0},
			},
		},
		{
			name:      "a COMPACTED directory closes as one unit",
			collapsed: map[string]bool{"src/b/deep": true},
			want: []rowShape{
				{0, "dir-open", "src", 46, 11},
				{1, "dir-open", "a", 16, 5},
				{2, "MODIFIED", "one.go", 5, 1},
				{2, "MODIFIED", "two.go", 11, 4},
				{1, "dir-closed", "b/deep", 23, 6},
				{1, "ADDED", "root.go", 7, 0},
			},
		},
		{
			name:      "closing the top leaves ONE row, still carrying the whole total",
			collapsed: map[string]bool{"src": true},
			want:      []rowShape{{0, "dir-closed", "src", 46, 11}},
		},
		{
			name:      "an inner directory closed inside a closed outer one is simply not drawn",
			collapsed: map[string]bool{"src": true, "src/a": true},
			want:      []rowShape{{0, "dir-closed", "src", 46, 11}},
		},
		{
			name:      "a path that names no directory changes nothing",
			collapsed: map[string]bool{"src/b": true, "not/a/directory": true},
			want:      treeFixtureRows,
		},
	} {
		t.Run(c.name, func(t *testing.T) {
			got := shapesOf(flattenTree(root, c.collapsed))
			if !shapesEqual(got, c.want) {
				t.Errorf("rows =\n%+v\nwant\n%+v", got, c.want)
			}
		})
	}
}

// 🔴 EVERY CHANGED FILE IS VISIBLE ON OPEN. No PR may get a worse first screen
// than it had before this panel became a tree, so the default collapse set is
// EMPTY and every file row is present.
//
// ⚠ THIS IS AN INVARIANT GUARD, NOT A REGRESSION GUARD, AND IT IS LABELLED SO
// BECAUSE IT WAS COUNTED AS ONE. `treeApp` builds a fresh `New()` App, whose
// `collapsedDirs` is already nil and whose `fileRowCur` is already 0 before
// `PRLoaded` is ever handled — so this passes whether or not `rebuildFileTree`
// writes those fields at all. MEASURED: mutants deleting `a.collapsedDirs = nil`
// and deleting `a.fileRowCur = 0` each SURVIVED it. What this pins is the FIRST
// screen; what a RE-READ does to that state is a different claim and is pinned
// by `TestARereadKeepsTheClosedDirectoriesAndTheCursorsFile` below, which starts
// from an App that is deliberately not in the default state.
func TestTheTreeStartsFullyExpanded(t *testing.T) {
	a := treeApp(t)
	if len(a.collapsedDirs) != 0 {
		t.Errorf("a freshly loaded App starts with %v collapsed, want nothing", a.collapsedDirs)
	}
	if got := shapesOf(a.fileRows); !shapesEqual(got, treeFixtureRows) {
		t.Errorf("the opening rows are\n%+v\nwant\n%+v", got, treeFixtureRows)
	}
	// Stated the other way round, in terms the operator would use: every file in
	// the snapshot has a visible row.
	visible := map[string]bool{}
	for _, r := range a.fileRows {
		if !r.IsDir {
			visible[r.Path] = true
		}
	}
	for _, f := range a.Snap.Files {
		if !visible[f.Path] {
			t.Errorf("%s is in the PR and has no row on open", f.Path)
		}
	}
	if len(visible) != len(a.Snap.Files) {
		t.Errorf("%d file rows for %d changed files", len(visible), len(a.Snap.Files))
	}
}

// --- a re-read ---------------------------------------------------------------
//
// 🔴 `PRLoaded` IS NOT ONLY THE FIRST OPEN. `stepWriteDone` emits `FetchPR`
// after every successful write, and that comes back as a second `PRLoaded` in
// the middle of a review. The tests below are the ones that drive it twice.

// reread hands the App a second `PRLoaded` carrying `files`, the way a
// successful write's re-fetch does.
func reread(a App, files []ghapi.File) App {
	snap := fixturePR()
	snap.Files = files
	next, _ := a.Step(PRLoaded{Snap: snap})
	return next
}

// rereadFiles is `treeFiles()` in a DIFFERENT ORDER, which is what makes the
// cursor assertions below discriminating.
//
// 🔴 THE POINT IS THAT THE ANCHOR'S ROW INDEX MOVES. Under this order
// `src/b/deep` is created before `src/a`, so `three.go` sits at a different row
// than it did on the first read — an implementation that remembered the cursor
// as an ORDINAL would restore it onto a different row and still look plausible.
// The re-read fixture is deliberately the SAME FOUR FILES, so nothing here can
// be explained by the file simply being gone.
func rereadFiles() []ghapi.File {
	return []ghapi.File{
		{Path: "src/root.go", ChangeType: "ADDED", Additions: 7, Deletions: 0},
		{Path: "src/b/deep/three.go", ChangeType: "RENAMED",
			PreviousPath: "src/b/deep/old.go", Additions: 23, Deletions: 6},
		{Path: "src/a/one.go", ChangeType: "MODIFIED", Additions: 5, Deletions: 1},
		{Path: "src/a/two.go", ChangeType: "MODIFIED", Additions: 11, Deletions: 4},
	}
}

// 🔴 A SUCCESSFUL WRITE MUST NOT THROW AWAY THE OPERATOR'S PLACE. Comment on a
// sixty-file PR and the re-read used to hand back a fully expanded tree with
// the cursor on row 0 — the collapse state discarded outright, and the cursor
// reset where it had previously survived.
//
// 🔴 THIS STARTS FROM A NON-DEFAULT STATE ON PURPOSE. A fresh `New()` App
// already has an empty collapse set and a zero cursor, so a test driven from
// one cannot tell "preserved" from "reset" — see the label on
// `TestTheTreeStartsFullyExpanded`. Here a directory is closed and the cursor is
// parked on a file several rows down BEFORE the second `PRLoaded`.
func TestARereadKeepsTheClosedDirectoriesAndTheCursorsFile(t *testing.T) {
	const anchor = "src/b/deep/three.go"

	a := treeApp(t)

	// Close `src/a` — row 1 on the first read's order.
	a.fileRowCur = 1
	if r, _ := a.currentRow(); r.Path != "src/a" || !r.IsDir {
		t.Fatalf("row 1 is %+v, want the `src/a` directory row", r)
	}
	a, _ = a.Step(keyPress("enter"))
	if !a.collapsedDirs["src/a"] {
		t.Fatalf("`enter` on `src/a` left %v collapsed", a.collapsedDirs)
	}

	// Park the cursor on a FILE, several rows from 0.
	a.fileRowCur = a.rowIndexOf(anchor)
	if a.fileRowCur <= 0 {
		t.Fatalf("%s is at row %d; the cursor has to start somewhere other than "+
			"0 or this test cannot tell preservation from a reset", anchor, a.fileRowCur)
	}
	before := a.fileRowCur
	beforeRows := shapesOf(a.fileRows)

	a = reread(a, rereadFiles())

	// The closed directory is STILL closed.
	if !a.collapsedDirs["src/a"] {
		t.Errorf("after a re-read `src/a` is open again (collapsed = %v) — a "+
			"successful write must not re-expand the tree the operator closed", a.collapsedDirs)
	}
	// Stated as the operator would see it: the two files under `src/a` have no
	// row, and the rest of the tree does.
	for _, hidden := range []string{"src/a/one.go", "src/a/two.go"} {
		if i := a.rowIndexOf(hidden); i >= 0 {
			t.Errorf("%s is visible at row %d after the re-read, inside a directory "+
				"that was closed:\n%+v", hidden, i, shapesOf(a.fileRows))
		}
	}

	// The cursor is on the SAME FILE, found by path.
	if got := a.SelectedFilePath(); got != anchor {
		t.Errorf("after a re-read the cursor is on %q (row %d of\n%+v), want %q — "+
			"the file it was on before", got, a.fileRowCur, shapesOf(a.fileRows), anchor)
	}
	// 🔴 AND BY PATH RATHER THAN BY INDEX: the row NUMBER moved, so holding the
	// old ordinal would have been wrong here and indistinguishable everywhere
	// the two orders happen to agree.
	if a.fileRowCur == before {
		t.Fatalf("%s is at row %d both before and after the re-read — the fixture "+
			"cannot tell a path-keyed restore from an index-keyed one. Rows were\n"+
			"%+v\nand are now\n%+v", anchor, before, beforeRows, shapesOf(a.fileRows))
	}

	// 🔴 AND IT SURVIVES THE SECOND MESSAGE OF THE SAME FLOW. `PRLoaded` asks
	// for the diff, so a real re-read is two messages and `DiffLoaded` is the
	// one the operator actually ends on. It resets `diffCur` by design — a new
	// diff, read from the top — and this asserts it does not take the FILES
	// cursor with it, which is a claim about a seam neither message's own test
	// covers.
	restored := a.fileRowCur
	a, _ = a.Step(DiffLoaded{Diff: treeDiff(t)})
	if a.fileRowCur != restored || a.SelectedFilePath() != anchor {
		t.Errorf("the DiffLoaded that follows a re-read moved the Files cursor from "+
			"row %d (%s) to row %d (%q)", restored, anchor, a.fileRowCur, a.SelectedFilePath())
	}
	if !a.collapsedDirs["src/a"] {
		t.Errorf("the DiffLoaded that follows a re-read re-opened `src/a` (collapsed = %v)",
			a.collapsedDirs)
	}
}

// 🔴 A COLLAPSE ENTRY NAMING A DIRECTORY THE NEW READ DOES NOT HAVE IS DROPPED.
// The entry is a path and nothing else removes it, so keeping it would make a
// directory that left the PR and came back reappear ALREADY CLOSED, from a
// decision the operator made about a different file list — and would let the
// set grow for the length of a session.
//
// ⚠ ITS RIVAL IS NOT THE CODE THIS REPLACED — SAY SO RATHER THAN LET IT READ AS
// A REGRESSION GUARD. Against the previous behaviour (`collapsedDirs = nil` on
// every `PRLoaded`) this test PASSES, trivially: a set that is emptied wholesale
// cannot hold a stale entry. MEASURED, by running it against that code. What it
// discriminates is the wrong version of the FIX — preserving the set without
// pruning it — and a mutant that returns `collapsed` unchanged does kill it.
func TestARereadDropsCollapseEntriesForDirectoriesThatAreGone(t *testing.T) {
	a := treeApp(t)
	a.fileRowCur = 1
	a, _ = a.Step(keyPress("enter")) // close `src/a`
	if !a.collapsedDirs["src/a"] {
		t.Fatalf("setup: `src/a` is not closed (%v)", a.collapsedDirs)
	}

	// A read in which `src/a` does not exist at all.
	gone := []ghapi.File{
		{Path: "src/b/deep/three.go", ChangeType: "RENAMED",
			PreviousPath: "src/b/deep/old.go", Additions: 23, Deletions: 6},
		{Path: "src/root.go", ChangeType: "ADDED", Additions: 7, Deletions: 0},
	}
	a = reread(a, gone)
	if a.collapsedDirs["src/a"] {
		t.Errorf("`src/a` is not in the PR any more and is still in the collapse "+
			"set %v", a.collapsedDirs)
	}
	if len(a.collapsedDirs) != 0 {
		t.Errorf("collapse set is %v, want nothing left once its only entry's "+
			"directory is gone", a.collapsedDirs)
	}

	// 🔴 THE CONSEQUENCE, WHICH IS THE PART THAT MATTERS: when those files come
	// back, the directory is OPEN. Without the prune this row would be closed
	// and the assertion above would be a claim about a map nobody reads.
	a = reread(a, treeFiles())
	if got := shapesOf(a.fileRows); !shapesEqual(got, treeFixtureRows) {
		t.Errorf("after `src/a` returned the rows are\n%+v\nwant the fully expanded\n%+v",
			got, treeFixtureRows)
	}
}

// --- rendering -------------------------------------------------------------------

// 🔴 THE OPEN/CLOSED STATE IS ON SCREEN AS A SHAPE, NOT AS A COLOUR. The
// operator's font renders the severity circles as one glyph, so every
// meaning-bearing state in this program is carried by something that survives
// `stripANSI`. These two chevrons point in different directions, and the
// literal characters are pinned here.
func TestTheChevronsAreOnScreenAndSurviveColourRemoval(t *testing.T) {
	a := treeApp(t)
	open := stripANSI(a.filesBody(30, 20))
	if !strings.Contains(open, chevronExpanded) {
		t.Errorf("an expanded directory row carries no %q:\n%s", chevronExpanded, open)
	}
	if strings.Contains(open, chevronCollapsed) {
		t.Errorf("a fully expanded tree drew a CLOSED chevron %q:\n%s", chevronCollapsed, open)
	}

	a.fileRowCur = 0 // the `src` row
	a, _ = a.Step(keyPress("enter"))
	closed := stripANSI(a.filesBody(30, 20))
	if !strings.Contains(closed, chevronCollapsed) {
		t.Errorf("a collapsed directory row carries no %q:\n%s", chevronCollapsed, closed)
	}
	if strings.Contains(closed, chevronExpanded) {
		t.Errorf("the collapsed tree still draws an OPEN chevron %q:\n%s", chevronExpanded, closed)
	}
	// The two are different characters — the assertion above is worthless if
	// they are not.
	if chevronExpanded == chevronCollapsed {
		t.Fatal("the two chevrons are the same character; open and closed are indistinguishable")
	}

	// 🔴 AND NOW THE LITERAL BYTES, WHICH IS THE ONLY PART OF THIS TEST THAT
	// PINS WHICH GLYPH MEANS WHICH.
	//
	// Every assertion above is written THROUGH `chevronExpanded` /
	// `chevronCollapsed`, so all of it is invariant under any consistent
	// renaming of the two — including SWAPPING THEIR VALUES, which makes every
	// open directory draw the closed glyph and every closed one the open glyph.
	// MEASURED: the whole package stays green under that swap. The identity
	// check just above catches only the degenerate case where both are the same
	// character, and `rowShape` spells "dir-open"/"dir-closed" rather than
	// reading a chevron, so no construction test can see it either. These two
	// assertions name the characters themselves and are the only thing that can.
	//
	// ⚠ THE CONTRAST MUST STAY A SHAPE, NOT A COLOUR — the operator's font
	// renders the severity circles as one glyph, so a DOWN-pointing triangle
	// against a RIGHT-pointing one is what survives both `stripANSI` and his
	// terminal. Changing either literal here means changing the glyph a human
	// reads, which is the point of writing them out.
	openRow := strings.SplitN(open, "\n", 2)[0]
	if !strings.HasPrefix(openRow, "▾ ") {
		t.Errorf("the EXPANDED `src` row is %q, want it to lead with the "+
			"down-pointing chevron \"▾\" — the two chevron constants may be "+
			"swapped, which no other assertion in this package can see", openRow)
	}
	closedRow := strings.SplitN(closed, "\n", 2)[0]
	if !strings.HasPrefix(closedRow, "▸ ") {
		t.Errorf("the COLLAPSED `src` row is %q, want it to lead with the "+
			"right-pointing chevron \"▸\" — the two chevron constants may be "+
			"swapped, which no other assertion in this package can see", closedRow)
	}
}

// 🔴 A COMPACTED DIRECTORY ROW TRUNCATES FROM THE LEFT.
//
// `truncate` cuts the TAIL, which on a joined path is the informative half:
// `nix/pkgs/tools/mention…` identifies nothing, `…/internal/ui` identifies the
// directory exactly. A file row keeps ordinary truncation, because a basename's
// HEAD is what names it.
func TestTruncateLeftKeepsTheTailAndTruncateKeepsTheHead(t *testing.T) {
	const path = "nix/pkgs/tools/mention-review/src/internal/ui"
	for _, c := range []struct {
		w    int
		want string
	}{
		{0, ""},
		{1, "i"},
		{2, "…i"},
		{14, "…c/internal/ui"},
		{20, "…iew/src/internal/ui"},
		{len(path), path},
		{len(path) + 10, path},
	} {
		if got := truncateLeft(path, c.w); got != c.want {
			t.Errorf("truncateLeft(%d) = %q, want %q", c.w, got, c.want)
		}
	}
	// The width contract: never wider than asked for.
	for w := 0; w < len(path)+3; w++ {
		if n := len([]rune(truncateLeft(path, w))); n > w {
			t.Errorf("truncateLeft(%d) returned %d runes", w, n)
		}
	}
	// 🔴 THE CONTRAST WITH `truncate`, IN ONE ASSERTION. If the two agreed, the
	// left-truncation would be a no-op nobody could see.
	if truncateLeft(path, 20) == truncate(path, 20) {
		t.Fatal("truncateLeft and truncate produce the same string — the left " +
			"truncation is not doing anything")
	}
	if !strings.HasPrefix(truncateLeft(path, 20), "…") {
		t.Errorf("truncateLeft(20) = %q, want a leading ellipsis", truncateLeft(path, 20))
	}
	if !strings.HasSuffix(truncateLeft(path, 20), "internal/ui") {
		t.Errorf("truncateLeft(20) = %q, want the TAIL kept", truncateLeft(path, 20))
	}
}

// 🔴 A DEEP PATH IN THE REAL PANEL WIDTH. `leftColWidth` is 34 and the body is
// `leftW-4` — about 30 columns — so this is the width the operator actually
// has. No row may exceed it, nothing may panic, and the compacted directory row
// must still end in the segments that name it.
func TestADeepPathRendersInsideThirtyColumns(t *testing.T) {
	const w = 30
	snap := fixturePR()
	snap.Files = []ghapi.File{
		{Path: "nix/pkgs/tools/mention-review/src/internal/ui/a-rather-long-file-name.go",
			ChangeType: "MODIFIED", Additions: 120, Deletions: 7},
		{Path: "nix/pkgs/tools/mention-review/src/internal/ui/tree.go",
			ChangeType: "ADDED", Additions: 9, Deletions: 0},
	}
	a := New(fxOwner, fxName, fxNum)
	a.Width, a.Height = 140, 40
	a, _ = a.Step(PRLoaded{Snap: snap})
	a.Focus = PanelFiles

	body := stripANSI(a.filesBody(w, 20))
	lines := strings.Split(body, "\n")
	if len(lines) != 3 {
		t.Fatalf("expected one directory row and two file rows, got %d:\n%s", len(lines), body)
	}
	for i, ln := range lines {
		if n := len([]rune(ln)); n > w {
			t.Errorf("row %d is %d columns wide in a %d-column panel: %q", i, n, w, ln)
		}
	}
	// The directory row kept its TAIL.
	if !strings.HasPrefix(lines[0], chevronExpanded+" …") {
		t.Errorf("the compacted directory row does not lead with an ellipsis: %q", lines[0])
	}
	if !strings.Contains(lines[0], "internal/ui") {
		t.Errorf("the compacted directory row lost its informative tail: %q", lines[0])
	}
	// And a file row kept its HEAD — the basename's beginning is what names it.
	if !strings.Contains(lines[1], "a-rather-long") {
		t.Errorf("the long file row lost its leading characters: %q", lines[1])
	}
	// The short file fits whole, indented under the directory.
	if !strings.Contains(lines[2], "tree.go") {
		t.Errorf("the short file row is missing its name: %q", lines[2])
	}
}

// 🔴 THE EMPTY STATE AND THE TRUNCATION BANNER BOTH SURVIVED THE REWRITE. Two
// states that predate the tree and that a rewrite of `filesBody` is exactly the
// kind of change to lose.
func TestTheFilesPanelKeepsItsEmptyStateAndItsTruncationBanner(t *testing.T) {
	t.Run("no files", func(t *testing.T) {
		snap := fixturePR()
		snap.Files = nil
		a := New(fxOwner, fxName, fxNum)
		a.Width, a.Height = 140, 40
		a, _ = a.Step(PRLoaded{Snap: snap})
		body := stripANSI(a.filesBody(30, 20))
		if !strings.Contains(body, "NO FILES") {
			t.Errorf("an empty file list rendered %q, want NO FILES", body)
		}
		if len(a.fileRows) != 0 {
			t.Errorf("an empty file list produced %d rows", len(a.fileRows))
		}
		// And nothing panics when the tree keys are pressed at it.
		a.Focus = PanelFiles
		for _, k := range []string{"j", "k", "h", "l", "enter"} {
			if next, intents := a.Step(keyPress(k)); len(intents) != 0 {
				t.Errorf("%q on an empty Files panel emitted %v", k, intents)
			} else if next.fileRowCur != 0 {
				t.Errorf("%q on an empty Files panel moved the cursor to %d", k, next.fileRowCur)
			}
		}
	})

	t.Run("truncated", func(t *testing.T) {
		a := treeApp(t)
		plain := stripANSI(a.filesBody(30, 20))
		// POSITIVE CONTROL: the banner is absent when the list is complete, so
		// the assertion below is a claim about the flag.
		if strings.Contains(plain, "TRUNCATED") {
			t.Fatalf("an untruncated list already carries the banner:\n%s", plain)
		}
		a.Snap.FilesTruncated = true
		body := stripANSI(a.filesBody(30, 20))
		if !strings.Contains(body, "TRUNCATED — more files than one page") {
			t.Errorf("the truncation banner is missing:\n%s", body)
		}
	})
}
