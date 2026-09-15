package ui

import (
	"strings"
	"testing"
)

// 🔴 LAYER 3(c) — THE CONFIRMATION TEXT, PINNED AS A WHOLE NORMALISED STRING.
//
// The artifact under test is PROSE, and a guard on WORDS is walkable by
// REWORDING: "the prompt contains SQUASH" passes over a sentence rearranged
// into a lie. So the WHOLE string is pinned per verb. A cosmetic reword then
// fails this test. 🔴 THAT COST IS PAID DELIBERATELY — the alternative is a
// machine-readable claim about what the operator is shown before an
// irreversible action that is not actually machine-readable.
//
// 🔴 THE `as <login>` CLAUSE IS INSIDE EVERY PINNED STRING. That is the §10.2
// mitigation for cli/cli#14370, and pinning it is what stops a later reword
// dropping it.

// promptFixtures is the ledger of expected prompts, keyed by the key that
// raises each one. 🔴 THE EXPECTATIONS ARE LITERALS WRITTEN OUT BY HAND, never
// derived from `ConfirmPrompt` — a test whose expectation came from the
// implementation it tests asserts only that the implementation is itself.
var promptFixtures = []struct {
	name string
	keys []string
	want string
}{
	{
		name: "merge",
		keys: []string{"m"},
		want: `merge gardenersguild/trowelcast#1559 "Refresh the stale context before running" ` +
			`using REBASE as a-reviewer — this cannot be undone. [y/N]`,
	},
	{
		name: "approve",
		keys: []string{"a"},
		want: `approve gardenersguild/trowelcast#1559 "Refresh the stale context before running" ` +
			`as a-reviewer — this posts a public review from that account. [y/N]`,
	},
	{
		name: "request changes",
		keys: []string{"R", "n", "o", "ctrl+d"},
		want: `request changes on gardenersguild/trowelcast#1559 "Refresh the stale context before running" ` +
			`as a-reviewer — this posts a public review from that account. [y/N]`,
	},
	{
		name: "submit review",
		keys: []string{"v", "n", "o", "ctrl+d"},
		want: `submit review on gardenersguild/trowelcast#1559 "Refresh the stale context before running" ` +
			`as a-reviewer — this posts a public review from that account. [y/N]`,
	},
}

func TestTheConfirmationPromptIsPinnedWholeForEveryConfirmedVerb(t *testing.T) {
	for _, f := range promptFixtures {
		t.Run(f.name, func(t *testing.T) {
			a, _ := pressAll(ready(t), f.keys...)
			if a.Mode() != ModeConfirm {
				t.Fatalf("%v did not raise a confirmation (mode %s, notice %q)",
					f.keys, a.Mode().Word(), a.Notice())
			}
			if got := a.PendingPrompt(); got != f.want {
				t.Errorf("the confirmation prompt has changed.\n got: %s\nwant: %s", got, f.want)
			}
		})
	}
}

// 🔴 AND THE STRING ON SCREEN IS THAT SAME STRING. Pinning the value of a field
// nobody renders is the DTO-field trap: a type declaration is not a code path.
// This renders the frame and looks for the whole sentence in it, with colour
// stripped — the operator's font cannot carry meaning in colour.
func TestTheConfirmationPromptIsWhatTheScreenSHOWS(t *testing.T) {
	for _, f := range promptFixtures {
		t.Run(f.name, func(t *testing.T) {
			a, _ := pressAll(ready(t), f.keys...)
			screen := stripANSI(a.render())
			if !strings.Contains(screen, f.want) {
				t.Errorf("the rendered screen does not carry the confirmation.\n"+
					"want: %s\nscreen:\n%s", f.want, screen)
			}
			// The mode word is on screen too, because "a prompt is showing" is
			// itself a meaning-bearing state.
			if !strings.Contains(screen, ModeConfirm.Word()) {
				t.Errorf("the screen does not carry the word %q", ModeConfirm.Word())
			}
		})
	}
}

