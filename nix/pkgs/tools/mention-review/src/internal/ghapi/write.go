package ghapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/innovation-upstream/devrc/mention-review/internal/cfg"
)

// 🔴 EVERY WRITE THIS BINARY CAN PERFORM IS IN THIS FILE, AND THERE ARE THREE.
//
// They are REST rather than GraphQL because the three mutations
// (`addComment`, `addPullRequestReview`, `mergePullRequest`) all need the
// node ID, which would cost a second round trip to look up, while the REST
// endpoints are addressable by `owner/name/number` — the argv this program
// already has.
//
// ⚠ NO TEST IN THIS MODULE EVER REACHES api.github.com. `SetBaseURLs` points
// the client at an `httptest` server, and `TestMain` installs an
// `http.DefaultTransport` that REFUSES any non-loopback host — see
// `nonet_test.go`, which carries the positive control showing that transport
// rejects a real GitHub URL.

// The review events GitHub accepts on `POST /pulls/{n}/reviews`.
//
// 🔴 CONSTANTS, NOT STRING LITERALS AT THE CALL SITES. `ui/run.go` maps three
// distinct intents onto this one endpoint, and a typo in one of those three
// would be a review submitted with the wrong verdict — approving where the
// operator asked to block.
const (
	ReviewApprove        = "APPROVE"
	ReviewRequestChanges = "REQUEST_CHANGES"
	ReviewComment        = "COMMENT"
)

// ReviewEvents enumerates the accepted events, so a test can walk them rather
// than restate them.
func ReviewEvents() []string {
	return []string{ReviewApprove, ReviewRequestChanges, ReviewComment}
}

func validReviewEvent(e string) bool {
	for _, ok := range ReviewEvents() {
		if e == ok {
			return true
		}
	}
	return false
}

// post is the shared write path: marshal, set the headers, call `do`, discard
// the body. 🔴 ONE PLACE, because every write must get the same auth handling,
// the same redaction and the same status classification — three copies would be
// three chances to drop one of those on the verb nobody re-read.
func (c *Client) write(ctx context.Context, method, url string, payload any) error {
	buf, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequest(method, url, bytes.NewReader(buf))
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("Content-Type", "application/json")
	_, err = c.do(ctx, req)
	return err
}

// PostComment posts a PR-LEVEL comment.
//
// 🔴 `/issues/{n}/comments` IS CORRECT FOR A PULL REQUEST AND IS NOT A BUG.
// GitHub models a PR as an issue for conversation purposes; this is the
// endpoint `gh pr comment` uses. The PR-review-comment endpoints
// (`/pulls/{n}/comments`) are the INLINE ones, which need a diff position, and
// inline commenting is explicitly out of scope for this phase (§12.4, answered
// by the operator: PR-level only). Nothing in this module computes a position.
func (c *Client) PostComment(ctx context.Context, owner, name string, num int, body string) error {
	if strings.TrimSpace(body) == "" {
		return &APIError{State: AuthOther, Detail: "refusing to post an empty comment"}
	}
	url := fmt.Sprintf("%s/repos/%s/%s/issues/%d/comments", c.rest, owner, name, num)
	return c.write(ctx, http.MethodPost, url, map[string]any{"body": body})
}

// SubmitReview submits a review with one of the three events.
func (c *Client) SubmitReview(ctx context.Context, owner, name string, num int, event, body string) error {
	if !validReviewEvent(event) {
		// 🔴 REFUSE RATHER THAN FORWARD. An unrecognised event reaching GitHub
		// is a 422 the operator has to decode; refusing here names the caller's
		// mistake, and the events are a closed set this program owns.
		return &APIError{State: AuthOther,
			Detail: fmt.Sprintf("refusing to submit review event %q: not one of %s",
				event, strings.Join(ReviewEvents(), ", "))}
	}
	if event == ReviewRequestChanges && strings.TrimSpace(body) == "" {
		// GitHub answers 422 for REQUEST_CHANGES with no body. Saying so here
		// is the difference between a legible refusal and a server error.
		return &APIError{State: AuthOther,
			Detail: "refusing to request changes with an empty body — GitHub requires one"}
	}
	url := fmt.Sprintf("%s/repos/%s/%s/pulls/%d/reviews", c.rest, owner, name, num)
	payload := map[string]any{"event": event}
	if body != "" {
		payload["body"] = body
	}
	return c.write(ctx, http.MethodPost, url, payload)
}

