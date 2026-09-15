package cfg

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// 🔴 THE SPLIT THIS PACKAGE MAKES IS THE THING UNDER TEST: ABSENT resolves to
// the declared default, PRESENT-AND-WRONG refuses. Every test below drives one
// side of that line, and the pair is what makes each side meaningful — a reader
// that refused everything would satisfy half of these and a reader that
// defaulted everything would satisfy the other half.

func write(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "config.json")
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestAnAbsentConfigResolvesToTheDeclaredDefault(t *testing.T) {
	p := filepath.Join(t.TempDir(), "definitely-absent.json")
	got, err := ResolveMergeMethod(p)
	if err != nil {
		t.Fatalf("an absent config is the ORDINARY state and must not error: %v", err)
	}
	if got != DefaultMergeMethod {
		t.Errorf("got %q, want the declared default %q", got, DefaultMergeMethod)
	}
	// ⚠ AND THE DECLARED DEFAULT IS THE ONE THE LUA WRAPPER USES. The two
	// surfaces have to agree or the operator's merges change shape depending on
	// which tool they opened.
	if DefaultMergeMethod != "squash" {
		t.Errorf("the declared default is %q; nvim-octo's default_merge_method is "+
			"\"squash\", and a disagreement changes the commit shape by tool",
			DefaultMergeMethod)
	}
}

func TestAConfiguredMethodIsRead(t *testing.T) {
	// ⚠ `rebase`, deliberately NOT the default: a reader that ignored the file
	// and returned the default would produce `squash` here and be caught.
	// A fixture equal to the constant the assertion names cannot see that.
	got, err := ResolveMergeMethod(write(t, `{"merge_method":"rebase"}`))
	if err != nil {
		t.Fatalf("ResolveMergeMethod: %v", err)
	}
	if got != "rebase" {
		t.Errorf("got %q, want %q", got, "rebase")
	}
	if got == DefaultMergeMethod {
		t.Fatal("the fixture equals the default, so this test cannot see a " +
			"reader that ignores the file")
	}
}

func TestAMethodIsCaseAndSpaceInsensitive(t *testing.T) {
	got, err := ResolveMergeMethod(write(t, `{"merge_method":"  REBASE "}`))
	if err != nil {
		t.Fatalf("ResolveMergeMethod: %v", err)
	}
	if got != "rebase" {
		t.Errorf("got %q, want %q", got, "rebase")
	}
}

// 🔴 EVERY UNTRUSTWORTHY CONFIG REFUSES, AND NONE OF THEM FALLS BACK. A
// fallback after a failed parse is the same lie in a new shape — and in the Lua
// it is the exact mutation (`return "squash"` in place of the sentinel) that
// survived a fully green suite.
func TestAnUntrustworthyConfigRefusesAndNeverFallsBack(t *testing.T) {
	cases := []struct {
		name string
		body string
		want string
	}{
		{"not JSON at all", `merge_method: squash`, "not valid JSON"},
		{"valid JSON, no key", `{"other":1}`, "sets no `merge_method`"},
		{"an empty method", `{"merge_method":"   "}`, "sets no `merge_method`"},
		{"a method GitHub does not have", `{"merge_method":"fast-forward"}`, "which is not one of"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := ResolveMergeMethod(write(t, c.body))
			if err == nil {
				t.Fatalf("an untrustworthy config resolved to %q instead of refusing", got)
			}
			// 🔴 THE RETURNED METHOD MUST BE EMPTY. A refusal that also handed
			// back a plausible string would be read as a value by a caller that
			// checked only the error — and the caller here dispatches merges.
			if got != "" {
				t.Errorf("a refusal returned the method %q as well as an error", got)
			}
			if !errors.Is(err, ErrNoMethod) {
				t.Errorf("the error does not wrap ErrNoMethod: %v", err)
			}
			if !strings.Contains(err.Error(), c.want) {
				t.Errorf("the failure did not come from THIS arm — a kill by a "+
					"different one is green for the wrong reason.\n got: %v\nwant to contain: %s",
					err, c.want)
			}
		})
	}
}

func TestAnUnreadableConfigRefuses(t *testing.T) {
	dir := t.TempDir()
	// A DIRECTORY where a file should be: present, and unreadable as a file.
	// ⚠ Chmod 000 is not used — a test running as root reads it anyway, and a
	// guard that silently stops guarding under one account is worse than none.
	p := filepath.Join(dir, "config.json")
	if err := os.Mkdir(p, 0o700); err != nil {
		t.Fatal(err)
	}
	got, err := ResolveMergeMethod(p)
	if err == nil {
		t.Fatalf("an unreadable config resolved to %q", got)
	}
	if !errors.Is(err, ErrNoMethod) {
		t.Errorf("the error does not wrap ErrNoMethod: %v", err)
	}
}

func TestTheValidMethodSetIsExactlyGitHubsThree(t *testing.T) {
	got := MergeMethods()
	want := map[string]bool{"merge": true, "rebase": true, "squash": true}
	if len(got) != len(want) {
		t.Fatalf("MergeMethods() = %v, want exactly %v", got, want)
	}
	for _, m := range got {
		if !want[m] {
			t.Errorf("MergeMethods() carries %q, which GitHub's merge endpoint does not accept", m)
			continue
		}
		if !ValidMergeMethod(m) {
			t.Errorf("ValidMergeMethod(%q) is false for a method in its own set", m)
		}
	}
	// 🔴 NEGATIVE CONTROL ON THE PREDICATE. Without it, a `ValidMergeMethod`
	// that returned true unconditionally would satisfy every assertion above —
	// and it is the predicate that decides what reaches a merge endpoint.
	for _, bad := range []string{"", "SQUASH", "fast-forward", "squash "} {
		if ValidMergeMethod(bad) {
			t.Errorf("ValidMergeMethod(%q) is true", bad)
		}
	}
}

// `Path()` honours XDG. ⚠ It is exercised rather than read: a function that
// built the path from a different variable would still look right in review.
func TestThePathHonoursXDGConfigHome(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", "/tmp/xdg-fixture")
	got := Path()
	want := filepath.Join("/tmp/xdg-fixture", "mention-review", "config.json")
	if got != want {
		t.Errorf("Path() = %q, want %q", got, want)
	}
}

// 🔴 AN EMPTY PATH IS THE ABSENT CASE, NOT THE UNREADABLE ONE. `Path()` returns
// "" on a host with neither `$XDG_CONFIG_HOME` nor a home directory, and
// treating that as a refusal would disable merging on a machine that simply has
// no config — which is the ordinary state, not a misconfiguration.
func TestAnEmptyPathResolvesToTheDefault(t *testing.T) {
	got, err := ResolveMergeMethod("")
	if err != nil || got != DefaultMergeMethod {
		t.Errorf("ResolveMergeMethod(\"\") = %q, %v; want %q, nil", got, err, DefaultMergeMethod)
	}
}
