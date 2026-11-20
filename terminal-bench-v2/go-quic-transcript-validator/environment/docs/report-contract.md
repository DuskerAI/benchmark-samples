# qtrace report contract

The CLI accepts `--trace <bundle>` and `--out <json-file>`. It creates parent directories for the output file and writes indented JSON.

The top level is:

```json
{
  "version": "qtrace-1",
  "summary": {
    "trace_count": 1,
    "packet_count": 0,
    "connection_count": 0,
    "problem_count": 0,
    "valid": true
  },
  "connections": []
}
```

The top-level `summary.packet_count` is the number of packet records parsed from the trace bundle, including records that are later reported as duplicates. Connections are sorted by lowercase hex `connection_id`, which is the client source CID. A connection's `packet_count` is the number of accepted transcript packets for that connection. `retry_valid` is true only when every Retry for the connection has a valid tag and at least one Retry was present; it is false when no Retry was present or when any Retry for that connection has a bad tag. Retransmitted Initial packets with the same endpoint and reconstructed packet number are reported once; later duplicates add a `duplicate_packet` problem.

Each packet entry has `index`, `type` (`initial` or `retry`), `endpoint` (`client` or `server`), lowercase hex `dcid`, lowercase hex `scid`, `packet_number` for Initial or `null` for Retry, and `token` when a token is present. The `token` field is omitted from an Initial packet entry when the token length is zero.

Transport parameters are reported as:

```json
"transport_parameters": {
  "client": [{"id": "0x0000", "value": ""}],
  "server": [{"id": "0x0000", "value": ""}]
}
```

Parameter arrays are sorted by numeric id, then by value bytes. IDs are four-digit lowercase hex strings and values are lowercase hex strings. If the same endpoint repeats a parameter id in one Initial payload, keep the entries and add a `duplicate_transport_parameter` problem for the packet.

Problems are kept in trace order and always serialize as a JSON array, including `[]` when there are no problems. Each problem has `code`, `packet_index`, and `detail`. The defined codes are `bad_retry_integrity`, `duplicate_packet`, and `duplicate_transport_parameter`. A `bad_retry_integrity` problem uses detail `"retry"`; a `duplicate_packet` problem uses the endpoint string; a `duplicate_transport_parameter` problem uses the repeated parameter id. `summary.valid` is true when `problem_count` is zero.