// The bound on the mergeability re-read below.
//
// 🔴 BOUNDED, AND SMALL. This poll sits between the operator's `y` and the
// merge, so every attempt is time they are watching a bar that says nothing.
// GitHub's recompute after a base-branch move normally settles in a second or
// two; four reads spread over ~2.1 s of waiting covers that without turning a
// keypress into a hang. Past the bound the answer is "ask again", never "wait
// longer" — an unbounded poll is how a UI stops being able to say what it is
// doing.
//
// ⚠ THE FIRST READ IS IMMEDIATE, AND THAT IS WHAT MAKES THE UNCONDITIONAL READ
// CHEAP. A merge whose live state is already resolved spends ONE round trip and
// no sleep at all; the interval is only ever paid by a state that came back
// UNKNOWN, which is the only case where waiting can change the answer.
const (
	mergeablePollAttempts = 4
	mergeablePollInterval = 700 * time.Millisecond
)

// awaitMergeable reads mergeability, and keeps reading while it says UNKNOWN,
// until it resolves or the bound is spent. It returns the last read and how many
// reads it took.
//
// 🔴 IT POLLS A READ. It does NOT retry a write: a merge that has already been
// dispatched and failed is the operator's call, not this loop's.
//
// 🔴 A TERMINAL PULL REQUEST ENDS THE LOOP ON THE SPOT. `mergeable` is UNKNOWN
// PERMANENTLY once a PR is merged or closed (measured — see `MergeableQuery`),
// so polling one spends the whole bound to learn nothing. The caller refuses on
// `Terminal` before it ever looks at `Mergeable`; breaking here is what stops
// that refusal costing the whole poll bound in silence first.
func (c *Client) awaitMergeable(ctx context.Context, owner, name string, num int) (MergeRead, int, error) {
	attempts := c.pollAttempts
	if attempts < 1 {
		attempts = 1
	}
	read := MergeRead{Mergeable: MergeableUnknown}
	for i := 0; i < attempts; i++ {
		if i > 0 {
			select {
			case <-ctx.Done():
				return read, i, ctx.Err()
			case <-time.After(c.pollInterval):
			}
		}
		r, err := c.Mergeability(ctx, owner, name, num)
		if err != nil {
			return read, i + 1, err
		}
		read = r
		if read.Terminal != "" || read.Mergeable != MergeableUnknown {
			return read, i + 1, nil
		}
	}
	return read, attempts, nil
}

// readsSpent says how much looking the gate did, in words a human can read.
//
// ⚠ IT EXISTS BECAUSE THE INLINE VERSION COULD LIE. The message used to call
// every read a "re-read" — including the first, which re-reads nothing — and
// compute the elapsed time as `(reads-1) * interval`, so a client configured
// with one attempt printed "after 1 re-reads over 0s". A refusal that misstates
// what it did is a refusal the operator cannot act on.
func readsSpent(reads int, interval time.Duration) string {
	if reads <= 1 {
		return "1 read"
	}
	return fmt.Sprintf("%d reads over %s", reads, time.Duration(reads-1)*interval)
}

