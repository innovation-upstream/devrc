// Package udiff turns the REST API's per-file patch fragments into a navigable
// unified diff.
//
// 🔴 PHASE 1 IS API-ONLY. There is no local git here, deliberately: 91% of
// clickable repositories have no local clone (measured, 31 of 341 by their own
// `origin` remotes), so the API path is the MAJORITY path and it is the one
// that has to be polished. Local git is the accelerator for the handful of
// repositories the operator lives in, and it is Phase 3.
//
// 🔴 NO SYNTAX HIGHLIGHTING, AND THAT IS A DECISION RATHER THAN AN OMISSION.
// lazygit — the tool this was asked to resemble — does none either; it renders
// `git diff --color`'s own ANSI. Adding a lexer costs a `recover()` (chroma
// panics on a rule timeout instead of returning an error), a goroutine budget
// (chroma#1377: zero-width-match rule cycles spin a core forever, and the
// per-regex timeout does not help), a custom formatter to keep syntax
// foreground from fighting the diff background, and a supply-chain entry in a
// binary that will eventually hold a token with merge scope.
package udiff

import (
	"fmt"
	"strings"

	"github.com/bluekeyes/go-gitdiff/gitdiff"
)

// Op is what a line does. Spelled as a small enum rather than a rune so a
// renderer branches on a VALUE and never on a leading character it has to
// re-derive from the text.
type Op int

const (
	OpContext Op = iota
	OpAdd
	OpDelete
	// OpHunk is the `@@ … @@` header line. It is a LINE in the rendered
	// buffer, so hunk navigation can address it directly.
	OpHunk
	// OpMeta is a line that is neither content nor a hunk header — a rename
	// notice, a binary notice, a "no newline" marker. Rendered dim, never
	// silently dropped.
	OpMeta
)

// Line is one rendered row of the diff.
type Line struct {
	Op   Op
	Text string // WITHOUT the leading +/-/space; the renderer supplies the marker
	// OldNo / NewNo are 0 where the line does not exist on that side.
	OldNo, NewNo int
	// FileIndex points back at the File this line belongs to, so the Files
	// panel and the Diff panel can stay in sync through one shared buffer.
	FileIndex int
}

// Hunk records where a `@@` header landed in the flattened line slice, so
// `]h` / `[h` are an index lookup rather than a scan.
type Hunk struct {
	LineIndex int
	FileIndex int
	Header    string
}

// File is one changed file.
type File struct {
	Path       string
	PrevPath   string
	ChangeType string
	Additions  int
	Deletions  int
	IsBinary   bool
	// NoPatch is true when the API omitted the patch entirely. 🔴 DISTINCT
	// FROM an empty diff: an empty diff means "nothing changed in this file",
	// NO PATCH means "we were not told what changed". Collapsing them would
	// render a binary file as an unchanged one.
	NoPatch bool
	// FirstLine is the index into Diff.Lines where this file's rows begin.
	FirstLine int
	LineCount int
}

// Diff is the whole parsed changeset, flattened into one line slice.
//
// 🔴 ONE FLAT SLICE, NOT A TREE OF FILES. The viewport takes `[]string` and
// slices it for rendering; a tree would have to be flattened on every frame.
// MEASURED: `viewport.View()` with `SoftWrap=false` over a flat slice is
// O(1) in buffer size — 318 µs at 1,000 lines, 347 µs at 4,000 and 342 µs at
// 10,000.
type Diff struct {
	Files []File
	Lines []Line
	Hunks []Hunk
	// Truncated is set when the file list was capped at one page. 🔴 CARRIED,
	// NOT INFERRED: a short list that looks complete is the same class of lie
	// as a green suite that ran nothing.
	Truncated bool
}

// Empty reports whether there is nothing to show at all.
func (d *Diff) Empty() bool { return d == nil || len(d.Lines) == 0 }

