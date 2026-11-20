use crate::types::*;
use crate::{mvcc, transfer, wire};
use std::collections::{BTreeMap, BTreeSet};

fn members(c: &Config) -> BTreeSet<Id> {
    c.old.iter().chain(c.new.iter().flatten()).copied().collect()
}
fn quorum(c: &Config, ids: &BTreeSet<Id>) -> bool {
    let majority = |v: &[Id]| v.iter().filter(|id| ids.contains(id)).count() > v.len() / 2;
    majority(&c.old) && c.new.as_ref().map_or(true, |v| majority(v))
}
fn last(d: &Disk) -> u64 { d.snapshot.index + d.log.len() as u64 }
fn term_at(d: &Disk, index: u64) -> Option<u64> {
    if index == d.snapshot.index { Some(d.snapshot.term) }
    else if index > d.snapshot.index {
        d.log.get((index - d.snapshot.index - 1) as usize).map(|e| e.term)
    } else { None }
}
fn configuration(d: &Disk) -> Config {
    let mut c = d.snapshot.config.clone();
    for e in &d.log {
        match &e.command {
            EntryKind::Joint { old, new, .. } => c = Config { old: old.clone(), new: Some(new.clone()) },
            EntryKind::Final { voters, .. } => c = Config { old: voters.clone(), new: None },
            _ => (),
        }
    }
    c
}
fn durable_coverage(d: &Disk, manifest: &Manifest) -> u64 {
    if d.commit >= manifest.index { return manifest.size; }
    let mut prefix = 0;
    if let Some(incoming) = d.incoming.as_ref().filter(|i| i.manifest == *manifest) {
        for (&offset, data) in &incoming.pieces {
            if offset > prefix { break; }
            prefix = prefix.max(offset + data.len() as u64);
        }
    }
    prefix
}

struct View {
    mvcc: Mvcc,
    tx_results: BTreeMap<u64, TransactionResult>,
    values: BTreeMap<String, i64>,
    sessions: BTreeMap<String, Session>,
    config: Config,
    completed: BTreeMap<String, Vec<Id>>,
    pending_change: Option<String>,
    results: BTreeMap<u64, Result<Session, u64>>,
}
fn replay(disk: &Disk, limit: u64) -> View {
    let s = &disk.snapshot;
    let mut v = View { mvcc: s.mvcc.clone(), tx_results: BTreeMap::new(), values: s.values.clone(), sessions: s.sessions.clone(),
        config: s.config.clone(), completed: s.completed.clone(), pending_change: s.pending_change.clone(), results: BTreeMap::new() };
    for (offset, e) in disk.log.iter().enumerate() {
        let index = s.index + offset as u64 + 1;
        if index > limit { break; }
        match &e.command {
            EntryKind::Write { client, seq, key, delta, expect } => {
                let result = match v.sessions.get(client) {
                    Some(cached) if *seq < cached.seq => Err(cached.seq),
                    Some(cached) if *seq == cached.seq => Ok(cached.clone()),
                    _ => {
                        let before = *v.values.get(key).unwrap_or(&0);
                        let applied = expect.map_or(true, |x| x == before);
                        let value = if applied { before + delta } else { before };
                        if applied { v.values.insert(key.clone(), value); mvcc::record(&mut v.mvcc, key, Some(value), index); }
                        let cached = Session { seq: *seq, value, applied };
                        v.sessions.insert(client.clone(), cached.clone());
                        Ok(cached)
                    }
                };
                v.results.insert(index, result);
            }
            EntryKind::Joint { change, old, new } => {
                v.config = Config { old: old.clone(), new: Some(new.clone()) };
                v.pending_change = Some(change.clone());
            }
            EntryKind::Final { change, voters } => {
                v.config = Config { old: voters.clone(), new: None };
                v.completed.insert(change.clone(), voters.clone());
                v.pending_change = None;
            }
            EntryKind::Transaction { operation } => {
                let result = mvcc::apply(&mut v.mvcc, &mut v.values, operation, index);
                v.tx_results.insert(index, result);
            }
            EntryKind::Noop => (),
        }
    }
    v
}

