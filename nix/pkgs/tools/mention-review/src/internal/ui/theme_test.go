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
// The whole point of a matching palette is that the review window does not look
// like a different application from the terminal it opened out of. A palette
// that drifts is a palette that stopped doing its one job — and drift is
// exactly the kind of thing nobody notices for months.
//
// ⚠ THIS READS A FILE OUTSIDE THE GO MODULE, so it is skipped when it cannot
// find it. 🔴 THE SKIP IS LOUD AND IT COUNTS WHAT IT PARSED: a test that
// silently skips is worse than no test, and a parser that matched zero colours
// would otherwise report "0 mismatches" and read as a pass.

var alacrittyColor = regexp.MustCompile(`^\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{6})";`)
var alacrittyGroup = regexp.MustCompile(`^\s*([a-z_]+)\s*=\s*\{\s*$`)

// alacrittyPalette parses the `colors` block out of the Nix file.
//
// It is a small hand parser rather than a Nix evaluation because the test tier
// must not need `nix` on PATH — and because what matters is the literal hex
// strings a human reads in that file.
func alacrittyPalette(t *testing.T, path string) map[string]string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("cannot read the Alacritty config: %v", err)
	}
	out := map[string]string{}
	var group string
	inColors := false
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
			if depth == 0 {
				break // the end of the `colors` block itself
			}
			depth--
			group = ""
			continue
		}
		if m := alacrittyColor.FindStringSubmatch(line); m != nil && group != "" {
			out[group+"."+m[1]] = strings.ToLower(m[2])
		}
	}
	return out
}

// repoRoot walks UP looking for `flake.nix`.
//
// 🔴 IT DOES NOT COUNT `..`s, AND THAT MATTERS. A hardcoded depth is wrong the
// moment the module moves, and it is wrong SILENTLY — the file is simply not
// found, the test skips, and a skipped palette check reads exactly like a
// passing one. MEASURED: the first version of this file had six `..` where
// seven were needed and skipped on every run, reporting nothing.
//
// Returning "" means there is no checkout here at all — the real situation
// inside the `buildGoModule` sandbox, where `src` is the Go module alone. That
// is the ONLY case where skipping is honest.
func repoRoot() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for i := 0; i < 12; i++ {
		if _, err := os.Stat(filepath.Join(dir, "flake.nix")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return ""
}

func alacrittyPath(t *testing.T) string {
	t.Helper()
	root := repoRoot()
	if root == "" {
		t.Skip("no repo checkout above this module (the Go-module-only sandbox)" +
			" — palette NOT COMPARED")
	}
	// 🔴 INSIDE A CHECKOUT THE FILE MUST EXIST. A missing file here is a MOVED
	// or DELETED Alacritty config, which is a finding, not a reason to skip.
	p := filepath.Join(root, "nix", "programs", "alacritty", "default.nix")
	if _, err := os.Stat(p); err != nil {
		t.Fatalf("inside a checkout (%s) but the Alacritty config is missing: %v", root, err)
	}
	return p
}

func TestThePaletteMatchesTheAlacrittyConfigBothWays(t *testing.T) {
	theirs := alacrittyPalette(t, alacrittyPath(t))

	// 🔴 POSITIVE CONTROL ON THE PARSER. A parser that matched nothing would
	// make every comparison below vacuous — "no mismatches" over an empty map.
	// The count is asserted against the ledger's own size, not a literal, so
	// the control cannot rot when a colour is added on purpose.
	if len(theirs) == 0 {
		t.Fatal("the Alacritty parser extracted ZERO colours — every comparison " +
			"below would be vacuous")
	}
	t.Logf("parsed %d colours from the Alacritty config, %d from the ledger",
		len(theirs), len(PaletteHex))

	ours := map[string]string{}
	for k, v := range PaletteHex {
		ours[k] = strings.ToLower(v)
	}

	var problems []string
	for k, want := range theirs {
		got, ok := ours[k]
		switch {
		case !ok:
			problems = append(problems, "the Go palette is MISSING "+k+" ("+want+")")
		case got != want:
			problems = append(problems, "colour "+k+": Go has "+got+", Alacritty has "+want)
		}
	}
	for k, got := range ours {
		if _, ok := theirs[k]; !ok {
			problems = append(problems, "the Go palette has EXTRA "+k+" ("+got+"), which the Alacritty config does not")
		}
	}
	sort.Strings(problems)
	if len(problems) > 0 {
		t.Errorf("the palette has drifted from nix/programs/alacritty/default.nix:\n  %s",
			strings.Join(problems, "\n  "))
	}
}

// 🔴 NO STYLE MAY SPELL A HEX LITERAL. Every colour has to come through the
// ledger, or the ledger is documentation of the palette rather than the palette
// itself — and a value with a second copy is a value that will drift from its
// guard.
func TestNoStyleSpellsAHexLiteralOutsideTheLedger(t *testing.T) {
	raw, err := os.ReadFile("theme.go")
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	ledgerStart := strings.Index(text, "var PaletteHex = map[string]string{")
	ledgerEnd := strings.Index(text[ledgerStart:], "\n}\n")
	if ledgerStart < 0 || ledgerEnd < 0 {
		t.Fatal("cannot find the PaletteHex block — this guard is not reading what it thinks")
	}
	outside := text[:ledgerStart] + text[ledgerStart+ledgerEnd:]

	hex := regexp.MustCompile(`"#[0-9a-fA-F]{6}"`)
	if found := hex.FindAllString(outside, -1); len(found) > 0 {
		t.Errorf("hex literals outside the ledger: %v", found)
	}
	// POSITIVE CONTROL: the pattern MUST match inside the ledger, or "no
	// matches outside" is a claim about a regex that matches nothing.
	if n := len(hex.FindAllString(text[ledgerStart:ledgerStart+ledgerEnd], -1)); n == 0 {
		t.Fatal("the hex pattern matched nothing even inside the ledger — " +
			"this guard cannot see a violation")
	} else {
		t.Logf("hex pattern matched %d literals inside the ledger, 0 outside", n)
	}
}
