package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

type Trust struct {
	Logs []TrustedLog `json:"logs"`
}
type TrustedLog struct {
	LogID         string `json:"log_id"`
	Key           string `json:"key"`
	ValidFrom     int64  `json:"valid_from"`
	ValidUntil    int64  `json:"valid_until"`
	CheckpointKey string `json:"checkpoint_key"`
	WitnessQuorum struct {
		Logs      []string `json:"logs"`
		Threshold int      `json:"threshold"`
	} `json:"witness_quorum"`
}
type Bundle struct {
	Entries     []Entry       `json:"entries"`
	Proofs      []Proof       `json:"proofs"`
	STHs        []STH         `json:"sths"`
	Consistency []Consistency `json:"consistency"`
	Checkpoints []Checkpoint  `json:"checkpoints"`
}
type Entry struct {
	ID        string          `json:"id"`
	LogID     string          `json:"log_id"`
	LeafIndex int             `json:"leaf_index"`
	Evidence  json.RawMessage `json:"evidence"`
}
type Proof struct {
	LeafID    string   `json:"leaf_id"`
	LogID     string   `json:"log_id"`
	TreeSize  int      `json:"tree_size"`
	LeafIndex int      `json:"leaf_index"`
	AuditPath []string `json:"audit_path"`
}
type STH struct {
	LogID     string `json:"log_id"`
	TreeSize  int    `json:"tree_size"`
	Timestamp int64  `json:"timestamp"`
	RootHash  string `json:"root_hash"`
	Signature string `json:"signature"`
	Order     int    `json:"-"`
}
type Consistency struct {
	LogID      string   `json:"log_id"`
	FromSize   int      `json:"from_size"`
	ToSize     int      `json:"to_size"`
	LeafHashes []string `json:"leaf_hashes"`
}
type Checkpoint struct {
	LogID     string `json:"log_id"`
	TreeSize  int    `json:"tree_size"`
	Timestamp int64  `json:"timestamp"`
	RootHash  string `json:"root_hash"`
	Signature string `json:"signature"`
}
type Accepted struct {
	ID           string `json:"id"`
	LogID        string `json:"log_id"`
	TreeSize     int    `json:"tree_size"`
	STHTimestamp int64  `json:"sth_timestamp"`
}
type Rejected struct {
	ID     string `json:"id"`
	Reason string `json:"reason"`
}
type Summary struct {
	Accepted  int `json:"accepted"`
	Rejected  int `json:"rejected"`
	Malformed int `json:"malformed"`
	Logs      int `json:"logs"`
}
type Timeline struct {
	LogID           string `json:"log_id"`
	Status          string `json:"status"`
	STHCount        int    `json:"sth_count"`
	LatestSize      int    `json:"latest_size"`
	LatestTimestamp int64  `json:"latest_timestamp"`
}
type Report struct {
	Accepted []Accepted `json:"accepted"`
	Rejected []Rejected `json:"rejected"`
	Summary  Summary    `json:"summary"`
	Timeline []Timeline `json:"timeline"`
}