fn checkpoint(d: &Disk, index: u64) -> Snapshot {
    let v = replay(d, index);
    Snapshot { index, term: term_at(d, index).unwrap(), values: v.values,
        sessions: v.sessions, config: v.config, completed: v.completed,
        pending_change: v.pending_change, mvcc: v.mvcc }
}

struct ReadWait {
    request: String,
    key: String,
    scan: Option<(String, Span, Option<String>, usize)>,
    acks: BTreeSet<Id>,
    frontier: Option<u64>,
}
struct Flight {
    start: u64,
    end: u64,
    read: Option<u64>,
    manifest: Option<Manifest>,
    offset: u64,
    length: usize,
}

// No staged image is used as evidence of storage or application completion.
// The two ordered workers have independent generations and never block step().
pub struct Node {
    id: Id,
    peers: Vec<Id>,
    disk: Disk,
    stable: Disk,
    submitted: Disk,
    generation: u64,
    durable: u64,
    storage: BTreeMap<u64, Disk>,
    sends: Vec<(u64, Id, Message)>,
    apply_generation: u64,
    apply_done: u64,
    apply_index: u64,
    applications: BTreeMap<u64, Snapshot>,
    applied: Snapshot,
    role: String,
    leader: Option<Id>,
    votes: BTreeSet<Id>,
    next: BTreeMap<Id, u64>,
    matched: BTreeMap<Id, u64>,
    flights: BTreeMap<(Id, u64), Flight>,
    transfers: BTreeMap<Id, (Manifest, u64)>,
    chunk_limits: BTreeMap<Id, usize>,
    serial: u64,
    writes: BTreeMap<u64, Vec<String>>,
    transactions: BTreeMap<u64, Vec<String>>,
    changes: BTreeSet<String>,
    ready: Vec<(u64, Action)>,
    reads: BTreeMap<u64, ReadWait>,
    read_config: Config,
    compact_target: Option<u64>,
}
impl Node {
    pub fn new(id: Id, peers: Vec<Id>, disk: Disk) -> Self {
        Self { id, peers, stable: disk.clone(), submitted: disk.clone(),
            applied: disk.snapshot.clone(), apply_index: disk.snapshot.index,
            read_config: configuration(&disk), disk,
            generation: 0, durable: 0, storage: BTreeMap::new(), sends: Vec::new(),
            apply_generation: 0, apply_done: 0, applications: BTreeMap::new(),
            role: "follower".into(), leader: None, votes: BTreeSet::new(),
            next: BTreeMap::new(), matched: BTreeMap::new(), flights: BTreeMap::new(),
            transfers: BTreeMap::new(), chunk_limits: BTreeMap::new(), serial: 0, writes: BTreeMap::new(),
            transactions: BTreeMap::new(), changes: BTreeSet::new(), ready: Vec::new(),
            reads: BTreeMap::new(), compact_target: None }
    }
    pub fn disk(&self) -> Disk { self.disk.clone() }
    pub fn status(&self) -> Status {
        Status { durable_generation: self.durable, apply_generation: self.apply_done,
            applied_index: self.applied.index, role: self.role.clone(), leader: self.leader,
            config: configuration(&self.disk), values: self.applied.values.clone(),
            sessions: self.applied.sessions.clone(), mvcc: self.applied.mvcc.clone() }
    }
    fn fresh(&mut self) -> u64 { self.serial += 1; self.serial }
    fn send(out: &mut Vec<Action>, to: Id, message: Message) { out.push(Action::Send { to, message }); }
    fn follow(&mut self, term: u64) {
        if term > self.disk.term { self.disk.term = term; self.disk.voted_for = None; }
        self.role = "follower".into(); self.leader = None;
        self.votes.clear(); self.writes.clear(); self.transactions.clear(); self.changes.clear();
        self.reads.clear(); self.flights.clear(); self.transfers.clear(); self.chunk_limits.clear();
        self.next.clear(); self.matched.clear();
        // ready contains only results already established by durable commit.
    }
    fn append(&mut self, command: EntryKind) -> u64 {
        self.disk.log.push(Entry { term: self.disk.term, command });
        last(&self.disk)
    }
    fn lead(&mut self, out: &mut Vec<Action>) {
        self.role = "leader".into(); self.leader = Some(self.id);
        self.next = self.peers.iter().map(|id| (*id, last(&self.disk) + 1)).collect();
        self.matched.clear(); self.flights.clear(); self.transfers.clear(); self.chunk_limits.clear();
        self.append(EntryKind::Noop);
        self.broadcast(None, out);
    }
    fn replicate(&mut self, to: Id, read: Option<u64>, out: &mut Vec<Action>) {
        let next = self.next.get(&to).copied().unwrap_or(last(&self.disk) + 1).min(last(&self.disk) + 1).max(1);
        let rpc = self.fresh();
        let (message, flight) = if next <= self.disk.snapshot.index {
            let (manifest, bytes) = wire::encode(&self.disk.snapshot);
            let offset = self.transfers.get(&to).filter(|(m, _)| m == &manifest).map_or(0, |(_, n)| *n);
            if !self.transfers.get(&to).is_some_and(|(m, _)| m == &manifest) { self.chunk_limits.remove(&to); }
            let offset = if offset < manifest.size { offset } else { 0 };
            let limit = self.chunk_limits.get(&to).copied().unwrap_or(wire::MAX_CHUNK);
            let end = (offset as usize + limit).min(bytes.len());
            let data = bytes[offset as usize..end].to_vec();
            self.transfers.insert(to, (manifest.clone(), offset));
            let f = Flight { start: next, end: manifest.index, read, manifest: Some(manifest.clone()), offset, length: data.len() };
            (Message::SnapshotChunk { term: self.disk.term, rpc, manifest, offset, data }, f)
        } else {
            let prev = next - 1;
            let entries: Vec<Entry> = self.disk.log[(prev - self.disk.snapshot.index) as usize..].iter().take(8).cloned().collect();
            let end = prev + entries.len() as u64;
            (Message::Append { term: self.disk.term, rpc, prev_index: prev,
                prev_term: term_at(&self.disk, prev).unwrap(), entries, commit: self.disk.commit, read },
                Flight { start: next, end, read, manifest: None, offset: 0, length: 0 })
        };
        self.flights.insert((to, rpc), flight);
        Self::send(out, to, message);
    }
    fn broadcast(&mut self, read: Option<u64>, out: &mut Vec<Action>) {
        for to in self.peers.clone() { if to != self.id { self.replicate(to, read, out); } }
    }

