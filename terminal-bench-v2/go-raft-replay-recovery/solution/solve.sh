#!/bin/bash
set -euo pipefail

cd /app/task_file

cat > internal/replay/replay.go <<'GO'
package replay

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type Membership struct {
	Voters         []string `json:"voters"`
	Learners       []string `json:"learners"`
	OutgoingVoters []string `json:"outgoing_voters,omitempty"`
	Phase          string   `json:"phase,omitempty"`
}

type Command struct {
	Op    string `json:"op"`
	Key   string `json:"key"`
	Value int    `json:"value,omitempty"`
	Delta int    `json:"delta,omitempty"`
	Expect int   `json:"expect,omitempty"`
}

type Session struct {
	Seq       int    `json:"seq"`
	Response  int    `json:"response"`
	RequestID string `json:"request_id"`
}

type Entry struct {
	Index      int        `json:"index"`
	Term       int        `json:"term"`
	Type       string     `json:"type"`
	Command    Command    `json:"command,omitempty"`
	ClientID   string     `json:"client_id,omitempty"`
	Seq        int        `json:"seq,omitempty"`
	RequestID  string     `json:"request_id,omitempty"`
	Membership Membership `json:"membership,omitempty"`
}

type Event struct {
	Type         string             `json:"type"`
	Node         string             `json:"node,omitempty"`
	Term         int                `json:"term,omitempty"`
	PrevIndex    int                `json:"prev_index,omitempty"`
	PrevTerm     int                `json:"prev_term,omitempty"`
	Entries      []Entry            `json:"entries,omitempty"`
	LeaderCommit int                `json:"leader_commit,omitempty"`
	LastIndex    int                `json:"last_index,omitempty"`
	LastTerm     int                `json:"last_term,omitempty"`
	Membership   Membership         `json:"membership,omitempty"`
	State        map[string]int     `json:"state,omitempty"`
	Sessions     map[string]Session `json:"sessions,omitempty"`
	Live         []string           `json:"live,omitempty"`
}

type traceFile struct {
	Events []Event `json:"events"`
}

type Counters struct {
	Commands       int `json:"commands"`
	Configs        int `json:"configs"`
	Snapshots      int `json:"snapshots"`
	Duplicates     int `json:"duplicates"`
	IgnoredEntries int `json:"ignored_entries"`
}

type TraceReport struct {
	Name           string             `json:"name"`
	CommittedIndex int                `json:"committed_index"`
	LastTerm       int                `json:"last_term"`
	KV             map[string]int     `json:"kv"`
	Sessions       map[string]Session `json:"sessions"`
	Membership     Membership         `json:"membership"`
	Counters       Counters           `json:"counters"`
}

type Report struct {
	Traces []TraceReport  `json:"traces"`
	Totals map[string]int `json:"totals"`
}

type snapshot struct {
	Index      int
	Term       int
	KV         map[string]int
	Sessions   map[string]Session
	Membership Membership
}

type nodeState struct {
	Log          map[int]Entry
	Snap         snapshot
	LeaderCommit int
}

