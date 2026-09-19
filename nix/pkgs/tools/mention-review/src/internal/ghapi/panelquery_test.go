package ghapi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
)

// 🔴 THE PANEL QUERY → SNAPSHOT → `TerminalPRState` SEAM, WHICH NOBODY OWNED.
//
// `ui/write.go` refuses `m` on a pull request that is already over by calling
// `ghapi.TerminalPRState(a.Snap.State, a.Snap.Merged)`. Those two Snapshot
// fields come from ONE place — the `... on PullRequest` line of the panel
// `Query` — and until this file existed, nothing asserted that the query selects
// them.
//
// MEASURED at `d2ec72c3`: deleting `state` and `merged` from that line left the
// ENTIRE module green (`go test ./...`, all 6 packages ok). Every layer was
// verified in isolation and the seam between them was not: `ghapi_test.go`
// decodes canned bodies that always carry the fields, and `ui/write_test.go`
// assigns them directly onto a fixture. So `Snapshot.State` would be "",
// `Snapshot.Merged` false, `TerminalPRState` "" forever, and the UI-side
// terminal-PR guard silently DEAD — with a green suite. The round-1 audit closed
// exactly this class for `MergeableQuery`; it left it open here.
//
// 🔴 WHY THIS IS NOT A TEXT PIN ON THE QUERY STRING. A pin like
// `strings.Contains(Query, "state isDraft merged")` would be walkable by any
// reformatting of a document whose whitespace nothing else depends on, and it
// would assert nothing about whether the decoded field reaches the consumer. The
// guard below drives the REAL `Fetch` against a server that HONOURS THE QUERY —
// a field the document did not select does not appear in the body, which is what
// a real GraphQL server does — and then asks the actual consumer predicate.
// Query, decode and consumer are asserted as one relationship.

// prFragmentSelections maps each field the panel `Query` selects DIRECTLY on the
// PullRequest — the scalars that land in a `Snapshot` field, nothing nested —
// onto the KEY a server answers it under.
//
// 🔴 A FLAT IDENTIFIER SET IS NOT ENOUGH HERE, AND THAT IS THE WHOLE REASON THIS
// FUNCTION EXISTS. `requestedFields` (mergegate_test.go) is sufficient for
// `MergeableQuery` because that document is four lines long and names each word
// once. The panel `Query` names `state` in FIVE places — the Issue fragment, the
// PullRequest fragment, the review nodes, the check rollup's own `state`, and
// the `... on StatusContext` arm inside its contexts — so a
// flat set still reports `state` as selected after it has been deleted from the
// pull request. A fake built on the flat set would answer `state` for a mutant
// that stopped asking for it, and the mutant would SURVIVE. That exact blindness
// is asserted below, against a probe document, so this claim is checked rather
// than believed.
//
// 🔴 AND THE KEY IS THE ALIAS WHERE THERE IS ONE. A server answers `prState:
// state` as `{"prState":…}`, with no `state` in the body at all; a fake that
// emitted `"state"` for it would feed the decoder's `state` json tag while
// production got nothing. MEASURED at `cb7c7949`: aliasing `state` on the
// PullRequest fragment left `go test ./internal/ghapi/` `ok`. This document
// already aliases one selection (`rollup: commits(last:1)`), so the shape is not
// hypothetical.
//
// The rule: an identifier at the fragment's own brace depth whose next
// non-space character is not `{` or `(` — which excludes selections with
// children (`author{login}`) and selections with arguments (`commits(last:100)`,
// `rollup: commits(…)`). An identifier followed by `:` is an ALIAS, and binds to
// the selection after it. Argument lists are skipped rather than scanned,
// because `last:100` is a parameter and not an alias.
func prFragmentSelections(query string) map[string]string {
	out := map[string]string{}
	const marker = "... on PullRequest"
	i := strings.Index(query, marker)
	if i < 0 {
		return out
	}
	body := query[i+len(marker):]
	open := strings.IndexByte(body, '{')
	if open < 0 {
		return out
	}
	body = body[open+1:]

	depth := 0
	alias := ""
	for j := 0; j < len(body); j++ {
		switch body[j] {
		case '{':
			depth++
			alias = ""
			continue
		case '}':
			if depth == 0 {
				return out // the fragment's own closing brace
			}
			depth--
			continue
		case '(':
			k := strings.IndexByte(body[j:], ')')
			if k < 0 {
				return out
			}
			j += k
			continue
		}
		if depth != 0 {
			continue
		}
		loc := gqlIdent.FindStringIndex(body[j:])
		if loc == nil || loc[0] != 0 {
			continue
		}
		name := body[j : j+loc[1]]
		j += loc[1] - 1
		k := j + 1
		for k < len(body) && gqlSpace(body[k]) {
			k++
		}
		if k < len(body) && body[k] == ':' {
			alias = name
			j = k
			continue
		}
		if k < len(body) && (body[k] == '{' || body[k] == '(') {
			alias = ""
			continue
		}
		key := name
		if alias != "" {
			key, alias = alias, ""
		}
		out[name] = key
	}
	return out
}

