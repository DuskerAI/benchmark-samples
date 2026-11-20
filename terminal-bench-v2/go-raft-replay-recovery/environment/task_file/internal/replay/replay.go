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
	membership := Membership{}
	committed := 0
	lastTerm := 0
	counters := Counters{}

	for _, ev := range tf.Events {
		switch ev.Type {
		case "cluster":
			membership = normalizeMembership(ev.Membership)
		case "install_snapshot":
			n := ensureNode(nodes, ev.Node)
			n.Snap = snapshot{Index: ev.LastIndex, Term: ev.LastTerm, KV: copyIntMap(ev.State), Sessions: copySessionMap(ev.Sessions), Membership: normalizeMembership(ev.Membership)}
			for i := range n.Log {
				if i < ev.LastIndex {
					delete(n.Log, i)
				}
			}
			counters.Snapshots++
		case "append_entries":
			n := ensureNode(nodes, ev.Node)
			for _, ent := range ev.Entries {
				if ent.Index <= n.Snap.Index {
					counters.IgnoredEntries++
					continue
				}
				n.Log[ent.Index] = normalizeEntry(ent)
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
			if ok && base.Index >= committed {
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
				ent, ok := chooseEntry(nodes, live, idx)
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

func selectSnapshot(nodes map[string]*nodeState, live map[string]bool) (snapshot, bool) {
	best := snapshot{}
	for node, st := range nodes {
		if !live[node] || st.Snap.Index == 0 {
			continue
		}
		count := 0
		for other, ost := range nodes {
			if !live[other] {
				continue
			}
			if ost.Snap.Index == st.Snap.Index && ost.Snap.Term == st.Snap.Term {
				count++
			}
		}
		if count >= majority(len(st.Snap.Membership.Voters)) && st.Snap.Index > best.Index {
			best = st.Snap
		}
	}
	return best, best.Index > 0
}

func chooseEntry(nodes map[string]*nodeState, live map[string]bool, idx int) (Entry, bool) {
	terms := map[int]Entry{}
	counts := map[int]int{}
	for node, st := range nodes {
		if !live[node] {
			continue
		}
		ent, ok := st.Log[idx]
		if !ok {
			continue
		}
		terms[ent.Term] = ent
		counts[ent.Term]++
	}
	bestTerm := -1
	for term, count := range counts {
		if count >= majority(len(live)) && term > bestTerm {
			bestTerm = term
		}
	}
	if bestTerm < 0 {
		return Entry{}, false
	}
	return terms[bestTerm], true
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
	if prev, ok := sessions[ent.ClientID]; ok && ent.Seq < prev.Seq {
		counters.Duplicates++
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