func main() {
	trustPath := flag.String("trust", "", "trust file")
	bundlePath := flag.String("bundle", "", "bundle file")
	outPath := flag.String("out", "", "report")
	flag.Parse()
	if *trustPath == "" || *bundlePath == "" || *outPath == "" {
		fmt.Fprintln(os.Stderr, "missing arguments")
		os.Exit(2)
	}
	rep := run(*trustPath, *bundlePath)
	data, _ := json.MarshalIndent(rep, "", "  ")
	data = append(data, '\n')
	if err := os.MkdirAll(filepath.Dir(*outPath), 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := os.WriteFile(*outPath, data, 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(trustPath, bundlePath string) Report {
	var trust Trust
	var bundle Bundle
	if err := loadJSON(trustPath, &trust); err != nil {
		return malformed("malformed-trust")
	}
	if err := loadJSON(bundlePath, &bundle); err != nil {
		return malformed("malformed-bundle")
	}
	keys := map[string][]byte{}
	windows := map[string][2]int64{}
	checkpointKeys := map[string][]byte{}
	for _, l := range trust.Logs {
		key, err := hex.DecodeString(l.Key)
		if err == nil && l.LogID != "" {
			keys[l.LogID] = key
			windows[l.LogID] = [2]int64{l.ValidFrom, l.ValidUntil}
			if l.CheckpointKey != "" {
				if cp, err := hex.DecodeString(l.CheckpointKey); err == nil {
					checkpointKeys[l.LogID] = cp
				}
			}
		}
	}
	validSTH := map[string][]STH{}
	for i, s := range bundle.STHs {
		s.Order = i
		key := keys[s.LogID]
		win := windows[s.LogID]
		if len(key) == 0 || s.TreeSize <= 0 || s.Timestamp < win[0] || s.Timestamp > win[1] || !goodSig(key, s) {
			continue
		}
		if _, err := hex.DecodeString(s.RootHash); err != nil || len(s.RootHash) != 64 {
			continue
		}
		validSTH[s.LogID] = append(validSTH[s.LogID], s)
	}
	timeline, badLogs := assessTimelines(validSTH, bundle.Consistency)
	accepted := []Accepted{}
	rejected := []Rejected{}
	seen := map[string]Entry{}
	dups := map[string]bool{}
	for _, e := range bundle.Entries {
		if e.ID == "" || e.LogID == "" || e.LeafIndex < 0 || !json.Valid(e.Evidence) {
			rejected = append(rejected, Rejected{ID: e.ID, Reason: "malformed-entry"})
			continue
		}
		if old, ok := seen[e.ID]; ok {
			dups[e.ID] = true
			seen[e.ID] = old
			continue
		}
		seen[e.ID] = e
	}
	for id, e := range seen {
		if dups[id] {
			rejected = append(rejected, Rejected{ID: id, Reason: "duplicate-entry"})
			continue
		}
		if len(keys[e.LogID]) == 0 {
			rejected = append(rejected, Rejected{ID: id, Reason: "unknown-log"})
			continue
		}
		if badLogs[e.LogID] {
			rejected = append(rejected, Rejected{ID: id, Reason: "bad-timeline"})
			continue
		}
		acc, ok := admit(e, bundle.Proofs, bundle.Checkpoints, validSTH[e.LogID], checkpointKeys[e.LogID])
		if ok {
			accepted = append(accepted, acc)
		} else {
			rejected = append(rejected, Rejected{ID: id, Reason: "missing-valid-proof"})
		}
	}
	sort.Slice(accepted, func(i, j int) bool { return accepted[i].ID < accepted[j].ID })
	sort.Slice(rejected, func(i, j int) bool {
		if rejected[i].ID == rejected[j].ID {
			return rejected[i].Reason < rejected[j].Reason
		}
		return rejected[i].ID < rejected[j].ID
	})
	mal := 0
	for _, r := range rejected {
		if len(r.Reason) >= 9 && r.Reason[:9] == "malformed" {
			mal++
		}
	}
	return Report{Accepted: accepted, Rejected: rejected, Summary: Summary{len(accepted), len(rejected), mal, len(timeline)}, Timeline: timeline}
}

func loadJSON(path string, v any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}
func malformed(reason string) Report {
	return Report{Rejected: []Rejected{{ID: "", Reason: reason}}, Summary: Summary{Rejected: 1, Malformed: 1}}
}
func goodSig(key []byte, s STH) bool {
	msg := fmt.Sprintf("ctgate-sth-v1\n%s\n%d\n%s\n%d", s.LogID, s.TreeSize, s.RootHash, s.Timestamp)
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(msg))
	want := mac.Sum(nil)
	got, err := hex.DecodeString(s.Signature)
	return err == nil && hmac.Equal(got, want)
}

func assessTimelines(sths map[string][]STH, cons []Consistency) ([]Timeline, map[string]bool) {
	bad := map[string]bool{}
	out := []Timeline{}
	for log, arr := range sths {
		sort.SliceStable(arr, func(i, j int) bool {
			if arr[i].Timestamp == arr[j].Timestamp {
				return arr[i].Order < arr[j].Order
			}
			return arr[i].Timestamp < arr[j].Timestamp
		})
		roots := map[int]string{}
		latestSize, latestTS := 0, int64(0)
		prevSet := false
		var prev STH
		for _, s := range arr {
			if r, ok := roots[s.TreeSize]; ok && r != s.RootHash {
				bad[log] = true
			}
			roots[s.TreeSize] = s.RootHash
			if prevSet {
				if s.TreeSize < prev.TreeSize {
					bad[log] = true
				}
				if s.TreeSize > prev.TreeSize && !hasConsistency(log, prev, s, cons) {
					bad[log] = true
				}
			}
			prev, prevSet = s, true
			latestSize, latestTS = s.TreeSize, s.Timestamp
		}
		status := "ok"
		if bad[log] {
			status = "bad"
		}
		out = append(out, Timeline{LogID: log, Status: status, STHCount: len(arr), LatestSize: latestSize, LatestTimestamp: latestTS})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].LogID < out[j].LogID })
	return out, bad
}

