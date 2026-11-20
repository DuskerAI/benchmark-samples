The C++ receiver trace tool in `/app/task_file` is misreporting reconstructed SCTP user messages. The smoke trace is only a baseline; fix the implementation so `/app/task_file/build/sctp_replay <input.trace> <output.json>` matches the full local contract in `/app/task_file/README.md`. Read that file before changing the state machine; it is the normative trace and JSON contract.

The project must continue to build from `/app/task_file` with `make`, and the repaired tool must process traces locally without external services. The verifier will build the project and run generated traces through the binary. The repaired state machine needs to handle, at minimum:

- ordered and unordered `DATA` delivery;
- interleaved `I-DATA` reassembly by FSN, including duplicate fragment positions where the lower-TSN fragment owns the FSN;
- duplicate TSNs while preserving the first accepted chunk, plus 32-bit TSN wrap-around at the cumulative frontier;
- abandoned ranges, including wrapped ranges, that advance the TSN frontier, fail incomplete messages, and release later ordered messages only when appropriate;
- stream resets, stale chunks after advancement or reset, mixed classic/interleaved sequence spaces, and delivery ordering across ready deliveries and skips; and
- deterministic JSON transcripts and final state counters written only to the output path passed on the command line, with `ppid` taken exactly from the ending fragment.
