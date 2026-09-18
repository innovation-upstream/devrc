package ui

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/innovation-upstream/devrc/mention-review/internal/cfg"
	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// 🔴 LAYER 2 FOR THE WRITE HALF — KEYS ARE PRESSED AND THE RESULT IS ASSERTED.
//
// ⚠ NOT THE `keys_test.go` PATTERN. That file's structural half compares
// `Dispatch()` against `FullHelp()`, both built from the same literal — it
// asserts the keymap agrees with ITSELF, which is worth having and is not
// coverage of behaviour. Every test below PRESSES the binding and asserts what
// changed, the way `movement_test.go` does.

// pressAll sends a sequence and returns the final app plus every intent emitted
// along the way, so a test can assert "nothing was emitted until the last key".
func pressAll(a App, keys ...string) (App, []Intent) {
	var all []Intent
	for _, k := range keys {
		var out []Intent
		a, out = a.Step(keyPress(k))
		all = append(all, out...)
	}
	return a, all
}

// --- merge: the verb the whole confirmation machinery exists for -------------

func TestPressingMergeRaisesAConfirmationAndEmitsNothing(t *testing.T) {
	a := ready(t)

	next, intents := a.Step(keyPress("m"))

	// 🔴 THE ASSERTION §5.5 NAMES: zero intents where a merge could have gone.
	if len(intents) != 0 {
		t.Fatalf("`m` emitted %v — a merge must not be one keypress away", intents)
	}
	if next.Mode() != ModeConfirm {
		t.Fatalf("`m` left the app in mode %s, want CONFIRM", next.Mode().Word())
	}
	if next.pending == nil {
		t.Fatal("`m` set CONFIRM mode with no pending intent")
	}
	got, ok := next.pending.Intent.(MergePR)
	if !ok {
		t.Fatalf("the pending intent is %T, want MergePR", next.pending.Intent)
	}
	want := MergePR{Owner: fxOwner, Name: fxName, Num: fxNum, Method: fxMergeMethod,
		Mergeable: fxMergeable}
	if got != want {
		t.Errorf("pending = %+v, want %+v", got, want)
	}
}

// 🔴 THE INTENT CARRIES THE MERGEABILITY THE OPERATOR WAS LOOKING AT.
//
// `ghapi.Merge` re-reads mergeability only when this field says UNKNOWN — that
// is the whole base-branch-recompute guard — so a `proposeMerge` that dropped
// the field would send every merge as "UNKNOWN" (an extra round trip on each
// one) and one that invented a value would skip the guard entirely. Neither is
// visible to any assertion about the PROMPT, which names the method and the
// login and says nothing about this.
func TestTheMergeIntentCarriesTheSnapshotsMergeability(t *testing.T) {
	// ⚠ THREE VALUES, NONE OF THEM THE FIXTURE'S OWN. The fixture snapshot says
	// MERGEABLE, so a mutant that read the fixture, or hardcoded the common
	// case, produces MERGEABLE for all three and dies here.
	for _, state := range []string{ghapi.MergeableUnknown, ghapi.MergeableNo, ""} {
		t.Run("snapshot="+state, func(t *testing.T) {
			a := ready(t)
			snap := fixturePR()
			snap.Mergeable = state
			a, _ = a.Step(PRLoaded{Snap: snap})

			next, intents := a.Step(keyPress("m"))
			if len(intents) != 0 {
				t.Fatalf("`m` emitted %v", intents)
			}
			got, ok := next.pending.Intent.(MergePR)
			if !ok {
				t.Fatalf("the pending intent is %T, want MergePR", next.pending.Intent)
			}
			if got.Mergeable != state {
				t.Errorf("the intent carries Mergeable=%q, want the snapshot's %q",
					got.Mergeable, state)
			}
			// And the rest of the intent is unchanged by the new field.
			if got.Method != fxMergeMethod || got.Num != fxNum {
				t.Errorf("intent = %+v", got)
			}
		})
	}
	// POSITIVE CONTROL: the DEFAULT fixture still yields its own value, so the
	// three cases above are the field being carried rather than a constant.
	next, _ := ready(t).Step(keyPress("m"))
	if got := next.pending.Intent.(MergePR).Mergeable; got != fxMergeable {
		t.Errorf("the unmodified fixture produced Mergeable=%q, want %q", got, fxMergeable)
	}
}

