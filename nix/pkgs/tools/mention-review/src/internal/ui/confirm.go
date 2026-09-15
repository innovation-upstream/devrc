package ui

import (
	"strings"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
)

// 🔴 THE CONFIRMATION PROMPT, BUILT IN EXACTLY ONE PLACE (§3.7, §5.3(c)).
//
// The artifact under test is PROSE, so a guard on WORDS is walkable by
// rewording: a test asserting the prompt "contains SQUASH" passes over a
// sentence that has been rearranged into a lie. `confirm_test.go` therefore
// pins the WHOLE NORMALISED STRING per verb, against a fixture. A cosmetic
// reword fails that test. That cost is paid deliberately, for a
// machine-readable claim about what the operator is shown before an
// irreversible action.
//
// 🔴 THE `as <login>` CLAUSE IS PART OF THE PINNED STRING, AND THAT IS THE
// §10.2 MITIGATION. cli/cli#14370: the OS keyring is not partitioned by
// account, so `gh auth token --secure-storage` can return a token belonging to
// a DIFFERENT account than the config's active one — and this host's hosts.yml
// carries two github.com users. For a read-only tool that is a curiosity. For
// one that can approve and merge it is the difference between approving as
// yourself and approving as somebody else, invisibly. Pinning the clause in the
// whole-string assertion is what stops a later reword dropping it.

// maxPromptTitle caps the title inside a prompt.
//
// ⚠ NOT A COSMETIC CAP. The prompt is a single line the operator reads under
// time pressure; a 200-character PR title would push `as <login>` and `[y/N]`
// off the end of an 80-column pane, which is the one part that must never be
// the part that scrolls away. The truncation is marked with `…` so a shortened
// title cannot be mistaken for the whole one.
const maxPromptTitle = 60

// Confirmable is the subset of the ledger this file can render a prompt for.
// 🔴 It is derived from `Confirmed` rather than restated: a verb added to the
// ledger with no prompt arm below fails `confirm_test.go` rather than merging
// with an empty sentence.
func promptVerb(i Intent) (verb, consequence string, ok bool) {
	switch i.(type) {
	case Approve:
		return "approve", "this posts a public review from that account", true
	case RequestChanges:
		return "request changes on", "this posts a public review from that account", true
	case SubmitReview:
		return "submit review on", "this posts a public review from that account", true
	case MergePR:
		return "merge", "this cannot be undone", true
	}
	return "", "", false
}

// ConfirmPrompt builds the whole confirmation line for one write intent.
//
// It returns ok=false when the prompt CANNOT be built truthfully, and the
// caller must then refuse rather than act:
//
//   - the intent is not a confirmed verb (a caller bug — the ledger disagrees
//     with this file, which `confirm_test.go` pins two-way);
//   - there is no snapshot, so there is no title to name;
//   - 🔴 the authenticated login is UNKNOWN, so the prompt cannot say who the
//     action would act as. That is precisely the §10.2 hazard, so a missing
//     login is a refusal and never a prompt with the clause quietly omitted.
func ConfirmPrompt(i Intent, s *ghapi.Snapshot) (string, bool) {
	verb, consequence, ok := promptVerb(i)
	if !ok || s == nil || s.ViewerLogin == "" {
		return "", false
	}

	var b strings.Builder
	b.WriteString(verb)
	b.WriteString(" ")
	b.WriteString(intentRef(i))
	b.WriteString(` "`)
	b.WriteString(promptTitle(s.Title))
	b.WriteString(`"`)
	if m, isMerge := i.(MergePR); isMerge {
		if m.Method == "" {
			// A merge whose method is unknown has no prompt: `app.go` refuses
			// before reaching here, and this arm means the two disagreed.
			return "", false
		}
		b.WriteString(" using ")
		b.WriteString(strings.ToUpper(m.Method))
	}
	b.WriteString(" as ")
	b.WriteString(s.ViewerLogin)
	b.WriteString(" — ")
	b.WriteString(consequence)
	b.WriteString(". [y/N]")
	return normalisePrompt(b.String()), true
}

// intentRef renders `owner/name#num` FROM THE INTENT, never from the snapshot.
//
// 🔴 THE PROMPT MUST NAME WHAT WILL BE SENT. The intent is the value the runner
// hands to the API; a prompt built from the snapshot would keep saying the
// right thing while the intent carried something else.
func intentRef(i Intent) string {
	switch v := i.(type) {
	case Approve:
		return v.Owner + "/" + v.Name + "#" + itoa(v.Num)
	case RequestChanges:
		return v.Owner + "/" + v.Name + "#" + itoa(v.Num)
	case SubmitReview:
		return v.Owner + "/" + v.Name + "#" + itoa(v.Num)
	case MergePR:
		return v.Owner + "/" + v.Name + "#" + itoa(v.Num)
	case PostComment:
		return v.Owner + "/" + v.Name + "#" + itoa(v.Num)
	}
	return ""
}

func promptTitle(title string) string {
	t := normalisePrompt(title)
	r := []rune(t)
	if len(r) <= maxPromptTitle {
		return t
	}
	return string(r[:maxPromptTitle-1]) + "…"
}

// normalisePrompt collapses every run of whitespace to one space and trims the
// ends, so the pinned string is stable under a title containing a newline or a
// tab and the assertion compares meaning rather than layout.
func normalisePrompt(s string) string { return strings.Join(strings.Fields(s), " ") }

// ComposeHeader is the line above the compose buffer.
//
// 🔴 IT NAMES THE LOGIN TOO, and for the same reason the confirmation does. A
// comment is NOT confirmed (§3.7: additive and trivially reversible), so the
// compose header is the only place the operator sees who they are about to
// speak as. §10.2 says the login must appear "in every confirmation string for
// an outward action"; a posted comment is outward, so the mitigation is carried
// here rather than skipped because this verb happens not to prompt.
func ComposeHeader(verb Action, ref, login string) string {
	var what string
	switch verb {
	case ActComment:
		what = "comment on"
	case ActRequestChanges:
		what = "REQUEST CHANGES on"
	case ActSubmitReview:
		what = "review of"
	default:
		what = "message for"
	}
	return normalisePrompt("COMPOSING " + what + " " + ref + " as " + login)
}
