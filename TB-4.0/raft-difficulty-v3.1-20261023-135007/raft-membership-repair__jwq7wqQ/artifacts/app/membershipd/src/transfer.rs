// Resumable checkpoint assembly. Coverage is persisted in Disk, never in a
// process-local buffer. A rejected fragment cannot destroy accepted bytes.
use crate::{types::*, wire};
use std::collections::BTreeMap;

pub fn valid_fragment(manifest: &Manifest, offset: u64, data: &[u8]) -> bool {
    manifest.size > 0 && manifest.size <= wire::MAX_SNAPSHOT as u64 && !data.is_empty() &&
        data.len() <= wire::MAX_CHUNK && offset <= manifest.size && data.len() as u64 <= manifest.size - offset
}

pub fn receive(disk: &mut Disk, manifest: &Manifest, offset: u64, data: &[u8]) -> (u64, Option<Snapshot>) {
    if !valid_fragment(manifest, offset, data) { return (0, None); }
    match &disk.incoming {
        None => disk.incoming = Some(Incoming { manifest: manifest.clone(), pieces: BTreeMap::new() }),
        Some(i) if i.manifest != *manifest => {
            if offset != 0 { return (0, None); }
            disk.incoming = Some(Incoming { manifest: manifest.clone(), pieces: BTreeMap::new() });
        }
        _ => (),
    }
    let incoming = disk.incoming.as_mut().unwrap();
    let end = offset + data.len() as u64;
    for (&start, piece) in &incoming.pieces {
        let lo = start.max(offset);
        let hi = (start + piece.len() as u64).min(end);
        if lo < hi && piece[(lo-start) as usize..(hi-start) as usize] != data[(lo-offset) as usize..(hi-offset) as usize] {
            return (0, None);
        }
    }
    // Normalize all intervals, including split pieces restored from disk.
    let mut intervals: Vec<(u64, Vec<u8>)> = incoming.pieces.iter()
        .map(|(&start, bytes)| (start, bytes.clone())).collect();
    intervals.push((offset, data.to_vec()));
    intervals.sort_by_key(|(start, _)| *start);
    let mut pieces: BTreeMap<u64, Vec<u8>> = BTreeMap::new();
    for (start, bytes) in intervals {
        if let Some((&previous, tail)) = pieces.iter_mut().next_back() {
            let stop = previous + tail.len() as u64;
            if start <= stop {
                let overlap = (stop - start).min(bytes.len() as u64) as usize;
                tail.extend_from_slice(&bytes[overlap..]);
                continue;
            }
        }
        pieces.insert(start, bytes);
    }
    incoming.pieces = pieces;
    let received = incoming.pieces.get(&0).map_or(0, |p| p.len() as u64);
    if received != manifest.size { return (received, None); }
    let snapshot = wire::decode(manifest, &incoming.pieces[&0]);
    disk.incoming = None;
    if snapshot.is_some() { (received, snapshot) } else { (0, None) }
}
