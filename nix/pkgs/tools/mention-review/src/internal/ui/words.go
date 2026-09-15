package ui

import (
	"fmt"

	"charm.land/lipgloss/v2"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
)

// 🔴 EVERY MEANING-BEARING STATE IS A WORD.
//
// This file is the ONE place a state becomes text. It exists so that claim is
// mechanically checkable rather than a habit: `words_test.go` walks
// `MeaningBearingStates()` — which is this file's own ledger — renders each
// one with ALL COLOUR REMOVED, and asserts a word survives. The day someone
// encodes approval as a green dot, that test goes red.
//
// The constraint is the operator's: the red/yellow/green severity circles
// render as one indistinguishable glyph in his font, so colour cannot carry
// meaning. Colour here is always decoration on top of text that already says
// the thing.

// StateWord pairs the word with the style that decorates it. The word is the
// payload; the style is never load-bearing.
type StateWord struct {
	Word  string
	Style lipgloss.Style
}

// Render applies the decoration.
func (s StateWord) Render() string { return s.Style.Render(s.Word) }

// ReviewWord maps `reviewDecision` onto a word.
//
// 🔴 THE EMPTY CASE IS `NONE`, NOT A BLANK. The server returns null for a PR
// nobody has reviewed — measured, on a real PR — and a blank cell reads as
// "this panel is broken" rather than "nobody has reviewed it".
func ReviewWord(decision string) StateWord {
	switch decision {
	case "APPROVED":
		return StateWord{"APPROVED", styGood}
	case "CHANGES_REQUESTED":
		return StateWord{"CHANGES", styBad}
	case "REVIEW_REQUIRED":
		return StateWord{"REQUIRED", styWarn}
	case "":
		return StateWord{"NONE", styDim}
	}
	return StateWord{decision, styDim}
}

// MergeWord maps `mergeable` + `mergeStateStatus` onto a word.
//
// ⚠ `UNKNOWN` IS A REAL AND COMMON ANSWER, NOT AN ERROR. GitHub computes
// mergeability lazily, so the first read of a PR frequently returns UNKNOWN —
// measured on a merged PR in this repo. Rendering it as CLEAN would be a
// guess presented as a fact; rendering it as CONFLICT would be alarming and
// wrong. It gets its own word.
func MergeWord(mergeable, stateStatus string, merged bool) StateWord {
	if merged {
		return StateWord{"MERGED", styMerged}
	}
	switch mergeable {
	case "CONFLICTING":
		return StateWord{"CONFLICT", styBad}
	case "UNKNOWN", "":
		return StateWord{"UNKNOWN", styDim}
	}
	// MERGEABLE — the finer detail lives in mergeStateStatus.
	switch stateStatus {
	case "CLEAN":
		return StateWord{"CLEAN", styGood}
	case "BLOCKED":
		return StateWord{"BLOCKED", styBad}
	case "BEHIND":
		return StateWord{"BEHIND", styWarn}
	case "UNSTABLE":
		return StateWord{"UNSTABLE", styWarn}
	case "DRAFT":
		return StateWord{"DRAFT", styDim}
	case "DIRTY":
		return StateWord{"CONFLICT", styBad}
	case "HAS_HOOKS":
		return StateWord{"HOOKS", styWarn}
	case "UNKNOWN", "":
		return StateWord{"MERGEABLE", styGood}
	}
	return StateWord{stateStatus, styDim}
}

// ChecksWord maps the status-check rollup onto a word. 🔴 THE COUNT IS PART OF
// THE WORD — "1 FAILING" tells the operator how much is wrong; a red dot does
// not.
func ChecksWord(c ghapi.CheckSummary) StateWord {
	switch {
	case c.Failing > 0:
		return StateWord{fmt.Sprintf("%d FAILING", c.Failing), styBad}
	case c.Pending > 0:
		return StateWord{fmt.Sprintf("%d PENDING", c.Pending), styWarn}
	case c.Total > 0 && c.State == "SUCCESS":
		return StateWord{fmt.Sprintf("%d PASSING", c.Total), styGood}
	case c.Total > 0:
		return StateWord{fmt.Sprintf("%d CHECKS", c.Total), styDim}
	}
	// 🔴 NO CHECKS, spelled out. An empty cell is indistinguishable from a
	// panel that failed to populate, and this repo's own CI posts nothing at
	// all on a run that hits its task timeout — a state the operator has to be
	// able to read off the screen.
	return StateWord{"NO CHECKS", styDim}
}

// PRStateWord maps a pull request's own lifecycle state onto a word.
func PRStateWord(state string, isDraft bool) StateWord {
	if isDraft {
		return StateWord{"DRAFT", styDim}
	}
	switch state {
	case "OPEN":
		return StateWord{"OPEN", styGood}
	case "MERGED":
		return StateWord{"MERGED", styMerged}
	case "CLOSED":
		return StateWord{"CLOSED", styBad}
	}
	return StateWord{state, styDim}
}

