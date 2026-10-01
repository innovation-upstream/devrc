package ui

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// 🔴 THE PALETTE IS PINNED TWO-WAY AGAINST THE ALACRITTY CONFIG.
//
// The whole point of a matching palette is that the popup does not look like
// a different application from the terminal it opened out of. A palette that
// drifts is a palette that stopped doing its one job — and drift is exactly
// the kind of thing nobody notices for months.
//
// ⚠ THIS READS A FILE OUTSIDE THE GO MODULE, so it is skipped when it cannot
// find it. 🔴 THE SKIP IS LOUD AND IT COUNTS WHAT IT PARSED: a test that
// silently skips is worse than no test, and a parser that matched zero
// colours would otherwise report "0 mismatches" and read as a pass.

var alacrittyColor = regexp.MustCompile(`^\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{6})";`)
var alacrittyGroup = regexp.MustCompile(`^\s*([a-z_]+)\s*=\s*\{\s*$`)

// alacrittyPalette parses the `colors` block out of the Nix file.
//
// It is a small hand parser rather than a Nix evaluation because the test
// tier must not need `nix` on PATH — and because what matters is the literal
// hex strings a human reads in that file.
func alacrittyPalette(t *testing.T, path string) map[string]string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("cannot read the Alacritty config (no repo checkout above the module?): %v", err)
	}
	out := map[string]string{}
	var group string
	inColors := false
	// 🔴 DEPTH, NOT A BARE `};` — the block nests (primary/normal/bright
	// groups INSIDE colors), so the first `};` after a group closes that
	// GROUP, not the block. Dropping the counter (measured) ends parsing at
	// primary's closer and "parses" 2 colours — every normal/bright key then
	// reads as "no counterpart", the inverse of a real drift.
	depth := 0
	for _, line := range strings.Split(string(raw), "\n") {
		trimmed := strings.TrimSpace(line)
		if !inColors {
			if trimmed == "colors = {" {
				inColors = true
			}
			continue
		}
		if m := alacrittyGroup.FindStringSubmatch(line); m != nil {
			group = m[1]
			depth++
			continue
		}
		if trimmed == "};" {
			depth--
			// depth 0 after a GROUP's closer is still inside `colors` —
			// the block ends only when its OWN `};` takes depth negative
			if depth < 0 {
				inColors = false
			}
			continue
		}
		if m := alacrittyColor.FindStringSubmatch(line); m != nil && group != "" {
			out[group+"."+m[1]] = strings.ToLower(m[2])
		}
	}
	return out
}

func TestPaletteMatchesAlacritty(t *testing.T) {
	// the module lives at nix/pkgs/tools/oc-sent-tui/src — the alacritty
	// config is SIX levels up from this package dir (ui → internal → src →
	// oc-sent-tui → tools → pkgs → the repo root)
	path := filepath.Join("..", "..", "..", "..", "..", "..", "programs", "alacritty", "default.nix")
	palette := alacrittyPalette(t, path)
	if len(palette) == 0 {
		t.Fatal("parser matched ZERO colours — the skip guard above cannot tell a real match from an empty one")
	}
	for key, hex := range PaletteHex {
		want, ok := palette[key]
		if !ok {
			t.Errorf("ledger key %q has no counterpart in the alacritty palette", key)
			continue
		}
		if want != strings.ToLower(hex) {
			t.Errorf("%s drifted: ui=%s alacritty=%s", key, hex, want)
		}
	}
	// and the reverse direction: nothing in the palette is unledgered
	keys := make([]string, 0, len(PaletteHex))
	for k := range PaletteHex {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for key := range palette {
		found := false
		for _, k := range keys {
			if k == key {
				found = true
			}
		}
		if !found && (strings.HasPrefix(key, "normal.") || strings.HasPrefix(key, "bright.")) {
			t.Errorf("alacritty colour %q is not in the ui ledger — the ledger is the whole palette or it is nothing", key)
		}
	}
}

func TestEveryStyleUsesTheLedger(t *testing.T) {
	// Nothing outside PaletteHex may spell a hex literal in this package —
	// a second copy of a value is how a value and its guard drift apart.
	src := map[string]string{}
	for _, f := range []string{"theme.go", "tui.go"} {
		raw, err := os.ReadFile(filepath.Join(".", f))
		if err != nil {
			t.Fatalf("read %s: %v", f, err)
		}
		src[f] = string(raw)
	}
	hexRe := regexp.MustCompile(`"#[0-9a-fA-F]{6}"`)
	for f, body := range src {
		if f == "theme.go" {
			// the ledger file is exactly where hexes belong
			continue
		}
		for _, m := range hexRe.FindAllString(body, -1) {
			t.Errorf("%s spells a hex literal %s — use the ledger", f, m)
		}
	}
}
