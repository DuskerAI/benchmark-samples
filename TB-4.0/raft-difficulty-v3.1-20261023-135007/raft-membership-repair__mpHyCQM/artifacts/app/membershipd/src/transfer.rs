use crate::{types::*, wire};
use std::collections::BTreeMap;

pub fn valid_fragment(manifest: &Manifest, offset: u64, data: &[u8]) -> bool {
    manifest.size > 0 && manifest.size <= wire::MAX_SNAPSHOT as u64 &&
        !data.is_empty() && data.len() <= wire::MAX_CHUNK &&
        offset <= manifest.size && data.len() as u64 <= manifest.size - offset
}

// Incoming is coverage, not an installed checkpoint. In particular a duplicate
// first packet must not erase previously fsynced coverage.
pub fn receive(disk: &mut Disk, manifest: &Manifest, offset: u64, data: &[u8]) -> (u64, Option<Snapshot>) {
    if !valid_fragment(manifest, offset, data) { return (0, None); }
    let same = disk.incoming.as_ref().is_some_and(|i| i.manifest == *manifest);
    if !same && disk.incoming.is_some() && offset != 0 { return (0, None); }

    let size = manifest.size as usize;
    let mut bytes = vec![0; size];
    let mut covered = vec![false; size];
    if same {
        for (&start, piece) in &disk.incoming.as_ref().unwrap().pieces {
            // Persisted pieces are valid, but keep allocation and indexing
            // bounded even when restoring an older fleet image.
            if start > manifest.size || piece.len() as u64 > manifest.size - start { return (0, None); }
            let start = start as usize;
            for (n, &byte) in piece.iter().enumerate() {
                bytes[start + n] = byte;
                covered[start + n] = true;
            }
        }
    }
    let start = offset as usize;
    for (n, &byte) in data.iter().enumerate() {
        if covered[start + n] && bytes[start + n] != byte {
            let prefix = covered.iter().position(|b| !*b).unwrap_or(size);
            return (prefix as u64, None);
        }
    }
    bytes[start..start + data.len()].copy_from_slice(data);
    covered[start..start + data.len()].fill(true);
    let prefix = covered.iter().position(|b| !*b).unwrap_or(size);
    if prefix == size {
        disk.incoming = None;
        return match wire::decode(manifest, &bytes) {
            Some(snapshot) => (manifest.size, Some(snapshot)),
            None => (0, None),
        };
    }
    let mut pieces = BTreeMap::new();
    let mut at = 0;
    while at < size {
        if !covered[at] { at += 1; continue; }
        let first = at;
        while at < size && covered[at] { at += 1; }
        pieces.insert(first as u64, bytes[first..at].to_vec());
    }
    disk.incoming = Some(Incoming { manifest: manifest.clone(), pieces });
    (prefix as u64, None)
}
