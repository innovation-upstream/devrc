// Package effects is stt-voice's live X11/session layer — the small surface
// where the Go tool touches the world outside its own files.
//
// 🔴 EVERY EFFECT IS AN INTERFACE (Effects), SO THE TUI TESTS RUN WITH A FAKE
// THAT RECORDS CALLS. No test here or in ui can reach a real X server; the
// LIVE implementations below are exec'd by bare name and are exercised for
// real only by the operator's MANUAL VERIFY checklist.
//
// 🔴 THE BINARIES ARE RESOLVED VIA PATH, AND THE PACKAGING PINS THEM. A
// stt-voice spawned from an i3 `exec` runs with the session PATH, which
// ~/.nix-profile blanks for ~30s during every home-manager switch — so
// default.nix wraps the binary with makeBinPath over exactly the tools these
// functions exec. If a new effect gains a new binary, the wrap list in
// nix/pkgs/tools/stt-voice/default.nix must grow with it.
package effects

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// Effects is the interface the TUI acts through.
type Effects interface {
	// Copy puts text on the CLIPBOARD selection.
	Copy(text string) error
	// Focus raises a window by i3 container id (the stop path captured the
	// operator's window before the TUI opened, so send-as-input can put
	// focus back where the user was).
	Focus(containerID int) error
	// Type types text into the FOCUSED window. Newlines are already stripped
	// by the caller; this never sends Return (a Return submits forms).
	Type(text string) error
	// CaptureActive returns the id of the currently focused window, or an
	// error when nothing answerable is focused.
	CaptureActive() (int, error)
	// Toast surfaces a failure in words. Best effort; never blocks the flow.
	Toast(msg string)
}

// Live is the real implementation.
type Live struct{}

// Copy feeds xclip on stdin: no argument parsing, no shell, no length limit.
func (Live) Copy(text string) error {
	cmd := exec.Command("xclip", "-selection", "clipboard")
	cmd.Stdin = strings.NewReader(text)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("xclip: %v: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// Focus asks i3 to focus the captured container.
func (Live) Focus(containerID int) error {
	out, err := exec.Command("i3-msg", fmt.Sprintf("[id=%d]", containerID), "focus").CombinedOutput()
	if err != nil {
		return fmt.Errorf("i3-msg focus: %v: %s", err, strings.TrimSpace(string(out)))
	}
	// i3-msg answers with JSON success:false rather than a failure exit code;
	// a bracketed id that no longer exists is the common case (the window was
	// closed while the operator was in the TUI).
	if !strings.Contains(string(out), `"success":true`) {
		return fmt.Errorf("i3-msg focus: window %d is gone: %s",
			containerID, strings.TrimSpace(string(out)))
	}
	return nil
}

// Type types text via xdotool reading STDIN (`type --file -`), so no shell
// metacharacter in a transcript can ever be reinterpreted as a flag. --delay 1
// keeps 1ms between keys. 🔴 THIS NEVER SENDS RETURN: the text is one line,
// and Return would submit whatever form the operator is filling.
func (Live) Type(text string) error {
	cmd := exec.Command("xdotool", "type", "--delay", "1", "--file", "-")
	cmd.Stdin = strings.NewReader(text)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("xdotool type: %v: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// CaptureActive reads the id of the focused window.
func (Live) CaptureActive() (int, error) {
	out, err := exec.Command("xdotool", "getactivewindow").Output()
	if err != nil {
		return 0, fmt.Errorf("xdotool getactivewindow: %v", err)
	}
	id, err := strconv.Atoi(strings.TrimSpace(string(out)))
	if err != nil || id <= 0 {
		return 0, fmt.Errorf("xdotool getactivewindow: %q is not a window id", strings.TrimSpace(string(out)))
	}
	return id, nil
}

// Toast is notify-send through dunst, urgent so it is not auto-dismissed.
// Fire-and-forget: a toast must never block or fail a transcription flow.
func (Live) Toast(msg string) {
	_ = exec.Command("notify-send", "-u", "critical", "-a", "stt-voice",
		"-t", "6000", "stt-voice", msg).Start()
}

// ToastAsync is Toast for callers outside the Effects interface (the stop
// path's error legs).
func Toast(msg string) { Live{}.Toast(msg) }

// CaptureActiveNow is the stop path's helper: the operator's window id, or 0
// when nothing could be captured (send-as-input then refuses in words rather
// than typing into whatever happens to have focus).
func CaptureActiveNow() int {
	id, err := (Live{}).CaptureActive()
	if err != nil {
		return 0
	}
	return id
}

// StripNewlines replaces every newline (and carriage return) with a space:
// send-as-input must never type Return, which submits forms. Runs of
// whitespace are collapsed so the typed text is one line.
func StripNewlines(text string) string {
	text = strings.ReplaceAll(text, "\r\n", " ")
	text = strings.ReplaceAll(text, "\r", " ")
	text = strings.ReplaceAll(text, "\n", " ")
	return strings.Join(strings.Fields(text), " ")
}

// WaitForFile polls path until it exists or the deadline passes (used by the
// stop path before POSTing, so a finalize that has not landed yet waits
// instead of POSTing an empty file).
func WaitForFile(path string, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if st, err := os.Stat(path); err == nil && st.Size() > 0 {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return false
}

// SignalBar repainting the pill (the store calls this after every write).
// Best effort: a missing pkill must never fail a state write.
func SignalBar(signal int) {
	_ = exec.Command("pkill", fmt.Sprintf("-RTMIN+%d", signal), "i3status-rs").Run()
}
