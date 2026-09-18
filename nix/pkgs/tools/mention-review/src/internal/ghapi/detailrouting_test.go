package ghapi

import (
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"os"
	"strings"
	"testing"
)

// 🔴 THE ENFORCEMENT THAT TWO COMMENTS CLAIMED AND NEITHER PROVIDED.
//
// `maxDetailRunes`' docstring said the reflecting paths "are enumerated on
// `TestNoErrorPathEverCarriesTheToken`, which fails if a fifth appears
// unledgered", and the pull request said the same. Both were false. That test is
// a hand-written four-row table walked by a `for` loop — it enumerates no call
// site, counts no `Client.detail` invocation and reads no source — and a FIFTH
// unrouted path (`do`'s transport error, which interpolates a redirect target
// the server chose) existed with the suite green at 362 tests. That was the
// THIRD time a completeness sentence in this file's neighbourhood was false.
//
// So this derives the set from the source instead of restating it. It parses
// every non-test file in the package, finds every `&APIError{…}` construction,
// and requires each one's `Detail` to be one of three things:
//
//   - absent, or built only from string literals;
//   - routed through `Client.detail` (redact then clip);
//   - an expression named in `detailLedger` below, with a reason.
//
// 🔴 BE PRECISE ABOUT WHAT THIS DOES AND DOES NOT CLAIM. It cannot tell whether
// an expression carries SERVER text — that judgement is a human one, and writing
// it down is what a ledger entry is for. What it enforces is that no `Detail`
// reaches the UI unbounded and un-examined: a new unrouted expression fails
// until somebody either routes it or states, in a sentence that ships with the
// code, why it needs no routing.

// detailLedger names every `APIError.Detail` sub-expression that is NOT a string
// literal and NOT routed through `Client.detail`, with the reason it needs no
// routing.
//
// 🔴 IT IS PINNED TWO-WAY. An entry naming an expression no construction uses
// fails the test too, so a route that later starts going through `Client.detail`
// cannot leave a stale excuse behind for the next one.
var detailLedger = map[string]string{
	"event": "the caller's own argument, being REJECTED for not being one of " +
		"`ReviewEvents()`; it never came from a response",
	"strings.Join(ReviewEvents(), \", \")": "this package's own three constants",
	"method": "the caller's own argument, being REJECTED for not being one of " +
		"`cfg.MergeMethods()`; it never came from a response",
	"strings.Join(cfg.MergeMethods(), \", \")": "the config package's own constants",
	"read.Terminal": "`TerminalPRState`'s answer, which is `PRStateMerged`, " +
		"`PRStateClosed` or the empty string — a closed vocabulary this package " +
		"owns. The server's `state` string is matched AGAINST those words and " +
		"never echoed: an unrecognised one yields \"\", which this arm does not " +
		"reach",
	"readsSpent(reads, c.pollInterval)": "this package's own count and duration, " +
		"formatted by `readsSpent`",
	"MergeableYes": "this package's own constant, named as the state the refusal " +
		"wanted; the SERVER's word beside it in that same sentence IS routed",
	"owner": "the argv this program was started with",
	"name":  "the argv this program was started with",
	"num":   "the argv this program was started with",
	"repo":  "`owner+\"/\"+name`, composed from argv by the caller",
}

// apiErrorDetail is one `&APIError{…}` construction the scan found.
type apiErrorDetail struct {
	file string
	line int
	// routed is true when the `Detail` expression calls `Client.detail`.
	routed bool
	// leaves are the sub-expressions that are neither string literals nor
	// routed — each must be named in `detailLedger`.
	leaves []string
	// hasDetail is false for a construction with no `Detail` field at all.
	hasDetail bool
}

// scanAPIErrorDetails parses `src` and reports every `APIError` composite
// literal in it.
//
// ⚠ `filename` is used for reporting and to let the caller feed this synthetic
// source, which is how the controls below check the scanner can go both red and
// green.
func scanAPIErrorDetails(filename, src string) ([]apiErrorDetail, error) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, filename, src, 0)
	if err != nil {
		return nil, err
	}
	var out []apiErrorDetail
	ast.Inspect(f, func(n ast.Node) bool {
		lit, ok := n.(*ast.CompositeLit)
		if !ok {
			return true
		}
		id, ok := lit.Type.(*ast.Ident)
		if !ok || id.Name != "APIError" {
			return true
		}
		rec := apiErrorDetail{file: filename, line: fset.Position(lit.Pos()).Line}
		for _, el := range lit.Elts {
			kv, ok := el.(*ast.KeyValueExpr)
			if !ok {
				continue
			}
			key, ok := kv.Key.(*ast.Ident)
			if !ok || key.Name != "Detail" {
				continue
			}
			rec.hasDetail = true
			rec.routed, rec.leaves = walkDetail(fset, kv.Value)
		}
		out = append(out, rec)
		return true
	})
	return out, nil
}

