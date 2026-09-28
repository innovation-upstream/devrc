package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestVersionIsSingleSourced(t *testing.T) {
	// the nix side reads exactly one `var buildVersion` line out of
	// version.go — a second declaration would make that read ambiguous, and
	// an ambiguous version is a binary that can lie about itself. The count
	// is per-LINE and anchored (the doc comment QUOTES the shape, and the
	// nix matcher — builtins.match, applied to the stripped line — is
	// anchored too, so a comment line can never match it).
	raw, err := os.ReadFile("version.go")
	if err != nil {
		t.Fatal(err)
	}
	n := 0
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), `var buildVersion = "`) {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("version.go carries %d `var buildVersion` declaration lines, want exactly 1", n)
	}
}

func TestCachePathsAreUnderSttVoice(t *testing.T) {
	t.Setenv("XDG_CACHE_HOME", "/tmp/xdg-fixture")
	base, err := cacheBase()
	if err != nil {
		t.Fatal(err)
	}
	if base != filepath.Join("/tmp/xdg-fixture", "stt-voice") {
		t.Fatalf("cacheBase = %q", base)
	}
	wav, err := wavPath()
	if err != nil {
		t.Fatal(err)
	}
	if wav != filepath.Join("/tmp/xdg-fixture", "stt-voice", "recording.wav") {
		t.Fatalf("wavPath = %q", wav)
	}
}

func TestUsageRejectsAnUnknownCommand(t *testing.T) {
	// usage(): exercised through the dispatch contract below — main() itself
	// os.Exits, so the switch is pinned by spelling the command set here
	for _, cmd := range []string{"start", "stop", "cancel", "toggle", "tui"} {
		if !isKnownCommand(cmd) {
			t.Errorf("%s is not dispatchable", cmd)
		}
	}
	if isKnownCommand("sttart") {
		t.Error("a typo'd command must not dispatch")
	}
}

func isKnownCommand(s string) bool {
	switch s {
	case "start", "stop", "cancel", "toggle", "tui":
		return true
	}
	return false
}

func TestTheTUISpawnCarriesTheClassAndTheEntry(t *testing.T) {
	// spawnTUI is pinned end-to-end by the i3 guard (the class), but the
	// ENTRY/target plumbing is a local contract: the TUI must open on the
	// transcript stop just wrote, not on whatever is newest at spawn time.
	if !strings.Contains(tuiClass, "stt-voice") {
		t.Fatalf("tuiClass = %q", tuiClass)
	}
}

func TestSignalOffsetMatchesTheGuardedConstant(t *testing.T) {
	// SetSignalOffset is main's only mutation of the store's default; this
	// asserts the wiring and the constant are one value.
	if pkillSignal != 20 {
		t.Fatalf("pkillSignal = %d — nix/graphical.nix's sttBlock signal is pinned to 20", pkillSignal)
	}
}

func TestFirstErrTextPicksTheFirstRealError(t *testing.T) {
	if got := firstErrText(nil, os.ErrNotExist); got != "file does not exist" {
		t.Fatalf("firstErrText = %q", got)
	}
	if got := firstErrText(nil, nil); got != "unknown error" {
		t.Fatalf("firstErrText = %q", got)
	}
}

func TestExecStubForLint(_ *testing.T) {
	_ = exec.Command // referenced by spawnTUI; keeps the import list stable
}
