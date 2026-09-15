package ghapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

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

// Merge merges the pull request with the method it was given.
//
// 🔴 THE METHOD IS VALIDATED AGAINST `cfg.ValidMergeMethod`, THE SAME PREDICATE
// THE CONFIG READER USES. One rule, one place: a method that the config reader
// would have rejected cannot arrive here by another route and be sent anyway.
// And an EMPTY method is the `UNKNOWN` sentinel, which must never become a
// request — the UI refuses first, and this is the second lock on the same door.
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
	url := fmt.Sprintf("%s/repos/%s/%s/pulls/%d/merge", c.rest, owner, name, num)
	return c.write(ctx, http.MethodPut, url, map[string]any{"merge_method": method})
}
