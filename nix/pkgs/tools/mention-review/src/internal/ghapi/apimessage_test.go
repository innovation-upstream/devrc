package ghapi

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
)

// 🔴 WHAT A FAILED WRITE SAYS, AND WHY IT USED TO SAY NOTHING.
//
// MEASURED 2026-09-17: the operator pressed `m` on a pull request twice — 25 s
// before and 20 s after its base branch moved — and both attempts produced an
// error card reading, in full, `unprocessable entity`. No status code, no
// reason, nothing to act on. The same PR merged unchanged two minutes later.
//
// The cause is that GitHub puts a GENERIC string in `message` for a validation
// failure and the ACTUAL reason in `errors[]`, and `apiMessage` read only the
// first. POSITIVE CONTROL, measured against the real API with a READ-ONLY probe
// (`GET /search/issues?q=`) so nothing was written to anybody's repository:
//
//	{"message":"Validation Failed",
//	 "errors":[{"resource":"Search","field":"q","code":"missing"}],
//	 "documentation_url":"…","status":"422"}
//
// Note the entry carries NO prose at all — `resource`/`field`/`code` and
// nothing else. A renderer that only understood `errors[].message` would print
// an empty string for the one measured shape, which is why both shapes are
// pinned below.
//
// ⚠ EVERY REQUEST HERE GOES TO AN `httptest` SERVER ON LOOPBACK, per
// `nonet_test.go`'s TestMain.

// The measured envelope, with the documentation URL elided — this repository is
// public and the shape is the part under test.
const measuredValidationEnvelope = `{"message":"Validation Failed",` +
	`"errors":[{"resource":"Search","field":"q","code":"missing"}],` +
	`"documentation_url":"<elided>","status":"422"}`

// newErrorClient points a client at a server that answers one canned failure.
func newErrorClient(t *testing.T, token string, status int, body string) (*Client, func()) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}))
	c := NewClient(token, srv.Client())
	c.SetBaseURLs(srv.URL+"/graphql", srv.URL)
	return c, srv.Close
}

// statusLine is what Go's own client puts in `resp.Status`, derived from the
// standard library rather than from the code under test — 422's reason phrase
// has been respelled in the RFCs and pinning a literal here would pin the
// wrong thing.
func statusLine(code int) string {
	return strconv.Itoa(code) + " " + http.StatusText(code)
}

// detailOf runs a real write against the fake and returns the APIError detail.
//
// ⚠ `PostComment`, NOT `Merge`. The rendering under test lives in `do`, which
// every verb shares, and the servers in this file answer the SAME canned failure
// to every path. `Merge` re-reads mergeability before it writes, so it would
// fail on that READ and the detail returned here would be the read's — the same
// string, arrived at through a path this file is not about. A comment post is
// one request, so what is measured is unambiguous.
func detailOf(t *testing.T, c *Client) string {
	t.Helper()
	err := c.PostComment(context.Background(), wOwner, wName, wNum, "a body the fake ignores")
	if err == nil {
		t.Fatal("the failure was reported as a success")
	}
	var ae *APIError
	if !asAPIError(err, &ae) {
		t.Fatalf("err is %T, want *APIError: %v", err, err)
	}
	return ae.Detail
}

// 🔴 THE CODE-ONLY SHAPE — the one that was MEASURED, and the one the old
// renderer turned into two useless words.
func TestAValidationFailureCarriesTheStatusCodeTheMessageAndTheCodeOnlyEntry(t *testing.T) {
	c, done := newErrorClient(t, "tok-apimessage-fixture", http.StatusUnprocessableEntity,
		measuredValidationEnvelope)
	defer done()

	got := detailOf(t, c)

	// The three facts the card must carry. Each is written out by hand from the
	// envelope above, never read off the renderer.
	for _, want := range []string{"422", "Validation Failed", "Search.q: missing"} {
		if !strings.Contains(got, want) {
			t.Errorf("the detail is missing %q:\n  %s", want, got)
		}
	}
	// 🔴 THE REGRESSION ITSELF. The pre-change renderer returned `m.Message`
	// alone, so this is the exact string the operator was shown. If it comes
	// back, the fix is gone.
	if got == "Validation Failed" {
		t.Error("the detail is the generic message ALONE — the `errors[]` array " +
			"and the status code were both discarded, which is the defect")
	}
}

