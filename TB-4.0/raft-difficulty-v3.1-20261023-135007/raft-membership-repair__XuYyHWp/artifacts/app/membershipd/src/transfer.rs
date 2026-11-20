use crate::{types::*, wire};
use std::collections::BTreeMap;

pub fn valid(manifest: &Manifest, offset: u64, data: &[u8]) -> bool {
    manifest.size > 0 && manifest.size <= wire::MAX_SNAPSHOT as u64 &&
        !data.is_empty() && data.len() <= wire::MAX_CHUNK &&
        offset <= manifest.size && data.len() as u64 <= manifest.size - offset
}

pub fn contiguous(incoming: &Incoming) -> u64 {
    let mut received = 0;
    for (&start, piece) in &incoming.pieces {
        if start > received { break; }
        received = received.max(start + piece.len() as u64);
    }
    received
}

// Accepted intervals are durable fleet data, not an installed application image.
// Keep disjoint, maximally merged intervals; duplicates must never reset coverage.
pub fn receive(disk: &mut Disk, manifest: &Manifest, offset: u64, data: &[u8]) -> (u64, Option<Snapshot>) {
    if !valid(manifest, offset, data) { return (0, None); }
    if disk.incoming.as_ref().is_some_and(|i| i.manifest != *manifest) {
        if offset != 0 { return (0, None); }
        disk.incoming = None;
    }
    let incoming = disk.incoming.get_or_insert_with(|| Incoming {
        manifest: manifest.clone(), pieces: BTreeMap::new(),
    });
    let end = offset + data.len() as u64;
    // Check all overlaps before changing anything.
    for (&start, piece) in &incoming.pieces {
        let lo = start.max(offset);
        let hi = (start + piece.len() as u64).min(end);
        if lo < hi && piece[(lo-start) as usize..(hi-start) as usize] != data[(lo-offset) as usize..(hi-offset) as usize] {
            // Reporting accepted coverage lets a sender get past corrupt bytes
            // and finish checksum validation without overwriting any fragment.
            return (contiguous(incoming), None);
        }
    }
    let mut lo = offset;
    let mut hi = end;
    let mut remove = Vec::new();
    for (&start, piece) in &incoming.pieces {
        let stop = start + piece.len() as u64;
        if stop >= lo && start <= hi {
            lo = lo.min(start);
            hi = hi.max(stop);
            remove.push(start);
        }
    }
    let mut merged = vec![0; (hi-lo) as usize];
    for start in remove {
        let piece = incoming.pieces.remove(&start).unwrap();
        merged[(start-lo) as usize..(start-lo) as usize+piece.len()].copy_from_slice(&piece);
    }
    merged[(offset-lo) as usize..(end-lo) as usize].copy_from_slice(data);
    incoming.pieces.insert(lo, merged);
    // A restored image may legally split adjacent intervals differently.
    let received = contiguous(incoming);
    if received != manifest.size { return (received, None); }
    let mut bytes = vec![0; manifest.size as usize];
    for (&start, piece) in &incoming.pieces {
        bytes[start as usize..start as usize + piece.len()].copy_from_slice(piece);
    }
    let snapshot = wire::decode(manifest, &bytes);
    disk.incoming = None;
    if snapshot.is_some() { (received, snapshot) } else { (0, None) }
}