func TestConfirmingAMergeEmitsExactlyThatIntent(t *testing.T) {
	a := ready(t)
	next, intents := pressAll(a, "m", "y")

	want := []Intent{MergePR{Owner: fxOwner, Name: fxName, Num: fxNum, Method: fxMergeMethod,
		Mergeable: fxMergeable}}
	if !intentsEqual(intents, want) {
		t.Fatalf("`m` then `y` emitted %v, want %v", intents, want)
	}
	if next.Mode() != ModeBrowse {
		t.Errorf("after confirming, mode = %s, want BROWSE", next.Mode().Word())
	}
	if next.PendingPrompt() != "" {
		t.Errorf("the confirmation survived the confirm: %q", next.PendingPrompt())
	}
}

// ⚠ TWO ABORT SPELLINGS, BOTH ASSERTED. `n` and `esc` are separate keys on one
// binding; a table that lost `esc` would leave the operator hammering a key
// that does nothing while a merge prompt is on screen.
func TestAbortingAMergeEmitsNothingAndSaysSo(t *testing.T) {
	for _, abort := range []string{"n", "esc"} {
		t.Run(abort, func(t *testing.T) {
			next, intents := pressAll(ready(t), "m", abort)
			if len(intents) != 0 {
				t.Fatalf("`m` then %q emitted %v, want nothing", abort, intents)
			}
			if next.Mode() != ModeBrowse {
				t.Errorf("mode = %s, want BROWSE", next.Mode().Word())
			}
			if !strings.HasPrefix(next.Notice(), "ABORTED") {
				t.Errorf("notice = %q, want it to begin ABORTED — silence after a "+
					"keypress is indistinguishable from a key that did not register",
					next.Notice())
			}
			if !strings.Contains(next.Notice(), "MergePR") {
				t.Errorf("the abort notice does not name the verb: %q", next.Notice())
			}
		})
	}
}

// 🔴 A KEY THAT IS NOT IN THE CONFIRM TABLE LEAVES THE CONFIRMATION STANDING.
// It must neither proceed nor silently cancel — both would be a keypress the
// operator did not intend changing the outcome of an irreversible action. `q`
// is the one that matters: in browse mode it quits.
func TestAnUnboundKeyDuringAConfirmationDoesNothingAtAll(t *testing.T) {
	a, _ := ready(t).Step(keyPress("m"))
	for _, k := range []string{"q", "j", "tab", "m", "a"} {
		next, intents := a.Step(keyPress(k))
		if len(intents) != 0 {
			t.Errorf("pressing %q during a confirmation emitted %v", k, intents)
		}
		if next.Mode() != ModeConfirm {
			t.Errorf("pressing %q during a confirmation left mode %s, want CONFIRM",
				k, next.Mode().Word())
		}
		if next.Quitting {
			t.Errorf("pressing %q during a confirmation QUIT the program", k)
		}
		if next.PendingPrompt() != a.PendingPrompt() {
			t.Errorf("pressing %q changed the pending prompt", k)
		}
	}
}