// panelFake answers the PANEL read and HONOURS THE QUERY.
//
// ⚠ IT KNOWS THREE SCALARS, ON PURPOSE. A fake that reproduced the whole
// fixture would be a second copy of `prFixture`; this one answers the minimum a
// Snapshot needs to exist plus the two fields under test, and every one of them
// is gated on the document having selected it. `__typename` is emitted
// unconditionally because the query selects it OUTSIDE both fragments, on
// `issueOrPullRequest` itself.
type panelFake struct {
	state  string
	merged bool
	title  string
	// lastBody is the last response written, so a test can look at what the fake
	// actually said rather than only at what the client made of it.
	lastBody string
}

func (f *panelFake) handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Query string `json:"query"`
		}
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &req)
		asked := prFragmentSelections(req.Query)

		// ⚠ THE RESPONSE KEY, NOT THE FIELD NAME. An aliased selection comes back
		// under its alias, which is what a real server does and what makes an
		// alias mutant visible here instead of silently passing.
		fields := []string{`"__typename":"PullRequest"`}
		if key := asked["number"]; key != "" {
			fields = append(fields, `"`+key+`":1559`)
		}
		if key := asked["title"]; key != "" {
			fields = append(fields, `"`+key+`":"`+f.title+`"`)
		}
		if key := asked["state"]; key != "" {
			fields = append(fields, `"`+key+`":"`+f.state+`"`)
		}
		if key := asked["merged"]; key != "" {
			fields = append(fields, `"`+key+`":`+strconv.FormatBool(f.merged))
		}
		f.lastBody = `{"data":{"viewer":{"login":"a-reviewer"},` +
			`"repository":{"issueOrPullRequest":{` + strings.Join(fields, ",") + `}}}}`
		_, _ = io.WriteString(w, f.lastBody)
	}
}

