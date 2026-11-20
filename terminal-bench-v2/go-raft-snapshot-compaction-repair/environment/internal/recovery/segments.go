package recovery

import (
	"bufio"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

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
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	var out []Batch
	ignored := 0
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var rec recordLine
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			ignored++
			continue
		}
		payload, err := canonicalJSON(rec.Payload)
		if err != nil {
			ignored++
			continue
		}
		want := fmt.Sprintf("%08x", crc32.ChecksumIEEE(payload))
		if want != rec.CRC {
			ignored++
			continue
		}
		out = append(out, rec.Payload)
	}
	if err := scanner.Err(); err != nil {
		return nil, 0, err
	}
	return out, ignored, nil
}