// 🔴 THE `MERGE_METHOD_UNKNOWN` ARM, REACHED FROM THE KEYBOARD. In the Lua this
// arm was close to unreachable and a mutation replacing the sentinel with
// "squash" survived a green suite. Here one keypress reaches it.
func TestMergingWithAnUnknownMethodRefusesRatherThanGuessing(t *testing.T) {
	a := ready(t)
	a.SetMergeMethod("") // the UNKNOWN sentinel

	next, intents := a.Step(keyPress("m"))
	if len(intents) != 0 {
		t.Fatalf("a merge with an unknown method emitted %v", intents)
	}
	if next.Mode() != ModeBrowse {
		t.Errorf("mode = %s, want BROWSE — there is nothing to confirm", next.Mode().Word())
	}
	// 🔴 THE NOTICE MUST BE *THIS* REFUSAL, NOT MERELY A REFUSAL.
	//
	// ⚠ MEASURED: asserting only the `REFUSED` prefix let a mutant SURVIVE.
	// Disabling `proposeMerge`'s method check does not produce a merge — a
	// second lock catches it, because `ConfirmPrompt` cannot build a sentence
	// naming an empty method — but it produces a DIFFERENT notice: "no
	// confirmation prompt could be built … this is a bug", which tells the
	// operator nothing about what to fix. The two refusals are different facts
	// and the operator reads the difference, so the test asserts the specific
	// one.
	if !strings.Contains(next.Notice(), "the merge method could not be read from the config") {
		t.Errorf("notice = %q, want the merge-method refusal specifically — a "+
			"generic REFUSED here means the method check was bypassed and some "+
			"OTHER lock caught it, with a message that does not name the fix",
			next.Notice())
	}
	if !strings.HasPrefix(next.Notice(), "REFUSED") {
		t.Errorf("notice = %q, want a REFUSED notice", next.Notice())
	}

	// 🔴 THE STRUCTURAL HALF, AND IT IS THE ONE THAT MATTERS: no prompt was
	// built at all, so there is no sentence anywhere naming a method the
	// operator did not choose. This cannot be walked around by rewording.
	if next.PendingPrompt() != "" {
		t.Errorf("a confirmation prompt was built for a merge with no method: %q",
			next.PendingPrompt())
	}

	// ⚠ AND A TRIPWIRE ON THE PROSE, LABELLED AS ONE. The prompt renders a
	// method UPPERCASED (`using REBASE`), so that is the shape a leak would
	// take; the list is derived from `cfg` rather than restated, so a fourth
	// method is covered without an edit here. It is NOT a proof — a leak
	// spelled some other way is invisible to it, which is why the structural
	// assertion above leads.
	//
	// ⚠ IT DELIBERATELY DOES NOT MATCH THE LOWERCASE WORDS. An earlier version
	// did, and failed on the refusal's own sentence ("a merge dispatched with a
	// method nobody chose") — a guard on words, red for the wrong reason.
	for _, m := range cfg.MergeMethods() {
		if strings.Contains(next.Notice(), strings.ToUpper(m)) {
			t.Errorf("the refusal names the method %q, which is exactly the guess "+
				"it exists to avoid: %q", m, next.Notice())
		}
	}
}

// --- approve -----------------------------------------------------------------

func TestApproveConfirmsFirstAndThenEmitsOneIntent(t *testing.T) {
	next, intents := pressAll(ready(t), "a")
	if len(intents) != 0 {
		t.Fatalf("`a` emitted %v — approve is a CONFIRMED verb", intents)
	}
	if next.Mode() != ModeConfirm {
		t.Fatalf("`a` left mode %s, want CONFIRM", next.Mode().Word())
	}

	final, emitted := pressAll(ready(t), "a", "y")
	want := []Intent{Approve{Owner: fxOwner, Name: fxName, Num: fxNum}}
	if !intentsEqual(emitted, want) {
		t.Fatalf("`a` then `y` emitted %v, want %v", emitted, want)
	}
	if final.Mode() != ModeBrowse {
		t.Errorf("mode = %s, want BROWSE", final.Mode().Word())
	}
}

// --- compose: comment, request changes, submit review ------------------------

// 🔴 THE COMPOSE BUFFER TAKES TEXT, AND THE BINDINGS STILL WIN OVER IT. A
// character that is also a binding in browse mode (`q`, `m`, `y`) must be TYPED
// here, not acted on — the modes are disjoint — while `ctrl+d` must act, not be
// typed.
func TestComposingTypesTextAndDoesNotActOnBrowseKeys(t *testing.T) {
	a, intents := pressAll(ready(t), "c", "q", "m", "y", " ", "o", "k")
	if len(intents) != 0 {
		t.Fatalf("typing into the compose buffer emitted %v", intents)
	}
	if a.Quitting {
		t.Fatal("typing `q` into a comment QUIT the program")
	}
	if got, want := a.ComposeBody(), "qmy ok"; got != want {
		t.Errorf("compose body = %q, want %q", got, want)
	}
}

