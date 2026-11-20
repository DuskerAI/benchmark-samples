use crate::{types::*, wire};
use std::collections::BTreeMap;

// Pieces are byte intervals, not packet boundaries. Normalize their union only
// after checking every overlap, so a bad packet cannot damage a resumable image.
pub fn valid_fragment(manifest: &Manifest, offset: u64, data: &[u8]) -> bool {
    manifest.size > 0 && manifest.size <= wire::MAX_SNAPSHOT as u64 && !data.is_empty()
        && data.len() <= wire::MAX_CHUNK && offset <= manifest.size
        && data.len() as u64 <= manifest.size - offset
}

pub fn receive(disk: &mut Disk, manifest: &Manifest, offset: u64, data: &[u8]) -> (u64, Option<Snapshot>) {
    if !valid_fragment(manifest, offset, data) { return (0, None); }
    if disk.incoming.as_ref().is_some_and(|i| i.manifest != *manifest) {
        if offset != 0 { return (0, None); }
        disk.incoming = None;
    }
    let incoming = disk.incoming.get_or_insert_with(|| Incoming {
        manifest: manifest.clone(), pieces: BTreeMap::new(),
    });
    let end = offset + data.len() as u64;
    for (&start, bytes) in incoming.pieces.range(..end) {
        let lo = start.max(offset);
        let hi = (start + bytes.len() as u64).min(end);
        if lo < hi && bytes[(lo-start) as usize..(hi-start) as usize] != data[(lo-offset) as usize..(hi-offset) as usize] {
            return (incoming.pieces.get(&0).map_or(0, |b| b.len() as u64), None);
        }
    }
    let piece = incoming.pieces.entry(offset).or_default();
    if piece.len() < data.len() { *piece = data.to_vec(); }
    let mut merged: BTreeMap<u64, Vec<u8>> = BTreeMap::new();
    for (start, bytes) in std::mem::take(&mut incoming.pieces) {
        if let Some((&base, previous)) = merged.iter_mut().next_back() {
            let covered = base + previous.len() as u64;
            if start <= covered {
                let overlap = (covered - start).min(bytes.len() as u64) as usize;
                previous.extend_from_slice(&bytes[overlap..]);
                continue;
            }
        }
        merged.insert(start, bytes);
    }
    incoming.pieces = merged;
    let received = incoming.pieces.get(&0).map_or(0, |b| b.len() as u64);
    if received != manifest.size { return (received, None); }
    let snapshot = wire::decode(manifest, &incoming.pieces[&0]);
    disk.incoming = None;
    if snapshot.is_some() { (received, snapshot) } else { (0, None) }
}
