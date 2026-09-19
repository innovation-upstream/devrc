package ui

import (
	"regexp"
	"strings"
	"testing"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// 🔴 LAYER 3(d) — MEANING IS NEVER COLOUR-ONLY.
//
// Every meaning-bearing state is rendered with ALL COLOUR REMOVED and the state
// WORD must survive. This goes red the day someone encodes approval as a green
// dot. It is the operator's font constraint turned into a gate: the
// red/yellow/green severity circles render as ONE indistinguishable glyph in
// his font, so colour cannot carry meaning.
//
// ⚠ MECHANISM NOTE, BECAUSE THE OBVIOUS SPELLING IS A DELETED API. Every guide
// still says `lipgloss.SetColorProfile(termenv.Ascii)` — Lip Gloss v2 REMOVED
// the whole `Renderer` concept, including `SetColorProfile`, `NewRenderer` and
// `ColorProfile`. Stripping the SGR sequences out of the rendered bytes is
// used instead: it is the definition of "colour removed", it cannot silently
// no-op the way a mis-set profile can, and `TestTheColourStripperActuallyStrips`
// below proves it moves.

// sgr matches every ANSI escape sequence lipgloss can emit.
var sgr = regexp.MustCompile(`\x1b\[[0-9;:]*[a-zA-Z]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)`)

func stripANSI(s string) string { return sgr.ReplaceAllString(s, "") }

// 🔴 VALIDATE THE INSTRUMENT BEFORE READING ITS VERDICT.
//
// If `stripANSI` stripped nothing, every assertion below would find its word
// anyway — because the word was there all along — and the suite would pass
// while measuring NOTHING. This asserts the pair: styled output DOES carry
// escape bytes, and stripping removes them while leaving the text.
func TestTheColourStripperActuallyStrips(t *testing.T) {
	styled := styGood.Render("APPROVED")
	if !strings.Contains(styled, "\x1b[") {
		t.Fatal("the styled render carries NO escape bytes — this test cannot " +
			"tell colour-removed from coloured, so every guard below is vacuous")
	}
	bare := stripANSI(styled)
	if strings.Contains(bare, "\x1b") {
		t.Errorf("stripANSI left escape bytes: %q", bare)
	}
	if bare != "APPROVED" {
		t.Errorf("stripANSI = %q, want %q", bare, "APPROVED")
	}
	// The NEGATIVE control: a plain string must come back unchanged, so the
	// stripper cannot be a function that deletes text generally.
	if got := stripANSI("1 FAILING"); got != "1 FAILING" {
		t.Errorf("stripANSI mangled plain text: %q", got)
	}
}

// 🔴 THE LEDGER WALK. Every entry in MeaningBearingStates() must render at
// least one WORD with colour removed.
func TestEveryMeaningBearingStateRendersAWordWithColourRemoved(t *testing.T) {
	states := MeaningBearingStates()
	if len(states) == 0 {
		t.Fatal("the ledger is EMPTY — this guard would pass wired to nothing")
	}
	word := regexp.MustCompile(`[A-Z]{2,}`)
	for _, s := range states {
		bare := stripANSI(s.Render())
		if strings.TrimSpace(bare) == "" {
			t.Errorf("state %q renders NOTHING with colour removed", s.Word)
			continue
		}
		// A single letter is a legitimate word here — `M`/`A`/`D`/`R` are the
		// Files panel's markers — so the pattern allows either an uppercase
		// run or a lone uppercase letter, and nothing else.
		if !word.MatchString(bare) && !regexp.MustCompile(`^[A-Z?]$`).MatchString(strings.TrimSpace(bare)) {
			t.Errorf("state %q renders %q with colour removed — no WORD survives",
				s.Word, bare)
		}
	}
}

// 🔴 MUTATION CONTROL, NAMED IN ADVANCE (§5.5): "replace the word APPROVED with
// a green glyph" must fail with THIS guard's own error.
//
// It is run here as a POSITIVE CONTROL rather than left to a manual mutation,
// so the guard's ability to catch that specific defect is re-proved on every
// run instead of once by hand.
func TestAGlyphOnlyStateIsCaughtByTheGuard(t *testing.T) {
	glyph := StateWord{Word: "●", Style: styGood}
	bare := strings.TrimSpace(stripANSI(glyph.Render()))
	word := regexp.MustCompile(`[A-Z]{2,}`)
	single := regexp.MustCompile(`^[A-Z?]$`)
	if word.MatchString(bare) || single.MatchString(bare) {
		t.Fatalf("a bare glyph %q passed the word test — the guard is walkable "+
			"by encoding state as colour plus a symbol, which is exactly what "+
			"it exists to prevent", bare)
	}
}

// 🔴 THE DIFF ROWS ARE MEANING-BEARING STATES TOO, AND NOTHING GUARDED THEM.
//
// MEASURED: deleting the `"+"` from `plainDiffLine`'s add case — which makes an
// added line and a context line IDENTICAL once colour is stripped, i.e. the
// exact colour-only encoding §5.3(d) exists to forbid — SURVIVED the whole
// suite. `MeaningBearingStates()` covered the Overview's words and not one row
// of the pane the tool is FOR.
//
// A guard whose description says "every meaning-bearing state" while its
// implementation inspects one surface is the shape this repo calls "reading as
// coverage while providing none". This closes it.
func TestEveryDiffRowKindIsDistinguishableWithColourRemoved(t *testing.T) {
	// The same text on every row, so the ONLY thing that can distinguish them
	// is the marker. If the marker goes, these collapse onto each other.
	const body = "ctx := build(req)"
	rows := map[udiff.Op]string{
		udiff.OpAdd:     "+",
		udiff.OpDelete:  "-",
		udiff.OpContext: " ",
	}

	seen := map[string]udiff.Op{}
	for op, wantMarker := range rows {
		bare := stripANSI(diffLineStyle(op).Render(plainDiffLine(udiff.Line{Op: op, Text: body})))
		if !strings.HasPrefix(bare, wantMarker) {
			t.Errorf("op %v renders %q with colour removed, want it to start with %q",
				op, bare, wantMarker)
		}
		if prev, dup := seen[bare]; dup {
			t.Errorf("ops %v and %v render IDENTICALLY with colour removed (%q) — "+
				"the difference is carried by colour alone, which the operator's "+
				"font cannot show", prev, op, bare)
		}
		seen[bare] = op
	}
	if len(seen) != len(rows) {
		t.Errorf("%d distinct rows out of %d", len(seen), len(rows))
	}
}

// --- the specific mappings, asserted against literals ------------------------
//
// 🔴 NOT DERIVED FROM THE IMPLEMENTATION. Each expected string is written from
// what the operator must be able to read off the screen.

func TestReviewDecisionWords(t *testing.T) {
	for in, want := range map[string]string{
		"APPROVED":          "APPROVED",
		"CHANGES_REQUESTED": "CHANGES",
		"REVIEW_REQUIRED":   "REQUIRED",
		"":                  "NONE", // 🔴 the null case is a WORD, not a blank
	} {
		if got := ReviewWord(in).Word; got != want {
			t.Errorf("ReviewWord(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestMergeWords(t *testing.T) {
	cases := []struct {
		mergeable, state string
		merged           bool
		want             string
	}{
		{"MERGEABLE", "CLEAN", false, "CLEAN"},
		{"MERGEABLE", "BLOCKED", false, "BLOCKED"},
		{"MERGEABLE", "BEHIND", false, "BEHIND"},
		{"MERGEABLE", "UNSTABLE", false, "UNSTABLE"},
		{"CONFLICTING", "DIRTY", false, "CONFLICT"},
		// ⚠ UNKNOWN IS A REAL AND COMMON ANSWER — GitHub computes mergeability
		// lazily and the first read of a PR frequently returns it. Rendering it
		// as CLEAN would be a guess presented as a fact.
		{"UNKNOWN", "UNKNOWN", false, "UNKNOWN"},
		{"", "", false, "UNKNOWN"},
		// Merged wins over everything, including a stale CONFLICTING.
		{"CONFLICTING", "DIRTY", true, "MERGED"},

		// 🔴 THE ENUM IS READ THROUGH `ghapi.NormalizeMergeable`, SO CASE AND
		// SURROUNDING SPACE FOLD. This switch used to open-code the predicate and
		// disagreed with the merge gate's copy on exactly these inputs.
		//
		// ⚠ THE `stateStatus` COLUMN IS CHOSEN SO THE TWO BRANCHES CANNOT AGREE
		// BY ACCIDENT. `DIRTY` also renders CONFLICT, so pairing a lower-case
		// `conflicting` with it would pass whether or not the fold happened;
		// `CLEAN` renders CLEAN, a word neither expectation below can reach any
		// other way.
		{" conflicting ", "CLEAN", false, "CONFLICT"},
		{" unknown ", "CLEAN", false, "UNKNOWN"},
		{"", "CLEAN", false, "UNKNOWN"},
		// POSITIVE CONTROL on the same column: a value that normalises to
		// MERGEABLE still falls through to `stateStatus`, so the two rows above
		// are the fold and not a blanket capture of everything paired with CLEAN.
		{" mergeable ", "CLEAN", false, "CLEAN"},
		// And a word the vocabulary does not know still falls through, rather
		// than being swept into UNKNOWN by the normalisation.
		{"SOMETHING_NEW", "BEHIND", false, "BEHIND"},
	}
	for _, c := range cases {
		if got := MergeWord(c.mergeable, c.state, c.merged).Word; got != c.want {
			t.Errorf("MergeWord(%q,%q,%v) = %q, want %q",
				c.mergeable, c.state, c.merged, got, c.want)
		}
	}
}

// 🔴 THE COUNT IS PART OF THE WORD. "1 FAILING" tells the operator how much is
// wrong; a red dot does not.
//
// ⚠ THE FIXTURE COUNTS ARE 3 AND 7, NEVER 0 OR 1. A fixture that can only ever
// produce the constant's own value cannot see a mutant that hardcodes the
// literal — feeding a value the constant CANNOT equal is the control.
func TestChecksWordCarriesTheCount(t *testing.T) {
	cases := []struct {
		in   ghapi.CheckSummary
		want string
	}{
		{ghapi.CheckSummary{State: "FAILURE", Total: 7, Failing: 3}, "3 FAILING"},
		{ghapi.CheckSummary{State: "PENDING", Total: 7, Pending: 3}, "3 PENDING"},
		{ghapi.CheckSummary{State: "SUCCESS", Total: 7}, "7 PASSING"},
		{ghapi.CheckSummary{State: "EXPECTED", Total: 7}, "7 CHECKS"},
		// 🔴 NO CHECKS, spelled out. An empty cell is indistinguishable from a
		// panel that failed to populate — and this repo's own CI posts NOTHING
		// at all on a run that hits its task timeout, a state the operator has
		// to be able to read off the screen.
		{ghapi.CheckSummary{}, "NO CHECKS"},
	}
	for _, c := range cases {
		if got := ChecksWord(c.in).Word; got != c.want {
			t.Errorf("ChecksWord(%+v) = %q, want %q", c.in, got, c.want)
		}
	}
	// FAILING outranks PENDING: a PR with both must not read as merely pending.
	both := ChecksWord(ghapi.CheckSummary{Total: 7, Failing: 3, Pending: 2}).Word
	if both != "3 FAILING" {
		t.Errorf("failing+pending = %q, want the FAILING word to win", both)
	}
}

func TestThreadsWordCarriesTheCount(t *testing.T) {
	if got := ThreadsWord(ghapi.ThreadSummary{Total: 7, Unresolved: 3}).Word; got != "3 UNRESOLVED" {
		t.Errorf("= %q", got)
	}
	if got := ThreadsWord(ghapi.ThreadSummary{Total: 7}).Word; got != "7 RESOLVED" {
		t.Errorf("= %q", got)
	}
	if got := ThreadsWord(ghapi.ThreadSummary{}).Word; got != "NO THREADS" {
		t.Errorf("= %q", got)
	}
}

func TestFileWordsAreLettersNotBullets(t *testing.T) {
	for in, want := range map[string]string{
		"ADDED": "A", "REMOVED": "D", "MODIFIED": "M",
		"RENAMED": "R", "COPIED": "C", "CHANGED": "M",
	} {
		if got := FileWord(in).Word; got != want {
			t.Errorf("FileWord(%q) = %q, want %q", in, got, want)
		}
	}
	// An unrecognised change type gets `?`, not a silent blank and not a
	// guess at MODIFIED — a new GitHub status must not render as an ordinary
	// edit.
	if got := FileWord("TELEPORTED").Word; got != "?" {
		t.Errorf("FileWord(unknown) = %q, want %q", got, "?")
	}
}

func TestDraftOutranksTheLifecycleState(t *testing.T) {
	if got := PRStateWord("OPEN", true).Word; got != "DRAFT" {
		t.Errorf("a draft PR reads as %q", got)
	}
	if got := PRStateWord("OPEN", false).Word; got != "OPEN" {
		t.Errorf("= %q", got)
	}
}

// The auth states are words too, and their hints name the FIX rather than
// restating the state.
func TestAuthStateWordsAreDistinguishable(t *testing.T) {
	if ghapi.AuthNoToken.Word() == ghapi.AuthRejected.Word() {
		t.Fatal("NO TOKEN and TOKEN REJECTED render the same word — the whole " +
			"point is that the FIXES differ")
	}
	if !strings.Contains(ghapi.AuthNoToken.Hint(), "gh auth login") {
		t.Errorf("NO TOKEN hint does not name the fix: %q", ghapi.AuthNoToken.Hint())
	}
	if strings.Contains(ghapi.AuthRejected.Hint(), "gh auth login") &&
		!strings.Contains(ghapi.AuthRejected.Hint(), "refused") {
		t.Errorf("TOKEN REJECTED hint does not say the token was refused: %q",
			ghapi.AuthRejected.Hint())
	}
}
