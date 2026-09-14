package argv

import "testing"

// 🔴 THE EXPECTED VALUES ARE WRITTEN BY HAND FROM THE CONTRACT, NEVER DERIVED
// FROM THE IMPLEMENTATION. The contract is `nvim-octo.sh` plus the table in
// `scripts/tests/test_nvim_octo.py`, and these cases are ported from it case
// for case so the contract survives the implementation swap.

// 🔴 THE THREE CODES ARE PINNED AS LITERALS, AND THIS TEST EXISTS BECAUSE A
// MUTATION SURVIVED WITHOUT IT.
//
// MEASURED: changing `ExitBadRepo = 65` to `= 64` — collapsing the malformed-repo
// code onto the usage code, a real and silent contract break — was NOT caught.
// Every rejection test below compared `err.Code` against the CONSTANT
// `ExitBadRepo`, so the mutant moved the expectation along with the behaviour
// and the suite stayed green. That is the "fixture equals the constant the
// assertion names" trap in its purest form: a test that can only ever compare a
// value with itself cannot see a mutant that changes it.
//
// The control is mechanical — assert values the constants CANNOT supply.
func TestTheExitCodesAreTheSYSEXITSVALUESTheShellContractUses(t *testing.T) {
	// Literals, deliberately. `nvim-octo.sh` exits 64/65/66 and
	// `test_nvim_octo.py` pins those numbers; these are those numbers, typed
	// again rather than referenced.
	for _, c := range []struct {
		name string
		got  int
		want int
	}{
		{"ExitUsage", ExitUsage, 64},
		{"ExitBadRepo", ExitBadRepo, 65},
		{"ExitBadNum", ExitBadNum, 66},
	} {
		if c.got != c.want {
			t.Errorf("%s = %d, want %d — the shell contract this replaces exits "+
				"with that value, and `mention-open.py`'s caller reads it",
				c.name, c.got, c.want)
		}
	}
	// And they are mutually distinct, so a kill is attributable to one guard.
	if ExitUsage == ExitBadRepo || ExitBadRepo == ExitBadNum || ExitUsage == ExitBadNum {
		t.Fatalf("exit codes collapsed: usage=%d repo=%d num=%d",
			ExitUsage, ExitBadRepo, ExitBadNum)
	}
}
//
// ⚠ THE FIXTURES ARE PAIRWISE DISTINCT AND DISTINCT FROM EVERY CONSTANT THE
// ASSERTIONS NAME. The numbers are 1559 / 42 / 7, never 0 or 1, and the owners
// and names differ, so a mutant that hardcodes a literal or returns the wrong
// half of the split cannot produce the expected value by accident.