func TestComposingACommentSendsWithoutAConfirmation(t *testing.T) {
	a, intents := pressAll(ready(t), "c", "h", "i")
	if len(intents) != 0 {
		t.Fatalf("composing emitted %v before the send key", intents)
	}
	next, emitted := a.Step(keyPress("ctrl+d"))

	want := []Intent{PostComment{Owner: fxOwner, Name: fxName, Num: fxNum, Body: "hi"}}
	if !intentsEqual(emitted, want) {
		t.Fatalf("sending a comment emitted %v, want %v", emitted, want)
	}
	// §3.7: a comment is NOT confirmed — additive and trivially reversible.
	if next.Mode() != ModeBrowse {
		t.Errorf("mode after sending a comment = %s, want BROWSE (no prompt)", next.Mode().Word())
	}
	if next.ComposeBody() != "" {
		t.Errorf("the buffer survived the send: %q", next.ComposeBody())
	}
}

// ⚠ REQUEST CHANGES AND SUBMIT REVIEW COMPOSE *AND* CONFIRM. They are the two
// verbs that do both, so they are the ones where a missing prompt would be
// easiest to miss.
func TestComposingAReviewVerbStillAsksBeforeSending(t *testing.T) {
	cases := []struct {
		key  string
		want Intent
	}{
		{"R", RequestChanges{Owner: fxOwner, Name: fxName, Num: fxNum, Body: "no"}},
		{"v", SubmitReview{Owner: fxOwner, Name: fxName, Num: fxNum, Body: "no"}},
	}
	for _, c := range cases {
		t.Run(c.key, func(t *testing.T) {
			a, intents := pressAll(ready(t), c.key, "n", "o", "ctrl+d")
			if len(intents) != 0 {
				t.Fatalf("composing and sending %q emitted %v BEFORE the prompt", c.key, intents)
			}
			if a.Mode() != ModeConfirm {
				t.Fatalf("mode = %s, want CONFIRM", a.Mode().Word())
			}
			final, emitted := a.Step(keyPress("y"))
			if !intentsEqual(emitted, []Intent{c.want}) {
				t.Fatalf("confirming emitted %v, want %v", emitted, c.want)
			}
			if final.Mode() != ModeBrowse {
				t.Errorf("mode = %s, want BROWSE", final.Mode().Word())
			}
		})
	}
}

func TestAnEmptyComposeBufferRefusesToSend(t *testing.T) {
	// A buffer of nothing but whitespace is empty: GitHub answers 422 for an
	// empty REQUEST_CHANGES body, and an empty comment is one nobody meant.
	a, intents := pressAll(ready(t), "c", " ", " ", "ctrl+d")
	if len(intents) != 0 {
		t.Fatalf("sending an empty body emitted %v", intents)
	}
	if !strings.HasPrefix(a.Notice(), "REFUSED") {
		t.Errorf("notice = %q, want REFUSED", a.Notice())
	}
	if a.Mode() != ModeCompose {
		t.Errorf("mode = %s, want COMPOSING — the refusal must not throw the text away",
			a.Mode().Word())
	}
}

func TestEscapeDiscardsAComposeBufferAndSaysSo(t *testing.T) {
	a, intents := pressAll(ready(t), "c", "h", "i", "esc")
	if len(intents) != 0 {
		t.Fatalf("discarding emitted %v", intents)
	}
	if a.Mode() != ModeBrowse || a.ComposeBody() != "" {
		t.Errorf("mode = %s body = %q, want BROWSE and empty", a.Mode().Word(), a.ComposeBody())
	}
	if !strings.HasPrefix(a.Notice(), "DISCARDED") {
		t.Errorf("notice = %q, want DISCARDED", a.Notice())
	}
}

