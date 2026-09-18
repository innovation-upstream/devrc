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

	// 🔴 THE THREE WRITES ARE ON THE SAME INTERFACE AS THE READS, AND THAT IS
	// THE WHOLE SAFETY STORY FOR THE TEST SUITE. `App.runner` is nil in every
	// pure test, so no test can reach GitHub even by mistake; the one
	// end-to-end test substitutes a fake that records calls. There is no
	// package-level client anywhere in this program.
	PostComment(ctx context.Context, owner, name string, num int, body string) error
	SubmitReview(ctx context.Context, owner, name string, num int, event, body string) error
	// 🔴 `mergeable` IS THE SNAPSHOT'S STATE, PASSED THROUGH UNCHANGED. The
	// client polls a re-read before dispatching when it says UNKNOWN; this
	// layer must not decide that, because then the decision would live where no
	// pure test can see it.
	Merge(ctx context.Context, owner, name string, num int, method, mergeable string) error
}

// Run converts ONE intent into a command.
//
// 🔴 A DEFAULT CASE THAT PANICS, NOT ONE THAT SILENTLY RETURNS nil. A new
// intent that nobody wired would otherwise be a keypress that does nothing,
// forever, with no error anywhere — the exact silent-zero shape this repo keeps
// getting bitten by. `intents_test.go`'s
// `TestEveryRegisteredIntentIsHandledByRun` asserts every registered intent is
// handled — there is no `run_test.go`, and this comment named one for a while —
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

	// --- the writes (§3.7) ---------------------------------------------------
	//
	// 🔴 EACH ONE RETURNS `WriteDone` CARRYING THE VERB, SUCCESS OR FAILURE.
	// A write that returned `nil` on success would leave the UI unable to tell
	// "it worked" from "the command never ran", which is the empty-result trap:
	// nothing happened is the observable that the most causes share.
	case PostComment:
		return func() tea.Msg {
			return WriteDone{Verb: v.intentName(),
				Err: r.PostComment(context.Background(), v.Owner, v.Name, v.Num, v.Body)}
		}
	case Approve:
		return func() tea.Msg {
			return WriteDone{Verb: v.intentName(),
				Err: r.SubmitReview(context.Background(), v.Owner, v.Name, v.Num,
					ghapi.ReviewApprove, "")}
		}
	case RequestChanges:
		return func() tea.Msg {
			return WriteDone{Verb: v.intentName(),
				Err: r.SubmitReview(context.Background(), v.Owner, v.Name, v.Num,
					ghapi.ReviewRequestChanges, v.Body)}
		}
	case SubmitReview:
		return func() tea.Msg {
			return WriteDone{Verb: v.intentName(),
				Err: r.SubmitReview(context.Background(), v.Owner, v.Name, v.Num,
					ghapi.ReviewComment, v.Body)}
		}
	case MergePR:
		return func() tea.Msg {
			return WriteDone{Verb: v.intentName(),
				Err: r.Merge(context.Background(), v.Owner, v.Name, v.Num, v.Method, v.Mergeable)}
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

// The three writes are one-line delegations on purpose: every decision about
// what may be sent — the verb, the body, the merge method — was made in `Step`,
// where a test can see it. This layer must not be able to change the meaning of
// an intent it was handed.

func (l LiveRunner) PostComment(ctx context.Context, owner, name string, num int, body string) error {
	return l.C.PostComment(ctx, owner, name, num, body)
}

func (l LiveRunner) SubmitReview(ctx context.Context, owner, name string, num int, event, body string) error {
	return l.C.SubmitReview(ctx, owner, name, num, event, body)
}

func (l LiveRunner) Merge(ctx context.Context, owner, name string, num int, method, mergeable string) error {
	return l.C.Merge(ctx, owner, name, num, method, mergeable)
}
