package ghapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Query is the ONE GraphQL read. 🔴 `issueOrPullRequest(number:)` with an
// inline fragment per type returns `__typename` — the issue-vs-PR answer — PLUS
// every field the four panels need, in a single round trip.
//
// MEASURED in a Go probe on 2026-09-14 against the public
// `innovation-upstream/devrc`: 1 HTTP round trip, median 663 ms on a 23-file PR,
// 536 ms on a small one, 402 ms on an issue. Not one field was ABSENT; the only
// null was `reviewDecision`, which is a legitimate "no decision yet" state the
// Overview panel renders as a WORD.
//
// ⚠ ONE KNOWN PAGINATION EDGE, AND IT IS SURFACED RATHER THAN HIDDEN:
// `files(first:100)` and `commits(last:100)` cap at one page. `pageInfo` is
// requested so a PR past either cap sets Snapshot.FilesTruncated /
// CommitsTruncated and the panel says so in words. A second round trip for the
// tail is deliberately NOT in Phase 1 — the p90 is 6 changed files in this repo
// and 19 in the second one measured, so the cap is the far tail, and an
// unmarked short list is the only outcome that would be dishonest.
const Query = `
query($owner:String!,$name:String!,$number:Int!){
  viewer { login }
  repository(owner:$owner,name:$name){
    issueOrPullRequest(number:$number){
      __typename
      ... on Issue {
        number title state body url createdAt updatedAt
        author{login}
      }
      ... on PullRequest {
        number title state isDraft merged body url createdAt updatedAt
        author{login}
        baseRefName headRefName
        additions deletions changedFiles
        mergeable mergeStateStatus reviewDecision
        commits(last:100){
          totalCount
          nodes{ commit{ oid abbreviatedOid messageHeadline committedDate author{name} } }
        }
        files(first:100){
          totalCount
          pageInfo{ hasNextPage }
          nodes{ path additions deletions changeType }
        }
        reviews(last:50){
          totalCount
          nodes{ author{login} state submittedAt }
        }
        reviewThreads(first:100){
          totalCount
          nodes{ isResolved }
        }
        rollup: commits(last:1){
          nodes{ commit{ statusCheckRollup{
            state
            contexts(first:100){
              totalCount
              nodes{
                __typename
                ... on CheckRun{ name conclusion status }
                ... on StatusContext{ context state }
              }
            }
          } } }
        }
      }
    }
  }
}`

// APIError is a transport- or auth-level failure the UI renders as a card.
// 🔴 It carries a STATE, not a bare string, because §6.1's cards differ by
// state and a string would make the UI re-parse prose to choose one.
type APIError struct {
	State AuthState
	// Detail is the underlying message. 🔴 NEVER the token and never a header
	// value — Client.do builds it from the status line and the API's own
	// `message` field only.
	Detail string
	// ResetAt is set only for AuthRateLimited, from the response header.
	ResetAt time.Time
}

func (e *APIError) Error() string {
	if e.Detail == "" {
		return e.State.Word()
	}
	return e.State.Word() + " — " + e.Detail
}

// Client is built once at startup over ONE http.Client so every call after the
// first reuses the connection (§3.5). MEASURED: a cold authenticated round trip
// to api.github.com costs ~0.30 s and a warm one ~0.17 s.
type Client struct {
	http     *http.Client
	token    string
	endpoint string // GraphQL endpoint; overridable so tests can point at a fake
	rest     string // REST base;     same
	ua       string

	// The bound on the mergeability re-read `Merge` performs when the snapshot
	// says UNKNOWN. Fields rather than bare constants ONLY so a test can run
	// the loop without spending real seconds — `NewClient` is the single place
	// the production values are set, and a test pins those defaults.
	pollAttempts int
	pollInterval time.Duration
}