// The editing keys, pressed. Backspace and the cursor are the only two ways a
// body can be wrong in a way the operator cannot see before sending.
func TestComposeEditingKeysMoveAndDelete(t *testing.T) {
	a, _ := pressAll(ready(t), "c", "a", "b", "c")
	if got := a.ComposeBody(); got != "abc" {
		t.Fatalf("body = %q, want %q", got, "abc")
	}
	a, _ = a.Step(keyPress("backspace"))
	if got := a.ComposeBody(); got != "ab" {
		t.Errorf("after backspace body = %q, want %q", got, "ab")
	}
	// left, then insert: the rune lands BEFORE the last character.
	a, _ = pressAll(a, "left", "X")
	if got := a.ComposeBody(); got != "aXb" {
		t.Errorf("after left+X body = %q, want %q", got, "aXb")
	}
	// right, then backspace: deletes the character now behind the cursor.
	a, _ = pressAll(a, "right", "backspace")
	if got := a.ComposeBody(); got != "aX" {
		t.Errorf("after right+backspace body = %q, want %q", got, "aX")
	}
	// enter is a NEWLINE here, not a send.
	a, _ = pressAll(a, "enter", "z")
	if got := a.ComposeBody(); got != "aX\nz" {
		t.Errorf("after enter+z body = %q, want %q", got, "aX\nz")
	}
}

// --- the gate ----------------------------------------------------------------

// 🔴 §10.2 MADE CONSEQUENTIAL. With no viewer login there is no way to say
// which account would act, so EVERY write verb refuses — not just the confirmed
// ones. cli/cli#14370 is the reason: the resolved token may belong to a
// different account than the one the config calls active.
func TestNoWriteVerbActsWhenTheViewerLoginIsUnknown(t *testing.T) {
	s := fixturePR()
	s.ViewerLogin = ""
	base := New(fxOwner, fxName, fxNum)
	base.Width, base.Height = 140, 40
	base.SetMergeMethod(fxMergeMethod)
	base, _ = base.Step(PRLoaded{Snap: s})

	for _, k := range []string{"c", "a", "R", "v", "m"} {
		next, intents := base.Step(keyPress(k))
		if len(intents) != 0 {
			t.Errorf("with no viewer login, %q emitted %v", k, intents)
		}
		if next.Mode() != ModeBrowse {
			t.Errorf("with no viewer login, %q entered mode %s — it must refuse outright",
				k, next.Mode().Word())
		}
		if !strings.HasPrefix(next.Notice(), "REFUSED") {
			t.Errorf("with no viewer login, %q gave notice %q, want REFUSED", k, next.Notice())
		}
		if !strings.Contains(next.Notice(), "cli/cli#14370") {
			t.Errorf("the refusal does not name the hazard it is mitigating: %q", next.Notice())
		}
	}

	// 🔴 POSITIVE CONTROL: the SAME keys on a snapshot that HAS a login must
	// get through. Without it, a gate that refused everything unconditionally
	// would pass every assertion above.
	ok := ready(t)
	for _, k := range []string{"c", "a", "R", "v", "m"} {
		next, _ := ok.Step(keyPress(k))
		if next.Mode() == ModeBrowse {
			t.Errorf("with a viewer login, %q was still refused: %q", k, next.Notice())
		}
	}
}

// §4: the issue card is TERMINAL — no comment posting, no labels, no
// close/reopen. Every write verb refuses on an issue.
func TestNoWriteVerbActsOnAnIssue(t *testing.T) {
	base := New(fxOwner, fxName, fxNum)
	base.Width, base.Height = 140, 40
	base.SetMergeMethod(fxMergeMethod)
	base, _ = base.Step(PRLoaded{Snap: fixtureIssue()})

	for _, k := range []string{"c", "a", "R", "v", "m"} {
		next, intents := base.Step(keyPress(k))
		if len(intents) != 0 || next.Mode() != ModeBrowse {
			t.Errorf("on an issue, %q emitted %v and entered %s",
				k, intents, next.Mode().Word())
		}
		if !strings.Contains(next.Notice(), "ISSUE") {
			t.Errorf("on an issue, %q gave notice %q, want it to say ISSUE", k, next.Notice())
		}
	}
}