func Run(input, output string) error {
	paths, err := filepath.Glob(filepath.Join(input, "*.json"))
	if err != nil {
		return err
	}
	sort.Strings(paths)

	rep := Report{Totals: map[string]int{"traces": 0, "committed_entries": 0, "snapshots": 0, "duplicates": 0}}
	for _, path := range paths {
		tr, err := replayOne(path)
		if err != nil {
			return err
		}
		rep.Traces = append(rep.Traces, tr)
		rep.Totals["traces"]++
		rep.Totals["committed_entries"] += tr.Counters.Commands + tr.Counters.Configs
		rep.Totals["snapshots"] += tr.Counters.Snapshots
		rep.Totals["duplicates"] += tr.Counters.Duplicates
	}

	data, err := json.MarshalIndent(rep, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(output, data, 0644)
}

func replayOne(path string) (TraceReport, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return TraceReport{}, err
	}
	var tf traceFile
	if err := json.Unmarshal(data, &tf); err != nil {
		return TraceReport{}, fmt.Errorf("%s: %w", path, err)
	}

	nodes := map[string]*nodeState{}
	kv := map[string]int{}
	sessions := map[string]Session{}
	membership := Membership{Phase: "stable"}
	committed := 0
	lastTerm := 0
	counters := Counters{}

	for _, ev := range tf.Events {
		switch ev.Type {
		case "cluster":
			membership = normalizeMembership(ev.Membership)
		case "install_snapshot":
			n := ensureNode(nodes, ev.Node)
			if n.Snap.Index > 0 && ev.LastIndex <= n.Snap.Index {
				continue
			}
			n.Snap = snapshot{Index: ev.LastIndex, Term: ev.LastTerm, KV: copyIntMap(ev.State), Sessions: copySessionMap(ev.Sessions), Membership: normalizeMembership(ev.Membership)}
			for i := range n.Log {
				if i <= ev.LastIndex {
					delete(n.Log, i)
				}
			}
			counters.Snapshots++
		case "append_entries":
			n := ensureNode(nodes, ev.Node)
			if !matchesPrev(n, ev.PrevIndex, ev.PrevTerm) {
				counters.IgnoredEntries += len(ev.Entries)
				continue
			}
			for _, ent := range ev.Entries {
				ent = normalizeEntry(ent)
				if ent.Index <= n.Snap.Index {
					counters.IgnoredEntries++
					continue
				}
				if old, ok := n.Log[ent.Index]; ok && old.Term != ent.Term {
					for i := range n.Log {
						if i >= ent.Index {
							delete(n.Log, i)
						}
					}
				}
				n.Log[ent.Index] = ent
			}
			if ev.LeaderCommit > n.LeaderCommit {
				n.LeaderCommit = ev.LeaderCommit
			}
		case "crash_cut":
			live := makeSet(ev.Live)
			if len(live) == 0 {
				for name := range nodes {
					live[name] = true
				}
			}
			base, ok := selectSnapshot(nodes, live)
			if ok && base.Index > committed {
				committed = base.Index
				lastTerm = base.Term
				kv = copyIntMap(base.KV)
				sessions = copySessionMap(base.Sessions)
				membership = normalizeMembership(base.Membership)
			}
			bound := 0
			for node := range live {
				if n := nodes[node]; n != nil && n.LeaderCommit > bound {
					bound = n.LeaderCommit
				}
			}
			for idx := committed + 1; idx <= bound; idx++ {
				ent, ok := chooseEntry(nodes, live, membership, idx)
				if !ok {
					break
				}
				applyEntry(ent, kv, sessions, &membership, &counters)
				committed = idx
				lastTerm = ent.Term
			}
		}
	}

	return TraceReport{Name: filepath.Base(path), CommittedIndex: committed, LastTerm: lastTerm, KV: kv, Sessions: sessions, Membership: normalizeMembership(membership), Counters: counters}, nil
}

func ensureNode(nodes map[string]*nodeState, name string) *nodeState {
	if nodes[name] == nil {
		nodes[name] = &nodeState{Log: map[int]Entry{}}
	}
	return nodes[name]
}

func matchesPrev(n *nodeState, index, term int) bool {
	if index == 0 {
		return n.Snap.Index == 0 && term == 0
	}
	if n.Snap.Index > 0 {
		if index < n.Snap.Index {
			return false
		}
		if index == n.Snap.Index {
			return n.Snap.Term == term
		}
	}
	ent, ok := n.Log[index]
	return ok && ent.Term == term
}

func selectSnapshot(nodes map[string]*nodeState, live map[string]bool) (snapshot, bool) {
	best := snapshot{}
	for _, st := range nodes {
		if st.Snap.Index == 0 {
			continue
		}
		holders := map[string]bool{}
		for name, other := range nodes {
			if !live[name] || other.Snap.Index == 0 {
				continue
			}
			if sameSnapshot(st.Snap, other.Snap) {
				holders[name] = true
			}
		}
		if quorum(st.Snap.Membership, holders) && st.Snap.Index > best.Index {
			best = st.Snap
		}
	}
	return best, best.Index > 0
}

func sameSnapshot(a, b snapshot) bool {
	if a.Index != b.Index || a.Term != b.Term {
		return false
	}
	ab, _ := json.Marshal(a)
	bb, _ := json.Marshal(b)
	return string(ab) == string(bb)
}