// walkDetail decomposes a `Detail` expression.
//
// String literals and `+` concatenations of them are this program's own prose.
// A `Client.detail(…)` call is the routing, so whatever it wraps is bounded and
// redacted and the walk stops there. `fmt.Sprintf` is transparent: its verbs are
// a literal and its arguments are what matter. Everything else is a LEAF the
// ledger must account for.
func walkDetail(fset *token.FileSet, e ast.Expr) (routed bool, leaves []string) {
	switch v := e.(type) {
	case *ast.BasicLit:
		if v.Kind == token.STRING {
			return false, nil
		}
	case *ast.ParenExpr:
		return walkDetail(fset, v.X)
	case *ast.BinaryExpr:
		if v.Op == token.ADD {
			r1, l1 := walkDetail(fset, v.X)
			r2, l2 := walkDetail(fset, v.Y)
			return r1 || r2, append(l1, l2...)
		}
	case *ast.CallExpr:
		if sel, ok := v.Fun.(*ast.SelectorExpr); ok {
			if _, isIdent := sel.X.(*ast.Ident); isIdent && sel.Sel.Name == "detail" {
				return true, nil
			}
			if x, isIdent := sel.X.(*ast.Ident); isIdent && x.Name == "fmt" && sel.Sel.Name == "Sprintf" {
				for _, arg := range v.Args {
					r, l := walkDetail(fset, arg)
					routed = routed || r
					leaves = append(leaves, l...)
				}
				return routed, leaves
			}
		}
	}
	return false, []string{renderExpr(fset, e)}
}

func renderExpr(fset *token.FileSet, e ast.Expr) string {
	var b strings.Builder
	if err := printer.Fprint(&b, fset, e); err != nil {
		return "<unprintable>"
	}
	return strings.Join(strings.Fields(b.String()), " ")
}

// packageSources returns the package's own non-test Go files.
func packageSources(t *testing.T) map[string]string {
	t.Helper()
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for _, e := range entries {
		n := e.Name()
		if !strings.HasSuffix(n, ".go") || strings.HasSuffix(n, "_test.go") {
			continue
		}
		body, err := os.ReadFile(n)
		if err != nil {
			t.Fatal(err)
		}
		out[n] = string(body)
	}
	return out
}

// 🔴 THE GUARD. Every `APIError.Detail` this package constructs is a literal,
// routed, or ledgered — derived from the source rather than from a list somebody
// remembered to update.
func TestEveryAPIErrorDetailIsRoutedOrLedgered(t *testing.T) {
	srcs := packageSources(t)
	var all []apiErrorDetail
	for name, body := range srcs {
		found, err := scanAPIErrorDetails(name, body)
		if err != nil {
			t.Fatalf("parsing %s: %v", name, err)
		}
		all = append(all, found...)
	}

	used := map[string]bool{}
	for _, rec := range all {
		for _, leaf := range rec.leaves {
			used[leaf] = true
			if _, ok := detailLedger[leaf]; ok {
				continue
			}
			t.Errorf("%s:%d builds an APIError.Detail out of %s, which is neither a "+
				"string literal nor routed through `Client.detail`.\n"+
				"Route it — `Detail: c.detail(…)` — or add it to `detailLedger` with "+
				"the reason it needs no routing. An unrouted detail is unbounded: the "+
				"server decides how long the card is.",
				rec.file, rec.line, leaf)
		}
	}
	// 🔴 THE LEDGER IS PINNED THE OTHER WAY TOO. A stale excuse is an excuse the
	// next unrouted expression can be filed under by accident.
	for leaf := range detailLedger {
		if !used[leaf] {
			t.Errorf("`detailLedger` names %s, which no APIError.Detail in this package "+
				"builds. Delete the entry — a reason kept past the code it excused is a "+
				"reason nobody will re-read.", leaf)
		}
	}

	// 🔴 THE SCAN MUST HAVE OBSERVED SOMETHING, AND IT MUST HAVE OBSERVED BOTH
	// ARMS. A walk that read no files, or that matched no construction, reports
	// zero violations and reads as a clean bill of health — the reassuring zero.
	if len(srcs) < 3 {
		t.Fatalf("the scan read %d non-test source files — a broken glob, not a clean result",
			len(srcs))
	}
	var routed, ledgered, files int
	seen := map[string]bool{}
	for _, rec := range all {
		if !seen[rec.file] {
			seen[rec.file] = true
			files++
		}
		if rec.routed {
			routed++
		}
		if len(rec.leaves) > 0 {
			ledgered++
		}
	}
	if len(all) < 10 {
		t.Fatalf("the scan found %d APIError constructions — this package builds many "+
			"more than that, so the matcher is wired to nothing", len(all))
	}
	if routed == 0 {
		t.Error("the scan classified NOTHING as routed — the `Client.detail` arm has " +
			"never executed, so every routed path is passing for the wrong reason")
	}
	if ledgered == 0 {
		t.Error("the scan classified NOTHING as ledgered — the leaf arm has never " +
			"executed, so an unrouted detail would not be recognised as one")
	}
	t.Logf("scanned %d source files, %d APIError constructions across %d files: "+
		"%d routed, %d ledgered", len(srcs), len(all), files, routed, ledgered)
}