// 🔴 EVERY CONFIRMED VERB IN THE LEDGER HAS A PROMPT, AND EVERY PROMPT BELONGS
// TO A LEDGERED VERB. Two-way, because either half alone permits the other's
// failure: a verb added to `Confirmed` with no prompt arm would refuse at run
// time in a way only the operator would ever see, and a prompt arm for a verb
// nobody confirms is prose guarding nothing.
func TestThePromptArmsAndTheConfirmedLedgerAgree(t *testing.T) {
	snap := fixturePR()
	withPrompt := map[string]bool{}
	for _, i := range KnownIntents() {
		// Build a representative with a method, so MergePR's method arm does
		// not fail this for the wrong reason.
		if _, isMerge := i.(MergePR); isMerge {
			i = MergePR{Owner: fxOwner, Name: fxName, Num: fxNum, Method: fxMergeMethod}
		}
		if _, ok := ConfirmPrompt(i, snap); ok {
			withPrompt[i.intentName()] = true
		}
	}
	for name := range Confirmed {
		if !withPrompt[name] {
			t.Errorf("CONFIRMED lists %q but ConfirmPrompt cannot build a prompt for "+
				"it — at run time that verb refuses, and only the operator finds out", name)
		}
	}
	for name := range withPrompt {
		if !Confirmed[name] {
			t.Errorf("ConfirmPrompt builds a prompt for %q, which is not in CONFIRMED "+
				"— prose guarding nothing", name)
		}
	}
	if len(withPrompt) == 0 {
		t.Fatal("no intent has a prompt at all — this guard observed nothing")
	}
}

// 🔴 NO LOGIN, NO PROMPT. §10.2's mitigation is only real if the absence of the
// login is a REFUSAL rather than a sentence with the clause quietly missing.
func TestNoPromptIsBuiltWithoutAViewerLogin(t *testing.T) {
	snap := fixturePR()
	snap.ViewerLogin = ""
	i := MergePR{Owner: fxOwner, Name: fxName, Num: fxNum, Method: fxMergeMethod}
	if got, ok := ConfirmPrompt(i, snap); ok {
		t.Errorf("a prompt was built with no viewer login: %q", got)
	}
	// POSITIVE CONTROL: the same intent against the same snapshot WITH a login
	// must produce one, or this test would pass against a function that never
	// builds anything.
	if _, ok := ConfirmPrompt(i, fixturePR()); !ok {
		t.Fatal("no prompt is built even with a login — this guard measures nothing")
	}
}

// 🔴 NO METHOD, NO PROMPT. The second lock on the `MERGE_METHOD_UNKNOWN` door:
// even if the UI's own refusal were removed, a merge with no method could not
// produce a sentence to confirm.
func TestNoPromptIsBuiltForAMergeWithNoMethod(t *testing.T) {
	if got, ok := ConfirmPrompt(MergePR{Owner: fxOwner, Name: fxName, Num: fxNum}, fixturePR()); ok {
		t.Errorf("a merge prompt was built with no method: %q", got)
	}
}

// A comment is NOT a confirmed verb (§3.7), so it has no prompt — and that is
// asserted rather than left implied, because "no prompt" and "a prompt nobody
// found" look identical from outside.
func TestAnUnconfirmedVerbHasNoPrompt(t *testing.T) {
	if got, ok := ConfirmPrompt(PostComment{Owner: fxOwner, Name: fxName, Num: fxNum, Body: "x"}, fixturePR()); ok {
		t.Errorf("PostComment produced a confirmation prompt: %q", got)
	}
	if _, ok := ConfirmPrompt(FetchPR{}, fixturePR()); ok {
		t.Error("a READ intent produced a confirmation prompt")
	}
}

// ⚠ A LONG TITLE IS TRUNCATED, AND `as <login> … [y/N]` SURVIVES. The tail is
// the part that must never be the part that scrolls away.
func TestALongTitleIsTruncatedAndTheTailSurvives(t *testing.T) {
	snap := fixturePR()
	snap.Title = strings.Repeat("wandering ", 30) // 300 characters
	got, ok := ConfirmPrompt(MergePR{Owner: fxOwner, Name: fxName, Num: fxNum, Method: fxMergeMethod}, snap)
	if !ok {
		t.Fatal("no prompt was built")
	}
	if !strings.Contains(got, "…") {
		t.Errorf("a 300-character title was not truncated: %s", got)
	}
	if !strings.HasSuffix(got, "as a-reviewer — this cannot be undone. [y/N]") {
		t.Errorf("the tail did not survive truncation: %s", got)
	}
	if len([]rune(got)) > 200 {
		t.Errorf("the prompt is %d runes long", len([]rune(got)))
	}
}

// A title carrying a newline or a tab must not break the line the operator
// reads, and must not make the pinned assertions above layout-dependent.
func TestAPromptIsNormalisedToOneLine(t *testing.T) {
	snap := fixturePR()
	snap.Title = "first line\n\tsecond   line"
	got, ok := ConfirmPrompt(Approve{Owner: fxOwner, Name: fxName, Num: fxNum}, snap)
	if !ok {
		t.Fatal("no prompt was built")
	}
	if strings.ContainsAny(got, "\n\t") {
		t.Errorf("the prompt carries raw whitespace: %q", got)
	}
	if !strings.Contains(got, `"first line second line"`) {
		t.Errorf("the title was not normalised: %q", got)
	}
}

