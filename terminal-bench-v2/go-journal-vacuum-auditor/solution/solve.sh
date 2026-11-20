#!/bin/bash
set -euo pipefail

cat > /app/jvac/internal/audit/audit.go <<'GO'
package audit

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

type Policy struct {
	RetainBoots           int   `json:"retain_boots"`
	RetainRealtimeAfterUS int64 `json:"retain_realtime_after_us"`
	MaxBytes              int   `json:"max_bytes"`
	MaxRotatedBytes       int   `json:"max_rotated_bytes"`
}

type Record struct {
	Cursor, BootID, Unit, Priority, Message string
	Monotonic, Realtime                     int64
	RecordBytes, Index, Seq                 int
	Segment                                 string
}

type Segment struct {
	Path    string
	Seq     int
	Rotated bool
	Bytes   int
	Records []Record
}

type Summary struct {
	Boots               []string `json:"boots"`
	CommittedRecords    int      `json:"committed_records"`
	IgnoredTailRecords  int      `json:"ignored_tail_records"`
	InvalidSegments     int      `json:"invalid_segments"`
	OverBudget          bool     `json:"over_budget"`
	ScannedSegments     int      `json:"scanned_segments"`
	TotalBytesAfterPlan int      `json:"total_bytes_after_plan"`
}

func Run(caseDir, outDir string) error {
	policy, err := loadPolicy(filepath.Join(caseDir, "policy.json"))
	if err != nil {
		return err
	}
	segments, ignored, invalid, scanned, err := loadSegments(caseDir)
	if err != nil {
		return err
	}
	records := collectRecords(segments)
	boots, bootRank := orderBoots(records)
	sortRecords(records, bootRank)
	if err := prepareOutput(outDir); err != nil {
		return err
	}
	if err := writeTimeline(filepath.Join(outDir, "timeline.tsv"), records); err != nil {
		return err
	}
	decisions, totalAfterPlan, overBudget := planVacuum(segments, records, bootRank, boots, policy)
	if err := writeVacuum(filepath.Join(outDir, "vacuum.tsv"), decisions); err != nil {
		return err
	}
	summary := Summary{Boots: boots, CommittedRecords: len(records), IgnoredTailRecords: ignored, InvalidSegments: invalid, OverBudget: overBudget, ScannedSegments: scanned, TotalBytesAfterPlan: totalAfterPlan}
	data, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(outDir, "summary.json"), append(data, '\n'), 0o644)
}

func loadPolicy(path string) (Policy, error) {
	var p Policy
	data, err := os.ReadFile(path)
	if err != nil {
		return p, err
	}
	if err := json.Unmarshal(data, &p); err != nil {
		return p, err
	}
	return p, nil
}

func prepareOutput(outDir string) error {
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return err
	}
	entries, err := os.ReadDir(outDir)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if err := os.RemoveAll(filepath.Join(outDir, entry.Name())); err != nil {
			return err
		}
	}
	return nil
}

func loadSegments(caseDir string) ([]Segment, int, int, int, error) {
	files, err := filepath.Glob(filepath.Join(caseDir, "segments", "*.jseg"))
	if err != nil {
		return nil, 0, 0, 0, err
	}
	sort.Strings(files)
	segments := make([]Segment, 0, len(files))
	ignored := 0
	invalid := 0
	for _, file := range files {
		seg, tail, ok := parseSegment(caseDir, file)
		ignored += tail
		if !ok {
			invalid++
			continue
		}
		segments = append(segments, seg)
	}
	return segments, ignored, invalid, len(files), nil
}

func parseSegment(caseDir, file string) (Segment, int, bool) {
	data, err := os.ReadFile(file)
	if err != nil {
		return Segment{}, 0, false
	}
	lineEnd := bytes.IndexByte(data, '\n')
	if lineEnd < 0 {
		return Segment{}, 0, false
	}
	header := strings.Split(string(data[:lineEnd]), "\t")
	seg, ok := parseHeader(header)
	if !ok {
		return Segment{}, 0, false
	}
	seg.Path = rel(caseDir, file)
	ignored := 0
	remaining := data[lineEnd+1:]
	index := 0
	for len(remaining) > 0 {
		next := bytes.IndexByte(remaining, '\n')
		if next < 0 {
			ignored++
			break
		}
		line := string(remaining[:next])
		remaining = remaining[next+1:]
		rec, ok := parseRecord(line, seg, index)
		if !ok {
			ignored++
			break
		}
		seg.Records = append(seg.Records, rec)
		index++
	}
	return seg, ignored, true
}

