package udiff

import (
	"strings"
	"testing"
)

// 🔴 EVERY FIXTURE HERE IS SYNTHETIC. This repository is PUBLIC, and captured
// text — anyone's diffs, filenames, message bodies — must not land in it in any
// form, fixtures included. A test needs the SHAPE; these are regenerated.
//
// 🔴 EXPECTED VALUES ARE WRITTEN BY HAND FROM THE UNIFIED-DIFF FORMAT, NEVER
// READ OFF THE IMPLEMENTATION. Where a count is asserted it is a number counted
// from the fixture text above it.

const patchModified = `@@ -12,3 +12,6 @@ func handle(req *Request) error {
 	ctx := build(req)
-	return ctx.run()
+	if ctx.stale() {
+		ctx.refresh()
+	}
+	return ctx.run()
 	log.Debug("done")
`

const patchAdded = `@@ -0,0 +1,3 @@
+package widget
+
+const Name = "widget"
`

const patchRemoved = `@@ -1,2 +0,0 @@
-package legacy
-
`

const patchNoNewline = `@@ -1,2 +1,2 @@
 first
-second
\ No newline at end of file
+second line
\ No newline at end of file
`

const patchTwoHunks = `@@ -3,2 +3,3 @@ type A struct {
 	x int
+	y int
 	z int
@@ -40,2 +41,2 @@ func (a A) Sum() int {
-	return a.x
+	return a.x + a.y
 }
`

func modified() FileInput {
	return FileInput{Path: "pkg/handler.go", ChangeType: "MODIFIED", Additions: 4, Deletions: 1, Patch: patchModified}
}

// --- BuildUnified: assert the TEXT, not just that parsing succeeds -----------

func TestBuildUnifiedReattachesTheHeadersTheRESTAPIOmits(t *testing.T) {
	got := BuildUnified([]FileInput{modified()})
	want := "diff --git a/pkg/handler.go b/pkg/handler.go\n" +
		"--- a/pkg/handler.go\n" +
		"+++ b/pkg/handler.go\n" +
		patchModified
	if got != want {
		t.Errorf("BuildUnified mismatch\n--- got ---\n%s\n--- want ---\n%s", got, want)
	}
}

func TestBuildUnifiedUsesDevNullForAddedAndRemoved(t *testing.T) {
	add := BuildUnified([]FileInput{{Path: "pkg/widget.go", ChangeType: "ADDED", Additions: 3, Patch: patchAdded}})
	if !strings.Contains(add, "--- /dev/null\n+++ b/pkg/widget.go\n") {
		t.Errorf("ADDED header wrong:\n%s", add)
	}
	del := BuildUnified([]FileInput{{Path: "pkg/legacy.go", ChangeType: "REMOVED", Deletions: 2, Patch: patchRemoved}})
	if !strings.Contains(del, "--- a/pkg/legacy.go\n+++ /dev/null\n") {
		t.Errorf("REMOVED header wrong:\n%s", del)
	}
}

func TestBuildUnifiedCarriesTheOldPathForARename(t *testing.T) {
	got := BuildUnified([]FileInput{{
		Path: "pkg/new_name.go", PrevPath: "pkg/old_name.go",
		ChangeType: "RENAMED", Patch: patchModified,
	}})
	for _, want := range []string{
		"diff --git a/pkg/old_name.go b/pkg/new_name.go\n",
		"rename from pkg/old_name.go\n",
		"rename to pkg/new_name.go\n",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %q in:\n%s", want, got)
		}
	}
}

// 🔴 A FILE WITH NO PATCH CONTRIBUTES NO DIFF TEXT — but it must still APPEAR
// in the file list. The two halves are asserted together because either alone
// is satisfied by the wrong implementation.
func TestAFileWithNoPatchContributesNoTextButStillAppears(t *testing.T) {
	in := []FileInput{
		{Path: "assets/logo.png", ChangeType: "MODIFIED"}, // no Patch
		modified(),
	}
	if txt := BuildUnified(in); strings.Contains(txt, "logo.png") {
		t.Errorf("a patchless file leaked into the diff text:\n%s", txt)
	}
	d, err := Parse(in)
	if err != nil {
		t.Fatal(err)
	}
	if len(d.Files) != 2 {
		t.Fatalf("Files = %d, want 2", len(d.Files))
	}
	if !d.Files[0].NoPatch {
		t.Error("the patchless file is not marked NoPatch")
	}
	if d.Files[1].NoPatch {
		t.Error("the file WITH a patch is marked NoPatch")
	}
	// The WORD, not a blank — §6.1.
	if !strings.Contains(renderedText(d), "NO PATCH") {
		t.Error("the NO PATCH word is missing from the rendered lines")
	}
}