// 🔴 THE PROSE SHAPE. An entry that DOES carry a human sentence must render
// that sentence, not be collapsed onto its code.
func TestAnErrorEntryWithProseRendersTheProseUnderItsFieldLabel(t *testing.T) {
	const body = `{"message":"Validation Failed","errors":[{` +
		`"resource":"PullRequest","field":"head_sha","code":"invalid",` +
		`"message":"head_sha does not match the current head of the branch"}]}`
	c, done := newErrorClient(t, "tok-apimessage-fixture", http.StatusUnprocessableEntity, body)
	defer done()

	got := detailOf(t, c)

	if !strings.Contains(got, "head_sha does not match the current head of the branch") {
		t.Errorf("the entry's prose was dropped:\n  %s", got)
	}
	if !strings.Contains(got, "PullRequest.head_sha") {
		t.Errorf("the entry lost its resource/field label:\n  %s", got)
	}
	// ⚠ THE CODE IS NOT ALSO PRINTED WHEN THERE IS PROSE. `invalid` beside a
	// sentence that already says what is invalid is noise, and the sentence is
	// the more specific of the two.
	if strings.Contains(got, "invalid") {
		t.Errorf("the code was printed beside the prose:\n  %s", got)
	}
}

// 🔴 THE FALLBACK SURVIVES. A body that is empty, or is not JSON at all, must
// still produce the status line rather than an empty card.
func TestAnEmptyOrUnparseableBodyStillYieldsTheStatusLine(t *testing.T) {
	want := statusLine(http.StatusUnprocessableEntity)
	for _, body := range []string{"", "not json at all {", "[9,8,7]", "null"} {
		t.Run(fmt.Sprintf("body=%q", body), func(t *testing.T) {
			c, done := newErrorClient(t, "tok-apimessage-fixture",
				http.StatusUnprocessableEntity, body)
			defer done()
			if got := detailOf(t, c); got != want {
				t.Errorf("detail = %q, want the status line %q", got, want)
			}
		})
	}
	// POSITIVE CONTROL: a body that IS readable produces something OTHER than
	// the bare status line, so the equality above is a claim about the fallback
	// rather than about a renderer that always returns the status.
	c, done := newErrorClient(t, "tok-apimessage-fixture", http.StatusUnprocessableEntity,
		`{"message":"Reference update failed"}`)
	defer done()
	got := detailOf(t, c)
	if got == want {
		t.Fatal("a body carrying a message ALSO rendered as the bare status line " +
			"— the parse is wired to nothing and every case above proves nothing")
	}
	if !strings.Contains(got, "Reference update failed") || !strings.Contains(got, "422") {
		t.Errorf("detail = %q, want the status line AND the message", got)
	}
}

// 🔴 THE CAP IS REAL AND THE REMAINDER IS COUNTED. A clipped list that did not
// say it was clipped would be the quietly-short-file-list defect in a card.
func TestTheErrorEntryCapBoundsTheListAndReportsTheRemainder(t *testing.T) {
	// 12 entries, every field name distinct from every other and from any
	// constant this test names.
	var entries []string
	for i := 1; i <= 12; i++ {
		entries = append(entries, fmt.Sprintf(
			`{"resource":"Payload","field":"f%02d","code":"c%02d"}`, i, i))
	}
	body := `{"message":"Validation Failed","errors":[` + strings.Join(entries, ",") + `]}`

	c, done := newErrorClient(t, "tok-apimessage-fixture", http.StatusUnprocessableEntity, body)
	defer done()
	got := detailOf(t, c)

	// The first five are rendered — the cap is 5, so these literals are the
	// contract rather than a restatement of the constant.
	for i := 1; i <= 5; i++ {
		want := fmt.Sprintf("Payload.f%02d: c%02d", i, i)
		if !strings.Contains(got, want) {
			t.Errorf("entry %d (%q) was not rendered:\n  %s", i, want, got)
		}
	}
	// The sixth is not.
	if strings.Contains(got, "Payload.f06") {
		t.Errorf("a sixth entry was rendered past the cap of 5:\n  %s", got)
	}
	// And the ones that were dropped are COUNTED.
	if !strings.Contains(got, "+7 more") {
		t.Errorf("the dropped entries are not counted — a clipped list that does "+
			"not say so reads as the whole list:\n  %s", got)
	}
}

// 🔴 THE COUNT CAP IS NOT A LENGTH CAP. One entry's `message` is a
// server-supplied string of any length.
func TestOnePathologicalEntryCannotBlowUpTheCard(t *testing.T) {
	huge := strings.Repeat("qwertyuiop", 900) // 9,000 runes in ONE entry
	body := `{"message":"Validation Failed","errors":[{"resource":"Payload",` +
		`"field":"blob","code":"too_large","message":"` + huge + `"}]}`

	c, done := newErrorClient(t, "tok-apimessage-fixture", http.StatusUnprocessableEntity, body)
	defer done()
	got := detailOf(t, c)

	// 🔴 AN ABSOLUTE CEILING FIRST, AND IT IS NOT MADE OF THE CONSTANT UNDER
	// TEST. MEASURED: a mutant raising `maxDetailRunes` from 400 to 4,000
	// SURVIVED the relative assertion below — the clip still happened, at the
	// mutant's own number, so an assertion phrased in terms of that number
	// cannot see it. This line is what kills that mutant.
	const cardCeiling = 600
	n := len([]rune(got))
	if n > cardCeiling {
		t.Errorf("the detail is %d runes — past the %d-rune ceiling a single "+
			"terminal line can carry, whatever `maxDetailRunes` says", n, cardCeiling)
	}
	// And then the relative one, which pins that the clip is exact rather than
	// approximate.
	if n != maxDetailRunes {
		t.Errorf("the detail is %d runes, want it clipped to exactly %d", n, maxDetailRunes)
	}
	if !strings.HasSuffix(got, "…") {
		r := []rune(got)
		t.Errorf("a clipped detail must say it was clipped, it ends %q",
			string(r[len(r)-16:]))
	}
	// POSITIVE CONTROL on the clipper: a SHORT detail is not padded or cut.
	c2, done2 := newErrorClient(t, "tok-apimessage-fixture", http.StatusUnprocessableEntity,
		`{"message":"Reference update failed"}`)
	defer done2()
	if short := detailOf(t, c2); len([]rune(short)) >= maxDetailRunes {
		t.Errorf("a short detail came back at the cap — the clipper is unconditional: %q", short)
	}
}

