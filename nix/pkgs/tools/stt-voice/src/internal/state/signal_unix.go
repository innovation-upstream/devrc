package state

import (
	"errors"
	"os"
	"syscall"
	"time"
)

// signalPid sends a signal to a pid; the callers are killZero and
// EndRecording, so the error surface stays small.
func signalPid(pid int, sig os.Signal) error {
	p, err := os.FindProcess(pid)
	if err != nil {
		return err
	}
	return p.Signal(sig)
}

// killZero is `kill -0`: true when the pid exists and is signalable. A
// PermissionError means it exists but belongs to someone else — alive. This
// is the ONLY liveness probe in the tool, and it lives here so the store's
// repair and EndRecording's wait loop can never disagree.
func killZero(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := signalPid(pid, syscall.Signal(0))
	return err == nil || errors.Is(err, os.ErrPermission)
}

// killSignal is the last-resort signal EndRecording sends when a supervisor
// refuses to exit after SIGINT (it alone is allowed to be heavy-handed; the
// tests never reach it because their shims honor SIGINT).
var killSignal os.Signal = syscall.SIGKILL

// waitForDeath polls kill -0 until the pid is gone or the deadline passes.
func waitForDeath(pid int, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if !killZero(pid) {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return !killZero(pid)
}
