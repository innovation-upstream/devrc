package ghapi

import (
	"strings"
	"time"
)

// The `mergeable` enum, as WORDS, in one place.
//
// 🔴 `UNKNOWN` IS NOT A BLOCKER AND IS NOT A GO-AHEAD — IT IS "ASK AGAIN".
// GitHub computes mergeability asynchronously and resets it to null/UNKNOWN
// every time the base branch moves. Reading that as a conflict refuses merges
// that are fine; reading it as MERGEABLE dispatches a merge into the recompute
// window, which is the failure this vocabulary exists to make legible.
const (
	MergeableYes     = "MERGEABLE"
	MergeableNo      = "CONFLICTING"
	MergeableUnknown = "UNKNOWN"
)

// NormalizeMergeable maps what the server sent onto that vocabulary.
//
// 🔴 AN EMPTY STRING IS `UNKNOWN`. GraphQL answers null while the computation
// is in flight and the decoder turns null into "", so "" and "UNKNOWN" are the
// SAME condition arriving by two routes — a predicate that handled only the
// spelled one would let the in-flight case straight through to a merge.
func NormalizeMergeable(s string) string {
	t := strings.ToUpper(strings.TrimSpace(s))
	if t == "" {
		return MergeableUnknown
	}
	return t
}

// Kind is what `issueOrPullRequest`'s `__typename` answered.
//
// 🔴 THE SERVER ANSWERS THIS, NOT US. `mention-open.py` builds `/pull/{id}` for
// every mention and lets github.com redirect, because it structurally cannot
// know whether `#N` is an issue or a PR — and it must not find out, because
// that module carries a test-pinned property that THE CLICK PATH MAKES NO
// NETWORK CALL. Resolution is local and kind-blind; the child asks the server.
type Kind string

const (
	KindPullRequest Kind = "PullRequest"
	KindIssue       Kind = "Issue"
)

// Snapshot is one immutable read of one reference. It is replaced wholesale,
// never mutated (§3.2).
type Snapshot struct {
	// 🔴 ViewerLogin is the §10.2 multi-account mitigation and it is NOT
	// optional. cli/cli#14370: the OS keyring is not partitioned by account, so
	// `gh auth token --secure-storage` can return a token for a DIFFERENT
	// account than the config's active one — and this host's hosts.yml carries
	// two github.com users. For a read-only tool that is a curiosity; for one
	// that can approve and merge it is the difference between approving as
	// yourself and approving as somebody else, invisibly. It rides the same
	// round trip, so it costs nothing.
	ViewerLogin string

	Kind Kind
	Repo string
	Num  int

	Title     string
	State     string
	URL       string
	Body      string
	Author    string
	CreatedAt time.Time
	UpdatedAt time.Time

	// Pull-request only.
	IsDraft          bool
	Merged           bool
	BaseRef          string
	HeadRef          string
	Additions        int
	Deletions        int
	ChangedFiles     int
	Mergeable        string // MERGEABLE | CONFLICTING | UNKNOWN
	MergeStateStatus string
	ReviewDecision   string // "" when the server returned null
	Commits          []Commit
	Files            []File
	Reviews          []Review
	Threads          ThreadSummary
	Checks           CheckSummary

	// FilesTruncated is true when the PR has more changed files than one page
	// carries. 🔴 REPORTED AS A WORD, never silently dropped — a file list that
	// is quietly short is the same class of lie as a green suite that ran
	// nothing.
	FilesTruncated   bool
	CommitsTruncated bool
}

type Commit struct {
	OID      string
	Abbrev   string
	Headline string
	Author   string
	When     time.Time
}

type File struct {
	Path       string
	Additions  int
	Deletions  int
	ChangeType string // ADDED | MODIFIED | REMOVED | RENAMED | COPIED | CHANGED
	// Patch is the unified-diff fragment from the REST files endpoint. Empty
	// when the API omitted it (binary, or too large) — §6.1 renders that as
	// `NO PATCH`, never as an empty diff.
	Patch        string
	PreviousPath string
}

type Review struct {
	Author string
	State  string // APPROVED | CHANGES_REQUESTED | COMMENTED | DISMISSED | PENDING
	When   time.Time
}

type ThreadSummary struct {
	Total      int
	Unresolved int
}

type CheckSummary struct {
	// State is the rollup: SUCCESS | FAILURE | PENDING | ERROR | EXPECTED, or
	// "" when there is no rollup at all.
	State   string
	Total   int
	Failing int
	Pending int
}