func TestNoWriteVerbActsBeforeThePullRequestHasLoaded(t *testing.T) {
	base := New(fxOwner, fxName, fxNum)
	base.SetMergeMethod(fxMergeMethod)
	for _, k := range []string{"c", "a", "R", "v", "m"} {
		next, intents := base.Step(keyPress(k))
		if len(intents) != 0 || next.Mode() != ModeBrowse {
			t.Errorf("while loading, %q emitted %v and entered %s",
				k, intents, next.Mode().Word())
		}
	}
}

// 🔴 THE GATE IS RE-EVALUATED AT THE MOMENT OF THE WRITE, not only when compose
// opened. A refetch can replace the snapshot while the operator is typing, and
// a gate checked when the action was motivated is a REMEMBERED fact about a
// state that may no longer hold.
func TestASnapshotThatLosesItsLoginMidComposeStopsTheSend(t *testing.T) {
	a, _ := pressAll(ready(t), "c", "h", "i")
	if a.Mode() != ModeCompose {
		t.Fatalf("fixture is wrong: mode = %s", a.Mode().Word())
	}
	// A refetch lands while the buffer is open, and this one has no login.
	degraded := fixturePR()
	degraded.ViewerLogin = ""
	a, _ = a.Step(PRLoaded{Snap: degraded})

	next, intents := a.Step(keyPress("ctrl+d"))
	if len(intents) != 0 {
		t.Fatalf("the send went through against a snapshot with no login: %v", intents)
	}
	if !strings.HasPrefix(next.Notice(), "REFUSED") {
		t.Errorf("notice = %q, want REFUSED", next.Notice())
	}
}

// --- the intent → API-call mapping -------------------------------------------

// callRecorder is a Runner that records the arguments it was handed.
type callRecorder struct{ calls []string }

func (c *callRecorder) FetchPR(context.Context, string, string, int) (*ghapi.Snapshot, error) {
	return fixturePR(), nil
}
func (c *callRecorder) FetchDiff(context.Context, string, string, int) (*udiff.Diff, error) {
	return &udiff.Diff{}, nil
}
func (c *callRecorder) OpenBrowser(string) error { return nil }
func (c *callRecorder) PostComment(_ context.Context, o, n string, num int, body string) error {
	c.calls = append(c.calls, fmt.Sprintf("PostComment %s/%s#%d body=%q", o, n, num, body))
	return nil
}
func (c *callRecorder) SubmitReview(_ context.Context, o, n string, num int, event, body string) error {
	c.calls = append(c.calls, fmt.Sprintf("SubmitReview %s/%s#%d event=%s body=%q", o, n, num, event, body))
	return nil
}
func (c *callRecorder) Merge(_ context.Context, o, n string, num int, method, mergeable string) error {
	c.calls = append(c.calls, fmt.Sprintf("Merge %s/%s#%d method=%s mergeable=%s", o, n, num, method, mergeable))
	return nil
}

