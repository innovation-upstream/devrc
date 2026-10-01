// oc-sent-tui — the popup TUI behind tmux's Alt+S: the messages the user
// sent in the CURRENT opencode session, resolved from the focused pane's
// cwd.
//
// The binary is a thin shell: resolve the cwd, load via internal/sent (which
// execs `oc-sent here <cwd> --json` — see that package's header for why the
// store is never opened here), render via internal/ui. Every failure is
// rendered IN the popup by ui.NewError — a popup that flashes and vanishes
// tells the operator nothing.
package main

import (
	"fmt"
	"os"

	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/oc-sent-tui/internal/sent"
	"github.com/innovation-upstream/devrc/oc-sent-tui/internal/ui"
)

func main() {
	if len(os.Args) > 1 && (os.Args[1] == "--version" || os.Args[1] == "-v") {
		fmt.Println("oc-sent-tui " + buildVersion)
		return
	}

	// the pane's cwd comes from the tmux binding (`#{pane_current_path}`);
	// run bare, "current" means here
	var cwd string
	if len(os.Args) > 1 {
		cwd = os.Args[1]
	} else if wd, err := os.Getwd(); err == nil {
		cwd = wd
	}

	d, loadErr := sent.Load(cwd, sent.LiveRunner{})

	width, height := 100, 28
	var model tea.Model
	if loadErr != nil {
		model = ui.NewError(loadErr.Error(), width, height)
	} else {
		model = ui.New(d, width, height)
	}

	p := tea.NewProgram(model)
	if _, err := p.Run(); err != nil {
		// the popup closes on exit (-E); a program error has to outlive it
		// or nobody ever sees it
		fmt.Fprintf(os.Stderr, "oc-sent-tui: %v\n", err)
		os.Exit(1)
	}
}