    fn confirm_reads(&mut self, to: Id, out: &mut Vec<Action>) {
        let index = self.matched.get(&to).copied().unwrap_or(0);
        let Some(prev_term) = term_at(&self.disk, index) else { return; };
        for context in self.reads.keys().copied().collect::<Vec<_>>() {
            let rpc = self.fresh();
            self.flights.insert((to, rpc), Flight { start: index + 1, end: index,
                read: Some(context), manifest: None, offset: 0, length: 0 });
            Self::send(out, to, Message::Append { term: self.disk.term, rpc,
                prev_index: index, prev_term, entries: Vec::new(), commit: self.disk.commit, read: Some(context) });
        }
    }

    // Save exact per-entry results before compaction or leadership loss can
    // discard the invocation. Visibility still waits for the independent worker.
    fn collect_results(&mut self) {
        if self.writes.is_empty() && self.transactions.is_empty() && self.changes.is_empty() { return; }
        let v = replay(&self.stable, self.stable.commit);
        let done: Vec<u64> = self.writes.keys().copied().filter(|i| *i <= self.stable.commit).collect();
        for index in done {
            if let Some(result) = v.results.get(&index) {
                for request in self.writes.remove(&index).unwrap() {
                    let action = match result {
                        Ok(s) => Action::Wrote { request, seq: s.seq, value: s.value, applied: s.applied },
                        Err(last_seq) => Action::Stale { request, last_seq: *last_seq },
                    };
                    self.ready.push((index, action));
                }
            }
        }
        let done: Vec<u64> = self.transactions.keys().copied().filter(|i| *i <= self.stable.commit).collect();
        for index in done {
            if let Some(result) = v.tx_results.get(&index) {
                for request in self.transactions.remove(&index).unwrap() {
                    self.ready.push((index, Action::Transaction { request, result: result.clone() }));
                }
            }
        }
        let done: Vec<String> = self.changes.iter().filter(|r| v.completed.contains_key(*r)).cloned().collect();
        for request in done {
            self.changes.remove(&request);
            self.ready.push((self.stable.commit, Action::Changed { voters: v.completed[&request].clone(), request }));
        }
    }

