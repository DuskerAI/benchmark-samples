use crate::{types::*, wire};
use std::collections::BTreeMap;

// Coverage is stored as disjoint, ordered intervals. Offset zero is not a
// restart instruction for the same manifest: it is often a retransmission.
pub fn receive(disk: &mut Disk, manifest: &Manifest, offset: u64, data: &[u8]) -> (u64, Option<Snapshot>) {
    if manifest.size == 0 || manifest.size > wire::MAX_SNAPSHOT as u64 || data.is_empty()
        || data.len() > wire::MAX_CHUNK || offset > manifest.size
        || data.len() as u64 > manifest.size - offset {
        return (0, None);
    }
    if disk.incoming.as_ref().is_some_and(|i| i.manifest != *manifest) && offset != 0 {
        return (0, None);
    }
    if disk.incoming.as_ref().map_or(true, |i| i.manifest != *manifest) {
        disk.incoming = Some(Incoming { manifest: manifest.clone(), pieces: BTreeMap::new() });
    }
    let incoming = disk.incoming.as_mut().unwrap();
    let end = offset + data.len() as u64;
    for (&start, piece) in &incoming.pieces {
        let lo = start.max(offset);
        let hi = (start + piece.len() as u64).min(end);
        if lo < hi && piece[(lo-start) as usize..(hi-start) as usize] != data[(lo-offset) as usize..(hi-offset) as usize] {
            // Do not endorse the conflicting bytes, but let the sender finish
            // the existing coverage. A corrupted accepted prefix can otherwise
            // livelock on retransmissions forever, before its checksum is tested.
            let mut prefix = 0;
            for (&start, piece) in &incoming.pieces {
                if start > prefix { break; }
                prefix = prefix.max(start + piece.len() as u64);
            }
            return (prefix, None);
        }
    }
    // Normalize the union, including split intervals restored from other
    // compatible writers. Never allocate for an uncovered hole.
    let mut parts: Vec<_> = incoming.pieces.iter().map(|(k, v)| (*k, v.clone())).collect();
    parts.push((offset, data.to_vec()));
    parts.sort_by_key(|(start, _)| *start);
    let mut merged: Vec<(u64, Vec<u8>)> = Vec::new();
    for (start, piece) in parts {
        if let Some((lo, bytes)) = merged.last_mut() {
            let stop = *lo + bytes.len() as u64;
            if start <= stop {
                let skip = (stop-start) as usize;
                if skip < piece.len() { bytes.extend_from_slice(&piece[skip..]); }
                continue;
            }
        }
        merged.push((start, piece));
    }
    incoming.pieces = merged.into_iter().collect();
    let received = incoming.pieces.get(&0).map_or(0, |p| p.len() as u64);
    if received != manifest.size { return (received, None); }
    let snapshot = wire::decode(manifest, incoming.pieces.get(&0).unwrap());
    disk.incoming = None;
    if snapshot.is_some() { (received, snapshot) } else { (0, None) }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn restored_split_coverage_and_retransmission() {
        let mut disk = Disk::initial(vec![1]);
        let mut state = disk.snapshot.clone(); state.index = 8; state.term = 2;
        let (manifest, bytes) = wire::encode(&state);
        disk.incoming = Some(Incoming { manifest: manifest.clone(), pieces: [
            (0, bytes[..10].to_vec()), (10, bytes[10..20].to_vec())
        ].into_iter().collect() });
        let before = disk.incoming.clone();
        let mut bad = bytes[5..15].to_vec(); bad[0] ^= 1;
        assert_eq!(receive(&mut disk, &manifest, 5, &bad), (20, None));
        assert_eq!(disk.incoming, before);
        assert_eq!(receive(&mut disk, &manifest, 0, &bytes[..10]), (20, None));
        let (received, complete) = receive(&mut disk, &manifest, 20, &bytes[20..]);
        assert_eq!(received, manifest.size); assert_eq!(complete, Some(state));
        assert_eq!(disk.incoming, None);
    }
    #[test]
    fn invalid_completion_can_restart() {
        let mut disk = Disk::initial(vec![1]);
        let (manifest, mut bytes) = wire::encode(&disk.snapshot);
        bytes[0] ^= 1;
        assert_eq!(receive(&mut disk, &manifest, 0, &bytes), (0, None));
        assert_eq!(disk.incoming, None);
        let (manifest, bytes) = wire::encode(&disk.snapshot);
        assert!(receive(&mut disk, &manifest, 0, &bytes).1.is_some());
    }
    #[test]
    fn corrupted_prefix_with_lost_reply_does_not_livelock() {
        let mut disk = Disk::initial(vec![1]);
        let mut snapshot = disk.snapshot.clone();
        snapshot.index = 10; snapshot.term = 2;
        snapshot.values.insert("k".repeat(1500), 1);
        let (manifest, bytes) = wire::encode(&snapshot);
        let mut bad = bytes[..512].to_vec(); bad[50] ^= 1;
        assert_eq!(receive(&mut disk, &manifest, 0, &bad), (512, None));
        // The first reply was lost. The correct duplicate must leave the
        // accepted bytes intact, but still allow coverage to advance.
        assert_eq!(receive(&mut disk, &manifest, 0, &bytes[..512]), (512, None));
        let mut offset = 512;
        while offset < bytes.len() {
            let end = (offset + wire::MAX_CHUNK).min(bytes.len());
            let reply = receive(&mut disk, &manifest, offset as u64, &bytes[offset..end]);
            assert!(reply.1.is_none());
            offset = end;
        }
        assert_eq!(disk.incoming, None);
        let mut installed = None;
        for (i, chunk) in bytes.chunks(wire::MAX_CHUNK).enumerate() {
            installed = receive(&mut disk, &manifest, (i*wire::MAX_CHUNK) as u64, chunk).1;
        }
        assert_eq!(installed, Some(snapshot));
    }

}
