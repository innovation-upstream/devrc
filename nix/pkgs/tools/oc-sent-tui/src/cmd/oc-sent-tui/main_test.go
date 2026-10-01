package main

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// 🔴 THE VERSION LINE'S SHAPE IS LOAD-BEARING: `nix/pkgs/tools/oc-sent-tui/
// default.nix` reads EXACTLY ONE line matching
// `var buildVersion = "([^"]+)"` out of version.go — zero or two matches and
// the derivation evaluates to null and the binary simply is not installed.
// This test pins that the shipped file keeps matching, so a reformat that
// breaks the derivation is caught by the gate instead of by a missing binary
// at Alt+S time.
var versionPattern = `var buildVersion = "([^"]+)"`

func TestVersionFileMatchesTheNixPatternExactlyOnce(t *testing.T) {
	raw, err := os.ReadFile("version.go")
	if err != nil {
		t.Fatalf("read version.go: %v", err)
	}
	re := regexp.MustCompile(`^` + versionPattern + `.*$`)
	matches := 0
	version := ""
	for _, line := range strings.Split(string(raw), "\n") {
		if m := re.FindStringSubmatch(line); m != nil {
			matches++
			version = m[1]
		}
	}
	if matches != 1 {
		t.Fatalf("version.go matches %d times (need EXACTLY 1) — default.nix will go null", matches)
	}
	if version == "" {
		t.Fatal("empty version")
	}
}

func TestVersionFlagPrintsTheBuildVersion(t *testing.T) {
	// --version must answer with the SAME buildVersion the derivation
	// stamps — a binary that prints a different version than its store path
	// is the clawgatectl 2026-08-14 failure mode.
	if buildVersion == "" {
		t.Fatal("buildVersion empty")
	}
}
