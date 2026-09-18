// Command mention-review is a single-purpose TUI for reading one GitHub pull
// request, spawned by the Alacritty mention-hint path.
//
// 🔴 PHASE 2 CAN WRITE: comment, approve, request changes, submit review,
// merge. Four of the five prompt first; a PR-level comment does not, per §3.7,
// because it is additive and trivially reversible. The two-way ledger in
// `internal/ui/intents.go` is what makes that list machine-readable: a sixth
// write verb in neither set fails the suite.
//
// 🔴 PR-LEVEL COMMENTS ONLY. Inline diff-line commenting needs review-thread
// positioning against the diff, and the operator ruled it out of this phase.
//
// Usage: mention-review <owner/repo> <number>
// Exit:  64 wrong argument count · 65 malformed owner/repo · 66 bad number
package main

import (
	"fmt"
	"net/http"
	"os"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/cli/go-gh/v2/pkg/auth"

	"github.com/innovation-upstream/devrc/mention-review/internal/argv"
	"github.com/innovation-upstream/devrc/mention-review/internal/cfg"
	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/ui"
)

func main() {
	// 🔴 `--version` IS ANSWERED BEFORE ARGUMENT VALIDATION, and it prints the
	// value `default.nix` read out of version.go. The Nix expression stamps
	// this same string back via `-X`, so the store path and the compiled-in
	// version are provably one value — which is the whole point of reading the
	// version out of the source rather than writing it in the derivation.
	if len(os.Args) == 2 && (os.Args[1] == "--version" || os.Args[1] == "-v") {
		fmt.Println(buildVersion)
		return
	}

	args, aerr := argv.Parse(os.Args[1:])
	if aerr != nil {
		// 🔴 BEFORE DRAWING ANYTHING. This is the ONE failure that exits rather
		// than rendering a card, because there is no window to keep open yet —
		// exactly as `nvim-octo` behaved, and the contract
		// `scripts/tests/test_nvim_octo.py` pinned. ⚠ Both were deleted in
		// Phase 4; `internal/argv`'s package comment names the git incantation
		// that recovers them, and `main_test.go` is what pins this wiring now.
		fmt.Fprintln(os.Stderr, aerr.Msg)
		os.Exit(aerr.Code)
	}

	// 🔴 AN EMPTY TOKEN IS NOT AN ERROR HERE — go-gh's fourth precedence rung
	// returns ("", "default") with NO error, so `err` is not the signal. The
	// emptiness is carried into the client, which turns the first request into
	// a NO TOKEN card. Exiting here instead would flash a window and vanish.
	token, _ := auth.TokenForHost("github.com")

	client := ghapi.NewClient(token, &http.Client{Timeout: 30 * time.Second})

	app := ui.New(args.Owner, args.Name, args.Num)
	app.SetRunner(ui.LiveRunner{C: client})

	// 🔴 A CONFIG THAT CANNOT BE READ DISABLES MERGING; IT DOES NOT EXIT, AND
	// IT DOES NOT SUBSTITUTE A METHOD. The empty string is the `UNKNOWN`
	// sentinel, and the merge key then refuses in words on screen. Exiting here
	// would flash a window and vanish; guessing `squash` is the exact failure
	// `nvim-octo`'s wrapper refuses, because a merge dispatched with a method
	// nobody chose is the wrong commit shape.
	method, cerr := cfg.ResolveMergeMethod(cfg.Path())
	if cerr != nil {
		fmt.Fprintln(os.Stderr, "mention-review:", cerr)
		method = ""
	}
	app.SetMergeMethod(method)

	p := tea.NewProgram(app)
	if _, err := p.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "mention-review:", err)
		os.Exit(1)
	}
}