// ThreadsWord maps the review-thread counts onto a word.
func ThreadsWord(t ghapi.ThreadSummary) StateWord {
	switch {
	case t.Unresolved > 0:
		return StateWord{fmt.Sprintf("%d UNRESOLVED", t.Unresolved), styBad}
	case t.Total > 0:
		return StateWord{fmt.Sprintf("%d RESOLVED", t.Total), styGood}
	}
	return StateWord{"NO THREADS", styDim}
}

// FileWord is the one-letter change marker plus its style. 🔴 A LETTER, NOT A
// COLOURED BULLET — `M`/`A`/`D`/`R` are the words here, and they survive with
// colour stripped.
func FileWord(changeType string) StateWord {
	switch changeType {
	case "ADDED":
		return StateWord{"A", styGood}
	case "REMOVED":
		return StateWord{"D", styBad}
	case "MODIFIED", "CHANGED":
		return StateWord{"M", styWarn}
	case "RENAMED":
		return StateWord{"R", styAccent}
	case "COPIED":
		return StateWord{"C", styAccent}
	}
	return StateWord{"?", styDim}
}

// LoadWord names where the fetch has got to. Every one of these is a state the
// operator can be looking at, so every one is a word.
type LoadState int

const (
	LoadLoading LoadState = iota
	LoadReady
	LoadFailed
)

func (l LoadState) Word() StateWord {
	switch l {
	case LoadLoading:
		return StateWord{"LOADING", styWarn}
	case LoadReady:
		return StateWord{"READY", styGood}
	}
	return StateWord{"FAILED", styBad}
}

// ModeWord names which key table is live.
//
// 🔴 THE MODE IS A MEANING-BEARING STATE, AND IT IS THE ONE WHERE GETTING IT
// WRONG IS EXPENSIVE. An operator who believes they are browsing while the app
// is waiting on y/N will press `y` for some other reason. A border colour
// cannot carry that, and the operator's font renders the severity circles as
// one indistinguishable glyph anyway.
func ModeWord(m Mode) StateWord {
	switch m {
	case ModeCompose:
		return StateWord{m.Word(), styWarn}
	case ModeConfirm:
		return StateWord{m.Word(), styBad}
	}
	return StateWord{m.Word(), styDim}
}

// MeaningBearingStates is the LEDGER `words_test.go` walks.
//
// 🔴 TWO-WAY, AND THAT IS THE POINT. The test asserts every entry renders a
// word with colour removed AND that the ledger covers every constructor in this
// file. A state word that exists but is not listed here is a state nobody
// checked; a listed one that no longer renders is a dead entry. Both fail.
//
// ⚠ THE FIXTURES ARE DELIBERATELY NOT THE CONSTANTS THE ASSERTIONS NAME. The
// check counts are 3 and 7, never 0 or 1, so a mutant that hardcodes a literal
// cannot produce the expected value by accident.
func MeaningBearingStates() []StateWord {
	return []StateWord{
		ReviewWord("APPROVED"),
		ReviewWord("CHANGES_REQUESTED"),
		ReviewWord("REVIEW_REQUIRED"),
		ReviewWord(""),

		MergeWord("MERGEABLE", "CLEAN", false),
		MergeWord("MERGEABLE", "BLOCKED", false),
		MergeWord("MERGEABLE", "BEHIND", false),
		MergeWord("MERGEABLE", "UNSTABLE", false),
		MergeWord("CONFLICTING", "DIRTY", false),
		MergeWord("UNKNOWN", "", false),
		MergeWord("MERGEABLE", "CLEAN", true),

		ChecksWord(ghapi.CheckSummary{State: "FAILURE", Total: 7, Failing: 3}),
		ChecksWord(ghapi.CheckSummary{State: "PENDING", Total: 7, Pending: 3}),
		ChecksWord(ghapi.CheckSummary{State: "SUCCESS", Total: 7}),
		ChecksWord(ghapi.CheckSummary{}),

		PRStateWord("OPEN", false),
		PRStateWord("OPEN", true),
		PRStateWord("MERGED", false),
		PRStateWord("CLOSED", false),

		ThreadsWord(ghapi.ThreadSummary{Total: 7, Unresolved: 3}),
		ThreadsWord(ghapi.ThreadSummary{Total: 7}),
		ThreadsWord(ghapi.ThreadSummary{}),

		FileWord("ADDED"),
		FileWord("REMOVED"),
		FileWord("MODIFIED"),
		FileWord("RENAMED"),
		FileWord("COPIED"),

		LoadLoading.Word(),
		LoadReady.Word(),
		LoadFailed.Word(),

		ModeWord(ModeBrowse),
		ModeWord(ModeCompose),
		ModeWord(ModeConfirm),

		{Word: ghapi.AuthNoToken.Word(), Style: styBad},
		{Word: ghapi.AuthRejected.Word(), Style: styBad},
		{Word: ghapi.AuthNotFound.Word(), Style: styBad},
		{Word: ghapi.AuthRateLimited.Word(), Style: styWarn},
	}
}