// NewClient takes the token as a value rather than resolving it, so the whole
// package is testable without touching the host's gh config.
func NewClient(token string, hc *http.Client) *Client {
	if hc == nil {
		hc = &http.Client{Timeout: 30 * time.Second}
	}
	return &Client{
		http:         hc,
		token:        token,
		endpoint:     "https://api.github.com/graphql",
		rest:         "https://api.github.com",
		ua:           "mention-review",
		pollAttempts: mergeablePollAttempts,
		pollInterval: mergeablePollInterval,
	}
}

// SetBaseURLs points the client at a fake server. Test seam only.
func (c *Client) SetBaseURLs(graphql, rest string) {
	c.endpoint, c.rest = graphql, rest
}

// SetMergeablePoll shortens the mergeability re-read. Test seam only — nothing
// in `main` calls it, so the production bound is the one `NewClient` sets.
func (c *Client) SetMergeablePoll(attempts int, interval time.Duration) {
	c.pollAttempts, c.pollInterval = attempts, interval
}

func (c *Client) do(ctx context.Context, req *http.Request) ([]byte, error) {
	if c.token == "" {
		// 🔴 The check is here and not only at startup, because go-gh's fourth
		// rung returns `("", "default")` with NO error — an empty token is a
		// value, not a failure, and it would otherwise produce a 401 card
		// naming the wrong fix.
		return nil, &APIError{State: AuthNoToken}
	}
	req.Header.Set("Authorization", "bearer "+c.token)
	req.Header.Set("User-Agent", c.ua)
	resp, err := c.http.Do(req.WithContext(ctx))
	if err != nil {
		return nil, &APIError{State: AuthOther, Detail: "could not reach api.github.com: " + err.Error()}
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<20))

	remaining := -1
	if v := resp.Header.Get("x-ratelimit-remaining"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			remaining = n
		}
	}
	st := ClassifyResponse(true, resp.StatusCode, remaining)
	if st != AuthOK {
		// 🔴 REDACT FIRST, CLIP SECOND, AND THE ORDER IS LOAD-BEARING. `redact`
		// looks for the exact secret this client holds; clipping first could cut
		// a reflected token in half and leave the surviving prefix un-redacted,
		// because the needle would no longer be in the haystack. Clipping AFTER
		// can only ever shorten a string the secret has already left.
		e := &APIError{State: st, Detail: clipDetail(c.redact(apiMessage(body, resp.Status)))}
		if st == AuthRateLimited {
			if v := resp.Header.Get("x-ratelimit-reset"); v != "" {
				if sec, err := strconv.ParseInt(v, 10, 64); err == nil {
					e.ResetAt = time.Unix(sec, 0)
				}
			}
		}
		return nil, e
	}
	return body, nil
}

// redact removes the token from any string that is about to become an error
// the UI renders.
//
// 🔴 THIS IS NOT BELT-AND-BRACES — IT CLOSES A MEASURED HOLE. `apiMessage`
// reflects the SERVER's own `message` field, which this code did not construct
// and cannot vouch for. A test that made the fake server echo the credential
// back watched the token come out the other side inside
// `TOKEN REJECTED — Bad credentials for gho_…`. GitHub does not do that today;
// nothing guarantees that it, a proxy, or an enterprise gateway never will, and
// §10.4 says the token is never logged, never cached and never included in any
// error card. The check is STRUCTURAL — it looks for the exact secret this
// client holds — rather than a pattern match on things that look like tokens.
func (c *Client) redact(s string) string {
	if c.token == "" {
		return s
	}
	return strings.ReplaceAll(s, c.token, "<redacted>")
}

// maxReflectedErrorEntries caps how many `errors[]` entries are rendered into
// one card.
//
// 🔴 A CAP, BECAUSE THE CONTENT IS THE SERVER'S AND NOT OURS. A validation
// failure over a large payload can carry one entry per field; the card is a
// single line in a bar the operator reads under time pressure, and an unbounded
// reflection would push the panels off the screen. Five is enough to show the
// SHAPE of the failure — the count of the rest is still reported, as `+N more`,
// so a clipped list can never be mistaken for the whole one.
const maxReflectedErrorEntries = 5