func TestAnEmptyChangesetParsesToAnEmptyDiff(t *testing.T) {
	d, err := Parse(nil)
	if err != nil {
		t.Fatal(err)
	}
	if !d.Empty() {
		t.Errorf("Empty() = false over %d lines", len(d.Lines))
	}
	if len(d.Hunks) != 0 {
		t.Errorf("Hunks = %d, want 0", len(d.Hunks))
	}
}

// --- Parse: line ops and numbering ------------------------------------------

func TestParseAssignsOpsAndLineNumbersFromTheHunkHeader(t *testing.T) {
	d, err := Parse([]FileInput{modified()})
	if err != nil {
		t.Fatal(err)
	}
	// Expected rows, counted by hand off `patchModified`. The file header and
	// the `@@` line come first; then the hunk body in order.
	type want struct {
		op    Op
		text  string
		oldNo int
		newNo int
	}
	body := []want{
		{OpContext, "\tctx := build(req)", 12, 12},
		{OpDelete, "\treturn ctx.run()", 13, 0},
		{OpAdd, "\tif ctx.stale() {", 0, 13},
		{OpAdd, "\t\tctx.refresh()", 0, 14},
		{OpAdd, "\t}", 0, 15},
		{OpAdd, "\treturn ctx.run()", 0, 16},
		{OpContext, "\tlog.Debug(\"done\")", 14, 17},
	}
	// Row 0 is the file header (OpMeta); row 1 is the `@@` header (OpHunk).
	if d.Lines[0].Op != OpMeta {
		t.Errorf("line 0 Op = %v, want OpMeta", d.Lines[0].Op)
	}
	if d.Lines[1].Op != OpHunk {
		t.Fatalf("line 1 Op = %v, want OpHunk", d.Lines[1].Op)
	}
	if got, want := d.Lines[1].Text, "@@ -12,3 +12,6 @@ func handle(req *Request) error {"; got != want {
		t.Errorf("hunk header = %q, want %q", got, want)
	}
	for i, w := range body {
		got := d.Lines[2+i]
		if got.Op != w.op || got.Text != w.text || got.OldNo != w.oldNo || got.NewNo != w.newNo {
			t.Errorf("line %d = {op:%v text:%q old:%d new:%d}, want {op:%v text:%q old:%d new:%d}",
				2+i, got.Op, got.Text, got.OldNo, got.NewNo, w.op, w.text, w.oldNo, w.newNo)
		}
	}
}

func TestParsePreservesTheNoNewlineMarker(t *testing.T) {
	d, err := Parse([]FileInput{{Path: "notes.txt", ChangeType: "MODIFIED", Patch: patchNoNewline}})
	if err != nil {
		t.Fatal(err)
	}
	n := strings.Count(renderedText(d), `\ No newline at end of file`)
	// TWO markers in the fixture — one on the deleted line, one on the added
	// line. Asserting "at least one" would pass with the second dropped.
	if n != 2 {
		t.Errorf("no-newline markers = %d, want 2\n%s", n, renderedText(d))
	}
}

// --- hunk navigation --------------------------------------------------------

func twoFileDiff(t *testing.T) *Diff {
	t.Helper()
	d, err := Parse([]FileInput{
		{Path: "pkg/a.go", ChangeType: "MODIFIED", Patch: patchTwoHunks},
		{Path: "pkg/b.go", ChangeType: "ADDED", Patch: patchAdded},
	})
	if err != nil {
		t.Fatal(err)
	}
	return d
}