// --- the compose header ------------------------------------------------------

// 🔴 THE LOGIN IS ON THE COMPOSE BAR TOO, AND THAT IS NOT DECORATION. A comment
// is outward and is NOT confirmed, so the compose bar is the only place the
// operator sees which account is about to speak. §10.2 requires the login in
// every confirmation for an outward action; this is that requirement applied to
// the one outward verb that has no confirmation.
func TestTheComposeBarNamesTheLogin(t *testing.T) {
	cases := []struct {
		key  string
		want string
	}{
		{"c", "COMPOSING comment on gardenersguild/trowelcast#1559 as a-reviewer"},
		{"R", "COMPOSING REQUEST CHANGES on gardenersguild/trowelcast#1559 as a-reviewer"},
		{"v", "COMPOSING review of gardenersguild/trowelcast#1559 as a-reviewer"},
	}
	for _, c := range cases {
		t.Run(c.key, func(t *testing.T) {
			a, _ := pressAll(ready(t), c.key)
			screen := stripANSI(a.render())
			if !strings.Contains(screen, c.want) {
				t.Errorf("the compose bar does not carry %q\nscreen:\n%s", c.want, screen)
			}
		})
	}
}

// The typed body is visible before it is sent. A compose bar that accepted keys
// and showed nothing would be indistinguishable from one that dropped them.
func TestTheComposeBarShowsWhatWasTyped(t *testing.T) {
	a, _ := pressAll(ready(t), "c", "h", "e", "l", "l", "o")
	screen := stripANSI(a.render())
	if !strings.Contains(screen, "hello") {
		t.Errorf("the compose bar does not show the typed body\nscreen:\n%s", screen)
	}
}

// 🔴 THE FOOTER FOLLOWS THE MODE, AND THAT IS A SEPARATE CLAIM FROM THE LEDGER
// PASSING. `keys_test.go` asserts the dispatched and helped SETS agree per
// mode; it says nothing about which mode `renderFooter` actually asks for. A
// footer wired to `ModeBrowse` unconditionally would satisfy every ledger
// assertion and still show `q quit` while a merge confirmation was on screen —
// the field exists, and only a BRANCH on it is a guard.
func TestTheFooterFollowsTheMode(t *testing.T) {
	cases := []struct {
		name string
		keys []string
		mode Mode
	}{
		{"browse", nil, ModeBrowse},
		{"composing", []string{"c"}, ModeCompose},
		{"confirming", []string{"m"}, ModeConfirm},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			a, _ := pressAll(ready(t), c.keys...)
			if a.Mode() != c.mode {
				t.Fatalf("fixture is wrong: mode = %s, want %s", a.Mode().Word(), c.mode.Word())
			}
			footer := stripANSI(a.renderFooter())
			for _, b := range Keys.ShortHelpFor(c.mode) {
				h := b.Help()
				if !strings.Contains(footer, h.Desc) {
					t.Errorf("the footer does not carry %q for mode %s\nfooter: %s",
						h.Desc, c.mode.Word(), footer)
				}
			}
			// 🔴 AND THE OTHER MODES' HELP IS ABSENT. Without this half, a
			// footer that concatenated every mode's keys would pass the loop
			// above — and that footer is a legend that lies in all three modes
			// at once.
			for _, other := range Modes() {
				if other == c.mode {
					continue
				}
				for _, b := range Keys.ShortHelpFor(other) {
					d := b.Help().Desc
					if inShortHelp(c.mode, d) {
						continue // a description both modes legitimately share
					}
					if strings.Contains(footer, d) {
						t.Errorf("in mode %s the footer carries %q, which belongs "+
							"to mode %s\nfooter: %s", c.mode.Word(), d, other.Word(), footer)
					}
				}
			}
		})
	}
}

func inShortHelp(m Mode, desc string) bool {
	for _, b := range Keys.ShortHelpFor(m) {
		if b.Help().Desc == desc {
			return true
		}
	}
	return false
}

// The notice is rendered, not merely stored — the DTO-field trap again.
func TestANoticeReachesTheScreen(t *testing.T) {
	a := ready(t)
	a.SetMergeMethod("")
	a, _ = a.Step(keyPress("m"))
	if !strings.Contains(stripANSI(a.render()), "REFUSED") {
		t.Errorf("the refusal notice never reached the screen:\n%s", stripANSI(a.render()))
	}
}