// 🔴 THE GUARD. Each case is answerable by exactly ONE of the two fields, so a
// mutant that drops either one — not only a mutant that drops both — dies here.
//
// ⚠ THE EXPECTATIONS ARE THE VOCABULARY CONSTANTS AND THE EMPTY STRING, WRITTEN
// FROM WHAT `TerminalPRState` PROMISES, never read off the decoder.
func TestThePanelQuerySelectsTheFieldsTheTerminalPRGuardReads(t *testing.T) {
	cases := []struct {
		name  string
		state string
		// merged is the server's `merged`.
		merged bool
		want   string
		// why names the field this case is the sole witness for.
		why string
	}{
		{
			name: "a CLOSED pull request — only `state` can answer",
			// `merged` is false, so it carries no information: if the query stops
			// selecting `state`, nothing is left to make this terminal.
			state: PRStateClosed, merged: false, want: PRStateClosed,
			why: "`state`",
		},
		{
			name: "a MERGED pull request — only `merged` can answer",
			// `state` is OPEN, which is not terminal: if the query stops selecting
			// `merged`, nothing is left to make this terminal.
			state: PRStateOpen, merged: true, want: PRStateMerged,
			why: "`merged`",
		},
		{
			// 🔴 THE NEGATIVE CONTROL, in the same table. Without it every
			// assertion above is satisfiable by a predicate that answers terminal
			// for everything.
			name:  "an OPEN, unmerged pull request is not terminal",
			state: PRStateOpen, merged: false, want: "",
			why: "neither field",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := &panelFake{state: tc.state, merged: tc.merged, title: "a title"}
			srv := httptest.NewServer(f.handler())
			defer srv.Close()
			c := NewClient("tok-panelquery-fixture", srv.Client())
			c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

			s, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559)
			if err != nil {
				t.Fatalf("Fetch: %v", err)
			}
			if s.Kind != KindPullRequest {
				t.Fatalf("Kind = %q — the fake did not answer a pull request, so this "+
					"case proves nothing", s.Kind)
			}
			got := TerminalPRState(s.State, s.Merged)
			if got != tc.want {
				t.Errorf("TerminalPRState(Snapshot.State=%q, Snapshot.Merged=%v) = %q, want %q.\n"+
					"This case is answerable by %s ALONE, and the server did send it — so "+
					"the panel `Query` no longer SELECTS it. `ui/write.go` refuses `m` on a "+
					"pull request that is already over through exactly this call, and it is "+
					"now dead: the guard is silent for every merged or closed pull request.\n"+
					"The server's response body was: %s",
					s.State, s.Merged, got, tc.want, tc.why, f.lastBody)
			}
		})
	}

	// 🔴 POSITIVE CONTROL ON THE FAKE ITSELF: it OMITS a field the document did
	// not select. Without this the assertions above would hold just as well
	// against a fake that answered every field unconditionally — which is the
	// blindness the whole file exists to close, and which `prFixture` and the
	// server in `TestFetchAgainstAFakeServerDecodesAndReusesOneRoundTrip` still
	// have (both answer one canned body whatever is asked).
	t.Run("the fake answers only what the PullRequest fragment selects", func(t *testing.T) {
		f := &panelFake{state: PRStateMerged, merged: true, title: "a title"}
		srv := httptest.NewServer(f.handler())
		defer srv.Close()
		c := NewClient("tok-panelquery-fixture", srv.Client())
		c.SetBaseURLs(srv.URL+"/graphql", srv.URL)

		// The shipped query selects all three, so all three come back.
		if _, err := c.Fetch(context.Background(), "gardenersguild", "trowelcast", 1559); err != nil {
			t.Fatalf("Fetch: %v", err)
		}
		for _, want := range []string{`"state"`, `"merged"`, `"title"`} {
			if !strings.Contains(f.lastBody, want) {
				t.Errorf("the shipped query did not get %s back: %s", want, f.lastBody)
			}
		}

		// A hand-written document that selects `title` and nothing else gets
		// `title` and nothing else.
		narrow := `query{ repository{ issueOrPullRequest(number:1){ __typename
		  ... on PullRequest { title } } } }`
		payload, _ := json.Marshal(map[string]any{"query": narrow})
		resp, err := srv.Client().Post(srv.URL+"/graphql", "application/json",
			strings.NewReader(string(payload)))
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(resp.Body)
		body := string(raw)
		if !strings.Contains(body, `"title"`) {
			t.Errorf("a query selecting `title` did not get it: %s", body)
		}
		for _, unwanted := range []string{`"state"`, `"merged"`} {
			if strings.Contains(body, unwanted) {
				t.Errorf("the fake answered %s for a document that never selected it — its "+
					"selection filter is wired to nothing, so every mutant that narrows the "+
					"panel `Query` would SURVIVE: %s", unwanted, body)
			}
		}

		// 🔴 AND AN ALIASED SELECTION COMES BACK UNDER ITS ALIAS. Pinning that
		// only through a mutant of the SHIPPED document is not enough: the
		// shipped one aliases nothing, so a fake re-keyed to the field name
		// behaves identically and SURVIVES on its own — MEASURED — while
		// silently re-enabling every alias mutant it was meant to catch.
		aliased := `query{ repository{ issueOrPullRequest(number:1){ __typename
		  ... on PullRequest { prState: state merged } } } }`
		payload2, _ := json.Marshal(map[string]any{"query": aliased})
		resp2, err := srv.Client().Post(srv.URL+"/graphql", "application/json",
			strings.NewReader(string(payload2)))
		if err != nil {
			t.Fatal(err)
		}
		defer resp2.Body.Close()
		raw2, _ := io.ReadAll(resp2.Body)
		body2 := string(raw2)
		if !strings.Contains(body2, `"prState"`) {
			t.Errorf("an aliased `state` did not come back under its alias: %s", body2)
		}
		if strings.Contains(body2, `"state":`) {
			t.Errorf("the fake answered `state` for a document that aliased it to `prState` — "+
				"it is keyed by the field name rather than the response key, so a query that "+
				"aliased the field would decode into an empty `Snapshot.State` in production "+
				"while every test here stayed green: %s", body2)
		}
		// The unaliased neighbour keeps its own name, so the check above is about
		// aliasing rather than about a fake that renames everything.
		if !strings.Contains(body2, `"merged"`) {
			t.Errorf("an unaliased `merged` lost its own name: %s", body2)
		}
	})
}

