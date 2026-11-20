#!/bin/bash
set -euo pipefail

cat > /app/ctgate/cmd/ctgate/main.go <<'GO'
package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type Trust struct{ Logs []TrustedLog `json:"logs"` }
type TrustedLog struct {
	LogID               string   `json:"log_id"`
	Key                 string   `json:"key"`
	ValidFrom           int64    `json:"valid_from"`
	ValidUntil          int64    `json:"valid_until"`
	AllowedIssuers      []string `json:"allowed_issuers"`
	MaxAgeSeconds       int64    `json:"max_age_seconds"`
	RevokedSerials      []string `json:"revoked_serials"`
	RequiredWitnessLogs []string `json:"required_witness_logs"`
	WitnessQuorum       WitnessQuorum `json:"witness_quorum"`
	CheckpointKey       string   `json:"checkpoint_key"`
	RevocationKey       string   `json:"revocation_key"`
}
type WitnessQuorum struct {
	Logs      []string `json:"logs"`
	Threshold int      `json:"threshold"`
}
type Bundle struct {
	Entries     []Entry       `json:"entries"`
	Proofs      []Proof       `json:"proofs"`
	STHs        []STH         `json:"sths"`
	Consistency []Consistency `json:"consistency"`
	Pins        []Pin         `json:"pins"`
	Witnesses   []Proof       `json:"witnesses"`
	Checkpoints []Checkpoint  `json:"checkpoints"`
	Revocations []Revocation  `json:"revocations"`
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
type Pin struct {
	LogID     string `json:"log_id"`
	TreeSize  int    `json:"tree_size"`
	Timestamp int64  `json:"timestamp"`
	RootHash  string `json:"root_hash"`
}
type Checkpoint struct {
	LogID     string `json:"log_id"`
	TreeSize  int    `json:"tree_size"`
	Timestamp int64  `json:"timestamp"`
	RootHash  string `json:"root_hash"`
	Signature string `json:"signature"`
}
type Revocation struct {
	Issuer    string `json:"issuer"`
	Serial    string `json:"serial"`
	Status    string `json:"status"`
	Timestamp int64  `json:"timestamp"`
	Signature string `json:"signature"`
	Order     int    `json:"-"`
}
type Consistency struct {
	LogID      string   `json:"log_id"`
	FromSize   int      `json:"from_size"`
	ToSize     int      `json:"to_size"`
	LeafHashes []string `json:"leaf_hashes"`
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
	rep, err := run(*trustPath, *bundlePath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	data, err := json.MarshalIndent(rep, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
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

func run(trustPath, bundlePath string) (Report, error) {
	var trust Trust
	var bundle Bundle
	if err := loadJSON(trustPath, &trust); err != nil {
		if errors.Is(err, errBadJSON) { return malformed("malformed-trust"), nil }
		return emptyReport(), err
	}
	if err := loadJSON(bundlePath, &bundle); err != nil {
		if errors.Is(err, errBadJSON) { return malformed("malformed-bundle"), nil }
		return emptyReport(), err
	}
	keys := map[string][]byte{}
	windows := map[string][2]int64{}
	policies := map[string]TrustedLog{}
	checkpointKeys := map[string][]byte{}
	revocationKeys := map[string][]byte{}
	for _, l := range trust.Logs {
		key, err := hex.DecodeString(l.Key)
		if err == nil && l.LogID != "" && l.ValidUntil >= l.ValidFrom {
			keys[l.LogID] = key
			windows[l.LogID] = [2]int64{l.ValidFrom, l.ValidUntil}
			policies[l.LogID] = l
			if l.CheckpointKey != "" {
				if cpKey, err := hex.DecodeString(l.CheckpointKey); err == nil && len(cpKey) > 0 {
					checkpointKeys[l.LogID] = cpKey
				}
			}
			if l.RevocationKey != "" {
				if rvKey, err := hex.DecodeString(l.RevocationKey); err == nil && len(rvKey) > 0 {
					revocationKeys[l.LogID] = rvKey
				}
			}
		}
	}
	for i := range bundle.Revocations { bundle.Revocations[i].Order = i }
	validSTH := map[string][]STH{}
	for i, s := range bundle.STHs {
		s.Order = i
		key := keys[s.LogID]
		win := windows[s.LogID]
		if len(key) == 0 || s.TreeSize <= 0 || s.Timestamp < win[0] || s.Timestamp > win[1] || !validHex32(s.RootHash) || !goodSig(key, s) {
			continue
		}
		validSTH[s.LogID] = append(validSTH[s.LogID], s)
	}
	timeline, badLogs := assessTimelines(validSTH, bundle.Consistency, bundle.Pins, keys)
	accepted := []Accepted{}
	rejected := []Rejected{}
	seen := map[string]Entry{}
	dups := map[string]bool{}
	for _, e := range bundle.Entries {
		if e.ID == "" || e.LogID == "" || e.LeafIndex < 0 {
			rejected = append(rejected, Rejected{ID: e.ID, Reason: "malformed-entry"})
			continue
		}
		if _, ok := decodeEvidenceObject(e.Evidence); !ok {
			rejected = append(rejected, Rejected{ID: e.ID, Reason: "malformed-entry"})
			continue
		}
		if _, ok := seen[e.ID]; ok {
			dups[e.ID] = true
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
		if !policyOK(e.Evidence, policies[e.LogID]) {
			rejected = append(rejected, Rejected{ID: id, Reason: "policy-violation"})
			continue
		}
		acc, reason, ok := admit(e, bundle.Proofs, bundle.Witnesses, bundle.Checkpoints, bundle.Revocations, validSTH, badLogs, keys, checkpointKeys, revocationKeys, policies[e.LogID])
		if ok {
			accepted = append(accepted, acc)
		} else {
			rejected = append(rejected, Rejected{ID: id, Reason: reason})
		}
	}
	sort.Slice(accepted, func(i, j int) bool { return accepted[i].ID < accepted[j].ID })
	sort.Slice(rejected, func(i, j int) bool {
		if rejected[i].ID == rejected[j].ID { return rejected[i].Reason < rejected[j].Reason }
		return rejected[i].ID < rejected[j].ID
	})
	mal := 0
	for _, r := range rejected { if strings.HasPrefix(r.Reason, "malformed") { mal++ } }
	return Report{Accepted: accepted, Rejected: rejected, Summary: Summary{Accepted:len(accepted), Rejected:len(rejected), Malformed:mal, Logs:len(timeline)}, Timeline: timeline}, nil
}

func policyOK(raw json.RawMessage, policy TrustedLog) bool {
	obj, ok := decodeEvidenceObject(raw)
	if !ok {
		return false
	}
	if len(policy.AllowedIssuers) > 0 {
		issuer, ok := issuerName(obj["issuer"])
		if !ok || !contains(policy.AllowedIssuers, issuer) {
			return false
		}
	}
	if policy.MaxAgeSeconds > 0 {
		nb, ok1 := numberInt64(obj["not_before"])
		na, ok2 := numberInt64(obj["not_after"])
		if !ok1 || !ok2 || na < nb || na-nb > policy.MaxAgeSeconds {
			return false
		}
	}
	if len(policy.RevokedSerials) > 0 {
		serial, ok := obj["serial"].(string)
		if ok && contains(policy.RevokedSerials, serial) {
			return false
		}
	}
	return true
}

func issuerName(v any) (string, bool) {
	if s, ok := v.(string); ok {
		return s, true
	}
	if obj, ok := v.(map[string]any); ok {
		s, ok := obj["name"].(string)
		return s, ok
	}
	return "", false
}

func numberInt64(v any) (int64, bool) {
	switch n := v.(type) {
	case json.Number:
		i, err := n.Int64()
		return i, err == nil
	case float64:
		i := int64(n)
		return i, float64(i) == n
	default:
		return 0, false
	}
}

func contains(items []string, needle string) bool {
	for _, item := range items {
		if item == needle {
			return true
		}
	}
	return false
}

var errBadJSON = errors.New("bad json")
func emptyReport() Report { return Report{Accepted: []Accepted{}, Rejected: []Rejected{}, Timeline: []Timeline{}} }
func loadJSON(path string, v any) error {
	b, err := os.ReadFile(path)
	if err != nil { return err }
	if err := json.Unmarshal(b, v); err != nil { return errBadJSON }
	return nil
}
func malformed(reason string) Report {
	return Report{Accepted: []Accepted{}, Rejected: []Rejected{{ID:"", Reason:reason}}, Summary: Summary{Rejected:1, Malformed:1}, Timeline: []Timeline{}}
}
func validHex32(s string) bool { b, err := hex.DecodeString(s); return err == nil && len(b) == 32 }
func goodSig(key []byte, s STH) bool {
	msg := fmt.Sprintf("ctgate-sth-v1\n%s\n%d\n%s\n%d", s.LogID, s.TreeSize, s.RootHash, s.Timestamp)
	mac := hmac.New(sha256.New, key); mac.Write([]byte(msg)); want := mac.Sum(nil)
	got, err := hex.DecodeString(s.Signature); return err == nil && hmac.Equal(got, want)
}

func assessTimelines(sths map[string][]STH, cons []Consistency, pins []Pin, keys map[string][]byte) ([]Timeline, map[string]bool) {
	bad := map[string]bool{}
	out := []Timeline{}
	pinsByLog := map[string][]Pin{}
	for _, p := range pins {
		if len(keys[p.LogID]) == 0 || p.TreeSize <= 0 || !validHex32(p.RootHash) {
			continue
		}
		pinsByLog[p.LogID] = append(pinsByLog[p.LogID], p)
	}
	for log, arr := range sths {
		sort.SliceStable(arr, func(i,j int) bool { if arr[i].Timestamp == arr[j].Timestamp { return arr[i].Order < arr[j].Order }; return arr[i].Timestamp < arr[j].Timestamp })
		roots := map[int]string{}
		latestSize, latestTS := 0, int64(0)
		prevSet := false
		var prev STH
		for _, s := range arr {
			if r, ok := roots[s.TreeSize]; ok && r != s.RootHash { bad[log] = true }
			roots[s.TreeSize] = s.RootHash
			if prevSet {
				if s.TreeSize < prev.TreeSize { bad[log] = true }
				if s.TreeSize > prev.TreeSize && !hasConsistency(log, prev, s, cons) { bad[log] = true }
			}
			prev, prevSet = s, true
			latestSize, latestTS = s.TreeSize, s.Timestamp
		}
		for _, p := range pinsByLog[log] {
			matched := false
			for _, s := range arr {
				if s.TreeSize == p.TreeSize && s.Timestamp == p.Timestamp && s.RootHash == p.RootHash {
					matched = true
					break
				}
			}
			if !matched { bad[log] = true }
		}
		status := "ok"; if bad[log] { status = "bad" }
		out = append(out, Timeline{LogID:log, Status:status, STHCount:len(arr), LatestSize:latestSize, LatestTimestamp:latestTS})
	}
	sort.Slice(out, func(i,j int) bool { return out[i].LogID < out[j].LogID })
	return out, bad
}

func hasConsistency(log string, old, neu STH, cons []Consistency) bool {
	for _, c := range cons {
		if c.LogID != log || c.FromSize != old.TreeSize || c.ToSize != neu.TreeSize || c.FromSize <= 0 || c.ToSize < c.FromSize || len(c.LeafHashes) != c.ToSize { continue }
		hashes := [][]byte{}
		ok := true
		for _, h := range c.LeafHashes { b, err := hex.DecodeString(h); if err != nil || len(b) != 32 { ok = false; break }; hashes = append(hashes, b) }
		if !ok { continue }
		oldRoot := hex.EncodeToString(treeRoot(hashes[:c.FromSize])); newRoot := hex.EncodeToString(treeRoot(hashes[:c.ToSize]))
		if oldRoot == old.RootHash && newRoot == neu.RootHash { return true }
	}
	return false
}

func admit(e Entry, proofs, witnesses []Proof, checkpoints []Checkpoint, revocations []Revocation, sths map[string][]STH, badLogs map[string]bool, keys, checkpointKeys, revocationKeys map[string][]byte, policy TrustedLog) (Accepted, string, bool) {
	le, ok := leafHash(e.Evidence)
	if !ok { return Accepted{}, "missing-valid-proof", false }
	var best Accepted
	var bestSTH STH
	found := false
	for _, p := range proofs {
		if p.LeafID != e.ID || p.LogID != e.LogID || p.LeafIndex != e.LeafIndex { continue }
		sth, ok := proofSTH(le, p, sths[p.LogID]); if !ok { continue }
		cand := Accepted{ID:e.ID, LogID:e.LogID, TreeSize:sth.TreeSize, STHTimestamp:sth.Timestamp}
		if !found || cand.TreeSize < best.TreeSize || (cand.TreeSize == best.TreeSize && cand.STHTimestamp < best.STHTimestamp) { best, bestSTH, found = cand, sth, true }
	}
	if !found { return Accepted{}, "missing-valid-proof", false }
	if !checkpointOK(bestSTH, checkpoints, checkpointKeys[e.LogID]) { return Accepted{}, "missing-valid-proof", false }
	validWitnessLogs := map[string]bool{}
	for _, w := range witnesses {
		if w.LeafID != e.ID { continue }
		if w.LogID == e.LogID || len(keys[w.LogID]) == 0 { return Accepted{}, "missing-valid-proof", false }
		if badLogs[w.LogID] { return Accepted{}, "bad-timeline", false }
		wsth, ok := proofSTH(le, w, sths[w.LogID]); if !ok { return Accepted{}, "missing-valid-proof", false }
		if !checkpointOK(wsth, checkpoints, checkpointKeys[w.LogID]) { return Accepted{}, "missing-valid-proof", false }
		validWitnessLogs[w.LogID] = true
	}
	for _, logID := range policy.RequiredWitnessLogs {
		if logID == e.LogID || len(keys[logID]) == 0 { return Accepted{}, "missing-valid-proof", false }
		if badLogs[logID] { return Accepted{}, "bad-timeline", false }
		if !validWitnessLogs[logID] { return Accepted{}, "missing-valid-proof", false }
	}
	if policy.WitnessQuorum.Threshold > 0 && len(policy.WitnessQuorum.Logs) > 0 {
		allowed := map[string]bool{}
		badNeeded := false
		for _, logID := range policy.WitnessQuorum.Logs {
			if logID == e.LogID || len(keys[logID]) == 0 {
				continue
			}
			allowed[logID] = true
			if badLogs[logID] {
				badNeeded = true
			}
		}
		count := 0
		for logID := range validWitnessLogs {
			if allowed[logID] {
				count++
			}
		}
		if count < policy.WitnessQuorum.Threshold {
			if badNeeded { return Accepted{}, "bad-timeline", false }
			return Accepted{}, "missing-valid-proof", false
		}
	}
	if !revocationOK(e.Evidence, bestSTH, revocations, revocationKeys[e.LogID]) { return Accepted{}, "policy-violation", false }
	return best, "", true
}

func revocationOK(raw json.RawMessage, sth STH, revocations []Revocation, key []byte) bool {
	if len(key) == 0 { return true }
	obj, ok := decodeEvidenceObject(raw); if !ok { return false }
	issuer, okIssuer := issuerName(obj["issuer"])
	serial, okSerial := obj["serial"].(string)
	if !okIssuer || !okSerial { return true }
	found := false
	latestTS := int64(0)
	latestOrder := -1
	latestStatus := ""
	for _, r := range revocations {
		if r.Issuer != issuer || r.Serial != serial || r.Timestamp > sth.Timestamp || (r.Status != "revoked" && r.Status != "reissued") { continue }
		msg := fmt.Sprintf("ctgate-revocation-v1\n%s\n%s\n%s\n%d", r.Issuer, r.Serial, r.Status, r.Timestamp)
		mac := hmac.New(sha256.New, key); mac.Write([]byte(msg)); want := mac.Sum(nil)
		got, err := hex.DecodeString(r.Signature)
		if err != nil || !hmac.Equal(got, want) { continue }
		if !found || r.Timestamp > latestTS || (r.Timestamp == latestTS && r.Order > latestOrder) {
			found, latestTS, latestOrder, latestStatus = true, r.Timestamp, r.Order, r.Status
		}
	}
	return !found || latestStatus != "revoked"
}

func checkpointOK(sth STH, checkpoints []Checkpoint, key []byte) bool {
	if len(key) == 0 { return true }
	for _, cp := range checkpoints {
		if cp.LogID != sth.LogID || cp.TreeSize != sth.TreeSize || cp.RootHash != sth.RootHash || cp.Timestamp < sth.Timestamp || !validHex32(cp.RootHash) {
			continue
		}
		msg := fmt.Sprintf("ctgate-checkpoint-v1\n%s\n%d\n%s\n%d", cp.LogID, cp.TreeSize, cp.RootHash, cp.Timestamp)
		mac := hmac.New(sha256.New, key); mac.Write([]byte(msg)); want := mac.Sum(nil)
		got, err := hex.DecodeString(cp.Signature)
		if err == nil && hmac.Equal(got, want) { return true }
	}
	return false
}

func proofSTH(leaf []byte, p Proof, sths []STH) (STH, bool) {
	if p.TreeSize <= p.LeafIndex || p.TreeSize <= 0 { return STH{}, false }
	root, ok := inclusionRoot(leaf, p.LeafIndex, p.TreeSize, p.AuditPath); if !ok { return STH{}, false }
	rootHex := hex.EncodeToString(root)
	var best STH
	found := false
	for _, s := range sths {
		if s.TreeSize == p.TreeSize && s.RootHash == rootHex {
			if !found || s.Timestamp < best.Timestamp { best, found = s, true }
		}
	}
	return best, found
}

func leafHash(raw json.RawMessage) ([]byte, bool) {
	v, ok := decodeEvidenceObject(raw)
	if !ok { return nil, false }
	canon, err := canonicalJSON(v)
	if err != nil { return nil, false }
	h := sha256.New(); h.Write([]byte{0}); h.Write(canon); return h.Sum(nil), true
}
func decodeEvidenceObject(raw json.RawMessage) (map[string]any, bool) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var obj map[string]any
	if err := dec.Decode(&obj); err != nil || obj == nil { return nil, false }
	var extra any
	if err := dec.Decode(&extra); err != io.EOF { return nil, false }
	return obj, true
}
func canonicalJSON(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil { return nil, err }
	return bytes.TrimSuffix(buf.Bytes(), []byte("\n")), nil
}
func nodeHash(l,r []byte) []byte { h := sha256.New(); h.Write([]byte{1}); h.Write(l); h.Write(r); return h.Sum(nil) }
func splitPoint(n int) int { k := 1; for k*2 < n { k *= 2 }; return k }
func treeRoot(leaves [][]byte) []byte {
	if len(leaves) == 0 { h := sha256.Sum256(nil); return h[:] }
	if len(leaves) == 1 { return leaves[0] }
	k := splitPoint(len(leaves))
	return nodeHash(treeRoot(leaves[:k]), treeRoot(leaves[k:]))
}
func inclusionRoot(leaf []byte, index, size int, path []string) ([]byte, bool) {
	if index < 0 || index >= size { return nil, false }
	pos := 0
	root, ok := inclusionRec(leaf, index, size, path, &pos)
	if !ok || pos != len(path) { return nil, false }
	return root, true
}
func inclusionRec(leaf []byte, index, size int, path []string, pos *int) ([]byte, bool) {
	if size == 1 { return leaf, true }
	k := splitPoint(size)
	if index < k {
		left, ok := inclusionRec(leaf, index, k, path, pos); if !ok || *pos >= len(path) { return nil, false }
		right, err := hex.DecodeString(path[*pos]); (*pos)++
		if err != nil || len(right) != 32 { return nil, false }
		return nodeHash(left, right), true
	}
	right, ok := inclusionRec(leaf, index-k, size-k, path, pos); if !ok || *pos >= len(path) { return nil, false }
	left, err := hex.DecodeString(path[*pos]); (*pos)++
	if err != nil || len(left) != 32 { return nil, false }
	return nodeHash(left, right), true
}
GO

gofmt -w /app/ctgate/cmd/ctgate/main.go
/app/ctgate/scripts/build.sh
