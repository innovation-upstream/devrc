package ghapi

import (
	"fmt"
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
// every non-test file in the package, finds every `APIError` composite literal
// and every assignment to a `.Detail` selector in it, and requires each `Detail`
// to be one of three things:
//
//   - absent, or built only from string literals;
//   - routed through `Client.detail` (redact then clip);
//   - an expression named in `detailLedger` below, with a reason.
//
// 🔴 STATE THE MECHANISM, NOT A PROPERTY. This comment used to say the guard
// enforces "that no `Detail` reaches the UI unbounded and un-examined" — wider
// than the code, and the FOURTH over-wide sentence in this change (the tally is
// kept on `maxDetailRunes` in `query.go`). What the scanner actually reads is:
// this package's own non-test `.go` files, parsed with `go/parser`; inside them,
// composite literals whose type is the identifier `APIError`, their keyed
// `Detail:` element, and assignments whose left-hand side is a selector named
// `Detail`. A POSITIONAL `APIError{…}` literal is an error rather than a skip,
// because the scanner cannot tell which element is the `Detail` field.
//
// What it therefore cannot see: a `Detail` set outside this package, through an
// interface, by reflection, or by `encoding/json` unmarshalling into the struct;
// nor what a ledgered expression evaluates to, since a ledger entry names an
// expression at one site and not the value that expression produces. It also
// cannot tell whether an expression carries SERVER text — that judgement is a
// human one, and writing it down is what a ledger entry is for.

// detailLedger names every `APIError.Detail` sub-expression that is NOT a string
// literal and NOT routed through `Client.detail`, with the reason it needs no
// routing.
//
// 🔴 THE KEY IS A SITE, NOT A WORD: `file.go:Func: expr`. Keying on the bare
// expression excused an IDENTIFIER wherever it appeared. MEASURED at `d70ce112`:
// with the key `name`, writing `name := "unreadable files response: " + err.Error()`
// in `diff.go` and then `Detail: name` passed the suite — absolved by an entry
// whose reason ("the argv this program was started with") was written for
// `write.go`, about a different value, in a different function. A reason is an
// argument about one expression in one place, so the key has to carry the place.
//
// 🔴 IT IS PINNED TWO-WAY. An entry naming a site no construction uses fails the
// test too, so a route that later starts going through `Client.detail` cannot
// leave a stale excuse behind for the next one.
var detailLedger = map[string]string{
	"write.go:Client.SubmitReview: event": "the caller's own argument, being " +
		"REJECTED for not being one of `ReviewEvents()`; it never came from a response",
	"write.go:Client.SubmitReview: strings.Join(ReviewEvents(), \", \")": "this " +
		"package's own three constants",
	"write.go:Client.Merge: method": "the caller's own argument, being REJECTED " +
		"for not being one of `cfg.MergeMethods()`; it never came from a response",
	"write.go:Client.Merge: strings.Join(cfg.MergeMethods(), \", \")": "the config " +
		"package's own constants",
	"write.go:Client.Merge: read.Terminal": "`TerminalPRState`'s answer, which is " +
		"`PRStateMerged`, `PRStateClosed` or the empty string — a closed vocabulary " +
		"this package owns. The server's `state` string is matched AGAINST those " +
		"words and never echoed: an unrecognised one yields \"\", which this arm " +
		"does not reach",
	"write.go:Client.Merge: readsSpent(reads, c.pollInterval)": "this package's own " +
		"count and duration, formatted by `readsSpent`",
	"write.go:Client.Merge: MergeableYes": "this package's own constant, named as " +
		"the state the refusal wanted; the SERVER's word beside it in that same " +
		"sentence IS routed",
	"query.go:Client.Mergeability: owner":  "the argv this program was started with",
	"query.go:Client.Mergeability: name":   "the argv this program was started with",
	"query.go:Client.Mergeability: num":    "the argv this program was started with",
	"query.go:Client.decodeSnapshot: num":  "the argv this program was started with",
	"query.go:Client.decodeSnapshot: repo": "`owner+\"/\"+name`, composed from argv by the caller",
}

// apiErrorDetail is one place the scan found an `APIError.Detail` being set — a
// composite literal's keyed `Detail:` element, or an assignment to a `.Detail`
// selector.
type apiErrorDetail struct {
	file string
	// fn is the enclosing function, so a ledger entry binds to a site rather
	// than to an identifier that any file may spell.
	fn   string
	line int
	// routed is true when the `Detail` expression calls `Client.detail`.
	routed bool
	// leaves are the sub-expressions that are neither string literals nor
	// routed — each must be named in `detailLedger`.
	leaves []string
	// hasDetail is false for a construction with no `Detail` field at all.
	hasDetail bool
}

// key is the `detailLedger` key for one leaf of this record.
func (r apiErrorDetail) key(leaf string) string {
	return r.file + ":" + r.fn + ": " + leaf
}

// scanAPIErrorDetails parses `src` and reports every place an `APIError.Detail`
// is set in it: the keyed `Detail:` element of an `APIError` composite literal,
// and any assignment whose left-hand side is a selector named `Detail`.
//
// 🔴 A POSITIONAL `APIError{…}` LITERAL IS AN ERROR, NOT A SKIP. Field order is
// not in the AST, so the scanner cannot say which element is `Detail` — and the
// old code stepped over it silently while still counting it, which both hid the
// detail AND raised the anti-degenerate floor it would otherwise have tripped.
//
// ⚠ `filename` is used for reporting, for the ledger key, and to let the caller
// feed this synthetic source, which is how the controls below check the scanner
// can go both red and green.
func scanAPIErrorDetails(filename, src string) ([]apiErrorDetail, error) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, filename, src, 0)
	if err != nil {
		return nil, err
	}
	funcs := funcSpans(f)
	var out []apiErrorDetail
	var scanErr error
	ast.Inspect(f, func(n ast.Node) bool {
		switch node := n.(type) {
		case *ast.AssignStmt:
			for i, lhs := range node.Lhs {
				sel, ok := lhs.(*ast.SelectorExpr)
				if !ok || sel.Sel.Name != "Detail" || len(node.Rhs) == 0 {
					continue
				}
				// A multi-value assignment (`a.Detail, b = f()`) has one RHS
				// for several LHS; walk that one rather than dropping the case.
				rhs := node.Rhs[0]
				if len(node.Rhs) == len(node.Lhs) {
					rhs = node.Rhs[i]
				}
				rec := apiErrorDetail{
					file:      filename,
					fn:        enclosingFunc(funcs, lhs.Pos()),
					line:      fset.Position(lhs.Pos()).Line,
					hasDetail: true,
				}
				rec.routed, rec.leaves = walkDetail(fset, rhs)
				out = append(out, rec)
			}
		case *ast.CompositeLit:
			id, ok := node.Type.(*ast.Ident)
			if !ok || id.Name != "APIError" {
				return true
			}
			rec := apiErrorDetail{
				file: filename,
				fn:   enclosingFunc(funcs, node.Pos()),
				line: fset.Position(node.Pos()).Line,
			}
			for _, el := range node.Elts {
				kv, ok := el.(*ast.KeyValueExpr)
				if !ok {
					if scanErr == nil {
						scanErr = fmt.Errorf("%s:%d constructs an APIError with POSITIONAL "+
							"fields. The scanner cannot tell which element is `Detail`, so it "+
							"cannot check it. Write the field names — `&APIError{State: …, "+
							"Detail: …}`",
							filename, fset.Position(el.Pos()).Line)
					}
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
		}
		return true
	})
	if scanErr != nil {
		return nil, scanErr
	}
	return out, nil
}

// funcSpan is one top-level function's name and source extent.
type funcSpan struct {
	name       string
	start, end token.Pos
}

func funcSpans(f *ast.File) []funcSpan {
	var out []funcSpan
	for _, d := range f.Decls {
		fd, ok := d.(*ast.FuncDecl)
		if !ok {
			continue
		}
		name := fd.Name.Name
		if fd.Recv != nil && len(fd.Recv.List) == 1 {
			name = recvTypeName(fd.Recv.List[0].Type) + "." + name
		}
		out = append(out, funcSpan{name: name, start: fd.Pos(), end: fd.End()})
	}
	return out
}

func recvTypeName(e ast.Expr) string {
	switch v := e.(type) {
	case *ast.StarExpr:
		return recvTypeName(v.X)
	case *ast.IndexExpr:
		return recvTypeName(v.X)
	case *ast.Ident:
		return v.Name
	}
	return "?"
}

// enclosingFunc names the top-level function containing `pos`. A construction
// outside any function — a package-level `var`, say — is keyed `<file-level>`
// rather than dropped.
func enclosingFunc(spans []funcSpan, pos token.Pos) string {
	for _, s := range spans {
		if pos >= s.start && pos < s.end {
			return s.name
		}
	}
	return "<file-level>"
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
			t.Fatalf("scanning %s: %v", name, err)
		}
		all = append(all, found...)
	}

	used := map[string]bool{}
	for _, rec := range all {
		for _, leaf := range rec.leaves {
			k := rec.key(leaf)
			used[k] = true
			if _, ok := detailLedger[k]; ok {
				continue
			}
			t.Errorf("%s:%d builds an APIError.Detail out of %s, which is neither a "+
				"string literal nor routed through `Client.detail`.\n"+
				"Route it — `Detail: c.detail(…)` — or add %q to `detailLedger` with "+
				"the reason it needs no routing. An unrouted detail is unbounded: the "+
				"server decides how long the card is.",
				rec.file, rec.line, leaf, k)
		}
	}
	// 🔴 THE LEDGER IS PINNED THE OTHER WAY TOO. A stale excuse is an excuse the
	// next unrouted expression can be filed under by accident.
	for k := range detailLedger {
		if !used[k] {
			t.Errorf("`detailLedger` names %q, which no APIError.Detail in this package "+
				"builds. Delete the entry — a reason kept past the code it excused is a "+
				"reason nobody will re-read.", k)
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

// 🔴 THE TWO SHAPES THE SCANNER USED TO WALK STRAIGHT PAST. Both were measured
// at `d70ce112`: written into `diff.go` in place of the routed call, each left
// the package guard above GREEN.
//
//   - `ae := &APIError{State: …}` followed by `ae.Detail = "…" + err.Error()` —
//     no `Detail:` element exists, so the keyed-element loop saw nothing.
//   - `&APIError{AuthOther, "…" + err.Error(), time.Time{}}` — every element is
//     positional, the `KeyValueExpr` assertion failed, and the `continue` threw
//     the field away while the record still counted toward the floor.
func TestTheDetailScannerSeesAssignedAndPositionalDetails(t *testing.T) {
	const assigned = `package p
func f(err error) error {
	ae := &APIError{State: AuthOther}
	ae.Detail = "unreadable files response: " + err.Error()
	return ae
}`
	got, err := scanAPIErrorDetails("assign.go", assigned)
	if err != nil {
		t.Fatal(err)
	}
	// The composite literal and the assignment are two separate records.
	if len(got) != 2 {
		t.Fatalf("the scanner reported %d records for a literal plus an assignment "+
			"to its `Detail`, want 2: %+v", len(got), got)
	}
	var assign *apiErrorDetail
	for i := range got {
		if len(got[i].leaves) > 0 {
			assign = &got[i]
		}
	}
	if assign == nil {
		t.Fatal("`x.Detail = \"…\" + err.Error()` produced no leaf — the assignment " +
			"arm never ran, so the detail reaches the card unexamined")
	}
	if assign.routed {
		t.Error("an unrouted assigned detail was classified as routed")
	}
	if len(assign.leaves) != 1 || assign.leaves[0] != "err.Error()" {
		t.Errorf("leaves = %q, want exactly [\"err.Error()\"]", assign.leaves)
	}
	if !assign.hasDetail {
		t.Error("an assigned `Detail` was recorded as having no Detail")
	}

	const positional = `package p
import "time"
func f(err error) error {
	return &APIError{AuthOther, "unreadable: " + err.Error(), time.Time{}}
}`
	recs, err := scanAPIErrorDetails("positional.go", positional)
	if err == nil {
		t.Errorf("a POSITIONAL APIError literal was accepted, and read as %+v. The "+
			"scanner cannot name its fields, so accepting it hides the `Detail` while "+
			"still counting the construction", recs)
	}
}

// 🔴 A LEDGER ENTRY EXCUSES A SITE, NOT A WORD. The same identifier in a
// different function, or in a different file, must not inherit the excuse: the
// reason written beside an entry is an argument about one expression in one
// place.
func TestALedgerKeyBindsTheExpressionToItsSite(t *testing.T) {
	const twoFuncs = `package p
func alpha(err error) error {
	return &APIError{State: AuthOther, Detail: "a: " + err.Error()}
}
func beta(err error) error {
	return &APIError{State: AuthOther, Detail: "b: " + err.Error()}
}`
	a, err := scanAPIErrorDetails("one.go", twoFuncs)
	if err != nil {
		t.Fatal(err)
	}
	if len(a) != 2 || len(a[0].leaves) != 1 || len(a[1].leaves) != 1 {
		t.Fatalf("expected two one-leaf records, got %+v", a)
	}
	if a[0].key(a[0].leaves[0]) == a[1].key(a[1].leaves[0]) {
		t.Errorf("the same expression in two different functions produced ONE key "+
			"(%q) — one function's excuse would absolve the other",
			a[0].key(a[0].leaves[0]))
	}

	// Same function name, different file: the key must still separate them.
	b, err := scanAPIErrorDetails("two.go", `package p
func alpha(err error) error {
	return &APIError{State: AuthOther, Detail: "a: " + err.Error()}
}`)
	if err != nil {
		t.Fatal(err)
	}
	if len(b) != 1 || len(b[0].leaves) != 1 {
		t.Fatalf("expected one one-leaf record, got %+v", b)
	}
	if a[0].key(a[0].leaves[0]) == b[0].key(b[0].leaves[0]) {
		t.Errorf("the same expression in identically-named functions in two files "+
			"produced ONE key (%q) — the key is not carrying the file",
			b[0].key(b[0].leaves[0]))
	}

	// A method's receiver type is part of the name, so two `Client.x` and
	// `other.x` methods do not share one excuse either.
	m, err := scanAPIErrorDetails("three.go", `package p
type other struct{}
func (c *Client) alpha(err error) error {
	return &APIError{State: AuthOther, Detail: "a: " + err.Error()}
}
func (o *other) alpha(err error) error {
	return &APIError{State: AuthOther, Detail: "a: " + err.Error()}
}`)
	if err != nil {
		t.Fatal(err)
	}
	if len(m) != 2 || len(m[0].leaves) != 1 || len(m[1].leaves) != 1 {
		t.Fatalf("expected two one-leaf records, got %+v", m)
	}
	if m[0].key(m[0].leaves[0]) == m[1].key(m[1].leaves[0]) {
		t.Errorf("two same-named methods on different receivers produced ONE key (%q)",
			m[0].key(m[0].leaves[0]))
	}
}