// Merge merges the pull request with the method it was given.
//
// 🔴 THE METHOD IS VALIDATED AGAINST `cfg.ValidMergeMethod`, THE SAME PREDICATE
// THE CONFIG READER USES. One rule, one place: a method that the config reader
// would have rejected cannot arrive here by another route and be sent anyway.
// And an EMPTY method is the `UNKNOWN` sentinel, which must never become a
// request — the UI refuses first, and this is the second lock on the same door.
//
// 🔴 MERGEABILITY IS RE-READ ON EVERY MERGE, IMMEDIATELY BEFORE THE WRITE. It
// is NOT taken from the snapshot the operator was looking at, and this function
// takes no `mergeable` argument at all — deliberately, because a parameter
// carrying one would be a value nobody can know is still current.
//
// The hazard is the base branch moving AFTER the snapshot was fetched. That
// leaves the snapshot holding a stale `MERGEABLE`, so a gate that only re-read
// when the SNAPSHOT already said UNKNOWN is structurally unable to fire in the
// case it was built for: at the moment of the press the screen says CLEAN and
// the server no longer agrees. An earlier version of this file did exactly
// that. The same window runs the other way too — a snapshot that said UNKNOWN
// has often resolved by the time `y` is pressed — and ONE unconditional read
// answers both.
//
// The rule is the operator's own, recorded in
// `claudedocs/handoff-mention-review-tui.md`: "never merge on a stale `CLEAN`".
//
// 🔴 THE SAME READ ANSWERS "IS THERE ANYTHING LEFT TO MERGE", AND IT HAS TO,
// BECAUSE `mergeable` CANNOT. A merged or closed pull request reports
// `mergeable: UNKNOWN` forever, so the poll below would spend its whole bound
// and then tell the operator to press `m` again — on a PR where that can never
// work. `state`/`merged` ride the same tiny query and are checked FIRST.
//
// ⚠ WHAT THE READ COSTS, MEASURED: a warm authenticated round trip is ~0.17 s
// (see `Client`'s comment in query.go), spent on a keypress that is already
// willing to spend the ~2.1 s poll bound above. The design this replaced saved
// that 0.17 s by trusting a value it had no way to know was current.
//
// ⚠ WHAT IS NOT ESTABLISHED — AND IS THEREFORE NOT CLAIMED HERE. This gate is
// NOT justified by a diagnosis of the two failed merges of 2026-09-17, because
// there is no such diagnosis. What was measured: #1758 merged at 01:56:30Z and
// #1760 at 01:58:42Z; the operator reported pressing `m` and reading
// `unprocessable entity` with nothing else on screen. The 422 RESPONSE BODY was
// never captured and is unrecoverable, so WHY those requests were rejected is
// unknown and cannot now be determined. A previous version of this comment
// asserted the base-branch recompute as the measured cause; it was not measured,
// and the pick log offered as evidence records repository OPENS, not keypresses
// and not write outcomes. The reason this function re-reads is the recorded rule
// above, which needs no diagnosis to be worth following.
func (c *Client) Merge(ctx context.Context, owner, name string, num int, method string) error {
	if method == "" {
		return &APIError{State: AuthOther,
			Detail: "refusing to merge with an UNKNOWN method — a merge dispatched " +
				"with a method nobody chose is the wrong commit shape"}
	}
	if !cfg.ValidMergeMethod(method) {
		return &APIError{State: AuthOther,
			Detail: fmt.Sprintf("refusing to merge with method %q: not one of %s",
				method, strings.Join(cfg.MergeMethods(), ", "))}
	}
	// 🔴 NO CONDITION IN FRONT OF THIS CALL. Whatever guards it would test is a
	// fact about a past read, and the thing being guarded against is that read
	// having gone out of date.
	read, reads, err := c.awaitMergeable(ctx, owner, name, num)
	if err != nil {
		// 🔴 A FAILED READ IS NOT A GO-AHEAD. An error deciding "probably fine"
		// is how a guard becomes decoration.
		//
		// ⚠ AND THE TRADE IS PAID IN FULL, NOT SOFTENED. The read is NOT retried:
		// ONE transient 502 on the FIRST attempt aborts the merge with the rest
		// of the bound unspent, and the operator sees a network error where
		// they expected a merge. That is a real cost and it is chosen — a failed
		// read cannot distinguish "the server hiccupped" from "the server is
		// telling us something", and pressing `m` again is cheap while an
		// unwanted merge is not. Do not read this loop as retrying anything.
		return err
	}
	if read.Terminal != "" {
		// 🔴 A MERGED OR CLOSED PULL REQUEST IS NOT AN UNRESOLVED ONE, AND
		// TELLING THE OPERATOR TO TRY AGAIN WOULD BE A FALSE STORY.
		//
		// MEASURED 2026-09-18 against the public `innovation-upstream/devrc` with
		// a read-only GraphQL probe: `mergeable` answers `UNKNOWN` PERMANENTLY for
		// any pull request that is not open (#1760 MERGED → UNKNOWN, #1701 CLOSED
		// → UNKNOWN, #1761 OPEN → MERGEABLE), and `mergeStateStatus` says UNKNOWN
		// too. Without this arm the gate below reads a merged PR as "still
		// recomputing", spends the whole bound, and prints "press `m` again in a
		// moment" — advice that can never come true, on a loop that never
		// terminates.
		//
		// 🔴 THIS ARM IS A REGRESSION GUARD ON THE GATE ITSELF. Before the gate
		// existed the merge PUT went out and GitHub refused it — reportedly with
		// `405 Pull Request is not mergeable` — which the renderer one file over
		// now shows in full rather than as two words. That is a TRUE, actionable
		// answer, and a gate that replaced it with a false one would have made
		// this whole change a net loss for the merged case.
		//
		// The wording says only what the read established, and deliberately does
		// NOT tell the operator to retry.
		return &APIError{State: AuthOther, Detail: fmt.Sprintf(
			"refusing to merge: GitHub reports this pull request is already %s, "+
				"so there is nothing to merge. NOTHING WAS SENT, and pressing `m` "+
				"again will not change this.", read.Terminal)}
	}
	switch read.Mergeable {
	case MergeableYes:
		// Resolved, and resolved NOW. Fall through to the one write.
	case MergeableUnknown:
		// 🔴 THIS REFUSAL DEPARTS FROM THE HANDOFF LINE IT SITS NEXT TO, AND
		// SAYING SO IS THE POINT.
		//
		// That line reads "poll it — never merge on a stale `CLEAN`, and never
		// read `UNKNOWN` as a blocker". The first half is why the read above is
		// unconditional. The second half is NOT followed here: after the bound
		// is spent, an unresolved UNKNOWN stops the merge, which is reading it
		// as a blocker.
		//
		// The departure is deliberate. That line was written into
		// `claudedocs/handoff-mention-review-tui.md` by a prior agent session
		// (`b34cdbe0`, PR #1729) as guidance for an AGENT MERGING BY HAND with
		// `gh`, where "not a blocker" means "look again yourself before giving
		// up". It was never written by the operator and never written as a spec
		// for this TUI. A program cannot "look again later": its two options at
		// this point are to dispatch into an unresolved window or to stop, and
		// stopping is recoverable while a merge is not. So UNKNOWN here means
		// "ask again", and the refusal says that in words — it is an
		// instruction to the operator, not a verdict about the pull request.
		return &APIError{State: AuthOther, Detail: fmt.Sprintf(
			"refusing to merge: GitHub still reports mergeability UNKNOWN after "+
				"%s. It recomputes this whenever the base branch moves. "+
				"NOTHING WAS SENT — press `m` again in a moment.",
			readsSpent(reads, c.pollInterval))}
	default:
		return &APIError{State: AuthOther, Detail: fmt.Sprintf(
			"refusing to merge: GitHub reports mergeability %s, not %s. "+
				"NOTHING WAS SENT.", read.Mergeable, MergeableYes)}
	}
	url := fmt.Sprintf("%s/repos/%s/%s/pulls/%d/merge", c.rest, owner, name, num)
	return c.write(ctx, http.MethodPut, url, map[string]any{"merge_method": method})
}