    fn advance_commit(&mut self) -> bool {
        let before = self.disk.commit;
        let c = configuration(&self.disk);
        for index in (before + 1..=last(&self.disk)).rev() {
            if term_at(&self.disk, index) != Some(self.disk.term) { continue; }
            let mut ids: BTreeSet<Id> = self.matched.iter().filter(|(_, n)| **n >= index).map(|(id, _)| *id).collect();
            // A local append is not a durable self acknowledgment. Comparing
            // terms also excludes an old durable suffix awaiting replacement.
            if self.stable.commit >= index || term_at(&self.stable, index) == Some(self.disk.term) {
                ids.insert(self.id);
            }
            if quorum(&c, &ids) { self.disk.commit = index; break; }
        }
        self.disk.commit != before
    }

    fn progress(&mut self, out: &mut Vec<Action>) {
        self.collect_results();
        if self.stable.commit > self.apply_index {
            self.apply_generation += 1;
            let state = checkpoint(&self.stable, self.stable.commit);
            self.apply_index = state.index;
            self.applications.insert(self.apply_generation, state.clone());
            out.push(Action::Apply { generation: self.apply_generation, state });
        }
        if self.role == "leader" {
            let advanced = self.advance_commit();
            if advanced { self.broadcast(None, out); }
            let committed = replay(&self.stable, self.stable.commit);
            if committed.config.new.is_none() && configuration(&self.disk) == committed.config && !members(&committed.config).contains(&self.id) {
                // Inform the new electorate even when this leader is retired.
                self.broadcast(None, out);
                self.follow(self.disk.term);
            } else {
                let c = configuration(&self.disk);
                if c.new.is_some() && committed.config == c {
                    if let Some(change) = committed.pending_change {
                        self.append(EntryKind::Final { change, voters: c.new.unwrap() });
                        self.broadcast(None, out);
                    }
                }
            }
        }
        if self.role == "leader" {
            let c = configuration(&self.disk);
            if c != self.read_config {
                self.read_config = c.clone();
                let waiting = std::mem::take(&mut self.reads);
                for (_, mut wait) in waiting {
                    wait.acks = BTreeSet::from([self.id]); wait.frontier = None;
                    let context = self.fresh();
                    self.reads.insert(context, wait);
                    self.broadcast(Some(context), out);
                }
            }
            // A newly elected leader must first commit its current-term no-op.
            if term_at(&self.stable, self.stable.commit) == Some(self.disk.term) {
                for wait in self.reads.values_mut() {
                    if wait.frontier.is_none() && quorum(&c, &wait.acks) {
                        wait.frontier = Some(self.disk.commit);
                    }
                }
                let done: Vec<u64> = self.reads.iter().filter(|(_, w)| w.frontier.is_some_and(|i| i <= self.applied.index)).map(|(ctx, _)| *ctx).collect();
                for ctx in done {
                    let w = self.reads.remove(&ctx).unwrap();
                    if let Some((txn, range, after, limit)) = w.scan {
                        match mvcc::scan(&self.applied.mvcc, &txn, &range, after.as_deref(), limit) {
                            Ok(page) => out.push(Action::Scan { request: w.request, txn, page }),
                            Err(result) => out.push(Action::Transaction { request: w.request, result }),
                        }
                    } else {
                        out.push(Action::Read { request: w.request, value: *self.applied.values.get(&w.key).unwrap_or(&0) });
                    }
                }
            }
        }
        let ready = std::mem::take(&mut self.ready);
        for (index, action) in ready {
            if index <= self.applied.index { out.push(action); }
            else { self.ready.push((index, action)); }
        }
        if let Some(target) = self.compact_target {
            if target <= self.disk.snapshot.index { self.compact_target = None; }
            else if target <= self.stable.commit && target <= self.applied.index {
                let snapshot = checkpoint(&self.disk, target);
                self.disk.log.drain(..(target - self.disk.snapshot.index) as usize);
                self.disk.snapshot = snapshot;
                self.compact_target = None;
            }
        }
    }

