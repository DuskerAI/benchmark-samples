# Virtual table graphs lose identity at JIT exits

This LuaJIT checkout includes an experimental table-graph allocation-sinking
extension. Shared children become distinct tables after an ordinary guard exit;
cycles, nested-only values and hot side traces can fail more severely. Escaping
graphs can also behave differently depending on the order of their edge stores.
See `incident/README.md` for the complete supported contract and run
`incident/graph-exit.lua` for a small public reproduction.

Repair graph recovery across the optimizer, snapshots and side-trace boundary.
One virtual allocation must retain one identity throughout a recovered graph;
restore exactly the selected snapshot's values and preserve aliases, distinct
objects, cycles, array/hash fields, template copies and metatables. Reachable nodes
and scalar dependencies need not have their own snapshot stack slots. Ordinary
exits, repeated/hot exits, escaping graphs, inline frames, GC and existing FFI,
error/coroutine behavior must remain correct. Recovery state belongs to one exit,
not to a later execution that reuses the same IR references.

Preserve the new optimization. Nonescaping constant-key fresh table graphs must
still execute in native loops without heap allocations growing with the iteration
count, including graphs with shared nodes, cycles and template-table copies.
Keep both interpreter recovery and hot side-trace compilation working; globally
disabling JIT/sinking, eagerly allocating graphs or suppressing side tracing is
not a solution. No particular internal representation or helper name is required.

The prototype is authored on top of a pinned LuaJIT revision; it is not claimed to
be an upstream released feature. The supported environment is Linux/aarch64 with
GCC 12 and assertions enabled. Everything needed is available offline:

```sh
make -j2 BUILDMODE=static XCFLAGS=-DLUA_USE_ASSERT
src/luajit incident/graph-exit.lua
```

Preserve the public Lua/C APIs and build/dependency manifests. Production C,
headers, Lua and DynASM sources under `src`/`dynasm` are exported to an isolated
verifier; new included helper headers are supported. Generated files, submitted
tests and binaries are rebuilt or ignored. Source limit: 16 MiB. The verifier
measures behavior and allocator requests, with no wall-time performance threshold.