func hasConsistency(log string, old, neu STH, cons []Consistency) bool {
	for _, c := range cons {
		if c.LogID != log || c.FromSize != old.TreeSize || c.ToSize != neu.TreeSize || len(c.LeafHashes) != c.ToSize {
			continue
		}
		hashes := [][]byte{}
		ok := true
		for _, h := range c.LeafHashes {
			b, err := hex.DecodeString(h)
			if err != nil || len(b) != 32 {
				ok = false
				break
			}
			hashes = append(hashes, b)
		}
		if !ok {
			continue
		}
		oldRoot := hex.EncodeToString(treeRoot(hashes[:c.FromSize]))
		newRoot := hex.EncodeToString(treeRoot(hashes[:c.ToSize]))
		if oldRoot == old.RootHash && newRoot == neu.RootHash {
			return true
		}
	}
	return false
}

func admit(e Entry, proofs []Proof, checkpoints []Checkpoint, sths []STH, checkpointKey []byte) (Accepted, bool) {
	le := leafHash(e.Evidence)
	var best Accepted
	found := false
	for _, p := range proofs {
		if p.LeafID != e.ID || p.LogID != e.LogID || p.LeafIndex != e.LeafIndex || p.TreeSize <= p.LeafIndex {
			continue
		}
		root, ok := inclusionRoot(le, p.LeafIndex, p.TreeSize, p.AuditPath)
		if !ok {
			continue
		}
		for _, s := range sths {
			if s.TreeSize == p.TreeSize && s.RootHash == hex.EncodeToString(root) && checkpointPresent(s, checkpoints, checkpointKey) {
				cand := Accepted{ID: e.ID, LogID: e.LogID, TreeSize: s.TreeSize, STHTimestamp: s.Timestamp}
				if !found || cand.TreeSize < best.TreeSize || (cand.TreeSize == best.TreeSize && cand.STHTimestamp < best.STHTimestamp) {
					best, found = cand, true
				}
			}
		}
	}
	return best, found
}

func checkpointPresent(s STH, checkpoints []Checkpoint, key []byte) bool {
	if len(key) == 0 {
		return true
	}
	for _, cp := range checkpoints {
		if cp.LogID == s.LogID && cp.TreeSize == s.TreeSize && cp.Timestamp >= s.Timestamp {
			return true
		}
	}
	return false
}

func leafHash(raw json.RawMessage) []byte { h := sha256.New(); h.Write(raw); return h.Sum(nil) }
func nodeHash(l, r []byte) []byte {
	h := sha256.New()
	h.Write([]byte{1})
	h.Write(l)
	h.Write(r)
	return h.Sum(nil)
}
func treeRoot(leaves [][]byte) []byte {
	if len(leaves) == 0 {
		h := sha256.Sum256(nil)
		return h[:]
	}
	if len(leaves) == 1 {
		return leaves[0]
	}
	k := 1
	for k*2 < len(leaves) {
		k *= 2
	}
	return nodeHash(treeRoot(leaves[:k]), treeRoot(leaves[k:]))
}
func inclusionRoot(leaf []byte, index, size int, path []string) ([]byte, bool) {
	if index < 0 || index >= size {
		return nil, false
	}
	h := leaf
	for _, p := range path {
		b, err := hex.DecodeString(p)
		if err != nil || len(b) != 32 {
			return nil, false
		}
		h = nodeHash(h, b)
	}
	return h, true
}