    fn install(&mut self, snapshot: Snapshot) {
        if snapshot.index <= self.disk.commit { return; }
        let suffix = if term_at(&self.disk, snapshot.index) == Some(snapshot.term) {
            self.disk.log[(snapshot.index - self.disk.snapshot.index) as usize..].to_vec()
        } else { Vec::new() };
        self.disk.commit = snapshot.index;
        self.disk.snapshot = snapshot;
        self.disk.log = suffix;
        self.disk.incoming = None;
    }

    fn start_read(&mut self, request: String, key: String, scan: Option<(String, Span, Option<String>, usize)>, out: &mut Vec<Action>) {
        if self.role != "leader" { out.push(Action::Retry { request }); return; }
        let context = self.fresh();
        self.reads.insert(context, ReadWait { request, key, scan, acks: BTreeSet::from([self.id]), frontier: None });
        self.broadcast(Some(context), out);
    }

    fn change(&mut self, request: String, mut voters: Vec<Id>, out: &mut Vec<Action>) {
        voters.sort_unstable(); voters.dedup();
        if self.role != "leader" { out.push(Action::Retry { request }); return; }
        let committed = replay(&self.disk, self.disk.commit);
        if committed.completed.contains_key(&request) {
            self.changes.insert(request); return;
        }
        let latest = self.disk.log.iter().rev().find_map(|e| match &e.command {
            EntryKind::Joint { change, .. } | EntryKind::Final { change, .. } => Some(change.clone()),
            _ => None,
        }).or_else(|| self.disk.snapshot.pending_change.clone());
        let c = configuration(&self.disk);
        let durable_config = replay(&self.stable, self.stable.commit).config;
        if c.new.is_some() || c != durable_config || committed.pending_change.is_some() {
            if latest.as_ref() == Some(&request) { self.changes.insert(request); }
            else { out.push(Action::Retry { request }); }
            return;
        }
        if voters.is_empty() || voters.iter().any(|id| !self.peers.contains(id)) {
            out.push(Action::Retry { request }); return;
        }
        self.changes.insert(request.clone());
        self.append(EntryKind::Joint { change: request, old: c.old, new: voters });
        self.broadcast(None, out);
    }

