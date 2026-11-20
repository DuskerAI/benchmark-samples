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
	sort.Strings(paths)
	data, err := os.ReadFile(paths[0])
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
	return snap, nil
}