// 🔴 THE TWO CAPS ARE PINNED AS NUMBERS, NOT ONLY AS BEHAVIOUR. Every
// assertion that names a cap is satisfied by whatever the cap happens to be, so
// without this a change from 5 to 500 entries, or from 400 to 40,000 runes,
// would leave the whole file green while the bound stopped bounding anything.
func TestTheCardCapsAreBoundsAHumanCouldRead(t *testing.T) {
	if maxReflectedErrorEntries < 1 || maxReflectedErrorEntries > 10 {
		t.Errorf("maxReflectedErrorEntries = %d — outside the range that fits in "+
			"one bar line beside the message it qualifies", maxReflectedErrorEntries)
	}
	if maxDetailRunes < 120 || maxDetailRunes > 600 {
		t.Errorf("maxDetailRunes = %d — below ~120 a real validation message is "+
			"cut off mid-reason, above ~600 the card stops being one line",
			maxDetailRunes)
	}
}

// 🔴 WIDENING WHAT WE REFLECT WIDENS THE REDACTION HOLE. `errors[]` is server
// content this client did not construct and cannot vouch for; the existing
// `TestAWriteErrorNeverCarriesTheToken` covers the credential arriving in
// `message`, and this is the same claim for the array that was just added.
func TestATokenEchoedInsideAnErrorsEntryIsRedacted(t *testing.T) {
	const token = "gho_thisisnotarealtokenitisanapimessagefixture"
	body := `{"message":"Validation Failed","errors":[{"resource":"PullRequest",` +
		`"field":"base","code":"invalid","message":"the credential ` + token +
		` may not merge this"}]}`

	c, done := newErrorClient(t, token, http.StatusUnprocessableEntity, body)
	defer done()
	got := detailOf(t, c)

	if strings.Contains(got, token) {
		t.Fatalf("the token leaked out of an `errors[]` entry: %q", got)
	}
	// POSITIVE CONTROL 1: the redaction marker is present, so the assertion
	// above is not satisfied by the entry having been dropped entirely.
	if !strings.Contains(got, "<redacted>") {
		t.Errorf("the entry was discarded rather than redacted: %q", got)
	}
	// POSITIVE CONTROL 2: the surrounding prose survived, so the entry is being
	// rendered — a renderer that dropped `errors[]` would also pass the leak
	// check, for the wrong reason.
	if !strings.Contains(got, "may not merge this") {
		t.Errorf("the entry's own text is missing: %q", got)
	}
}

// 🔴 REDACT FIRST, CLIP SECOND — AND THE ORDER IS OBSERVABLE. A token placed so
// that it STRADDLES the length cap would, under the opposite order, be cut in
// half and leave the surviving prefix un-redacted: `redact` looks for the exact
// secret, and half a secret is not it.
func TestATokenStraddlingTheLengthCapIsStillRedacted(t *testing.T) {
	const token = "gho_straddlingfixturenotarealcredential0123456789"
	// Pad so the token begins exactly 20 runes before the cap. Under the wrong
	// order the first 19 of its characters survive the cut; under the right one
	// the whole thing is `<redacted>` before the cut ever happens, and the
	// marker still fits inside the cap.
	prefix := statusLine(http.StatusUnprocessableEntity) + ": "
	pad := strings.Repeat("z", maxDetailRunes-len([]rune(prefix))-20)
	body := `{"message":"` + pad + token + ` tail"}`

	c, done := newErrorClient(t, token, http.StatusUnprocessableEntity, body)
	defer done()
	got := detailOf(t, c)

	if strings.Contains(got, token[:16]) {
		t.Fatalf("a clipped token prefix survived — the detail was clipped BEFORE "+
			"it was redacted:\n  %s", got)
	}
	if !strings.Contains(got, "<redacted>") {
		t.Errorf("the token was not redacted, it was merely cut off: %s", got)
	}
}
