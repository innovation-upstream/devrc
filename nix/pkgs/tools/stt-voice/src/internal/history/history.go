// Package history is the transcript history: a jsonl file in the tool's cache
// directory, last MaxEntries entries, atomically rewritten on every append so
// a crash can never leave a half-written entry behind.
//
// 🔴 THE TUI OPENS ON THE NEWEST ENTRY, which is why every entry carries an
// id (unix nanoseconds): the stop path passes `--entry <id>` and the TUI
// locates it, so a second recording finishing while the first TUI is still
// open cannot hijack which transcript is on screen.
//
// 🔴 CORRUPT LINES ARE SKIPPED, NEVER FATAL. The file is user data in a cache
// directory; one bad line (a truncated write from an older tool, a disk
// hiccup) must not take the history down. Append preserves the good lines and
// drops the bad ones on the next rewrite.
package history

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// MaxEntries is the trim size (the feature spec: last 10).
const MaxEntries = 10

// Entry is one transcript row.
type Entry struct {
	ID        int64   `json:"id"`
	TS        float64 `json:"ts"`
	Text      string  `json:"text"`
	DurationS float64 `json:"duration_s"`
	ElapsedS  float64 `json:"elapsed_s"`
}

// Path is the jsonl file inside the tool's cache directory.
func Path(base string) string { return filepath.Join(base, "history.jsonl") }

// Load reads the entries oldest→newest, skipping lines that do not parse.
func Load(base string) ([]Entry, error) {
	raw, err := os.ReadFile(Path(base))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var out []Entry
	for _, line := range strings.Split(strings.TrimRight(string(raw), "\n"), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var e Entry
		if json.Unmarshal([]byte(line), &e) != nil {
			continue // corrupt line: skipped, never fatal
		}
		if strings.TrimSpace(e.Text) == "" {
			continue // an entry with no transcript is not history
		}
		out = append(out, e)
	}
	return out, nil
}

// Append adds one entry and trims to the last MaxEntries, writing the WHOLE
// file atomically (temp + rename in the same directory). Reads tolerate a
// missing file (fresh host) and corrupt lines (dropped).
func Append(base string, e Entry) error {
	existing, _ := Load(base)
	all := append(existing, e)
	if len(all) > MaxEntries {
		all = all[len(all)-MaxEntries:]
	}
	var buf strings.Builder
	for _, row := range all {
		line, err := json.Marshal(row)
		if err != nil {
			return err
		}
		buf.Write(line)
		buf.WriteByte('\n')
	}
	dir := base
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("history dir: %w", err)
	}
	tmp, err := os.CreateTemp(dir, ".history-*.jsonl")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() {
		if tmpName != "" {
			os.Remove(tmpName) // no-op after a successful rename
		}
	}()
	if _, err := tmp.WriteString(buf.String()); err != nil {
		tmp.Close()
		return err
	}
	// Match state.Set's durability: Close flushes to page cache only, so a
	// crash between Close and Rename could leave a truncated history file.
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmpName, Path(base)); err != nil {
		return err
	}
	tmpName = "" // renamed; the deferred remove must not touch the real file
	return nil
}