func parseHeader(parts []string) (Segment, bool) {
	if len(parts) != 5 || parts[0] != "JSEG" {
		return Segment{}, false
	}
	attrs := map[string]string{}
	for _, part := range parts[1:] {
		kv := strings.SplitN(part, "=", 2)
		if len(kv) != 2 || kv[0] == "" {
			return Segment{}, false
		}
		attrs[kv[0]] = kv[1]
	}
	if _, ok := attrs["name"]; !ok || len(attrs) != 4 {
		return Segment{}, false
	}
	seq, errSeq := strconv.Atoi(attrs["seq"])
	size, errSize := strconv.Atoi(attrs["bytes"])
	if errSeq != nil || errSize != nil || seq < 0 || size <= 0 {
		return Segment{}, false
	}
	rotatedValue := attrs["rotated"]
	if rotatedValue != "true" && rotatedValue != "false" {
		return Segment{}, false
	}
	return Segment{Seq: seq, Bytes: size, Rotated: rotatedValue == "true"}, true
}

func parseRecord(line string, seg Segment, index int) (Record, bool) {
	fields := strings.Split(line, "\t")
	if len(fields) != 9 || fields[0] != "R" || fields[1] == "" || fields[2] == "" {
		return Record{}, false
	}
	mono, errMono := strconv.ParseInt(fields[3], 10, 64)
	real, errReal := strconv.ParseInt(fields[4], 10, 64)
	recordBytes, errBytes := strconv.Atoi(fields[8])
	if errMono != nil || errReal != nil || errBytes != nil || recordBytes < 0 {
		return Record{}, false
	}
	return Record{Cursor: fields[1], BootID: fields[2], Monotonic: mono, Realtime: real, Unit: fields[5], Priority: fields[6], Message: fields[7], RecordBytes: recordBytes, Index: index, Seq: seg.Seq, Segment: seg.Path}, true
}

func collectRecords(segments []Segment) []Record {
	var records []Record
	for _, segment := range segments {
		records = append(records, segment.Records...)
	}
	return records
}

func orderBoots(records []Record) ([]string, map[string]int) {
	first := map[string]int64{}
	for _, rec := range records {
		old, ok := first[rec.BootID]
		if !ok || rec.Realtime < old {
			first[rec.BootID] = rec.Realtime
		}
	}
	boots := make([]string, 0, len(first))
	for boot := range first {
		boots = append(boots, boot)
	}
	sort.Slice(boots, func(i, j int) bool {
		if first[boots[i]] != first[boots[j]] {
			return first[boots[i]] < first[boots[j]]
		}
		return boots[i] < boots[j]
	})
	rank := map[string]int{}
	for i, boot := range boots {
		rank[boot] = i
	}
	return boots, rank
}

func sortRecords(records []Record, bootRank map[string]int) {
	sort.Slice(records, func(i, j int) bool { return recordLess(records[i], records[j], bootRank) })
}

func recordLess(a, b Record, bootRank map[string]int) bool {
	if bootRank[a.BootID] != bootRank[b.BootID] {
		return bootRank[a.BootID] < bootRank[b.BootID]
	}
	if a.Monotonic != b.Monotonic {
		return a.Monotonic < b.Monotonic
	}
	if a.Realtime != b.Realtime {
		return a.Realtime < b.Realtime
	}
	if a.Seq != b.Seq {
		return a.Seq < b.Seq
	}
	if a.Segment != b.Segment {
		return a.Segment < b.Segment
	}
	return a.Index < b.Index
}

type Decision struct {
	Segment Segment
	Action  string
	Reason  string
	Oldest  string
	Newest  string
}

type candidate struct {
	newestPos int
	seq       int
	path      string
	decision  *Decision
}

