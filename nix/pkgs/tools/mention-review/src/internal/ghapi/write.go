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
// until it resolves or the bound is spent. It returns the last state seen and
// how many reads it took.
//
// 🔴 IT POLLS A READ. It does NOT retry a write: a merge that has already been
// dispatched and failed is the operator's call, not this loop's.
func (c *Client) awaitMergeable(ctx context.Context, owner, name string, num int) (string, int, error) {
	attempts := c.pollAttempts
	if attempts < 1 {
		attempts = 1
	}
	state := MergeableUnknown
	for i := 0; i < attempts; i++ {
		if i > 0 {
			select {
			case <-ctx.Done():
				return state, i, ctx.Err()
			case <-time.After(c.pollInterval):
			}
		}
		s, err := c.Mergeability(ctx, owner, name, num)
		if err != nil {
			return state, i + 1, err
		}
		state = s
		if state != MergeableUnknown {
			return state, i + 1, nil
		}
	}
	return state, attempts, nil
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
	state, reads, err := c.awaitMergeable(ctx, owner, name, num)
	if err != nil {
		// 🔴 A FAILED READ IS NOT A GO-AHEAD. An error deciding "probably fine"
		// is how a guard becomes decoration.
		return err
	}
	switch state {
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
				"%d re-reads over %s. It recomputes this whenever the base branch "+
				"moves. NOTHING WAS SENT — press `m` again in a moment.",
			reads, time.Duration(reads-1)*c.pollInterval)}
	default:
		return &APIError{State: AuthOther, Detail: fmt.Sprintf(
			"refusing to merge: GitHub reports mergeability %s, not %s. "+
				"NOTHING WAS SENT.", state, MergeableYes)}
	}
	url := fmt.Sprintf("%s/repos/%s/%s/pulls/%d/merge", c.rest, owner, name, num)
	return c.write(ctx, http.MethodPut, url, map[string]any{"merge_method": method})
}
