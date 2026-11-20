# MRT replay contract

`mrt_replay` reads one binary capture and writes one JSON report. The capture uses the MRT record header from RFC 6396: `timestamp(4)`, `type(2)`, `subtype(2)`, and `length(4)`, all unsigned and in network byte order. The `length` bytes that follow are the record payload. Count a record as seen as soon as its full 12-byte header has been read, even if the payload is shorter than the declared length. If a header is incomplete, or a payload is shorter than the declared length, count one malformed record and stop scanning. Unknown record types or subtypes are ignored and counted as ignored.

Supported MRT records:

- `type 13, subtype 1` is `PEER_INDEX_TABLE`: `collector_bgp_id(4)`, `view_name_len(2)`, `view_name`, `peer_count(2)`, then peers. Each peer has `peer_type(1)`, `bgp_id(4)`, an address, and an AS number. In `peer_type`, bit 0 selects IPv6 address length, bit 1 selects a 4-byte AS number instead of a 2-byte AS number, and bit 2 marks an iBGP peer. Peer indexes are the zero-based order in the current table. A new peer table starts a new table boundary: replace the peer-index table and clear the current route table.
- `type 13, subtype 2` is an IPv4 unicast RIB entry. `type 13, subtype 4` is an IPv6 unicast RIB entry. The payload is `sequence(4)`, `prefix_len(1)`, enough prefix bytes to cover the prefix length, `entry_count(2)`, then entries. Each entry is `peer_index(2)`, `originated_time(4)`, `attr_len(2)`, and BGP path attributes. Entries with an unknown peer index are malformed for that record.
- `type 16, subtype 1` is a BGP4MP update. The payload is `peer_as(4)`, `local_as(4)`, `interface_index(2)`, `afi(2)`, `peer_address`, `local_address`, and a compact BGP UPDATE message. The address length is 4 bytes for AFI 1 and 16 bytes for AFI 2. For routes created or withdrawn by these updates, identify the announcing peer by matching `(peer_as, peer_address)` against the active peer-index table. Use that peer's `peer_type` bit 2 for the candidate `source`; do not infer iBGP from `peer_as == local_as`.

The compact UPDATE message is `message_type(1)`, `withdrawn_len(2)`, withdrawn IPv4 prefixes, `attr_len(2)`, path attributes, and IPv4 NLRI. `message_type` must be `2`. IPv6 announcements and withdrawals use `MP_REACH_NLRI` and `MP_UNREACH_NLRI` attributes.

BGP path attributes use `flags(1)`, `code(1)`, and length. If flag bit `0x10` is set, the length is two bytes; otherwise it is one byte. The supported attributes are `ORIGIN(1)`, `AS_PATH(2)`, `MULTI_EXIT_DISC(4)`, `LOCAL_PREF(5)`, `MP_REACH_NLRI(14)`, `MP_UNREACH_NLRI(15)`, and `AS4_PATH(17)`. Unknown attributes are ignored. `AS_PATH` and `AS4_PATH` consist of segments: `segment_type(1)`, `segment_count(1)`, then AS numbers. `AS_PATH` carries 2-byte ASNs; `AS4_PATH` carries 4-byte ASNs. A path length counts each AS_SEQUENCE member and counts an AS_SET segment as one. For output and best-path selection, merge AS4 information by replacing AS_TRANS value `23456` in `AS_PATH` with the corresponding value from `AS4_PATH` in order; if there is no `AS_PATH`, use `AS4_PATH`.

Prefix bytes contain only the significant network bytes. Any missing host bits in the last byte are zero for display and matching. IPv4 strings use dotted decimal and IPv6 strings use eight lowercase hexadecimal groups separated by `:`. A new announcement for a prefix from the same `(peer_as, peer_address)` replaces that peer's previous candidate for the prefix. Withdrawals remove only the route from the announcing peer. Malformed records do not change peer or route state, including records that contain both decodable and malformed entries or updates that contain withdrawals before malformed NLRI. A complete record that cannot be decoded under this contract counts as malformed and scanning continues at the next MRT record.

For each prefix, select the best path using these rules in order:

1. higher `LOCAL_PREF`; default is 100
2. shorter merged AS path length
3. lower `ORIGIN` value; default is 2
4. lower `MED` only when the first AS in the merged path is the same for both paths, using the peer AS when the path is empty and treating missing MED as 0
5. eBGP before iBGP
6. lower peer address, compared numerically by address family bytes
7. older route, using lower replay sequence and then lower timestamp

The output JSON has this shape:

```json
{
  "input": "path supplied on argv[1]",
  "records": {"seen": 0, "applied": 0, "ignored": 0, "malformed": 0},
  "peers": [],
  "routes": [],
  "selected_count": 0
}
```

Peers are ordered by index and include `index`, `as`, `bgp_id`, `address`, and `ibgp`. Routes are ordered by address family, network bytes, and prefix length. Each route object has `prefix`, `selected`, and `candidates`. Candidates are ordered by the same best-path comparison and include `peer_as`, `peer_address`, `local_pref`, `as_path`, `as_path_len`, `origin`, `med`, `source`, `timestamp`, and `sequence`. `origin` is `igp`, `egp`, or `incomplete`; `source` is `ebgp` or `ibgp`; missing `med` is JSON null.
