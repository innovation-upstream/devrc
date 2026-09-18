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
// ⚠ THE FIRST READ IS IMMEDIATE. The snapshot that said UNKNOWN may be minutes
// old, so sleeping before looking would pay for a wait that has already been
// served.
const (
	mergeablePollAttempts = 4
	mergeablePollInterval = 700 * time.Millisecond
)

// awaitMergeable re-reads mergeability until it resolves or the bound is spent.
// It returns the last state seen and how many reads it took.
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
// 🔴 `mergeable` IS THE SNAPSHOT'S VALUE, CARRIED IN RATHER THAN RE-READ HERE,
// for the same reason `method` is: the intent the operator confirmed is the
// thing that gets acted on. When it says UNKNOWN this REFUSES OR POLLS — it
// never forwards — which is the same judgement the two refusals above make.
//
// MEASURED 2026-09-17: two merges pressed 25 s before and 20 s after the base
// branch moved both came back `unprocessable entity` and nothing on screen said
// why; the PR merged unchanged two minutes later. GitHub resets `mergeable` to
// null while it recomputes against the new base, and a merge dispatched into
// that window fails. The repo's own recorded lesson is "poll it — never merge
// on a stale CLEAN, and never read UNKNOWN as a blocker", and this is that
// lesson as code.
func (c *Client) Merge(ctx context.Context, owner, name string, num int, method, mergeable string) error {
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
	if NormalizeMergeable(mergeable) == MergeableUnknown {
		state, reads, err := c.awaitMergeable(ctx, owner, name, num)
		if err != nil {
			return err
		}
		switch state {
		case MergeableYes:
			// It resolved. Fall through to the one write.
		case MergeableUnknown:
			return &APIError{State: AuthOther, Detail: fmt.Sprintf(
				"refusing to merge: GitHub still reports mergeability UNKNOWN after "+
					"%d re-reads over %s. It recomputes this whenever the base branch "+
					"moves, and a merge sent into that window fails as `unprocessable "+
					"entity`. NOTHING WAS SENT — press `m` again in a moment.",
				reads, time.Duration(reads-1)*c.pollInterval)}
		default:
			return &APIError{State: AuthOther, Detail: fmt.Sprintf(
				"refusing to merge: GitHub reports mergeability %s, not %s. "+
					"NOTHING WAS SENT.", state, MergeableYes)}
		}
	}
	url := fmt.Sprintf("%s/repos/%s/%s/pulls/%d/merge", c.rest, owner, name, num)
	return c.write(ctx, http.MethodPut, url, map[string]any{"merge_method": method})
}
