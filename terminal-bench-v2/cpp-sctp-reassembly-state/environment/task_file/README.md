# sctp_replay

`sctp_replay` reads a small SCTP-like receiver trace and writes a deterministic JSON transcript. It is a local debugging tool, not a network daemon.

Run it as:

```sh
/app/task_file/build/sctp_replay <input.trace> <output.json>
```

The program must accept the paths from the command line. It must not write any other files. Blank lines are ignored, and after leading whitespace a `#` starts a whole-line comment. Fields are separated by ASCII whitespace.

Trace records:

```text
START <initial_tsn>
DATA <tsn> <stream> <ssn> <flags> <ppid> <hex_payload>
IDATA <tsn> <stream> <mid> <fsn> <flags> <ppid> <hex_payload>
ABANDON <first_tsn> <last_tsn>
RESET <stream> <next_ssn> <next_mid>
```

`DATA` is the classic format. Fragments for one classic message share `stream` and `ssn`; the receiver joins them by TSN order. `IDATA` is the interleaving format. Fragments for one interleaved message share `stream` and `mid`; the receiver joins them by FSN order, not by TSN. An interleaved message is complete only when it has a beginning fragment at FSN 0, an ending fragment, and every FSN between them. If two accepted fragments for the same interleaved message use the same FSN, the lower-TSN fragment occupies that FSN, even if it arrives later in the trace; the other fragment is ignored for message assembly while still counting for TSN-frontier advancement. Flags are a string made from `B`, `E`, and `U`: beginning, ending, and unordered. The literal flag value `0` means none of those bits are set; it is valid for an intermediate fragment. A single-fragment user message uses both `B` and `E`. `U` removes stream ordering for that message. Payloads are lowercase or uppercase hex with an even number of digits. The `ppid` reported for a reassembled user message is the `ppid` carried by its ending fragment, including when that value is zero; for a single-fragment message this is that chunk's `ppid`. The receiver treats duplicate TSNs as duplicates and keeps the first accepted chunk.

The receiver has a cumulative TSN frontier using unsigned 32-bit TSN arithmetic. After `4294967295`, the next TSN is `0`. A chunk above a missing TSN can be stored, but no user message containing that chunk can be emitted until every lower TSN in serial order has either arrived or been abandoned. `ABANDON` marks an inclusive TSN range as not coming; if `first_tsn` is numerically greater than `last_tsn`, the range wraps through `4294967295` and continues at `0`. If a stored fragment of an incomplete message is abandoned, that message is failed. An incomplete message is also failed when the abandoned range covers a TSN inside the already known span of that message, or covers the TSN immediately after its highest known fragment; that adjacent TSN is the next possible continuation and the message can no longer complete. Already complete stored messages are not failed solely because an `ABANDON` range later includes one of their TSNs. For an ordered message, failing the next expected identifier emits a `skip` event and advances that stream's expected identifier. A failed unordered message is discarded without a `skip` event. Later ordered messages wait for their turn. `RESET` is applied at its position in the trace after any delivery already possible at that point; it drops buffered messages for the stream, sets the next expected classic SSN and I-DATA MID exactly to the given values, and emits a reset event. After a stream has advanced, chunks for an ordered message with an identifier lower than the current expected SSN or MID are stale: they still count as arrived for the TSN frontier, but they do not create or reopen a pending message and they do not emit an event.

Delivery is deterministic. Each input record is processed as its own step: after that record, advance the cumulative TSN frontier as far as possible, then repeatedly emit from one candidate set until no more deliveries or skips are ready before reading the next record. The candidate set is every ready unordered message, the next ready ordered message for each stream and format, and the next failed ordered message for each stream and format. Candidates are sorted by their smallest TSN, then by stream, format (`data` before `idata`), identifier, and unordered before ordered. A tied delivery sorts before a tied skip. Ordered `DATA` uses SSN; ordered `IDATA` uses MID. Classic and interleaved sequence spaces are independent. A stale ordered message with an identifier lower than the current expected value is ignored.

The output is UTF-8 JSON with this shape:

```json
{
  "events": [
    {"type":"deliver","format":"data","stream":1,"id":0,"ordered":true,"ppid":53,"first_tsn":10,"last_tsn":12,"payload_hex":"6869"},
    {"type":"skip","format":"idata","stream":1,"id":4,"reason":"abandoned"},
    {"type":"reset","stream":1,"next_ssn":7,"next_mid":9}
  ],
  "state": {"cumulative_tsn": 123, "buffered_chunks": 0, "pending_messages": 0}
}
```

Use decimal integers in JSON and lowercase `payload_hex`. The events array is the transcript in emission order. In `state`, `cumulative_tsn` is the final frontier, `buffered_chunks` is the number of accepted chunks still belonging to pending messages, and `pending_messages` is the number of messages that remain incomplete or blocked at end of input. Delivered, skipped, stale, ignored duplicate, and reset-dropped fragments are not counted as buffered.