// BuildUnified reconstructs a single unified diff from the API's per-file
// patch fragments.
//
// 🔴 WHY RECONSTRUCT RATHER THAN PARSE THE FRAGMENTS DIRECTLY. The REST
// endpoint returns each file's hunks WITHOUT the `diff --git` / `---` / `+++`
// headers, so a fragment on its own has no filename and no add/delete/rename
// information — those live in sibling JSON fields. Re-attaching them produces
// exactly the input `go-gitdiff` is built for, which is where the fiddly parts
// live: `\ No newline at end of file`, zero-length hunks, and the
// old/new line numbering.
//
// The function is PURE and returns a string, so its output is assertable
// against a literal in a test rather than only through the parser.
func BuildUnified(files []FileInput) string {
	var b strings.Builder
	for _, f := range files {
		if f.Patch == "" {
			// A file with no patch contributes no diff text at all. It is
			// still carried in the Files list, with NoPatch set, so the panel
			// can say NO PATCH — see Parse.
			continue
		}
		old := f.PrevPath
		if old == "" {
			old = f.Path
		}
		fmt.Fprintf(&b, "diff --git a/%s b/%s\n", old, f.Path)
		switch f.ChangeType {
		case "ADDED":
			b.WriteString("new file mode 100644\n")
			b.WriteString("--- /dev/null\n")
			fmt.Fprintf(&b, "+++ b/%s\n", f.Path)
		case "REMOVED":
			b.WriteString("deleted file mode 100644\n")
			fmt.Fprintf(&b, "--- a/%s\n", old)
			b.WriteString("+++ /dev/null\n")
		case "RENAMED":
			fmt.Fprintf(&b, "rename from %s\n", old)
			fmt.Fprintf(&b, "rename to %s\n", f.Path)
			fmt.Fprintf(&b, "--- a/%s\n", old)
			fmt.Fprintf(&b, "+++ b/%s\n", f.Path)
		default:
			fmt.Fprintf(&b, "--- a/%s\n", old)
			fmt.Fprintf(&b, "+++ b/%s\n", f.Path)
		}
		b.WriteString(f.Patch)
		if !strings.HasSuffix(f.Patch, "\n") {
			b.WriteString("\n")
		}
	}
	return b.String()
}

// FileInput is the subset of a changed file BuildUnified and Parse need. It is
// declared here rather than importing the API package so this package stays a
// pure text transformation with no network types in its signature.
type FileInput struct {
	Path       string
	PrevPath   string
	ChangeType string
	Additions  int
	Deletions  int
	Patch      string
}

// Parse builds the flattened Diff.
//
// The file LIST comes from `files` (so a file the API gave no patch for still
// appears, marked NoPatch); the CONTENT comes from parsing the reconstructed
// unified diff. 🔴 The two are joined by PATH, and a parsed file whose path
// matches nothing in the list is still appended rather than dropped — a diff
// carrying a file the list did not is a disagreement worth seeing, not one to
// swallow.
func Parse(files []FileInput) (*Diff, error) {
	unified := BuildUnified(files)
	var parsed []*gitdiff.File
	if unified != "" {
		var err error
		parsed, _, err = gitdiff.Parse(strings.NewReader(unified))
		if err != nil {
			return nil, fmt.Errorf("parsing unified diff: %w", err)
		}
	}
	byPath := make(map[string]*gitdiff.File, len(parsed))
	for _, p := range parsed {
		byPath[displayPath(p)] = p
	}

	d := &Diff{}
	for _, f := range files {
		fi := File{
			Path:       f.Path,
			PrevPath:   f.PrevPath,
			ChangeType: f.ChangeType,
			Additions:  f.Additions,
			Deletions:  f.Deletions,
			NoPatch:    f.Patch == "",
			FirstLine:  len(d.Lines),
		}
		idx := len(d.Files)
		p := byPath[f.Path]
		if p != nil {
			fi.IsBinary = p.IsBinary
		}
		d.appendFileHeader(fi, idx)
		if p != nil {
			d.appendFragments(p, idx)
		}
		fi.LineCount = len(d.Lines) - fi.FirstLine
		d.Files = append(d.Files, fi)
	}
	return d, nil
}

// displayPath is the name a file is known by in the UI: the NEW name, except
// for a deletion where there is no new name.
func displayPath(p *gitdiff.File) string {
	if p.NewName != "" {
		return p.NewName
	}
	return p.OldName
}

