# Arena replay contract

`arena_replay` reconstructs the state of a single crash-recovered arena from an append-only trace. Build it with `make` from `/app/task_file`. The command line is:

```text
/app/task_file/build/arena_replay <trace-file> <report-json>
```

The input is UTF-8 text. Only complete newline-terminated records are replayed. If the file ends with bytes after the last newline, those bytes are ignored and the report sets `ignored_tail` to `true`. Blank lines and lines beginning with `#` are ignored. Fields after the record name are `key=value` pairs separated by spaces; field order is not meaningful and a complete record with a missing, duplicate, malformed, or unknown field is rejected.

The first complete non-comment record must be:

```text
ARENA name=<token> size=<uint> page=<uint> quarantine=<uint>
```

`size` is the byte length of the arena, `page` is the page size, and `quarantine` is the number of freed blocks that remain unavailable. `size` and `page` must be positive, `quarantine` may be zero, and `size` must be a multiple of `page`. All addresses are zero-based byte offsets.

The remaining complete records are:

```text
ALLOC id=<token> gen=<uint> size=<uint> align=<uint> tag=<hex-token>
FREE id=<token> gen=<uint> tag=<hex-token>
```

Tokens are 1-31 characters from `[A-Za-z0-9_.:-]`. `tag` uses lowercase or uppercase hexadecimal digits. `gen`, `size`, and `align` are positive unsigned decimal integers. `align` must be a power of two no larger than 64. An `ALLOC` is valid only when the handle is not currently live, the supplied generation is one greater than the last accepted generation for that handle (or `1` for a new handle), and a block can be placed. A block is placed by scanning the free list in ascending address order and choosing the lowest address that satisfies the requested alignment and keeps the whole block inside one page. A request with `size > page` is rejected. Allocation consumes exactly `size` bytes and may split a free range.

A `FREE` is valid only when the handle is live and both the generation and tag match that live allocation. A valid free removes the live allocation and appends the block to the quarantine queue. After each valid free, release the oldest queued blocks until the queue length is at most the arena's `quarantine` value. Released blocks return to the free list, which is always sorted by address and coalesced.

Rejected complete records do not change allocator state and increment `rejected`. Valid `ALLOC` and `FREE` records, and no other records, increment `applied`; a valid `ARENA` record initializes the arena but is not an applied operation. The allocator does not release remaining quarantined blocks at end of file. `high_water` is the maximum end offset ever reached by a valid allocation.

The report must be deterministic JSON with these keys: `arena`, `applied`, `rejected`, `ignored_tail`, `live`, `free`, `quarantine`, `high_water`, and `digest`. `arena` contains `name`, `size`, `page_size`, and `quarantine_limit`. `live` is sorted by address then id and each entry contains `id`, `gen`, `tag`, `addr`, `size`, and `align`. `free` is sorted by address and each entry contains `addr` and `size`. `quarantine` is in release order and each entry contains `id`, `gen`, `tag`, `addr`, and `size`.

`digest` is a 16-character lowercase hexadecimal FNV-1a 64-bit digest over these UTF-8 lines in this exact order:

```text
A <name> <size> <page_size> <quarantine_limit>\n
L <id> <gen> <tag> <addr> <size> <align>\n
F <addr> <size>\n
Q <id> <gen> <tag> <addr> <size>\n
H <high_water> R <rejected> T <0-or-1>\n
```
