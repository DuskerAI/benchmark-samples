use crate::{types::*, wire};
use std::collections::BTreeMap;

// Only coverage beginning at zero is a resumable acknowledgement. Sparse pieces
// beyond a hole are nevertheless part of the durable incoming image.
pub fn prefix(incoming: &Incoming) -> u64 {
    let mut end = 0;
    for (&offset, data) in &incoming.pieces {
        if offset > end { break; }
        end = end.max(offset + data.len() as u64);
    }
    end.min(incoming.manifest.size)
}

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
    // Validate the entire overlap before modifying any accepted byte.
    for (&start, piece) in &incoming.pieces {
        let lo = start.max(offset);
        let hi = (start + piece.len() as u64).min(end);
        if lo < hi && piece[(lo-start) as usize..(hi-start) as usize]
            != data[(lo-offset) as usize..(hi-offset) as usize] {
            return (prefix(incoming), None);
        }
    }
    // Canonicalize the union into disjoint, coalesced intervals. The size bound
    // was checked before either allocation or offset arithmetic.
    let mut pieces: Vec<(u64, Vec<u8>)> = incoming.pieces.iter().map(|(o,b)| (*o,b.clone())).collect();
    pieces.push((offset, data.to_vec()));
    pieces.sort_by_key(|p| p.0);
    let mut merged: Vec<(u64, Vec<u8>)> = Vec::new();
    for (start, bytes) in pieces {
        if let Some((prev, buf)) = merged.last_mut() {
            let tail = *prev + buf.len() as u64;
            if start <= tail {
                let overlap = (tail - start).min(bytes.len() as u64) as usize;
                buf.extend_from_slice(&bytes[overlap..]);
                continue;
            }
        }
        merged.push((start, bytes));
    }
    incoming.pieces = merged.into_iter().collect();
    let received = prefix(incoming);
    if received != manifest.size { return (received, None); }
    let bytes = incoming.pieces.get(&0).unwrap();
    let snapshot = wire::decode(manifest, bytes);
    disk.incoming = None;
    if snapshot.is_some() { (received, snapshot) } else { (0, None) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sparse_overlap_and_restart_preserve_coverage() {
        let mut snapshot = Disk::initial(vec![1,2,3]).snapshot;
        snapshot.index = 9;
        snapshot.term = 2;
        for n in 0..150 { snapshot.values.insert(format!("key{n:03}"), n); }
        let (manifest, bytes) = wire::encode(&snapshot);
        let mut disk = Disk::initial(vec![1,2,3]);
        assert_eq!(receive(&mut disk, &manifest, 50, &bytes[50..150]), (0, None));
        let saved = disk.incoming.clone();
        assert_eq!(receive(&mut disk, &manifest, 75, &bytes[75..125]), (0, None));
        assert_eq!(disk.incoming, saved);
        let mut bad = bytes[75..125].to_vec(); bad[0] ^= 1;
        assert_eq!(receive(&mut disk, &manifest, 75, &bad), (0, None));
        assert_eq!(disk.incoming, saved);
        // This also exercises the fleet's string-keyed persisted offset map.
        disk = serde_json::from_slice(&serde_json::to_vec(&disk).unwrap()).unwrap();
        for start in (150..bytes.len()).step_by(200) {
            let end = (start+200).min(bytes.len());
            assert_eq!(receive(&mut disk, &manifest, start as u64, &bytes[start..end]), (0, None));
        }
        let (received, installed) = receive(&mut disk, &manifest, 0, &bytes[..80]);
        assert_eq!(received, manifest.size);
        assert_eq!(installed, Some(snapshot));
        assert!(disk.incoming.is_none());
    }

    #[test]
    fn invalid_fragments_and_invalid_completed_images_are_distinct() {
        let snapshot = Disk::initial(vec![1]).snapshot;
        let (manifest, bytes) = wire::encode(&snapshot);
        let mut disk = Disk::initial(vec![1]);
        receive(&mut disk, &manifest, 10, &bytes[10..20]);
        let saved = disk.incoming.clone();
        receive(&mut disk, &manifest, u64::MAX, &[0]);
        assert_eq!(disk.incoming, saved);
        let mut other = manifest.clone(); other.checksum ^= 1;
        receive(&mut disk, &other, 10, &bytes[10..20]);
        assert_eq!(disk.incoming, saved);
        // A complete checksum failure discards the unusable assembly.
        assert_eq!(receive(&mut disk, &other, 0, &bytes), (0, None));
        assert!(disk.incoming.is_none());
        assert_eq!(receive(&mut disk, &manifest, 0, &bytes), (manifest.size, Some(snapshot)));
    }
}
