package audit

import (
	"bufio"
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
	Bytes, Index, Seq                       int
	Segment                                 string
}

type Segment struct {
	Path    string
	Seq     int
	Rotated bool
	Bytes   int
	Records []Record
}

func Run(caseDir, outDir string) error {
	policy, err := loadPolicy(filepath.Join(caseDir, "policy.json"))
	if err != nil {
		return err
	}
	segs, ignored, invalid, err := loadSegments(caseDir)
	if err != nil {
		return err
	}
	var records []Record
	for _, s := range segs {
		records = append(records, s.Records...)
	}
	sort.Slice(records, func(i, j int) bool {
		if records[i].Realtime != records[j].Realtime {
			return records[i].Realtime < records[j].Realtime
		}
		return records[i].Cursor < records[j].Cursor
	})
	if err := os.MkdirAll(outDir, 0755); err != nil {
		return err
	}
	if err := writeTimeline(filepath.Join(outDir, "timeline.tsv"), records); err != nil {
		return err
	}
	boots := bootList(records)
	decisions, total, over := plan(segs, records, policy, boots)
	if err := writeVacuum(filepath.Join(outDir, "vacuum.tsv"), decisions); err != nil {
		return err
	}
	summary := map[string]any{"boots": boots, "committed_records": len(records), "ignored_tail_records": ignored, "invalid_segments": invalid, "over_budget": over, "scanned_segments": len(segs) + invalid, "total_bytes_after_plan": total}
	b, _ := json.MarshalIndent(summary, "", "  ")
	return os.WriteFile(filepath.Join(outDir, "summary.json"), append(b, '\n'), 0644)
}

func loadPolicy(path string) (Policy, error) {
	var p Policy
	b, err := os.ReadFile(path)
	if err != nil {
		return p, err
	}
	err = json.Unmarshal(b, &p)
	return p, err
}

func loadSegments(caseDir string) ([]Segment, int, int, error) {
	files, err := filepath.Glob(filepath.Join(caseDir, "segments", "*.jseg"))
	if err != nil {
		return nil, 0, 0, err
	}
	sort.Strings(files)
	var segs []Segment
	ignored, invalid := 0, 0
	for _, f := range files {
		s, tail, ok := readSegment(caseDir, f)
		ignored += tail
		if ok {
			segs = append(segs, s)
		} else {
			invalid++
		}
	}
	return segs, ignored, invalid, nil
}

func readSegment(caseDir, file string) (Segment, int, bool) {
	fh, err := os.Open(file)
	if err != nil {
		return Segment{}, 0, false
	}
	defer fh.Close()
	sc := bufio.NewScanner(fh)
	if !sc.Scan() {
		return Segment{}, 0, false
	}
	header := strings.Split(sc.Text(), "\t")
	s := Segment{Path: rel(caseDir, file)}
	for _, part := range header[1:] {
		kv := strings.SplitN(part, "=", 2)
		if len(kv) != 2 {
			continue
		}
		switch kv[0] {
		case "seq":
			s.Seq, _ = strconv.Atoi(kv[1])
		case "rotated":
			s.Rotated = kv[1] == "true"
		case "bytes":
			s.Bytes, _ = strconv.Atoi(kv[1])
		}
	}
	if len(header) < 5 || header[0] != "JSEG" || s.Bytes <= 0 {
		return Segment{}, 0, false
	}
	idx := 0
	tail := 0
	for sc.Scan() {
		parts := strings.Split(sc.Text(), "\t")
		if len(parts) != 9 || parts[0] != "R" {
			tail++
			continue
		}
		mono, _ := strconv.ParseInt(parts[3], 10, 64)
		real, _ := strconv.ParseInt(parts[4], 10, 64)
		rb, _ := strconv.Atoi(parts[8])
		s.Records = append(s.Records, Record{Cursor: parts[1], BootID: parts[2], Monotonic: mono, Realtime: real, Unit: parts[5], Priority: parts[6], Message: parts[7], Bytes: rb, Index: idx, Seq: s.Seq, Segment: s.Path})
		idx++
	}
	return s, tail, true
}

func bootList(records []Record) []string {
	seen := map[string]bool{}
	var boots []string
	for _, r := range records {
		if !seen[r.BootID] {
			seen[r.BootID] = true
			boots = append(boots, r.BootID)
		}
	}
	return boots
}

type Decision struct {
	Segment                        Segment
	Action, Reason, Oldest, Newest string
}

func plan(segs []Segment, records []Record, p Policy, boots []string) ([]Decision, int, bool) {
	keepBoot := map[string]bool{}
	for i := len(boots) - p.RetainBoots; i < len(boots); i++ {
		if i >= 0 {
			keepBoot[boots[i]] = true
		}
	}
	var ds []Decision
	total := 0
	for _, s := range segs {
		d := Decision{Segment: s, Action: "keep", Reason: "budget-spare"}
		oldest, newest := "", ""
		for i, r := range s.Records {
			if i == 0 {
				oldest = r.Cursor
			}
			newest = r.Cursor
			if keepBoot[r.BootID] || r.Realtime >= p.RetainRealtimeAfterUS {
				d.Reason = "retention-window"
			}
		}
		d.Oldest, d.Newest = oldest, newest
		if !s.Rotated {
			d.Reason = "active"
		}
		total += s.Bytes
		ds = append(ds, d)
	}
	for i := range ds {
		if total <= p.MaxBytes {
			break
		}
		if ds[i].Segment.Rotated && ds[i].Reason == "budget-spare" {
			ds[i].Action = "delete"
			ds[i].Reason = "budget"
			total -= ds[i].Segment.Bytes
		}
	}
	sort.Slice(ds, func(i, j int) bool { return ds[i].Segment.Path < ds[j].Segment.Path })
	return ds, total, total > p.MaxBytes
}

func writeTimeline(path string, records []Record) error {
	var b strings.Builder
	b.WriteString("rank\tboot_id\tcursor\trealtime_us\tmonotonic_us\tsegment\trecord_index\tunit\tpriority\tmessage\n")
	for i, r := range records {
		fmt.Fprintf(&b, "%d\t%s\t%s\t%d\t%d\t%s\t%d\t%s\t%s\t%s\n", i, r.BootID, r.Cursor, r.Realtime, r.Monotonic, r.Segment, r.Index, r.Unit, r.Priority, r.Message)
	}
	return os.WriteFile(path, []byte(b.String()), 0644)
}

func writeVacuum(path string, ds []Decision) error {
	var b strings.Builder
	b.WriteString("segment\taction\treason\tbytes\tcommitted_records\toldest_cursor\tnewest_cursor\n")
	for _, d := range ds {
		fmt.Fprintf(&b, "%s\t%s\t%s\t%d\t%d\t%s\t%s\n", d.Segment.Path, d.Action, d.Reason, d.Segment.Bytes, len(d.Segment.Records), d.Oldest, d.Newest)
	}
	return os.WriteFile(path, []byte(b.String()), 0644)
}

func rel(root, path string) string { r, _ := filepath.Rel(root, path); return filepath.ToSlash(r) }
