package history

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAppendAndLoadRoundTrip(t *testing.T) {
	base := t.TempDir()
	if entries, err := Load(base); err != nil || len(entries) != 0 {
		t.Fatalf("fresh host: %v %v — a missing file is empty history, not an error", entries, err)
	}
	if err := Append(base, Entry{ID: 1, TS: 1.0, Text: "first"}); err != nil {
		t.Fatal(err)
	}
	entries, err := Load(base)
	if err != nil || len(entries) != 1 || entries[0].Text != "first" {
		t.Fatalf("round trip: %v %v", entries, err)
	}
}

func TestAppendTrimsAtTen(t *testing.T) {
	base := t.TempDir()
	for i := 0; i < 12; i++ {
		if err := Append(base, Entry{ID: int64(i), Text: fmt.Sprintf("entry-%d", i)}); err != nil {
			t.Fatal(err)
		}
	}
	entries, err := Load(base)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != MaxEntries {
		t.Fatalf("kept %d entries, want %d", len(entries), MaxEntries)
	}
	if entries[0].Text != "entry-2" || entries[len(entries)-1].Text != "entry-11" {
		t.Fatalf("the trim kept the WRONG half: first=%q last=%q (the OLDEST must go)",
			entries[0].Text, entries[len(entries)-1].Text)
	}
}

func TestAppendIsAtomic_NoTempLitter(t *testing.T) {
	base := t.TempDir()
	if err := Append(base, Entry{ID: 1, Text: "one"}); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(base)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".history-") {
			t.Fatalf("temp litter left behind: %s", e.Name())
		}
	}
}

func TestCorruptLinesAreSkippedNeverFatal(t *testing.T) {
	base := t.TempDir()
	p := Path(base)
	// one good line, one corrupt (a truncated write), one good
	os.WriteFile(p, []byte(
		`{"id":1,"ts":1,"text":"good-one"}`+"\n"+
			`{"id":2,"ts":2,"tex`+"\n"+
			`{"id":3,"ts":3,"text":"good-two"}`+"\n"), 0o600)
	entries, err := Load(base)
	if err != nil {
		t.Fatalf("a corrupt line must be skipped, not fatal: %v", err)
	}
	if len(entries) != 2 || entries[0].Text != "good-one" || entries[1].Text != "good-two" {
		t.Fatalf("entries = %+v", entries)
	}
	// and Append PRESERVES the good lines while dropping the bad one
	if err := Append(base, Entry{ID: 4, Text: "newest"}); err != nil {
		t.Fatal(err)
	}
	entries, _ = Load(base)
	if len(entries) != 3 {
		t.Fatalf("after append: %+v", entries)
	}
	if entries[0].Text != "good-one" || entries[2].Text != "newest" {
		t.Fatalf("append lost good history: %+v", entries)
	}
	// the corrupt line must be GONE from the file after the rewrite
	raw, _ := os.ReadFile(p)
	if strings.Contains(string(raw), `"tex`) && !strings.Contains(string(raw), `"text"`) {
		t.Fatalf("the corrupt line survived the rewrite: %s", raw)
	}
}

func TestEmptyTextEntriesAreNotHistory(t *testing.T) {
	base := t.TempDir()
	os.WriteFile(Path(base), []byte(`{"id":1,"ts":1,"text":"  "}`+"\n"), 0o600)
	entries, err := Load(base)
	if err != nil || len(entries) != 0 {
		t.Fatalf("a whitespace transcript is not history: %v %v", entries, err)
	}
}

func TestPathIsInTheCacheDirectory(t *testing.T) {
	if got := Path("/tmp/xdg-fixture/stt-voice"); got != filepath.Join("/tmp/xdg-fixture/stt-voice", "history.jsonl") {
		t.Fatalf("Path = %q", got)
	}
}