    fn receive(&mut self, from: Id, message: Message, out: &mut Vec<Action>) {
        if from == self.id || !self.peers.contains(&from) { return; }
        // Obsolete removed voters cannot raise the term of a fresher cluster.
        // A fresher log may, however, carry this candidate's promotion.
        if let Message::Vote { last_index, last_term, .. } = &message {
            if !members(&configuration(&self.disk)).contains(&from) &&
                (*last_term, *last_index) <= (term_at(&self.disk, last(&self.disk)).unwrap(), last(&self.disk)) { return; }
        }
        if message.term() > self.disk.term { self.follow(message.term()); }
        if message.term() < self.disk.term {
            match message {
                Message::Vote { .. } => Self::send(out, from, Message::VoteReply { term: self.disk.term, granted: false }),
                Message::Append { rpc, read, .. } => Self::send(out, from, Message::AppendReply {
                    term: self.disk.term, rpc, success: false, index: 0, hint: last(&self.disk) + 1, read }),
                Message::SnapshotChunk { rpc, manifest, .. } => Self::send(out, from, Message::ChunkReply {
                    term: self.disk.term, rpc, manifest, received: 0, installed: false }),
                Message::Snapshot { rpc, .. } => Self::send(out, from, Message::SnapshotReply {
                    term: self.disk.term, rpc, index: self.disk.snapshot.index }),
                _ => (),
            }
            return;
        }
        match message {
            Message::Vote { last_index, last_term, .. } => {
                let fresh = (last_term, last_index) >= (term_at(&self.disk, last(&self.disk)).unwrap(), last(&self.disk));
                let granted = fresh && self.disk.voted_for.map_or(true, |id| id == from);
                if granted { self.disk.voted_for = Some(from); }
                Self::send(out, from, Message::VoteReply { term: self.disk.term, granted });
            }
            Message::VoteReply { granted, .. } => {
                if self.role == "candidate" && granted {
                    self.votes.insert(from);
                    if quorum(&configuration(&self.disk), &self.votes) { self.lead(out); }
                }
            }
            Message::Append { rpc, prev_index, prev_term, entries, commit, read, .. } => {
                if self.role != "follower" { self.follow(self.disk.term); }
                self.leader = Some(from);
                if term_at(&self.disk, prev_index) != Some(prev_term) {
                    let hint = if prev_index < self.disk.snapshot.index { self.disk.snapshot.index + 1 }
                        else if prev_index > last(&self.disk) { last(&self.disk) + 1 }
                        else { prev_index.max(1) };
                    Self::send(out, from, Message::AppendReply { term: self.disk.term, rpc,
                        success: false, index: 0, hint, read });
                    return;
                }
                let established = prev_index + entries.len() as u64;
                for (offset, entry) in entries.into_iter().enumerate() {
                    let index = prev_index + offset as u64 + 1;
                    if term_at(&self.disk, index) != Some(entry.term) {
                        // Authenticated Raft histories cannot conflict with a
                        // committed entry. Never truncate an established prefix.
                        if index <= self.disk.commit { return; }
                        self.disk.log.truncate((index - self.disk.snapshot.index - 1) as usize);
                        self.disk.log.push(entry);
                    }
                }
                self.disk.commit = self.disk.commit.max(commit.min(established));
                Self::send(out, from, Message::AppendReply { term: self.disk.term, rpc,
                    success: true, index: established, hint: established + 1, read });
            }
            Message::AppendReply { rpc, success, index, hint, read, .. } => {
                if self.role != "leader" { return; }
                let Some(flight) = self.flights.remove(&(from, rpc)) else { return; };
                if flight.manifest.is_some() { return; }
                if success {
                    let acknowledged = index.min(flight.end);
                    let old = self.matched.get(&from).copied().unwrap_or(0);
                    self.matched.insert(from, old.max(acknowledged));
                    let next = self.next.get(&from).copied().unwrap_or(1).max(acknowledged + 1);
                    self.next.insert(from, next);
                    if index >= flight.end && read == flight.read {
                        if let Some(wait) = read.and_then(|ctx| self.reads.get_mut(&ctx)) { wait.acks.insert(from); }
                    }
                    if acknowledged > old && self.matched[&from] < last(&self.disk) { self.replicate(from, read, out); }
                } else {
                    let next = self.next.get(&from).copied().unwrap_or(last(&self.disk) + 1);
                    let floor = self.matched.get(&from).copied().unwrap_or(0) + 1;
                    // A failure for an older probe cannot undo newer progress.
                    if flight.start == next && floor <= next {
                        let target = hint.max(floor).min(last(&self.disk) + 1);
                        let target = if target == next { next.saturating_sub(1).max(floor) } else { target };
                        if target != next {
                            self.next.insert(from, target);
                            self.replicate(from, flight.read, out);
                        }
                    }
                }
            }
            Message::SnapshotChunk { rpc, manifest, offset, data, .. } => {
                if self.role != "follower" { self.follow(self.disk.term); }
                self.leader = Some(from);
                if !transfer::valid_fragment(&manifest, offset, &data) {
                    Self::send(out, from, Message::ChunkReply { term: self.disk.term, rpc, manifest, received: 0, installed: false });
                    return;
                }
                if manifest.index <= self.disk.commit {
                    if self.disk.incoming.as_ref().is_some_and(|i| i.manifest == manifest) { self.disk.incoming = None; }
                    Self::send(out, from, Message::ChunkReply { term: self.disk.term, rpc,
                        received: manifest.size, installed: true, manifest });
                    return;
                }
                let (received, snapshot) = transfer::receive(&mut self.disk, &manifest, offset, &data);
                let installed = snapshot.is_some();
                if let Some(snapshot) = snapshot { self.install(snapshot); }
                Self::send(out, from, Message::ChunkReply { term: self.disk.term, rpc, manifest, received, installed });
            }
            Message::ChunkReply { rpc, manifest, received, installed, .. } => {
                if self.role != "leader" { return; }
                let Some(flight) = self.flights.remove(&(from, rpc)) else { return; };
                if flight.manifest.as_ref() != Some(&manifest) || received > manifest.size { return; }
                let (current, _) = wire::encode(&self.disk.snapshot);
                if current != manifest { self.replicate(from, flight.read, out); return; }
                if installed && received == manifest.size {
                    let old = self.matched.get(&from).copied().unwrap_or(0);
                    if old >= manifest.index { return; }
                    self.matched.insert(from, old.max(manifest.index));
                    let next = self.next.get(&from).copied().unwrap_or(1).max(manifest.index + 1);
                    self.next.insert(from, next);
                    self.transfers.remove(&from);
                    self.chunk_limits.remove(&from);
                    // Installation does not acknowledge a read context. Send
                    // fresh append probes, without waiting for another tick.
                    self.replicate(from, None, out);
                    self.confirm_reads(from, out);
                } else if !installed && received < manifest.size {
                    let current = self.transfers.get(&from).filter(|(m, _)| *m == manifest).map(|(_, n)| *n);
                    if current == Some(flight.offset) {
                        self.transfers.insert(from, (manifest, received));
                        if received != flight.offset {
                            self.chunk_limits.remove(&from);
                            self.replicate(from, flight.read, out);
                        } else if flight.length > 1 {
                            // An out-of-order corrupt piece can conflict with
                            // a large retry before byte zero is covered. Fill
                            // the gaps using smaller bounded probes; completion
                            // will then reject the checksum and allow a clean
                            // restart. Never erase accepted bytes to do this.
                            self.chunk_limits.insert(from, flight.length / 2);
                            self.replicate(from, flight.read, out);
                        }
                    }
                }
            }
            Message::Snapshot { rpc, snapshot, .. } => {
                if self.role != "follower" { self.follow(self.disk.term); }
                self.leader = Some(from);
                let index = snapshot.index;
                self.install(snapshot);
                Self::send(out, from, Message::SnapshotReply { term: self.disk.term, rpc, index });
            }
            Message::SnapshotReply { .. } => (), // We only send chunk snapshots.
        }
    }

