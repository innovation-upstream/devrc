package main

import (
	"os"
	"path/filepath"
)

// The per-user cache directory this tool owns. Everything it writes lives
// here: state.json (the bar pill reads it), recording.wav (one at a time —
// start no-ops while a recording is live) and history.jsonl.
func cacheBase() (string, error) {
	base, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "stt-voice"), nil
}

// wavPath is THE in-flight recording. A fixed name is deliberate: two
// recordings cannot run at once (the state machine refuses), so a second name
// would only ever accumulate debris.
func wavPath() (string, error) {
	base, err := cacheBase()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "recording.wav"), nil
}