// maxDetailRunes caps the WHOLE composed detail, after redaction.
//
// 🔴 THE ENTRY CAP ALONE IS NOT A BOUND. One entry's `message` is a
// server-supplied string of any length, so capping the COUNT still permits a
// pathological body to produce a megabyte-long card. Runes rather than bytes so
// the truncation cannot land inside a codepoint.
const maxDetailRunes = 400

// apiMessage renders GitHub's error body into the one line the card shows.
//
// 🔴 IT CARRIES `errors[]`, AND THAT IS THE WHOLE POINT. GitHub puts a GENERIC
// string in `message` for every validation failure and the ACTUAL reason in
// `errors[]`. MEASURED on 2026-09-17 against the real API with a read-only
// probe (`GET /search/issues?q=`):
//
//	{"message":"Validation Failed",
//	 "errors":[{"resource":"Search","field":"q","code":"missing"}],
//	 "documentation_url":"…","status":"422"}
//
// The old version of this function returned `m.Message` alone, so every 422
// from any of the three write verbs rendered as two words that explain nothing
// — which is exactly what a merge dispatched during a base-branch recompute
// showed the operator: `unprocessable entity`, twice, with no reason and no
// status code.
//
// It falls back to the status line when the body is empty or unparseable, and
// 🔴 it still never reflects a request header back: the only inputs are the
// response body and the status line.
func apiMessage(body []byte, status string) string {
	var env struct {
		Message string            `json:"message"`
		Errors  []json.RawMessage `json:"errors"`
	}
	if json.Unmarshal(body, &env) != nil {
		// ⚠ NOT NECESSARILY UNPARSEABLE. Some endpoints answer with `errors` as
		// something other than an array, which fails the decode above while the
		// `message` is perfectly readable — so the narrow shape is tried before
		// the body is given up on. Widening what we render must not NARROW what
		// we render for a shape that used to work.
		var narrow struct {
			Message string `json:"message"`
		}
		if json.Unmarshal(body, &narrow) == nil && narrow.Message != "" {
			return joinDetail(status, narrow.Message)
		}
		return status
	}

	detail := joinDetail(status, env.Message)

	shown, extra := env.Errors, 0
	if len(shown) > maxReflectedErrorEntries {
		extra = len(shown) - maxReflectedErrorEntries
		shown = shown[:maxReflectedErrorEntries]
	}
	rendered := make([]string, 0, len(shown)+1)
	for _, raw := range shown {
		if s := renderErrorEntry(raw); s != "" {
			rendered = append(rendered, s)
		}
	}
	if extra > 0 {
		rendered = append(rendered, fmt.Sprintf("+%d more", extra))
	}
	if len(rendered) > 0 {
		detail += " [" + strings.Join(rendered, "; ") + "]"
	}
	return detail
}

// joinDetail puts the STATUS LINE in front of the server's prose.
//
// 🔴 THE CODE IS PART OF THE ANSWER. A card reading `Validation Failed` cannot
// be told apart from a card reading `Not Found` by anybody deciding what to do
// next; `422 Unprocessable Entity: Validation Failed` can.
func joinDetail(status, message string) string {
	switch {
	case message == "":
		return status
	case status == "":
		return message
	}
	return status + ": " + message
}

// renderErrorEntry turns ONE `errors[]` entry into readable text.
//
// 🔴 TWO SHAPES, BOTH REAL, AND THE CODE-ONLY ONE IS THE MEASURED ONE. An entry
// may carry human prose in `message`, or nothing but `resource`/`field`/`code`
// — the envelope quoted above is the second kind, and rendering it as an empty
// string would lose the only fact in the response. A `code` with no prose is
// still the reason, so it is printed.
func renderErrorEntry(raw json.RawMessage) string {
	var obj struct {
		Message  string `json:"message"`
		Resource string `json:"resource"`
		Field    string `json:"field"`
		Code     string `json:"code"`
	}
	if json.Unmarshal(raw, &obj) == nil {
		label := obj.Resource
		if obj.Field != "" {
			if label != "" {
				label += "."
			}
			label += obj.Field
		}
		text := obj.Message
		if text == "" {
			text = obj.Code
		}
		switch {
		case label != "" && text != "":
			return label + ": " + text
		case text != "":
			return text
		case label != "":
			return label
		}
	}
	// A bare string entry — some endpoints answer `"errors":["…"]`.
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return strings.TrimSpace(s)
	}
	// Anything else is echoed as the JSON it is, rather than dropped. An entry
	// this code does not understand is still the server's reason, and a silent
	// drop is the empty-result trap: "nothing happened" is the observable the
	// most causes share.
	return strings.TrimSpace(string(raw))
}