    fn send_ready(&self, dependency: u64, to: Id, message: &Message) -> bool {
        if dependency > self.durable || message.term() > self.stable.term { return false; }
        match message {
            Message::VoteReply { granted: true, .. } => self.stable.voted_for == Some(to),
            Message::AppendReply { success: true, index, .. } => {
                self.stable.commit >= *index ||
                    (term_at(&self.stable, *index).is_some() && term_at(&self.stable, *index) == term_at(&self.disk, *index))
            }
            Message::ChunkReply { manifest, installed: true, .. } => self.stable.commit >= manifest.index,
            Message::SnapshotReply { index, .. } => self.stable.commit >= *index,
            _ => true,
        }
    }

    pub fn step(&mut self, event: Event) -> Vec<Action> {
        let mut effects = Vec::new();
        match event {
            Event::Durable { through } => {
                if through > self.durable && through <= self.generation {
                    if let Some(disk) = self.storage.get(&through) {
                        self.stable = disk.clone();
                        self.durable = through;
                        self.storage.retain(|g, _| *g > through);
                    }
                }
            }
            Event::Applied { through } => {
                if through > self.apply_done && through <= self.apply_generation {
                    if let Some(state) = self.applications.get(&through) {
                        self.applied = state.clone();
                        self.apply_done = through;
                        self.applications.retain(|g, _| *g > through);
                    }
                }
            }
            Event::Campaign => {
                if members(&configuration(&self.disk)).contains(&self.id) {
                    self.follow(self.disk.term + 1);
                    self.role = "candidate".into();
                    self.disk.voted_for = Some(self.id);
                    self.votes.insert(self.id);
                    for peer in members(&configuration(&self.disk)) {
                        if peer != self.id { Self::send(&mut effects, peer, Message::Vote { term: self.disk.term,
                            last_index: last(&self.disk), last_term: term_at(&self.disk, last(&self.disk)).unwrap() }); }
                    }
                    if quorum(&configuration(&self.disk), &self.votes) { self.lead(&mut effects); }
                }
            }
            Event::Tick => {
                if self.role == "leader" {
                    self.broadcast(None, &mut effects);
                    for ctx in self.reads.keys().copied().collect::<Vec<_>>() { self.broadcast(Some(ctx), &mut effects); }
                }
            }
            Event::Inspect => (),
            Event::Compact => {
                self.compact_target = Some(self.compact_target.unwrap_or(0).max(self.disk.commit));
            }
            Event::Receive { from, message } => self.receive(from, message, &mut effects),
            Event::Write { request, client, seq, key, delta, expect } => {
                if self.role != "leader" { effects.push(Action::Retry { request }); }
                else {
                    let index = self.append(EntryKind::Write { client, seq, key, delta, expect });
                    self.writes.entry(index).or_default().push(request);
                    self.broadcast(None, &mut effects);
                }
            }
            Event::Transaction { request, operation } => {
                if self.role != "leader" { effects.push(Action::Retry { request }); }
                else {
                    let index = self.append(EntryKind::Transaction { operation });
                    self.transactions.entry(index).or_default().push(request);
                    self.broadcast(None, &mut effects);
                }
            }
            Event::Read { request, key } => self.start_read(request, key, None, &mut effects),
            Event::Scan { request, txn, range, after, limit } => self.start_read(request, String::new(), Some((txn, range, after, limit)), &mut effects),
            Event::Change { request, voters } => self.change(request, voters, &mut effects),
        }
        self.progress(&mut effects);
        let mut out = Vec::new();
        if self.disk != self.submitted {
            self.generation += 1;
            self.submitted = self.disk.clone();
            self.storage.insert(self.generation, self.submitted.clone());
            out.push(Action::Persist { generation: self.generation, disk: self.submitted.clone() });
        }
        for effect in effects {
            match effect {
                Action::Send { to, message } => {
                    // Coalesce probes that have never escaped storage gating.
                    // Otherwise a long fsync stall followed by one callback
                    // could emit thousands of obsolete copies of the suffix.
                    let probe = |m: &Message| match m {
                        Message::Append { read, .. } => Some(*read),
                        Message::SnapshotChunk { .. } => Some(None),
                        _ => None,
                    };
                    if let Some(context) = probe(&message) {
                        let mut removed = Vec::new();
                        self.sends.retain(|(g, dest, m)| {
                            let keep = (*g <= self.durable && m.term() <= self.stable.term) || *dest != to || m.term() != message.term() || probe(m) != Some(context);
                            if !keep {
                                match m {
                                    Message::Append { rpc, .. } | Message::SnapshotChunk { rpc, .. } => removed.push((*dest, *rpc)),
                                    _ => (),
                                }
                            }
                            keep
                        });
                        for key in removed { self.flights.remove(&key); }
                    }
                    // Sending staged entries does not claim they are durable.
                    // A leader can replicate while its own worker is behind.
                    let dependency = match &message {
                        Message::ChunkReply { manifest, received, installed: false, .. }
                            if *received > durable_coverage(&self.stable, manifest) => self.generation,
                        _ => 0,
                    };
                    self.sends.push((dependency, to, message));
                },
                other => out.push(other),
            }
        }
        let sends = std::mem::take(&mut self.sends);
        for (dependency, to, mut message) in sends {
            if message.term() != self.disk.term { continue; }
            if self.send_ready(dependency, to, &message) {
                if let Message::ChunkReply { manifest, received, installed, .. } = &mut message {
                    if !*installed && self.stable.commit < manifest.index {
                        // A newer fsynced image may have replaced this transfer
                        // or discarded a completed, corrupt byte stream.
                        let prefix = durable_coverage(&self.stable, manifest);
                        *received = (*received).min(prefix);
                    }
                }
                out.push(Action::Send { to, message });
            }
            else { self.sends.push((dependency, to, message)); }
        }
        out
    }
}
