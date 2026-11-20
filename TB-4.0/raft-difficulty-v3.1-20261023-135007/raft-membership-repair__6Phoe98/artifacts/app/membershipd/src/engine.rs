use crate::{mvcc, transfer, types::*, wire};
use std::collections::{BTreeMap, BTreeSet};

fn members(c: &Config) -> BTreeSet<Id> {
    c.old.iter().chain(c.new.iter().flatten()).copied().collect()
}
fn quorum(c: &Config, ids: &BTreeSet<Id>) -> bool {
    let majority = |v: &[Id]| v.iter().filter(|id| ids.contains(id)).count() > v.len() / 2;
    majority(&c.old) && c.new.as_ref().map_or(true, |v| majority(v))
}
fn last(d: &Disk) -> u64 { d.snapshot.index + d.log.len() as u64 }
fn term_at(d: &Disk, i: u64) -> Option<u64> {
    if i == d.snapshot.index { Some(d.snapshot.term) }
    else if i > d.snapshot.index { d.log.get((i-d.snapshot.index-1) as usize).map(|e| e.term) }
    else if i == 0 { Some(0) } else { None }
}
fn config(d: &Disk) -> Config {
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
struct Replay {
    state: Snapshot,
    writes: BTreeMap<u64, Result<Session, u64>>,
    transactions: BTreeMap<u64, TransactionResult>,
}
fn replay(d: &Disk, through: u64) -> Replay {
    assert!(through >= d.snapshot.index && through <= last(d));
    let mut r = Replay { state: d.snapshot.clone(), writes: BTreeMap::new(), transactions: BTreeMap::new() };
    for (offset, e) in d.log.iter().enumerate() {
        let index = d.snapshot.index + offset as u64 + 1;
        if index > through { break; }
        let s = &mut r.state;
        match &e.command {
            EntryKind::Noop => (),
            EntryKind::Write { client, seq, key, delta, expect } => {
                let result = match s.sessions.get(client) {
                    Some(old) if *seq < old.seq => Err(old.seq),
                    Some(old) if *seq == old.seq => Ok(old.clone()),
                    _ => {
                        let before = *s.values.get(key).unwrap_or(&0);
                        let applied = expect.map_or(true, |v| v == before);
                        let value = if applied { before + delta } else { before };
                        if applied {
                            s.values.insert(key.clone(), value);
                            mvcc::record(&mut s.mvcc, key, Some(value), index);
                        }
                        let result = Session { seq: *seq, value, applied };
                        s.sessions.insert(client.clone(), result.clone());
                        Ok(result)
                    }
                };
                r.writes.insert(index, result);
            }
            EntryKind::Transaction { operation } => {
                r.transactions.insert(index, mvcc::apply(&mut s.mvcc, &mut s.values, operation, index));
            }
            EntryKind::Joint { change, old, new } => {
                s.config = Config { old: old.clone(), new: Some(new.clone()) };
                s.pending_change = Some(change.clone());
            }
            EntryKind::Final { change, voters } => {
                s.config = Config { old: voters.clone(), new: None };
                s.completed.insert(change.clone(), voters.clone());
                s.pending_change = None;
            }
        }
        s.index = index; s.term = e.term;
    }
    r
}

#[derive(Clone)]
enum Query {
    Read { request: String, key: String },
    Scan { request: String, txn: String, range: Span, after: Option<String>, limit: usize },
}
struct ReadWait { query: Query, config: Config, acks: BTreeSet<Id> }
struct Flight {
    end: u64,
    prev: u64,
    read: Option<u64>,
    manifest: Option<Manifest>,
}
// A dependency is a submitted storage generation, not the diagnostic Disk.
// Storage promises created during an event are bound to its final image.
struct Deferred { storage: u64, application: u64, term: Option<u64>, action: Action }

pub struct Node {
    id: Id, peers: Vec<Id>, disk: Disk,
    role: String, leader: Option<Id>,
    votes: BTreeSet<Id>, next: BTreeMap<Id, u64>, matched: BTreeMap<Id, u64>,
    serial: u64, flights: BTreeMap<(Id, u64), Flight>, latest_rpc: BTreeMap<Id, u64>,
    transfers: BTreeMap<Id, (Manifest, u64)>, reads: BTreeMap<u64, ReadWait>,
    writes: BTreeMap<u64, Vec<String>>, transactions: BTreeMap<u64, Vec<String>>,
    changes: BTreeSet<String>, deferred: Vec<Deferred>, compact: BTreeSet<u64>,
    generation: u64, durable_generation: u64, submitted: Disk, durable: Disk,
    storage_jobs: BTreeMap<u64, Disk>,
    apply_generation: u64, apply_done: u64, apply_index: u64,
    application_jobs: BTreeMap<u64, Snapshot>, applied: Snapshot,
}
impl Node {
    pub fn new(id: Id, peers: Vec<Id>, disk: Disk) -> Self {
        Self {
            id, peers, role: "follower".into(), leader: None,
            applied: disk.snapshot.clone(), apply_index: disk.snapshot.index,
            submitted: disk.clone(), durable: disk.clone(), disk,
            votes: BTreeSet::new(), next: BTreeMap::new(), matched: BTreeMap::new(),
            serial: 0, flights: BTreeMap::new(), latest_rpc: BTreeMap::new(), transfers: BTreeMap::new(),
            reads: BTreeMap::new(), writes: BTreeMap::new(), transactions: BTreeMap::new(),
            changes: BTreeSet::new(), deferred: Vec::new(), compact: BTreeSet::new(),
            generation: 0, durable_generation: 0, storage_jobs: BTreeMap::new(),
            apply_generation: 0, apply_done: 0, application_jobs: BTreeMap::new(),
        }
    }
    pub fn disk(&self) -> Disk { self.disk.clone() }
    pub fn status(&self) -> Status {
        Status {
            durable_generation: self.durable_generation, apply_generation: self.apply_done,
            applied_index: self.applied.index, role: self.role.clone(), leader: self.leader,
            config: config(&self.disk), values: self.applied.values.clone(),
            sessions: self.applied.sessions.clone(), mvcc: self.applied.mvcc.clone(),
        }
    }
    fn fresh(&mut self) -> u64 { self.serial += 1; self.serial }
    fn send(&mut self, to: Id, message: Message) {
        let term = message.term();
        // Requests may pipeline replication with local storage. Only term
        // durability is needed to send them; self never counts unfsynced data
        // toward commitment. A positive reply, in contrast, is a storage promise.
        let promises_data = match &message {
            Message::VoteReply { granted, .. } => *granted,
            Message::AppendReply { success, .. } => *success,
            Message::ChunkReply { received, installed, .. } => *installed || *received > 0,
            Message::SnapshotReply { index, .. } => *index > 0,
            _ => false,
        };
        let storage = if promises_data { u64::MAX }
            else if term <= self.durable.term { 0 }
            else { self.storage_jobs.iter().find(|(_, d)| d.term >= term)
                .map_or(u64::MAX, |(g, _)| *g) };
        self.deferred.push(Deferred { storage, application: 0, term: Some(term), action: Action::Send { to, message } });
    }
    fn result(&mut self, application: u64, action: Action) {
        // Completion of this application frontier implies storage completion of
        // the immutable committed prefix, even if newer storage jobs stall.
        self.deferred.push(Deferred { storage: 0, application, term: None, action });
    }
    fn follow(&mut self, term: u64) {
        if term > self.disk.term { self.disk.term = term; self.disk.voted_for = None; }
        self.role = "follower".into(); self.leader = None;
        self.votes.clear(); self.reads.clear(); self.writes.clear(); self.transactions.clear(); self.changes.clear();
        self.flights.clear(); self.latest_rpc.clear(); self.transfers.clear();
    }
    fn append(&mut self, command: EntryKind) -> u64 {
        self.disk.log.push(Entry { term: self.disk.term, command }); last(&self.disk)
    }
    fn replicate(&mut self, to: Id, read: Option<u64>) {
        // Do not strand all fresh log holders in a configuration that excludes
        // them. Until the new quorum durably establishes Final, keep retired
        // peers on the joint configuration, where they can still recover an
        // interrupted transition. Commit advancement below uses durable matches,
        // so a staged commit is already sufficient to release this restriction.
        if self.disk.log.iter().enumerate().any(|(offset, entry)| {
            self.disk.snapshot.index + offset as u64 + 1 > self.disk.commit &&
                matches!(&entry.command, EntryKind::Final { voters, .. } if !voters.contains(&to))
        }) { return; }
        let next = self.next.get(&to).copied().unwrap_or(last(&self.disk)+1).min(last(&self.disk)+1).max(1);
        let rpc = self.fresh();
        let (message, flight) = if next <= self.disk.snapshot.index {
            let (manifest, bytes) = wire::encode(&self.disk.snapshot);
            let offset = self.transfers.get(&to).filter(|(m, _)| m == &manifest).map_or(0, |(_, n)| *n);
            let offset = if offset < manifest.size { offset } else { 0 };
            let end = (offset + wire::MAX_CHUNK as u64).min(manifest.size);
            let data = bytes[offset as usize..end as usize].to_vec();
            self.transfers.insert(to, (manifest.clone(), offset));
            (Message::SnapshotChunk { term: self.disk.term, rpc, manifest: manifest.clone(), offset, data },
             Flight { end: manifest.index, prev: 0, read, manifest: Some(manifest) })
        } else {
            let prev = next-1;
            let entries = self.disk.log[(next-self.disk.snapshot.index-1) as usize..].to_vec();
            (Message::Append { term: self.disk.term, rpc, prev_index: prev,
                prev_term: term_at(&self.disk, prev).unwrap(), entries, commit: self.disk.commit, read },
             Flight { end: last(&self.disk), prev, read, manifest: None })
        };
        self.flights.insert((to, rpc), flight); self.latest_rpc.insert(to, rpc);
        self.send(to, message);
    }
    fn broadcast(&mut self, read: Option<u64>) {
        for to in self.peers.clone() { if to != self.id { self.replicate(to, read); } }
    }
    fn lead(&mut self) {
        self.role = "leader".into(); self.leader = Some(self.id);
        self.next = self.peers.iter().map(|p| (*p, last(&self.disk)+1)).collect();
        self.matched = self.peers.iter().map(|p| (*p, 0)).collect();
        self.flights.clear(); self.latest_rpc.clear(); self.transfers.clear();
        self.append(EntryKind::Noop);
        self.broadcast(None);
    }
    fn start_read(&mut self, query: Query) {
        let context = self.fresh();
        self.reads.insert(context, ReadWait { query, config: config(&self.disk), acks: BTreeSet::new() });
        self.broadcast(Some(context));
    }
    fn refresh_reads(&mut self) {
        let c = config(&self.disk);
        let stale: Vec<_> = self.reads.iter().filter(|(_, r)| r.config != c).map(|(n, _)| *n).collect();
        for n in stale {
            let query = self.reads.remove(&n).unwrap().query;
            // New contexts make pre-transition replies unusable, including replies
            // from packets already queued in the transport.
            self.start_read(query);
        }
    }
    fn install(&mut self, snapshot: Snapshot) {
        if snapshot.index <= self.disk.commit { return; }
        let keep = term_at(&self.disk, snapshot.index) == Some(snapshot.term);
        if keep {
            let count = (snapshot.index-self.disk.snapshot.index) as usize;
            self.disk.log.drain(..count);
        } else { self.disk.log.clear(); }
        self.disk.commit = snapshot.index;
        self.disk.snapshot = snapshot;
        self.disk.incoming = None;
    }
    fn receive(&mut self, from: Id, message: Message) {
        // A retired voter with an obsolete log must not continually raise the
        // new cluster's term. A fresher candidate may have logged its promotion.
        if let Message::Vote { last_index, last_term, .. } = &message {
            if !members(&config(&self.disk)).contains(&from) &&
                (*last_term, *last_index) <= (term_at(&self.disk, last(&self.disk)).unwrap(), last(&self.disk)) {
                return;
            }
        }
        if message.term() > self.disk.term { self.follow(message.term()); }
        if message.term() < self.disk.term {
            match message {
                Message::Vote { .. } => self.send(from, Message::VoteReply { term: self.disk.term, granted: false }),
                Message::Append { rpc, read, .. } => self.send(from, Message::AppendReply {
                    term: self.disk.term, rpc, success: false, index: 0, hint: last(&self.disk)+1, read }),
                Message::SnapshotChunk { rpc, manifest, .. } => self.send(from, Message::ChunkReply {
                    term: self.disk.term, rpc, manifest, received: 0, installed: false }),
                Message::Snapshot { rpc, .. } => self.send(from, Message::SnapshotReply { term: self.disk.term, rpc, index: 0 }),
                _ => (),
            }
            return;
        }
        match message {
            Message::Vote { last_index, last_term, .. } => {
                let fresh = (last_term, last_index) >= (term_at(&self.disk, last(&self.disk)).unwrap(), last(&self.disk));
                let granted = fresh && self.disk.voted_for.map_or(true, |v| v == from);
                if granted { self.disk.voted_for = Some(from); }
                self.send(from, Message::VoteReply { term: self.disk.term, granted });
            }
            Message::VoteReply { granted, .. } => {
                if self.role == "candidate" && granted {
                    self.votes.insert(from);
                    if quorum(&config(&self.disk), &self.votes) { self.lead(); }
                }
            }
            Message::Append { rpc, prev_index, prev_term, entries, commit, read, .. } => {
                if self.role != "follower" { self.follow(self.disk.term); }
                self.leader = Some(from);
                if term_at(&self.disk, prev_index) != Some(prev_term) || prev_index < self.disk.snapshot.index {
                    let hint = if prev_index < self.disk.snapshot.index { self.disk.snapshot.index+1 }
                        else if prev_index > last(&self.disk) { last(&self.disk)+1 }
                        else {
                            let t = term_at(&self.disk, prev_index);
                            let mut first = prev_index;
                            while first > self.disk.snapshot.index+1 && term_at(&self.disk, first-1) == t { first -= 1; }
                            first
                        };
                    self.send(from, Message::AppendReply { term: self.disk.term, rpc, success: false, index: 0, hint, read });
                    return;
                }
                let end = prev_index + entries.len() as u64;
                for (offset, e) in entries.iter().enumerate() {
                    let i = prev_index + offset as u64 + 1;
                    let pos = (i-self.disk.snapshot.index-1) as usize;
                    if self.disk.log.get(pos) == Some(e) { continue; }
                    if i <= self.disk.commit {
                        self.send(from, Message::AppendReply { term: self.disk.term, rpc, success: false,
                            index: 0, hint: self.disk.commit+1, read });
                        return;
                    }
                    self.disk.log.truncate(pos);
                    self.disk.log.extend_from_slice(&entries[offset..]);
                    break;
                }
                self.disk.commit = self.disk.commit.max(commit.min(end));
                self.send(from, Message::AppendReply { term: self.disk.term, rpc, success: true, index: end, hint: end+1, read });
            }
            Message::AppendReply { rpc, success, index, hint, read, .. } => {
                if self.role != "leader" { return; }
                let Some(f) = self.flights.remove(&(from, rpc)) else { return; };
                if f.manifest.is_some() { return; }
                if success {
                    if index != f.end || read != f.read { return; }
                    self.matched.entry(from).and_modify(|m| *m = (*m).max(index)).or_insert(index);
                    self.next.entry(from).and_modify(|n| *n = (*n).max(index+1)).or_insert(index+1);
                    if let Some(context) = f.read {
                        if let Some(wait) = self.reads.get_mut(&context) { wait.acks.insert(from); }
                    }
                    if self.next.get(&from).copied().unwrap_or(1) <= last(&self.disk) { self.replicate(from, None); }
                } else if self.latest_rpc.get(&from) == Some(&rpc) {
                    let matched = self.matched.get(&from).copied().unwrap_or(0);
                    let current = self.next.get(&from).copied().unwrap_or(f.prev+1);
                    if f.end > matched || f.prev >= matched {
                        let n = if hint > current { hint.min(last(&self.disk)+1) }
                            else { hint.min(current.saturating_sub(1)).max(1) };
                        self.next.insert(from, n.max(matched+1));
                        self.replicate(from, f.read);
                    }
                }
            }
            Message::SnapshotChunk { rpc, manifest, offset, data, .. } => {
                if self.role != "follower" { self.follow(self.disk.term); }
                self.leader = Some(from);
                let valid = manifest.size > 0 && manifest.size <= wire::MAX_SNAPSHOT as u64 &&
                    !data.is_empty() && data.len() <= wire::MAX_CHUNK && offset <= manifest.size &&
                    data.len() as u64 <= manifest.size-offset;
                let (received, installed) = if !valid { (0, false) }
                    else if manifest.index <= self.disk.commit { (manifest.size, true) }
                    else {
                        let (received, complete) = transfer::receive(&mut self.disk, &manifest, offset, &data);
                        if let Some(snapshot) = complete { self.install(snapshot); (manifest.size, true) }
                        else { (received, false) }
                    };
                self.send(from, Message::ChunkReply { term: self.disk.term, rpc, manifest, received, installed });
            }
            Message::ChunkReply { rpc, manifest, received, installed, .. } => {
                if self.role != "leader" { return; }
                let Some(f) = self.flights.remove(&(from, rpc)) else { return; };
                if f.manifest.as_ref() != Some(&manifest) || received > manifest.size { return; }
                let (current, _) = wire::encode(&self.disk.snapshot);
                if current != manifest { return; }
                if installed && received == manifest.size {
                    self.matched.entry(from).and_modify(|n| *n = (*n).max(manifest.index)).or_insert(manifest.index);
                    self.next.entry(from).and_modify(|n| *n = (*n).max(manifest.index+1)).or_insert(manifest.index+1);
                    self.transfers.remove(&from);
                    self.replicate(from, f.read);
                } else if !installed && self.latest_rpc.get(&from) == Some(&rpc) {
                    self.transfers.insert(from, (manifest, received));
                    self.replicate(from, f.read);
                }
            }
            Message::Snapshot { rpc, snapshot, .. } => {
                if self.role != "follower" { self.follow(self.disk.term); }
                self.leader = Some(from);
                let index = snapshot.index;
                self.install(snapshot);
                self.send(from, Message::SnapshotReply { term: self.disk.term, rpc, index });
            }
            Message::SnapshotReply { .. } => (),
        }
    }

    fn progress(&mut self, out: &mut Vec<Action>) {
        if self.role == "leader" {
            // Self is counted only for a prefix known to have reached storage.
            // Followers' match indexes come exclusively from durable replies.
            let own = last(&self.durable).min(last(&self.disk));
            if term_at(&self.disk, own) == term_at(&self.durable, own) {
                self.matched.insert(self.id, own);
            }
            let old_commit = self.disk.commit;
            let old_last = last(&self.disk);
            let c = config(&self.disk);
            for i in (self.disk.commit+1..=last(&self.disk)).rev() {
                if term_at(&self.disk, i) != Some(self.disk.term) { continue; }
                let ids = self.matched.iter().filter(|(_, n)| **n >= i).map(|(p, _)| *p).collect();
                if quorum(&c, &ids) { self.disk.commit = i; break; }
            }
            // Finalization is consensus work, not application work. It must run
            // even while the external application worker is stopped.
            let committed = replay(&self.durable, self.durable.commit).state;
            if let (Some(change), Some(new)) = (&committed.pending_change, &committed.config.new) {
                let c = config(&self.disk);
                if c.new.is_some() && !self.disk.log.iter().any(|e|
                    matches!(&e.command, EntryKind::Final { change: id, .. } if id == change)) {
                    self.append(EntryKind::Final { change: change.clone(), voters: new.clone() });
                }
            }
            self.refresh_reads();
            if old_commit != self.disk.commit || old_last != last(&self.disk) { self.broadcast(None); }
        }

        // Capture per-entry results before a completed checkpoint permits log
        // reclamation. Results stay tied to their original entry, not the latest
        // client session or the latest receipt observed by a retry.
        let committed = replay(&self.durable, self.durable.commit);
        let indexes: Vec<_> = self.writes.keys().copied().filter(|i| *i <= self.durable.commit).collect();
        for index in indexes {
            let requests = self.writes.remove(&index).unwrap();
            if let Some(result) = committed.writes.get(&index) {
                for request in requests {
                    let action = match result {
                        Ok(s) => Action::Wrote { request, seq: s.seq, value: s.value, applied: s.applied },
                        Err(last_seq) => Action::Stale { request, last_seq: *last_seq },
                    };
                    self.result(index, action);
                }
            }
        }
        let indexes: Vec<_> = self.transactions.keys().copied().filter(|i| *i <= self.durable.commit).collect();
        for index in indexes {
            let requests = self.transactions.remove(&index).unwrap();
            if let Some(result) = committed.transactions.get(&index) {
                for request in requests { self.result(index, Action::Transaction { request, result: result.clone() }); }
            }
        }
        for request in self.changes.clone() {
            if let Some(voters) = committed.state.completed.get(&request) {
                self.changes.remove(&request);
                self.result(self.durable.commit, Action::Changed { request, voters: voters.clone() });
            }
        }

        if self.role == "leader" && term_at(&self.durable, self.durable.commit) == Some(self.disk.term) {
            let c = config(&self.disk);
            let mut ready = Vec::new();
            for (context, wait) in &mut self.reads {
                if self.durable.term == self.disk.term { wait.acks.insert(self.id); }
                if wait.config == c && quorum(&c, &wait.acks) { ready.push(*context); }
            }
            for context in ready {
                let wait = self.reads.remove(&context).unwrap();
                let frontier = self.disk.commit;
                let state = replay(&self.disk, frontier).state;
                let action = match wait.query {
                    Query::Read { request, key } => Action::Read { request, value: *state.values.get(&key).unwrap_or(&0) },
                    Query::Scan { request, txn, range, after, limit } => {
                        match mvcc::scan(&state.mvcc, &txn, &range, after.as_deref(), limit) {
                            Ok(page) => Action::Scan { request, txn, page },
                            Err(result) => Action::Transaction { request, result },
                        }
                    }
                };
                self.result(frontier, action);
            }
        }

        if self.role == "leader" && committed.state.config.new.is_none() &&
            committed.state.config == config(&self.disk) && !members(&committed.state.config).contains(&self.id) {
            // Propagate the final durable commit before retiring. Already-safe
            // results may finish their application dependency after retirement.
            self.broadcast(None);
            self.follow(self.disk.term);
        }
        if self.durable.commit > self.apply_index {
            self.apply_generation += 1;
            self.apply_index = self.durable.commit;
            let state = committed.state;
            self.application_jobs.insert(self.apply_generation, state.clone());
            out.push(Action::Apply { generation: self.apply_generation, state });
        }
        self.compact.retain(|target| *target > self.disk.snapshot.index);
        let target = self.compact.iter().copied().filter(|target|
            *target <= self.durable.commit && *target <= self.applied.index).max();
        if let Some(target) = target {
            let state = replay(&self.disk, target).state;
            let count = (target-self.disk.snapshot.index) as usize;
            self.disk.log.drain(..count);
            self.disk.snapshot = state;
            self.compact.retain(|pending| *pending > target);
        }
    }

    pub fn step(&mut self, event: Event) -> Vec<Action> {
        let mut out = Vec::new();
        match event {
            Event::Durable { through } => {
                if through > self.durable_generation {
                    if let Some(disk) = self.storage_jobs.get(&through) {
                        self.durable = disk.clone(); self.durable_generation = through;
                        self.storage_jobs.retain(|g, _| *g > through);
                    }
                }
            }
            Event::Applied { through } => {
                if through > self.apply_done {
                    if let Some(state) = self.application_jobs.get(&through) {
                        self.applied = state.clone(); self.apply_done = through;
                        self.application_jobs.retain(|g, _| *g > through);
                    }
                }
            }
            Event::Inspect => (),
            Event::Campaign => {
                let c = config(&self.disk);
                if members(&c).contains(&self.id) {
                    self.follow(self.disk.term+1);
                    self.role = "candidate".into(); self.disk.voted_for = Some(self.id);
                    self.votes.insert(self.id);
                    for to in members(&c) {
                        if to != self.id { self.send(to, Message::Vote { term: self.disk.term,
                            last_index: last(&self.disk), last_term: term_at(&self.disk, last(&self.disk)).unwrap() }); }
                    }
                    if quorum(&c, &self.votes) { self.lead(); }
                }
            }
            Event::Tick => {
                if self.role == "leader" {
                    self.broadcast(None);
                    for context in self.reads.keys().copied().collect::<Vec<_>>() { self.broadcast(Some(context)); }
                }
            }
            Event::Receive { from, message } => self.receive(from, message),
            Event::Write { request, client, seq, key, delta, expect } => {
                if self.role != "leader" { out.push(Action::Retry { request }); }
                else {
                    let index = self.append(EntryKind::Write { client, seq, key, delta, expect });
                    self.writes.entry(index).or_default().push(request);
                    self.broadcast(None);
                }
            }
            Event::Transaction { request, operation } => {
                if self.role != "leader" { out.push(Action::Retry { request }); }
                else {
                    let index = self.append(EntryKind::Transaction { operation });
                    self.transactions.entry(index).or_default().push(request);
                    self.broadcast(None);
                }
            }
            Event::Read { request, key } => {
                if self.role != "leader" { out.push(Action::Retry { request }); }
                else { self.start_read(Query::Read { request, key }); }
            }
            Event::Scan { request, txn, range, after, limit } => {
                if self.role != "leader" { out.push(Action::Retry { request }); }
                else { self.start_read(Query::Scan { request, txn, range, after, limit }); }
            }
            Event::Change { request, mut voters } => {
                voters.sort_unstable(); voters.dedup();
                if self.role != "leader" || voters.is_empty() || voters.iter().any(|id| !self.peers.contains(id)) {
                    out.push(Action::Retry { request });
                } else {
                    let committed = replay(&self.durable, self.durable.commit).state;
                    if committed.completed.contains_key(&request) {
                        self.changes.insert(request);
                    } else {
                        let pending = self.disk.log.iter().rev().find_map(|e| match &e.command {
                            EntryKind::Joint { change, .. } | EntryKind::Final { change, .. } =>
                                if committed.completed.contains_key(change) { None } else { Some(change.clone()) },
                            _ => None,
                        }).or_else(|| committed.pending_change.clone());
                        if let Some(pending) = pending {
                            if pending == request { self.changes.insert(request); }
                            else { out.push(Action::Retry { request }); }
                        } else {
                            let c = config(&self.disk);
                            self.changes.insert(request.clone());
                            self.append(EntryKind::Joint { change: request, old: c.old, new: voters });
                            self.broadcast(None);
                        }
                    }
                }
            }
            Event::Compact => {
                self.compact.insert(self.disk.commit);
            }
        }
        self.progress(&mut out);
        if self.disk != self.submitted {
            self.generation += 1;
            self.submitted = self.disk.clone();
            self.storage_jobs.insert(self.generation, self.submitted.clone());
            out.push(Action::Persist { generation: self.generation, disk: self.submitted.clone() });
        }
        let mut waiting = Vec::new();
        for mut effect in self.deferred.drain(..) {
            if effect.storage == u64::MAX { effect.storage = self.generation; }
            if effect.term.is_some_and(|term| term != self.disk.term) { continue; }
            if effect.storage <= self.durable_generation && effect.application <= self.applied.index {
                out.push(effect.action);
            } else { waiting.push(effect); }
        }
        self.deferred = waiting;
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn joint_requires_both_majorities() {
        let c = Config { old: vec![1,2,3], new: Some(vec![3,4,5]) };
        assert!(!quorum(&c, &[1,2,4].into_iter().collect()));
        assert!(quorum(&c, &[1,3,4].into_iter().collect()));
    }
    #[test]
    fn recovery_and_workers_do_not_reset_or_serialize_consensus() {
        let mut disk = Disk::initial(vec![1]);
        disk.term = 2; disk.voted_for = Some(1);
        let (manifest, bytes) = wire::encode(&disk.snapshot);
        disk.incoming = Some(Incoming { manifest, pieces: [(0, bytes[..10].to_vec())].into_iter().collect() });
        let mut n = Node::new(1, vec![1,2], disk.clone());
        assert_eq!(n.disk(), disk);
        assert!(n.step(Event::Inspect).is_empty());
        let first = n.step(Event::Campaign);
        assert!(first.iter().any(|a| matches!(a, Action::Persist { generation: 1, .. })));
        assert!(!first.iter().any(|a| matches!(a, Action::Apply { .. })));
        let next = n.step(Event::Write { request: "w".into(), client: "c".into(), seq: 1, key: "k".into(), delta: 1, expect: None });
        assert!(next.iter().any(|a| matches!(a, Action::Persist { generation: 2, .. })));
        n.step(Event::Receive { from: 2, message: Message::Append { term: 4, rpc: 1,
            prev_index: 0, prev_term: 0, entries: vec![], commit: 0, read: None } });
        assert_eq!(n.status().role, "follower");
        assert_eq!(n.disk.term, 4);
        assert_eq!(n.status().applied_index, 0);
    }
    #[test]
    fn old_term_entries_cannot_directly_advance_commit() {
        let mut disk = Disk::initial(vec![1]);
        disk.term = 2; disk.log.push(Entry { term: 1, command: EntryKind::Noop });
        let mut n = Node::new(1, vec![1], disk);
        n.role = "leader".into();
        n.step(Event::Inspect);
        assert_eq!(n.disk.commit, 0);
    }
    #[test]
    fn retired_peers_learn_final_only_after_new_quorum_is_durable() {
        let mut disk = Disk::initial(vec![1,2,3]);
        disk.term = 1;
        disk.log.push(Entry { term: 1, command: EntryKind::Joint {
            change: "replace".into(), old: vec![1,2,3], new: vec![1] } });
        disk.commit = 1;
        let mut n = Node::new(1, vec![1,2,3], disk);
        n.role = "leader".into();
        // A committed joint transition is inherited and finalized automatically.
        let actions = n.step(Event::Inspect);
        assert!(actions.iter().any(|a| matches!(a, Action::Persist { generation: 1, .. })));
        assert!(!actions.iter().any(|a| matches!(a, Action::Send { .. })));
        let actions = n.step(Event::Durable { through: 1 });
        assert_eq!(n.disk.commit, 2);
        assert!(actions.iter().any(|a| matches!(a, Action::Send { to: 2, message: Message::Append { .. } })));
    }

}