func TestAGoodInvocationSplitsOwnerAndName(t *testing.T) {
	got, err := Parse([]string{"gardenersguild/trowelcast", "1559"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Asserted FIELD BY FIELD, not via Repo(): `Repo()` re-joins with a slash,
	// so a mutant that swapped Owner and Name would round-trip through it
	// undetected.
	if got.Owner != "gardenersguild" {
		t.Errorf("Owner = %q, want %q", got.Owner, "gardenersguild")
	}
	if got.Name != "trowelcast" {
		t.Errorf("Name = %q, want %q", got.Name, "trowelcast")
	}
	if got.Num != 1559 {
		t.Errorf("Num = %d, want %d", got.Num, 1559)
	}
	if got.Repo() != "gardenersguild/trowelcast" {
		t.Errorf("Repo() = %q", got.Repo())
	}
}

// badRepos is ported verbatim from test_nvim_octo.py's parametrize list, plus
// the cases §5.3(e) names explicitly.
var badRepos = []string{
	"notarepo",         // no slash at all
	"too/many/slashes", // a second slash
	"/leadingslash",
	"trailing/",
	"../../etc/passwd",    // traversal, and it HAS a slash
	"owner/repo;rm -rf /", // shell metacharacters
	"owner/repo with space",
	"owner/$(whoami)",
	"",
	"owner/re..po", // traversal spelled mid-segment
	"owner/repo\n", // a newline, which a `case` glob would also reject
}

func TestABadRepositoryIsRejectedWithItsOwnCodeAndMessage(t *testing.T) {
	// 🔴 THREE HALVES, AND THE FIRST IS WHAT MAKES THE OTHER TWO MEAN ANYTHING:
	// THIS guard's own exit code, THIS guard's own message, and no Args. A
	// code alone would be satisfied by the arity guard firing instead.
	for _, repo := range badRepos {
		t.Run(repo, func(t *testing.T) {
			got, err := Parse([]string{repo, "1559"})
			if err == nil {
				t.Fatalf("accepted %q -> %+v", repo, got)
			}
			if err.Code != ExitBadRepo {
				t.Errorf("Code = %d, want %d (%q)", err.Code, ExitBadRepo, err.Msg)
			}
			if !contains(err.Msg, "not an owner/repo") {
				t.Errorf("Msg = %q, want it to name THIS guard", err.Msg)
			}
			if got != (Args{}) {
				t.Errorf("returned Args %+v on a rejection", got)
			}
		})
	}
}

// badNums is ported verbatim, plus the empty case §5.3(e) names.
var badNums = []string{"", "abc", "12a", "-1", "1.5", "1 2", "$(id)", "42;ls", "+7", " 7"}

func TestABadNumberIsRejectedWithADIFFERENTCodeFromTheRepoGuard(t *testing.T) {
	// 🔴 A DIFFERENT EXIT CODE, ON PURPOSE. A mutation test that broke the
	// repository check and watched "a test fail" would be green for the wrong
	// reason if the NUMBER guard was the one that fired. Distinct codes plus
	// distinct messages make each guard's kill attributable to itself.
	for _, num := range badNums {
		t.Run(num, func(t *testing.T) {
			_, err := Parse([]string{"rivalorg/spadeworks", num})
			if err == nil {
				t.Fatalf("accepted %q", num)
			}
			if err.Code != ExitBadNum {
				t.Errorf("Code = %d, want %d (%q)", err.Code, ExitBadNum, err.Msg)
			}
			if !contains(err.Msg, "not a reference number") {
				t.Errorf("Msg = %q, want it to name THIS guard", err.Msg)
			}
		})
	}
	if ExitBadRepo == ExitBadNum {
		t.Fatal("the two guards share an exit code — neither kill is attributable")
	}
}

func TestTheWrongNumberOfArgumentsIsRejected(t *testing.T) {
	for _, args := range [][]string{
		{},
		{"only-one"},
		{"a/b", "1", "extra"},
	} {
		_, err := Parse(args)
		if err == nil {
			t.Fatalf("accepted %d arguments", len(args))
		}
		if err.Code != ExitUsage {
			t.Errorf("%v: Code = %d, want %d", args, err.Code, ExitUsage)
		}
		if !contains(err.Msg, "usage:") {
			t.Errorf("%v: Msg = %q", args, err.Msg)
		}
	}
}

// 🔴 THE NEGATIVE CONTROL ON THE VALIDATOR, BUILT FROM REALISTIC DATA. A
// rejector that refused EVERYTHING would pass every rejection test above —
// which is the instrument-validation trap. Dots, dashes, underscores and mixed
// case all occur in real owner and repository names.
func TestRealShapedRepositoryNamesAreACCEPTED(t *testing.T) {
	for _, repo := range []string{
		"gardenersguild/trowelcast",
		"rivalorg/spadeworks",
		"innovation-upstream/devrc",
		"a-b.c/d_e.f",
		"Mixed-Case/Repo.Name_2",
	} {
		got, err := Parse([]string{repo, "7"})
		if err != nil {
			t.Errorf("rejected %q: %v", repo, err.Msg)
			continue
		}
		if got.Repo() != repo {
			t.Errorf("round trip: %q -> %q", repo, got.Repo())
		}
		if got.Num != 7 {
			t.Errorf("%q: Num = %d, want 7", repo, got.Num)
		}
	}
}

// ⚠ `"0"` IS ACCEPTED, MATCHING THE SHELL CONTRACT. `nvim-octo.sh` rejects only
// the empty string and non-digits, so `0` reaches octo today. This test pins
// the AGREEMENT rather than an improvement: two implementations of one contract
// disagreeing about a reachable input is worse than being marginally stricter.
func TestZeroIsAcceptedBecauseTheShellContractAcceptsIt(t *testing.T) {
	got, err := Parse([]string{"rivalorg/spadeworks", "0"})
	if err != nil {
		t.Fatalf("rejected %q, which nvim-octo.sh accepts: %v", "0", err.Msg)
	}
	if got.Num != 0 {
		t.Errorf("Num = %d, want 0", got.Num)
	}
}

// The one deliberate divergence, pinned so it cannot become accidental.
func TestAnOverflowingNumberIsRejectedRatherThanWrapped(t *testing.T) {
	_, err := Parse([]string{"rivalorg/spadeworks", "99999999999999999999999"})
	if err == nil {
		t.Fatal("accepted a number that cannot fit in an int")
	}
	if err.Code != ExitBadNum {
		t.Errorf("Code = %d, want %d", err.Code, ExitBadNum)
	}
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && indexOf(haystack, needle) >= 0
}

func indexOf(h, n string) int {
	for i := 0; i+len(n) <= len(h); i++ {
		if h[i:i+len(n)] == n {
			return i
		}
	}
	return -1
}