// 🔴 THE EXTRACTOR IS NOT BLIND WHERE A FLAT IDENTIFIER SET IS, AND THIS IS THE
// CONTROL THAT SAYS SO RATHER THAN ASSUMING IT.
//
// The probe document below names `state` and `merged` in places that are NOT the
// PullRequest's own selection set — an Issue fragment, a nested `reviews` node,
// an aliased rollup — and names neither on the pull request itself. A flat set
// reports them present; the fragment-scoped one must not. If this ever stopped
// holding, the fake above would answer `state` for a mutant that deleted it and
// the guard would go quietly vacuous.
func TestTheFragmentScopedExtractorIsNotFooledByTheSameWordElsewhere(t *testing.T) {
	const probe = `
query{
  repository{
    issueOrPullRequest(number:1){
      __typename
      ... on Issue { number title state merged }
      ... on PullRequest {
        number title
        author{login}
        reviews(last:50){ nodes{ author{login} state } }
        rollup: commits(last:1){ nodes{ commit{ statusCheckRollup{ state } } } }
      }
    }
  }
}`
	sel := prFragmentSelections(probe)
	for _, name := range []string{"number", "title"} {
		if sel[name] == "" {
			t.Errorf("%q is selected directly on the PullRequest and the extractor missed it", name)
		}
	}
	for _, name := range []string{"state", "merged", "login", "author", "reviews", "rollup",
		"nodes", "commit", "statusCheckRollup"} {
		if sel[name] != "" {
			t.Errorf("the extractor reports %q as selected on the PullRequest, but the probe "+
				"names it only in a sibling fragment or a nested selection — so it is a flat "+
				"identifier set in disguise and cannot see a mutant that narrows the fragment",
				name)
		}
	}

	// 🔴 THE DEMONSTRATION THAT THE DISTINCTION IS REAL. The flat set — the one a
	// reasonable person would have reached for — DOES report both words for the
	// same document. If this ever fails, the comment above is wrong about why
	// this function exists.
	flat := requestedFields(probe)
	for _, name := range []string{"state", "merged"} {
		if flat[name] == "" {
			t.Errorf("the flat identifier set does NOT report %q for the probe — the reason "+
				"`prFragmentSelections` exists no longer holds, and one of the two should go",
				name)
		}
	}

	// 🔴 THE SECOND BLINDNESS, AND THE CONTROL THAT SAYS IT IS CLOSED: AN ALIAS.
	// A server answers `prState: state` under the ALIAS, so an extractor that
	// reported the field's own name would have the fake emit `"state"`, the
	// decoder would find it, and the mutant would SURVIVE while production read
	// nothing. MEASURED at `cb7c7949`, with the whole package `ok`.
	const aliasedProbe = `
query{
  repository{
    issueOrPullRequest(number:1){
      __typename
      ... on PullRequest { number title prState: state merged }
    }
  }
}`
	al := prFragmentSelections(aliasedProbe)
	if al["state"] != "prState" {
		t.Errorf("the extractor keys `state` as %q for a document that asked for "+
			"`prState: state` — the fake answers under a key the server would never "+
			"use, so every alias mutant SURVIVES", al["state"])
	}
	// The unaliased neighbours keep their own names, so the assertion above is
	// about aliasing rather than about an extractor that renames everything.
	for _, name := range []string{"number", "title", "merged"} {
		if al[name] != name {
			t.Errorf("an unaliased %q is keyed as %q, want its own name", name, al[name])
		}
	}

	// And on the SHIPPED document the extractor finds something rather than
	// silently returning an empty set, which would make every assertion in this
	// file pass by answering no fields at all.
	shipped := prFragmentSelections(Query)
	if len(shipped) == 0 {
		t.Fatal("the extractor found NO directly-selected fields on the shipped panel " +
			"`Query` — it is returning an empty set, and the guard above is vacuous")
	}
	if shipped["login"] != "" || shipped["oid"] != "" || shipped["isResolved"] != "" {
		t.Errorf("the extractor reports a NESTED field as directly selected on the shipped "+
			"query: %v", shipped)
	}
}
