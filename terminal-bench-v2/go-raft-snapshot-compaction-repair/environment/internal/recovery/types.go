package recovery

type Snapshot struct {
	ID    string         `json:"id"`
	Index int            `json:"index"`
	Term  int            `json:"term"`
	KV    map[string]int `json:"kv"`
}

type Entry struct {
	Index  int     `json:"index"`
	Term   int     `json:"term"`
	Op     string  `json:"op"`
	Key    string  `json:"key,omitempty"`
	Value  int     `json:"value,omitempty"`
	Delta  int     `json:"delta,omitempty"`
	Expect int     `json:"expect,omitempty"`
	Ops    []Entry `json:"ops,omitempty"`
}

type Batch struct {
	Node         string  `json:"node"`
	PrevIndex    int     `json:"prev_index"`
	PrevTerm     int     `json:"prev_term"`
	LeaderCommit int     `json:"leader_commit"`
	Entries      []Entry `json:"entries"`
}

type recordLine struct {
	CRC     string `json:"crc"`
	Payload Batch  `json:"payload"`
}

type Output struct {
	SnapshotID     string         `json:"snapshot_id"`
	LastApplied    int            `json:"last_applied"`
	LastTerm       int            `json:"last_term"`
	KV             map[string]int `json:"kv"`
	Applied        []int          `json:"applied"`
	IgnoredRecords int            `json:"ignored_records"`
	StateDigest    string         `json:"state_digest"`
}
