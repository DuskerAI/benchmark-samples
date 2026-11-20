#!/bin/bash
set -euo pipefail

cat > /app/internal/recovery/snapshot.go <<'GO'
package recovery

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

func loadSnapshot(caseDir string) (Snapshot, error) {
	paths, err := filepath.Glob(filepath.Join(caseDir, "snapshots", "*.json"))
	if err != nil {
		return Snapshot{}, err
	}
	if len(paths) == 0 {
		return Snapshot{}, fmt.Errorf("no snapshots")
	}
	var snaps []Snapshot
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return Snapshot{}, err
		}
		var snap Snapshot
		if err := json.Unmarshal(data, &snap); err != nil {
			return Snapshot{}, err
		}
		if snap.KV == nil {
			snap.KV = map[string]int{}
		}
		snaps = append(snaps, snap)
	}
	sort.Slice(snaps, func(i, j int) bool {
		if snaps[i].Index != snaps[j].Index {
			return snaps[i].Index > snaps[j].Index
		}
		if snaps[i].Term != snaps[j].Term {
			return snaps[i].Term > snaps[j].Term
		}
		return snaps[i].ID < snaps[j].ID
	})
	return snaps[0], nil
}
GO

cat > /app/internal/recovery/segments.go <<'GO'
package recovery

import (
	"encoding/json"
	"fmt"
	"hash/crc32"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type rawRecord struct {
	CRC     string          `json:"crc"`
	Payload json.RawMessage `json:"payload"`
}

func readSegments(dir string) ([]Batch, int, error) {
	paths, err := filepath.Glob(filepath.Join(dir, "segments", "*.rlog"))
	if err != nil {
		return nil, 0, err
	}
	sort.Strings(paths)
	var batches []Batch
	ignored := 0
	for _, p := range paths {
		b, bad, err := readSegment(p)
		if err != nil {
			return nil, 0, err
		}
		batches = append(batches, b...)
		ignored += bad
	}
	return batches, ignored, nil
}

func readSegment(path string) ([]Batch, int, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, 0, err
	}
	type segmentLine struct {
		text       string
		incomplete bool
	}
	incompleteLast := len(data) > 0 && data[len(data)-1] != '\n'
	rawLines := strings.Split(string(data), "\n")
	if len(rawLines) > 0 && rawLines[len(rawLines)-1] == "" {
		rawLines = rawLines[:len(rawLines)-1]
	}
	var lines []segmentLine
	for i, raw := range rawLines {
		line := strings.TrimSpace(raw)
		if line != "" {
			lines = append(lines, segmentLine{text: line, incomplete: incompleteLast && i == len(rawLines)-1})
		}
	}
	var out []Batch
	for i, line := range lines {
		if line.incomplete {
			return out, len(lines) - i, nil
		}
		var rec rawRecord
		if err := json.Unmarshal([]byte(line.text), &rec); err != nil {
			return out, len(lines) - i, nil
		}
		var raw any
		dec := json.NewDecoder(strings.NewReader(string(rec.Payload)))
		dec.UseNumber()
		if err := dec.Decode(&raw); err != nil {
			return out, len(lines) - i, nil
		}
		payload, err := canonicalJSON(raw)
		if err != nil {
			return out, len(lines) - i, nil
		}
		want := fmt.Sprintf("%08x", crc32.ChecksumIEEE(payload))
		if want != rec.CRC {
			return out, len(lines) - i, nil
		}
		var batch Batch
		if err := json.Unmarshal(rec.Payload, &batch); err != nil {
			return out, len(lines) - i, nil
		}
		out = append(out, batch)
	}
	return out, 0, nil
}
GO

cat > /app/internal/recovery/recover.go <<'GO'
package recovery

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

type engine struct {
	snap        Snapshot
	kv          map[string]int
	log         map[int]Entry
	lastApplied int
	applied     []int
}

func Run(caseDir, outDir string) error {
	snap, err := loadSnapshot(caseDir)
	if err != nil {
		return err
	}
	batches, ignored, err := readSegments(caseDir)
	if err != nil {
		return err
	}
	e := &engine{snap: snap, kv: copyMap(snap.KV), log: map[int]Entry{}, lastApplied: snap.Index}
	for _, b := range batches {
		e.consume(b)
	}
	lastTerm := snap.Term
	if len(e.log) > 0 {
		keys := make([]int, 0, len(e.log))
		for k := range e.log {
			keys = append(keys, k)
		}
		sort.Ints(keys)
		lastTerm = e.log[keys[len(keys)-1]].Term
	}
	out := Output{SnapshotID: snap.ID, LastApplied: e.lastApplied, LastTerm: lastTerm, KV: e.kv, Applied: e.applied, IgnoredRecords: ignored}
	out.StateDigest = digestState(out.LastApplied, out.LastTerm, out.KV)
	if err := os.MkdirAll(outDir, 0755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return err
	}
	b = append(b, '\n')
	return os.WriteFile(filepath.Join(outDir, "state.json"), b, 0644)
}

func (e *engine) consume(b Batch) {
	if term, ok := e.termAt(b.PrevIndex); !ok || term != b.PrevTerm {
		return
	}
	for _, ent := range b.Entries {
		if ent.Index <= e.snap.Index {
			continue
		}
		if old, ok := e.log[ent.Index]; ok && sameEntry(old, ent) {
			continue
		}
		for idx := range e.log {
			if idx >= ent.Index {
				delete(e.log, idx)
			}
		}
		e.log[ent.Index] = ent
	}
	limit := b.LeaderCommit
	last := e.lastIndex()
	if limit > last {
		limit = last
	}
	for i := e.lastApplied + 1; i <= limit; i++ {
		ent, ok := e.log[i]
		if !ok {
			break
		}
		e.apply(ent)
	}
}

func (e *engine) termAt(index int) (int, bool) {
	if index == e.snap.Index {
		return e.snap.Term, true
	}
	ent, ok := e.log[index]
	if !ok {
		return 0, false
	}
	return ent.Term, true
}

func (e *engine) lastIndex() int {
	last := e.snap.Index
	for idx := range e.log {
		if idx > last {
			last = idx
		}
	}
	return last
}

func (e *engine) apply(ent Entry) {
	applyOp(e.kv, ent)
	e.lastApplied = ent.Index
	e.applied = append(e.applied, ent.Index)
}

func applyOp(kv map[string]int, ent Entry) {
	switch ent.Op {
	case "set":
		kv[ent.Key] = ent.Value
	case "incr":
		kv[ent.Key] += ent.Delta
	case "del":
		delete(kv, ent.Key)
	case "cas":
		if kv[ent.Key] == ent.Expect {
			kv[ent.Key] = ent.Value
		}
	case "batch":
		for _, child := range ent.Ops {
			applyOp(kv, child)
		}
	}
}

func sameEntry(a, b Entry) bool {
	ab, err := canonicalJSON(a)
	if err != nil {
		return false
	}
	bb, err := canonicalJSON(b)
	if err != nil {
		return false
	}
	return string(ab) == string(bb)
}

func copyMap(in map[string]int) map[string]int {
	out := make(map[string]int, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

func digestState(lastApplied, lastTerm int, kv map[string]int) string {
	b, err := canonicalJSON(map[string]any{"last_applied": lastApplied, "last_term": lastTerm, "kv": kv})
	if err != nil {
		panic(fmt.Sprintf("digest: %v", err))
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
GO

bash /app/scripts/build.sh
