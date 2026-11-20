use crate::types::*;
use crate::{mvcc, transfer, wire};
use std::collections::{BTreeMap, BTreeSet};

fn members(c: &Config) -> BTreeSet<Id> {
    c.old.iter().chain(c.new.iter().flatten()).copied().collect()
}
fn quorum(c: &Config, ids: &BTreeSet<Id>) -> bool {
    let majority = |v: &[Id]| {
        let set: BTreeSet<Id> = v.iter().copied().collect();
        !set.is_empty() && set.intersection(ids).count() > set.len() / 2
    };
    majority(&c.old) && c.new.as_ref().map_or(true, |v| majority(v))
}
fn last(d: &Disk) -> u64 { d.snapshot.index + d.log.len() as u64 }
fn term_at(d: &Disk, index: u64) -> Option<u64> {
    if index == d.snapshot.index { Some(d.snapshot.term) }
    else if index > d.snapshot.index {
        d.log.get((index - d.snapshot.index - 1) as usize).map(|e| e.term)
    } else { None }
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
    c.old.sort_unstable(); c.old.dedup();
    if let Some(v) = &mut c.new { v.sort_unstable(); v.dedup(); }
    c
}

struct Replay {
    state: Snapshot,
    writes: BTreeMap<u64, Result<Session, u64>>,
    transactions: BTreeMap<u64, TransactionResult>,
}
// This is pure replay, not application completion. Only the application worker
// may publish one of these checkpoints as visible state.
fn replay(d: &Disk, index: u64) -> Replay {
    assert!(index >= d.snapshot.index && index <= last(d));
    let mut r = Replay { state: d.snapshot.clone(), writes: BTreeMap::new(), transactions: BTreeMap::new() };
    for (offset, entry) in d.log.iter().take((index - d.snapshot.index) as usize).enumerate() {
        let i = d.snapshot.index + offset as u64 + 1;
        let s = &mut r.state;
        match &entry.command {
            EntryKind::Noop => (),
            EntryKind::Write { client, seq, key, delta, expect } => {
                let result = match s.sessions.get(client) {
                    Some(cached) if *seq == cached.seq => Ok(cached.clone()),
                    Some(cached) if *seq < cached.seq => Err(cached.seq),
                    _ => {
                        let before = *s.values.get(key).unwrap_or(&0);
                        let applied = expect.map_or(true, |x| x == before);
                        let value = if applied { before + delta } else { before };
                        if applied {
                            s.values.insert(key.clone(), value);
                            mvcc::record(&mut s.mvcc, key, Some(value), i);
                        }
                        let session = Session { seq: *seq, value, applied };
                        s.sessions.insert(client.clone(), session.clone());
                        Ok(session)
                    }
                };
                r.writes.insert(i, result);
            }
            EntryKind::Transaction { operation } => {
                let result = mvcc::apply(&mut s.mvcc, &mut s.values, operation, i);
                r.transactions.insert(i, result);
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
        s.index = i; s.term = entry.term;
    }
    r
}

#[derive(Clone)]
enum Query {
    Read(String),
    Scan { txn: String, range: Span, after: Option<String>, limit: usize },
}
struct ReadWait {
    request: String,
    query: Query,
    acks: BTreeSet<Id>,
    frontier: Option<u64>,
}
struct Flight {
    prev: u64,
    end: u64,
    read: Option<u64>,
    manifest: Option<Manifest>,
    chunk_len: usize,
}
struct Sending {
    manifest: Manifest,
    offset: u64,
    reply_rpc: u64,
    chunk_size: usize,
}
struct Deferred {
    generation: u64,
    to: Id,
    message: Message,
}

pub struct Node {
    id: Id,
    peers: Vec<Id>,
    disk: Disk,
    role: String,
    leader: Option<Id>,
    votes: BTreeSet<Id>,
    next: BTreeMap<Id, u64>,
    matched: BTreeMap<Id, u64>,
    flights: BTreeMap<(Id, u64), Flight>,
    transfers: BTreeMap<Id, Sending>,
    serial: u64,
    broadcast_due: bool,
    reads: BTreeMap<u64, ReadWait>,
    read_config: Config,
    writes: BTreeMap<u64, Vec<String>>,
    transactions: BTreeMap<u64, Vec<String>>,
    changes: BTreeSet<String>,
    results: Vec<(u64, Action)>,
    compact_target: Option<u64>,

    // Storage images are complete replacements, processed in generation order.
    generation: u64,
    durable_generation: u64,
    submitted: Disk,
    stable: Disk,
    images: BTreeMap<u64, Disk>,
    sends: Vec<Deferred>,
    // The application queue is independent of the storage queue.
    apply_generation: u64,
    applied_generation: u64,
    apply_index: u64,
    jobs: BTreeMap<u64, Snapshot>,
    visible: Snapshot,
}

impl Node {
    pub fn new(id: Id, peers: Vec<Id>, disk: Disk) -> Self {
        let visible = disk.snapshot.clone();
        let read_config = config(&disk);
        Self {
            id, peers, submitted: disk.clone(), stable: disk.clone(), disk,
            role: "follower".into(), leader: None, votes: BTreeSet::new(),
            next: BTreeMap::new(), matched: BTreeMap::new(), flights: BTreeMap::new(),
            transfers: BTreeMap::new(), serial: 0, broadcast_due: false,
            reads: BTreeMap::new(), read_config, writes: BTreeMap::new(),
            transactions: BTreeMap::new(), changes: BTreeSet::new(), results: Vec::new(),
            compact_target: None, generation: 0, durable_generation: 0,
            images: BTreeMap::new(), sends: Vec::new(), apply_generation: 0,
            applied_generation: 0, apply_index: visible.index, jobs: BTreeMap::new(), visible,
        }
    }
    pub fn disk(&self) -> Disk { self.disk.clone() }
    pub fn status(&self) -> Status {
        Status {
            durable_generation: self.durable_generation, apply_generation: self.applied_generation,
            applied_index: self.visible.index, role: self.role.clone(), leader: self.leader,
            config: config(&self.disk), values: self.visible.values.clone(),
            sessions: self.visible.sessions.clone(), mvcc: self.visible.mvcc.clone(),
        }
    }
    fn fresh(&mut self) -> u64 { self.serial += 1; self.serial }
    fn follow(&mut self, term: u64) {
        if term > self.disk.term { self.disk.term = term; self.disk.voted_for = None; }
        self.role = "follower".into(); self.leader = None;
        self.votes.clear(); self.next.clear(); self.matched.clear();
        self.flights.clear(); self.transfers.clear(); self.reads.clear();
        self.writes.clear(); self.transactions.clear(); self.changes.clear();
        self.broadcast_due = false;
        // Results already established by a durable prefix survive retirement.
    }
    fn append(&mut self, command: EntryKind) -> u64 {
        self.disk.log.push(Entry { term: self.disk.term, command });
        self.broadcast_due = true;
        last(&self.disk)
    }
    fn send(raw: &mut Vec<(Id, Message)>, to: Id, message: Message) { raw.push((to, message)); }
    fn replicate(&mut self, to: Id, read: Option<u64>, raw: &mut Vec<(Id, Message)>) {
        let next = self.next.get(&to).copied().unwrap_or(last(&self.disk) + 1).max(1);
        let rpc = self.fresh();
        if next <= self.disk.snapshot.index {
            let (manifest, bytes) = wire::encode(&self.disk.snapshot);
            let sending = self.transfers.entry(to).or_insert_with(|| Sending {
                manifest: manifest.clone(), offset: 0, reply_rpc: 0, chunk_size: wire::MAX_CHUNK,
            });
            if sending.manifest != manifest {
                *sending = Sending { manifest: manifest.clone(), offset: 0, reply_rpc: 0, chunk_size: wire::MAX_CHUNK };
            }
            let offset = if sending.offset < manifest.size { sending.offset } else { 0 };
            let end = (offset as usize + sending.chunk_size).min(bytes.len());
            let data = bytes[offset as usize..end].to_vec();
            self.flights.insert((to, rpc), Flight { prev: offset, end: manifest.index, read, manifest: Some(manifest.clone()), chunk_len: end - offset as usize });
            Self::send(raw, to, Message::SnapshotChunk { term: self.disk.term, rpc, manifest, offset, data });
        } else {
            let prev = next.min(last(&self.disk) + 1) - 1;
            let entries: Vec<Entry> = self.disk.log[(prev - self.disk.snapshot.index) as usize..]
                .iter().take(8).cloned().collect();
            let end = prev + entries.len() as u64;
            self.flights.insert((to, rpc), Flight { prev, end, read, manifest: None, chunk_len: 0 });
            Self::send(raw, to, Message::Append { term: self.disk.term, rpc, prev_index: prev,
                prev_term: term_at(&self.disk, prev).unwrap(), entries, commit: self.disk.commit, read });
        }
    }
    fn broadcast(&mut self, read: Option<u64>, raw: &mut Vec<(Id, Message)>) {
        for peer in self.peers.clone() { if peer != self.id { self.replicate(peer, read, raw); } }
    }
    fn lead(&mut self) {
        self.role = "leader".into(); self.leader = Some(self.id);
        self.next = self.peers.iter().map(|id| (*id, last(&self.disk) + 1)).collect();
        self.matched.clear(); self.flights.clear(); self.transfers.clear();
        self.read_config = config(&self.disk);
        self.append(EntryKind::Noop);
    }
    fn joint(&self) -> Option<(u64, String, Vec<Id>)> {
        for (offset, e) in self.disk.log.iter().enumerate().rev() {
            match &e.command {
                EntryKind::Final { .. } => return None,
                EntryKind::Joint { change, new, .. } => return Some((self.disk.snapshot.index + offset as u64 + 1, change.clone(), new.clone())),
                _ => (),
            }
        }
        self.disk.snapshot.config.new.as_ref().and_then(|new| {
            self.disk.snapshot.pending_change.as_ref().map(|change| (self.disk.snapshot.index, change.clone(), new.clone()))
        })
    }
    fn advance_consensus(&mut self) {
        if self.role != "leader" { return; }
        // Finalization depends on durable commitment, not external application.
        if let Some((index, change, voters)) = self.joint() {
            if index <= self.stable.commit { self.append(EntryKind::Final { change, voters }); }
        }
        let c = config(&self.disk);
        for index in (self.disk.commit + 1..=last(&self.disk)).rev() {
            if term_at(&self.disk, index) != Some(self.disk.term) { continue; }
            let mut ids: BTreeSet<Id> = self.matched.iter().filter(|(_, n)| **n >= index).map(|(id, _)| *id).collect();
            // The local data and this commit can become durable in the same
            // atomic replacement. All remote matches came from durable replies.
            ids.insert(self.id);
            if quorum(&c, &ids) {
                self.disk.commit = index;
                self.broadcast_due = true;
                break;
            }
        }
    }
    fn application(&mut self, out: &mut Vec<Action>) {
        let r = replay(&self.stable, self.stable.commit);
        if r.state.index > self.apply_index {
            self.apply_generation += 1;
            self.apply_index = r.state.index;
            self.jobs.insert(self.apply_generation, r.state.clone());
            out.push(Action::Apply { generation: self.apply_generation, state: r.state.clone() });
        }
        let ready: Vec<u64> = self.writes.keys().copied().filter(|i| *i <= self.stable.commit).collect();
        for i in ready {
            if let Some(result) = r.writes.get(&i) {
                for request in self.writes.remove(&i).unwrap() {
                    let action = match result {
                        Ok(s) => Action::Wrote { request, seq: s.seq, value: s.value, applied: s.applied },
                        Err(last_seq) => Action::Stale { request, last_seq: *last_seq },
                    };
                    self.results.push((i, action));
                }
            }
        }
        let ready: Vec<u64> = self.transactions.keys().copied().filter(|i| *i <= self.stable.commit).collect();
        for i in ready {
            if let Some(result) = r.transactions.get(&i) {
                for request in self.transactions.remove(&i).unwrap() {
                    self.results.push((i, Action::Transaction { request, result: result.clone() }));
                }
            }
        }
        let done: Vec<String> = self.changes.iter().filter(|id| r.state.completed.contains_key(*id)).cloned().collect();
        for request in done {
            self.changes.remove(&request);
            self.results.push((r.state.index, Action::Changed { voters: r.state.completed[&request].clone(), request }));
        }
        // A committed final removes the leader even if the application worker
        // has stopped. Keep the established completion in the result queue.
        if self.role == "leader" && r.state.config.new.is_none()
            && !members(&r.state.config).contains(&self.id) && !members(&config(&self.disk)).contains(&self.id) {
            self.follow(self.disk.term);
        }
    }
    fn compact(&mut self) {
        let Some(target) = self.compact_target else { return; };
        if target <= self.disk.snapshot.index { self.compact_target = None; return; }
        if target > self.stable.commit || target > self.visible.index { return; }
        let snapshot = replay(&self.disk, target).state;
        let suffix = self.disk.log[(target - self.disk.snapshot.index) as usize..].to_vec();
        self.disk.snapshot = snapshot;
        self.disk.log = suffix;
        self.compact_target = None;
    }
    fn read_progress(&mut self, raw: &mut Vec<(Id, Message)>, out: &mut Vec<Action>) {
        if self.role != "leader" { return; }
        let c = config(&self.disk);
        if c != self.read_config {
            self.read_config = c.clone();
            for (_, mut wait) in std::mem::take(&mut self.reads) {
                wait.acks = BTreeSet::from([self.id]); wait.frontier = None;
                let context = self.fresh();
                self.reads.insert(context, wait);
                self.broadcast(Some(context), raw);
            }
        }
        if self.stable.term == self.disk.term && term_at(&self.stable, self.stable.commit) == Some(self.disk.term) {
            for wait in self.reads.values_mut() {
                if wait.frontier.is_none() && quorum(&c, &wait.acks) { wait.frontier = Some(self.disk.commit); }
            }
        }
        let done: Vec<u64> = self.reads.iter().filter(|(_, wait)| wait.frontier.is_some_and(|i|
            i <= self.stable.commit && i <= self.visible.index)).map(|(ctx, _)| *ctx).collect();
        for ctx in done {
            let wait = self.reads.remove(&ctx).unwrap();
            match wait.query {
                Query::Read(key) => out.push(Action::Read { request: wait.request, value: *self.visible.values.get(&key).unwrap_or(&0) }),
                Query::Scan { txn, range, after, limit } => {
                    match mvcc::scan(&self.visible.mvcc, &txn, &range, after.as_deref(), limit) {
                        Ok(page) => out.push(Action::Scan { request: wait.request, txn, page }),
                        Err(result) => out.push(Action::Transaction { request: wait.request, result }),
                    }
                }
            }
        }
    }
    fn start_read(&mut self, request: String, query: Query, raw: &mut Vec<(Id, Message)>, out: &mut Vec<Action>) {
        if self.role != "leader" { out.push(Action::Retry { request }); return; }
        let ctx = self.fresh();
        self.reads.insert(ctx, ReadWait { request, query, acks: BTreeSet::from([self.id]), frontier: None });
        self.broadcast(Some(ctx), raw);
    }
    fn install(&mut self, snapshot: Snapshot) {
        if snapshot.index <= self.disk.commit { return; }
        let suffix = if term_at(&self.disk, snapshot.index) == Some(snapshot.term) {
            self.disk.log[(snapshot.index - self.disk.snapshot.index) as usize..].to_vec()
        } else { vec![] };
        self.disk.commit = snapshot.index;
        self.disk.snapshot = snapshot;
        self.disk.log = suffix;
        self.disk.incoming = None;
    }
    fn receive(&mut self, from: Id, message: Message, raw: &mut Vec<(Id, Message)>) {
        if from == self.id || !self.peers.contains(&from) { return; }
        if let Message::Vote { last_index, last_term, .. } = &message {
            // An obsolete retired server cannot raise everybody's term. A
            // fresher candidate may, however, have logged its own promotion.
            if !members(&config(&self.disk)).contains(&from)
                && (*last_term, *last_index) <= (term_at(&self.disk, last(&self.disk)).unwrap(), last(&self.disk)) { return; }
        }
        if message.term() > self.disk.term { self.follow(message.term()); }
        if message.term() < self.disk.term {
            let reply = match message {
                Message::Vote { .. } => Some(Message::VoteReply { term: self.disk.term, granted: false }),
                Message::Append { rpc, read, .. } => Some(Message::AppendReply { term: self.disk.term, rpc, success: false, index: 0, hint: last(&self.disk) + 1, read }),
                Message::SnapshotChunk { rpc, manifest, .. } => Some(Message::ChunkReply { term: self.disk.term, rpc, manifest, received: 0, installed: false }),
                Message::Snapshot { rpc, .. } => Some(Message::SnapshotReply { term: self.disk.term, rpc, index: 0 }),
                _ => None,
            };
            if let Some(reply) = reply { Self::send(raw, from, reply); }
            return;
        }
        match message {
            Message::Vote { last_index, last_term, .. } => {
                let fresh = (last_term, last_index) >= (term_at(&self.disk, last(&self.disk)).unwrap(), last(&self.disk));
                let granted = fresh && self.disk.voted_for.map_or(true, |v| v == from);
                if granted { self.disk.voted_for = Some(from); }
                Self::send(raw, from, Message::VoteReply { term: self.disk.term, granted });
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
                if term_at(&self.disk, prev_index) != Some(prev_term) {
                    let hint = if prev_index < self.disk.snapshot.index { self.disk.snapshot.index + 1 }
                        else if prev_index > last(&self.disk) { last(&self.disk) + 1 }
                        else { prev_index.max(1) };
                    Self::send(raw, from, Message::AppendReply { term: self.disk.term, rpc, success: false, index: 0, hint, read });
                    return;
                }
                let end = prev_index + entries.len() as u64;
                for (offset, entry) in entries.into_iter().enumerate() {
                    let index = prev_index + offset as u64 + 1;
                    if term_at(&self.disk, index) != Some(entry.term) {
                        if index <= self.disk.commit {
                            Self::send(raw, from, Message::AppendReply { term: self.disk.term, rpc, success: false, index: 0, hint: self.disk.commit + 1, read });
                            return;
                        }
                        self.disk.log.truncate((index - self.disk.snapshot.index - 1) as usize);
                        self.disk.log.push(entry);
                    }
                }
                self.disk.commit = self.disk.commit.max(commit.min(end));
                Self::send(raw, from, Message::AppendReply { term: self.disk.term, rpc, success: true, index: end, hint: end + 1, read });
            }
            Message::AppendReply { rpc, success, index, hint, read, .. } => {
                if self.role != "leader" { return; }
                let Some(f) = self.flights.remove(&(from, rpc)) else { return; };
                if f.manifest.is_some() { return; }
                if success {
                    if index != f.end { return; }
                    let old = self.matched.get(&from).copied().unwrap_or(0);
                    self.matched.insert(from, old.max(index));
                    let next = self.next.get(&from).copied().unwrap_or(1).max(index + 1);
                    self.next.insert(from, next);
                    if read == f.read {
                        if let Some(ctx) = read {
                            // This packet was sent after every earlier waiting
                            // invocation too. Configuration resets allocate new,
                            // larger contexts, so old packets cannot cross them.
                            for (_, wait) in self.reads.range_mut(..=ctx) { wait.acks.insert(from); }
                        }
                    }
                    if index > old && self.matched[&from] < last(&self.disk) { self.replicate(from, f.read, raw); }
                } else {
                    let floor = self.matched.get(&from).copied().unwrap_or(0) + 1;
                    let next = self.next.get(&from).copied().unwrap_or(last(&self.disk) + 1);
                    if next != f.prev + 1 { return; }
                    let mut target = hint.max(floor).min(last(&self.disk) + 1);
                    if target == next { target = next.saturating_sub(1).max(floor); }
                    if target != next {
                        self.next.insert(from, target);
                        self.replicate(from, f.read, raw);
                    }
                }
            }
            Message::SnapshotChunk { rpc, manifest, offset, data, .. } => {
                if self.role != "follower" { self.follow(self.disk.term); }
                self.leader = Some(from);
                let (received, installed) = if !transfer::valid_fragment(&manifest, offset, &data) {
                    (0, false)
                } else if manifest.index <= self.disk.commit {
                    (manifest.size, true)
                } else {
                    let (received, snapshot) = transfer::receive(&mut self.disk, &manifest, offset, &data);
                    let installed = snapshot.is_some();
                    if let Some(snapshot) = snapshot { self.install(snapshot); }
                    (received, installed)
                };
                Self::send(raw, from, Message::ChunkReply { term: self.disk.term, rpc, manifest, received, installed });
            }
            Message::ChunkReply { rpc, manifest, received, installed, .. } => {
                if self.role != "leader" { return; }
                let Some(f) = self.flights.remove(&(from, rpc)) else { return; };
                if f.manifest.as_ref() != Some(&manifest) || received > manifest.size { return; }
                let Some(sending) = self.transfers.get_mut(&from) else { return; };
                if sending.manifest != manifest || rpc <= sending.reply_rpc { return; }
                sending.reply_rpc = rpc;
                if installed {
                    if received != manifest.size { return; }
                    let old = self.matched.get(&from).copied().unwrap_or(0);
                    self.matched.insert(from, old.max(manifest.index));
                    let next = self.next.get(&from).copied().unwrap_or(1).max(manifest.index + 1);
                    self.next.insert(from, next);
                    self.transfers.remove(&from);
                    // Only this subsequent append can certify a read context.
                    self.replicate(from, f.read, raw);
                } else if received < manifest.size {
                    // A conflicting overlap beyond the contiguous prefix may
                    // hide a gap. Smaller packets can fill that gap without
                    // changing accepted bytes; the eventual checksum then
                    // discards a corrupt image. Grow again on forward progress.
                    sending.chunk_size = if received == f.prev {
                        (f.chunk_len / 2).max(1)
                    } else if received < f.prev {
                        wire::MAX_CHUNK
                    } else {
                        (f.chunk_len * 2).min(wire::MAX_CHUNK).max(1)
                    };
                    sending.offset = received;
                    self.replicate(from, f.read, raw);
                }
            }
            Message::Snapshot { rpc, snapshot, .. } => {
                if self.role != "follower" { self.follow(self.disk.term); }
                self.leader = Some(from);
                let index = snapshot.index;
                self.install(snapshot);
                Self::send(raw, from, Message::SnapshotReply { term: self.disk.term, rpc, index });
            }
            // We never originate the legacy full-snapshot RPC.
            Message::SnapshotReply { .. } => (),
        }
    }
    fn change(&mut self, request: String, mut voters: Vec<Id>, out: &mut Vec<Action>) {
        if self.role != "leader" { out.push(Action::Retry { request }); return; }
        if let Some(voters) = self.visible.completed.get(&request) {
            out.push(Action::Changed { request, voters: voters.clone() }); return;
        }
        let committed = replay(&self.disk, self.disk.commit).state;
        if committed.completed.contains_key(&request) {
            self.changes.insert(request); return;
        }
        // A retry of an unfinished ID always retains the target already logged.
        let mut pending = committed.pending_change.clone();
        for (offset, e) in self.disk.log.iter().enumerate() {
            if self.disk.snapshot.index + offset as u64 + 1 > self.disk.commit {
                match &e.command {
                    EntryKind::Joint { change, .. } | EntryKind::Final { change, .. } => pending = Some(change.clone()),
                    _ => (),
                }
            }
        }
        if let Some(id) = pending {
            if id == request { self.changes.insert(request); }
            else { out.push(Action::Retry { request }); }
            return;
        }
        voters.sort_unstable(); voters.dedup();
        if voters.is_empty() || voters.iter().any(|id| !self.peers.contains(id)) {
            out.push(Action::Retry { request }); return;
        }
        let c = config(&self.disk);
        if c.new.is_some() { out.push(Action::Retry { request }); return; }
        self.changes.insert(request.clone());
        self.append(EntryKind::Joint { change: request, old: c.old, new: voters });
    }
    fn durable(&mut self, through: u64) {
        if through <= self.durable_generation || through > self.generation { return; }
        if let Some(image) = self.images.get(&through).cloned() {
            self.stable = image;
            self.durable_generation = through;
            self.images.retain(|g, _| *g > through);
        }
    }
    fn applied(&mut self, through: u64) {
        if through <= self.applied_generation || through > self.apply_generation { return; }
        if let Some(state) = self.jobs.get(&through).cloned() {
            if state.index > self.visible.index { self.visible = state; }
            self.applied_generation = through;
            self.jobs.retain(|g, _| *g > through);
        }
    }
    pub fn step(&mut self, event: Event) -> Vec<Action> {
        let mut out = Vec::new();
        let mut raw = Vec::new();
        match event {
            Event::Durable { through } => self.durable(through),
            Event::Applied { through } => self.applied(through),
            Event::Inspect => (),
            Event::Campaign => {
                if members(&config(&self.disk)).contains(&self.id) {
                    self.follow(self.disk.term + 1);
                    self.role = "candidate".into();
                    self.disk.voted_for = Some(self.id);
                    self.votes.insert(self.id);
                    for peer in members(&config(&self.disk)) {
                        if peer != self.id {
                            Self::send(&mut raw, peer, Message::Vote { term: self.disk.term,
                                last_index: last(&self.disk), last_term: term_at(&self.disk, last(&self.disk)).unwrap() });
                        }
                    }
                    if quorum(&config(&self.disk), &self.votes) { self.lead(); }
                }
            }
            Event::Tick => {
                if self.role == "leader" {
                    self.broadcast(None, &mut raw);
                    for ctx in self.reads.keys().copied().collect::<Vec<_>>() { self.broadcast(Some(ctx), &mut raw); }
                }
            }
            Event::Receive { from, message } => self.receive(from, message, &mut raw),
            Event::Write { request, client, seq, key, delta, expect } => {
                if self.role != "leader" { out.push(Action::Retry { request }); }
                else {
                    let i = self.append(EntryKind::Write { client, seq, key, delta, expect });
                    self.writes.entry(i).or_default().push(request);
                }
            }
            Event::Transaction { request, operation } => {
                if self.role != "leader" { out.push(Action::Retry { request }); }
                else {
                    let i = self.append(EntryKind::Transaction { operation });
                    self.transactions.entry(i).or_default().push(request);
                }
            }
            Event::Read { request, key } => self.start_read(request, Query::Read(key), &mut raw, &mut out),
            Event::Scan { request, txn, range, after, limit } => {
                self.start_read(request, Query::Scan { txn, range, after, limit }, &mut raw, &mut out);
            }
            Event::Change { request, voters } => self.change(request, voters, &mut out),
            Event::Compact => {
                self.compact_target = Some(self.compact_target.unwrap_or(0).max(self.disk.commit));
            }
        }
        self.application(&mut out);
        self.advance_consensus();
        if self.disk.incoming.as_ref().is_some_and(|i| i.manifest.index <= self.disk.commit) {
            self.disk.incoming = None;
        }
        self.compact();
        self.read_progress(&mut raw, &mut out);
        if self.role == "leader" && self.broadcast_due {
            self.broadcast_due = false;
            self.broadcast(None, &mut raw);
        }
        let mut waiting = Vec::new();
        for (index, action) in self.results.drain(..) {
            if index <= self.visible.index { out.push(action); }
            else { waiting.push((index, action)); }
        }
        self.results = waiting;

        // Register dependencies before releasing any protocol effects. Later
        // staged images never satisfy an earlier callback, or vice versa.
        if self.disk != self.submitted {
            self.generation += 1;
            self.submitted = self.disk.clone();
            self.images.insert(self.generation, self.submitted.clone());
            out.insert(0, Action::Persist { generation: self.generation, disk: self.submitted.clone() });
        }
        for (to, message) in raw {
            self.sends.push(Deferred { generation: self.generation, to, message });
        }
        let mut pending = Vec::new();
        for mut send in self.sends.drain(..) {
            if send.message.term() != self.disk.term { continue; }
            if send.generation <= self.durable_generation {
                if let Message::ChunkReply { manifest, received, installed: false, .. } = &mut send.message {
                    let prefix = self.stable.incoming.as_ref().filter(|i| i.manifest == *manifest)
                        .and_then(|i| i.pieces.get(&0)).map_or(0, |b| b.len() as u64);
                    *received = (*received).min(prefix);
                }
                out.push(Action::Send { to: send.to, message: send.message });
            } else { pending.push(send); }
        }
        self.sends = pending;
        out
    }
}
