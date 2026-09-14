package ui

import (
	"errors"
	"fmt"
	"strings"

	"github.com/innovation-upstream/devrc/mention-review/internal/ghapi"
)

// 🔴 A WINDOW THAT FLASHES AND VANISHES IS THE WORST OUTCOME AND MUST NEVER
// HAPPEN.
//
// Alacritty exits 0 whether its `-e` command exits 0 or 127, so an exit code
// teaches the operator nothing. Every failure below therefore renders a
// readable card and WAITS FOR `q` — it does not exit, and it does not leave a
// blank pane that reads as "GitHub is down".
//
// The one exception is malformed argv, which exits 64/65/66 BEFORE anything is
// drawn — the same contract `nvim-octo` has today, and the only case where
// there is no window to keep open.

// errorCardBody is the prose under the state WORD.
func errorCardBody(repo string, num int, err error) string {
	var ae *ghapi.APIError
	if !errors.As(err, &ae) {
		return strings.Join([]string{
			fmt.Sprintf("%s#%d", repo, num),
			"",
			err.Error(),
			"",
			"`o` opens it in the browser · `r` retries · `q` closes",
		}, "\n")
	}

	lines := []string{fmt.Sprintf("%s#%d", repo, num), ""}
	switch ae.State {
	case ghapi.AuthNoToken:
		// 🔴 EXPLICITLY DISTINGUISHED FROM A 401, BECAUSE THE FIXES DIFFER.
		// go-gh's fourth rung returns ("", "default") with NO error, so an
		// empty token is a value rather than a failure — a client that checked
		// only `err` would show a 401 card here and send the operator to debug
		// a token that does not exist.
		lines = append(lines,
			"No GitHub token could be resolved for github.com.",
			"",
			"Run `gh auth login`.")
	case ghapi.AuthRejected:
		lines = append(lines,
			"A token was found and GitHub refused it.",
			"",
			"It may be expired, revoked, or missing the scopes this needs.",
			"Run `gh auth status` and then `gh auth login` if needed.")
	case ghapi.AuthNotFound:
		lines = append(lines,
			ae.Detail,
			"",
			"A browser session may have access this token does not —")
	case ghapi.AuthRateLimited:
		if !ae.ResetAt.IsZero() {
			lines = append(lines,
				"GitHub's rate limit is spent.",
				"",
				"Resets at "+ae.ResetAt.Local().Format("15:04")+".")
		} else {
			lines = append(lines, "GitHub's rate limit is spent.")
		}
	default:
		lines = append(lines, ae.Detail)
	}
	lines = append(lines, "", "`o` opens it in the browser · `r` retries · `q` closes")
	return strings.Join(lines, "\n")
}

// errorsAs is a tiny shim so panels.go does not import `errors` purely for one
// call — and so the two files cannot disagree about how an APIError is
// recognised.
func errorsAs(err error, target **ghapi.APIError) bool {
	return errors.As(err, target)
}