// 🔴 THE HIGHEST-CONSEQUENCE MAPPING IN THIS PHASE, AND IT LIVES IN `Run` WHERE
// NOTHING ELSE CAN SEE IT.
//
// Three DIFFERENT intents — approve, request changes, submit review — collapse
// onto ONE endpoint, distinguished only by an `event` string. A swap there
// approves where the operator asked to BLOCK, and every other test in this
// package passes: `Step` emitted the right intent, the ledger agreed, the
// prompt named the right verb, and the wrong verdict went to GitHub.
//
// ⚠ IT RUNS THE RETURNED `tea.Cmd`. A `tea.Cmd` is an opaque `func() tea.Msg`,
// so asserting on the value is impossible — but CALLING it against a recording
// runner is not, and that is the only way this mapping is observable.
func TestRunMapsEachWriteIntentToItsOwnCall(t *testing.T) {
	cases := []struct {
		intent Intent
		want   string
	}{
		{
			PostComment{Owner: fxOwner, Name: fxName, Num: fxNum, Body: "a note"},
			`PostComment gardenersguild/trowelcast#1559 body="a note"`,
		},
		{
			Approve{Owner: fxOwner, Name: fxName, Num: fxNum},
			`SubmitReview gardenersguild/trowelcast#1559 event=APPROVE body=""`,
		},
		{
			RequestChanges{Owner: fxOwner, Name: fxName, Num: fxNum, Body: "split this"},
			`SubmitReview gardenersguild/trowelcast#1559 event=REQUEST_CHANGES body="split this"`,
		},
		{
			SubmitReview{Owner: fxOwner, Name: fxName, Num: fxNum, Body: "a remark"},
			`SubmitReview gardenersguild/trowelcast#1559 event=COMMENT body="a remark"`,
		},
		{
			// ⚠ `CONFLICTING`, WHICH THE FIXTURE SNAPSHOT DOES NOT CARRY. The
			// fixture says MERGEABLE, so a `Run` that ignored the intent's field
			// and read the snapshot's — or that hardcoded the common value —
			// would produce `mergeable=MERGEABLE` here and be caught.
			MergePR{Owner: fxOwner, Name: fxName, Num: fxNum, Method: fxMergeMethod,
				Mergeable: ghapi.MergeableNo},
			`Merge gardenersguild/trowelcast#1559 method=rebase mergeable=CONFLICTING`,
		},
	}
	for _, c := range cases {
		t.Run(c.intent.intentName(), func(t *testing.T) {
			rec := &callRecorder{}
			cmd := Run(c.intent, rec)
			if cmd == nil {
				t.Fatal("Run returned no command")
			}
			msg := cmd()
			if len(rec.calls) != 1 || rec.calls[0] != c.want {
				t.Fatalf("calls = %q, want exactly [%q]", rec.calls, c.want)
			}
			// 🔴 AND THE RESULT MESSAGE NAMES THE VERB. A `WriteDone` carrying
			// the wrong verb would put the wrong word in the notice, which is
			// the only thing the operator reads after the write.
			done, ok := msg.(WriteDone)
			if !ok {
				t.Fatalf("the command returned %T, want WriteDone", msg)
			}
			if done.Verb != c.intent.intentName() {
				t.Errorf("WriteDone.Verb = %q, want %q", done.Verb, c.intent.intentName())
			}
			if done.Err != nil {
				t.Errorf("WriteDone.Err = %v, want nil", done.Err)
			}
		})
	}

	// 🔴 THE THREE REVIEW EVENTS MUST BE THREE DISTINCT STRINGS. Without this,
	// a `ghapi` change that collapsed two of the constants onto one value would
	// leave every case above passing — each would assert the value it was given.
	seen := map[string]bool{}
	for _, e := range ghapi.ReviewEvents() {
		if seen[e] {
			t.Errorf("the review event %q is spelled twice — two verbs would "+
				"submit the same verdict", e)
		}
		seen[e] = true
	}
	if len(seen) != 3 {
		t.Errorf("there are %d distinct review events, want 3", len(seen))
	}
}

// --- the result --------------------------------------------------------------

func TestASuccessfulWriteRereadsThePullRequest(t *testing.T) {
	a := ready(t)
	next, intents := a.Step(WriteDone{Verb: "MergePR"})
	want := []Intent{FetchPR{Owner: fxOwner, Name: fxName, Num: fxNum}}
	if !intentsEqual(intents, want) {
		t.Fatalf("a successful write emitted %v, want %v — every panel still "+
			"describes the PR as it was BEFORE the write", intents, want)
	}
	if !strings.Contains(next.Notice(), "MergePR") {
		t.Errorf("notice = %q, want it to name the verb", next.Notice())
	}
}

func TestAFailedWriteIsANoticeAndNotAPageFailure(t *testing.T) {
	a := ready(t)
	next, intents := a.Step(WriteDone{
		Verb: "Approve",
		Err:  &ghapi.APIError{State: ghapi.AuthRejected, Detail: "Bad credentials"},
	})
	if len(intents) != 0 {
		t.Errorf("a failed write emitted %v, want nothing", intents)
	}
	if next.Load != LoadReady || next.Snap == nil {
		t.Error("a failed write replaced the loaded page — the panels are still true")
	}
	if !strings.HasPrefix(next.Notice(), "FAILED") || !strings.Contains(next.Notice(), "Approve") {
		t.Errorf("notice = %q, want FAILED naming the verb", next.Notice())
	}
}
