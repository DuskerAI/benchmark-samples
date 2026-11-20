Fix `/app/task_file`'s C replay tool. Run `cd /app/task_file && make`, then `/app/task_file/build/uring_replay <trace-file> <report-json>`.

Trace lines are blank, comments, or `RECORD key=value...`; fields are unordered. Names match `[A-Za-z0-9_.-]{1,63}`; hex is byte pairs. Ignore invalid records atomically, pre-`INIT` records, and later `INIT`s.

Records: `INIT entries=<power-of-two> page=<nonzero-bytes> head=<u32>`; `FILE name=<name> hex=<hex>`; `MAP name=<name> size=<bytes> present=<pages/ranges> data=<hex>` where data length equals size; `SUB id=<positive-int> op=<READ|WRITE|NOP|TIMEOUT> file=<name|-> map=<name|-> off=<bytes> mapoff=<bytes> len=<bytes> link=<id|none>`. FILE, MAP, and SUB create or replace. Handle >=1024 FILE/MAP/SUB/CQ and 4096 resident pages.

Replay live `CQ id=<sub-id> pos=<u32> res=<signed-int>` by increasing unsigned distance from `head`; distance `>= entries` is outside, input order breaks ties. Emit only the first live CQ per submission. READ/WRITE copy at most nonnegative `res`, requested length, remaining file bytes, and contiguous resident map bytes. Linked `TIMEOUT` after a nonnegative linked completion becomes `-125` and increments `cancelled`.

Output strict JSON keys `schema`, `applied`, `cancelled`, `short_io`, `completions`, sorted `files`/`maps`, `digest`; `schema` is `uring-replay-v1`. Completions have only `id`, `op`, `res`, `bytes`; files/maps have `name`, `size`, `hex`, `digest`. Per-image digest is FNV-1a64 raw bytes, 16 lowercase hex digits.

Top `digest` uses FNV offset `0xcbf29ce484222325`, prime `0x100000001b3`, 64-bit wrap, over newline-terminated lines: `A {applied} {cancelled} {short_io}`, each `C {id} {op} {res} {bytes}`, each sorted `F {name} {size} {hex}`, then each sorted `M {name} {size} {hex}`. `F` and `M` use raw image hex.
