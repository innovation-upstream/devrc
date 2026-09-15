// Package ghapi is the GitHub client: token resolution, ONE GraphQL read, and
// the REST diff read. It owns every network call this binary makes.
package ghapi

import "net/http"

// AuthState is the classification §6.1 requires the failure cards to be able to
// make. 🔴 `NO TOKEN` and `TOKEN REJECTED` are explicitly distinguished,
// because the fixes differ: one is `gh auth login`, the other is a token that
// exists and was refused.
type AuthState int

const (
	AuthOK AuthState = iota
	// AuthNoToken — go-gh resolved nothing. Note its fourth rung returns
	// `("", "default")` with NO ERROR, so an empty string is the only signal
	// there is; a caller that checks only `err` sees success.
	AuthNoToken
	// AuthRejected — a token was resolved and GitHub answered 401.
	AuthRejected
	// AuthNotFound — 404. Separated because a browser session may have access
	// this token does not, so the card offers `o` (§6.1).
	AuthNotFound
	// AuthRateLimited — 403/429 with a rate-limit signal.
	AuthRateLimited
	// AuthOther — reached the server, got something else.
	AuthOther
)

// Word is the WORD this state renders as. 🔴 Every meaning-bearing state is a
// WORD and colour is decoration on top of it, never the carrier (§3.1). The
// operator's font renders the red/yellow/green severity circles as one
// indistinguishable glyph, so a coloured dot carries nothing.
func (s AuthState) Word() string {
	switch s {
	case AuthOK:
		return "OK"
	case AuthNoToken:
		return "NO TOKEN"
	case AuthRejected:
		return "TOKEN REJECTED"
	case AuthNotFound:
		return "NOT FOUND"
	case AuthRateLimited:
		return "RATE LIMITED"
	}
	return "ERROR"
}

// Hint is the fix, in words, for each state. Never the token, never a header,
// never a URL with a credential in it (§10.4).
func (s AuthState) Hint() string {
	switch s {
	case AuthNoToken:
		return "run `gh auth login`"
	case AuthRejected:
		return "the token exists but GitHub refused it"
	case AuthNotFound:
		return "not visible to this token — `o` opens it in the browser"
	case AuthRateLimited:
		return "wait for the reset shown above"
	}
	return ""
}

// Classify maps a resolved token plus an HTTP status onto the card §6.1 draws.
//
// 🔴 PURE, AND TAKING THE TOKEN'S EMPTINESS RATHER THAN THE TOKEN. It never
// sees a credential, so no test of it can leak one and no error path built on
// it can print one.
func Classify(haveToken bool, statusCode int) AuthState {
	if !haveToken {
		return AuthNoToken
	}
	switch statusCode {
	case http.StatusOK:
		return AuthOK
	case http.StatusUnauthorized:
		return AuthRejected
	case http.StatusNotFound:
		return AuthNotFound
	case http.StatusForbidden, http.StatusTooManyRequests:
		// 🔴 403 IS AMBIGUOUS AND IS NOT ASSUMED TO BE RATE LIMITING. GitHub
		// answers 403 both for a spent rate limit and for a token whose scopes
		// do not cover the request. The caller passes the rate-limit signal
		// separately via ClassifyResponse; this arm is the conservative
		// default for a bare status code.
		return AuthRateLimited
	}
	return AuthOther
}

// ClassifyResponse is Classify with the one header that disambiguates 403.
// `remaining` is the value of `x-ratelimit-remaining`, or -1 when absent.
func ClassifyResponse(haveToken bool, statusCode int, remaining int) AuthState {
	s := Classify(haveToken, statusCode)
	if s == AuthRateLimited && statusCode == http.StatusForbidden && remaining != 0 {
		// A 403 with budget left is a SCOPE problem, not a rate limit. Calling
		// it RATE LIMITED would send the operator to wait for a reset that is
		// not coming.
		return AuthOther
	}
	return s
}
