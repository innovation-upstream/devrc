package ui

import (
	"context"
	"os/exec"

	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// 🔴 THE ONLY PLACE I/O IS CONSTRUCTED (§3.3).
//
// `Step` returns intents; this file turns them into `tea.Cmd`s. It is small on
// purpose: everything here is untestable-by-assertion (a `tea.Cmd` is an opaque
// `func() tea.Msg`), so the less of it there is, the more of the program is
// covered by the pure tests.

// Runner is the effect surface. An interface rather than a concrete client so
// the one end-to-end test can substitute a fake without a live network.
type Runner interface {
	FetchPR(ctx context.Context, owner, name string, num int) (*ghapi.Snapshot, error)
	FetchDiff(ctx context.Context, owner, name string, num int) (*udiff.Diff, error)
	OpenBrowser(url string) error
}

// Run converts ONE intent into a command.
//
// 🔴 A DEFAULT CASE THAT PANICS, NOT ONE THAT SILENTLY RETURNS nil. A new
// intent that nobody wired would otherwise be a keypress that does nothing,
// forever, with no error anywhere — the exact silent-zero shape this repo keeps
// getting bitten by. `run_test.go` asserts every registered intent is handled,
// so the panic is unreachable in a passing build and is the backstop for a
// build that is not.
func Run(i Intent, r Runner) tea.Cmd {
	switch v := i.(type) {
	case FetchPR:
		return func() tea.Msg {
			s, err := r.FetchPR(context.Background(), v.Owner, v.Name, v.Num)
			return PRLoaded{Snap: s, Err: err}
		}
	case FetchDiff:
		return func() tea.Msg {
			d, err := r.FetchDiff(context.Background(), v.Owner, v.Name, v.Num)
			return DiffLoaded{Diff: d, Err: err}
		}
	case OpenBrowser:
		return func() tea.Msg {
			// Fire and forget. §6.1 records the failure mode that matters here:
			// a window that flashes and vanishes teaches the operator nothing,
			// so a failed browser launch must never take the TUI down with it.
			_ = r.OpenBrowser(v.URL)
			return nil
		}
	}
	panic("ui.Run: unhandled intent " + i.intentName())
}

// RunAll maps a slice of intents.
func RunAll(intents []Intent, r Runner) []tea.Cmd {
	if len(intents) == 0 {
		return nil
	}
	cmds := make([]tea.Cmd, 0, len(intents))
	for _, i := range intents {
		cmds = append(cmds, Run(i, r))
	}
	return cmds
}

// --- the live runner --------------------------------------------------------

// LiveRunner performs the real effects.
type LiveRunner struct{ C *ghapi.Client }

func (l LiveRunner) FetchPR(ctx context.Context, owner, name string, num int) (*ghapi.Snapshot, error) {
	return l.C.Fetch(ctx, owner, name, num)
}

func (l LiveRunner) FetchDiff(ctx context.Context, owner, name string, num int) (*udiff.Diff, error) {
	files, truncated, err := l.C.FetchFiles(ctx, owner, name, num)
	if err != nil {
		return nil, err
	}
	in := make([]udiff.FileInput, 0, len(files))
	for _, f := range files {
		in = append(in, udiff.FileInput{
			Path:       f.Path,
			PrevPath:   f.PreviousPath,
			ChangeType: f.ChangeType,
			Additions:  f.Additions,
			Deletions:  f.Deletions,
			Patch:      f.Patch,
		})
	}
	d, err := udiff.Parse(in)
	if err != nil {
		return nil, err
	}
	d.Truncated = truncated
	return d, nil
}

// OpenBrowser shells out to xdg-open.
//
// 🔴 PINNED BY THE WRAPPER'S PATH, NOT INHERITED. The whole call chain starts
// in an Alacritty hint spawned with the DISPLAY MANAGER's environment, and
// `~/.nix-profile` is blanked for ~30 s during every home-manager switch. The
// Alacritty wrapper's `lib.makeBinPath` already carries `pkgs.xdg-utils` for
// exactly this reason, and the packaging puts it in `runtimeInputs` too.
func (LiveRunner) OpenBrowser(url string) error {
	return exec.Command("xdg-open", url).Start()
}