// 🔴 VALIDATE THE INSTRUMENT BEFORE READING ITS VERDICT — BOTH DIRECTIONS, ON
// SYNTHETIC SOURCE, SO NEITHER CONTROL DEPENDS ON WHAT THE PACKAGE HAPPENS TO
// LOOK LIKE TODAY.
//
// The dangerous failure is a scanner that matches nothing: it reports a clean
// zero for a package full of unrouted details, and the test above would read as
// enforcement while providing none.
func TestTheDetailScannerCanGoRedAndGreen(t *testing.T) {
	const bad = `package p
func f(c *Client, err error) error {
	return &APIError{State: AuthOther, Detail: "could not reach it: " + err.Error()}
}`
	got, parseErr := scanAPIErrorDetails("bad.go", bad)
	if parseErr != nil {
		t.Fatal(parseErr)
	}
	if len(got) != 1 {
		t.Fatalf("the scanner found %d APIError constructions in a source with exactly "+
			"one — it is matching on something other than the type name", len(got))
	}
	if got[0].routed {
		t.Error("an unrouted detail was classified as routed")
	}
	if len(got[0].leaves) != 1 || got[0].leaves[0] != "err.Error()" {
		t.Errorf("leaves = %q, want exactly [\"err.Error()\"] — the walk is not reaching "+
			"the sub-expression that carries the text", got[0].leaves)
	}

	const good = `package p
import "fmt"
func f(c *Client, err error, owner string) error {
	if err != nil {
		return &APIError{State: AuthOther, Detail: c.detail("unreadable: " + err.Error())}
	}
	if owner == "" {
		return &APIError{State: AuthOther, Detail: "refusing to act on nothing"}
	}
	return &APIError{State: AuthOther, Detail: fmt.Sprintf("%s is not visible", c.detail(owner))}
}`
	ok, parseErr := scanAPIErrorDetails("good.go", good)
	if parseErr != nil {
		t.Fatal(parseErr)
	}
	if len(ok) != 3 {
		t.Fatalf("the scanner found %d constructions, want 3", len(ok))
	}
	for i, rec := range ok {
		if len(rec.leaves) != 0 {
			t.Errorf("construction %d was flagged for %q — a routed or literal detail "+
				"must produce no leaves, or the guard is noise nobody will read",
				i, rec.leaves)
		}
	}
	if !ok[0].routed || ok[1].routed || !ok[2].routed {
		t.Errorf("routed = %v/%v/%v, want true/false/true — `Client.detail` inside a "+
			"`fmt.Sprintf` argument must still count as routing",
			ok[0].routed, ok[1].routed, ok[2].routed)
	}

	// A construction with NO `Detail` is not a violation, and must not be counted
	// as one — the no-token error carries a state and nothing else.
	none, parseErr := scanAPIErrorDetails("none.go", `package p
func f() error { return &APIError{State: AuthNoToken} }`)
	if parseErr != nil {
		t.Fatal(parseErr)
	}
	if len(none) != 1 || none[0].hasDetail || len(none[0].leaves) != 0 {
		t.Errorf("a construction with no Detail was read as %+v", none)
	}
}