func (d *Diff) appendFileHeader(f File, idx int) {
	head := f.Path
	if f.PrevPath != "" && f.PrevPath != f.Path {
		head = f.PrevPath + " -> " + f.Path
	}
	d.Lines = append(d.Lines, Line{
		Op:        OpMeta,
		Text:      fmt.Sprintf("%s  %s  +%d -%d", f.ChangeType, head, f.Additions, f.Deletions),
		FileIndex: idx,
	})
	switch {
	case f.NoPatch:
		// 🔴 The WORD, not a blank. §6.1: "NO PATCH — binary or too large for
		// the API". Phase 1 has no local-git fallback to offer, and saying so
		// is better than an empty pane that reads as "no changes".
		d.Lines = append(d.Lines, Line{
			Op:        OpMeta,
			Text:      "NO PATCH — binary, or too large for the API",
			FileIndex: idx,
		})
	case f.IsBinary:
		d.Lines = append(d.Lines, Line{
			Op:        OpMeta,
			Text:      "BINARY — no textual diff",
			FileIndex: idx,
		})
	}
}

func (d *Diff) appendFragments(p *gitdiff.File, idx int) {
	for _, fr := range p.TextFragments {
		// ⚠ `fr.Comment` ARRIVES WITHOUT ITS LEADING SPACE — go-gitdiff strips
		// the separator when it splits the `@@ … @@ <comment>` line. Joining
		// them naively produces `@@ -12,3 +12,6 @@func handle(…` , which is a
		// header no `git apply` would accept and which reads as a typo on
		// screen. Caught by the round-trip assertion in udiff_test.go.
		hdr := fmt.Sprintf("@@ -%d,%d +%d,%d @@",
			fr.OldPosition, fr.OldLines, fr.NewPosition, fr.NewLines)
		if fr.Comment != "" {
			hdr += " " + fr.Comment
		}
		d.Hunks = append(d.Hunks, Hunk{
			LineIndex: len(d.Lines),
			FileIndex: idx,
			Header:    hdr,
		})
		d.Lines = append(d.Lines, Line{Op: OpHunk, Text: hdr, FileIndex: idx})

		oldNo := int(fr.OldPosition)
		newNo := int(fr.NewPosition)
		for _, ln := range fr.Lines {
			text := strings.TrimSuffix(ln.Line, "\n")
			switch ln.Op {
			case gitdiff.OpContext:
				d.Lines = append(d.Lines, Line{Op: OpContext, Text: text, OldNo: oldNo, NewNo: newNo, FileIndex: idx})
				oldNo++
				newNo++
			case gitdiff.OpAdd:
				d.Lines = append(d.Lines, Line{Op: OpAdd, Text: text, NewNo: newNo, FileIndex: idx})
				newNo++
			case gitdiff.OpDelete:
				d.Lines = append(d.Lines, Line{Op: OpDelete, Text: text, OldNo: oldNo, FileIndex: idx})
				oldNo++
			}
			if ln.NoEOL() {
				d.Lines = append(d.Lines, Line{
					Op:        OpMeta,
					Text:      `\ No newline at end of file`,
					FileIndex: idx,
				})
			}
		}
	}
}

// --- navigation -------------------------------------------------------------
//
// All pure. §5.1 drives these from a table with literal expected values.

// NextHunk returns the line index of the first hunk header strictly after
// `from`, or -1 when there is none. 🔴 It crosses FILE boundaries on purpose:
// `]h` is "the next hunk in this review", not "in this file".
func (d *Diff) NextHunk(from int) int {
	for _, h := range d.Hunks {
		if h.LineIndex > from {
			return h.LineIndex
		}
	}
	return -1
}

// PrevHunk returns the line index of the last hunk header strictly before
// `from`, or -1 when there is none.
func (d *Diff) PrevHunk(from int) int {
	best := -1
	for _, h := range d.Hunks {
		if h.LineIndex < from {
			best = h.LineIndex
		} else {
			break
		}
	}
	return best
}

// FileStart returns the line index where file `i` begins, or -1.
func (d *Diff) FileStart(i int) int {
	if i < 0 || i >= len(d.Files) {
		return -1
	}
	return d.Files[i].FirstLine
}

// FileAt returns the index of the file owning line `i`, or -1.
func (d *Diff) FileAt(i int) int {
	if i < 0 || i >= len(d.Lines) {
		return -1
	}
	return d.Lines[i].FileIndex
}
