package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// 🔴 LAYER 3(e) — THE ARGV CONTRACT, DRIVEN AGAINST THE *BINARY*.
//
// `internal/argv` already tests the parser exhaustively. This tests something
// the parser tests structurally cannot: that `main` WIRES it to `os.Exit` with
// the right code, before drawing anything and before touching the network.
//
// Ported from `scripts/tests/test_nvim_octo.py`, which pinned the same contract
// for the implementation this one replaced, so the contract survived the swap
// rather than being re-derived. ⚠ That file was deleted in Phase 4 — see
// `internal/argv`'s package comment for the git incantation that recovers it,
// and note that these cases are now the only binary-level statement of the
// contract rather than a second opinion on it.
//
// 🔴 EXIT CODES ARE ASSERTED BY VALUE, NEVER AS "NON-ZERO". Non-zero is also
// what a missing binary, a panic and a failed dynamic link produce — and
// `_run_wrapper`'s note in the Python file records 18 tests that passed against
// a tree with no wrapper at all.

var binary string

func TestMain(m *testing.M) {
	dir, err := os.MkdirTemp("", "mention-review-argv-")
	if err != nil {
		panic(err)
	}
	defer os.RemoveAll(dir)

	binary = filepath.Join(dir, "mention-review")
	build := exec.Command("go", "build", "-o", binary, ".")
	build.Stderr = os.Stderr
	if err := build.Run(); err != nil {
		panic("could not build the binary under test: " + err.Error())
	}
	os.Exit(m.Run())
}

// run executes the binary with a DELIBERATELY EMPTY credential environment.
//
// 🔴 THE POINT IS THAT NONE OF THESE CASES REACHES THE NETWORK ANYWAY — every
// one of them must exit on argv before a token is even resolved. Clearing the
// environment makes that a property the test enforces rather than one it hopes
// for: if a case ever DID reach the fetch, it would hit the NO TOKEN path and
// hang on a TUI instead of exiting, which the timeout would then catch.
func run(t *testing.T, args ...string) (rc int, stdout, stderr string) {
	t.Helper()
	cmd := exec.Command(binary, args...)
	cmd.Env = []string{"HOME=/nonexistent", "PATH=/nonexistent", "GH_CONFIG_DIR=/nonexistent"}
	var out, errb strings.Builder
	cmd.Stdout = &out
	cmd.Stderr = &errb
	err := cmd.Run()
	if ee, ok := err.(*exec.ExitError); ok {
		return ee.ExitCode(), out.String(), errb.String()
	}
	if err != nil {
		t.Fatalf("running the binary: %v", err)
	}
	return 0, out.String(), errb.String()
}

func TestTheWrongNumberOfArgumentsExits64(t *testing.T) {
	for _, args := range [][]string{
		{},
		{"only-one"},
		{"gardenersguild/trowelcast", "1559", "extra"},
	} {
		rc, _, stderr := run(t, args...)
		if rc != 64 {
			t.Errorf("%v: exit %d, want 64 (%q)", args, rc, stderr)
		}
		if !strings.Contains(stderr, "usage:") {
			t.Errorf("%v: stderr = %q, want a usage line", args, stderr)
		}
	}
}

func TestAMalformedRepositoryExits65(t *testing.T) {
	for _, repo := range []string{
		"notarepo",
		"too/many/slashes",
		"/leadingslash",
		"trailing/",
		"../../etc/passwd",
		"owner/repo;rm -rf /",
		"owner/repo with space",
		"owner/$(whoami)",
		"",
	} {
		rc, _, stderr := run(t, repo, "1559")
		if rc != 65 {
			t.Errorf("%q: exit %d, want 65 (%q)", repo, rc, stderr)
		}
		if !strings.Contains(stderr, "not an owner/repo") {
			t.Errorf("%q: stderr = %q, want THIS guard's own message", repo, stderr)
		}
	}
}

func TestABadNumberExits66(t *testing.T) {
	for _, num := range []string{"", "abc", "12a", "-1", "1.5", "1 2", "$(id)", "42;ls"} {
		rc, _, stderr := run(t, "gardenersguild/trowelcast", num)
		if rc != 66 {
			t.Errorf("%q: exit %d, want 66 (%q)", num, rc, stderr)
		}
		if !strings.Contains(stderr, "not a reference number") {
			t.Errorf("%q: stderr = %q, want THIS guard's own message", num, stderr)
		}
	}
}

// 🔴 THE THREE CODES ARE DISTINCT, ASSERTED DIRECTLY. Without this, a build
// that collapsed them all onto 64 would pass every test above that happened to
// exercise the arity guard first.
func TestTheThreeExitCodesAreDistinct(t *testing.T) {
	usage, _, _ := run(t)
	repo, _, _ := run(t, "notarepo", "1559")
	num, _, _ := run(t, "gardenersguild/trowelcast", "abc")
	if usage == repo || repo == num || usage == num {
		t.Fatalf("exit codes collapsed: usage=%d repo=%d num=%d", usage, repo, num)
	}
}

// 🔴 `--version` PRINTS THE VALUE `default.nix` READS OUT OF version.go, AND
// THE TWO ARE PINNED TOGETHER.
//
// The Nix expression matches `var buildVersion = "([^"]+)".*` in version.go and
// stamps the captured string back via `-X`. This asserts the running binary
// reports the same string THAT PATTERN would capture — so the store path's
// version and the compiled-in one are provably one value, which is the entire
// point of reading the version out of the source instead of writing it in the
// derivation.
func TestVersionMatchesWhatTheNixPatternWouldCapture(t *testing.T) {
	src, err := os.ReadFile("version.go")
	if err != nil {
		t.Fatal(err)
	}
	// The SAME regular expression `default.nix` uses, transcribed from Nix's
	// `builtins.match` (which is anchored) to Go's syntax.
	re := regexp.MustCompile(`(?m)^var buildVersion = "([^"]+)".*$`)
	all := re.FindAllStringSubmatch(string(src), -1)
	// 🔴 EXACTLY ONE MATCHING LINE, OR NOTHING — the Nix side switches the
	// package OFF on zero or two matches rather than guessing. This test is
	// what stops that state reaching a build.
	if len(all) != 1 {
		t.Fatalf("version.go has %d lines matching the Nix pattern, want exactly 1", len(all))
	}
	want := all[0][1]

	rc, stdout, stderr := run(t, "--version")
	if rc != 0 {
		t.Fatalf("--version exited %d (%q)", rc, stderr)
	}
	if got := strings.TrimSpace(stdout); got != want {
		t.Errorf("the binary reports %q, the Nix pattern would capture %q", got, want)
	}
	if want == "" {
		t.Error("the captured version is empty — the comparison above is vacuous")
	}
}