func chooseEntry(nodes map[string]*nodeState, live map[string]bool, membership Membership, idx int) (Entry, bool) {
	byTerm := map[int]map[string]Entry{}
	holdersByTerm := map[int]map[string]map[string]bool{}
	for name, st := range nodes {
		if !live[name] {
			continue
		}
		ent, ok := st.Log[idx]
		if !ok {
			continue
		}
		key := entryFingerprint(ent)
		if byTerm[ent.Term] == nil {
			byTerm[ent.Term] = map[string]Entry{}
		}
		if holdersByTerm[ent.Term] == nil {
			holdersByTerm[ent.Term] = map[string]map[string]bool{}
		}
		byTerm[ent.Term][key] = ent
		if holdersByTerm[ent.Term][key] == nil {
			holdersByTerm[ent.Term][key] = map[string]bool{}
		}
		holdersByTerm[ent.Term][key][name] = true
	}
	terms := make([]int, 0, len(byTerm))
	for term := range byTerm {
		terms = append(terms, term)
	}
	sort.Sort(sort.Reverse(sort.IntSlice(terms)))
	for _, term := range terms {
		keys := make([]string, 0, len(byTerm[term]))
		for key := range byTerm[term] {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			ent := byTerm[term][key]
			holders := holdersByTerm[term][key]
			if !quorum(membership, holders) {
				continue
			}
			if ent.Type == "config" && ent.Membership.Phase == "joint" && !majorityOf(ent.Membership.Voters, holders) {
				continue
			}
			return ent, true
		}
	}
	return Entry{}, false
}

func entryFingerprint(ent Entry) string {
	ent = normalizeEntry(ent)
	data, _ := json.Marshal(ent)
	return string(data)
}

func quorum(m Membership, holders map[string]bool) bool {
	if len(m.OutgoingVoters) > 0 {
		return majorityOf(m.OutgoingVoters, holders) && majorityOf(m.Voters, holders)
	}
	return majorityOf(m.Voters, holders)
}

func majorityOf(voters []string, holders map[string]bool) bool {
	if len(voters) == 0 {
		return false
	}
	count := 0
	for _, voter := range voters {
		if holders[voter] {
			count++
		}
	}
	return count >= majority(len(voters))
}

func applyEntry(ent Entry, kv map[string]int, sessions map[string]Session, membership *Membership, counters *Counters) {
	if ent.Type == "config" {
		*membership = normalizeMembership(ent.Membership)
		counters.Configs++
		return
	}
	if ent.Type != "command" {
		return
	}
	if prev, ok := sessions[ent.ClientID]; ok && ent.Seq <= prev.Seq {
		counters.Duplicates++
		return
	}
	var response int
	switch strings.ToLower(ent.Command.Op) {
	case "set":
		kv[ent.Command.Key] = ent.Command.Value
		response = ent.Command.Value
	case "incr":
		kv[ent.Command.Key] += ent.Command.Delta
		response = kv[ent.Command.Key]
	case "delete":
		response = kv[ent.Command.Key]
		delete(kv, ent.Command.Key)
	case "cas":
		if kv[ent.Command.Key] == ent.Command.Expect {
			kv[ent.Command.Key] = ent.Command.Value
			response = 1
		} else {
			response = 0
		}
	}
	sessions[ent.ClientID] = Session{Seq: ent.Seq, Response: response, RequestID: ent.RequestID}
	counters.Commands++
}

func normalizeEntry(ent Entry) Entry {
	ent.Membership = normalizeMembership(ent.Membership)
	return ent
}

func normalizeMembership(m Membership) Membership {
	sort.Strings(m.Voters)
	sort.Strings(m.Learners)
	sort.Strings(m.OutgoingVoters)
	return m
}

func majority(n int) int { return n/2 + 1 }

func makeSet(xs []string) map[string]bool {
	out := map[string]bool{}
	for _, x := range xs {
		out[x] = true
	}
	return out
}

func copyIntMap(in map[string]int) map[string]int {
	out := map[string]int{}
	for k, v := range in {
		out[k] = v
	}
	return out
}

func copySessionMap(in map[string]Session) map[string]Session {
	out := map[string]Session{}
	for k, v := range in {
		out[k] = v
	}
	return out
}
GO

gofmt -w internal/replay/replay.go cmd/raftreport/main.go
go run ./cmd/raftreport --input /app/task_file/traces --output /app/task_file/report.json
