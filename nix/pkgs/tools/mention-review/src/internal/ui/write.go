package ui

import (
	"strings"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
)

// 🔴 PHASE 2 — THE WRITE HALF, AND EVERY LINE OF IT IS STILL PURE.
//
// Nothing in this file performs I/O or constructs a closure that does. A write
// verb produces an INTENT, exactly as a read does, and `run.go` is still the
// only place a `tea.Cmd` exists. That is what makes "pressing `m` produces a
// pending confirmation and ZERO network intents" a mechanical assertion rather
// than a promise, and it is the single property that lets this phase be tested
// at all without a GitHub account.

// composeState is the body being typed.
//
// ⚠ HAND-ROLLED, AND THE ALTERNATIVE WAS WEIGHED. `bubbles/v2/textarea` is a
// real editor and this is not one — it appends, deletes and walks left/right,
// with no word motions, no selection and no undo. Two reasons it is still the
// right call here: a textarea is a `tea.Model` with its own `Update`, so a
// comment body would stop being assertable state and become a component's
// private buffer; and pulling in a package the module does not vendor today
// changes `vendorHash`, which is measured by building with a wrong hash. If the
// operator wants real editing, that is a Phase-3 swap with a known cost, not a
// thing to discover half-built.
type composeState struct {
	// Verb is the browse-mode action that opened the buffer, and it decides
	// what `ComposeSend` builds.
	Verb Action
	// Buf is runes, not bytes, so the cursor cannot land inside a codepoint.
	Buf []rune
	Cur int
}

// pendingWrite is a built intent waiting on y/N.
type pendingWrite struct {
	Intent Intent
	// Prompt is the WHOLE normalised string the operator reads. It is stored
	// rather than re-derived at render time so the thing shown and the thing
	// asserted cannot diverge across a redraw.
	Prompt string
}

// --- the gate ----------------------------------------------------------------

// writeGate answers "may this App write to GitHub at all", in words.
//
// 🔴 ONE PREDICATE, ONE PLACE, FOR ALL FIVE VERBS. The five refusal conditions
// below were open-coded per verb in the first draft of this file, and that is
// the shape this repo keeps re-fixing: a predicate duplicated across call sites
// regenerates the same bug at every site. Every write verb calls this first.
//
// It returns ("", true) when writing is allowed.
func (a App) writeGate() (string, bool) {
	if a.Load != LoadReady || a.Snap == nil {
		return "REFUSED — the pull request has not loaded, so there is nothing to act on.", false
	}
	if a.Snap.Kind != ghapi.KindPullRequest {
		// §4: the issue card is TERMINAL. No comment posting, no labels, no
		// close/reopen. That line is drawn in the proposal and it is drawn here.
		return "REFUSED — this is an ISSUE, and the issue card is read-only.", false
	}
	if a.Snap.ViewerLogin == "" {
		// 🔴 §10.2, AND THIS IS THE ARM THAT MAKES THE MITIGATION REAL. The
		// login is the only thing on screen that says WHICH account a write
		// would come from, and cli/cli#14370 means the resolved token may not
		// belong to the account the config calls active. A write whose prompt
		// cannot name the actor is exactly the invisible case, so it is refused
		// rather than performed with the clause omitted.
		return "REFUSED — the authenticated login is UNKNOWN, so nothing here can " +
			"say which account would act. See cli/cli#14370.", false
	}
	return "", true
}

// --- browse-mode entry points ------------------------------------------------

// beginCompose opens the buffer for a verb that needs a body.
func (a App) beginCompose(verb Action) App {
	if notice, ok := a.writeGate(); !ok {
		a.notice = notice
		return a
	}
	a.mode = ModeCompose
	a.compose = composeState{Verb: verb}
	a.notice = ""
	return a
}

