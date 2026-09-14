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
}

// NewClient takes the token as a value rather than resolving it, so the whole
// package is testable without touching the host's gh config.
func NewClient(token string, hc *http.Client) *Client {
	if hc == nil {
		hc = &http.Client{Timeout: 30 * time.Second}
	}
	return &Client{
		http:     hc,
		token:    token,
		endpoint: "https://api.github.com/graphql",
		rest:     "https://api.github.com",
		ua:       "mention-review",
	}
}

// SetBaseURLs points the client at a fake server. Test seam only.
func (c *Client) SetBaseURLs(graphql, rest string) {
	c.endpoint, c.rest = graphql, rest
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
		e := &APIError{State: st, Detail: c.redact(apiMessage(body, resp.Status))}
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

// apiMessage pulls GitHub's own `message` out of an error body. Falls back to
// the status line. 🔴 It never reflects a request header back.
func apiMessage(body []byte, status string) string {
	var m struct {
		Message string `json:"message"`
	}
	if json.Unmarshal(body, &m) == nil && m.Message != "" {
		return m.Message
	}
	return status
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
