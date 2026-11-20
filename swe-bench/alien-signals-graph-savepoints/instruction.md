# Cancelled previews must restore the reactive graph

Our dashboard reads derived values while previewing an edit, and cancels the edit
if validation fails. Batching alone cannot roll this back: reads already changed
caches, dependency choices and owned subscriptions. Add a synchronous public
`transaction<T>(fn: () => T): T` export to Alien Signals. See `INCIDENT.md` and the
public incident tests. This is a feature request; the upstream has batching but
no transaction implementation.

**Values, reads and savepoints**

- Run `fn` once and return its exact result on success. Reads inside it see its
  tentative writes immediately, including through chains, diamonds and conditional
  dependencies. Existing effects wait for the outermost transaction to settle.
- Every nested transaction is a savepoint. A successful inner transaction joins
  its parent. An error escaping a callback aborts that frame, restores its entry
  state, and rethrows that exact value, including `undefined` or a primitive. An
  inner error caught by its parent must leave the parent usable. An outer abort
  also undoes successful inner work. Errors caught inside a callback do not abort
  it. This is synchronous; a returned object or promise is an ordinary result.
- Restore reactive bookkeeping as well as source assignments: cached values,
  previous-value getter state, actual dependencies, pending work, force-trigger
  state and ambient tracking. A cached read after abort must not rerun a getter
  whose entry cache was valid. A computed first evaluated in an aborted frame
  becomes unevaluated again. Failed speculative reads escaping the callback must
  leave its previously valid graph recoverable.
- Successful speculative evaluations remain cached on commit. Getters receive
  their latest successful value within the surviving frame. Commit must not
  rerun an already clean getter merely to publish it, and must not evaluate
  unobserved or unrelated lazy computations. This request does not otherwise
  change upstream computed error semantics outside transactions.
- Rollback is shallow: restore assigned signal values and object identities,
  without cloning user data or undoing arbitrary external JavaScript side effects.

**Publication and ordinary batching**

- Publish only the final committed state. A pre-existing observer must not run
  for a net unchanged outcome, even if speculative reads temporarily advanced a
  dependency's cache and then returned it to its entry value. Preserve normal
  unchanged-computed suppression. A successful `trigger` still forces its direct
  consumers; an aborted trigger must not leak that forced notification.
- Transactions compose with ordinary paired `startBatch`/`endBatch`, including
  work queued before the transaction. An abort cannot erase that older work or
  flush it early. Restore the caller's batch depth and active consumer. Reads
  participate in ambient tracking on commit; dependencies introduced only by an
  aborted frame must be removed. Callback batch calls remain balanced.
- Commit is decided when the outer callback returns successfully. Then finish
  deferred resource releases and drain committed notifications at the appropriate
  batch boundary. Attempt all pending observers and applicable cleanup, report
  the first encountered failure, and keep the committed writes. A commit-side
  observer or cleanup error must never cause rollback. Reentrant writes and
  transactions from those callbacks must also settle correctly.

**Resource ownership participates**

- Creating an effect or scope still runs its initial callback immediately. On
  abort, release resources created in that frame once, in reverse creation order,
  after restoring reactive state. Their escaped disposers remain harmless and
  later writes must not revive them. Objects created by successful inner work
  belong to the surviving parent. A handle to a new signal or computed escaping
  an aborted frame resets to its original creation state and remains usable.
- Stopping a pre-existing effect/scope during a transaction is tentative: it is
  inactive for that frame, but cleanup waits until outer commit. Abort restores
  it without running cleanup. This includes a resource created by the parent and
  tentatively stopped by an inner frame. Repeated stops clean up once on commit.
- The same rule applies when speculative computed reevaluation replaces owned
  children or drops its last consumer. Abort must restore the old generation and
  release only resources created by aborted work. Commit releases retired old
  generations while keeping the surviving subscriptions and new resources.
  Shared computeds remain live while another consumer remains.
- Abort compensation runs untracked in the nearest surviving transaction, or
  normally after an outer abort. Writes and newly created resources from cleanup
  belong to that surviving context. Attempt all compensation; preserve the
  original callback error over secondary cleanup/observer failures.

Keep existing APIs, types, low-level system behavior and nontransactional public
compatibility. No runtime dependency, service, asynchronous scheduler or new
configuration is needed. Only production source under `src` is evaluated. The
verifier builds it afresh for CJS and ESM; editing tests or generated files cannot
supply the feature. Dependencies are installed. Run `pnpm test` for public tests.