// propose builds a confirmed write intent and parks it behind the prompt.
//
// 🔴 IT RETURNS NO INTENTS. That is the assertion the §5.5 mutation table names
// — "make `m` fire the merge directly with no confirmation" must break a test
// that asserted `intents` was empty — and it is why this function's signature
// is the same as an acting one's.
func (a App) propose(i Intent) (App, []Intent) {
	if notice, ok := a.writeGate(); !ok {
		a.notice = notice
		return a, nil
	}
	if !RequiresConfirmation(i) {
		// A caller bug: this path is only for ledgered-CONFIRMED verbs. Saying
		// so beats silently confirming something the ledger says need not be,
		// or silently acting on something it says must be.
		a.notice = "REFUSED — " + i.intentName() + " is not a confirmed verb; this is a bug."
		return a, nil
	}
	prompt, ok := ConfirmPrompt(i, a.Snap)
	if !ok {
		// 🔴 NO PROMPT, NO ACTION. `ConfirmPrompt` refuses when it cannot name
		// the login or the merge method, and the only safe reading of "I cannot
		// describe what I am about to do" is not to do it.
		a.notice = "REFUSED — no confirmation prompt could be built for " +
			i.intentName() + ", so nothing was sent."
		return a, nil
	}
	a.mode = ModeConfirm
	a.pending = &pendingWrite{Intent: i, Prompt: prompt}
	a.notice = ""
	return a, nil
}

// proposeMerge is `propose` plus the method check.
//
// 🔴 REFUSING ON AN UNKNOWN METHOD IS CARRIED OVER FROM THE LUA VERBATIM IN
// SUBSTANCE: "a merge dispatched with a method nobody chose produces the wrong
// commit shape". There, a mutation replacing the sentinel with `"squash"`
// survived a green suite because nothing reached the arm. Here the arm is
// reachable from the keyboard in one keypress, and a test presses it.
func (a App) proposeMerge() (App, []Intent) {
	if notice, ok := a.writeGate(); !ok {
		a.notice = notice
		return a, nil
	}
	if a.MergeMethod == "" {
		a.notice = "REFUSED — the merge method could not be read from the config, " +
			"so it is UNKNOWN. Refusing rather than guessing: a merge dispatched " +
			"with a method nobody chose is the wrong commit shape."
		return a, nil
	}
	return a.propose(MergePR{
		Owner: a.Owner, Name: a.Name, Num: a.Num, Method: a.MergeMethod,
	})
}

// --- compose mode ------------------------------------------------------------

func (a App) composeInsert(text string) App {
	if a.mode != ModeCompose {
		return a
	}
	r := []rune(text)
	cur := clamp(a.compose.Cur, 0, len(a.compose.Buf))
	next := make([]rune, 0, len(a.compose.Buf)+len(r))
	next = append(next, a.compose.Buf[:cur]...)
	next = append(next, r...)
	next = append(next, a.compose.Buf[cur:]...)
	a.compose.Buf = next
	a.compose.Cur = cur + len(r)
	return a
}

func (a App) composeBackspace() App {
	if a.mode != ModeCompose || a.compose.Cur == 0 || len(a.compose.Buf) == 0 {
		return a
	}
	cur := clamp(a.compose.Cur, 1, len(a.compose.Buf))
	next := make([]rune, 0, len(a.compose.Buf)-1)
	next = append(next, a.compose.Buf[:cur-1]...)
	next = append(next, a.compose.Buf[cur:]...)
	a.compose.Buf = next
	a.compose.Cur = cur - 1
	return a
}

func (a App) composeMove(d int) App {
	if a.mode != ModeCompose {
		return a
	}
	a.compose.Cur = clamp(a.compose.Cur+d, 0, len(a.compose.Buf))
	return a
}

// composeCancel discards the buffer.
//
// ⚠ IT SAYS SO IN WORDS. `nvim-octo` notifies "ABORTED — nothing was sent to
// GitHub" on every abort, and the reason is that silence after a keypress is
// indistinguishable from a keypress that did not register.
func (a App) composeCancel() App {
	a.mode = ModeBrowse
	a.compose = composeState{}
	a.notice = "DISCARDED — nothing was sent to GitHub."
	a.pending = nil
	return a
}

// ComposeBody is the buffer as a string. Exported so the one end-to-end test
// can assert what the real event loop accumulated.
func (a App) ComposeBody() string { return string(a.compose.Buf) }

// Mode reports the live key table, for tests and for the renderer.
func (a App) Mode() Mode { return a.mode }

// PendingPrompt is the confirmation line, or "" when nothing is pending.
func (a App) PendingPrompt() string {
	if a.pending == nil {
		return ""
	}
	return a.pending.Prompt
}

