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
		e.log[ent.Index] = ent
	}
	limit := b.LeaderCommit
	for i := e.lastApplied + 1; i <= limit; i++ {
		ent, ok := e.log[i]
		if !ok {
			break
		}
		e.apply(ent)
	}
}

func (e *engine) termAt(index int) (int, bool) {
	ent, ok := e.log[index]
	if !ok {
		return 0, false
	}
	return ent.Term, true
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
