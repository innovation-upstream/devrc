// Package recorder owns the pw-record child process.
//
// 🔴 THE RECORDING FORMAT IS A CONTRACT WITH THE ASR ENDPOINT, NOT A
// PREFERENCE: 16 kHz mono signed-16-bit wav (the feature spec). Changing a
// number here changes what the self-hosted transcriber receives.
//
// 🔴 pw-record IS RESOLVED VIA PATH, NEVER A STORE-PINNED OVERRIDE HERE, AND
// THE TESTS SHIM IT THE SAME WAY. A fake `pw-record` earlier on PATH is how
// every recorder test runs (the shim writes a fixture wav and honours
// SIGINT), which is also exactly how the real binary is resolved at runtime.
//
// 🔴 THE WAV HEADER IS WRITTEN WHEN pw-record CLOSES. pw-cat/pw-record write
// a placeholder length at open and patch it on close, so the finalize path
// must give it a real SIGINT and let it exit — a SIGKILLed recorder leaves a
// headerless file the server will reject. Stop() therefore signals, waits a
// grace period, and only then kills.
package recorder

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"time"
)

const (
	// SampleRate/Channels/Format are the endpoint's contract (see header).
	SampleRate = 16000
	Channels   = 1
	Format     = "s16"

	// Bin is exec'd by bare name, so the tests (and a debugging operator)
	// can shim it via PATH.
	Bin = "pw-record"

	// FinalizeGrace is how long Stop waits for pw-record to exit after
	// SIGINT before escalating to SIGKILL. Real recordings close in
	// milliseconds; the grace only ever burns in the wedged-recorder case.
	FinalizeGrace = 5 * time.Second
)

// Args builds the pw-record invocation for one wav. The .wav extension is
// what selects the wav muxer (pw-cat infers the file format from it); the
// default target is the default microphone, which is the whole point of the
// tool.
func Args(wav string) []string {
	return []string{
		"--rate", fmt.Sprint(SampleRate),
		"--channels", fmt.Sprint(Channels),
		"--format", Format,
		wav,
	}
}

// Recorder is one running pw-record. The wait is owned by ONE goroutine
// (exec.Cmd.Wait is not safe to call concurrently); `done` carries the exit
// error to Done() exactly once, and `finished` is closed the moment the wait
// completes — Stop must observe completion even when a caller already
// drained done, so completion and the error are two separate signals.
type Recorder struct {
	cmd      *exec.Cmd
	done     chan error
	finished chan struct{}
	wav      string
}

// Start execs pw-record writing to wav.
func Start(wav string) (*Recorder, error) {
	cmd := exec.Command(Bin, Args(wav)...)
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("pw-record: %w", err)
	}
	r := &Recorder{cmd: cmd, done: make(chan error, 1),
		finished: make(chan struct{}), wav: wav}
	go func() {
		err := cmd.Wait()
		close(r.finished)
		r.done <- err
	}()
	return r, nil
}

// Done fires when the recorder exits BY ITSELF (a crash, a killed device) —
// the supervisor's signal that the recording failed. It never fires as a
// result of Stop (which observes `finished` instead).
func (r *Recorder) Done() <-chan error { return r.done }

// Wav is the file being written.
func (r *Recorder) Wav() string { return r.wav }

// Stop finalizes the recording: SIGINT, wait grace, SIGKILL fallback. The
// return is "the child is gone" (nil) or "it would not die" (an error) — the
// wav's VALIDITY is judged by whoever POSTs it, not by pw-record's exit
// code, so a finalization that needed the kill is not reported as a failure
// here; the headerless file it may have left is what the server will reject,
// and that path is the transcribe error path.
func (r *Recorder) Stop(grace time.Duration) error {
	if r.cmd.Process != nil {
		_ = r.cmd.Process.Signal(os.Interrupt)
	}
	select {
	case <-r.finished:
		return nil
	case <-time.After(grace):
	}
	if r.cmd.Process != nil {
		_ = r.cmd.Process.Kill()
	}
	select {
	case <-r.finished:
		return nil
	case <-time.After(2 * time.Second):
		return errors.New("pw-record did not exit even after SIGKILL")
	}
}