// Notice is the last outcome in words.
func (a App) Notice() string { return a.notice }

// composeSend turns the buffer into an intent.
//
// A CONFIRMED verb goes to the prompt; the one NOT_CONFIRMED verb — a comment,
// "additive and trivially reversible" per §3.7 — is emitted here. 🔴 THE
// BRANCH READS THE LEDGER (`RequiresConfirmation`) RATHER THAN NAMING THE
// VERBS. A sixth verb added to `Confirmed` is confirmed by this code the moment
// it is ledgered, with no second edit that could be forgotten.
func (a App) composeSend() (App, []Intent) {
	if a.mode != ModeCompose {
		return a, nil
	}
	body := strings.TrimSpace(string(a.compose.Buf))
	if body == "" {
		// GitHub answers 422 for an empty REQUEST_CHANGES body, and an empty
		// comment is a comment nobody meant to post. Refusing here names the
		// problem; letting it through names it in server prose.
		a.notice = "REFUSED — the body is empty, so there is nothing to send."
		return a, nil
	}
	verb := a.compose.Verb
	var i Intent
	switch verb {
	case ActComment:
		i = PostComment{Owner: a.Owner, Name: a.Name, Num: a.Num, Body: body}
	case ActRequestChanges:
		i = RequestChanges{Owner: a.Owner, Name: a.Name, Num: a.Num, Body: body}
	case ActSubmitReview:
		i = SubmitReview{Owner: a.Owner, Name: a.Name, Num: a.Num, Body: body}
	default:
		a.notice = "REFUSED — the compose buffer has no verb; this is a bug."
		a.mode = ModeBrowse
		a.compose = composeState{}
		return a, nil
	}

	if notice, ok := a.writeGate(); !ok {
		// 🔴 RE-CHECKED AT THE MOMENT OF THE WRITE, not only when compose
		// opened. The snapshot can be replaced between the two — a refetch
		// landing mid-compose — and a gate evaluated on the state that
		// motivated the action is a remembered fact, not a current one.
		a.notice = notice
		a.mode = ModeBrowse
		a.compose = composeState{}
		return a, nil
	}

	a.mode = ModeBrowse
	a.compose = composeState{}
	if RequiresConfirmation(i) {
		return a.propose(i)
	}
	a.notice = "SENDING — " + i.intentName() + " as " + a.Snap.ViewerLogin + "."
	return a, []Intent{i}
}

// --- confirm mode ------------------------------------------------------------

// confirmYes is the ONLY place a confirmed write intent is emitted.
func (a App) confirmYes() (App, []Intent) {
	if a.mode != ModeConfirm || a.pending == nil {
		return a, nil
	}
	i := a.pending.Intent
	a.pending = nil
	a.mode = ModeBrowse
	if notice, ok := a.writeGate(); !ok {
		a.notice = notice
		return a, nil
	}
	a.notice = "SENDING — " + i.intentName() + " as " + a.Snap.ViewerLogin + "."
	return a, []Intent{i}
}

func (a App) confirmNo() App {
	name := ""
	if a.pending != nil {
		name = a.pending.Intent.intentName() + " "
	}
	a.pending = nil
	a.mode = ModeBrowse
	a.notice = "ABORTED — " + name + "was NOT sent to GitHub."
	return a
}

// --- the result --------------------------------------------------------------

// stepWriteDone records the outcome and re-reads the PR on success.
func (a App) stepWriteDone(m WriteDone) (App, []Intent) {
	if m.Err != nil {
		// 🔴 A FAILED WRITE IS NOT A FAILED PAGE. The panels are still true, so
		// the error becomes a notice rather than replacing the screen with a
		// card — the same judgement `DiffLoaded` makes one message up.
		a.notice = "FAILED — " + m.Verb + ": " + m.Err.Error()
		return a, nil
	}
	a.notice = "DONE — " + m.Verb + " succeeded; re-reading the pull request."
	// 🔴 RE-READ, because every panel still describes the PR as it was BEFORE
	// the write. A screen that says OPEN after a successful merge is the same
	// class of lie as a file list that is quietly short.
	return a, []Intent{FetchPR{Owner: a.Owner, Name: a.Name, Num: a.Num}}
}