// clipDetail bounds the rendered detail. 🔴 CALLED AFTER `redact`, never before
// — see the comment at its call site in `do`.
func clipDetail(s string) string {
	r := []rune(s)
	if len(r) <= maxDetailRunes {
		return s
	}
	return string(r[:maxDetailRunes-1]) + "…"
}

// Fetch performs the one GraphQL read and decodes it into a Snapshot.
func (c *Client) Fetch(ctx context.Context, owner, name string, num int) (*Snapshot, error) {
	payload, err := json.Marshal(map[string]any{
		"query": Query,
		"variables": map[string]any{
			"owner": owner, "name": name, "number": num,
		},
	})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequest(http.MethodPost, c.endpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	body, err := c.do(ctx, req)
	if err != nil {
		return nil, err
	}
	return decodeSnapshot(body, owner+"/"+name, num)
}

// MergeableQuery is the SECOND read this client can make, and it is deliberately
// tiny: two scalars off the pull request, nothing else.
//
// 🔴 IT IS NOT `Query` WITH A FILTER. The panel read pulls 100 files, 100
// commits, 50 reviews and a check rollup; re-running it to answer one enum
// would make a bounded poll expensive enough that nobody would keep the bound
// honest. This costs one small round trip per attempt.
//
// ⚠ `pullRequest(number:)`, not `issueOrPullRequest`. The only caller is the
// merge gate, and merging an issue is not a thing — a number that resolves to
// an issue returns null here, which the decode reports rather than guesses at.
const MergeableQuery = `
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){ mergeable mergeStateStatus }
  }
}`

// Mergeability re-reads just the merge state. It returns the NORMALISED word,
// so "" from the server is `UNKNOWN` rather than an empty string the caller
// would have to re-interpret.
func (c *Client) Mergeability(ctx context.Context, owner, name string, num int) (string, error) {
	payload, err := json.Marshal(map[string]any{
		"query": MergeableQuery,
		"variables": map[string]any{
			"owner": owner, "name": name, "number": num,
		},
	})
	if err != nil {
		return "", err
	}
	req, err := http.NewRequest(http.MethodPost, c.endpoint, bytes.NewReader(payload))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")

	body, err := c.do(ctx, req)
	if err != nil {
		return "", err
	}

	var r struct {
		Data struct {
			Repository *struct {
				PullRequest *struct {
					Mergeable string `json:"mergeable"`
				} `json:"pullRequest"`
			} `json:"repository"`
		} `json:"data"`
		Errors []struct {
			Type    string `json:"type"`
			Message string `json:"message"`
		} `json:"errors"`
	}
	if err := json.Unmarshal(body, &r); err != nil {
		return "", &APIError{State: AuthOther, Detail: "unreadable response: " + err.Error()}
	}
	if len(r.Errors) > 0 {
		st := AuthOther
		if r.Errors[0].Type == "NOT_FOUND" {
			st = AuthNotFound
		}
		return "", &APIError{State: st, Detail: r.Errors[0].Message}
	}
	if r.Data.Repository == nil || r.Data.Repository.PullRequest == nil {
		return "", &APIError{
			State:  AuthNotFound,
			Detail: fmt.Sprintf("%s/%s#%d is not a pull request this token can read", owner, name, num),
		}
	}
	return NormalizeMergeable(r.Data.Repository.PullRequest.Mergeable), nil
}

// --- decoding ---------------------------------------------------------------

type gqlResponse struct {
	Data struct {
		Viewer struct {
			Login string `json:"login"`
		} `json:"viewer"`
		Repository *struct {
			Node *gqlNode `json:"issueOrPullRequest"`
		} `json:"repository"`
	} `json:"data"`
	Errors []struct {
		Type    string `json:"type"`
		Message string `json:"message"`
	} `json:"errors"`
}

type gqlActor struct {
	Login string `json:"login"`
	Name  string `json:"name"`
}

type gqlNode struct {
	Typename  string    `json:"__typename"`
	Number    int       `json:"number"`
	Title     string    `json:"title"`
	State     string    `json:"state"`
	Body      string    `json:"body"`
	URL       string    `json:"url"`
	CreatedAt time.Time `json:"createdAt"`
	UpdatedAt time.Time `json:"updatedAt"`
	Author    *gqlActor `json:"author"`

	IsDraft          bool    `json:"isDraft"`
	Merged           bool    `json:"merged"`
	BaseRefName      string  `json:"baseRefName"`
	HeadRefName      string  `json:"headRefName"`
	Additions        int     `json:"additions"`
	Deletions        int     `json:"deletions"`
	ChangedFiles     int     `json:"changedFiles"`
	Mergeable        string  `json:"mergeable"`
	MergeStateStatus string  `json:"mergeStateStatus"`
	ReviewDecision   *string `json:"reviewDecision"`

	Commits struct {
		TotalCount int `json:"totalCount"`
		Nodes      []struct {
			Commit struct {
				OID            string    `json:"oid"`
				AbbreviatedOID string    `json:"abbreviatedOid"`
				Headline       string    `json:"messageHeadline"`
				CommittedDate  time.Time `json:"committedDate"`
				Author         *gqlActor `json:"author"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"commits"`

	Files struct {
		TotalCount int `json:"totalCount"`
		PageInfo   struct {
			HasNextPage bool `json:"hasNextPage"`
		} `json:"pageInfo"`
		Nodes []struct {
			Path       string `json:"path"`
			Additions  int    `json:"additions"`
			Deletions  int    `json:"deletions"`
			ChangeType string `json:"changeType"`
		} `json:"nodes"`
	} `json:"files"`

	Reviews struct {
		TotalCount int `json:"totalCount"`
		Nodes      []struct {
			Author      *gqlActor `json:"author"`
			State       string    `json:"state"`
			SubmittedAt time.Time `json:"submittedAt"`
		} `json:"nodes"`
	} `json:"reviews"`

	ReviewThreads struct {
		TotalCount int `json:"totalCount"`
		Nodes      []struct {
			IsResolved bool `json:"isResolved"`
		} `json:"nodes"`
	} `json:"reviewThreads"`

	Rollup struct {
		Nodes []struct {
			Commit struct {
				StatusCheckRollup *struct {
					State    string `json:"state"`
					Contexts struct {
						TotalCount int `json:"totalCount"`
						Nodes      []struct {
							Typename   string `json:"__typename"`
							Conclusion string `json:"conclusion"`
							Status     string `json:"status"`
							State      string `json:"state"`
						} `json:"nodes"`
					} `json:"contexts"`
				} `json:"statusCheckRollup"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"rollup"`
}

// ErrNotFound is returned when the reference does not resolve. 🔴 A GraphQL
// 200 with `repository: null` is the SAME user-facing condition as a REST 404
// — "not visible to this token" — and collapsing them here is what stops the
// UI needing two code paths for one meaning.
func decodeSnapshot(body []byte, repo string, num int) (*Snapshot, error) {
	var r gqlResponse
	if err := json.Unmarshal(body, &r); err != nil {
		return nil, &APIError{State: AuthOther, Detail: "unreadable response: " + err.Error()}
	}
	if len(r.Errors) > 0 {
		st := AuthOther
		if r.Errors[0].Type == "NOT_FOUND" {
			st = AuthNotFound
		}
		return nil, &APIError{State: st, Detail: r.Errors[0].Message}
	}
	if r.Data.Repository == nil || r.Data.Repository.Node == nil {
		return nil, &APIError{
			State:  AuthNotFound,
			Detail: fmt.Sprintf("%s#%d is not visible to this token", repo, num),
		}
	}
	n := r.Data.Repository.Node

	s := &Snapshot{
		ViewerLogin: r.Data.Viewer.Login,
		Kind:        Kind(n.Typename),
		Repo:        repo,
		Num:         num,
		Title:       n.Title,
		State:       n.State,
		URL:         n.URL,
		Body:        n.Body,
		Author:      actorLogin(n.Author),
		CreatedAt:   n.CreatedAt,
		UpdatedAt:   n.UpdatedAt,
	}
	if s.Kind != KindPullRequest {
		return s, nil
	}

	s.IsDraft = n.IsDraft
	s.Merged = n.Merged
	s.BaseRef = n.BaseRefName
	s.HeadRef = n.HeadRefName
	s.Additions = n.Additions
	s.Deletions = n.Deletions
	s.ChangedFiles = n.ChangedFiles
	s.Mergeable = n.Mergeable
	s.MergeStateStatus = n.MergeStateStatus
	if n.ReviewDecision != nil {
		s.ReviewDecision = *n.ReviewDecision
	}

	for _, c := range n.Commits.Nodes {
		s.Commits = append(s.Commits, Commit{
			OID:      c.Commit.OID,
			Abbrev:   c.Commit.AbbreviatedOID,
			Headline: c.Commit.Headline,
			Author:   actorName(c.Commit.Author),
			When:     c.Commit.CommittedDate,
		})
	}
	s.CommitsTruncated = n.Commits.TotalCount > len(n.Commits.Nodes)

	for _, f := range n.Files.Nodes {
		s.Files = append(s.Files, File{
			Path:       f.Path,
			Additions:  f.Additions,
			Deletions:  f.Deletions,
			ChangeType: f.ChangeType,
		})
	}
	s.FilesTruncated = n.Files.PageInfo.HasNextPage || n.Files.TotalCount > len(n.Files.Nodes)

	for _, rv := range n.Reviews.Nodes {
		s.Reviews = append(s.Reviews, Review{
			Author: actorLogin(rv.Author),
			State:  rv.State,
			When:   rv.SubmittedAt,
		})
	}

	s.Threads.Total = n.ReviewThreads.TotalCount
	for _, t := range n.ReviewThreads.Nodes {
		if !t.IsResolved {
			s.Threads.Unresolved++
		}
	}

	if len(n.Rollup.Nodes) > 0 {
		if rc := n.Rollup.Nodes[0].Commit.StatusCheckRollup; rc != nil {
			s.Checks.State = rc.State
			s.Checks.Total = rc.Contexts.TotalCount
			for _, ctxNode := range rc.Contexts.Nodes {
				switch ctxNode.Typename {
				case "CheckRun":
					switch {
					case ctxNode.Status != "COMPLETED":
						s.Checks.Pending++
					case ctxNode.Conclusion == "FAILURE" ||
						ctxNode.Conclusion == "TIMED_OUT" ||
						ctxNode.Conclusion == "CANCELLED" ||
						ctxNode.Conclusion == "STARTUP_FAILURE":
						s.Checks.Failing++
					}
				case "StatusContext":
					switch ctxNode.State {
					case "PENDING", "EXPECTED":
						s.Checks.Pending++
					case "FAILURE", "ERROR":
						s.Checks.Failing++
					}
				}
			}
		}
	}
	return s, nil
}

func actorLogin(a *gqlActor) string {
	if a == nil {
		return "" // a deleted account; the UI renders GHOST rather than blank
	}
	return a.Login
}

func actorName(a *gqlActor) string {
	if a == nil {
		return ""
	}
	if a.Name != "" {
		return a.Name
	}
	return a.Login
}
