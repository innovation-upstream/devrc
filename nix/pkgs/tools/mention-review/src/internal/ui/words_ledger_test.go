package ui

import (
	"go/ast"
	"go/parser"
	"go/token"
	"testing"
)

// 🔴 THE `MeaningBearingStates` LEDGER IS COMPLETE, AND THAT CLAIM IS NOW
// ENFORCED RATHER THAN MERELY WRITTEN DOWN.
//
// That function's own doc comment says the ledger "covers every constructor in
// this file". Until this test existed, NOTHING asserted it: `words_test.go`
// proved every LISTED state renders a word with colour removed, and a
// constructor nobody listed was invisible to it. A guard whose DESCRIPTION
// claims more than its implementation checks is worse than no guard, because it
// stops anyone looking — and this codebase's handoff recorded this exact line
// as one of three such over-claims. Phase 2 added `ModeWord` to that file, so
// the gap was about to GROW rather than merely persist, which is why closing it
// belongs to this change rather than to a follow-up.
//
// 🔴 IT PARSES THE AST, NOT A REGEX OVER THE SOURCE. A grep for `func .*Word(`
// measures the SPELLING; the AST measures what the file declares. A constructor
// is identified by its RETURN TYPE, which is the property that actually makes
// it one — `StateWord.Render` returns a string and is correctly not counted.
func TestEveryStateWordConstructorIsInTheLedger(t *testing.T) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "words.go", nil, 0)
	if err != nil {
		t.Fatalf("parsing words.go: %v", err)
	}

	constructors := map[string]bool{}
	var ledgerBody *ast.BlockStmt
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok {
			continue
		}
		if fn.Name.Name == "MeaningBearingStates" {
			ledgerBody = fn.Body
			continue
		}
		if fn.Type.Results == nil || len(fn.Type.Results.List) != 1 {
			continue
		}
		id, ok := fn.Type.Results.List[0].Type.(*ast.Ident)
		if !ok || id.Name != "StateWord" {
			continue
		}
		constructors[fn.Name.Name] = true
	}
	if ledgerBody == nil {
		t.Fatal("words.go no longer declares MeaningBearingStates")
	}

	// 🔴 POSITIVE CONTROL ON THE PARSE. Zero constructors would make the loop
	// below vacuous and read as a clean, complete ledger — the silent zero.
	if len(constructors) < 5 {
		t.Fatalf("the AST walk found only %d StateWord constructors (%v) — that is "+
			"a broken parse, not a small file", len(constructors), constructors)
	}

	called := map[string]bool{}
	ast.Inspect(ledgerBody, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		switch fun := call.Fun.(type) {
		case *ast.Ident:
			called[fun.Name] = true
		case *ast.SelectorExpr:
			// `LoadLoading.Word()` and `ghapi.AuthNoToken.Word()` both land
			// here, and the method NAME is what the declaration side reports.
			called[fun.Sel.Name] = true
		}
		return true
	})

	for name := range constructors {
		if !called[name] {
			t.Errorf("words.go declares the state-word constructor %q, and "+
				"MeaningBearingStates() never calls it — a state nobody checked, "+
				"inside a ledger whose doc comment claims it covers every "+
				"constructor in the file", name)
		}
	}

	// 🔴 THE CONTROL THAT THIS COMPARISON CAN GO RED. Without it the loop above
	// is satisfied by a `called` map that reports everything — which is exactly
	// what an `ast.Inspect` matching too broadly would produce.
	if called["NoSuchWordConstructor"] {
		t.Error("the call-site walk reports a function that words.go never calls")
	}
	if !called["ModeWord"] {
		t.Error("the call-site walk cannot see ModeWord, which the ledger does " +
			"call — the walk is not reading the ledger's body")
	}
	t.Logf("the ledger covers %d StateWord constructors", len(constructors))
}
