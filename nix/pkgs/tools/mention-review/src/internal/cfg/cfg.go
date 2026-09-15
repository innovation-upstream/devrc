// Package cfg resolves the few operator settings this TUI has. Today that is
// one: the merge METHOD.
//
// 🔴 THE MERGE METHOD IS READ, NEVER GUESSED (§3.7). This is carried over from
// `nvim-octo`'s Lua wrapper unchanged in substance, and the reason is written
// down there: *"a merge dispatched with a method nobody chose is the wrong
// commit shape on a repo whose last five merges were all squashes, and the
// prompt would have named the guess as though it were the setting."* That
// wrapper refuses rather than substituting a plausible value, and so does this.
//
// ⚠ THE DIFFERENCE FROM THE LUA, STATED RATHER THAN SMOOTHED OVER. octo's
// wrapper reads a THIRD-PARTY live config it does not own, so *absent* and
// *unreadable* are the same condition there — both mean "octo's state is not
// what I think it is" — and both refuse. Here the config file is ours, so the
// two are genuinely different facts:
//
//   - ABSENT is the ordinary state on a host nobody has configured, and it
//     resolves to `DefaultMergeMethod` — which is a DECLARATION, not a guess:
//     it is spelled exactly once, in this file, and the prompt names the value
//     that was actually used.
//   - PRESENT-AND-UNREADABLE, or present and naming a method GitHub does not
//     have, is the octo case exactly. It REFUSES, because the operator plainly
//     intended *something* and we cannot tell what.
//
// That split is the whole design decision in this package, and it is the one
// worth arguing with if it is wrong.
package cfg

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// DefaultMergeMethod is the declared setting for a host with no config file.
//
// 🔴 SPELLED ONCE. It matches `nvim-octo`'s `default_merge_method = "squash"`
// and the operator's actual merge history, and the only reason it is allowed to
// exist at all is that the confirmation prompt NAMES the method it is about to
// use — so a default that was wrong for this repo is readable on screen before
// anything irreversible happens, rather than inferred afterwards from the
// commit shape.
const DefaultMergeMethod = "squash"

// MergeMethods is the set GitHub's `PUT /pulls/{n}/merge` accepts. A value
// outside it is a refusal, not a pass-through: the API would answer 422 and the
// operator would read a server error for what is really a typo in their config.
func MergeMethods() []string { return []string{"merge", "rebase", "squash"} }

// ValidMergeMethod reports whether `m` is one GitHub accepts.
//
// 🔴 ONE PREDICATE, ONE PLACE. Both the config reader below and
// `ghapi.Client.Merge` branch on this same function rather than each carrying
// its own `switch`. A predicate open-coded at two sites is wrong at one of them
// eventually, and this one decides what gets sent to a merge endpoint.
func ValidMergeMethod(m string) bool {
	for _, ok := range MergeMethods() {
		if m == ok {
			return true
		}
	}
	return false
}

// Config is the on-disk shape. One field today; the file exists so the method
// is a SETTING rather than a constant, which is what makes "read, never
// guessed" true of the running program and not only of the source.
type Config struct {
	MergeMethod string `json:"merge_method"`
}

// Path is where the file lives. `$XDG_CONFIG_HOME` is honoured because the rest
// of this host's tooling does.
func Path() string {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			// No home and no XDG: there is no config file, which is the ABSENT
			// case, not the unreadable one. An empty path reads as absent.
			return ""
		}
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "mention-review", "config.json")
}

// ErrNoMethod is returned when the config exists but cannot say what the merge
// method is. 🔴 The caller must REFUSE on this, never substitute.
var ErrNoMethod = errors.New("the merge method could not be read")

// ResolveMergeMethod reads `path` and returns the method to use.
//
// It returns `(DefaultMergeMethod, nil)` when the file does not exist, and
// `("", err)` — wrapping ErrNoMethod — when it exists and cannot be trusted.
// 🔴 IT NEVER RETURNS A METHOD IT DID NOT READ OR DECLARE. There is no arm that
// falls back to the default after a failed parse: that is the same lie in a new
// shape, and it is exactly the mutation that survived a green suite in the Lua.
func ResolveMergeMethod(path string) (string, error) {
	if path == "" {
		return DefaultMergeMethod, nil
	}
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return DefaultMergeMethod, nil
	}
	if err != nil {
		return "", fmt.Errorf("%w: %s is present but unreadable: %v",
			ErrNoMethod, path, err)
	}
	var c Config
	if err := json.Unmarshal(raw, &c); err != nil {
		return "", fmt.Errorf("%w: %s is not valid JSON: %v", ErrNoMethod, path, err)
	}
	if strings.TrimSpace(c.MergeMethod) == "" {
		// A file that exists and says nothing about the method is not the same
		// as no file: the operator wrote a config. Refusing here is the
		// conservative direction, and the message says which key is missing.
		return "", fmt.Errorf("%w: %s sets no `merge_method`", ErrNoMethod, path)
	}
	m := strings.ToLower(strings.TrimSpace(c.MergeMethod))
	if !ValidMergeMethod(m) {
		methods := MergeMethods()
		sort.Strings(methods)
		return "", fmt.Errorf("%w: %s sets merge_method=%q, which is not one of %s",
			ErrNoMethod, path, c.MergeMethod, strings.Join(methods, ", "))
	}
	return m, nil
}
