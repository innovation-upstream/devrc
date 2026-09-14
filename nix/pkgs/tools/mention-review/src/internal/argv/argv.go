// Package argv holds the command-line contract, and NOTHING else.
//
// 🔴 THE CONTRACT IS INHERITED, NOT INVENTED. `scripts/mention-open.py` spawns
// `alacritty … -e <review-exe> <owner/repo> <number>` — two plain argv entries,
// no quoting anywhere. `nvim-octo.sh` validates them today and exits 64/65/66;
// `scripts/tests/test_nvim_octo.py` pins that table. This package reproduces it
// so the contract survives an implementation swap rather than being re-derived,
// and `argv_test.go` carries the same cases so the two implementations can be
// compared case for case.
//
// 🔴 THREE DISTINCT EXIT CODES, ON PURPOSE. A mutation test that breaks the
// repository check and watches "a test fail" is green for the wrong reason if
// the NUMBER guard is the one that fired. Distinct codes plus distinct messages
// make each guard's kill attributable to itself.
package argv

import "strings"

// Exit codes. Asserted by VALUE everywhere, never as "non-zero" — non-zero is
// also what a missing binary, a panic and a failed dynamic link produce.
const (
	ExitUsage   = 64 // wrong number of arguments
	ExitBadRepo = 65 // malformed owner/repo
	ExitBadNum  = 66 // non-numeric or empty number
)

// Args is a validated invocation.
type Args struct {
	Owner string
	Name  string
	Num   int
}

// Repo renders the "owner/repo" spelling the GitHub API and the UI both want.
func (a Args) Repo() string { return a.Owner + "/" + a.Name }

// Error carries the exit code AND the message, because the pair is the
// contract: an operator who sees a window close learns nothing from the code,
// and a test that asserts only the code passes against a wrapper that launched
// first and complained after.
type Error struct {
	Code int
	Msg  string
}

func (e *Error) Error() string { return e.Msg }

// Parse validates the two positional arguments.
//
// `args` is argv WITHOUT the program name — i.e. os.Args[1:].
func Parse(args []string) (Args, *Error) {
	if len(args) != 2 {
		return Args{}, &Error{
			Code: ExitUsage,
			Msg:  "usage: mention-review <owner/repo> <number>",
		}
	}
	repo, num := args[0], args[1]

	owner, name, ok := splitRepo(repo)
	if !ok {
		return Args{}, &Error{
			Code: ExitBadRepo,
			Msg:  "mention-review: not an owner/repo: " + repo,
		}
	}
	n, ok := parseNum(num)
	if !ok {
		return Args{}, &Error{
			Code: ExitBadNum,
			Msg:  "mention-review: not a reference number: " + num,
		}
	}
	return Args{Owner: owner, Name: name, Num: n}, nil
}

// splitRepo accepts exactly one slash, no traversal, and only the characters
// GitHub allows in an owner or a repository name.
//
// ⚠ THE REJECTIONS COME FIRST AND THEY WIN. `strings.Cut` alone would accept
// `../../etc/passwd`, which HAS a slash — the shell version this is ported from
// records the same ordering trap in its `case` glob, where the negative arm is
// deliberately first.
func splitRepo(s string) (owner, name string, ok bool) {
	if s == "" {
		return "", "", false
	}
	// `..` anywhere — traversal, and it also covers `.` -only segments that
	// would resolve outside the intended repository when interpolated.
	if strings.Contains(s, "..") {
		return "", "", false
	}
	for _, r := range s {
		if !allowedRepoRune(r) {
			return "", "", false
		}
	}
	// Exactly one slash, and neither side empty. This one check subsumes "no
	// slash at all", "a second slash", "a leading slash" and "a trailing
	// slash" — the four cases the shell glob spells separately.
	owner, name, found := strings.Cut(s, "/")
	if !found || owner == "" || name == "" || strings.Contains(name, "/") {
		return "", "", false
	}
	return owner, name, true
}

// allowedRepoRune is the character class GitHub actually permits. It is an
// ALLOWLIST rather than a denylist of metacharacters: a denylist has to
// enumerate every shell, every interpolation context and every future one.
func allowedRepoRune(r rune) bool {
	switch {
	case r >= 'a' && r <= 'z':
		return true
	case r >= 'A' && r <= 'Z':
		return true
	case r >= '0' && r <= '9':
		return true
	case r == '.' || r == '_' || r == '-' || r == '/':
		return true
	}
	return false
}

// maxNum bounds the accumulator. See the DIVERGENCE note on parseNum.
const maxNum = 1 << 40

// parseNum requires digits only, at least one.
//
// ⚠ NOT strconv.Atoi. Atoi accepts a leading `+` or `-`, and `-1` is a value
// the shell contract rejects with code 66. The digit walk IS the contract;
// the accumulator is incidental.
//
// 🔴 `"0"` IS ACCEPTED, DELIBERATELY, BECAUSE THE SHELL CONTRACT ACCEPTS IT.
// `case "$num" in "" | *[!0-9]*)` rejects only the empty string and non-digits,
// so `0` reaches octo today. Rejecting it here would make this binary and
// `nvim-octo` disagree about a reachable input while both claim to implement
// one contract — and `0` is not silently wrong, it produces the `NOT FOUND`
// card §6.1 specifies. Matching the existing behaviour is worth more than
// being marginally stricter than it.
//
// ⚠ ONE KNOWN DIVERGENCE, AND IT IS STATED RATHER THAN HIDDEN: a digit string
// long enough to overflow `int` is rejected with 66 where the shell would pass
// it through. Go would wrap silently; the shell hands the string to another
// program. No mention click can produce such an input — `mention-open.py`
// builds the number out of a `#N` match in terminal text — so this arm is
// unreachable from the click path and exists only so the accumulator cannot
// produce a negative or wrapped number if something else ever calls it.
func parseNum(s string) (int, bool) {
	if s == "" {
		return 0, false
	}
	n := 0
	for _, r := range s {
		if r < '0' || r > '9' {
			return 0, false
		}
		n = n*10 + int(r-'0')
		if n > maxNum {
			return 0, false
		}
	}
	return n, true
}
