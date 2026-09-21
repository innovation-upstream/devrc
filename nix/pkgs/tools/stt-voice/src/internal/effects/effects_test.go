package effects

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// installShim puts a fake binary by bare name on PATH for the test. The
// interface under test is "exec <name> with these args", so the shim is the
// honest instrument.
func installShim(t *testing.T, name, body string) {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, name),
		[]byte("#!/bin/sh\n"+body), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
}

func TestCopyFeedsXclipOnStdin(t *testing.T) {
	rec := filepath.Join(t.TempDir(), "copied.txt")
	installShim(t, "xclip", `cat > "$SHIM_OUT"`)
	t.Setenv("SHIM_OUT", rec)
	if err := (Live{}).Copy("paste me"); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(rec)
	if err != nil || string(got) != "paste me" {
		t.Fatalf("clipboard got %q %v", got, err)
	}
}

func TestCopySurfacesXclipFailure(t *testing.T) {
	installShim(t, "xclip", `echo "no display" >&2; exit 1`)
	if err := (Live{}).Copy("x"); err == nil || !strings.Contains(err.Error(), "xclip") {
		t.Fatalf("copy failure surfaced as %v", err)
	}
}

func TestTypeUsesStdinAndNeverBuildsAShellString(t *testing.T) {
	rec := filepath.Join(t.TempDir(), "typed.txt")
	installShim(t, "xdotool", `cat > "$SHIM_OUT"; echo "ARGS:$*" >> "$SHIM_OUT"`)
	t.Setenv("SHIM_OUT", rec)
	// a transcript that would be dangerous as an argv: flags, metachars, semicolons
	text := "run --flag; rm -rf / ; $(dangerous)"
	if err := (Live{}).Type(text); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(rec)
	if !strings.Contains(string(got), text) {
		t.Fatalf("xdotool received %q — the text must arrive via stdin verbatim", got)
	}
	if !strings.Contains(string(got), "--file -") {
		t.Fatalf("xdotool was not invoked with --file - (args line: %s)", got)
	}
}

func TestFocusChecksTheI3SuccessField(t *testing.T) {
	installShim(t, "i3-msg", `echo '[{"success":true}]'`)
	if err := (Live{}).Focus(1234); err != nil {
		t.Fatalf("focus: %v", err)
	}
	// i3 answers success:false IN JSON with exit 0 for a window that is gone;
	// reading only the exit code would type into whatever HAS focus
	installShim(t, "i3-msg", `echo '[{"success":false}]'`)
	if err := (Live{}).Focus(9999); err == nil || !strings.Contains(err.Error(), "gone") {
		t.Fatalf("a gone window surfaced as %v", err)
	}
}

func TestCaptureActiveParsesANumberAndRefusesGarbage(t *testing.T) {
	installShim(t, "xdotool", `echo 9465926`)
	id, err := (Live{}).CaptureActive()
	if err != nil || id != 9465926 {
		t.Fatalf("capture: %d %v", id, err)
	}
	installShim(t, "xdotool", `echo "not a window"`)
	if _, err := (Live{}).CaptureActive(); err == nil {
		t.Fatal("garbage must not become a window id")
	}
	installShim(t, "xdotool", `exit 1`)
	if _, err := (Live{}).CaptureActive(); err == nil {
		t.Fatal("a failing getactivewindow must be an error, not 0-as-success")
	}
}

func TestCaptureActiveNowIsZeroWhenNothingIsAnswerable(t *testing.T) {
	t.Setenv("PATH", t.TempDir()) // no xdotool anywhere
	if id := CaptureActiveNow(); id != 0 {
		t.Fatalf("CaptureActiveNow = %d, want 0 (the send path then refuses in words)", id)
	}
}

func TestToastNeverBlocksNorFails(t *testing.T) {
	// notify-send without a display server still exits 0 when it can't show;
	// either way Toast is fire-and-forget: this asserts it returns promptly
	// even when the shim hangs (Start, not Run).
	installShim(t, "notify-send", `sleep 5`)
	done := make(chan struct{})
	go func() { (Live{}).Toast("hi"); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Toast blocked — a toast must never stall a transcription flow")
	}
}

func TestStripNewlinesNeverLeavesAReturn(t *testing.T) {
	for in, want := range map[string]string{
		"one\ntwo":            "one two",
		"one\r\ntwo\rthree\n": "one two three",
		"a\n\n  b\n":          "a b",
		"flat":                "flat",
		"":                    "",
	} {
		if got := StripNewlines(in); got != want {
			t.Fatalf("StripNewlines(%q) = %q, want %q", in, got, want)
		}
	}
	if strings.ContainsAny(StripNewlines("x\ny\rz"), "\n\r") {
		t.Fatal("a Return survives — send-as-input would submit forms")
	}
}

func TestWaitForFile(t *testing.T) {
	p := filepath.Join(t.TempDir(), "wav")
	if WaitForFile(p, 50*time.Millisecond) {
		t.Fatal("WaitForFile succeeded on a missing file")
	}
	os.WriteFile(p, []byte("data"), 0o600)
	if !WaitForFile(p, time.Second) {
		t.Fatal("WaitForFile missed an existing file")
	}
	// an EMPTY file is not a recording: a finalize that has not landed must
	// not be POSTed
	os.WriteFile(p, []byte{}, 0o600)
	if WaitForFile(p, 50*time.Millisecond) {
		t.Fatal("an empty file counted as a recording")
	}
}

func TestSignalBarIsBestEffort(t *testing.T) {
	t.Setenv("PATH", t.TempDir()) // no pkill anywhere
	SignalBar(20)                 // must not panic or fail
}
