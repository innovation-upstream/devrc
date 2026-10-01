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
	"syscall"
	"time"
)

// Effects is the interface the TUI acts through.
type Effects interface {
	// Copy puts text on the CLIPBOARD selection.
	Copy(text string) error
	// SpawnSend launches the DETACHED send helper (`stt-voice send --target
	// N`, transcript on stdin) and returns without waiting for it. 🔴 THE TUI
	// MUST CLOSE BEFORE ANYTHING IS TYPED: i3's defaults (mouse_warping
	// middle + focus_follows_mouse) steal focus back to the still-open
	// transcript window when the helper would focus the target, and
	// xdotool then types the transcript INTO the TUI, where bubbletea
	// interprets it as keypresses (measured live 2026-09-30: 7 of 44
	// characters reached the target). The helper waits for the window to be
	// gone, then focuses and types; its own failures surface as a critical
	// toast from the helper. Only a failure to LAUNCH is reported here.
	SpawnSend(text string, target int) error
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

// Focus asks i3 to focus the captured container. Used by the `send` helper
// AFTER the TUI window is closed (the operator's window was captured at stop
// time, so send-as-input can put focus back where the user was); with the
// TUI gone there is no window left for i3's focus-follows-mouse to hand
// focus back to. This aligns I3's model (workspace, raise, active window);
// the X-server-level focus pin happens inside TypeInto.
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

// SpawnSend launches `self send --target N` detached (own session, transcript
// on stdin) and returns as soon as it is running. The helper outlives the TUI
// process on purpose: the TUI quits the moment this returns, and only a
// process that is not inside the closing terminal can wait for the window to
// disappear and then focus + type.
func (Live) SpawnSend(text string, target int) error {
	self, err := os.Executable()
	if err != nil {
		return fmt.Errorf("resolve self: %w", err)
	}
	cmd := exec.Command(self, "send", "--target", strconv.Itoa(target))
	cmd.Stdin = strings.NewReader(text)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("spawn send helper: %w", err)
	}
	go func() { _ = cmd.Wait() }() // reap the detached child; its errors toast
	return nil
}

// TypeInto types text into window (0 = whatever is currently focused), as
// the `send` helper's final step. It runs as ONE xdotool process —
// `windowfocus N sleep 0.1 type --delay 1 --file -` — and each piece of that
// shape is measured, not cosmetic (live probes 2026-09-30, catcher windows):
//
//   - ONE PROCESS: on a live desktop i3's focus_follows_mouse flips focus
//     the moment the operator's mouse crosses a window boundary; a
//     two-exec focus-then-type sequence leaves a 100ms+ hole for exactly
//     that, and the transcript then goes into whatever the mouse touched.
//     The chain's focus→type gap is sub-millisecond.
//   - sleep 0.1: the client needs a beat after the focus transfer, or the
//     first keystrokes of the burst are dropped entirely ('CHAINED' arrived
//     as 'e CHAINED' without it, 'CH' landing nowhere).
//   - ABORT ON A DEAD WINDOW: a failed windowfocus (BadWindow) kills the
//     xdotool process before the chained type runs (rc=1, nothing typed) —
//     a gone target refuses instead of leaking the transcript into
//     whatever is focused.
//
// The text goes in via STDIN (`type --file -`), so no shell metacharacter in
// a transcript can ever be reinterpreted as a flag; --delay 1 keeps 1ms
// between keys. 🔴 THIS NEVER SENDS RETURN: the text is one line, and Return
// would submit whatever form the operator is filling.
func (Live) TypeInto(window int, text string) error {
	args := make([]string, 0, 7)
	if window > 0 {
		args = append(args, "windowfocus", strconv.Itoa(window), "sleep", "0.1")
	}
	args = append(args, "type", "--delay", "1", "--file", "-")
	cmd := exec.Command("xdotool", args...)
	cmd.Stdin = strings.NewReader(text)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("xdotool: %v: %s", err, strings.TrimSpace(string(out)))
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