func TestHunkIndexesPointAtHunkHeaderLines(t *testing.T) {
	d := twoFileDiff(t)
	// THREE hunks, counted from the fixtures: two in a.go, one in b.go.
	if len(d.Hunks) != 3 {
		t.Fatalf("Hunks = %d, want 3", len(d.Hunks))
	}
	for i, h := range d.Hunks {
		if d.Lines[h.LineIndex].Op != OpHunk {
			t.Errorf("hunk %d points at line %d, whose Op is %v", i, h.LineIndex, d.Lines[h.LineIndex].Op)
		}
	}
}

// 🔴 `]h` CROSSES FILE BOUNDARIES ON PURPOSE — it is "the next hunk in this
// review", not "in this file". The third hunk lives in a different file from
// the first two, and reaching it is the assertion.
func TestNextHunkCrossesFileBoundaries(t *testing.T) {
	d := twoFileDiff(t)
	h0, h1, h2 := d.Hunks[0].LineIndex, d.Hunks[1].LineIndex, d.Hunks[2].LineIndex
	if d.Hunks[0].FileIndex == d.Hunks[2].FileIndex {
		t.Fatal("fixture is wrong: the first and last hunks must be in DIFFERENT files")
	}
	if got := d.NextHunk(0); got != h0 {
		t.Errorf("NextHunk(0) = %d, want %d", got, h0)
	}
	if got := d.NextHunk(h0); got != h1 {
		t.Errorf("NextHunk(%d) = %d, want %d", h0, got, h1)
	}
	if got := d.NextHunk(h1); got != h2 {
		t.Errorf("NextHunk(%d) = %d, want %d (a DIFFERENT file)", h1, got, h2)
	}
	if got := d.NextHunk(h2); got != -1 {
		t.Errorf("NextHunk(last) = %d, want -1", got)
	}
}

func TestPrevHunkIsTheMirrorImage(t *testing.T) {
	d := twoFileDiff(t)
	h0, h1, h2 := d.Hunks[0].LineIndex, d.Hunks[1].LineIndex, d.Hunks[2].LineIndex
	if got := d.PrevHunk(h2); got != h1 {
		t.Errorf("PrevHunk(%d) = %d, want %d", h2, got, h1)
	}
	if got := d.PrevHunk(h1); got != h0 {
		t.Errorf("PrevHunk(%d) = %d, want %d", h1, got, h0)
	}
	if got := d.PrevHunk(h0); got != -1 {
		t.Errorf("PrevHunk(first) = %d, want -1", got)
	}
}

func TestSingleHunkFileHasNoNeighbours(t *testing.T) {
	d, err := Parse([]FileInput{{Path: "pkg/only.go", ChangeType: "ADDED", Patch: patchAdded}})
	if err != nil {
		t.Fatal(err)
	}
	h := d.Hunks[0].LineIndex
	if got := d.NextHunk(h); got != -1 {
		t.Errorf("NextHunk = %d, want -1", got)
	}
	if got := d.PrevHunk(h); got != -1 {
		t.Errorf("PrevHunk = %d, want -1", got)
	}
}

func TestFileStartAndFileAtAreInverses(t *testing.T) {
	d := twoFileDiff(t)
	for i := range d.Files {
		start := d.FileStart(i)
		if start < 0 {
			t.Fatalf("FileStart(%d) = %d", i, start)
		}
		if got := d.FileAt(start); got != i {
			t.Errorf("FileAt(FileStart(%d)) = %d", i, got)
		}
	}
	// Out of range in both directions returns -1, not a clamped index — a
	// clamp would make an off-by-one silently address the wrong file.
	if got := d.FileStart(-1); got != -1 {
		t.Errorf("FileStart(-1) = %d, want -1", got)
	}
	if got := d.FileStart(len(d.Files)); got != -1 {
		t.Errorf("FileStart(past end) = %d, want -1", got)
	}
	if got := d.FileAt(len(d.Lines)); got != -1 {
		t.Errorf("FileAt(past end) = %d, want -1", got)
	}
}

func renderedText(d *Diff) string {
	var b strings.Builder
	for _, l := range d.Lines {
		b.WriteString(l.Text)
		b.WriteString("\n")
	}
	return b.String()
}
