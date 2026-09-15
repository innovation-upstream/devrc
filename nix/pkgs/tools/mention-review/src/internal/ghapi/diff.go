package ghapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// restFile is the shape of one entry from `GET /pulls/{n}/files`.
//
// 🔴 GRAPHQL CANNOT RETURN PATCH TEXT, which is the whole reason this second
// endpoint exists. MEASURED: `GET /pulls/N/files?per_page=100` costs ~0.66 s
// and carried a `patch` for 23/23 files on the largest PR in this repo — but
// that is ONE PR, not a guarantee. The API omits `patch` for binary files and
// caps it for very large ones, so an absent patch is a first-class state
// rendered as the WORD `NO PATCH`, never as an empty diff.
type restFile struct {
	Filename         string `json:"filename"`
	Status           string `json:"status"`
	Additions        int    `json:"additions"`
	Deletions        int    `json:"deletions"`
	Patch            string `json:"patch"`
	PreviousFilename string `json:"previous_filename"`
}

// FetchFiles reads the per-file patches for a pull request.
//
// ⚠ ONE PAGE, DELIBERATELY, IN PHASE 1 — matching the GraphQL read's cap so the
// two halves cannot disagree about how many files exist. `truncated` is
// returned rather than inferred, and the Files panel says so in words.
func (c *Client) FetchFiles(ctx context.Context, owner, name string, num int) (files []File, truncated bool, err error) {
	const perPage = 100
	url := fmt.Sprintf("%s/repos/%s/%s/pulls/%d/files?per_page=%d", c.rest, owner, name, num, perPage)
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, false, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")

	body, err := c.do(ctx, req)
	if err != nil {
		return nil, false, err
	}
	var raw []restFile
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, false, &APIError{State: AuthOther, Detail: "unreadable files response: " + err.Error()}
	}
	for _, f := range raw {
		files = append(files, File{
			Path:         f.Filename,
			Additions:    f.Additions,
			Deletions:    f.Deletions,
			ChangeType:   restStatusToChangeType(f.Status),
			Patch:        f.Patch,
			PreviousPath: f.PreviousFilename,
		})
	}
	return files, len(raw) == perPage, nil
}

// restStatusToChangeType normalises REST's lowercase `status` onto the same
// vocabulary GraphQL's `changeType` uses.
//
// 🔴 ONE VOCABULARY, ONE PLACE. The two endpoints spell the same fact
// differently ("removed" vs "REMOVED", "added" vs "ADDED"), and a predicate
// open-coded at each reader is wrong at all but one of them in the same
// direction. The Files panel branches on ONE set of words, defined here.
func restStatusToChangeType(s string) string {
	switch s {
	case "added":
		return "ADDED"
	case "removed":
		return "REMOVED"
	case "modified":
		return "MODIFIED"
	case "renamed":
		return "RENAMED"
	case "copied":
		return "COPIED"
	case "changed":
		return "CHANGED"
	case "unchanged":
		return "UNCHANGED"
	}
	// 🔴 An unknown status is reported as itself, uppercased by the caller's
	// eye rather than mapped onto a guess. A silent fallback to MODIFIED would
	// make a new GitHub status render as an ordinary edit.
	return s
}