func planVacuum(segments []Segment, ordered []Record, bootRank map[string]int, boots []string, policy Policy) ([]Decision, int, bool) {
	position := map[string]int{}
	for i, rec := range ordered {
		position[recordID(rec)] = i
	}
	protectedBoots := map[string]bool{}
	if policy.RetainBoots > 0 {
		start := len(boots) - policy.RetainBoots
		if start < 0 {
			start = 0
		}
		for _, boot := range boots[start:] {
			protectedBoots[boot] = true
		}
	}
	decisions := make([]Decision, 0, len(segments))
	candidates := []candidate{}
	keptTotal := 0
	keptRotated := 0
	for _, segment := range segments {
		keptTotal += segment.Bytes
		if segment.Rotated {
			keptRotated += segment.Bytes
		}
		segRecords := append([]Record(nil), segment.Records...)
		sortRecords(segRecords, bootRank)
		oldest, newest := "", ""
		newestPos := -1
		protected := false
		for i, rec := range segRecords {
			if i == 0 {
				oldest = rec.Cursor
			}
			newest = rec.Cursor
			if pos := position[recordID(rec)]; pos > newestPos {
				newestPos = pos
			}
			if protectedBoots[rec.BootID] || rec.Realtime >= policy.RetainRealtimeAfterUS {
				protected = true
			}
		}
		reason := "budget-spare"
		if !segment.Rotated {
			reason = "active"
		} else if protected {
			reason = "retention-window"
		}
		decisions = append(decisions, Decision{Segment: segment, Action: "keep", Reason: reason, Oldest: oldest, Newest: newest})
		if segment.Rotated && reason == "budget-spare" {
			candidates = append(candidates, candidate{newestPos: newestPos, seq: segment.Seq, path: segment.Path, decision: &decisions[len(decisions)-1]})
		}
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].newestPos != candidates[j].newestPos {
			return candidates[i].newestPos < candidates[j].newestPos
		}
		if candidates[i].seq != candidates[j].seq {
			return candidates[i].seq < candidates[j].seq
		}
		return candidates[i].path < candidates[j].path
	})
	for _, cand := range candidates {
		if keptTotal <= policy.MaxBytes && keptRotated <= policy.MaxRotatedBytes {
			break
		}
		cand.decision.Action = "delete"
		cand.decision.Reason = "budget"
		keptTotal -= cand.decision.Segment.Bytes
		keptRotated -= cand.decision.Segment.Bytes
	}
	over := keptTotal > policy.MaxBytes || keptRotated > policy.MaxRotatedBytes
	sort.Slice(decisions, func(i, j int) bool { return decisions[i].Segment.Path < decisions[j].Segment.Path })
	return decisions, keptTotal, over
}

func recordID(rec Record) string {
	return rec.Segment + "\x00" + strconv.Itoa(rec.Index)
}

func writeTimeline(path string, records []Record) error {
	var b strings.Builder
	b.WriteString("rank\tboot_id\tcursor\trealtime_us\tmonotonic_us\tsegment\trecord_index\tunit\tpriority\tmessage\n")
	for rank, rec := range records {
		fmt.Fprintf(&b, "%d\t%s\t%s\t%d\t%d\t%s\t%d\t%s\t%s\t%s\n", rank, rec.BootID, rec.Cursor, rec.Realtime, rec.Monotonic, rec.Segment, rec.Index, rec.Unit, rec.Priority, rec.Message)
	}
	return os.WriteFile(path, []byte(b.String()), 0o644)
}

func writeVacuum(path string, decisions []Decision) error {
	var b strings.Builder
	b.WriteString("segment\taction\treason\tbytes\tcommitted_records\toldest_cursor\tnewest_cursor\n")
	for _, d := range decisions {
		fmt.Fprintf(&b, "%s\t%s\t%s\t%d\t%d\t%s\t%s\n", d.Segment.Path, d.Action, d.Reason, d.Segment.Bytes, len(d.Segment.Records), d.Oldest, d.Newest)
	}
	return os.WriteFile(path, []byte(b.String()), 0o644)
}

func rel(root, path string) string {
	relPath, err := filepath.Rel(root, path)
	if err != nil {
		return filepath.ToSlash(path)
	}
	return filepath.ToSlash(relPath)
}
GO

gofmt -w /app/jvac/internal/audit/audit.go /app/jvac/cmd/jvac/main.go
/app/jvac/scripts/build.sh
