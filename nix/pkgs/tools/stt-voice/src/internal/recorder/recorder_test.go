package recorder

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// alive reports whether the recorder's process still exists (Signal 0).
func alive(r *Recorder) bool {
	return r.cmd.Process.Signal(syscall.Signal(0)) == nil
}

// startWithShim prepends a fake-bin directory to PATH for the duration of the
// test and returns the shim's wav target. The shim IS the interface:
// pw-record is exec'd by bare name at runtime, so the tests exercise the SAME
// resolution a deployed binary uses.
func startWithShim(t *testing.T, body, wav, dir string) {
	t.Helper()
	if dir == "" {
		dir = t.TempDir()
	} else if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "pw-record"), []byte(
		"#!/bin/sh\n"+body), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("STT_SHIM_WAV", wav)
}

func TestArgsShapeIsTheEndpointContract(t *testing.T) {
	got := Args("/tmp/x/recording.wav")
	want := []string{"--rate", "16000", "--channels", "1", "--format", "s16",
		"/tmp/x/recording.wav"}
	if len(got) != len(want) {
		t.Fatalf("args = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("args[%d]=%q, want %q (the 16 kHz mono s16 wav contract)",
				i, got[i], want[i])
		}
	}
}

func TestStartAndStopFinalizeViaSIGINT(t *testing.T) {
	root := t.TempDir()
	wav := filepath.Join(root, "recording.wav")
	startWithShim(t, `
printf 'RIFF-fake-wav' > "$STT_SHIM_WAV"
exec sleep 30
`, wav, filepath.Join(root, "shim"))

	r, err := Start(wav)
	if err != nil {
		t.Fatalf("start: %v", err)
	}
	if r.Wav() != wav {
		t.Fatalf("Wav() = %q", r.Wav())
	}
	// the shim must be RUNNING: a Start that returns before exec would make
	// every Stop assertion below measure nothing
	if !alive(r) {
		t.Fatal("the recorder is not running after Start")
	}
	// 🔴 WAIT FOR THE RECORDING TO ACTUALLY BEGIN before stopping. Start()
	// returns after fork+execve, but the shim's first statement may not have
	// run yet (this box runs dozens of suites at once), and a SIGINT that
	// arrives before the shim writes anything produces a silent zero-wav
	// death that reads exactly like a broken Stop. The real flow never races
	// like this — stop arrives after a human hold — so the wait belongs to
	// the fixture, not to Stop.
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := os.Stat(wav); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the shimmed recorder never wrote its fixture wav")
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err := r.Stop(2 * time.Second); err != nil {
		t.Fatalf("stop: %v", err)
	}
	if alive(r) {
		t.Fatal("the recorder survived Stop")
	}
	// and the SIGINT path must have finalised the fixture wav — a Stop that
	// killed instead of letting the shim write would leave nothing behind
	if _, err := os.Stat(wav); err != nil {
		t.Fatalf("wav not written by the (shimmed) recorder: %v", err)
	}
}

func TestStopEscalatesToSIGKILLWhenSIGINTIsIgnored(t *testing.T) {
	wav := filepath.Join(t.TempDir(), "recording.wav")
	startWithShim(t, `
printf 'RIFF-fake-wav' > "$STT_SHIM_WAV"
trap '' INT
sleep 30
`, wav, "")

	r, err := Start(wav)
	if err != nil {
		t.Fatal(err)
	}
	// 🔴 Same race as the finalize test: the trap must be INSTALLED (and the
	// fixture wav written) before Stop signals, or the shim dies to a default
	// SIGINT and the test passes without ever exercising the SIGKILL fallback
	// — a guard that measured nothing.
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := os.Stat(wav); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the shimmed recorder never wrote its fixture wav")
		}
		time.Sleep(20 * time.Millisecond)
	}
	// grace far below the test timeout; the shim ignores SIGINT by trap
	if err := r.Stop(200 * time.Millisecond); err != nil {
		t.Fatalf("the SIGKILL fallback failed to end the recorder: %v", err)
	}
	if alive(r) {
		t.Fatal("the recorder is still alive after Stop")
	}
}

func TestStopOnAnAlreadyDeadRecorderReturns(t *testing.T) {
	wav := filepath.Join(t.TempDir(), "recording.wav")
	startWithShim(t, `exec sleep 1`, wav, "")
	r, err := Start(wav)
	if err != nil {
		t.Fatal(err)
	}
	<-r.Done() // the shim exits by itself after ~1s
	if err := r.Stop(time.Second); err != nil {
		t.Fatalf("Stop on an already-dead recorder: %v", err)
	}
}

func TestStartFailsWhenPwRecordIsNotOnPath(t *testing.T) {
	t.Setenv("PATH", t.TempDir()) // REPLACED: pw-record cannot resolve
	if _, err := Start(filepath.Join(t.TempDir(), "r.wav")); err == nil {
		t.Fatal("Start succeeded without any pw-record on PATH — the failure must be loud")
	}
}

func TestDoneFiresWhenTheRecorderCrashesByItself(t *testing.T) {
	// pw-record dying on its own (no mic, device busy) is the supervisor's
	// crash signal: Done must fire, carrying the exit error.
	wav := filepath.Join(t.TempDir(), "recording.wav")
	startWithShim(t, `exit 3`, wav, "")
	r, err := Start(wav)
	if err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-r.Done():
		if err == nil {
			t.Fatal("Done fired without an error for a non-zero exit")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Done never fired after the recorder exited")
	}
}
